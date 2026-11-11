"""Generate firmware/keyboards/mad68he/{keyboard.json,config.h}.

Inputs are things read from the real keyboard or the stock firmware:
  - which matrix positions hold a switch, and the default keymap: from the
    read-only backup of the stock settings (backup/stock-settings.json)
  - row/column wiring: recovered from the stock firmware in emulation
  - physical key widths: the standard MAD68 65% layout

Usage: python gen_keyboard.py <backup.json> <firmware dir>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROWS, COLS = 5, 15

# Physical layout [row][col] -> width (units), from the MAD68 65% layout
WIDTHS = {
    (0, 13): 2.0,
    (1, 0): 1.5, (1, 13): 1.5,
    (2, 0): 1.75, (2, 13): 2.25,
    (3, 0): 2.25, (3, 12): 1.75,
    (4, 0): 1.25, (4, 1): 1.25, (4, 2): 1.25, (4, 6): 6.25,
}

# Stock (QMK/VIA) keycodes found in the backup -> libhmk names
QMK_TO_HMK = {
    0x0000: 'KC_NO', 0x0001: 'KC_TRNS',
    0x0029: 'KC_ESC', 0x001E: 'KC_1', 0x001F: 'KC_2', 0x0020: 'KC_3', 0x0021: 'KC_4',
    0x0022: 'KC_5', 0x0023: 'KC_6', 0x0024: 'KC_7', 0x0025: 'KC_8', 0x0026: 'KC_9',
    0x0027: 'KC_0', 0x002D: 'KC_MINS', 0x002E: 'KC_EQL', 0x002A: 'KC_BSPC', 0x0049: 'KC_INS',
    0x002B: 'KC_TAB', 0x0014: 'KC_Q', 0x001A: 'KC_W', 0x0008: 'KC_E', 0x0015: 'KC_R',
    0x0017: 'KC_T', 0x001C: 'KC_Y', 0x0018: 'KC_U', 0x000C: 'KC_I', 0x0012: 'KC_O',
    0x0013: 'KC_P', 0x002F: 'KC_LBRC', 0x0030: 'KC_RBRC', 0x0031: 'KC_BSLS', 0x004C: 'KC_DEL',
    0x0039: 'KC_CAPS', 0x0004: 'KC_A', 0x0016: 'KC_S', 0x0007: 'KC_D', 0x0009: 'KC_F',
    0x000A: 'KC_G', 0x000B: 'KC_H', 0x000D: 'KC_J', 0x000E: 'KC_K', 0x000F: 'KC_L',
    0x0033: 'KC_SCLN', 0x0034: 'KC_QUOT', 0x0028: 'KC_ENT', 0x004B: 'KC_PGUP',
    0x00E1: 'KC_LSFT', 0x001D: 'KC_Z', 0x001B: 'KC_X', 0x0006: 'KC_C', 0x0019: 'KC_V',
    0x0005: 'KC_B', 0x0011: 'KC_N', 0x0010: 'KC_M', 0x0036: 'KC_COMM', 0x0037: 'KC_DOT',
    0x0038: 'KC_SLSH', 0x00E5: 'KC_RSFT', 0x0052: 'KC_UP', 0x004E: 'KC_PGDN',
    0x00E0: 'KC_LCTL', 0x00E3: 'KC_LGUI', 0x00E2: 'KC_LALT', 0x002C: 'KC_SPC',
    0x00E6: 'KC_RALT', 0x00E4: 'KC_RCTL', 0x00E7: 'KC_RGUI', 0x0050: 'KC_LEFT',
    0x0051: 'KC_DOWN', 0x004F: 'KC_RGHT', 0x0035: 'KC_GRV',
    0x003A: 'KC_F1', 0x003B: 'KC_F2', 0x003C: 'KC_F3', 0x003D: 'KC_F4', 0x003E: 'KC_F5',
    0x003F: 'KC_F6', 0x0040: 'KC_F7', 0x0041: 'KC_F8', 0x0042: 'KC_F9', 0x0043: 'KC_F10',
    0x0044: 'KC_F11', 0x0045: 'KC_F12',
    0x5221: 'MO(1)', 0x5223: 'MO(3)',
    # Stock RGB keys (QMK RGB keycodes in the vendor keymap)
    0x7820: 'SP_RGB_TOG', 0x7821: 'SP_RGB_MOD', 0x7822: 'SP_RGB_RMOD',
    0x7823: 'SP_RGB_HUI', 0x7824: 'SP_RGB_HUD', 0x7827: 'SP_RGB_VAI', 0x7828: 'SP_RGB_VAD',
}

# Extra Fn-layer RGB keys this firmware adds where stock had nothing.
EXTRA_FN = {(1, 4): 'SP_RGB_SPI', (2, 4): 'SP_RGB_SPD'}   # Fn+R / Fn+F: speed
# Controller (XInput) output on/off: Fn+G
GAMEPAD_TOGGLE = (2, 5)


def main(backup_path: str, fw_dir: str) -> None:
    backup = json.loads(Path(backup_path).read_text(encoding='utf-8-sig'))
    km = bytes.fromhex(backup['keymap']['hex'])
    code = lambda L, r, c: int.from_bytes(km[((L * ROWS + r) * COLS + c) * 2:][:2], 'big')

    # A position holds a switch if the stock base layer maps anything there
    present = [[code(0, r, c) != 0 for c in range(COLS)] for r in range(ROWS)]
    keys = [(r, c) for r in range(ROWS) for c in range(COLS) if present[r][c]]
    index = {rc: i for i, rc in enumerate(keys)}
    assert len(keys) == 68, len(keys)

    unknown = set()

    def name(L, r, c):
        v = code(L, r, c)
        if v not in QMK_TO_HMK:
            unknown.add(v)
            return 'KC_TRNS'  # vendor RGB / mode keys: not supported yet
        return QMK_TO_HMK[v]

    base = [name(0, r, c) for r, c in keys]
    fn = [name(1, r, c) for r, c in keys]
    # Way back to update mode from the keyboard itself: Fn + the key right of
    # Backspace (Ins on the base layer).
    fn[index[(0, 14)]] = 'SP_BOOT'
    for rc, kc in EXTRA_FN.items():
        if fn[index[rc]] == 'KC_TRNS':
            fn[index[rc]] = kc
    assert fn[index[GAMEPAD_TOGGLE]] == 'KC_TRNS'
    fn[index[GAMEPAD_TOGGLE]] = 'SP_GAMEPAD_TOG'
    gamepad_toggle_key = index[GAMEPAD_TOGGLE]
    # Fn-layer RGB keys to add (once) to keymaps created by an earlier build
    rgb_fn = [(index[(r, c)], fn[index[(r, c)]]) for r in range(ROWS) for c in range(COLS)
              if present[r][c] and fn[index[(r, c)]].startswith('SP_RGB_')]
    blank = ['KC_TRNS'] * len(keys)
    keymap = [base, fn, blank, blank]

    layout = []
    for r in range(ROWS):
        row = []
        for c in range(COLS):
            if present[r][c]:
                k = {'key': index[(r, c)]}
                if (r, c) in WIDTHS:
                    k['w'] = WIDTHS[(r, c)]
                row.append(k)
        layout.append(row)

    kb = {
        'name': 'MAD68 HE (libhmk)',
        'manufacturer': 'MADLIONS',
        'maintainer': 'devyn',
        'usb': {'vid': '0x373B', 'pid': '0x1F58', 'port': 'hs'},
        'keyboard': {'num_profiles': 4, 'num_layers': 4, 'num_keys': len(keys),
                     'num_advanced_keys': 32},
        'hardware': {'hse_value': 16000000, 'driver': 'stm32f407xx_mad'},
        'analog': {'adc_resolution': 12, 'invert_adc': False},
        # Resting values measured on this keyboard through the stock firmware
        # were 2225-2457 (median ~2310). The driver re-centres every key on its
        # own measured rest value, so this only seeds the matrix calibration.
        # The analog driver reports every key relative to ADC_REST_REFERENCE
        # (1024) no matter what its sensor rests at (2225-2457 measured on this
        # keyboard). libhmk only ever lowers a key's rest value during
        # calibration, so it has to start just above that reference.
        'calibration': {'initial_rest_value': 1100, 'initial_bottom_out_threshold': 500},
        'layout': {'keymap': layout},
        'keymap': keymap,
    }
    out = Path(fw_dir) / 'keyboards' / 'mad68he'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'keyboard.json').write_text(json.dumps(kb, indent=2) + '\n')

    matrix_rows = []
    for r in range(ROWS):
        matrix_rows.append('{' + ', '.join(str(index[(r, c)] + 1) if present[r][c] else '0'
                                           for c in range(COLS)) + '}')
    config = f'''#pragma once

// MAD68 HE (DuckBread) analog front end. Generated by tools/gen_keyboard.py.
// Wiring recovered from the stock v1.06 firmware: five ADC1 rows, fifteen
// active-low column power-select lines.

#define ADC_COLUMN_SCAN
#define ADC_COLUMN_SCAN_ROWS {ROWS}
#define ADC_COLUMN_SCAN_COLS {COLS}

// Rows in conversion order: PA3, PA2, PA1, PA0, PA7
#define ADC_COLUMN_SCAN_ROW_CHANNELS                                           \\
  {{ADC_CHANNEL_3, ADC_CHANNEL_2, ADC_CHANNEL_1, ADC_CHANNEL_0, ADC_CHANNEL_7}}

// Columns 0-14: PA8 PA9 PA10 PA11 PA12 PA15 PC10 PC11 PC12 PD2 PB3 PB4 PB5 PB6 PB7
#define ADC_COLUMN_SCAN_COL_PORTS                                              \\
  {{GPIOA, GPIOA, GPIOA, GPIOA, GPIOA, GPIOA, GPIOC, GPIOC, GPIOC, GPIOD,        \\
   GPIOB, GPIOB, GPIOB, GPIOB, GPIOB}}
#define ADC_COLUMN_SCAN_COL_PINS                                               \\
  {{GPIO_PIN_8, GPIO_PIN_9, GPIO_PIN_10, GPIO_PIN_11, GPIO_PIN_12, GPIO_PIN_15, \\
   GPIO_PIN_10, GPIO_PIN_11, GPIO_PIN_12, GPIO_PIN_2, GPIO_PIN_3, GPIO_PIN_4,   \\
   GPIO_PIN_5, GPIO_PIN_6, GPIO_PIN_7}}

// Key index + 1 at each (row, col); 0 = no switch at that position
#define ADC_COLUMN_SCAN_MATRIX                                                 \\
  {{{', '.join(matrix_rows)}}}

// The sensor settle time after switching a column on is the scan speed
// setting (hardware/stm32f407xx_mad/scan.h), not a build option.

// Same per-conversion sample time as the stock firmware
#define ADC_NUM_SAMPLE_CYCLES ADC_SAMPLETIME_28CYCLES

// Parallel read (v7.4 timing test): the rows each ADC converts, in order, as
// indexes into the row list above. PA0-PA3 reach ADC1, ADC2 and ADC3; PA7
// reaches ADC1 and ADC2 only. ADC1 converts at least as many as the others.
#define ADC_PARALLEL_ADC1_ROWS {{0, 3}}
#define ADC_PARALLEL_ADC2_ROWS {{1, 4}}
#define ADC_PARALLEL_ADC3_ROWS {{2}}

// Paired read (v7.5): one row is converted twice in a row by ADC2, so that a
// stray reading on it shows as the two disagreeing; ADC1 and ADC3 share the
// other rows. PA7 (row 4) is the row with stray readings on this board.
#define ADC_PAIRED_ADC1_ROWS {{0, 3}}
#define ADC_PAIRED_ADC3_ROWS {{1, 2}}
#define ADC_PAIRED_ROW 4

// Value reported for a key at rest; travel is added on top in the direction
// learned for that key, leaving 3071 counts of headroom.
#define ADC_REST_REFERENCE 1024

// USB: reproduce the stock firmware's OTG_HS setup (on-chip UTMI+ PHY, 8-bit,
// full-speed transceiver left powered). See scripts/patch_tinyusb.py.
// TinyUSB does not mark the STM32F4 HS port as high-speed capable by default
// (genuine F4 parts need an external ULPI PHY), so request it explicitly.
#define CFG_TUD_MAX_SPEED OPT_MODE_HIGH_SPEED
#define BOARD_DWC2_PHY_SELECT
#define BOARD_DWC2_FORCE_UTMI
#define BOARD_DWC2_KEEP_FS_XCVR
// The MCU is an STM32F4-compatible part with its own HS PHY; nothing about its
// USB core is assumed beyond what the stock firmware relies on.
#define BOARD_DWC2_SKIP_ID_CHECK
#define BOARD_DWC2_CORE_ID 0x4F54281AUL // reset like a 2.81a core, as stock does
#define BOARD_DWC2_EP_COUNT 6           // stock initialises exactly 6 endpoints
#define BOARD_DWC2_HS_FIFO_SIZE 1280    // stay well inside proven FIFO RAM
'''
    led_map = json.loads((Path(backup_path).parent / 'stock-led-map.json').read_text())
    gamma = json.loads((Path(backup_path).parent / 'stock-gamma.json').read_text())
    key_to_led = [led_map[r * COLS + c] for r, c in keys]
    assert sorted(key_to_led) == list(range(68)), 'stock LED map must cover all 68 keys once'
    led_row = [0] * 68
    led_col = [0] * 68
    for (r, c), led in zip(keys, key_to_led):
        led_row[led], led_col[led] = r, c
    # Physical centre of each LED's key on a 224 x 64 grid (QMK convention:
    # 14 per key unit across, 16 per row down), for ripples, waves, gradients
    centre = {}
    for r in range(ROWS):
        x = 0.0
        for c in range(COLS):
            if present[r][c]:
                w = WIDTHS.get((r, c), 1.0)
                centre[(r, c)] = x + w / 2
                x += w
    led_x = [0] * 68
    led_y = [0] * 68
    for (r, c), led in zip(keys, key_to_led):
        led_x[led] = min(224, round(centre[(r, c)] * 14))
        led_y[led] = r * 16
    arr = lambda xs: '{' + ', '.join(str(x) for x in xs) + '}'
    config += f'''
// RGB: 68 WS2812-style LEDs (GRB, MSB first) chained in a serpentine, driven
// exactly like stock: TIM3_CH4 on PC9, DMA1 Stream 2 channel 5 circular.
#define BOARD_RGB
#define RGB_NUM_LEDS 68
// libhmk key index -> LED chain index (from the stock firmware's table)
#define RGB_KEY_TO_LED {arr(key_to_led)}
// Physical matrix row/column of each LED, for spatial effects
#define RGB_LED_ROW {arr(led_row)}
#define RGB_LED_COL {arr(led_col)}
// Key centre of each LED on a 224 x 64 grid (x: 14 per key unit, y: 16 per row)
#define RGB_LED_X {arr(led_x)}
#define RGB_LED_Y {arr(led_y)}
// Stock gamma curve (v1.06 @ 0x08031E18) and brightness cap (stock rejects
// brightness above 210), so the LEDs never draw more than stock lets them.
#define RGB_GAMMA_LUT {arr(gamma)}
#define RGB_VAL_MAX 210
// Start like this keyboard's stock setting: solid #800000 at brightness 194
#define RGB_DEFAULT_HUE 0
#define RGB_DEFAULT_SAT 255
#define RGB_DEFAULT_VAL 97
#define RGB_DEFAULT_SPEED 128
// Fn-layer RGB keys added once to keymaps created before RGB support
#define RGB_FN_DEFAULTS {{{', '.join('{%d, %s}' % kv for kv in rgb_fn)}}}
#define RGB_FN_DEFAULTS_COUNT {len(rgb_fn)}
// Controller output toggle (Fn+G), added once to keymaps created before it
#define GAMEPAD_TOGGLE_FN_KEY {gamepad_toggle_key}
'''
    (out / 'config.h').write_text(config)
    (out / 'README.md').write_text(
        '# MAD68 HE\n\nlibhmk port for the MadLions MAD68 HE RGB (USB 373B:1058 stock).\n'
        'Generated from the stock firmware and this keyboard\'s own settings; see '
        '`mad68-cfw/README.md`.\n\nRGB lighting is not driven by this firmware.\n')
    print(f'wrote {out}; vendor keycodes mapped to KC_TRNS: {sorted(hex(u) for u in unknown)}')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
