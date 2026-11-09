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

// Per-key RGB for the MAD68 HE.
//
// The 68 LEDs are WS2812-style (GRB, MSB first) in one serpentine chain on PC9.
// Output is identical to the stock firmware (v1.06 @ 0x0802BBF0/0x08030E60):
// TIM3 channel 4 runs PWM with ARR 1049 at 84 MHz, and DMA1 Stream 2 channel 5
// rewrites CCR4 on every update event from a circular buffer of 1632 bit slots
// (67 ticks = 1, 29 ticks = 0) followed by 224 zero slots (latch). Changing a
// colour is just rewriting that LED's 24 slots; no interrupts are involved.
//
// Brightness is capped at RGB_VAL_MAX and passed through the stock gamma
// curve, so the LEDs never draw more current than the stock firmware allows.
//
// Effects are computed once per 16 ms frame and drawn a few LEDs per main
// loop pass. Spatial effects use each LED's key centre on a 224 x 64 grid.
// Indicators (Caps Lock, active layer, profile) draw over the effect.

#include "rgb.h"

#include "eeconfig.h"
#include "hardware/hardware.h"
#include "keycodes.h"
#include "layout.h"
#include "matrix.h"

#include "stm32f4xx_hal.h"
#include "xinput.h"

#if defined(BOARD_RGB)

#define RGB_BITS (RGB_NUM_LEDS * 24u)
#define RGB_RESET_SLOTS 224u
#define RGB_SLOTS (RGB_BITS + RGB_RESET_SLOTS)
#define RGB_T1 67u
#define RGB_T0 29u
#define RGB_FRAME_MS 16u
#define RGB_SAVE_DELAY_MS 2000u
#define RGB_PROFILE_FLASH_MS 1500u
#define RGB_GAMEPAD_FLASH_MS 1000u
#define RGB_IDLE_MAX_MINUTES 120u
#define RGB_RIPPLES 8u
#define RGB_RIPPLE_END 300u     // how far a ripple from a full press travels
#define RGB_RIPPLE_MIN_END 60u  // ... and from the lightest press
#define RGB_PEAK_MS 250u        // a ripple follows its key's depth this long
#define RGB_GRID_COLS 15u       // coarse grid for Fire and Digital Rain:
#define RGB_GRID_ROWS 5u        // 15 units across, one row per key row
#define RGB_RAIN_TAIL 40u       // Digital Rain tail length (y units)
#define RGB_INDICATOR_MIN_VAL 48u

_Static_assert(RGB_SLOTS == 1856u, "frame length must match stock");
_Static_assert(NUM_KEYS == RGB_NUM_LEDS, "one LED per key");
_Static_assert(NUM_KEYS * 3u <= RGB_REC_MAX_PAYLOAD,
               "per-key colours must fit one record");
_Static_assert(3u + RGB_KEYS_PER_REPORT * 3u <= 64u,
               "per-key colours must fit one report");

enum {
  RGB_MODE_SOLID = 0,
  RGB_MODE_BREATHING,
  RGB_MODE_RAINBOW_WAVE,
  RGB_MODE_RAINBOW_CYCLE,
  RGB_MODE_REACTIVE,
  // Firmware v5
  RGB_MODE_CUSTOM,
  RGB_MODE_KEY_DEPTH,
  RGB_MODE_RIPPLE,
  RGB_MODE_SPLASH,
  RGB_MODE_STARLIGHT,
  RGB_MODE_RAINDROPS,
  RGB_MODE_GRADIENT,
  RGB_MODE_HEATMAP,
  RGB_MODE_DUAL_BREATHING,
  RGB_MODE_WAVE,
  // Firmware v7
  RGB_MODE_FIRE,
  RGB_MODE_DIGITAL_RAIN,
  RGB_MODE_SPIRAL,
  RGB_MODE_REACTIVE_CROSS,
  RGB_MODE_COUNT,
};

enum {
  RGB_DIR_RIGHT = 0,
  RGB_DIR_LEFT,
  RGB_DIR_DOWN,
  RGB_DIR_UP,
  RGB_DIR_OUT,
  RGB_DIR_IN,
  RGB_DIR_COUNT,
};

#define RGB_MODES ((uint32_t)RGB_MODE_COUNT)

#define RGB_FLAG_ON 0x01u
#define RGB_FLAG_FN_KEYS_ADDED 0x02u
#define RGB_FLAG_GP_KEY_ADDED 0x04u // Fn-layer controller toggle added
#define RGB_LEGACY_MAGIC 0xA5u

typedef struct {
  uint8_t flags;
  uint8_t mode;
  uint8_t hue;
  uint8_t sat;
  uint8_t val;
  uint8_t speed;
  // Firmware v5
  uint8_t hue2;
  uint8_t sat2;
  uint8_t direction;
  uint8_t idle_minutes;
  uint8_t ind_flags;
  uint8_t ind_hue;
  uint8_t ind_sat;
  uint8_t reserved[3];
} rgb_config_t;

_Static_assert(sizeof(rgb_config_t) == 16u, "settings record layout");

typedef struct {
  uint8_t r, g, b;
} rgb_t;

typedef struct {
  uint8_t key;
  uint8_t keycode;
} rgb_fn_default_t;

typedef struct {
  uint32_t start;
  uint8_t x;
  uint8_t y;
  uint8_t hue;
  uint8_t active;
  uint8_t key;  // key that started it
  uint8_t peak; // deepest travel of that key so far: sets how far it goes
} ripple_t;

// Exported (not static) so tooling can inspect them.
__attribute__((aligned(4))) volatile uint16_t rgb_slots[RGB_SLOTS];
rgb_config_t rgb_config;
uint8_t rgb_key_colors[NUM_KEYS][3];
uint8_t rgb_host_led_state;
uint8_t rgb_idle_level = 255;
uint32_t rgb_last_activity = 0;

static const uint8_t gamma_lut[256] = RGB_GAMMA_LUT;
static const uint8_t key_to_led[NUM_KEYS] = RGB_KEY_TO_LED;
static const uint8_t led_x[RGB_NUM_LEDS] = RGB_LED_X;
static const uint8_t led_y[RGB_NUM_LEDS] = RGB_LED_Y;
static const rgb_fn_default_t fn_defaults[] = RGB_FN_DEFAULTS;

