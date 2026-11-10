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

// Per-key RGB for the MAD68 HE (see rgb.c)

#if defined(BOARD_RGB)
void rgb_init(void);
void rgb_task(void);
void rgb_keycode(uint8_t keycode);
void rgb_host_leds(uint8_t leds);
bool rgb_command(const uint8_t *in, uint8_t *out);
#endif

// Lighting protocol version reported by GET_RGB
#define RGB_PROTOCOL_VERSION 2

// Raw HID commands (board-specific range). Layouts after the command byte:
//
// GET_RGB / SET_RGB:
//   [1] flags (bit0 on)  [2] effect  [3] hue  [4] sat  [5] brightness
//   [6] speed
//   SET only: [7] = 2 when the extended fields [8..14] below follow
//     [8] hue 2  [9] sat 2  [10] direction  [11] idle minutes (0 = off)
//     [12] indicator flags  [13] indicator hue  [14] indicator sat
//   GET (and the SET reply): [7] effect count  [8] max brightness
//     [9] LED count  [10] protocol version  [11] hue 2  [12] sat 2
//     [13] direction  [14] idle minutes  [15] indicator flags
//     [16] indicator hue  [17] indicator sat  [18] direction count
//     [19] key count
//
// GET_RGB_KEYS: [1] first key -> [1] first key [2] count (<= 20)
//   [3 + 3i ..] R, G, B of each key (libhmk key order)
// SET_RGB_KEYS: [1] first key [2] count (1..20) [3 + 3i ..] R, G, B;
//   replies like GET_RGB_KEYS
#define BOARD_COMMAND_GET_RGB 0x60
#define BOARD_COMMAND_SET_RGB 0x61
#define BOARD_COMMAND_GET_RGB_KEYS 0x62
#define BOARD_COMMAND_SET_RGB_KEYS 0x63
#define RGB_KEYS_PER_REPORT 20
#define RGB_SET_EXTENDED 2

// Indicator flags
#define RGB_IND_CAPS 0x01u    // Caps Lock key while Caps Lock is on
#define RGB_IND_LAYER 0x02u   // keys bound on the active layer while held
#define RGB_IND_PROFILE 0x04u // new profile's number key after a switch, and
                              // the controller keys after Fn+G (green on, blinking red off)
#define RGB_IND_MASK 0x07u

// Persistent records kept by board.c in flash sector 6 (lighting, and since
// v7.3 the key scan speed). Older firmware skips types it doesn't know.
#define RGB_REC_CONFIG 1u // rgb_config_t
#define RGB_REC_KEYS 2u   // R, G, B per key
#define RGB_REC_SCAN 3u   // key scan speed (scan.h, v7.3)
#define RGB_REC_TYPES 4u  // 0 = legacy 8-byte record from firmware v4
#define RGB_REC_MAX_PAYLOAD 240u
#define RGB_LEGACY_RECORD_SIZE 8u

// Copies up to max_len bytes of the latest record of `type` (0 = legacy) and
// returns its stored length, or 0 if there is none.
uint32_t board_rgb_load(uint32_t type, uint8_t *payload, uint32_t max_len);
// Appends a record of `type` (1 to RGB_REC_TYPES - 1) unless it equals the
// latest one.
void board_rgb_store(uint32_t type, const uint8_t *payload, uint32_t len);
