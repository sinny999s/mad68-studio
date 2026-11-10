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
// settle time, then its five rows are converted (about 9.5 us). The settle
// time is the only part that changes between speeds. The stock firmware
// spends 40 us per column in total, about 1,670 full scans per second.
#define SCAN_SPEED_RELAXED 0  // 40 us: the v7.2 timing, about 1,300 scans/s
#define SCAN_SPEED_STANDARD 1 // 28 us: about the stock firmware's rate
#define SCAN_SPEED_FAST 2     // 18 us
#define SCAN_SPEED_FASTEST 3  // 8 us
#define SCAN_SPEED_COUNT 4
#define SCAN_SPEED_DEFAULT SCAN_SPEED_STANDARD
#define SCAN_SETTLE_US {40, 28, 18, 8}

// Saved in board.c's record journal (rgb.h RGB_REC_SCAN):
//   [0] speed  [1..3] reserved, 0
#define SCAN_REC_SIZE 4u

// Raw HID commands (board-specific range):
//
// GET_SCAN (0x68): no arguments. Reply:
//   [1] scan protocol version (1)  [2] current speed  [3] speed count
//   [4..5] full-matrix scans per second measured on the keyboard (LE16), 0
//       while the first measurement after start-up or a change is running
//   [6] 1 while every key's rest value is being captured at the new timing
//   [7 + i] settle time in microseconds of speed i (i < speed count)
// SET_SCAN (0x69): [1] speed. Applies at once (rest values are captured
//   again at the new timing), saves it, and replies like GET_SCAN.
// Firmware before v7.3 answers both as unknown commands.
#define BOARD_COMMAND_GET_SCAN 0x68
#define BOARD_COMMAND_SET_SCAN 0x69
#define SCAN_PROTOCOL_VERSION 1

uint8_t analog_scan_speed(void);
uint8_t analog_settle_us(uint8_t speed);
// Applies a speed at once; returns false for an unknown one. Not saved.
bool analog_set_scan_speed(uint8_t speed);
// Full-matrix scans per second, measured with the cycle counter over the last
// 64 scans; 0 until the first measurement after start-up or a speed change
uint32_t analog_scan_rate(void);
// True while the rest values are being captured
bool analog_baseline_busy(void);
