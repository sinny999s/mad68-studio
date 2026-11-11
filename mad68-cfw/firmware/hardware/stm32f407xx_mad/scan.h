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

#pragma once

#include "common.h"

// Key scan speed for the MAD68 HE (v7.3, see analog.c).
//
// The sensors are read one column at a time: the column is powered, given a
// settle time, then its five rows are converted (about 9.5 us with one ADC).
// The stock firmware spends 40 us per column in total, about 1,670 full
// scans per second.
//
// Speeds 0-3 (v7.3) read with one ADC and differ only in the settle time.
// Speeds 4-6 (v7.4) read with all three ADCs at once and power the next
// column before storing the results. Measured on a MAD68 HE: about 4,740,
// 6,060 and 6,850 scans per second. From v7.5 they also read the bottom row
// twice (mode 7 below), which takes no longer.
#define SCAN_SPEED_RELAXED 0  // 40 us: the v7.2 timing, about 1,300 scans/s
#define SCAN_SPEED_STANDARD 1 // 28 us: about the stock firmware's rate
#define SCAN_SPEED_FAST 2     // 18 us
#define SCAN_SPEED_FASTEST 3  // 8 us
#define SCAN_SPEED_TURBO 4    // three ADCs, 8 us
#define SCAN_SPEED_EXTREME 5  // three ADCs, 5 us
#define SCAN_SPEED_MAX 6      // three ADCs, 5 us, 15-cycle samples
#define SCAN_SPEED_COUNT 7
#define SCAN_SPEED_DEFAULT SCAN_SPEED_STANDARD
#define SCAN_SETTLE_US {40, 28, 18, 8, 8, 5, 5}
#define SCAN_SPEED_MODES {0, 0, 0, 0, 7, 7, 7}
#define SCAN_SPEED_SAMPLES {2, 2, 2, 2, 2, 2, 1}

// A three-ADC speed is a request. The keyboard starts on one ADC, then
// measures the speed's timing with the keys left on their readings (a probe,
// below) and only switches if every key reads where it did. Refused, or
// dropped later after faults, the speed runs as this one-ADC speed instead.
#define SCAN_FALLBACK_SPEED SCAN_SPEED_FASTEST
#define SCAN_SPEED_IN_EFFECT 0u
#define SCAN_SPEED_SWITCHING 1u
#define SCAN_SPEED_REFUSED 2u

// Smoothing of the key readings (matrix.c).
//   0 Classic: libhmk's filter, one step of (x + 15y) / 16 per main loop
//     pass. The filtered value trails the key by about 15 passes (about
//     1.5 ms), whatever the scan speed.
//   1-3: one step per new reading of the key instead, so a faster scan
//     means less delay: the median of the last three readings (which drops
//     a single stray reading), then y += (x - y) / 8, / 4 or / 2. The result
//     follows the readings down at once and up from MATRIX_FILTER_RISE_BAND
//     counts below, which keeps a resting key still.
#define SCAN_FILTER_CLASSIC 0u
#define SCAN_FILTER_SMOOTH 1u
#define SCAN_FILTER_BALANCED 2u
#define SCAN_FILTER_FAST 3u
#define SCAN_FILTER_COUNT 4u
#define SCAN_FILTER_DEFAULT SCAN_FILTER_CLASSIC

// Saved in board.c's record journal (rgb.h RGB_REC_SCAN):
//   [0] speed  [1] smoothing (v7.4; v7.3 ignores it)  [2..3] reserved, 0
#define SCAN_REC_SIZE 4u

