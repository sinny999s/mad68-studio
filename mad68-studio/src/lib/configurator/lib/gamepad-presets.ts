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

import { HMK_GamepadButton as B } from "$lib/libhmk/gamepad"
import { Keycode as K } from "$lib/libhmk/keycodes"

export type GamepadLayoutPreset = {
  name: string
  description: string
  /** Base-layer keycode -> controller input */
  bindings: [K, B][]
}

export const gamepadLayoutPresets: GamepadLayoutPreset[] = [
  {
    name: "WASD stick",
    description: "W A S D move the left stick, as far as you press them.",
    bindings: [
      [K.KC_W, B.LS_UP],
      [K.KC_A, B.LS_LEFT],
      [K.KC_S, B.LS_DOWN],
      [K.KC_D, B.LS_RIGHT],
    ],
  },
  {
    name: "Racing",
    description:
      "W is the throttle (right trigger), S the brake (left trigger), A and D steer.",
    bindings: [
      [K.KC_W, B.RT],
      [K.KC_S, B.LT],
      [K.KC_A, B.LS_LEFT],
      [K.KC_D, B.LS_RIGHT],
    ],
  },
  {
    name: "Arrow camera",
    description: "The arrow keys move the right stick.",
    bindings: [
      [K.KC_UP, B.RS_UP],
      [K.KC_DOWN, B.RS_DOWN],
      [K.KC_LEFT, B.RS_LEFT],
      [K.KC_RIGHT, B.RS_RIGHT],
    ],
  },
]

/**
 * Bindings after applying a preset on top of the current ones. Keys are found
 * by what they do on layer 0, so remapped keyboards still get the right keys.
 */
export function applyLayoutPreset(
  current: number[],
  baseLayer: number[],
  preset: GamepadLayoutPreset,
) {
  const buttons = [...current]
  const missing: K[] = []
  for (const [keycode, button] of preset.bindings) {
    const key = baseLayer.indexOf(keycode)
    if (key < 0) missing.push(keycode)
    else buttons[key] = button
  }
  return { buttons, missing }
}
