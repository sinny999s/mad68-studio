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

// Key scan speed of the MAD68 custom firmware (GET_SCAN / SET_SCAN, from
// v7.3; see the firmware's stm32f407xx_mad/scan.h).

import type { Commander } from "$lib/keyboard/commander"
import { HMK_Command } from "."

export type ScanSpeedInfo = {
  version: number
  /** Index into settleUs: 0 Relaxed, 1 Standard, 2 Fast, 3 Fastest */
  speed: number
  /** Full-keyboard scans per second measured on the keyboard; 0 = measuring */
  scanRate: number
  /** The keyboard is re-reading every key's rest position */
  capturing: boolean
  /** Settle time in microseconds of each speed */
  settleUs: number[]
}

/** Sensor columns read one after another in a full scan */
const COLUMNS = 15
/** Converting a column's five rows: 5 x 40 ADC cycles at 21 MHz */
const CONVERT_US = 9.52
/** Interrupt work per column (measured on a MAD68 HE at all four speeds) */
const OVERHEAD_US = 2.3

/** Expected full scans per second for a settle time */
export function estimateScanRate(settleUs: number) {
  return 1e6 / (COLUMNS * (settleUs + CONVERT_US + OVERHEAD_US))
}

export function parseScanSpeed(view: DataView): ScanSpeedInfo {
  // `view` starts after the command byte
  const count = view.getUint8(2)
  return {
    version: view.getUint8(0),
    speed: view.getUint8(1),
    scanRate: view.getUint16(3, true),
    capturing: view.getUint8(5) !== 0,
    settleUs: Array.from({ length: count }, (_, i) => view.getUint8(6 + i)),
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