// Raw HID commands (board-specific range):
//
// GET_SCAN (0x68): no arguments. Reply:
//   [1] scan protocol version (1)  [2] current speed  [3] speed count
//   [4..5] full-matrix scans per second measured on the keyboard (LE16), 0
//       while the first measurement after start-up or a change is running
//   [6] 1 while every key's rest value is being captured at the new timing
//   [7 + i] settle time in microseconds of speed i (i < speed count)
//   From scan protocol version 2 (v7.4), with n = speed count:
//   [7 + n + i] speed i's mode (bits 0-3) and ADC sample code (bits 4-7)
//   [7 + 2n] whether the speed is in effect (SCAN_SPEED_*)
//   [8 + 2n] smoothing level  [9 + 2n] number of smoothing levels
//   [10 + 2n .. 11 + 2n] main loop passes per second (LE16)
// SET_SCAN (0x69): [1] speed. Applies at once (rest values are captured
//   again at the new timing), saves it, and replies like GET_SCAN. A
//   three-ADC speed replies while it is still switching ([6] is 1 until it
//   is in effect or refused).
// SET_SCAN_FILTER (0x6C, v7.4): [1] smoothing level. Applies at once, saves
//   it, and replies like GET_SCAN.
// Firmware before v7.3 answers all three as unknown commands.
#define BOARD_COMMAND_GET_SCAN 0x68
#define BOARD_COMMAND_SET_SCAN 0x69
#define BOARD_COMMAND_SET_SCAN_FILTER 0x6C
#define SCAN_PROTOCOL_VERSION 2

uint8_t analog_scan_speed(void);
uint8_t analog_settle_us(uint8_t speed);
// A speed's mode (bits 0-3) and ADC sample code (bits 4-7)
uint8_t analog_speed_timing(uint8_t speed);
// SCAN_SPEED_IN_EFFECT, _SWITCHING or _REFUSED for the saved speed
uint8_t analog_speed_state(void);
uint8_t analog_filter_level(void);
// False for an unknown level. Not saved.
bool analog_set_filter_level(uint8_t level);
uint32_t analog_loop_rate(void);
// For the per-reading filter: a counter per column that goes up by one with
// every reading taken of it, and the column each key is in
extern volatile uint8_t analog_col_seq[];
extern uint8_t analog_key_col[];
// How far behind the readings the filtered value is on real key presses:
// the time from a key's reading passing half travel to its filtered value
// doing so (matrix.c). `reset` clears the statistics after reading them.
void matrix_lag_measure(bool on);
void matrix_lag_stats(uint32_t *count, uint32_t *mean_us, uint32_t *min_us,
                      uint32_t *max_us, bool reset);
// Applies a speed at once; returns false for an unknown one. Not saved.
bool analog_set_scan_speed(uint8_t speed);
// Full-matrix scans per second, measured with the cycle counter over the last
// 64 scans; 0 until the first measurement after start-up or a speed change
uint32_t analog_scan_rate(void);
// True while the rest values are being captured or a speed is switching
bool analog_baseline_busy(void);

//--------------------------------------------------------------------+
// Timing test (from the v7.4 test builds). Nothing here is saved: after a
// restart the keyboard is back on its saved scan speed.
//--------------------------------------------------------------------+
//
// A timing is how one column is read:
//   mode    bit 0: ADC1, ADC2 and ADC3 each convert their rows at the same
//               time (config.h ADC_PARALLEL_*), two rounds instead of five;
//               clear: ADC1 converts the five rows one after another
//           bit 1: once a column's results are in, the next column is
//               powered and its settle time started before they are
//               stored; clear: they are stored first
//           bit 2 (v7.5, only with bit 0): ADC2 converts one row twice in a
//               row (config.h ADC_PAIRED_*). The two readings, about 1.3 us
//               apart, are averaged when they agree to within
//               SCAN_PAIR_AGREE counts; when they do not, one of them is a
//               stray reading and the one nearer the key's last reading is
//               kept
//           0 is how v7.3 reads the keys
//   settle  wait after powering the column, in TIM10 ticks (F_CPU per
//           second: 168 per microsecond)
//   sample  ADC sample time code: 0 = 3, 1 = 15, 2 = 28 (as stock), 3 = 56,
//           4 = 84, 5 = 112, 6 = 144, 7 = 480 ADC clock cycles
//
// A probe reads the keyboard at a timing for a number of full scans and
// keeps each key's lowest, highest and average raw reading. While it runs
// the keys go on using their last readings from before it, so nothing a
// timing does to the readings can press or release a key. Afterwards the
// keyboard is back on the timing it was using.
//
// With "go live" the probe is short (16 scans), and if no key's average moved
// more than SCAN_TUNE_MAX_SHIFT counts from its reading just before, the
// keyboard keeps the timing and captures every rest value again. A timing
// that then produces SCAN_LIVE_MAX_FAULTS faults, or stops the scan, is
// dropped for the saved scan speed.
#define SCAN_MODE_V73 0u
#define SCAN_MODE_PARALLEL 0x01u
#define SCAN_MODE_EARLY 0x02u
#define SCAN_MODE_PAIRED 0x04u
#define SCAN_MODE_COUNT 8u
// Counts two readings of a resting or moving key, taken about 1.3 us apart,
// can differ by (the noise is a few counts; a stray reading is 20 to 60 off)
#define SCAN_PAIR_AGREE 16
// A paired reading this far from the key's last one, with both readings
// agreeing, is logged too (a disturbance longer than the gap, or a key moving)
#define SCAN_PAIR_JUMP 24

