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

// Analog front end for the MAD68 HE.
//
// The Hall sensors sit in a 5 x 15 matrix. Each column has its own active-low
// select line (power switch); the five rows are wired to ADC inputs. One
// column is enabled at a time, the sensors are given a settle time, then all
// five rows are converted. Pin assignment and row/column order were recovered
// from the stock firmware (see tools/), and verified against which per-key
// record the stock scan code fills.
//
// Timing (v7.3). The settle time comes from the scan speed setting (scan.h).
// Per column: TIM10, in one-pulse mode, times the settle; its interrupt
// starts the ADC sequence; a circular DMA stream moves the five results and
// raises one interrupt, which stores them, switches to the next column and
// starts TIM10 again. This runs on registers directly: the HAL calls v7.2 made
// for every column cost a few microseconds each time. A late interrupt can
// only make a settle time longer, never shorter, because the ADC is started
// by the timer alone and the timer only starts once the column is switched.
//
// Timing test (v7.4 test builds, scan.h). A column can also be read by all
// three ADCs at once: PA0-PA3 reach ADC1, ADC2 and ADC3 and PA7 reaches ADC1
// and ADC2, so the five rows take two conversion rounds instead of five. Each
// ADC has its own circular DMA stream; ADC1 is started last and converts at
// least as many rows as the others, so its DMA interrupt still marks the end
// of the column. Separately, that interrupt can power the next column and
// start its settle time first and store the results afterwards, so storing
// them costs the scan no time. The stock firmware only ever uses ADC1, so
// nothing about
// ADC2 and ADC3 is taken for granted: these modes, and any other settle or
// sample time, are only ever entered through a probe (scan.h), which measures
// with the keys left on their last good readings, and only goes live if the
// readings stay where they were. After a restart the keyboard reads its keys
// exactly as v7.3 does.
//
// The physical magnet polarity is not assumed. Each key's resting value is
// captured at start-up (and again after a timing change); the value handed to
// the matrix code is ADC_REST_REFERENCE plus the distance from that rest
// value, oriented so it always grows as the key is pressed. Until a key has
// moved far enough to learn its direction the absolute distance is used, which
// is correct for either polarity.

#include "hardware/hardware.h"
#include "rgb.h"
#include "scan.h"

#include "stm32f4xx_hal.h"

#if !defined(ADC_COLUMN_SCAN)
#error "This driver requires ADC_COLUMN_SCAN board configuration"
#endif

#define NUM_ROWS ADC_COLUMN_SCAN_ROWS
#define NUM_COLS ADC_COLUMN_SCAN_COLS

// ADC channel for each row, in single-ADC conversion order
static const uint32_t row_channels[NUM_ROWS] = ADC_COLUMN_SCAN_ROW_CHANNELS;
// Column select lines (active low)
static GPIO_TypeDef *const col_ports[NUM_COLS] = ADC_COLUMN_SCAN_COL_PORTS;
static const uint16_t col_pins[NUM_COLS] = ADC_COLUMN_SCAN_COL_PINS;
// Key index + 1 for each matrix position, 0 where there is no switch
static const uint8_t key_map[NUM_ROWS][NUM_COLS] = ADC_COLUMN_SCAN_MATRIX;

// Parallel read: the rows each ADC converts, in order
static const uint8_t par_rows1[] = ADC_PARALLEL_ADC1_ROWS;
static const uint8_t par_rows2[] = ADC_PARALLEL_ADC2_ROWS;
static const uint8_t par_rows3[] = ADC_PARALLEL_ADC3_ROWS;
#define PAR_N1 ((uint32_t)sizeof(par_rows1))
#define PAR_N2 ((uint32_t)sizeof(par_rows2))
#define PAR_N3 ((uint32_t)sizeof(par_rows3))

_Static_assert(sizeof(par_rows1) + sizeof(par_rows2) + sizeof(par_rows3) ==
                   NUM_ROWS,
               "the three ADCs convert every row once between them");
_Static_assert(sizeof(par_rows1) >= sizeof(par_rows2) &&
                   sizeof(par_rows1) >= sizeof(par_rows3),
               "ADC1 finishes last: its DMA interrupt ends the column");
_Static_assert(NUM_ROWS <= 6, "a sequence must fit SQR3");

// Paired read: ADC2 converts one row twice, ADC1 and ADC3 the others
static const uint8_t pair_rows1[] = ADC_PAIRED_ADC1_ROWS;
static const uint8_t pair_rows2[2] = {ADC_PAIRED_ROW, ADC_PAIRED_ROW};
static const uint8_t pair_rows3[] = ADC_PAIRED_ADC3_ROWS;
#define PAIR_N1 ((uint32_t)sizeof(pair_rows1))
#define PAIR_N3 ((uint32_t)sizeof(pair_rows3))
// Result slots of the paired row's two readings: after ADC1's and ADC3's
#define PAIR_A (NUM_ROWS - 1u)
#define PAIR_B NUM_ROWS

_Static_assert(sizeof(pair_rows1) + sizeof(pair_rows3) + 1 == NUM_ROWS,
               "ADC1 and ADC3 convert every row but the paired one");
_Static_assert(sizeof(pair_rows1) >= 2 && sizeof(pair_rows1) >= sizeof(pair_rows3),
               "ADC1 finishes last: its DMA interrupt ends the column");

#if !defined(ADC_NUM_SAMPLE_CYCLES)
#define ADC_NUM_SAMPLE_CYCLES ADC_SAMPLETIME_28CYCLES
#endif

#if !defined(ADC_BASELINE_SCANS)
// Full-matrix scans averaged to establish each key's resting value
#define ADC_BASELINE_SCANS 16
#endif

#if !defined(ADC_REST_REFERENCE)
// Value reported for a key at rest, whatever its own sensor offset is
#define ADC_REST_REFERENCE 1024
#endif

#if !defined(ADC_POLARITY_LEARN_THRESHOLD)
// Distance from rest (ADC counts) after which a key's direction is learned
#define ADC_POLARITY_LEARN_THRESHOLD 300
#endif

#if !defined(ADC_REBASE_MAX_SHIFT)
// When the rest values are captured again after a timing change, a key whose
// reading moved further than this from its old rest value is being held
// down, and keeps its old rest value.
#define ADC_REBASE_MAX_SHIFT ADC_POLARITY_LEARN_THRESHOLD
#endif

_Static_assert(SCAN_TUNE_MAX_SHIFT < ADC_REBASE_MAX_SHIFT,
               "a timing that goes live must leave every rest value capturable");

#if !defined(SCAN_STALL_MS)
// No column converted for this long: re-arm the ADC, DMA and timer
#define SCAN_STALL_MS 100u
#endif

// Full scans per scan-rate measurement
#define SCAN_RATE_WINDOW 64u

// DMA2 flag bits in LISR/LIFCR for one stream (FEIF, DMEIF, TEIF, HTIF, TCIF)
#define DMA_S0_FLAGS 0x3Du
#define DMA_S1_FLAGS (0x3Du << 6)
#define DMA_S2_FLAGS (0x3Du << 16)
// Parallel read: both other ADCs delivered every result / one of them failed
#define DMA_PAR_DONE (DMA_LISR_TCIF1 | DMA_LISR_TCIF2)
#define DMA_PAR_ERRORS                                                         \
  (DMA_LISR_TEIF1 | DMA_LISR_DMEIF1 | DMA_LISR_TEIF2 | DMA_LISR_DMEIF2)
