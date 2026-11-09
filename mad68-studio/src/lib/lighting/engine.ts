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

// On-screen preview of the keyboard's lighting: the same effect maths as the
// firmware (rgb.c), run in the browser on the same 224 x 64 key grid.

import type { RgbConfig } from "$lib/libhmk/commands/rgb"
import { brightest, hsvToRgb, hueLerp, mix, scale, type RGB } from "./color"
import { Effect } from "./effects"

export type LedKey = { key: number; x: number; y: number }

type Ripple = {
  start: number
  x: number
  y: number
  hue: number
  key: number
  // Deepest travel of the key so far: sets how far the ripple goes
  peak: number
}

const FRAME_MS = 16
const RIPPLE_END = 300 // how far a ripple from a full press travels
const RIPPLE_MIN_END = 60 // ... and from the lightest press
const PEAK_MS = 250 // a ripple or rain streak follows its key this long
const GRID_COLS = 15 // Fire / Digital Rain grid
const GRID_ROWS = 5
const RAIN_TAIL = 40
const WHITE: RGB = [255, 255, 255]

const tri = (phase: number) => (phase < 128 ? phase * 2 : (255 - phase) * 2)
const dist = (dx: number, dy: number) =>
  dx > dy ? dx + Math.floor(dy / 2) : dy + Math.floor(dx / 2)
const rand = (n: number) => Math.floor(Math.random() * n)

/** Angle of (dx, dy), 0-255 for a full turn, as the firmware works it out. */
export function angle8(dx: number, dy: number) {
  const ax = Math.abs(dx)
  const ay = Math.abs(dy)
  if (ax === 0 && ay === 0) return 0
  let a =
    ax >= ay ? Math.floor((ay * 32) / ax) : 64 - Math.floor((ax * 32) / ay)
  if (dx < 0) a = 128 - a
  if (dy < 0) a = 256 - a
  return a & 0xff
}

function fireColor(baseHue: number, heat: number): RGB {
  if (heat < 16) return [0, 0, 0]
  const sat = heat > 215 ? 255 - (heat - 215) * 3 : 255
  return hsvToRgb((baseHue + Math.floor((heat * 40) / 255)) & 0xff, sat, heat)
}

/** Key centres on the firmware's 224 x 64 grid, from the display layout. */
export function ledKeysFromLayout(
  keys: { key: number; x: number; y: number; w: number; h: number }[],
): LedKey[] {
  return keys.map(({ key, x, y, w, h }) => ({
    key,
    x: Math.min(224, Math.round((x + w / 2) * 14)),
    y: Math.max(0, Math.min(64, Math.round((y + h / 2 - 0.5) * 16))),
  }))
}