_Static_assert(M_ARRAY_SIZE(fn_defaults) == RGB_FN_DEFAULTS_COUNT,
               "RGB_FN_DEFAULTS_COUNT mismatch");

static uint8_t led_key[RGB_NUM_LEDS];
// Geometry worked out once at start-up
static uint8_t led_angle[RGB_NUM_LEDS];  // around the board's centre, 0-255
static uint8_t led_radius[RGB_NUM_LEDS]; // from the centre (x units)
static uint8_t led_gcol[RGB_NUM_LEDS];   // Fire / Digital Rain grid column
static uint8_t led_grow[RGB_NUM_LEDS];   // ... and row

static bool config_dirty = false;
static uint32_t config_changed_at = 0;
static bool keys_dirty = false;
static uint32_t keys_version = 0; // bumped whenever the per-key colours change
static uint32_t keys_changed_at = 0;
static uint32_t last_frame = 0;
static bool fn_keys_checked = false;
static bool was_pressed[NUM_KEYS];

// Effect state
static uint8_t react[RGB_NUM_LEDS];     // reactive flash
static uint8_t heat[RGB_NUM_LEDS];      // typing heatmap
static uint8_t glow[RGB_NUM_LEDS];      // key depth with afterglow
static uint8_t spark[RGB_NUM_LEDS];     // starlight / raindrops level
static uint8_t spark_hue[RGB_NUM_LEDS]; // raindrop colour
static ripple_t ripples[RGB_RIPPLES];
static uint32_t next_ripple = 0;
static uint32_t rng = 0x2545F491u;
static uint32_t frame_count = 0;
static uint8_t last_profile = 0xFF;
static uint32_t profile_flash_until = 0;
static uint8_t fire_heat[RGB_GRID_ROWS][RGB_GRID_COLS];
static uint16_t rain_head[RGB_GRID_COLS]; // 1/16 of a y unit
static uint8_t rain_speed[RGB_GRID_COLS]; // 1/16 y units per frame
static uint16_t rain_active;              // one bit per grid column
static uint8_t rain_key[RGB_GRID_COLS];   // key that dropped it (or 0xFF)
static uint32_t rain_start[RGB_GRID_COLS];
static bool last_gamepad_active = true;
static uint32_t gamepad_flash_until = 0;

//--------------------------------------------------------------------+
// Output
//--------------------------------------------------------------------+

static void put_byte(volatile uint16_t *slot, uint8_t value) {
  for (uint32_t bit = 0; bit < 8; bit++)
    slot[bit] = (uint16_t)(((uint32_t)value << bit) & 0x80u ? RGB_T1 : RGB_T0);
}

// Raw wire bytes for one LED (already gamma corrected), in chain order G, R, B.
void rgb_write_grb(uint32_t led, uint8_t g, uint8_t r, uint8_t b) {
  if (led >= RGB_NUM_LEDS)
    return;
  volatile uint16_t *slot = &rgb_slots[led * 24u];
  put_byte(slot, g);
  put_byte(slot + 8, r);
  put_byte(slot + 16, b);
}

static uint8_t output_level(uint32_t component, uint32_t val) {
  // Scale by brightness, then apply the stock gamma curve
  return gamma_lut[(component * val) / 255u];
}

static void set_led(uint32_t led, rgb_t c, uint32_t val) {
  if (val > RGB_VAL_MAX)
    val = RGB_VAL_MAX;
  rgb_write_grb(led, output_level(c.g, val), output_level(c.r, val),
                output_level(c.b, val));
}

//--------------------------------------------------------------------+
// Colour helpers
//--------------------------------------------------------------------+

static rgb_t hsv(uint8_t h, uint8_t s, uint8_t v) {
  rgb_t c;
  if (s == 0) {
    c.r = c.g = c.b = v;
    return c;
  }
  const uint32_t region = h / 43u;
  const uint32_t rem = ((uint32_t)h - region * 43u) * 6u;
  const uint8_t p = (uint8_t)(((uint32_t)v * (255u - s)) >> 8);
  const uint8_t q =
      (uint8_t)(((uint32_t)v * (255u - (((uint32_t)s * rem) >> 8))) >> 8);
  const uint8_t t = (uint8_t)(((uint32_t)v *
                               (255u - (((uint32_t)s * (255u - rem)) >> 8))) >>
                              8);
  switch (region) {
  case 0:
    c.r = v, c.g = t, c.b = p;
    break;
  case 1:
    c.r = q, c.g = v, c.b = p;
    break;
  case 2:
    c.r = p, c.g = v, c.b = t;
    break;
  case 3:
    c.r = p, c.g = q, c.b = v;
    break;
  case 4:
    c.r = t, c.g = p, c.b = v;
    break;
  default:
    c.r = v, c.g = p, c.b = q;
    break;
  }
  return c;
}

static uint8_t lerp8(uint8_t a, uint8_t b, uint32_t t) {
  return (uint8_t)(((uint32_t)a * (255u - t) + (uint32_t)b * t) / 255u);
}

static rgb_t mix(rgb_t a, rgb_t b, uint32_t t) {
  return (rgb_t){lerp8(a.r, b.r, t), lerp8(a.g, b.g, t), lerp8(a.b, b.b, t)};
}

static rgb_t scale(rgb_t c, uint32_t v) {
  return (rgb_t){(uint8_t)(c.r * v / 255u), (uint8_t)(c.g * v / 255u),
                 (uint8_t)(c.b * v / 255u)};
}

static rgb_t brightest(rgb_t a, rgb_t b) {
  return (rgb_t){a.r > b.r ? a.r : b.r, a.g > b.g ? a.g : b.g,
                 a.b > b.b ? a.b : b.b};
}

// Shortest way round the hue circle
static uint8_t hue_lerp(uint8_t a, uint8_t b, uint32_t t) {
  const int32_t d = (int8_t)(uint8_t)(b - a);
  return (uint8_t)((int32_t)a + d * (int32_t)t / 255);
}

static uint32_t tri(uint8_t phase) {
  return phase < 128u ? (uint32_t)phase * 2u : (255u - phase) * 2u;
}

static uint32_t random32(void) {
  rng ^= rng << 13;
  rng ^= rng >> 17;
  rng ^= rng << 5;
  return rng;
}

