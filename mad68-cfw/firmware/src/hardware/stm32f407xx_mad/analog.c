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
// column is enabled at a time, the sensors are given ADC_SAMPLE_DELAY us to
// settle, then all five rows are converted in a single scan. Pin assignment and
// row/column order were recovered from the stock firmware (see tools/), and
// verified against which per-key record the stock scan code fills.
//
// The physical magnet polarity is not assumed. Each key's resting value is
// captured at start-up; the value handed to the matrix code is
// ADC_REST_REFERENCE plus the distance from that rest value, oriented so it
// always grows as the key is pressed. Until a key has moved far enough to
// learn its direction the absolute distance is used, which is correct for
// either polarity.

#include "hardware/hardware.h"

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

#if !defined(ADC_SAMPLE_DELAY)
#define ADC_SAMPLE_DELAY 40
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

_Static_assert((F_CPU / 1000000) * ADC_SAMPLE_DELAY < 65536,
               "ADC_SAMPLE_DELAY exceeds maximum timer period");

static ADC_HandleTypeDef adc_handle;
static DMA_HandleTypeDef dma_handle;
static TIM_HandleTypeDef tim_handle;

__attribute__((aligned(8))) static volatile uint16_t adc_buffer[NUM_ROWS];
static volatile uint16_t adc_raw[NUM_KEYS];

static volatile uint32_t baseline_acc[NUM_KEYS];
static volatile uint16_t baseline[NUM_KEYS];
static volatile int8_t polarity[NUM_KEYS];
static volatile uint32_t full_scans = 0;
static volatile bool baseline_ready = false;

static volatile uint8_t current_col = 0;

static inline void column_select(uint8_t col) {
  col_ports[col]->BSRR = (uint32_t)col_pins[col] << 16; // drive low
}

static inline void column_deselect(uint8_t col) {
  col_ports[col]->BSRR = col_pins[col]; // drive high
}

static void start_settle_timer(void) {
  __HAL_TIM_SET_COUNTER(&tim_handle, 0);
  HAL_TIM_Base_Start_IT(&tim_handle);
}

void analog_init(void) {
  ADC_ChannelConfTypeDef channel_config = {0};

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
  adc_handle.Init.DMAContinuousRequests = DISABLE;
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

  dma_handle.Instance = DMA2_Stream0;
  dma_handle.Init.Channel = DMA_CHANNEL_0;
  dma_handle.Init.Direction = DMA_PERIPH_TO_MEMORY;
  dma_handle.Init.PeriphInc = DMA_PINC_DISABLE;
  dma_handle.Init.MemInc = DMA_MINC_ENABLE;
  dma_handle.Init.PeriphDataAlignment = DMA_PDATAALIGN_HALFWORD;
  dma_handle.Init.MemDataAlignment = DMA_MDATAALIGN_HALFWORD;
  dma_handle.Init.Mode = DMA_NORMAL;
  dma_handle.Init.Priority = DMA_PRIORITY_HIGH;
  dma_handle.Init.FIFOMode = DMA_FIFOMODE_DISABLE;
  if (HAL_DMA_Init(&dma_handle) != HAL_OK)
    board_error_handler();
  __HAL_LINKDMA(&adc_handle, DMA_Handle, dma_handle);

  // One-shot settle delay after switching columns (TIM10 runs at F_CPU)
  tim_handle.Instance = TIM10;
  tim_handle.Init.Prescaler = 0;
  tim_handle.Init.CounterMode = TIM_COUNTERMODE_UP;
  tim_handle.Init.Period = (F_CPU / 1000000) * ADC_SAMPLE_DELAY - 1;
  tim_handle.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  tim_handle.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
  if (HAL_TIM_Base_Init(&tim_handle) != HAL_OK)
    board_error_handler();
  __HAL_TIM_CLEAR_FLAG(&tim_handle, TIM_FLAG_UPDATE);

  HAL_NVIC_SetPriority(ADC_IRQn, 1, 0);
  HAL_NVIC_SetPriority(DMA2_Stream0_IRQn, 1, 0);
  HAL_NVIC_SetPriority(TIM1_UP_TIM10_IRQn, 1, 0);
  HAL_NVIC_EnableIRQ(ADC_IRQn);
  HAL_NVIC_EnableIRQ(DMA2_Stream0_IRQn);
  HAL_NVIC_EnableIRQ(TIM1_UP_TIM10_IRQn);

  // Kick off the scan loop with column 0
  current_col = 0;
  column_select(0);
  start_settle_timer();

  // Wait until every key has a resting baseline. If the scan never runs, the
  // board fail-safe returns to the bootloader on an unconfirmed build.
  while (!baseline_ready)
    ;
}

void analog_task(void) {}

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
// Interrupt Handlers
//--------------------------------------------------------------------+

void ADC_IRQHandler(void) { HAL_ADC_IRQHandler(&adc_handle); }

void DMA2_Stream0_IRQHandler(void) { HAL_DMA_IRQHandler(&dma_handle); }

void TIM1_UP_TIM10_IRQHandler(void) { HAL_TIM_IRQHandler(&tim_handle); }

void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim) {
  if (htim == &tim_handle) {
    HAL_TIM_Base_Stop_IT(&tim_handle);
    HAL_ADC_Start_DMA(&adc_handle, (uint32_t *)adc_buffer, NUM_ROWS);
  }
}

void HAL_ADC_ConvCpltCallback(ADC_HandleTypeDef *hadc) {
  if (hadc != &adc_handle)
    return;

  const uint8_t col = current_col;
  for (uint32_t r = 0; r < NUM_ROWS; r++) {
    const uint8_t k = key_map[r][col];
    if (k == 0)
      continue;
    adc_raw[k - 1] = adc_buffer[r];
    if (!baseline_ready)
      baseline_acc[k - 1] += adc_buffer[r];
  }

  // Break before make: power the next column only after this one is off.
  column_deselect(col);
  const uint8_t next = (uint8_t)((col + 1) % NUM_COLS);
  current_col = next;

  if (next == 0 && !baseline_ready) {
    // Skip the very first pass (sensors still powering up), then average.
    if (++full_scans > ADC_BASELINE_SCANS) {
      for (uint32_t i = 0; i < NUM_KEYS; i++)
        baseline[i] = (uint16_t)(baseline_acc[i] / ADC_BASELINE_SCANS);
      baseline_ready = true;
    } else if (full_scans == 1) {
      for (uint32_t i = 0; i < NUM_KEYS; i++)
        baseline_acc[i] = 0;
    }
  }

  column_select(next);
  start_settle_timer();
}

void HAL_ADC_ErrorCallback(ADC_HandleTypeDef *hadc) {
  if (hadc != &adc_handle)
    return;
  // Overrun or DMA error: restart the current column cleanly.
  HAL_ADC_Stop_DMA(&adc_handle);
  start_settle_timer();
}
