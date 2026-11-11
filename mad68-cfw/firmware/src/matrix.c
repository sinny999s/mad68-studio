/*
 * This program is free software: you can redistribute it and/or modify it under
 * the terms of the GNU General Public License as published by the Free Software
 * Foundation, either version 3 of the License, or (at your option) any later
 * version.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
 * FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
 * details.
 *
 * You should have received a copy of the GNU General Public License along with
 * this program. If not, see <https://www.gnu.org/licenses/>.
 */

#include "matrix.h"

#include "distance.h"
#include "eeconfig.h"
#include "hardware/hardware.h"
#include "lib/bitmap.h"

// Exponential moving average (EMA) filter
#define EMA(x, y)                                                              \
  (((uint32_t)(x) +                                                            \
    ((uint32_t)(y) * ((1 << MATRIX_EMA_ALPHA_EXPONENT) - 1))) >>               \
   MATRIX_EMA_ALPHA_EXPONENT)

__attribute__((always_inline)) static inline uint16_t
matrix_analog_read(uint8_t key) {
#if defined(MATRIX_INVERT_ADC_VALUES)
  return ADC_MAX_VALUE - analog_read(key);
#else
  return analog_read(key);
#endif
}

#if defined(MATRIX_SCAN_FILTER)
// Filter that runs once per reading of a key instead of once per main loop
// pass (levels and what they do: the board's scan.h), so that a faster scan
// means less delay. The board's analog driver counts the readings it takes.
#include "scan.h"

#if !defined(MATRIX_FILTER_RISE_BAND)
// The filtered value follows the smoothed readings down at once, and up only
// once they are more than this far above it, from then on this far below
// them. A resting key therefore sits at the low edge of its noise, where the
// start-up calibration put its rest value, and stays there through a few
// counts of noise or drift. The classic filter does the same with a band of
// 16 counts.
#define MATRIX_FILTER_RISE_BAND 8
#endif

// Fraction bits of the smoothed reading
#define FILTER_FRAC 8

typedef struct {
  // Smoothed reading, in 1/256 counts
  int32_t acc;
  // The two readings before the latest
  uint16_t prev[2];
  // Counter value of the latest reading taken
  uint8_t seq;
} key_filter_t;

static key_filter_t key_filter[NUM_KEYS];

// What the next pass does to every key's filter state before anything else:
// nothing; start again with the filtered value set to the reading
// (calibration); or start again keeping the filtered value in use (the level
// has changed)
#define FILTER_SEED_NONE 0u
#define FILTER_SEED_READING 1u
#define FILTER_SEED_KEEP 2u
static uint8_t filter_seed = FILTER_SEED_READING;
static uint8_t filter_last_level = SCAN_FILTER_CLASSIC;

// Filter delay on real presses (scan.h matrix_lag_stats)
static bool lag_on = false;
static uint8_t lag_state[NUM_KEYS];
static uint32_t lag_start[NUM_KEYS];
static uint32_t lag_count = 0, lag_sum = 0, lag_min = 0, lag_max = 0;

// The level this pass runs at, and what to do to the filter states first
__attribute__((always_inline)) static inline uint32_t
filter_pass_begin(uint32_t *seed) {
  const uint32_t level = analog_filter_level();
  if (level != filter_last_level) {
    filter_last_level = (uint8_t)level;
    filter_seed = FILTER_SEED_KEEP;
  }
  *seed = FILTER_SEED_NONE;
  if (level != SCAN_FILTER_CLASSIC) {
    *seed = filter_seed;
    filter_seed = FILTER_SEED_NONE;
  }
  return level;
}
#else
#define SCAN_FILTER_CLASSIC 0u
#define FILTER_SEED_NONE 0u
__attribute__((always_inline)) static inline uint32_t
filter_pass_begin(uint32_t *seed) {
  *seed = FILTER_SEED_NONE;
  return SCAN_FILTER_CLASSIC;
}
#endif

// One filter step for a key. Returns false if there is nothing new for it
// (per-reading levels, no reading since the last pass): its filtered value,
// and so everything that follows from it, is unchanged.
__attribute__((always_inline)) static inline bool
matrix_filter(uint32_t i, uint32_t level, uint32_t seed, uint16_t *raw,
              uint16_t *filtered);

