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

// Typing pause for calibration (PAUSE_INPUT, custom firmware v7.6; see
// board.c). While it is on, the keyboard reads its keys as usual but nothing
// reaches the PC. It ends by itself unless it is asked for again in time, so
// the app repeats it while it wants it.

import type { Commander } from "$lib/keyboard/commander"
import { HMK_Command } from "."

export type InputPauseState = {
  version: number
  paused: boolean
  /** Milliseconds until the pause ends by itself */
  msLeft: number
  /** Keys held when a pause ended, silent until they are let go */
  keysHeldOver: number
}

/** Longest pause one request may ask for */
export const INPUT_PAUSE_MAX_MS = 10000

export type InputPauseAction =
  { action: "status" } | { action: "pause"; ms: number } | { action: "resume" }

function parse(view: DataView): InputPauseState {
  // `view` starts after the command byte
  return {
    version: view.getUint8(0),
    paused: view.getUint8(1) === 1,
    msLeft: view.getUint16(2, true),
    keysHeldOver: view.getUint8(4),
  }
}

/** null when the firmware is older than v7.6 (it doesn't know the command) */
export async function getInputPause(
  commander: Commander,
): Promise<InputPauseState | null> {
  try {
    // Older firmware answers "unknown"; fail fast instead of waiting 4 s
    return parse(
      await commander.sendCommand({
        command: HMK_Command.PAUSE_INPUT,
        payload: [0],
        timeout: 600,
      }),
    )
  } catch {
    return null
  }
}

export async function setInputPause(
  commander: Commander,
  request: InputPauseAction,
): Promise<InputPauseState> {
  let payload: number[]
  if (request.action === "pause") {
    const ms = Math.round(request.ms)
    if (!(ms >= 1 && ms <= INPUT_PAUSE_MAX_MS))
      throw new Error(`A pause lasts 1 to ${INPUT_PAUSE_MAX_MS} ms, not ${ms}`)
    payload = [1, ms & 0xff, ms >> 8]
  } else {
    payload = [request.action === "resume" ? 2 : 0]
  }
  return parse(
    await commander.sendCommand({
      command: HMK_Command.PAUSE_INPUT,
      payload,
      timeout: 1500,
    }),
  )
}