// LISR reads allowed for the other two ADCs' last results to arrive. They are
// started before ADC1, so the results are normally there on the first read.
#define SCAN_SYNC_TRIES 16u
// Register polls allowed for a DMA stream to stop (it stops within a transfer)
#define DMA_STOP_TRIES 1000u

#define TICKS_PER_US (F_CPU / 1000000u)

static const uint8_t settle_us[SCAN_SPEED_COUNT] = SCAN_SETTLE_US;
static const uint8_t speed_modes[SCAN_SPEED_COUNT] = SCAN_SPEED_MODES;
static const uint8_t speed_samples[SCAN_SPEED_COUNT] = SCAN_SPEED_SAMPLES;

_Static_assert((F_CPU / 1000000) * 255u <= 65536u,
               "a settle time must fit TIM10's 16-bit period");

// How a column is read (scan.h)
typedef struct {
  uint8_t mode;
  uint8_t sample;
  uint16_t ticks;
} scan_timing_t;

static ADC_HandleTypeDef adc_handle;

// Results of one column. One ADC (layout 0): the five rows in order. Three
// ADCs (layout 1): ADC1's rows, then ADC2's, then ADC3's. Paired (layout 2):
// ADC1's rows, then ADC3's, then the paired row's two readings, the second of
// them in one slot more than there are rows. slot_row maps a slot to its row,
// and slot_key to the key index + 1 it holds in each column (0: no switch).
#define LAYOUT_SINGLE 0u
#define LAYOUT_PARALLEL 1u
#define LAYOUT_PAIRED 2u
#define LAYOUTS 3u
#define NUM_SLOTS (NUM_ROWS + 1u)
__attribute__((aligned(8))) static volatile uint16_t adc_buffer[NUM_SLOTS];
static uint8_t slot_row[LAYOUTS][NUM_ROWS];
static uint8_t slot_key[LAYOUTS][NUM_COLS][NUM_ROWS];
static volatile uint16_t adc_raw[NUM_KEYS];

static volatile uint32_t baseline_acc[NUM_KEYS];
static volatile uint16_t baseline[NUM_KEYS];
static volatile int8_t polarity[NUM_KEYS];
static volatile bool baseline_ready = false;

// Rest value capture: BASELINE_IDLE, or 0 while waiting for the next full scan
// to begin, then 1 + the number of full scans accumulated so far.
#define BASELINE_IDLE 0xFFu
static volatile uint8_t baseline_pass = 0;

static volatile uint8_t current_col = 0;
static volatile uint8_t scan_speed = SCAN_SPEED_DEFAULT;
// Whether the saved speed's own timing is the one in use (SCAN_SPEED_*)
static volatile uint8_t speed_state = SCAN_SPEED_IN_EFFECT;
static uint8_t filter_level = SCAN_FILTER_DEFAULT;
static volatile uint32_t column_count = 0;
// Readings taken of each column (wraps), and each key's column (scan.h)
volatile uint8_t analog_col_seq[NUM_COLS];
uint8_t analog_key_col[NUM_KEYS];

static volatile uint32_t rate_t0 = 0;
static volatile uint32_t rate_scans = 0;
static volatile uint32_t rate_cycles = 0;

// The timing the ADCs and the timer are set to, and the one the keys are read
// with. They differ only while a probe runs.
static volatile scan_timing_t now;
static scan_timing_t live;
static volatile uint8_t live_flags = 0;
static volatile uint16_t live_faults = 0;
// Rest values from the last one-ADC timing, kept while a timing that came
// through a probe is live so that leaving it puts them straight back
static uint16_t baseline_saved[NUM_KEYS];
static bool baseline_saved_valid = false;
static volatile bool adc23_on = false; // ADC2 and ADC3 clocks enabled
static volatile bool adc23_ok = false; // and their registers answer

// Probe (scan.h). Phases: the scan in progress when it starts is dropped, so
// is the first full scan at the new timing, then full scans are measured.
#define PROBE_IDLE 0u
#define PROBE_SYNC 1u
#define PROBE_SKIP 2u
#define PROBE_RUN 3u
static volatile uint8_t probe_phase = PROBE_IDLE;
static volatile uint8_t probe_state = SCAN_PROBE_NONE;
static volatile uint8_t probe_error = SCAN_PROBE_ERR_NONE;
static scan_timing_t probe_timing;
static uint8_t probe_log2 = 0;
// What a probe is for: just measuring, the timing test asking to keep its
// timing, or the saved scan speed asking for its own
#define PROBE_MEASURE 0u
#define PROBE_GO_LIVE 1u
#define PROBE_SPEED 2u
static uint8_t probe_purpose = PROBE_MEASURE;
static volatile uint32_t probe_left = 0;
static volatile uint32_t probe_t0 = 0;
static volatile uint32_t probe_cycles = 0;
static volatile uint16_t probe_faults = 0;
static volatile uint16_t probe_shift = 0;
static volatile uint8_t probe_shift_key = 0;
static uint32_t probe_sum[NUM_KEYS];
static uint16_t probe_min[NUM_KEYS];
static uint16_t probe_max[NUM_KEYS];

// Stray reading log (scan.h): the last SCAN_STRAY_LOG paired readings that
// disagreed or jumped
typedef struct {
  uint32_t cycles;
  uint32_t sof;
  uint32_t systick;
  int16_t first, second;
  uint8_t late;
  uint8_t key;
  uint8_t kind;
} stray_t;
static stray_t stray_log[SCAN_STRAY_LOG];
static volatile uint32_t stray_count = 0;  // entries logged
static volatile uint32_t pair_strays = 0;  // of which the two disagreed
static uint32_t pair_control = 0;          // paired readings since a control entry

// Since power-up
static volatile uint16_t scan_faults = 0;
static volatile uint16_t scan_sync_errors = 0;
static volatile uint16_t scan_overruns = 0;
static volatile uint16_t scan_stalls = 0;
static volatile uint32_t loop_rate = 0;

static inline void count_up(volatile uint16_t *n) {
  if (*n != 0xFFFFu)
    (*n)++;
}

static inline __attribute__((always_inline)) void column_select(uint8_t col) {
  col_ports[col]->BSRR = (uint32_t)col_pins[col] << 16; // drive low
}

static inline __attribute__((always_inline)) void
column_deselect(uint8_t col) {
  col_ports[col]->BSRR = col_pins[col]; // drive high
}

// A scan speed's own timing
static scan_timing_t speed_timing(uint8_t speed) {
  // TIM10 is on APB2 (84 MHz, divided), so it counts at 2 x 84 = F_CPU
  return (scan_timing_t){speed_modes[speed], speed_samples[speed],
                         (uint16_t)(settle_us[speed] * TICKS_PER_US)};
}

// What the saved scan speed runs as when its own timing cannot be used: a
// one-ADC speed is its own fallback
static scan_timing_t fallback_timing(void) {
  const uint8_t speed = scan_speed;
  return speed_timing(speed_modes[speed] == SCAN_MODE_V73 ? speed
                                                          : SCAN_FALLBACK_SPEED);
}

static bool same_timing(const scan_timing_t *a, const scan_timing_t *b) {
  return a->mode == b->mode && a->sample == b->sample && a->ticks == b->ticks;
}

// A live timing that came through a probe is watched: faults or a stalled
// scan give it up for the fallback
static bool live_guarded(void) {
  return (live_flags & SCAN_FLAG_TUNED) || live.mode != SCAN_MODE_V73;
}