__attribute__((always_inline)) static inline uint16_t
matrix_bottom_out_value(uint8_t key, uint16_t rest_value) {
  return M_MIN(rest_value +
                   M_MAX(eeconfig->calibration.initial_bottom_out_threshold,
                         eeconfig->bottom_out_threshold[key]),
               ADC_MAX_VALUE);
}

key_state_t key_matrix[NUM_KEYS];

__attribute__((always_inline)) static inline bool
matrix_filter(uint32_t i, uint32_t level, uint32_t seed, uint16_t *raw,
              uint16_t *filtered) {
#if defined(MATRIX_SCAN_FILTER)
  if (level != SCAN_FILTER_CLASSIC) {
    key_filter_t *f = &key_filter[i];
    const volatile uint8_t *counter = &analog_col_seq[analog_key_col[i]];
    // Nothing read since the last pass: the reading need not be fetched
    if (seed == FILTER_SEED_NONE && *counter == f->seq)
      return false;
    const uint16_t x = matrix_analog_read((uint8_t)i);
    *raw = x;
    // The counter is read after the reading: if a new reading lands in
    // between, it is skipped next time rather than taken twice
    const uint8_t seq = *counter;
    if (seed != FILTER_SEED_NONE) {
      f->seq = seq;
      f->prev[0] = x;
      f->prev[1] = x;
      f->acc = (int32_t)x << FILTER_FRAC;
      *filtered = seed == FILTER_SEED_READING ? x : key_matrix[i].adc_filtered;
      return true;
    }
    uint32_t steps = (uint8_t)(seq - f->seq);
    if (steps == 0)
      return false;
    f->seq = seq;

    // Median of the last three readings: a single stray one never gets
    // through
    const uint16_t a = f->prev[0], b = f->prev[1];
    f->prev[1] = a;
    f->prev[0] = x;
    const uint16_t lo = a < b ? a : b, hi = a < b ? b : a;
    const uint16_t median = x < lo ? lo : (x > hi ? hi : x);

    // One step per reading taken since the last pass, so the delay stays
    // the same in time when the main loop misses readings
    const uint32_t shift = SCAN_FILTER_COUNT - level;
    const int32_t target = (int32_t)median << FILTER_FRAC;
    int32_t acc = f->acc;
    if (steps > 4)
      steps = 4;
    do
      acc += (target - acc) >> shift;
    while (--steps);
    f->acc = acc;

    const int32_t smoothed = (acc + (1 << (FILTER_FRAC - 1))) >> FILTER_FRAC;
    int32_t out = key_matrix[i].adc_filtered;
    if (smoothed < out)
      out = smoothed;
    else if (smoothed > out + MATRIX_FILTER_RISE_BAND)
      out = smoothed - MATRIX_FILTER_RISE_BAND;
    *filtered = (uint16_t)out;
    return true;
  }
#else
  (void)level;
  (void)seed;
#endif
  const uint16_t x = matrix_analog_read((uint8_t)i);
  *raw = x;
  *filtered = (uint16_t)EMA(x, key_matrix[i].adc_filtered);
  return true;
}

#if defined(MATRIX_SCAN_FILTER)
// A key's reading `x` and filtered value `y` this pass: time how long after
// the reading passes half travel the filtered value does
static void lag_update(uint32_t i, uint16_t x, uint16_t y) {
  const uint32_t rest = key_matrix[i].adc_rest_value;
  const uint32_t bottom = key_matrix[i].adc_bottom_out_value;
  if (bottom < rest + 200u)
    return;
  const uint32_t half = (rest + bottom) >> 1;
  const uint32_t quarter = (rest + half) >> 1;
  switch (lag_state[i]) {
  case 0:
    if (x < half)
      break;
    lag_start[i] = board_cycle_count();
    lag_state[i] = 1;
    // fall through
  case 1:
    if (y >= half) {
      const uint32_t us =
          (board_cycle_count() - lag_start[i]) / (F_CPU / 1000000u);
      if (lag_count == 0 || us < lag_min)
        lag_min = us;
      if (us > lag_max)
        lag_max = us;
      lag_sum += us;
      lag_count++;
      lag_state[i] = 2;
    } else if (x < quarter) {
      lag_state[i] = 0;
    }
    break;
  default:
    if (x < quarter && y < quarter)
      lag_state[i] = 0;
    break;
  }
}

