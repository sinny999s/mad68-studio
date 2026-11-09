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

import type { RgbConfig } from "$lib/libhmk/commands/rgb"
import { Keycode } from "$lib/libhmk/keycodes"
import type { RGB } from "./color"

// Effect numbers match the firmware (rgb.c RGB_MODE_*)
export enum Effect {
  Solid = 0,
  Breathing,
  RainbowWave,
  RainbowCycle,
  Reactive,
  Custom,
  KeyDepth,
  Ripple,
  Splash,
  Starlight,
  Raindrops,
  Gradient,
  Heatmap,
  DualBreathing,
  Wave,
  // Firmware v7
  Fire,
  DigitalRain,
  Spiral,
  ReactiveCross,
}

export type EffectInfo = {
  name: string
  description: string
  color?: boolean // uses colour 1
  color2?: boolean // uses colour 2
  speed?: boolean
  direction?: boolean
  keys?: boolean // reacts to typing
}

export const EFFECTS: EffectInfo[] = [
  { name: "Solid", description: "One colour on every key.", color: true },
  {
    name: "Breathing",
    description: "Slowly fades in and out.",
    color: true,
    speed: true,
  },
  {
    name: "Rainbow Wave",
    description: "A rainbow that flows across the board.",
    color: true,
    speed: true,
    direction: true,
  },
  {
    name: "Rainbow Cycle",
    description: "The whole board cycles through the rainbow.",
    color: true,
    speed: true,
  },
  {
    name: "Reactive",
    description: "Keys flash the second colour when pressed, then fade back.",
    color: true,
    color2: true,
    speed: true,
    keys: true,
  },
  {
    name: "Custom",
    description: "Your own colour on every key. Paint keys on the preview.",
  },
  {
    name: "Key Depth",
    description:
      "Hall-effect special: keys glow brighter and turn to the second colour the further you press them.",
    color: true,
    color2: true,
    keys: true,
  },
  {
    name: "Ripple",
    description:
      "Rings of the second colour spread out from every key press, further the harder you press.",
    color: true,
    color2: true,
    speed: true,
    keys: true,
  },
  {
    name: "Splash",
    description:
      "Rainbow rings burst out from every key press, further the harder you press.",
    speed: true,
    keys: true,
  },
  {
    name: "Starlight",
    description: "Keys twinkle in the second colour.",
    color: true,
    color2: true,
    speed: true,
  },
  {
    name: "Raindrops",
    description: "Random keys light up in random colours.",
    color: true,
    speed: true,
  },
  {
    name: "Gradient",
    description: "Blends from the first colour to the second across the board.",
    color: true,
    color2: true,
    direction: true,
  },
  {
    name: "Heatmap",
    description: "Keys warm from blue to red the more you type them.",
    speed: true,
    keys: true,
  },
  {
    name: "Dual Breathing",
    description: "Fades back and forth between the two colours.",
    color: true,
    color2: true,
    speed: true,
  },
  {
    name: "Wave",
    description: "A band of the first colour sweeps over the second.",
    color: true,
    color2: true,
    speed: true,
    direction: true,
  },
  {
    name: "Fire",
    description:
      "Flames flicker up from the bottom row, starting from the first colour. Pressing a key feeds the fire there, more the harder you press.",
    color: true,
    speed: true,
    keys: true,
  },
  {
    name: "Digital Rain",
    description:
      "Streaks of the first colour fall down the columns. Pressing a key drops one from it, faster the harder you press.",
    color: true,
    speed: true,
    keys: true,
  },
  {
    name: "Spiral",
    description:
      "A rainbow spiral turns around the middle of the board. The colour sets where the rainbow starts.",
    color: true,
    speed: true,
  },
  {
    name: "Reactive Cross",
    description:
      "Each press sends the second colour along its row and column, further the harder you press.",
    color: true,
    color2: true,
    speed: true,
    keys: true,
  },
]

export const DIRECTIONS = [
  "Left to right",
  "Right to left",
  "Top to bottom",
  "Bottom to top",
  "Centre out",
  "Edges in",
]

export const IDLE_OPTIONS = [0, 1, 2, 5, 10, 15, 30, 60]

export const SWATCHES: [string, number, number][] = [
  ["Red", 0, 255],
  ["Orange", 14, 255],
  ["Amber", 26, 255],
  ["Yellow", 40, 255],
  ["Lime", 64, 255],
  ["Green", 85, 255],
  ["Mint", 110, 200],
  ["Cyan", 128, 255],
  ["Sky", 145, 255],
  ["Blue", 170, 255],
  ["Purple", 192, 255],
  ["Magenta", 213, 255],
  ["Pink", 235, 160],
  ["White", 0, 0],
]

//--------------------------------------------------------------------+
// Key groups for painting (by what the key does on the base layer)
//--------------------------------------------------------------------+