static void set_now(const scan_timing_t *t) {
  now.mode = t->mode;
  now.sample = t->sample;
  now.ticks = t->ticks;
}

static inline __attribute__((always_inline)) void start_settle_timer(void) {
  TIM10->CNT = 0;
  TIM10->CR1 = TIM_CR1_URS | TIM_CR1_OPM | TIM_CR1_CEN;
}

static void rate_reset(void) {
  rate_cycles = 0;
  rate_scans = 0;
  rate_t0 = DWT->CYCCNT;
}

static void adc_power_up(void) {
  ADC1->CR2 |= ADC_CR2_ADON;
  if (now.mode & SCAN_MODE_PARALLEL) {
    ADC2->CR2 |= ADC_CR2_ADON;
    ADC3->CR2 |= ADC_CR2_ADON;
  }
  // t_STAB is 3 us; this loop takes longer than that at 168 MHz
  for (volatile uint32_t i = 0; i < 300; i++)
    ;
}

// The rows an ADC converts, in order, and their sample time
static void adc_sequence(ADC_TypeDef *adc, const uint8_t *rows, uint32_t n,
                         uint32_t sample) {
  uint32_t sqr3 = 0, smpr2 = 0;
  for (uint32_t i = 0; i < n; i++) {
    const uint32_t ch = row_channels[rows[i]];
    sqr3 |= ch << (5u * i);
    smpr2 |= sample << (3u * ch);
  }
  adc->SQR1 = (n - 1u) << 20; // L: sequence length
  adc->SQR3 = sqr3;
  adc->SMPR2 = smpr2;
}

static void dma_stream_stop(DMA_Stream_TypeDef *s) {
  s->CR &= ~DMA_SxCR_EN;
  for (uint32_t i = 0; i < DMA_STOP_TRIES && (s->CR & DMA_SxCR_EN); i++)
    ;
}

// Arm a DMA2 stream: an ADC's data register -> `buf`, `n` halfwords, circular
// (it reloads itself after every sequence). `cr` adds the request channel,
// the priority and any interrupt enables.
static void dma_stream_arm(DMA_Stream_TypeDef *s, uint32_t cr, uint32_t flags,
                           ADC_TypeDef *adc, volatile uint16_t *buf,
                           uint32_t n) {
  dma_stream_stop(s);
  DMA2->LIFCR = flags;
  s->PAR = (uint32_t)&adc->DR;
  s->M0AR = (uint32_t)buf;
  s->NDTR = n;
  s->FCR = 0; // direct mode
  s->CR = cr | DMA_SxCR_MSIZE_0 | DMA_SxCR_PSIZE_0 | DMA_SxCR_MINC |
          DMA_SxCR_CIRC;
  s->CR |= DMA_SxCR_EN;
}

// ADC1's stream ends the column: transfer-complete and error interrupts
#define DMA_ADC1_CR                                                            \
  (DMA_CHANNEL_0 | DMA_SxCR_PL_1 | DMA_SxCR_TCIE | DMA_SxCR_TEIE |             \
   DMA_SxCR_DMEIE)

// Turn ADC2 and ADC3 on for the first time and see whether they are there:
// their clock enable bits and a register each must keep what is written.
static bool adc23_enable(void) {
  const uint32_t both = RCC_APB2ENR_ADC2EN | RCC_APB2ENR_ADC3EN;
  if (adc23_ok)
    return true;
  RCC->APB2ENR |= both;
  (void)RCC->APB2ENR; // a peripheral clock needs two cycles after its enable
  adc23_on = true;
  ADC2->SQR3 = 0x15u;
  ADC3->SQR3 = 0x0Au;
  adc23_ok = (RCC->APB2ENR & both) == both && ADC2->SQR3 == 0x15u &&
             ADC3->SQR3 == 0x0Au;
  return adc23_ok;
}

// Stop the settle timer and switch the ADCs off, which abandons any sequence
// in progress, so results can never land out of order afterwards.
static void scan_halt(void) {
  TIM10->CR1 = TIM_CR1_URS | TIM_CR1_OPM;
  TIM10->SR = 0;
  ADC1->CR2 &= ~(ADC_CR2_ADON | ADC_CR2_DMA);
  ADC1->SR = 0;
  if (adc23_on) {
    // Off, and unable to raise the ADC interrupt (which only looks at them
    // during a parallel read) until scan_arm() sets them up again
    ADC2->CR1 = 0;
    ADC2->CR2 &= ~(ADC_CR2_ADON | ADC_CR2_DMA);
    ADC2->SR = 0;
    ADC3->CR1 = 0;
    ADC3->CR2 &= ~(ADC_CR2_ADON | ADC_CR2_DMA);
    ADC3->SR = 0;
    dma_stream_stop(DMA2_Stream1);
    dma_stream_stop(DMA2_Stream2);
  }
}

// Set the ADCs, their DMA streams and the settle time up for `now`, and
// switch the ADCs on. Call with the scan halted.
static void scan_arm(void) {
  const uint32_t sample = now.sample;
  TIM10->ARR = (uint32_t)now.ticks - 1u;
  if (now.mode & SCAN_MODE_PARALLEL) {
    // ADC2 and ADC3 deliver ahead of ADC1 when their results are due at the
    // same time, and raise no interrupts of their own.
    if (now.mode & SCAN_MODE_PAIRED) {
      adc_sequence(ADC1, pair_rows1, PAIR_N1, sample);
      adc_sequence(ADC2, pair_rows2, 2, sample);
      adc_sequence(ADC3, pair_rows3, PAIR_N3, sample);
      dma_stream_arm(DMA2_Stream1, DMA_CHANNEL_2 | DMA_SxCR_PL, DMA_S1_FLAGS,
                     ADC3, &adc_buffer[PAIR_N1], PAIR_N3);
      dma_stream_arm(DMA2_Stream2, DMA_CHANNEL_1 | DMA_SxCR_PL, DMA_S2_FLAGS,
                     ADC2, &adc_buffer[PAIR_A], 2);
      dma_stream_arm(DMA2_Stream0, DMA_ADC1_CR, DMA_S0_FLAGS, ADC1,
                     adc_buffer, PAIR_N1);
    } else {
      adc_sequence(ADC1, par_rows1, PAR_N1, sample);
      adc_sequence(ADC2, par_rows2, PAR_N2, sample);
      adc_sequence(ADC3, par_rows3, PAR_N3, sample);
      dma_stream_arm(DMA2_Stream1, DMA_CHANNEL_2 | DMA_SxCR_PL, DMA_S1_FLAGS,
                     ADC3, &adc_buffer[PAR_N1 + PAR_N2], PAR_N3);
      dma_stream_arm(DMA2_Stream2, DMA_CHANNEL_1 | DMA_SxCR_PL, DMA_S2_FLAGS,
                     ADC2, &adc_buffer[PAR_N1], PAR_N2);
      dma_stream_arm(DMA2_Stream0, DMA_ADC1_CR, DMA_S0_FLAGS, ADC1,
                     adc_buffer, PAR_N1);
    }
    // 12-bit, one sequence per start, overrun interrupt, DMA requests that
    // continue after each sequence: what ADC1 runs with
    ADC2->CR1 = ADC_CR1_SCAN | ADC_CR1_OVRIE;
    ADC3->CR1 = ADC_CR1_SCAN | ADC_CR1_OVRIE;
    ADC2->CR2 = ADC_CR2_DMA | ADC_CR2_DDS;
    ADC3->CR2 = ADC_CR2_DMA | ADC_CR2_DDS;
  } else {
    adc_sequence(ADC1, slot_row[LAYOUT_SINGLE], NUM_ROWS, sample);
    dma_stream_arm(DMA2_Stream0, DMA_ADC1_CR, DMA_S0_FLAGS, ADC1, adc_buffer,
                   NUM_ROWS);
  }
  ADC1->CR2 |= ADC_CR2_DMA | ADC_CR2_DDS;
  adc_power_up();
}