void matrix_lag_measure(bool on) { lag_on = on; }

void matrix_lag_stats(uint32_t *count, uint32_t *mean_us, uint32_t *min_us,
                      uint32_t *max_us, bool reset) {
  *count = lag_count;
  *mean_us = lag_count ? lag_sum / lag_count : 0;
  *min_us = lag_min;
  *max_us = lag_max;
  if (reset) {
    lag_count = 0;
    lag_sum = 0;
    lag_min = 0;
    lag_max = 0;
    for (uint32_t i = 0; i < NUM_KEYS; i++)
      lag_state[i] = 2;
  }
}
#endif

// Bitmap for tracking which keys have Rapid Trigger disabled
static bitmap_t rapid_trigger_disabled[] = MAKE_BITMAP(NUM_KEYS);

void matrix_init(void) { matrix_recalibrate(false); }

void matrix_recalibrate(bool reset_bottom_out_threshold) {
  if (reset_bottom_out_threshold) {
    uint16_t bottom_out_threshold[NUM_KEYS] = {0};
    EECONFIG_WRITE(bottom_out_threshold, bottom_out_threshold);
  }

  for (uint32_t i = 0; i < NUM_KEYS; i++) {
    key_matrix[i].adc_filtered = eeconfig->calibration.initial_rest_value;
    key_matrix[i].adc_rest_value = eeconfig->calibration.initial_rest_value;
    key_matrix[i].adc_bottom_out_value =
        matrix_bottom_out_value(i, eeconfig->calibration.initial_rest_value);
    key_matrix[i].distance = 0;
    key_matrix[i].extremum = 0;
    key_matrix[i].key_dir = KEY_DIR_INACTIVE;
    key_matrix[i].is_pressed = false;
  }

#if defined(MATRIX_SCAN_FILTER)
  // A per-reading filter starts from each key's reading
  filter_seed = FILTER_SEED_READING;
#endif

  // We only calibrate the rest value. The bottom-out value will be updated
  // during the scan process.
  const uint32_t calibration_start = timer_read();
  while (timer_elapsed(calibration_start) < MATRIX_CALIBRATION_DURATION) {
    // Run the analog task to possibly update the ADC values
    analog_task();

    uint32_t seed;
    const uint32_t level = filter_pass_begin(&seed);
    for (uint32_t i = 0; i < NUM_KEYS; i++) {
      uint16_t raw, new_adc_filtered;
      if (!matrix_filter(i, level, seed, &raw, &new_adc_filtered))
        continue;

      key_matrix[i].adc_filtered = new_adc_filtered;

      if (new_adc_filtered + MATRIX_CALIBRATION_EPSILON <=
          key_matrix[i].adc_rest_value)
        // Only update the rest value if the new value is smaller and the
        // difference is at least the calibration epsilon
        key_matrix[i].adc_rest_value = new_adc_filtered;

      // Update the bottom-out value to be the minimum bottom-out value based on
      // the updated rest value
      key_matrix[i].adc_bottom_out_value =
          matrix_bottom_out_value(i, key_matrix[i].adc_rest_value);
    }
  }
}

