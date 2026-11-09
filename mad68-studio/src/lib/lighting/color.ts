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

// Colour maths shared with the MAD68 HE firmware (rgb.c): hue, saturation and
// value are 0-255, and a hue of 256 would be a full turn.

export type RGB = [number, number, number]

/** Integer HSV to RGB, identical to the firmware's hsv(). */
export function hsvToRgb(h: number, s: number, v: number): RGB {
  h = Math.round(h) & 0xff
  s = Math.max(0, Math.min(255, Math.round(s)))
  v = Math.max(0, Math.min(255, Math.round(v)))
  if (s === 0) return [v, v, v]
  const region = Math.floor(h / 43)
  const rem = (h - region * 43) * 6
  const p = (v * (255 - s)) >> 8
  const q = (v * (255 - ((s * rem) >> 8))) >> 8
  const t = (v * (255 - ((s * (255 - rem)) >> 8))) >> 8
  switch (region) {
    case 0:
      return [v, t, p]
    case 1:
      return [q, v, p]
    case 2:
      return [p, v, t]
    case 3:
      return [p, q, v]
    case 4:
      return [t, p, v]
    default:
      return [v, p, q]
  }
}

/** RGB to firmware HSV (hue 0-255). */
export function rgbToHsv([r, g, b]: RGB): [number, number, number] {
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const d = max - min
  const s = max === 0 ? 0 : Math.round((d * 255) / max)
  let deg = 0
  if (d > 0) {
    if (max === r) deg = 60 * (((g - b) / d) % 6)
    else if (max === g) deg = 60 * ((b - r) / d + 2)
    else deg = 60 * ((r - g) / d + 4)
  }
  if (deg < 0) deg += 360
  return [Math.round((deg / 360) * 256) & 0xff, s, max]
}

export const toHex = ([r, g, b]: RGB) =>
  "#" + [r, g, b].map((c) => c.toString(16).padStart(2, "0")).join("")

export function parseHex(text: string): RGB | null {
  const m = text.trim().match(/^#?([0-9a-f]{3}|[0-9a-f]{6})$/i)
  if (!m) return null
  const h =
    m[1].length === 3
      ? m[1]
          .split("")
          .map((c) => c + c)
          .join("")
      : m[1]
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as RGB
}

export const css = ([r, g, b]: RGB, alpha = 1) =>
  alpha >= 1 ? `rgb(${r} ${g} ${b})` : `rgb(${r} ${g} ${b} / ${alpha})`

export const hueCss = (h: number, s = 255) => css(hsvToRgb(h, s, 255))

/** Mix two colours, t in 0-255 (like the firmware's mix()). */
export const mix = (a: RGB, b: RGB, t: number): RGB =>
  a.map((c, i) => Math.floor((c * (255 - t) + b[i] * t) / 255)) as RGB

export const scale = (c: RGB, v: number): RGB =>
  c.map((x) => Math.floor((x * v) / 255)) as RGB

export const brightest = (a: RGB, b: RGB): RGB =>
  a.map((c, i) => Math.max(c, b[i])) as RGB

/** Hue blend the short way round the circle, t in 0-255. */
export function hueLerp(a: number, b: number, t: number) {
  let d = (b - a) & 0xff
  if (d > 127) d -= 256
  return (a + Math.trunc((d * t) / 255)) & 0xff
}

export const RAINBOW_GRADIENT = `linear-gradient(to right, ${[
  0, 43, 85, 128, 170, 213, 255,
]
  .map((h) => hueCss(h))
  .join(", ")})`