const range = (a: number, b: number) =>
  Array.from({ length: b - a + 1 }, (_, i) => a + i)

export const KEY_GROUPS: { name: string; keycodes: number[] }[] = [
  {
    name: "WASD",
    keycodes: [Keycode.KC_W, Keycode.KC_A, Keycode.KC_S, Keycode.KC_D],
  },
  {
    name: "Arrows",
    keycodes: [
      Keycode.KC_UP,
      Keycode.KC_DOWN,
      Keycode.KC_LEFT,
      Keycode.KC_RIGHT,
    ],
  },
  { name: "Letters", keycodes: range(Keycode.KC_A, Keycode.KC_Z) },
  {
    name: "Numbers",
    keycodes: [
      ...range(Keycode.KC_1, Keycode.KC_0),
      Keycode.KC_MINUS,
      Keycode.KC_EQUAL,
    ],
  },
  {
    name: "Modifiers",
    keycodes: [
      ...range(Keycode.KC_LEFT_CTRL, Keycode.KC_RIGHT_GUI),
      Keycode.KC_TAB,
      Keycode.KC_CAPS_LOCK,
      Keycode.KC_ENTER,
      Keycode.KC_BACKSPACE,
      Keycode.KC_ESCAPE,
    ],
  },
  { name: "Space", keycodes: [Keycode.KC_SPACE] },
]

//--------------------------------------------------------------------+
// Presets
//--------------------------------------------------------------------+

export type LightingPreset = {
  name: string
  description?: string
  config: Partial<RgbConfig>
  // Per-key colours from the base-layer keymap (Custom presets)
  keys?: (baseLayer: number[]) => RGB[]
}

const paint =
  (base: RGB, groups: [number[], RGB][]) =>
  (baseLayer: number[]): RGB[] =>
    baseLayer.map((kc) => {
      for (const [codes, color] of groups) if (codes.includes(kc)) return color
      return base
    })

const group = (name: string) =>
  KEY_GROUPS.find((g) => g.name === name)?.keycodes ?? []

export const PRESETS: LightingPreset[] = [
  {
    name: "Ocean",
    config: {
      mode: Effect.Gradient,
      hue: 128,
      sat: 255,
      hue2: 175,
      sat2: 255,
      direction: 0,
    },
  },
  {
    name: "Sunset",
    config: {
      mode: Effect.Gradient,
      hue: 18,
      sat: 255,
      hue2: 205,
      sat2: 230,
      direction: 2,
    },
  },
  {
    name: "Cyberpunk",
    config: {
      mode: Effect.Wave,
      hue: 213,
      sat: 255,
      hue2: 135,
      sat2: 255,
      direction: 0,
      speed: 90,
    },
  },
  {
    name: "Aurora",
    config: {
      mode: Effect.Wave,
      hue: 95,
      sat: 255,
      hue2: 185,
      sat2: 255,
      direction: 4,
      speed: 70,
    },
  },
  {
    name: "Ember",
    config: {
      mode: Effect.Ripple,
      hue: 8,
      sat: 255,
      hue2: 32,
      sat2: 255,
      speed: 120,
    },
  },
  {
    name: "Pressure",
    config: { mode: Effect.KeyDepth, hue: 180, sat: 255, hue2: 230, sat2: 120 },
  },
  {
    name: "Night Sky",
    config: {
      mode: Effect.Starlight,
      hue: 170,
      sat: 255,
      hue2: 40,
      sat2: 60,
      speed: 60,
    },
  },
  {
    name: "Matrix",
    config: { mode: Effect.DigitalRain, hue: 85, sat: 255, speed: 150 },
  },
  {
    name: "Stealth",
    config: { mode: Effect.Solid, hue: 0, sat: 0, brightness: 40 },
  },
  {
    name: "Lights Off",
    description:
      "Turns the LEDs off and keeps your effect for later. From firmware v7.1 the keyboard also stops drawing frames, so lighting takes almost no processing time.",
    config: { on: false },
  },
  {
    name: "Gamer",
    config: { mode: Effect.Custom },
    keys: paint(
      [20, 30, 90],
      [
        [group("WASD"), [255, 0, 40]],
        [group("Arrows"), [255, 0, 40]],
        [[Keycode.KC_ESCAPE], [255, 200, 0]],
        [
          [Keycode.KC_SPACE, Keycode.KC_LEFT_SHIFT, Keycode.KC_LEFT_CTRL],
          [0, 200, 255],
        ],
      ],
    ),
  },
  {
    name: "Typist",
    config: { mode: Effect.Custom },
    keys: paint(
      [255, 170, 90],
      [
        [group("Modifiers"), [90, 120, 255]],
        [group("Numbers"), [200, 120, 255]],
        [group("Arrows"), [90, 255, 180]],
      ],
    ),
  },
]