#define SCAN_TICKS_MIN 8u // shortest settle a probe accepts (ARR >= 1)
#define SCAN_PROBE_LOG2_MIN 4u
#define SCAN_PROBE_LOG2_MAX 12u // 4,096 scans: the sums still fit 32 bits
#define SCAN_TUNE_LOG2 4u
#define SCAN_TUNE_MAX_SHIFT 150u
#define SCAN_PROBE_MAX_FAULTS 8u
#define SCAN_LIVE_MAX_FAULTS 32u

#define SCAN_PROBE_NONE 0u
#define SCAN_PROBE_RUNNING 1u
#define SCAN_PROBE_DONE 2u
#define SCAN_PROBE_FAILED 3u

#define SCAN_PROBE_ERR_NONE 0u
#define SCAN_PROBE_ERR_NO_ADC 1u   // ADC2 or ADC3 did not answer
#define SCAN_PROBE_ERR_FAULTS 2u   // overruns, DMA errors or missing results
#define SCAN_PROBE_ERR_STALL 3u    // no column finished for SCAN_STALL_MS
#define SCAN_PROBE_ERR_SHIFT 4u    // go live refused: readings moved too far
#define SCAN_PROBE_ERR_REPLACED 5u // a scan speed was set while it ran

#define SCAN_FLAG_TUNED 0x01u     // live timing came from a probe
#define SCAN_FLAG_ADC23 0x02u     // ADC2 and ADC3 answered
#define SCAN_FLAG_FELL_BACK 0x04u // a live timing was dropped after faults
#define SCAN_FLAG_CAPTURING 0x08u // rest values are being captured