// Octagonal distance, within ~8% of Euclidean
static uint32_t distance(uint32_t dx, uint32_t dy) {
  return dx > dy ? dx + dy / 2u : dy + dx / 2u;
}

static uint32_t absdiff(uint32_t a, uint32_t b) { return a > b ? a - b : b - a; }

// Position of an LED along a direction, 0-255
static uint8_t led_pos(uint32_t led, uint8_t dir) {
  const uint32_t x = led_x[led], y = led_y[led];
  switch (dir) {
  case RGB_DIR_LEFT:
    return (uint8_t)((224u - x) * 255u / 224u);
  case RGB_DIR_DOWN:
    return (uint8_t)(y * 255u / 64u);
  case RGB_DIR_UP:
    return (uint8_t)((64u - y) * 255u / 64u);
  case RGB_DIR_OUT:
  case RGB_DIR_IN: {
    const uint32_t d = distance(absdiff(x, 112u), absdiff(y, 32u) * 2u);
    const uint32_t n = d * 255u / 144u;
    const uint8_t v = (uint8_t)(n > 255u ? 255u : n);
    return dir == RGB_DIR_OUT ? v : (uint8_t)(255u - v);
  }
  case RGB_DIR_RIGHT:
  default:
    return (uint8_t)(x * 255u / 224u);
  }
}

// Angle of (dx, dy) as 0-255 for a full turn. Linear within each octant,
// which is close enough to atan2 for a rotating pattern.
static uint8_t angle8(int32_t dx, int32_t dy) {
  const uint32_t ax = (uint32_t)(dx < 0 ? -dx : dx);
  const uint32_t ay = (uint32_t)(dy < 0 ? -dy : dy);
  if (ax == 0u && ay == 0u)
    return 0;
  uint32_t a = ax >= ay ? ay * 32u / ax : 64u - ax * 32u / ay;
  if (dx < 0)
    a = 128u - a;
  if (dy < 0)
    a = 256u - a;
  return (uint8_t)a;
}

static void geometry_init(void) {
  for (uint32_t led = 0; led < RGB_NUM_LEDS; led++) {
    const int32_t dx = (int32_t)led_x[led] - 112;
    const int32_t dy = (int32_t)led_y[led] - 32;
    led_angle[led] = angle8(dx, dy);
    const uint32_t r = distance((uint32_t)(dx < 0 ? -dx : dx),
                                (uint32_t)(dy < 0 ? -dy : dy));
    led_radius[led] = (uint8_t)(r > 255u ? 255u : r);
    const uint32_t col = led_x[led] / 15u;
    led_gcol[led] = (uint8_t)(col < RGB_GRID_COLS ? col : RGB_GRID_COLS - 1u);
    const uint32_t row = led_y[led] / 16u;
    led_grow[led] = (uint8_t)(row < RGB_GRID_ROWS ? row : RGB_GRID_ROWS - 1u);
  }
}

// Fire: heat rises from the bottom row and cools on the way up; pressed keys
// add heat where they are, more the deeper they go.
static void fire_step(void) {
  for (uint32_t r = 0; r + 1u < RGB_GRID_ROWS; r++) {
    for (uint32_t c = 0; c < RGB_GRID_COLS; c++) {
      const uint32_t below = fire_heat[r + 1u][c];
      const uint32_t left = fire_heat[r + 1u][c ? c - 1u : c];
      const uint32_t right =
          fire_heat[r + 1u][c + 1u < RGB_GRID_COLS ? c + 1u : c];
      const uint32_t h = (left + 2u * below + right) / 4u;
      // Cooler towards the top, so the flames taper
      const uint32_t cool =
          random32() % (20u + (RGB_GRID_ROWS - 1u - r) * 12u);
      fire_heat[r][c] = (uint8_t)(h > cool ? h - cool : 0u);
    }
  }
  for (uint32_t c = 0; c < RGB_GRID_COLS; c++) {
    const uint32_t rnd = random32();
    uint8_t *h = &fire_heat[RGB_GRID_ROWS - 1u][c];
    if ((rnd & 0xFFu) < 110u)
      *h = (uint8_t)(150u + ((rnd >> 8) % 106u)); // spark
    else
      *h = (uint8_t)(*h * 3u / 4u);
  }
  for (uint32_t k = 0; k < NUM_KEYS; k++) {
    const uint8_t d = key_matrix[k].distance;
    const uint8_t led = key_to_led[k];
    uint8_t *h = &fire_heat[led_grow[led]][led_gcol[led]];
    if (d > 24u && d > *h)
      *h = d;
  }
}

static rgb_t fire_color(uint8_t base_hue, uint8_t heat) {
  if (heat < 16u)
    return (rgb_t){0, 0, 0};
  // Base colour when cool, 40 hue steps on (red -> yellow) when hot, and a
  // whiter core at the hottest
  const uint8_t sat =
      heat > 215u ? (uint8_t)(255u - (uint32_t)(heat - 215u) * 3u) : 255u;
  return hsv((uint8_t)(base_hue + (uint32_t)heat * 40u / 255u), sat, heat);
}

// Digital Rain: one falling streak per grid column at a time. A streak
// dropped by a key falls faster the deeper that key goes.
static uint32_t rain_base_speed(uint8_t speed) {
  return 10u + ((uint32_t)speed >> 4);
}

static void rain_spawn(uint32_t col, uint32_t y, uint32_t speed, uint8_t key,
                       uint32_t now) {
  rain_head[col] = (uint16_t)(y * 16u);
  rain_speed[col] = (uint8_t)(speed > 255u ? 255u : speed);
  rain_key[col] = key;
  rain_start[col] = now;
  rain_active |= (uint16_t)(1u << col);
}

static void rain_step(uint8_t speed, uint32_t now) {
  for (uint32_t c = 0; c < RGB_GRID_COLS; c++) {
    if (!(rain_active & (1u << c)))
      continue;
    // Follow the key down for a moment, as ripples do
    if (rain_key[c] < NUM_KEYS && now - rain_start[c] < RGB_PEAK_MS) {
      const uint32_t s = rain_base_speed(speed) + 2u +
                         key_matrix[rain_key[c]].distance / 8u;
      if (s > rain_speed[c])
        rain_speed[c] = (uint8_t)(s > 255u ? 255u : s);
    }
    rain_head[c] = (uint16_t)(rain_head[c] + rain_speed[c]);
    if (rain_head[c] > (64u + 8u + RGB_RAIN_TAIL) * 16u)
      rain_active &= (uint16_t)~(1u << c);
  }
  if ((random32() & 0xFFu) < 6u + ((uint32_t)speed >> 4)) {
    const uint32_t c = random32() % RGB_GRID_COLS;
    if (!(rain_active & (1u << c)))
      rain_spawn(c, 0, rain_base_speed(speed) + random32() % 8u, 0xFFu, now);
  }
}