// Convert the current column again after a full settle time, from a clean
// start. Runs with this driver's interrupts unable to preempt it (from one of
// them, or with interrupts disabled).
static void scan_restart(void) {
  scan_halt();
  scan_arm();
  start_settle_timer();
}

// Back to the live timing after a probe. The caller starts the settle timer.
static void probe_end(uint8_t state) {
  probe_phase = PROBE_IDLE;
  probe_state = state;
  // A rest value capture the probe interrupted starts over
  if (baseline_pass != BASELINE_IDLE)
    baseline_pass = 0;
  scan_halt();
  set_now(&live);
  scan_arm();
  rate_reset();
}

static void baseline_restore(void);

// The saved scan speed did not get its own timing: it runs as its fallback.
// The caller arms the scan for `live` afterwards.
static void speed_refused(void) {
  const scan_timing_t fallback = fallback_timing();
  speed_state = SCAN_SPEED_REFUSED;
  live_flags &= (uint8_t)~SCAN_FLAG_TUNED;
  if (!same_timing(&live, &fallback)) {
    live = fallback;
    baseline_restore();
    baseline_pass = 0;
  }
}

static void probe_abort(uint8_t error) {
  probe_error = error;
  if (probe_purpose == PROBE_SPEED)
    speed_refused();
  probe_end(SCAN_PROBE_FAILED);
  start_settle_timer();
}

// Leaving a live timing from a probe: the rest values from before it apply
// again (and are then captured afresh). Each key's last reading moves with
// its rest value, so its travel carries on from where it was until the first
// readings at the other timing arrive.
static void baseline_restore(void) {
  if (!baseline_saved_valid)
    return;
  for (uint32_t k = 0; k < NUM_KEYS; k++) {
    int32_t raw = (int32_t)adc_raw[k] + (int32_t)baseline_saved[k] -
                  (int32_t)baseline[k];
    if (raw < 0)
      raw = 0;
    if (raw > ADC_MAX_VALUE)
      raw = ADC_MAX_VALUE;
    adc_raw[k] = (uint16_t)raw;
    baseline[k] = baseline_saved[k];
  }
  baseline_saved_valid = false;
}

// Give a live timing that came through a probe up for the fallback
static void live_fallback(void) {
  live = fallback_timing();
  live_flags = (uint8_t)((live_flags & ~SCAN_FLAG_TUNED) | SCAN_FLAG_FELL_BACK);
  if (speed_modes[scan_speed] != SCAN_MODE_V73)
    speed_state = SCAN_SPEED_REFUSED;
  baseline_restore();
  baseline_pass = 0;
  scan_halt();
  set_now(&live);
  scan_arm();
  rate_reset();
  start_settle_timer();
}

// An overrun, a DMA error or missing results: convert the column again. A
// timing under test that keeps doing it is given up. Interrupt context.
static void scan_fault(void) {
  count_up(&scan_faults);
  if (probe_phase != PROBE_IDLE) {
    count_up(&probe_faults);
    if (probe_faults > SCAN_PROBE_MAX_FAULTS) {
      probe_abort(SCAN_PROBE_ERR_FAULTS);
      return;
    }
  } else if (live_guarded()) {
    count_up(&live_faults);
    if (live_faults > SCAN_LIVE_MAX_FAULTS) {
      live_fallback();
      return;
    }
  }
  scan_restart();
}

// Start a probe: read the keyboard at `timing` for 2^log2_scans full scans
// with the keys left on their readings. Call with interrupts disabled and no
// probe running.
static void probe_begin(const scan_timing_t *timing, uint8_t log2_scans,
                        uint8_t purpose) {
  for (uint32_t k = 0; k < NUM_KEYS; k++) {
    probe_sum[k] = 0;
    probe_min[k] = 0xFFFFu;
    probe_max[k] = 0;
  }
  probe_timing = *timing;
  probe_log2 = log2_scans;
  probe_purpose = purpose;
  probe_left = 1u << log2_scans;
  probe_faults = 0;
  probe_cycles = 0;
  probe_shift = 0;
  probe_shift_key = 0;
  probe_error = SCAN_PROBE_ERR_NONE;
  if ((timing->mode & SCAN_MODE_PARALLEL) && !adc23_enable()) {
    // Nothing was switched: the keyboard carries on as it was
    probe_state = SCAN_PROBE_FAILED;
    probe_error = SCAN_PROBE_ERR_NO_ADC;
    if (purpose == PROBE_SPEED) {
      speed_refused();
      scan_halt();
      set_now(&live);
      scan_arm();
      start_settle_timer();
    }
    return;
  }
  probe_state = SCAN_PROBE_RUNNING;
  probe_phase = PROBE_SYNC;
  // The column that is powered stays powered and is converted first
  scan_halt();
  set_now(&probe_timing);
  scan_arm();
  start_settle_timer();
}