export class LightingEngine {
  #byKey = new Map<number, LedKey>()
  #n: number
  #react: Uint8Array
  #heat: Uint8Array
  #glow: Uint8Array
  #spark: Uint8Array
  #sparkHue: Uint8Array
  #ripples: Ripple[] = []
  #frame = 0
  #last = -Infinity
  #now = 0
  #phase = 0
  #pressed: boolean[]
  #wasPressed: boolean[]
  #distance: Uint8Array
  // Presses seen since the last frame, so taps shorter than a frame count
  #latched: boolean[]
  #latchedDistance: Uint8Array
  #cfg: RgbConfig | null = null
  #c1: RGB = [0, 0, 0]
  #c2: RGB = [0, 0, 0]
  // Geometry by key, worked out once (as the firmware does at start-up)
  #angle: Uint8Array
  #radiusOf: Uint8Array
  #gcol: Uint8Array
  #grow: Uint8Array
  // Fire and Digital Rain state
  #fire: Uint8Array[] = Array.from(
    { length: GRID_ROWS },
    () => new Uint8Array(GRID_COLS),
  )
  #rainHead = new Array<number>(GRID_COLS).fill(0) // 1/16 y units
  #rainSpeed = new Array<number>(GRID_COLS).fill(0)
  #rainActive = new Array<boolean>(GRID_COLS).fill(false)
  #rainKey = new Array<number>(GRID_COLS).fill(-1)
  #rainStart = new Array<number>(GRID_COLS).fill(0)

  constructor(keys: LedKey[], keyCount: number) {
    this.#n = keyCount
    this.#react = new Uint8Array(keyCount)
    this.#heat = new Uint8Array(keyCount)
    this.#glow = new Uint8Array(keyCount)
    this.#spark = new Uint8Array(keyCount)
    this.#sparkHue = new Uint8Array(keyCount)
    this.#pressed = Array(keyCount).fill(false)
    this.#wasPressed = Array(keyCount).fill(false)
    this.#distance = new Uint8Array(keyCount)
    this.#latched = Array(keyCount).fill(false)
    this.#latchedDistance = new Uint8Array(keyCount)
    this.#angle = new Uint8Array(keyCount)
    this.#radiusOf = new Uint8Array(keyCount)
    this.#gcol = new Uint8Array(keyCount)
    this.#grow = new Uint8Array(keyCount)
    for (const led of keys) {
      if (led.key < 0 || led.key >= keyCount) continue
      this.#byKey.set(led.key, led)
      const dx = led.x - 112
      const dy = led.y - 32
      this.#angle[led.key] = angle8(dx, dy)
      this.#radiusOf[led.key] = Math.min(255, dist(Math.abs(dx), Math.abs(dy)))
      this.#gcol[led.key] = Math.min(GRID_COLS - 1, Math.floor(led.x / 15))
      this.#grow[led.key] = Math.min(GRID_ROWS - 1, Math.floor(led.y / 16))
    }
  }

  /** Key travel 0-255 (live from the keyboard, or simulated by the mouse). */
  setKey(key: number, distance: number, pressed = distance >= 100) {
    if (key < 0 || key >= this.#n) return
    const d = Math.max(0, Math.min(255, distance))
    this.#distance[key] = d
    this.#pressed[key] = pressed
    if (pressed) this.#latched[key] = true
    if (d > this.#latchedDistance[key]) this.#latchedDistance[key] = d
  }

  /** Advance the animation; returns true when a new frame was computed. */
  update(now: number, cfg: RgbConfig) {
    this.#cfg = cfg
    if (now - this.#last < FRAME_MS) return false
    this.#last = now
    this.#now = now
    this.#frame++
    const speed = cfg.speed
    this.#phase = Math.floor((now * (speed + 8)) / 1024) & 0xff
    this.#c1 = hsvToRgb(cfg.hue, cfg.sat, 255)
    this.#c2 = hsvToRgb(cfg.hue2, cfg.sat2, 255)

    const reactFade = (speed >> 4) + 2
    const sparkFade = (speed >> 5) + 2
    const cool = this.#frame % (1 + ((255 - speed) >> 5)) === 0
    for (let k = 0; k < this.#n; k++) {
      this.#react[k] = Math.max(0, this.#react[k] - reactFade)
      this.#spark[k] = Math.max(0, this.#spark[k] - sparkFade)
      this.#glow[k] = Math.max(0, this.#glow[k] - 12)
      if (cool && this.#heat[k]) this.#heat[k]--
      const travel = Math.max(this.#distance[k], this.#latchedDistance[k])
      if (travel > this.#glow[k]) this.#glow[k] = travel
      const pressed = this.#pressed[k] || this.#latched[k]
      this.#latched[k] = false
      this.#latchedDistance[k] = 0
      if (pressed && !this.#wasPressed[k]) {
        this.#react[k] = 255
        this.#heat[k] = Math.min(255, this.#heat[k] + 48)
        const led = this.#byKey.get(k)
        if (led) {
          this.#ripples.push({
            start: now,
            x: led.x,
            y: led.y,
            hue: rand(256),
            key: k,
            peak: travel,
          })
          if (this.#ripples.length > 8) this.#ripples.shift()
          if (cfg.mode === Effect.DigitalRain)
            this.#rainSpawn(
              this.#gcol[k],
              led.y,
              this.#rainBase(speed) + 2 + Math.floor(travel / 8),
              k,
              now,
            )
        }
      }
      this.#wasPressed[k] = pressed
    }
    // Ripples follow their key down for a moment, then stop where it sent them
    for (const r of this.#ripples)
      if (now - r.start < PEAK_MS)
        r.peak = Math.max(r.peak, this.#distance[r.key])
    this.#ripples = this.#ripples.filter((r) => this.#radius(r) <= this.#end(r))

    if (
      cfg.mode === Effect.Fire &&
      this.#frame % (1 + ((255 - speed) >> 6)) === 0
    )
      this.#fireStep()
    if (cfg.mode === Effect.DigitalRain) this.#rainStep(speed, now)

    if (cfg.mode === Effect.Starlight || cfg.mode === Effect.Raindrops) {
      if (Math.random() * 256 < 24 + (speed >> 1)) {
        const k = rand(this.#n)
        if (this.#spark[k] < 64) {
          this.#spark[k] = 255
          this.#sparkHue[k] = rand(256)
        }
      }
    }
    return true
  }

  #radius(r: Ripple) {
    return ((this.#now - r.start) * (16 + (this.#cfg?.speed ?? 128))) >> 9
  }

  #end(r: Ripple) {
    return (
      RIPPLE_MIN_END +
      Math.floor((r.peak * (RIPPLE_END - RIPPLE_MIN_END)) / 255)
    )
  }

  #ring(led: LedKey, width: number, cross = false): [number, number] {
    let best = 0
    let hue = 0
    for (const r of this.#ripples) {
      const radius = this.#radius(r)
      const dx = Math.abs(led.x - r.x)
      const dy = Math.abs(led.y - r.y)
      let d: number
      if (!cross) d = dist(dx, dy)
      else if (dy < 8)
        d = dx // same row
      else if (dx < 8)
        d = dy // same column
      else continue
      const off = Math.abs(d - radius)
      if (off >= width) continue
      const end = this.#end(r)
      const level = Math.floor(
        ((255 - Math.floor((off * 255) / width)) *
          (end - Math.min(radius, end))) /
          end,
      )
      if (level > best) {
        best = level
        hue = (r.hue + d) & 0xff
      }
    }
    return [best, hue]
  }

  #fireStep() {
    const f = this.#fire
    for (let r = 0; r + 1 < GRID_ROWS; r++) {
      for (let c = 0; c < GRID_COLS; c++) {
        const below = f[r + 1][c]
        const left = f[r + 1][c ? c - 1 : c]
        const right = f[r + 1][c + 1 < GRID_COLS ? c + 1 : c]
        const h = Math.floor((left + 2 * below + right) / 4)
        const cool = rand(20 + (GRID_ROWS - 1 - r) * 12)
        f[r][c] = Math.max(0, h - cool)
      }
    }
    const bottom = f[GRID_ROWS - 1]
    for (let c = 0; c < GRID_COLS; c++) {
      if (rand(256) < 110) bottom[c] = 150 + rand(106)
      else bottom[c] = Math.floor((bottom[c] * 3) / 4)
    }
    // Pressed keys feed the flames, more the deeper they go
    for (let k = 0; k < this.#n; k++) {
      const d = this.#distance[k]
      const cell = f[this.#grow[k]]
      const c = this.#gcol[k]
      if (d > 24 && d > cell[c]) cell[c] = d
    }
  }

  #rainBase(speed: number) {
    return 10 + (speed >> 4)
  }

  #rainSpawn(col: number, y: number, speed: number, key: number, now: number) {
    this.#rainHead[col] = y * 16
    this.#rainSpeed[col] = Math.min(255, speed)
    this.#rainKey[col] = key
    this.#rainStart[col] = now
    this.#rainActive[col] = true
  }

  #rainStep(speed: number, now: number) {
    for (let c = 0; c < GRID_COLS; c++) {
      if (!this.#rainActive[c]) continue
      const key = this.#rainKey[c]
      if (key >= 0 && now - this.#rainStart[c] < PEAK_MS) {
        const s =
          this.#rainBase(speed) + 2 + Math.floor(this.#distance[key] / 8)
        if (s > this.#rainSpeed[c]) this.#rainSpeed[c] = Math.min(255, s)
      }
      this.#rainHead[c] += this.#rainSpeed[c]
      if (this.#rainHead[c] > (64 + 8 + RAIN_TAIL) * 16)
        this.#rainActive[c] = false
    }
    if (rand(256) < 6 + (speed >> 4)) {
      const c = rand(GRID_COLS)
      if (!this.#rainActive[c])
        this.#rainSpawn(c, 0, this.#rainBase(speed) + rand(8), -1, now)
    }
  }

  #rainColor(led: LedKey, c1: RGB): RGB {
    const col = this.#gcol[led.key]
    if (!this.#rainActive[col]) return [0, 0, 0]
    const d = Math.floor(this.#rainHead[col] / 16) - led.y
    if (d < 0) return [0, 0, 0]
    if (d < 8) return mix(c1, WHITE, 150)
    if (d < 8 + RAIN_TAIL)
      return scale(c1, 255 - Math.floor(((d - 8) * 255) / RAIN_TAIL))
    return [0, 0, 0]
  }

  #pos(led: LedKey, dir: number) {
    const { x, y } = led
    switch (dir) {
      case 1:
        return Math.floor(((224 - x) * 255) / 224)
      case 2:
        return Math.floor((y * 255) / 64)
      case 3:
        return Math.floor(((64 - y) * 255) / 64)
      case 4:
      case 5: {
        const d = dist(Math.abs(x - 112), Math.abs(y - 32) * 2)
        const v = Math.min(255, Math.floor((d * 255) / 144))
        return dir === 4 ? v : 255 - v
      }
      default:
        return Math.floor((x * 255) / 224)
    }
  }

  /** Effect colour of one key at full brightness. */
  color(led: LedKey, keyColors: RGB[]): RGB {
    const c = this.#cfg
    if (!c) return [0, 0, 0]
    const k = led.key
    const c1 = this.#c1
    const c2 = this.#c2
    const phase = this.#phase
    switch (c.mode) {
      case Effect.Breathing:
        return hsvToRgb(c.hue, c.sat, 32 + Math.floor((tri(phase) * 223) / 255))
      case Effect.RainbowWave:
        return hsvToRgb(
          (c.hue + Math.floor((this.#pos(led, c.direction) * 2) / 3) - phase) &
            0xff,
          c.sat,
          255,
        )
      case Effect.RainbowCycle:
        return hsvToRgb((c.hue + phase) & 0xff, c.sat, 255)
      case Effect.Reactive:
        return mix(scale(c1, 48), c2, this.#react[k])
      case Effect.Custom:
        return keyColors[k] ?? [0, 0, 0]
      case Effect.KeyDepth: {
        const g = this.#glow[k]
        return scale(mix(c1, c2, g), 24 + Math.floor((g * 231) / 255))
      }
      case Effect.Ripple:
        return mix(scale(c1, 40), c2, this.#ring(led, 18)[0])
      case Effect.Splash: {
        const [level, hue] = this.#ring(led, 28)
        return level ? hsvToRgb(hue, c.sat, level) : [0, 0, 0]
      }
      case Effect.Starlight:
        return brightest(scale(c1, 40), scale(c2, this.#spark[k]))
      case Effect.Raindrops:
        return brightest(
          scale(c1, 16),
          hsvToRgb(this.#sparkHue[k], c.sat, this.#spark[k]),
        )
      case Effect.Gradient: {
        const t = this.#pos(led, c.direction)
        return hsvToRgb(
          hueLerp(c.hue, c.hue2, t),
          Math.floor((c.sat * (255 - t) + c.sat2 * t) / 255),
          255,
        )
      }
      case Effect.Heatmap: {
        const h = this.#heat[k]
        return hsvToRgb(
          170 - Math.floor((h * 170) / 255),
          255,
          32 + Math.floor((h * 223) / 255),
        )
      }
      case Effect.DualBreathing:
        return mix(c1, c2, tri(phase))
      case Effect.Wave: {
        const w = tri((this.#pos(led, c.direction) - phase) & 0xff)
        const band = Math.min(255, w > 128 ? (w - 128) * 2 : 0)
        return mix(scale(c2, 64), c1, band)
      }
      case Effect.Fire:
        return fireColor(c.hue, this.#fire[this.#grow[k]][this.#gcol[k]])
      case Effect.DigitalRain:
        return this.#rainColor(led, c1)
      case Effect.Spiral:
        return hsvToRgb(
          (c.hue + this.#angle[k] + this.#radiusOf[k] - phase) & 0xff,
          c.sat,
          255,
        )
      case Effect.ReactiveCross:
        return mix(scale(c1, 40), c2, this.#ring(led, 14, true)[0])
      case Effect.Solid:
      default:
        return c1
    }
  }
}
