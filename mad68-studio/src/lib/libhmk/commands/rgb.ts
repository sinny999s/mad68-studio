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

// MAD68 HE lighting commands (board-specific raw HID range, see the firmware's
// hardware/stm32f407xx_mad/rgb.h). Protocol 1 = firmware v4 (5 effects),
// protocol 2 = firmware v5 (15 effects, second colour, direction, indicators,
// idle dimming, per-key colours).

import type { Commander } from "$lib/keyboard/commander"
import { HMK_Command } from "."

export const RGB_KEYS_PER_REPORT = 20
const SET_EXTENDED = 2

export const RGB_IND_CAPS = 0x01
export const RGB_IND_LAYER = 0x02
export const RGB_IND_PROFILE = 0x04

export type RgbConfig = {
  on: boolean
  mode: number
  hue: number
  sat: number
  brightness: number
  speed: number
  // Protocol 2
  hue2: number
  sat2: number
  direction: number
  idleMinutes: number
  indicators: number
  indicatorHue: number
  indicatorSat: number
}

export type RgbInfo = RgbConfig & {
  modeCount: number
  maxBrightness: number
  ledCount: number
  version: number
  directionCount: number
  keyCount: number
}

export type Rgb = [number, number, number]

function parse(view: DataView): RgbInfo {
  // `view` starts after the command byte
  const u8 = (i: number) => (i < view.byteLength ? view.getUint8(i) : 0)
  const version = u8(9) || 1
  const v2 = version >= 2
  const hue = u8(2)
  return {
    on: (u8(0) & 1) === 1,
    mode: u8(1),
    hue,
    sat: u8(3),
    brightness: u8(4),
    speed: u8(5),
    modeCount: u8(6),
    maxBrightness: u8(7),
    ledCount: u8(8),
    version,
    hue2: v2 ? u8(10) : (hue + 128) & 0xff,
    sat2: v2 ? u8(11) : 255,
    direction: v2 ? u8(12) : 0,
    idleMinutes: v2 ? u8(13) : 0,
    indicators: v2 ? u8(14) : 0,
    indicatorHue: v2 ? u8(15) : 0,
    indicatorSat: v2 ? u8(16) : 0,
    directionCount: v2 ? u8(17) : 1,
    keyCount: v2 ? u8(18) : 0,
  }
}

export async function getRgb(commander: Commander) {
  // Firmware without RGB answers "unknown"; fail fast instead of waiting 4 s
  return parse(
    await commander.sendCommand({ command: HMK_Command.GET_RGB, timeout: 600 }),
  )
}

export async function setRgb(
  commander: Commander,
  data: RgbConfig,
  version: number,
) {
  const payload = [
    data.on ? 1 : 0,
    data.mode,
    data.hue,
    data.sat,
    data.brightness,
    data.speed,
  ]
  if (version >= 2) {
    payload.push(
      SET_EXTENDED,
      data.hue2,
      data.sat2,
      data.direction,
      data.idleMinutes,
      data.indicators,
      data.indicatorHue,
      data.indicatorSat,
    )
  }
  return parse(
    await commander.sendCommand({ command: HMK_Command.SET_RGB, payload }),
  )
}

export async function getRgbKeys(commander: Commander, keyCount: number) {
  const colors: Rgb[] = []
  for (let first = 0; first < keyCount; first += RGB_KEYS_PER_REPORT) {
    const view = await commander.sendCommand({
      command: HMK_Command.GET_RGB_KEYS,
      payload: [first],
    })
    const count = view.getUint8(1)
    for (let i = 0; i < count; i++) {
      const o = 2 + i * 3
      colors.push([
        view.getUint8(o),
        view.getUint8(o + 1),
        view.getUint8(o + 2),
      ])
    }
  }
  return colors
}

export async function setRgbKeys(
  commander: Commander,
  offset: number,
  colors: Rgb[],
) {
  for (let i = 0; i < colors.length; i += RGB_KEYS_PER_REPORT) {
    const chunk = colors.slice(i, i + RGB_KEYS_PER_REPORT)
    await commander.sendCommand({
      command: HMK_Command.SET_RGB_KEYS,
      payload: [offset + i, chunk.length, ...chunk.flat()],
    })
  }
}