void analog_init(void) {
  ADC_ChannelConfTypeDef channel_config = {0};

  // Which row each result slot holds, per mode
  for (uint32_t r = 0; r < NUM_ROWS; r++) {
    slot_row[LAYOUT_SINGLE][r] = (uint8_t)r;
    // Sample times and sequences are written to SMPR2 and SQR3 only
    if (row_channels[r] > 9u)
      board_error_handler();
  }
  uint32_t slot = 0;
  for (uint32_t i = 0; i < PAR_N1; i++)
    slot_row[LAYOUT_PARALLEL][slot++] = par_rows1[i];
  for (uint32_t i = 0; i < PAR_N2; i++)
    slot_row[LAYOUT_PARALLEL][slot++] = par_rows2[i];
  for (uint32_t i = 0; i < PAR_N3; i++)
    slot_row[LAYOUT_PARALLEL][slot++] = par_rows3[i];
  slot = 0;
  for (uint32_t i = 0; i < PAIR_N1; i++)
    slot_row[LAYOUT_PAIRED][slot++] = pair_rows1[i];
  for (uint32_t i = 0; i < PAIR_N3; i++)
    slot_row[LAYOUT_PAIRED][slot++] = pair_rows3[i];
  slot_row[LAYOUT_PAIRED][slot] = ADC_PAIRED_ROW; // PAIR_A; PAIR_B repeats it
  for (uint32_t m = 0; m < LAYOUTS; m++)
    for (uint32_t c = 0; c < NUM_COLS; c++)
      for (uint32_t i = 0; i < NUM_ROWS; i++)
        slot_key[m][c][i] = key_map[slot_row[m][i]][c];
  for (uint32_t r = 0; r < NUM_ROWS; r++)
    for (uint32_t c = 0; c < NUM_COLS; c++)
      if (key_map[r][c] != 0)
        analog_key_col[key_map[r][c] - 1] = (uint8_t)c;

  // Saved scan speed and smoothing; the defaults if there are none or they
  // are unknown
  uint8_t rec[SCAN_REC_SIZE] = {0};
  const uint32_t rec_len = board_rgb_load(RGB_REC_SCAN, rec, sizeof(rec));
  if (rec_len >= 1 && rec[0] < SCAN_SPEED_COUNT)
    scan_speed = rec[0];
  if (rec_len >= 2 && rec[1] < SCAN_FILTER_COUNT)
    filter_level = rec[1];
  // Always start on one ADC. A three-ADC speed asks for its timing once the
  // rest values are in (below).
  live = fallback_timing();
  if (speed_modes[scan_speed] != SCAN_MODE_V73)
    speed_state = SCAN_SPEED_SWITCHING;
  set_now(&live);

  __HAL_RCC_ADC1_CLK_ENABLE();
  __HAL_RCC_DMA2_CLK_ENABLE();
  __HAL_RCC_TIM10_CLK_ENABLE();

  // Row inputs and column outputs are already configured by board_init()
  // exactly as the stock firmware leaves them; make sure every column starts
  // deselected.
  for (uint32_t c = 0; c < NUM_COLS; c++)
    column_deselect((uint8_t)c);

  adc_handle.Instance = ADC1;
  adc_handle.Init.ClockPrescaler = ADC_CLOCK_SYNC_PCLK_DIV4; // 21 MHz, as stock
  adc_handle.Init.Resolution = ADC_RESOLUTION_12B;
  adc_handle.Init.ScanConvMode = ENABLE;
  adc_handle.Init.ContinuousConvMode = DISABLE;
  adc_handle.Init.DiscontinuousConvMode = DISABLE;
  adc_handle.Init.ExternalTrigConvEdge = ADC_EXTERNALTRIGCONVEDGE_NONE;
  adc_handle.Init.ExternalTrigConv = ADC_SOFTWARE_START;
  adc_handle.Init.DataAlign = ADC_DATAALIGN_RIGHT;
  adc_handle.Init.NbrOfConversion = NUM_ROWS;
  // DMA requests continue after each sequence, for the circular DMA stream
  adc_handle.Init.DMAContinuousRequests = ENABLE;
  adc_handle.Init.EOCSelection = ADC_EOC_SEQ_CONV;
  if (HAL_ADC_Init(&adc_handle) != HAL_OK)
    board_error_handler();

  for (uint32_t r = 0; r < NUM_ROWS; r++) {
    channel_config.Channel = row_channels[r];
    channel_config.Rank = r + 1;
    channel_config.SamplingTime = ADC_NUM_SAMPLE_CYCLES;
    if (HAL_ADC_ConfigChannel(&adc_handle, &channel_config) != HAL_OK)
      board_error_handler();
  }

  // One-shot settle timer: counts F_CPU ticks, stops itself at the end
  TIM10->CR1 = TIM_CR1_URS | TIM_CR1_OPM;
  TIM10->PSC = 0;
  TIM10->ARR = (uint32_t)live.ticks - 1u;
  TIM10->EGR = TIM_EGR_UG; // load the prescaler (URS: no interrupt for this)
  TIM10->SR = 0;
  TIM10->DIER = TIM_DIER_UIE;

  ADC1->CR1 |= ADC_CR1_OVRIE;
  // Writes the sequence and sample times HAL has just set once more (the same
  // values), arms the DMA stream and switches ADC1 on
  scan_arm();

  HAL_NVIC_SetPriority(ADC_IRQn, 1, 0);
  HAL_NVIC_SetPriority(DMA2_Stream0_IRQn, 1, 0);
  HAL_NVIC_SetPriority(TIM1_UP_TIM10_IRQn, 1, 0);
  HAL_NVIC_EnableIRQ(ADC_IRQn);
  HAL_NVIC_EnableIRQ(DMA2_Stream0_IRQn);
  HAL_NVIC_EnableIRQ(TIM1_UP_TIM10_IRQn);

  // Kick off the scan loop with column 0
  rate_reset();
  baseline_pass = 0;
  current_col = 0;
  column_select(0);
  start_settle_timer();

  // Wait until every key has a resting baseline. If the scan never runs, the
  // board fail-safe returns to the bootloader on an unconfirmed build.
  while (!baseline_ready)
    ;

  if (speed_state == SCAN_SPEED_SWITCHING) {
    const scan_timing_t target = speed_timing(scan_speed);
    __disable_irq();
    probe_begin(&target, SCAN_TUNE_LOG2, PROBE_SPEED);
    __enable_irq();
  }
}

void analog_task(void) {
  static uint32_t last_count = 0;
  static uint32_t last_change = 0;
  static uint32_t loops = 0;
  static uint32_t loops_since = 0;
  const uint32_t tick = HAL_GetTick();

  // Main loop passes per second (this runs once per pass)
  loops++;
  if (tick - loops_since >= 1000u) {
    loop_rate = loops * 1000u / (tick - loops_since);
    loops = 0;
    loops_since = tick;
  }

  const uint32_t count = column_count;
  if (count != last_count) {
    last_count = count;
    last_change = tick;
  } else if (tick - last_change >= SCAN_STALL_MS) {
    __disable_irq();
    count_up(&scan_stalls);
    if (probe_phase != PROBE_IDLE)
      probe_abort(SCAN_PROBE_ERR_STALL);
    else if (live_guarded())
      live_fallback();
    else
      scan_restart();
    __enable_irq();
    last_change = tick;
  }
}

uint16_t analog_read(uint8_t key) {
  const int32_t raw = adc_raw[key];
  const int32_t rest = baseline[key];
  int32_t delta = raw - rest;

  int8_t dir = polarity[key];
  if (dir == 0 && (delta > ADC_POLARITY_LEARN_THRESHOLD ||
                   delta < -ADC_POLARITY_LEARN_THRESHOLD)) {
    dir = delta > 0 ? 1 : -1;
    polarity[key] = dir;
  }

  if (dir < 0)
    delta = -delta;
  else if (dir == 0 && delta < 0)
    delta = -delta;

  // Report travel on top of a common reference so every key rests at the
  // same value regardless of its sensor's own offset.
  int32_t value = ADC_REST_REFERENCE + delta;
  if (value < 0)
    value = 0;
  if (value > ADC_MAX_VALUE)
    value = ADC_MAX_VALUE;
  return (uint16_t)value;
}

//--------------------------------------------------------------------+
// Scan speed
//--------------------------------------------------------------------+

uint8_t analog_scan_speed(void) { return scan_speed; }

uint8_t analog_settle_us(uint8_t speed) {
  return speed < SCAN_SPEED_COUNT ? settle_us[speed] : 0;
}

uint8_t analog_speed_timing(uint8_t speed) {
  return speed < SCAN_SPEED_COUNT
             ? (uint8_t)(speed_modes[speed] | speed_samples[speed] << 4)
             : 0;
}

uint8_t analog_speed_state(void) { return speed_state; }

uint8_t analog_filter_level(void) { return filter_level; }

bool analog_set_filter_level(uint8_t level) {
  if (level >= SCAN_FILTER_COUNT)
    return false;
  filter_level = level;
  return true;
}

uint32_t analog_loop_rate(void) { return loop_rate; }

