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

import { analogCurvePresets } from "$lib/configurator/lib/gamepad"
import { HMK_FIRMWARE_MAX_VERSION, type HMK_Options } from "$lib/libhmk"
import { defaultActuation, type HMK_Actuation } from "$lib/libhmk/actuation"
import {
  DEFAULT_TICK_RATE,
  defaultAdvancedKey,
  type HMK_AdvancedKey,
} from "$lib/libhmk/advanced-keys"
import type { FirmwareInfo } from "$lib/libhmk/commands/firmware-info"
import {
  XINPUT_BUTTONS,
  type GamepadState,
} from "$lib/libhmk/commands/gamepad-state"
import type { Rgb, RgbConfig } from "$lib/libhmk/commands/rgb"
import {
  estimateScanRate,
  SCAN_SPEED_IN_EFFECT,
  type ScanSpeedInfo,
} from "$lib/libhmk/commands/scan-speed"
import { HMK_GamepadButton, type HMK_GamepadOptions } from "$lib/libhmk/gamepad"
import { defaultMacroNode, type HMK_MacroNode } from "$lib/libhmk/macro"
import { hsvToRgb } from "$lib/lighting/color"
import type {
  DuplicateProfileParams,
  GetActuationMapParams,
  GetAdvancedKeysParams,
  GetGamepadButtonsParams,
  GetGamepadOptionsParams,
  GetKeymapParams,
  GetMacrosParams,
  GetTickRateParams,
  Keyboard,
  ResetProfileParams,
  SetActuationMapParams,
  SetAdvancedKeysParams,
  SetGamepadButtonsParams,
  SetGamepadOptionsParams,
  SetKeymapParams,
  SetMacrosParams,
  SetOptionsParams,
  SetRgbKeysParams,
  SetTickRateParams,
} from "."
import { demoMetadata } from "./metadata"

const {
  adcResolution,
  numProfiles,
  numKeys,
  numAdvancedKeys,
  numMacroNodes,
  defaultKeymaps,
} = demoMetadata

type DemoKeyboardProfileState = {
  keymap: number[][]
  actuationMap: HMK_Actuation[]
  advancedKeys: HMK_AdvancedKey[]
  macros: HMK_MacroNode[]
  gamepadButtons: number[]
  gamepadOptions: HMK_GamepadOptions
  tickRate: number
}

function defaultProfile(profile: number): DemoKeyboardProfileState {
  return {
    keymap: defaultKeymaps[profile],
    actuationMap: Array(numKeys).fill(defaultActuation),
    advancedKeys: Array(numAdvancedKeys).fill(defaultAdvancedKey),
    macros: Array(numMacroNodes).fill(defaultMacroNode),
    gamepadButtons: Array(numKeys).fill(HMK_GamepadButton.NONE),
    gamepadOptions: {
      analogCurve: analogCurvePresets[0].curve,
      keyboardEnabled: true,
      gamepadOverride: false,
      squareJoystick: false,
      snappyJoystick: true,
      lastJoystick: false,
    },
    tickRate: DEFAULT_TICK_RATE,
  }
}

type DemoKeyboardState = {
  options: HMK_Options
  profiles: DemoKeyboardProfileState[]
}

export class DemoKeyboard implements Keyboard {
  id = "demo"
  demo = true
  version = HMK_FIRMWARE_MAX_VERSION
  metadata = demoMetadata

