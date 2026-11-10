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
// select line (power switch); the five rows are wired to ADC1 inputs. One
// column is enabled at a time, the sensors are given a settle time, then all
// five rows are converted in a single sequence. Pin assignment and row/column
// order were recovered from the stock firmware (see tools/), and verified
// against which per-key record the stock scan code fills.
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
// The physical magnet polarity is not assumed. Each key's resting value is
// captured at start-up (and again after a scan speed change); the value handed
// to the matrix code is ADC_REST_REFERENCE plus the distance from that rest
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

// ADC1 channel for each row, in conversion rank order
static const uint32_t row_channels[NUM_ROWS] = ADC_COLUMN_SCAN_ROW_CHANNELS;
// Column select lines (active low)
static GPIO_TypeDef *const col_ports[NUM_COLS] = ADC_COLUMN_SCAN_COL_PORTS;
static const uint16_t col_pins[NUM_COLS] = ADC_COLUMN_SCAN_COL_PINS;
// Key index + 1 for each matrix position, 0 where there is no switch
static const uint8_t key_map[NUM_ROWS][NUM_COLS] = ADC_COLUMN_SCAN_MATRIX;

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
// When the rest values are captured again after a scan speed change, a key
// whose reading moved further than this from its old rest value is being held
// down, and keeps its old rest value.
#define ADC_REBASE_MAX_SHIFT ADC_POLARITY_LEARN_THRESHOLD
#endif

#if !defined(SCAN_STALL_MS)
// No column converted for this long: re-arm the ADC, DMA and timer
#define SCAN_STALL_MS 100u
#endif

// Full scans per scan-rate measurement
#define SCAN_RATE_WINDOW 64u

// DMA2 Stream0 flag bits in LISR/LIFCR (FEIF0, DMEIF0, TEIF0, HTIF0, TCIF0)
#define DMA_S0_FLAGS 0x3Du

static const uint8_t settle_us[SCAN_SPEED_COUNT] = SCAN_SETTLE_US;

_Static_assert((F_CPU / 1000000) * 255u <= 65536u,
               "a settle time must fit TIM10's 16-bit period");

static ADC_HandleTypeDef adc_handle;

__attribute__((aligned(8))) static volatile uint16_t adc_buffer[NUM_ROWS];
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
static volatile uint32_t column_count = 0;

static volatile uint32_t rate_t0 = 0;
static volatile uint32_t rate_scans = 0;
static volatile uint32_t rate_cycles = 0;

static inline void column_select(uint8_t col) {
  col_ports[col]->BSRR = (uint32_t)col_pins[col] << 16; // drive low
}

static inline void column_deselect(uint8_t col) {
  col_ports[col]->BSRR = col_pins[col]; // drive high
}

static inline uint32_t settle_ticks(uint8_t speed) {
  // TIM10 is on APB2 (84 MHz, divided), so it counts at 2 x 84 = F_CPU
  return (uint32_t)settle_us[speed] * (F_CPU / 1000000u) - 1u;
}

static inline void start_settle_timer(void) {
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
  // t_STAB is 3 us; this loop takes longer than that at 168 MHz
  for (volatile uint32_t i = 0; i < 300; i++)
    ;
}

// Arm DMA2 Stream0: ADC1 data register -> adc_buffer, NUM_ROWS halfwords,
// circular (it reloads itself after every sequence), transfer-complete and
// error interrupts only.
static void dma_arm(void) {
  DMA2_Stream0->CR &= ~DMA_SxCR_EN;
  while (DMA2_Stream0->CR & DMA_SxCR_EN)
    ;
  DMA2->LIFCR = DMA_S0_FLAGS;
  DMA2_Stream0->PAR = (uint32_t)&ADC1->DR;
  DMA2_Stream0->M0AR = (uint32_t)adc_buffer;
  DMA2_Stream0->NDTR = NUM_ROWS;
  DMA2_Stream0->FCR = 0; // direct mode
  DMA2_Stream0->CR = DMA_CHANNEL_0 | DMA_SxCR_PL_1 | DMA_SxCR_MSIZE_0 |
                     DMA_SxCR_PSIZE_0 | DMA_SxCR_MINC | DMA_SxCR_CIRC |
                     DMA_SxCR_TCIE | DMA_SxCR_TEIE | DMA_SxCR_DMEIE;
  DMA2_Stream0->CR |= DMA_SxCR_EN;
}

// Overrun, DMA error or a stalled scan: stop everything, power-cycle the ADC
// (which abandons any sequence in progress, so the five results can never
// land out of order), re-arm the DMA and convert the current column again
// after a full settle time. Runs with this driver's interrupts unable to
// preempt it (from one of them, or with interrupts disabled).
static void scan_restart(void) {
  TIM10->CR1 = TIM_CR1_URS | TIM_CR1_OPM;
  TIM10->SR = 0;
  ADC1->CR2 &= ~(ADC_CR2_ADON | ADC_CR2_DMA);
  ADC1->SR = 0;
  dma_arm();
  ADC1->CR2 |= ADC_CR2_DMA | ADC_CR2_DDS;
  adc_power_up();
  start_settle_timer();
}