void matrix_scan(void) {
  uint32_t seed;
  const uint32_t level = filter_pass_begin(&seed);
#if defined(MATRIX_SCAN_FILTER)
  // With a per-reading filter, a key whose filtered value is the same as last
  // pass comes out of everything below exactly as it went in (same travel,
  // same state), as long as its settings are the same. So such a key is left
  // alone, except in one pass per millisecond, which goes through every key
  // and so picks up a change of profile, actuation or Rapid Trigger setting.
  static uint32_t last_refresh = 0;
  const uint32_t tick = timer_read();
  const bool refresh = (level == SCAN_FILTER_CLASSIC) |
                       (seed != FILTER_SEED_NONE) | (tick != last_refresh);
  last_refresh = tick;
#endif
  for (uint32_t i = 0; i < NUM_KEYS; i++) {
    uint16_t raw, new_adc_filtered;
#if defined(MATRIX_SCAN_FILTER)
    if (!matrix_filter(i, level, seed, &raw, &new_adc_filtered)) {
      // No reading since the last pass
      if (!refresh)
        continue;
      new_adc_filtered = key_matrix[i].adc_filtered;
    } else if (lag_on) {
      lag_update(i, raw, new_adc_filtered);
    } else if (!refresh && new_adc_filtered == key_matrix[i].adc_filtered) {
      continue;
    }
#else
    if (!matrix_filter(i, level, seed, &raw, &new_adc_filtered))
      continue;
#endif
    const actuation_t *actuation = &CURRENT_PROFILE.actuation_map[i];

    key_matrix[i].adc_filtered = new_adc_filtered;

    if (new_adc_filtered >=
        key_matrix[i].adc_bottom_out_value + MATRIX_CALIBRATION_EPSILON)
      // Only update the bottom-out value if the new value is larger and the
      // difference is at least the calibration epsilon.
      key_matrix[i].adc_bottom_out_value = new_adc_filtered;

    key_matrix[i].distance =
        adc_to_distance(new_adc_filtered, key_matrix[i].adc_rest_value,
                        key_matrix[i].adc_bottom_out_value);

    if (bitmap_get(rapid_trigger_disabled, i) | (actuation->rt_down == 0)) {
      key_matrix[i].key_dir = KEY_DIR_INACTIVE;
      key_matrix[i].is_pressed =
          (key_matrix[i].distance >= actuation->actuation_point);
    } else {
      const uint8_t reset_point =
          actuation->continuous ? 0 : actuation->actuation_point;
      const uint8_t rt_up =
          actuation->rt_up == 0 ? actuation->rt_down : actuation->rt_up;

      switch (key_matrix[i].key_dir) {
      case KEY_DIR_INACTIVE:
        if (key_matrix[i].distance > actuation->actuation_point) {
          // Pressed down past actuation point
          key_matrix[i].extremum = key_matrix[i].distance;
          key_matrix[i].key_dir = KEY_DIR_DOWN;
          key_matrix[i].is_pressed = true;
        }
        break;

      case KEY_DIR_DOWN:
        if (key_matrix[i].distance <= reset_point) {
          // Released past reset point
          key_matrix[i].extremum = key_matrix[i].distance;
          key_matrix[i].key_dir = KEY_DIR_INACTIVE;
          key_matrix[i].is_pressed = false;
        } else if (key_matrix[i].distance + rt_up < key_matrix[i].extremum) {
          // Released by Rapid Trigger
          key_matrix[i].extremum = key_matrix[i].distance;
          key_matrix[i].key_dir = KEY_DIR_UP;
          key_matrix[i].is_pressed = false;
        } else if (key_matrix[i].distance > key_matrix[i].extremum)
          // Pressed down further
          key_matrix[i].extremum = key_matrix[i].distance;
        break;

      case KEY_DIR_UP:
        if (key_matrix[i].distance <= reset_point) {
          // Released past reset point
          key_matrix[i].extremum = key_matrix[i].distance;
          key_matrix[i].key_dir = KEY_DIR_INACTIVE;
          key_matrix[i].is_pressed = false;
        } else if (key_matrix[i].extremum + actuation->rt_down <
                   key_matrix[i].distance) {
          // Pressed by Rapid Trigger
          key_matrix[i].extremum = key_matrix[i].distance;
          key_matrix[i].key_dir = KEY_DIR_DOWN;
          key_matrix[i].is_pressed = true;
        } else if (key_matrix[i].distance < key_matrix[i].extremum)
          // Released further
          key_matrix[i].extremum = key_matrix[i].distance;
        break;

      default:
        break;
      }
    }
  }
}

void matrix_disable_rapid_trigger(uint8_t key, bool disable) {
  bitmap_set(rapid_trigger_disabled, key, disable);
}
