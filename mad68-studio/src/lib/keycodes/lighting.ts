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

import { Keycode } from "$lib/libhmk/keycodes"
import type { KeycodeMetadata } from "."

const lighting = (
  keycode: Keycode,
  name: string,
  tooltip: string,
): KeycodeMetadata => ({
  name,
  tooltip,
  keycode,
  webCodes: [],
  category: "Lighting",
})

export const lightingKeycodeMetadata: KeycodeMetadata[] = [
  lighting(Keycode.SP_RGB_TOG, "RGB Toggle", "Turn the lighting on or off"),
  lighting(Keycode.SP_RGB_MOD, "RGB Mode+", "Next lighting effect"),
  lighting(Keycode.SP_RGB_RMOD, "RGB Mode-", "Previous lighting effect"),
  lighting(Keycode.SP_RGB_HUI, "Hue+", "Shift the lighting colour forward"),
  lighting(Keycode.SP_RGB_HUD, "Hue-", "Shift the lighting colour back"),
  lighting(Keycode.SP_RGB_VAI, "Bright+", "Increase lighting brightness"),
  lighting(Keycode.SP_RGB_VAD, "Bright-", "Decrease lighting brightness"),
  lighting(Keycode.SP_RGB_SPI, "Speed+", "Speed up lighting animation"),
  lighting(Keycode.SP_RGB_SPD, "Speed-", "Slow down lighting animation"),
]

export const lightingKeycodes = lightingKeycodeMetadata.map((m) => m.keycode)