bool analog_set_scan_speed(uint8_t speed) {
  if (speed >= SCAN_SPEED_COUNT)
    return false;
  const scan_timing_t target = speed_timing(speed);
  __disable_irq();
  const bool idle =
      probe_phase == PROBE_IDLE && !(live_flags & SCAN_FLAG_TUNED);
  // Already what the keyboard is doing
  if (idle && speed == scan_speed && speed_state == SCAN_SPEED_IN_EFFECT) {
    __enable_irq();
    return true;
  }
  // From one one-ADC speed to another with nothing from a probe in the way,
  // only the settle time changes: the v7.3 path
  const bool plain = idle && live.mode == SCAN_MODE_V73 &&
                     target.mode == SCAN_MODE_V73 &&
                     live.sample == target.sample;
  if (probe_phase != PROBE_IDLE) {
    // A probe in progress is dropped, and the scan put back on the live
    // timing it left
    probe_phase = PROBE_IDLE;
    probe_state = SCAN_PROBE_FAILED;
    probe_error = SCAN_PROBE_ERR_REPLACED;
    if (baseline_pass != BASELINE_IDLE)
      baseline_pass = 0;
    scan_halt();
    set_now(&live);
    scan_arm();
    start_settle_timer();
  }
  scan_speed = speed;
  live_flags &= (uint8_t)~SCAN_FLAG_FELL_BACK;
  if (target.mode == SCAN_MODE_V73) {
    live_flags &= (uint8_t)~SCAN_FLAG_TUNED;
    speed_state = SCAN_SPEED_IN_EFFECT;
    if (plain) {
      live = target;
      now.ticks = live.ticks;
      TIM10->ARR = (uint32_t)live.ticks - 1u;
      // A settle in progress past the new end would run on to the 16-bit
      // overflow; start it again instead (a full settle at the new speed).
      if ((TIM10->CR1 & TIM_CR1_CEN) && TIM10->CNT >= TIM10->ARR)
        TIM10->CNT = 0;
    } else if (!same_timing(&live, &target)) {
      live = target;
      baseline_restore();
      scan_halt();
      set_now(&live);
      scan_arm();
      start_settle_timer();
    }
    // Readings can shift slightly with the settle time: capture every key's
    // rest value again at the new timing (the old ones stay in use
    // meanwhile).
    baseline_pass = 0;
    rate_reset();
  } else if (same_timing(&live, &target)) {
    // The timing test had already put this very timing live
    live_flags &= (uint8_t)~SCAN_FLAG_TUNED;
    speed_state = SCAN_SPEED_IN_EFFECT;
  } else {
    // Three ADCs: measured first, and taken only if every key reads where
    // it did (probe_finish)
    speed_state = SCAN_SPEED_SWITCHING;
    probe_begin(&target, SCAN_TUNE_LOG2, PROBE_SPEED);
  }
  __enable_irq();
  return true;
}

uint32_t analog_scan_rate(void) {
  const uint32_t cycles = rate_cycles;
  if (cycles == 0)
    return 0;
  return (uint32_t)(((uint64_t)F_CPU * SCAN_RATE_WINDOW + cycles / 2u) /
                    cycles);
}

bool analog_baseline_busy(void) {
  return baseline_pass != BASELINE_IDLE || speed_state == SCAN_SPEED_SWITCHING;
}

//--------------------------------------------------------------------+
// Timing test
//--------------------------------------------------------------------+

bool analog_probe_start(uint8_t mode, uint16_t ticks, uint8_t sample,
                        uint8_t log2_scans, bool go_live) {
  if (go_live)
    log2_scans = SCAN_TUNE_LOG2;
  if (mode >= SCAN_MODE_COUNT || sample > 7u || ticks < SCAN_TICKS_MIN ||
      log2_scans < SCAN_PROBE_LOG2_MIN || log2_scans > SCAN_PROBE_LOG2_MAX)
    return false;
  // The paired read is a three-ADC read
  if ((mode & SCAN_MODE_PAIRED) && !(mode & SCAN_MODE_PARALLEL))
    return false;
  if (probe_phase != PROBE_IDLE)
    return false;
  const scan_timing_t timing = {mode, sample, ticks};
  __disable_irq();
  probe_begin(&timing, log2_scans, go_live ? PROBE_GO_LIVE : PROBE_MEASURE);
  __enable_irq();
  return true;
}

static void put16(uint8_t *p, uint32_t v) {
  p[0] = (uint8_t)v;
  p[1] = (uint8_t)(v >> 8);
}

void analog_test_status(uint8_t *out) {
  const uint32_t rate = analog_scan_rate();
  const uint32_t loops = loop_rate;
  uint8_t flags = live_flags;
  if (adc23_ok)
    flags |= SCAN_FLAG_ADC23;
  if (analog_baseline_busy())
    flags |= SCAN_FLAG_CAPTURING;

  memset(out + 1, 0, 63);
  __disable_irq();
  out[1] = SCAN_TEST_PROTOCOL_VERSION;
  out[2] = live.mode;
  put16(out + 3, live.ticks);
  out[5] = live.sample;
  out[6] = flags;
  out[7] = probe_state;
  out[8] = probe_error;
  out[9] = probe_timing.mode;
  put16(out + 10, probe_timing.ticks);
  out[12] = probe_timing.sample;
  out[13] = probe_log2;
  const uint32_t cycles = probe_cycles;
  put16(out + 14, cycles);
  put16(out + 16, cycles >> 16);
  put16(out + 18, probe_shift);
  out[20] = probe_shift_key;
  put16(out + 21, probe_faults);
  put16(out + 23, scan_faults);
  put16(out + 25, scan_sync_errors);
  put16(out + 27, scan_overruns);
  put16(out + 29, scan_stalls);
  __enable_irq();
  put16(out + 31, rate > 0xFFFFu ? 0xFFFFu : rate);
  put16(out + 33, loops);
  out[35] = (uint8_t)(loops > 0xFFFFFFu ? 0xFFu : loops >> 16);
  out[36] = (uint8_t)TICKS_PER_US;
  out[37] = scan_speed;
  out[38] = speed_state;
  out[39] = filter_level;
  const uint32_t strays = pair_strays;
  put16(out + 49, strays);
  put16(out + 51, strays >> 16);
}

_Static_assert(5 + SCAN_STRAY_ENTRY * SCAN_STRAY_REPLY <= 64,
               "stray log reply fits");

void analog_test_strays(uint16_t first, uint8_t *out) {
  memset(out + 1, 0, 63);
  __disable_irq();
  const uint32_t total = stray_count;
  // The entry asked for, as a full count: the one with those low 16 bits
  // nearest below the total; older than the log keeps, the oldest one kept
  uint32_t from = (total & ~0xFFFFu) | first;
  if (from > total)
    from -= 0x10000u;
  if (total - from > SCAN_STRAY_LOG)
    from = total - SCAN_STRAY_LOG;
  uint32_t n = total - from;
  if (n > SCAN_STRAY_REPLY)
    n = SCAN_STRAY_REPLY;
  put16(out + 1, total);
  out[3] = (uint8_t)from;
  out[4] = (uint8_t)n;
  for (uint32_t i = 0; i < n; i++) {
    const stray_t *e = &stray_log[(from + i) % SCAN_STRAY_LOG];
    uint8_t *p = out + 5 + SCAN_STRAY_ENTRY * i;
    put16(p, e->cycles);
    put16(p + 2, e->cycles >> 16);
    put16(p + 4, e->sof);
    put16(p + 6, e->sof >> 16);
    put16(p + 8, e->systick);
    p[10] = (uint8_t)(e->systick >> 16);
    p[11] = e->late;
    p[13] = e->key;
    p[14] = e->kind;
    put16(p + 15, (uint16_t)e->first);
    put16(p + 17, (uint16_t)e->second);
  }
  __enable_irq();
}

