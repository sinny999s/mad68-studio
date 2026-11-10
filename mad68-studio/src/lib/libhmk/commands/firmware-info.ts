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

// Which MAD68 custom firmware release is running (GET_FIRMWARE_INFO, from
// v7.2; see board.c). libhmk's own version stays at upstream's protocol
// number, so it can't tell custom releases apart.

import type { Commander } from "$lib/keyboard/commander"
import type { Release } from "$lib/release"
import { HMK_Command } from "."

export type FirmwareInfo = Release & { buildId: number }

/** null when the firmware is older than v7.2 (it doesn't know the command) */
export async function getFirmwareInfo(
  commander: Commander,
): Promise<FirmwareInfo | null> {
  try {
    // Older firmware answers "unknown"; fail fast instead of waiting 4 s
    const view = await commander.sendCommand({
      command: HMK_Command.GET_FIRMWARE_INFO,
      timeout: 600,
    })
    // `view` starts after the command byte
    return {
      major: view.getUint8(0),
      minor: view.getUint8(1),
      patch: view.getUint8(2),
      buildId: view.getUint32(3, true),
    }
  } catch {
    return null
  }
}