  #state: DemoKeyboardState = {
    options: {
      xInputEnabled: true,
      saveBottomOutThreshold: true,
      highPollingRateEnabled: true,
    },
    profiles: [...Array(numProfiles)].map((_, i) =>
      structuredClone(defaultProfile(i)),
    ),
  }

  #rgb: RgbConfig = {
    on: true,
    mode: 2,
    hue: 0,
    sat: 255,
    brightness: 160,
    speed: 128,
    hue2: 128,
    sat2: 255,
    direction: 0,
    idleMinutes: 0,
    indicators: 7,
    indicatorHue: 0,
    indicatorSat: 0,
  }
  #rgbKeys: Rgb[] = Array.from({ length: numKeys }, (_, i) => {
    const [r, g, b] = hsvToRgb((i * 255) / numKeys, 255, 255)
    return [r, g, b] as Rgb
  })

  async getRgb() {
    return {
      ...this.#rgb,
      modeCount: 19,
      maxBrightness: 210,
      ledCount: numKeys,
      version: 2,
      directionCount: 6,
      keyCount: numKeys,
    }
  }
  async setRgb({ data }: { data: RgbConfig }) {
    this.#rgb = { ...data, brightness: Math.min(data.brightness, 210) }
    return this.getRgb()
  }
  async getRgbKeys() {
    return this.#rgbKeys.map((c) => [...c] as Rgb)
  }
  async setRgbKeys({ offset, data }: SetRgbKeysParams) {
    data.forEach((c, i) => (this.#rgbKeys[offset + i] = [...c] as Rgb))
  }

  // Controller (firmware v6): the demo moves the left stick in a slow circle
  #gamepadActive = true
  #activeProfile = 0

  async getGamepadState(): Promise<GamepadState> {
    const enabled = this.#state.options.xInputEnabled
    const on = enabled && this.#gamepadActive
    const t = performance.now() / 1000
    const amp = on ? 26000 : 0
    return {
      version: 3,
      interfaceEnabled: enabled,
      interfaceOpen: enabled,
      active: this.#gamepadActive,
      profile: this.#activeProfile,
      profileCount: numProfiles,
      buttons: on && Math.floor(t / 1.5) % 2 === 0 ? XINPUT_BUTTONS.A : 0,
      leftTrigger: 0,
      rightTrigger: on ? Math.round(127 + 127 * Math.sin(t * 1.3)) : 0,
      leftX: Math.round(amp * Math.cos(t)),
      leftY: Math.round(amp * Math.sin(t)),
      rightX: 0,
      rightY: 0,
    }
  }
  async setGamepadActive(active: boolean) {
    this.#gamepadActive = active
    return this.getGamepadState()
  }
  async setActiveProfile(profile: number) {
    this.#activeProfile = profile
    return this.getGamepadState()
  }
  async getFirmwareInfo(): Promise<FirmwareInfo> {
    // The demo plays the firmware this app bundles
    return { major: 7, minor: 4, patch: 0, buildId: 0 }
  }

  #scanSpeed = 1
  #scanSmoothing = 0
  async getScanSpeed(): Promise<ScanSpeedInfo> {
    const settleUs = [40, 28, 18, 8, 8, 5, 5]
    const modes = [0, 0, 0, 0, 3, 3, 3]
    const samples = [2, 2, 2, 2, 2, 2, 1]
    const i = this.#scanSpeed
    // The demo shows what the firmware's own measurements would roughly say
    const scanRate = Math.round(
      estimateScanRate(settleUs[i], modes[i], samples[i]),
    )
    return {
      version: 2,
      speed: i,
      scanRate,
      capturing: false,
      settleUs,
      modes,
      samples,
      state: SCAN_SPEED_IN_EFFECT,
      smoothing: this.#scanSmoothing,
      // The main loop slows as the scan takes more of the processor's time
      loopRate: Math.round(12500 - 0.75 * scanRate),
    }
  }
  async setScanSpeed(speed: number) {
    this.#scanSpeed = speed
    return this.getScanSpeed()
  }
  async setScanSmoothing(level: number) {
    this.#scanSmoothing = level
    return this.getScanSpeed()
  }

  async disconnect() {}
  async forget() {}

  async reboot() {}
  async bootloader() {}
  async factoryReset() {}
  async recalibrate() {}
  // Simulated typing so the demo's live key travel has something to show:
  // keys go down and come back up in a rolling pattern.
  async analogInfo() {
    const t = performance.now() / 1000
    return Array.from({ length: numKeys }, (_, i) => {
      const phase = (t * 0.9 + i * 0.618) % 3.2
      const press = phase < 0.7 ? Math.sin((phase / 0.7) * Math.PI) : 0
      const distance = Math.round(press * 255)
      return { adcValue: 2300 - Math.round(press * 760), distance }
    })
  }
  async getCalibration() {
    return {
      initialRestValue: (1 << adcResolution) - 1,
      initialBottomOutThreshold: (1 << adcResolution) - 1,
    }
  }
  async setCalibration() {}
  async getProfile() {
    return this.#activeProfile
  }
  async getOptions() {
    return this.#state.options
  }
  async setOptions({ data }: SetOptionsParams) {
    this.#state.options = data
  }
  async resetProfile({ profile }: ResetProfileParams) {
    this.#state.profiles[profile] = structuredClone(defaultProfile(profile))
  }
  async duplicateProfile({ profile, srcProfile }: DuplicateProfileParams) {
    this.#state.profiles[profile] = structuredClone(
      this.#state.profiles[srcProfile],
    )
  }
  async saveCalibrationThreshold() {
    return
  }

  async getKeymap({ profile }: GetKeymapParams) {
    return this.#state.profiles[profile].keymap
  }
  async setKeymap({ profile, layer, offset, data }: SetKeymapParams) {
    for (let i = 0; i < data.length; i++) {
      this.#state.profiles[profile].keymap[layer][offset + i] = data[i]
    }
  }
  async getActuationMap({ profile }: GetActuationMapParams) {
    return this.#state.profiles[profile].actuationMap
  }
  async setActuationMap({ profile, offset, data }: SetActuationMapParams) {
    for (let i = 0; i < data.length; i++) {
      this.#state.profiles[profile].actuationMap[offset + i] = data[i]
    }
  }
  async getAdvancedKeys({ profile }: GetAdvancedKeysParams) {
    return this.#state.profiles[profile].advancedKeys
  }
  async setAdvancedKeys({ profile, offset, data }: SetAdvancedKeysParams) {
    for (let i = 0; i < data.length; i++) {
      this.#state.profiles[profile].advancedKeys[offset + i] = data[i]
    }
  }
  async getGamepadButtons(params: GetGamepadButtonsParams): Promise<number[]> {
    return this.#state.profiles[params.profile].gamepadButtons
  }
  async setGamepadButtons({ profile, offset, data }: SetGamepadButtonsParams) {
    for (let i = 0; i < data.length; i++) {
      this.#state.profiles[profile].gamepadButtons[offset + i] = data[i]
    }
  }
  async getGamepadOptions({ profile }: GetGamepadOptionsParams) {
    return this.#state.profiles[profile].gamepadOptions
  }
  async setGamepadOptions({ profile, data }: SetGamepadOptionsParams) {
    this.#state.profiles[profile].gamepadOptions = data
  }
  async getTickRate({ profile }: GetTickRateParams) {
    return this.#state.profiles[profile].tickRate
  }
  async setTickRate({ profile, data }: SetTickRateParams) {
    this.#state.profiles[profile].tickRate = data
  }
  async getMacros({ profile }: GetMacrosParams) {
    return this.#state.profiles[profile].macros
  }
  async setMacros({ profile, offset, data }: SetMacrosParams) {
    for (let i = 0; i < data.length; i++) {
      this.#state.profiles[profile].macros[offset + i] = data[i]
    }
  }
}