bool analog_test_results(uint8_t first, uint8_t *out) {
  if (first >= NUM_KEYS)
    return false;
  const uint8_t state = probe_state;
  memset(out + 1, 0, 63);
  out[1] = state;
  out[2] = first;
  // Readings are only whole once the probe has ended
  if (state != SCAN_PROBE_DONE && state != SCAN_PROBE_FAILED)
    return true;
  uint32_t n = (uint32_t)NUM_KEYS - first;
  if (n > SCAN_RESULT_KEYS)
    n = SCAN_RESULT_KEYS;
  out[3] = (uint8_t)n;
  for (uint32_t i = 0; i < n; i++) {
    const uint32_t k = first + i;
    uint8_t *p = out + 4 + 6u * i;
    put16(p, (probe_sum[k] << 4) >> probe_log2);
    put16(p + 2, probe_min[k]);
    put16(p + 4, probe_max[k]);
  }
  return true;
}

//--------------------------------------------------------------------+
// Interrupt Handlers
//--------------------------------------------------------------------+

// The last measured scan of a probe has just finished: see how far the
// readings are from the ones the keys are using, go live if that was asked
// for and they are close, and return to the live timing.
static void probe_finish(void) {
  uint32_t worst = 0, worst_key = 0;
  for (uint32_t k = 0; k < NUM_KEYS; k++) {
    const int32_t d =
        (int32_t)(probe_sum[k] >> probe_log2) - (int32_t)adc_raw[k];
    const uint32_t a = (uint32_t)(d < 0 ? -d : d);
    if (a > worst) {
      worst = a;
      worst_key = k;
    }
  }
  probe_shift = (uint16_t)worst;
  probe_shift_key = (uint8_t)worst_key;

  uint8_t state = SCAN_PROBE_DONE;
  if (probe_purpose != PROBE_MEASURE) {
    if (worst <= SCAN_TUNE_MAX_SHIFT) {
      // Each key's rest value moves by as much as its reading did, and the
      // key continues from the new timing's average, so its travel carries
      // on from where it was: nothing jumps while the rest values are
      // captured again.
      const bool keep = !baseline_saved_valid;
      for (uint32_t k = 0; k < NUM_KEYS; k++) {
        const int32_t mean = (int32_t)(probe_sum[k] >> probe_log2);
        int32_t rest = (int32_t)baseline[k] + mean - (int32_t)adc_raw[k];
        if (rest < 0)
          rest = 0;
        if (rest > ADC_MAX_VALUE)
          rest = ADC_MAX_VALUE;
        if (keep)
          baseline_saved[k] = baseline[k];
        baseline[k] = (uint16_t)rest;
        adc_raw[k] = (uint16_t)mean;
      }
      baseline_saved_valid = true;
      live = probe_timing;
      if (probe_purpose == PROBE_SPEED) {
        // The saved scan speed's own timing: nothing left of the test
        live_flags &= (uint8_t)~(SCAN_FLAG_FELL_BACK | SCAN_FLAG_TUNED);
        speed_state = SCAN_SPEED_IN_EFFECT;
      } else {
        live_flags =
            (uint8_t)((live_flags & ~SCAN_FLAG_FELL_BACK) | SCAN_FLAG_TUNED);
      }
      live_faults = 0;
      // Every rest value again at the new timing (probe_end does not clear
      // an idle capture, so start it here)
      baseline_pass = 0;
    } else {
      probe_error = SCAN_PROBE_ERR_SHIFT;
      state = SCAN_PROBE_FAILED;
      if (probe_purpose == PROBE_SPEED)
        speed_refused();
    }
  }
  probe_end(state);
}

// End of a full scan (the last column has just been converted). Returns true
// if it ended a probe, which halts the scan and arms it for the live timing:
// the settle timer then has to be started (again).
static bool full_scan_done(void) {
  const uint32_t cyc = DWT->CYCCNT;

  const uint8_t phase = probe_phase;
  if (phase != PROBE_IDLE) {
    if (phase != PROBE_RUN) {
      probe_phase = (uint8_t)(phase + 1u);
      probe_t0 = cyc;
    } else if (--probe_left == 0) {
      probe_cycles = cyc - probe_t0;
      probe_finish();
      return true;
    }
    return false;
  }

  if (++rate_scans >= SCAN_RATE_WINDOW) {
    rate_cycles = cyc - rate_t0;
    rate_t0 = cyc;
    rate_scans = 0;
  }

  const uint8_t pass = baseline_pass;
  if (pass == BASELINE_IDLE)
    return false;
  if (pass == 0) {
    // Skip the scan that was in progress (sensors still powering up at
    // start-up, or partly at the old timing after a change), then average.
    for (uint32_t i = 0; i < NUM_KEYS; i++)
      baseline_acc[i] = 0;
    baseline_pass = 1;
    return false;
  }
  if (pass < ADC_BASELINE_SCANS) {
    baseline_pass = (uint8_t)(pass + 1);
    return false;
  }
  for (uint32_t i = 0; i < NUM_KEYS; i++) {
    const uint16_t avg = (uint16_t)(baseline_acc[i] / ADC_BASELINE_SCANS);
    const int32_t shift = (int32_t)avg - (int32_t)baseline[i];
    if (!baseline_ready ||
        (shift <= ADC_REBASE_MAX_SHIFT && shift >= -ADC_REBASE_MAX_SHIFT))
      baseline[i] = avg;
  }
  baseline_pass = BASELINE_IDLE;
  baseline_ready = true;
  return false;
}

// Store one column's results (`keys`: the key index + 1 of each), or add them
// to a probe's measurements, which the keys do not see. Returns true if they
// were stored: the caller then counts one more reading of the column
// (analog_col_seq), which can wait until the next column is settling.
static inline __attribute__((always_inline)) bool
column_results(const uint8_t *keys, const volatile uint16_t *results) {
  const uint8_t phase = probe_phase;
  if (phase == PROBE_IDLE) {
    const bool accumulate =
        baseline_pass != BASELINE_IDLE && baseline_pass != 0;
    for (uint32_t s = 0; s < NUM_ROWS; s++) {
      const uint8_t k = keys[s];
      if (k == 0)
        continue;
      const uint16_t v = results[s];
      adc_raw[k - 1] = v;
      if (accumulate)
        baseline_acc[k - 1] += v;
    }
    return true;
  } else if (phase == PROBE_RUN) {
    for (uint32_t s = 0; s < NUM_ROWS; s++) {
      const uint8_t k = keys[s];
      if (k == 0)
        continue;
      const uint16_t v = results[s];
      probe_sum[k - 1] += v;
      if (v < probe_min[k - 1])
        probe_min[k - 1] = v;
      if (v > probe_max[k - 1])
        probe_max[k - 1] = v;
    }
  }
  return false;
}

// `arrived`: the cycle counter when the column's results arrived
static void stray_put(uint32_t key, uint8_t kind, int32_t first, int32_t second,
                      uint32_t arrived) {
  stray_t *e = &stray_log[stray_count % SCAN_STRAY_LOG];
  // The SysTick counter is read here, a known number of cycles later
  const uint32_t systick = SysTick->VAL;
  const uint32_t late = (DWT->CYCCNT - arrived) >> 1;
  e->cycles = arrived;
  e->systick = systick;
  e->late = (uint8_t)(late > 255u ? 255u : late);
  e->sof = board_sof_cycles;
  e->first = (int16_t)first;
  e->second = (int16_t)second;
  e->key = (uint8_t)key;
  e->kind = kind;
  stray_count++;
}