static rgb_t rain_color(uint32_t led, rgb_t c1) {
  const uint32_t col = led_gcol[led];
  if (!(rain_active & (1u << col)))
    return (rgb_t){0, 0, 0};
  const int32_t d = (int32_t)(rain_head[col] / 16u) - (int32_t)led_y[led];
  if (d < 0)
    return (rgb_t){0, 0, 0};
  if (d < 8)
    return mix(c1, (rgb_t){255, 255, 255}, 150); // bright head
  if (d < 8 + (int32_t)RGB_RAIN_TAIL)
    return scale(c1, 255u - (uint32_t)(d - 8) * 255u / RGB_RAIN_TAIL);
  return (rgb_t){0, 0, 0};
}

//--------------------------------------------------------------------+
// Settings
//--------------------------------------------------------------------+

static void config_defaults(void) {
  memset(&rgb_config, 0, sizeof(rgb_config));
  rgb_config.flags = RGB_FLAG_ON;
  rgb_config.mode = RGB_MODE_SOLID;
  rgb_config.hue = RGB_DEFAULT_HUE;
  rgb_config.sat = RGB_DEFAULT_SAT;
  rgb_config.val = RGB_DEFAULT_VAL;
  rgb_config.speed = RGB_DEFAULT_SPEED;
  rgb_config.hue2 = (uint8_t)(RGB_DEFAULT_HUE + 128u);
  rgb_config.sat2 = 255;
  rgb_config.direction = RGB_DIR_RIGHT;
  rgb_config.idle_minutes = 0;
  rgb_config.ind_flags = RGB_IND_MASK;
  rgb_config.ind_hue = 0;
  rgb_config.ind_sat = 0; // white
}

static void config_sanitize(void) {
  rgb_config_t *c = &rgb_config;
  if (c->mode >= RGB_MODES)
    c->mode = RGB_MODE_SOLID;
  if (c->val > RGB_VAL_MAX)
    c->val = RGB_VAL_MAX;
  if (c->direction >= RGB_DIR_COUNT)
    c->direction = RGB_DIR_RIGHT;
  if (c->idle_minutes > RGB_IDLE_MAX_MINUTES)
    c->idle_minutes = RGB_IDLE_MAX_MINUTES;
  c->ind_flags &= RGB_IND_MASK;
  memset(c->reserved, 0, sizeof(c->reserved));
}

static uint8_t legacy_checksum(const uint8_t *rec) {
  uint8_t sum = 0x5A;
  for (uint32_t i = 0; i < RGB_LEGACY_RECORD_SIZE - 1; i++)
    sum = (uint8_t)(sum ^ rec[i]);
  return sum;
}

static void config_changed(void) {
  config_dirty = true;
  config_changed_at = HAL_GetTick();
}

static void config_load(void) {
  config_defaults();
  rgb_config_t stored;
  const uint32_t len =
      board_rgb_load(RGB_REC_CONFIG, (uint8_t *)&stored, sizeof(stored));
  if (len >= 6u) {
    // Fields a shorter (older) record does not have keep their defaults
    memcpy(&rgb_config, &stored, len < sizeof(stored) ? len : sizeof(stored));
    config_sanitize();
    return;
  }
  uint8_t rec[RGB_LEGACY_RECORD_SIZE];
  if (board_rgb_load(0, rec, sizeof(rec)) == RGB_LEGACY_RECORD_SIZE &&
      rec[0] == RGB_LEGACY_MAGIC &&
      rec[RGB_LEGACY_RECORD_SIZE - 1] == legacy_checksum(rec)) {
    // Settings from firmware v4; reactive keeps its complementary flash
    rgb_config.flags = rec[1];
    rgb_config.mode = rec[2];
    rgb_config.hue = rec[3];
    rgb_config.sat = rec[4];
    rgb_config.val = rec[5];
    rgb_config.speed = rec[6];
    rgb_config.hue2 = (uint8_t)(rec[3] + 128u);
    config_sanitize();
    config_changed(); // store them in the new form
  }
}

static void config_save(void) {
  board_rgb_store(RGB_REC_CONFIG, (const uint8_t *)&rgb_config,
                  sizeof(rgb_config));
}

static void keys_defaults(void) {
  // A rainbow across the board, so Custom shows something before painting
  for (uint32_t k = 0; k < NUM_KEYS; k++) {
    const rgb_t c = hsv((uint8_t)(led_x[key_to_led[k]] * 255u / 224u), 255, 255);
    rgb_key_colors[k][0] = c.r;
    rgb_key_colors[k][1] = c.g;
    rgb_key_colors[k][2] = c.b;
  }
}

static void keys_load(void) {
  keys_defaults();
  uint8_t stored[NUM_KEYS * 3u];
  if (board_rgb_load(RGB_REC_KEYS, stored, sizeof(stored)) == sizeof(stored))
    memcpy(rgb_key_colors, stored, sizeof(stored));
}

static void keys_changed(void) {
  keys_version++;
  keys_dirty = true;
  keys_changed_at = HAL_GetTick();
}

static void keys_save(void) {
  board_rgb_store(RGB_REC_KEYS, &rgb_key_colors[0][0], sizeof(rgb_key_colors));
}

// Keymaps created by an earlier build have nothing on the Fn-layer keys a
// later build adds (RGB keys, then the controller toggle). Fill those
// positions once, only where they are still transparent.
static void add_fn_key(uint8_t key, uint8_t keycode) {
  for (uint32_t p = 0; p < NUM_PROFILES; p++)
    if (NUM_LAYERS > 1 && eeconfig->profiles[p].keymap[1][key] == KC_TRNS)
      EECONFIG_WRITE(profiles[p].keymap[1][key], &keycode);
}