// SCAN_PROBE (0x6A): [1] mode  [2..3] settle ticks (LE16)  [4] sample code
//   [5] log2 of the full scans to measure (4-12; ignored with go live)
//   [6] 1 = go live with this timing if the readings allow it
//   Refused (unknown command reply) for values out of range or while another
//   probe runs. Replies like SCAN_STATUS.
// SCAN_STATUS (0x6B): [1] 0 = status, 1 = results, 2 = status after
//     clearing the filter delay statistics and starting to measure,
//     3 = status after stopping that, 4 = stray reading log, 5 = pin activity,
//     6 = pin override, 7 = USB frame timestamps on or off (v7.5, below)
//   Status reply:
//     [1] test protocol version (3)
//     [2] live mode  [3..4] live settle ticks  [5] live sample code
//     [6] flags (SCAN_FLAG_*)
//     [7] probe state (SCAN_PROBE_*)  [8] probe error (SCAN_PROBE_ERR_*)
//     [9] probe mode  [10..11] probe settle ticks  [12] probe sample code
//     [13] log2 of its scans
//     [14..17] CPU cycles its measured scans took (LE32)
//     [18..19] largest move of a key's average from its reading just before
//         the probe (LE16)  [20] that key
//     [21..22] faults during the probe (LE16)
//     [23..24] faults since power-up (LE16): [25..26] of them results of
//         ADC2/ADC3 missing, [27..28] overruns; [29..30] stalled scans
//     [31..32] full scans per second, as GET_SCAN (LE16)
//     [33..35] main loop passes per second (LE24)
//     [36] TIM10 ticks per microsecond  [37] saved scan speed
//     From test protocol version 2:
//     [38] whether the saved speed is in effect (SCAN_SPEED_*)
//     [39] smoothing level
//     [40] 1 while filter delay is measured  [41..42] key presses measured
//     (LE16)  [43..44] average, [45..46] shortest, [47..48] longest delay of
//     the filtered value behind the reading, in microseconds (LE16)
//     From test protocol version 3:
//     [49..52] paired readings that disagreed since power-up (LE32)
//   Results request: [2] first key. Reply: [1] probe state  [2] first key
//     [3] number of keys that follow (up to 10), then for each key:
//     average x 16 (LE16), lowest (LE16), highest (LE16) raw reading
#define BOARD_COMMAND_SCAN_PROBE 0x6A
#define BOARD_COMMAND_SCAN_STATUS 0x6B
//   Stray reading log (request 4): [2..3] number of the first entry wanted
//     (LE16; entries are numbered from 0 in the order they happened). Reply:
//     [1..2] entries logged so far (LE16, wraps)  [3] number of the first
//     entry that follows (low byte)  [4] how many follow (up to 3), then for
//     each, 19 bytes: [0..3] cycle counter when the column's results arrived
//     (LE32, F_CPU per second)  [4..7] cycle counter at the last USB start of
//     frame (LE32; 0 while those are not being timestamped)  [8..10] SysTick
//     counter when the entry was written (LE24, counts down through each
//     millisecond)  [11] how long after the results arrived that was, in
//     pairs of cycles (255 = more)  [12] 0  [13] key  [14] kind: 1 = the two
//     readings disagreed, 2 = they agreed but jumped, 0 = an ordinary
//     reading, logged once in SCAN_STRAY_CONTROL for comparison  [15..16],
//     [17..18] first and second reading minus the key's last reading (LE
//     int16). The log keeps the last 64 entries; an entry older than that is
//     skipped to the oldest one kept.
//   USB frame timestamps (request 7): [2] 1 = on, 0 = off. While on, the USB
//     start-of-frame interrupt is enabled (8,000 a second at high speed) and
//     the cycle counter is noted at each. Replies like the status request.
//   Pin activity (request 5): [2] port (0 = A ... 4 = E). Watches the port's
//     input levels for SCAN_PIN_WATCH_MS and replies [1] port  [2..3] samples
//     taken / 256 (LE16)  [4 + 2p ..] level changes seen on pin p (LE16,
//     stops at 65535)  [36 + p] share of samples pin p was high (0-255)
//     [52..53] levels at the end. The keyboard does nothing else meanwhile.
//   Pin override (request 6): [2] port  [3] pin  [4] 0 = as the stock
//     firmware leaves it, 1 = analog, 2 = input, 3 = input with pull-up,
//     4 = input with pull-down. Only for the unused pins next to the sensor
//     lines (PA4, PA5, PA6, PC4, PC5, PB0), never as an output, and not
//     saved. Replies [1] port  [2] pin  [3] the pin's mode bits  [4] its pull
//     bits; refused for any other pin.
#define SCAN_TEST_PROTOCOL_VERSION 3
#define SCAN_RESULT_KEYS 10u
#define SCAN_STRAY_LOG 64u
#define SCAN_STRAY_REPLY 3u
#define SCAN_STRAY_ENTRY 19u
#define SCAN_STRAY_CONTROL 2048u
#define SCAN_PIN_WATCH_MS 20u

// Starts a probe; false if the arguments are out of range or one is running
bool analog_probe_start(uint8_t mode, uint16_t ticks, uint8_t sample,
                        uint8_t log2_scans, bool go_live);
// Write the status / results reply into out[1..] (out[0] is the command ID)
void analog_test_status(uint8_t *out);
bool analog_test_results(uint8_t first, uint8_t *out);
void analog_test_strays(uint16_t first, uint8_t *out);
// Cycle counter at the last USB start of frame; 0 while not timestamped
extern volatile uint32_t board_sof_cycles;