void analog_init(void) {
  ADC_ChannelConfTypeDef channel_config = {0};

  // Saved scan speed; the default if there is none or it is unknown
  uint8_t rec[SCAN_REC_SIZE] = {0};
  if (board_rgb_load(RGB_REC_SCAN, rec, sizeof(rec)) >= 1 &&
      rec[0] < SCAN_SPEED_COUNT)
    scan_speed = rec[0];

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
  TIM10->ARR = settle_ticks(scan_speed);
  TIM10->EGR = TIM_EGR_UG; // load the prescaler (URS: no interrupt for this)
  TIM10->SR = 0;
  TIM10->DIER = TIM_DIER_UIE;

  dma_arm();
  ADC1->CR1 |= ADC_CR1_OVRIE;
  ADC1->CR2 |= ADC_CR2_DMA | ADC_CR2_DDS;
  adc_power_up();

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
}

void analog_task(void) {
  static uint32_t last_count = 0;
  static uint32_t last_change = 0;
  const uint32_t now = HAL_GetTick();
  const uint32_t count = column_count;
  if (count != last_count) {
    last_count = count;
    last_change = now;
  } else if (now - last_change >= SCAN_STALL_MS) {
    __disable_irq();
    scan_restart();
    __enable_irq();
    last_change = now;
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

bool analog_set_scan_speed(uint8_t speed) {
  if (speed >= SCAN_SPEED_COUNT)
    return false;
  if (speed == scan_speed)
    return true;
  __disable_irq();
  scan_speed = speed;
  TIM10->ARR = settle_ticks(speed);
  // A settle in progress past the new end would run on to the 16-bit
  // overflow; start it again instead (a full settle at the new speed).
  if ((TIM10->CR1 & TIM_CR1_CEN) && TIM10->CNT >= TIM10->ARR)
    TIM10->CNT = 0;
  // Readings can shift slightly with the settle time: capture every key's
  // rest value again at the new timing (the old ones stay in use meanwhile).
  baseline_pass = 0;
  rate_reset();
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

bool analog_baseline_busy(void) { return baseline_pass != BASELINE_IDLE; }

//--------------------------------------------------------------------+
// Interrupt Handlers
//--------------------------------------------------------------------+

// End of a full scan (the last column has just been converted)
static void full_scan_done(void) {
  const uint32_t now = DWT->CYCCNT;
  if (++rate_scans >= SCAN_RATE_WINDOW) {
    rate_cycles = now - rate_t0;
    rate_t0 = now;
    rate_scans = 0;
  }

  const uint8_t pass = baseline_pass;
  if (pass == BASELINE_IDLE)
    return;
  if (pass == 0) {
    // Skip the scan that was in progress (sensors still powering up at
    // start-up, or partly at the old timing after a change), then average.
    for (uint32_t i = 0; i < NUM_KEYS; i++)
      baseline_acc[i] = 0;
    baseline_pass = 1;
    return;
  }
  if (pass < ADC_BASELINE_SCANS) {
    baseline_pass = (uint8_t)(pass + 1);
    return;
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
}

static void column_done(void) {
  const uint8_t col = current_col;
  const bool accumulate =
      baseline_pass != BASELINE_IDLE && baseline_pass != 0;
  for (uint32_t r = 0; r < NUM_ROWS; r++) {
    const uint8_t k = key_map[r][col];
    if (k == 0)
      continue;
    const uint16_t v = adc_buffer[r];
    adc_raw[k - 1] = v;
    if (accumulate)
      baseline_acc[k - 1] += v;
  }

  // Break before make: power the next column only after this one is off.
  column_deselect(col);
  const uint8_t next = col + 1u == NUM_COLS ? 0u : (uint8_t)(col + 1u);
  current_col = next;
  column_count++;
  if (next == 0)
    full_scan_done();

  column_select(next);
  start_settle_timer();
}

// Settle time over: convert the five rows of the powered column
void TIM1_UP_TIM10_IRQHandler(void) {
  if (!(TIM10->SR & TIM_SR_UIF))
    return;
  TIM10->SR = ~TIM_SR_UIF;
  ADC1->CR2 |= ADC_CR2_SWSTART;
}

// The five results are in adc_buffer
void DMA2_Stream0_IRQHandler(void) {
  const uint32_t flags = DMA2->LISR & DMA_S0_FLAGS;
  DMA2->LIFCR = flags;
  if (flags & (DMA_LISR_TEIF0 | DMA_LISR_DMEIF0)) {
    scan_restart();
    return;
  }
  if (flags & DMA_LISR_TCIF0)
    column_done();
}

void ADC_IRQHandler(void) {
  if (ADC1->SR & ADC_SR_OVR)
    scan_restart();
}