static void add_fn_keys_once(void) {
  const uint8_t before = rgb_config.flags;
  if (!(rgb_config.flags & RGB_FLAG_FN_KEYS_ADDED)) {
    for (uint32_t i = 0; i < M_ARRAY_SIZE(fn_defaults); i++)
      add_fn_key(fn_defaults[i].key, fn_defaults[i].keycode);
    rgb_config.flags |= RGB_FLAG_FN_KEYS_ADDED;
  }
#if defined(GAMEPAD_TOGGLE_FN_KEY)
  if (!(rgb_config.flags & RGB_FLAG_GP_KEY_ADDED)) {
    add_fn_key(GAMEPAD_TOGGLE_FN_KEY, SP_GAMEPAD_TOG);
    rgb_config.flags |= RGB_FLAG_GP_KEY_ADDED;
  }
#endif
  if (rgb_config.flags != before)
    config_save();
}

//--------------------------------------------------------------------+
// Effects
//--------------------------------------------------------------------+

// Frame state, computed once per frame and then drawn a few LEDs at a time
// so no single main-loop pass is held up for long.
#define RGB_LEDS_PER_PASS 8u

static rgb_config_t frame_cfg;
static uint32_t frame_now;
static uint8_t frame_phase;
static rgb_t frame_c1, frame_c2, frame_ind; // colour 1, colour 2, indicator
static uint8_t frame_layer;
static uint8_t frame_caps_led = 0xFF;
static uint8_t frame_profile_led = 0xFF;
static bool frame_gamepad_flash;        // controller output just switched
static bool frame_gamepad_any_bound;    // current profile has bindings
static rgb_t frame_gamepad_color;       // green = on, blinking red = off
static uint32_t frame_cursor = RGB_NUM_LEDS; // RGB_NUM_LEDS = frame done
// Still pictures (lighting off, Solid, Custom, Gradient) are drawn once and
// then left alone until something that shows changes; the DMA keeps sending
// the last frame to the LEDs.
static bool drawn_still = false;
static uint32_t drawn_signature = 0;

static uint32_t ripple_radius(const ripple_t *r) {
  return ((frame_now - r->start) * (16u + frame_cfg.speed)) >> 9;
}

// How far a ripple travels: further the deeper its key was pressed
static uint32_t ripple_end(const ripple_t *r) {
  return RGB_RIPPLE_MIN_END +
         (uint32_t)r->peak * (RGB_RIPPLE_END - RGB_RIPPLE_MIN_END) / 255u;
}