// The reading to use for a key whose row was converted twice: the average
// when the two agree; otherwise one of them is a stray reading, and the one
// nearer the key's last reading is kept.
static inline __attribute__((always_inline)) uint16_t
pair_reading(uint32_t key, uint32_t a, uint32_t b, uint32_t arrived) {
  const int32_t last = adc_raw[key];
  const int32_t da = (int32_t)a - last, db = (int32_t)b - last;
  const int32_t apart = da > db ? da - db : db - da;
  if (apart <= SCAN_PAIR_AGREE) {
    const int32_t moved = (da + db) / 2;
    if (moved > SCAN_PAIR_JUMP || moved < -SCAN_PAIR_JUMP) {
      stray_put(key, 2, da, db, arrived);
    } else if (++pair_control >= SCAN_STRAY_CONTROL) {
      pair_control = 0;
      stray_put(key, 0, da, db, arrived);
    }
    return (uint16_t)((a + b + 1u) >> 1);
  }
  pair_strays++;
  stray_put(key, 1, da, db, arrived);
  return (uint16_t)((da < 0 ? -da : da) <= (db < 0 ? -db : db) ? a : b);
}

// Paired read: settle the paired row's two readings into one, then store the
// column like any other. `early`: power the next column first.
static __attribute__((noinline)) void column_done_paired(bool early) {
  const uint32_t arrived = DWT->CYCCNT;
  const uint8_t col = current_col;
  const uint8_t *keys = slot_key[LAYOUT_PAIRED][col];
  // The results are complete (their DMA transfers have ended); take them as
  // whole words, which is quicker than six halfwords
  uint16_t results[NUM_SLOTS];
  memcpy(results, (const void *)adc_buffer, sizeof(results));

  const uint8_t next = col + 1u == NUM_COLS ? 0u : (uint8_t)(col + 1u);
  if (early) {
    column_deselect(col);
    column_select(next);
    start_settle_timer();
  }

  if (keys[PAIR_A] != 0)
    results[PAIR_A] = pair_reading(keys[PAIR_A] - 1u, results[PAIR_A],
                                   results[PAIR_B], arrived);
  const bool stored = column_results(keys, results);

  if (!early)
    column_deselect(col);
  current_col = next;
  column_count++;
  const bool rearmed = next == 0 && full_scan_done();
  if (!early) {
    column_select(next);
    start_settle_timer();
  } else if (rearmed) {
    // A probe that ended here has halted the scan with the next column
    // powered
    start_settle_timer();
  }
  if (stored)
    analog_col_seq[col]++;
}

// Store the results, then switch to the next column (as v7.3 does)
static inline __attribute__((always_inline)) void
column_done_late(const uint8_t (*keys)[NUM_ROWS]) {
  const uint8_t col = current_col;
  const bool stored = column_results(keys[col], adc_buffer);

  // Break before make: power the next column only after this one is off.
  column_deselect(col);
  const uint8_t next = col + 1u == NUM_COLS ? 0u : (uint8_t)(col + 1u);
  current_col = next;
  column_count++;
  if (next == 0)
    full_scan_done();

  column_select(next);
  start_settle_timer();
  if (stored)
    analog_col_seq[col]++;
}

// Take the results out of the DMA buffer, switch to the next column and start
// its settle time at once, and only then store them. If that takes longer
// than the settle time, the timer's interrupt waits for this one to return:
// a settle can still only get longer.
static inline __attribute__((always_inline)) void
column_done_early(const uint8_t (*keys)[NUM_ROWS]) {
  const uint8_t col = current_col;
  // The results are complete (their DMA transfers have ended); take them as
  // whole words, which is quicker than five halfwords
  uint16_t results[NUM_SLOTS];
  memcpy(results, (const void *)adc_buffer, sizeof(results));

  column_deselect(col);
  const uint8_t next = col + 1u == NUM_COLS ? 0u : (uint8_t)(col + 1u);
  column_select(next);
  start_settle_timer();
  current_col = next;
  column_count++;

  if (column_results(keys[col], results))
    analog_col_seq[col]++;
  // A probe that ends here has halted the scan with the next column powered
  if (next == 0 && full_scan_done())
    start_settle_timer();
}

// A column read in any mode but v7.3's. `lisr`: DMA2's flags when ADC1's
// results arrived.
static __attribute__((noinline)) void column_done_other(uint32_t mode,
                                                        uint32_t lisr) {
  if (mode & SCAN_MODE_PARALLEL) {
    // ADC2's and ADC3's results must be in too, whole and without an error
    for (uint32_t i = SCAN_SYNC_TRIES;
         (lisr & DMA_PAR_DONE) != DMA_PAR_DONE && i > 0; i--)
      lisr = DMA2->LISR;
    DMA2->LIFCR = DMA_S1_FLAGS | DMA_S2_FLAGS;
    if ((lisr & DMA_PAR_DONE) != DMA_PAR_DONE || (lisr & DMA_PAR_ERRORS)) {
      count_up(&scan_sync_errors);
      scan_fault();
      return;
    }
  }
  if (mode & SCAN_MODE_PAIRED) {
    column_done_paired((mode & SCAN_MODE_EARLY) != 0);
    return;
  }
  const uint8_t (*keys)[NUM_ROWS] =
      slot_key[mode & SCAN_MODE_PARALLEL ? LAYOUT_PARALLEL : LAYOUT_SINGLE];
  if (mode & SCAN_MODE_EARLY)
    column_done_early(keys);
  else
    column_done_late(keys);
}

// Settle time over: convert the rows of the powered column. In a parallel
// read ADC1 is started last, so it is the last to finish.
void TIM1_UP_TIM10_IRQHandler(void) {
  if (!(TIM10->SR & TIM_SR_UIF))
    return;
  TIM10->SR = ~TIM_SR_UIF;
  if (now.mode & SCAN_MODE_PARALLEL) {
    ADC3->CR2 |= ADC_CR2_SWSTART;
    ADC2->CR2 |= ADC_CR2_SWSTART;
  }
  ADC1->CR2 |= ADC_CR2_SWSTART;
}

// ADC1's results are in adc_buffer
void DMA2_Stream0_IRQHandler(void) {
  uint32_t lisr = DMA2->LISR;
  const uint32_t flags = lisr & DMA_S0_FLAGS;
  DMA2->LIFCR = flags;
  if (flags & (DMA_LISR_TEIF0 | DMA_LISR_DMEIF0)) {
    scan_fault();
    return;
  }
  if (!(flags & DMA_LISR_TCIF0))
    return;
  const uint32_t mode = now.mode;
  if (mode == SCAN_MODE_V73)
    column_done_late(slot_key[LAYOUT_SINGLE]);
  else
    column_done_other(mode, lisr);
}

void ADC_IRQHandler(void) {
  uint32_t sr = ADC1->SR;
  if (now.mode & SCAN_MODE_PARALLEL)
    sr |= ADC2->SR | ADC3->SR;
  if (sr & ADC_SR_OVR) {
    count_up(&scan_overruns);
    scan_fault();
  }
}
