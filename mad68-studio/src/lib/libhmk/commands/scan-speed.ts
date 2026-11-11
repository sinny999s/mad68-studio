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

// Key scan speed and smoothing of the MAD68 custom firmware (GET_SCAN /
// SET_SCAN from v7.3, three-sensor-reader speeds and SET_SCAN_FILTER from
// v7.4; see the firmware's stm32f407xx_mad/scan.h).

import type { Commander } from "$lib/keyboard/commander"
import { HMK_Command } from "."

/** Whether the saved scan speed's own timing is the one in use */
export const SCAN_SPEED_IN_EFFECT = 0
export const SCAN_SPEED_SWITCHING = 1
/** The keyboard could not use it and reads with one ADC at 8 µs instead */
export const SCAN_SPEED_REFUSED = 2

export type ScanSpeedInfo = {
  version: number
  /** Index into settleUs: 0 Relaxed, 1 Standard, 2 Quick, 3 Rapid, 4 Turbo,
   * 5 Extreme, 6 Max (2 and 3 were Fast and Fastest up to Studio 0.11.0) */
  speed: number
  /** Full-keyboard scans per second measured on the keyboard; 0 = measuring */
  scanRate: number
  /** The keyboard is re-reading every key's rest position, or switching */
  capturing: boolean
  /** Settle time in microseconds of each speed the firmware has */
  settleUs: number[]
  /** Each speed's read mode (bit 0: three ADCs at once; bit 2, from v7.5:
   * the bottom row read twice); [] before v7.4 */
  modes: number[]
  /** Each speed's ADC sample time code (2 = 28 cycles); [] before v7.4 */
  samples: number[]
  /** SCAN_SPEED_*; always in effect before v7.4 */
  state: number
  /** Smoothing level, 0 = classic; null before v7.4 */
  smoothing: number | null
  /** Main loop passes per second measured on the keyboard; 0 before v7.4 */
  loopRate: number
}

/** Sensor columns read one after another in a full scan */
const COLUMNS = 15
/** Sensor positions read in a full scan (15 columns x 5 rows) */
export const SENSORS_PER_SCAN = 75
/** ADC clock in MHz */
const ADC_MHZ = 21
/** Sample time in ADC clock cycles for each sample code */
const SAMPLE_CYCLES = [3, 15, 28, 56, 84, 112, 144, 480]
/** Conversion rounds per column: five rows on one ADC, or on three at once */
const ROUNDS = [5, 2]
/** Interrupt work per column in microseconds (measured on a MAD68 HE) */
const OVERHEAD_US = [2.3, 2.1]

/** Expected full scans per second for a speed's timing */
export function estimateScanRate(settleUs: number, mode = 0, sample = 2) {
  const parallel = mode & 1
  const convertUs =
    (ROUNDS[parallel] * ((SAMPLE_CYCLES[sample] ?? 28) + 12)) / ADC_MHZ
  return 1e6 / (COLUMNS * (settleUs + convertUs + OVERHEAD_US[parallel]))
}

/**
 * How far behind the key the filtered reading is, in milliseconds. Classic
 * (level 0) trails by about 15 main loop passes; levels 1-3 by one reading
 * for the median and then 7, 3 or 1.
 */
export function estimateSmoothingDelay(
  level: number,
  scanRate: number,
  loopRate: number,
) {
  if (level === 0) return loopRate > 0 ? 15000 / loopRate : 1.5
  return scanRate > 0 ? ((1 << (4 - level)) * 1000) / scanRate : 0
}

export function parseScanSpeed(view: DataView): ScanSpeedInfo {
  // `view` starts after the command byte
  const version = view.getUint8(0)
  const count = view.getUint8(2)
  const bytes = (from: number) =>
    Array.from({ length: count }, (_, i) => view.getUint8(from + i))
  const v2 = version >= 2
  const timing = v2 ? bytes(6 + count) : []
  return {
    version,
    speed: view.getUint8(1),
    scanRate: view.getUint16(3, true),
    capturing: view.getUint8(5) !== 0,
    settleUs: bytes(6),
    modes: timing.map((t) => t & 0x0f),
    samples: timing.map((t) => t >> 4),
    state: v2 ? view.getUint8(6 + 2 * count) : SCAN_SPEED_IN_EFFECT,
    smoothing: v2 ? view.getUint8(7 + 2 * count) : null,
    loopRate: v2 ? view.getUint16(9 + 2 * count, true) : 0,
  }
}

/** null when the firmware is older than v7.3 (it doesn't know the command) */
export async function getScanSpeed(
  commander: Commander,
): Promise<ScanSpeedInfo | null> {
  try {
    // Older firmware answers "unknown"; fail fast instead of waiting 4 s
    return parseScanSpeed(
      await commander.sendCommand({
        command: HMK_Command.GET_SCAN,
        timeout: 600,
      }),
    )
  } catch {
    return null
  }
}

/** Applies at once and is saved on the keyboard */
export async function setScanSpeed(commander: Commander, speed: number) {
  return parseScanSpeed(
    await commander.sendCommand({
      command: HMK_Command.SET_SCAN,
      payload: [speed],
    }),
  )
}

/** Applies at once and is saved on the keyboard (firmware v7.4) */
export async function setScanSmoothing(commander: Commander, level: number) {
  return parseScanSpeed(
    await commander.sendCommand({
      command: HMK_Command.SET_SCAN_FILTER,
      payload: [level],
    }),
  )
}