static void frame_begin(uint32_t now) {
  frame_now = now;
  frame_count++;
  frame_cfg = rgb_config;
  const uint8_t speed = frame_cfg.speed;
  frame_c1 = hsv(frame_cfg.hue, frame_cfg.sat, 255);
  frame_c2 = hsv(frame_cfg.hue2, frame_cfg.sat2, 255);
  frame_ind = hsv(frame_cfg.ind_hue, frame_cfg.ind_sat, 255);

  // Animation phase, 0-255 per cycle; speed 128 is about 2 s per cycle
  frame_phase = (uint8_t)((now * ((uint32_t)speed + 8u)) >> 10);

  // Fades and cooling
  const uint32_t react_fade = ((uint32_t)speed >> 4) + 2u;
  const uint32_t spark_fade = ((uint32_t)speed >> 5) + 2u;
  const bool cool = frame_count % (1u + ((255u - speed) >> 5)) == 0;
  for (uint32_t led = 0; led < RGB_NUM_LEDS; led++) {
    react[led] = (uint8_t)(react[led] > react_fade ? react[led] - react_fade : 0u);
    spark[led] = (uint8_t)(spark[led] > spark_fade ? spark[led] - spark_fade : 0u);
    glow[led] = (uint8_t)(glow[led] > 12u ? glow[led] - 12u : 0u);
    if (cool && heat[led])
      heat[led]--;
  }

  // Key presses: flashes, heat, ripples, key depth, activity
  bool active = false;
  for (uint32_t k = 0; k < NUM_KEYS; k++) {
    const uint8_t led = key_to_led[k];
    const key_state_t *ks = &key_matrix[k];
    const bool pressed = ks->is_pressed;
    if (ks->distance > glow[led])
      glow[led] = ks->distance;
    if (pressed || ks->distance > 24u)
      active = true;
    if (pressed && !was_pressed[k]) {
      react[led] = 255;
      heat[led] = (uint8_t)(heat[led] > 255u - 48u ? 255u : heat[led] + 48u);
      ripple_t *r = &ripples[next_ripple++ % RGB_RIPPLES];
      r->start = now;
      r->x = led_x[led];
      r->y = led_y[led];
      r->hue = (uint8_t)random32();
      r->active = 1;
      r->key = (uint8_t)k;
      r->peak = ks->distance;
      // Digital Rain: a streak falls from the key, faster the deeper it went
      if (frame_cfg.mode == RGB_MODE_DIGITAL_RAIN)
        rain_spawn(led_gcol[led], led_y[led],
                   rain_base_speed(speed) + 2u + ks->distance / 8u, (uint8_t)k,
                   now);
    }
    was_pressed[k] = pressed;
  }
  if (active)
    rgb_last_activity = now;
  for (uint32_t i = 0; i < RGB_RIPPLES; i++) {
    ripple_t *r = &ripples[i];
    if (!r->active)
      continue;
    // Follow the key down for a moment: a press keeps going after it counts
    if (now - r->start < RGB_PEAK_MS && key_matrix[r->key].distance > r->peak)
      r->peak = key_matrix[r->key].distance;
    if (ripple_radius(r) > ripple_end(r))
      r->active = 0;
  }

  // Fire updates every 1-4 frames depending on the speed
  const bool lit = (frame_cfg.flags & RGB_FLAG_ON) != 0;
  if (lit && frame_cfg.mode == RGB_MODE_FIRE &&
      frame_count % (1u + ((255u - speed) >> 6)) == 0)
    fire_step();
  if (lit && frame_cfg.mode == RGB_MODE_DIGITAL_RAIN)
    rain_step(speed, now);

  // Starlight / raindrops: new sparkles at a rate set by the speed
  if (lit && (frame_cfg.mode == RGB_MODE_STARLIGHT ||
              frame_cfg.mode == RGB_MODE_RAINDROPS)) {
    if ((random32() & 0xFFu) < 24u + ((uint32_t)speed >> 1)) {
      const uint32_t led = random32() % RGB_NUM_LEDS;
      if (spark[led] < 64u) {
        spark[led] = 255;
        spark_hue[led] = (uint8_t)random32();
      }
    }
  }

  // Idle dimming: fade out after the set time, back on with any key
  const uint32_t idle_ms = (uint32_t)frame_cfg.idle_minutes * 60000u;
  if (idle_ms && now - rgb_last_activity >= idle_ms)
    rgb_idle_level = (uint8_t)(rgb_idle_level > 4u ? rgb_idle_level - 4u : 0u);
  else
    rgb_idle_level =
        (uint8_t)(rgb_idle_level < 255u - 32u ? rgb_idle_level + 32u : 255u);

  // Indicators
  frame_layer = layout_current_layer();
  frame_caps_led = 0xFF;
  if ((frame_cfg.ind_flags & RGB_IND_CAPS) && (rgb_host_led_state & 0x02u)) {
    for (uint32_t k = 0; k < NUM_KEYS; k++) {
      if (CURRENT_PROFILE.keymap[0][k] == KC_CAPS) {
        frame_caps_led = key_to_led[k];
        break;
      }
    }
  }
  const uint8_t profile = eeconfig->current_profile;
  if (profile != last_profile) {
    if (last_profile != 0xFF)
      profile_flash_until = now + RGB_PROFILE_FLASH_MS;
    last_profile = profile;
  }
  frame_profile_led = 0xFF;
  if ((frame_cfg.ind_flags & RGB_IND_PROFILE) &&
      (int32_t)(profile_flash_until - now) > 0 && 1u + profile < NUM_KEYS)
    frame_profile_led = key_to_led[1u + profile]; // number row: 1, 2, 3, 4

  // Controller output switched (Fn+G): the keys bound to the controller (or
  // the toggle key if none are bound) light green for on, or blink red for
  // off so it reads even over a red effect
  const bool gamepad_active = xinput_is_active();
  if (gamepad_active != last_gamepad_active) {
    last_gamepad_active = gamepad_active;
    if (eeconfig->options.xinput_enabled)
      gamepad_flash_until = now + RGB_GAMEPAD_FLASH_MS;
  }
  frame_gamepad_flash = (frame_cfg.ind_flags & RGB_IND_PROFILE) &&
                        (int32_t)(gamepad_flash_until - now) > 0;
  if (frame_gamepad_flash) {
    frame_gamepad_color = gamepad_active             ? hsv(85, 255, 255)
                          : ((now / 125u) & 1u) == 0u ? hsv(0, 255, 255)
                                                      : (rgb_t){0, 0, 0};
    frame_gamepad_any_bound = false;
    for (uint32_t k = 0; k < NUM_KEYS; k++)
      if (CURRENT_PROFILE.gamepad_buttons[k] != GP_BUTTON_NONE)
        frame_gamepad_any_bound = true;
  }

  // A still picture is only drawn again when something on it changes
  bool still = !frame_gamepad_flash;
  if (still && lit)
    still = frame_cfg.mode == RGB_MODE_SOLID ||
            frame_cfg.mode == RGB_MODE_CUSTOM ||
            frame_cfg.mode == RGB_MODE_GRADIENT;
  if (still) {
    // FNV-1a over everything a still frame depends on
    uint32_t h = 2166136261u;
    const uint8_t *cfg_bytes = (const uint8_t *)&frame_cfg;
    for (uint32_t i = 0; i < sizeof(frame_cfg); i++)
      h = (h ^ cfg_bytes[i]) * 16777619u;
    const uint32_t parts[] = {rgb_idle_level,      frame_layer,
                              frame_caps_led,      frame_profile_led,
                              keys_version,        eeconfig->current_profile};
    for (uint32_t i = 0; i < M_ARRAY_SIZE(parts); i++)
      h = (h ^ parts[i]) * 16777619u;
    if (drawn_still && h == drawn_signature) {
      frame_cursor = RGB_NUM_LEDS; // unchanged: nothing to draw
      return;
    }
    drawn_signature = h;
  }
  drawn_still = still;
  frame_cursor = 0;
}

// Strongest ring of all active ripples at an LED, 0-255. A cross only runs
// along the key's row and column.
static uint32_t ripple_ring(uint32_t led, uint32_t width, uint8_t *hue,
                            bool cross) {
  uint32_t best = 0;
  for (uint32_t i = 0; i < RGB_RIPPLES; i++) {
    const ripple_t *r = &ripples[i];
    if (!r->active)
      continue;
    const uint32_t radius = ripple_radius(r);
    const uint32_t dx = absdiff(led_x[led], r->x);
    const uint32_t dy = absdiff(led_y[led], r->y);
    uint32_t d;
    if (!cross)
      d = distance(dx, dy);
    else if (dy < 8u)
      d = dx; // same row
    else if (dx < 8u)
      d = dy; // same column
    else
      continue;
    const uint32_t off = absdiff(d, radius);
    if (off >= width)
      continue;
    // Rings get fainter as they spread, and stop where the press sent them
    const uint32_t end = ripple_end(r);
    const uint32_t level = (255u - off * 255u / width) *
                           (end - (radius < end ? radius : end)) / end;
    if (level > best) {
      best = level;
      if (hue)
        *hue = (uint8_t)(r->hue + d);
    }
  }
  return best;
}

static rgb_t effect_color(uint32_t led) {
  const rgb_config_t *c = &frame_cfg;
  const uint8_t phase = frame_phase;
  const rgb_t c1 = frame_c1;
  const rgb_t c2 = frame_c2;
  switch (c->mode) {
  case RGB_MODE_BREATHING:
    return hsv(c->hue, c->sat, (uint8_t)(32u + (tri(phase) * 223u) / 255u));
  case RGB_MODE_RAINBOW_WAVE:
    return hsv((uint8_t)(c->hue + led_pos(led, c->direction) * 2u / 3u - phase),
               c->sat, 255);
  case RGB_MODE_RAINBOW_CYCLE:
    return hsv((uint8_t)(c->hue + phase), c->sat, 255);
  case RGB_MODE_REACTIVE:
    return mix(scale(c1, 48), c2, react[led]);
  case RGB_MODE_CUSTOM: {
    const uint8_t *k = rgb_key_colors[led_key[led]];
    return (rgb_t){k[0], k[1], k[2]};
  }
  case RGB_MODE_KEY_DEPTH: {
    const uint32_t g = glow[led];
    return scale(mix(c1, c2, g), 24u + g * 231u / 255u);
  }
  case RGB_MODE_RIPPLE:
    return mix(scale(c1, 40), c2, ripple_ring(led, 18u, NULL, false));
  case RGB_MODE_SPLASH: {
    uint8_t hue = 0;
    const uint32_t level = ripple_ring(led, 28u, &hue, false);
    return level ? hsv(hue, c->sat, (uint8_t)level) : (rgb_t){0, 0, 0};
  }
  case RGB_MODE_STARLIGHT:
    return brightest(scale(c1, 40), scale(c2, spark[led]));
  case RGB_MODE_RAINDROPS:
    return brightest(scale(c1, 16), hsv(spark_hue[led], c->sat, spark[led]));
  case RGB_MODE_GRADIENT: {
    const uint8_t t = led_pos(led, c->direction);
    return hsv(hue_lerp(c->hue, c->hue2, t), lerp8(c->sat, c->sat2, t), 255);
  }
  case RGB_MODE_HEATMAP: {
    const uint32_t h = heat[led];
    return hsv((uint8_t)(170u - h * 170u / 255u), 255, (uint8_t)(32u + h * 223u / 255u));
  }
  case RGB_MODE_DUAL_BREATHING:
    return mix(c1, c2, tri(phase));
  case RGB_MODE_WAVE: {
    const uint32_t w = tri((uint8_t)(led_pos(led, c->direction) - phase));
    const uint32_t band = w > 128u ? (w - 128u) * 2u : 0u;
    return mix(scale(c2, 64), c1, band > 255u ? 255u : band);
  }
  case RGB_MODE_FIRE:
    return fire_color(c->hue, fire_heat[led_grow[led]][led_gcol[led]]);
  case RGB_MODE_DIGITAL_RAIN:
    return rain_color(led, c1);
  case RGB_MODE_SPIRAL:
    return hsv((uint8_t)(c->hue + led_angle[led] + led_radius[led] - phase),
               c->sat, 255);
  case RGB_MODE_REACTIVE_CROSS:
    return mix(scale(c1, 40), c2, ripple_ring(led, 14u, NULL, true));
  case RGB_MODE_SOLID:
  default:
    return c1;
  }
}

static void draw_led(uint32_t led) {
  const rgb_config_t *c = &frame_cfg;
  const uint32_t ind_val = c->val > RGB_INDICATOR_MIN_VAL ? c->val : RGB_INDICATOR_MIN_VAL;
  const rgb_t ind = frame_ind;

  // Caps Lock and profile indicators show even with the lighting off
  if (led == frame_caps_led || led == frame_profile_led) {
    set_led(led, ind, ind_val);
    return;
  }
  if (frame_gamepad_flash) {
    const uint8_t key = led_key[led];
    bool flash = CURRENT_PROFILE.gamepad_buttons[key] != GP_BUTTON_NONE;
    if (!frame_gamepad_any_bound)
      for (uint32_t l = 0; l < NUM_LAYERS && !flash; l++)
        flash = CURRENT_PROFILE.keymap[l][key] == SP_GAMEPAD_TOG;
    if (flash) {
      set_led(led, frame_gamepad_color, ind_val);
      return;
    }
  }
  if (!(c->flags & RGB_FLAG_ON)) {
    rgb_write_grb(led, 0, 0, 0);
    return;
  }
  rgb_t color = effect_color(led);
  uint32_t val = (uint32_t)c->val * rgb_idle_level / 255u;

  // While a layer is held, light the keys that do something on it
  if ((c->ind_flags & RGB_IND_LAYER) && frame_layer > 0 &&
      frame_layer < NUM_LAYERS) {
    const uint8_t kc = CURRENT_PROFILE.keymap[frame_layer][led_key[led]];
    if (kc != KC_TRNS && kc != KC_NO) {
      color = ind;
      val = ind_val;
    } else {
      val /= 6u;
    }
  }
  set_led(led, color, val);
}

//--------------------------------------------------------------------+
// API
//--------------------------------------------------------------------+

void rgb_init(void) {
  for (uint32_t k = 0; k < NUM_KEYS; k++)
    led_key[key_to_led[k]] = (uint8_t)k;
  geometry_init();
  config_load();
  keys_load();

  for (uint32_t i = 0; i < RGB_SLOTS; i++)
    rgb_slots[i] = (uint16_t)(i < RGB_BITS ? RGB_T0 : 0u);

  __HAL_RCC_TIM3_CLK_ENABLE();
  __HAL_RCC_TIM3_CLK_SLEEP_ENABLE();
  __HAL_RCC_TIM3_FORCE_RESET();
  __HAL_RCC_TIM3_RELEASE_RESET();
  __HAL_RCC_DMA1_CLK_ENABLE();

  // PC9: TIM3_CH4, push-pull, very high speed, no pull (as stock)
  GPIO_InitTypeDef gpio_init = {0};
  gpio_init.Pin = GPIO_PIN_9;
  gpio_init.Mode = GPIO_MODE_AF_PP;
  gpio_init.Pull = GPIO_NOPULL;
  gpio_init.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  gpio_init.Alternate = GPIO_AF2_TIM3;
  HAL_GPIO_Init(GPIOC, &gpio_init);

  // TIM3: stock register values (84 MHz, period 1050 ticks, PWM1 + preload)
  TIM3->CCMR1 = 0x6868u;
  TIM3->CCMR2 = 0x6868u;
  TIM3->PSC = 0;
  TIM3->ARR = 1049u;
  TIM3->CCR4 = 0;
  TIM3->CCER = TIM_CCER_CC4E;
  TIM3->DIER = TIM_DIER_UDE;

  // DMA1 Stream 2 channel 5 (TIM3_UP/CH4): memory -> CCR4, circular,
  // half-words, very high priority; stock CR 0x0A032D41, FCR 0x21
  DMA1_Stream2->CR = 0;
  while (DMA1_Stream2->CR & DMA_SxCR_EN)
    ;
  DMA1->LIFCR = DMA_LIFCR_CTCIF2 | DMA_LIFCR_CHTIF2 | DMA_LIFCR_CTEIF2 |
                DMA_LIFCR_CDMEIF2 | DMA_LIFCR_CFEIF2;
  DMA1_Stream2->PAR = (uint32_t)&TIM3->CCR4;
  DMA1_Stream2->M0AR = (uint32_t)rgb_slots;
  DMA1_Stream2->NDTR = RGB_SLOTS;
  DMA1_Stream2->FCR = 0x21u;
  DMA1_Stream2->CR = 0x0A032D40u;
  DMA1_Stream2->CR |= DMA_SxCR_EN;

  TIM3->CR1 = TIM_CR1_ARPE | TIM_CR1_URS | TIM_CR1_CEN; // 0x85 as stock
}

void rgb_task(void) {
  const uint32_t now = HAL_GetTick();

  if (!fn_keys_checked) {
    fn_keys_checked = true;
    add_fn_keys_once();
  }

  if (frame_cursor >= RGB_NUM_LEDS && now - last_frame >= RGB_FRAME_MS) {
    last_frame = now;
    frame_begin(now);
  }
  for (uint32_t n = 0; n < RGB_LEDS_PER_PASS && frame_cursor < RGB_NUM_LEDS;
       n++)
    draw_led(frame_cursor++);

  if (config_dirty && now - config_changed_at >= RGB_SAVE_DELAY_MS) {
    config_dirty = false;
    config_save();
  }
  if (keys_dirty && now - keys_changed_at >= RGB_SAVE_DELAY_MS) {
    keys_dirty = false;
    keys_save();
  }
}

void rgb_keycode(uint8_t keycode) {
  rgb_config_t *c = &rgb_config;
  switch (keycode) {
  case SP_RGB_TOG:
    c->flags ^= RGB_FLAG_ON;
    break;
  case SP_RGB_MOD:
    c->mode = (uint8_t)((c->mode + 1u) % RGB_MODES);
    break;
  case SP_RGB_RMOD:
    c->mode = (uint8_t)((c->mode + RGB_MODES - 1u) % RGB_MODES);
    break;
  case SP_RGB_HUI:
    c->hue = (uint8_t)(c->hue + 8u);
    break;
  case SP_RGB_HUD:
    c->hue = (uint8_t)(c->hue - 8u);
    break;
  case SP_RGB_VAI:
    c->val = (uint8_t)(c->val + 16u > RGB_VAL_MAX ? RGB_VAL_MAX : c->val + 16u);
    break;
  case SP_RGB_VAD:
    c->val = (uint8_t)(c->val > 16u ? c->val - 16u : 0u);
    break;
  case SP_RGB_SPI:
    c->speed = (uint8_t)(c->speed + 16u > 255u ? 255u : c->speed + 16u);
    break;
  case SP_RGB_SPD:
    c->speed = (uint8_t)(c->speed > 16u ? c->speed - 16u : 0u);
    break;
  default:
    return;
  }
  // Any adjustment turns the lighting back on
  if (keycode != SP_RGB_TOG)
    c->flags |= RGB_FLAG_ON;
  config_changed();
}

void rgb_host_leds(uint8_t leds) { rgb_host_led_state = leds; }

static void rgb_fill_response(uint8_t *out) {
  const rgb_config_t *c = &rgb_config;
  out[1] = (uint8_t)(c->flags & RGB_FLAG_ON);
  out[2] = c->mode;
  out[3] = c->hue;
  out[4] = c->sat;
  out[5] = c->val;
  out[6] = c->speed;
  out[7] = (uint8_t)RGB_MODES;
  out[8] = RGB_VAL_MAX;
  out[9] = RGB_NUM_LEDS;
  out[10] = RGB_PROTOCOL_VERSION;
  out[11] = c->hue2;
  out[12] = c->sat2;
  out[13] = c->direction;
  out[14] = c->idle_minutes;
  out[15] = c->ind_flags;
  out[16] = c->ind_hue;
  out[17] = c->ind_sat;
  out[18] = (uint8_t)RGB_DIR_COUNT;
  out[19] = NUM_KEYS;
}

static void rgb_fill_keys(uint8_t *out, uint32_t first) {
  const uint32_t left = NUM_KEYS - first;
  const uint32_t count = left < RGB_KEYS_PER_REPORT ? left : RGB_KEYS_PER_REPORT;
  out[1] = (uint8_t)first;
  out[2] = (uint8_t)count;
  memcpy(out + 3, rgb_key_colors[first], count * 3u);
}

bool rgb_command(const uint8_t *in, uint8_t *out) {
  rgb_config_t *c = &rgb_config;
  switch (in[0]) {
  case BOARD_COMMAND_GET_RGB:
    rgb_fill_response(out);
    return true;
  case BOARD_COMMAND_SET_RGB: {
    const bool extended = in[7] == RGB_SET_EXTENDED;
    if (in[2] >= RGB_MODES)
      return false;
    if (extended &&
        (in[10] >= RGB_DIR_COUNT || in[11] > RGB_IDLE_MAX_MINUTES))
      return false;
    c->flags = (uint8_t)((c->flags & ~RGB_FLAG_ON) | (in[1] & RGB_FLAG_ON));
    c->mode = in[2];
    c->hue = in[3];
    c->sat = in[4];
    c->val = in[5] <= RGB_VAL_MAX ? in[5] : (uint8_t)RGB_VAL_MAX;
    c->speed = in[6];
    if (extended) {
      c->hue2 = in[8];
      c->sat2 = in[9];
      c->direction = in[10];
      c->idle_minutes = in[11];
      c->ind_flags = (uint8_t)(in[12] & RGB_IND_MASK);
      c->ind_hue = in[13];
      c->ind_sat = in[14];
    }
    config_changed();
    rgb_fill_response(out);
    return true;
  }
  case BOARD_COMMAND_GET_RGB_KEYS:
    if (in[1] >= NUM_KEYS)
      return false;
    rgb_fill_keys(out, in[1]);
    return true;
  case BOARD_COMMAND_SET_RGB_KEYS: {
    const uint32_t first = in[1], count = in[2];
    if (count == 0 || count > RGB_KEYS_PER_REPORT || first + count > NUM_KEYS)
      return false;
    memcpy(rgb_key_colors[first], in + 3, count * 3u);
    keys_changed();
    rgb_fill_keys(out, first);
    return true;
  }
  default:
    return false;
  }
}

#endif
