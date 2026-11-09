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

// MAD68 HE controller and profile commands (firmware v6, board-specific raw
// HID range; see GET_GAMEPAD in the firmware's stm32f407xx_mad/board.c).

import type { Commander } from "$lib/keyboard/commander"
import { HMK_Command } from "."

export type GamepadState = {
  version: number
  /** Controller (XInput) interface switched on in the settings */
  interfaceEnabled: boolean
  /** The computer has set up the controller (it shows up in Windows) */
  interfaceOpen: boolean
  /** Controller output on (Fn+G); off means controller keys type normally */
  active: boolean
  profile: number
  profileCount: number
  /** XInput button bits */
  buttons: number
  /** 0-255 */
  leftTrigger: number
  rightTrigger: number
  /** -32768 to 32767, up is positive */
  leftX: number
  leftY: number
  rightX: number
  rightY: number
}

export const XINPUT_BUTTONS = {
  UP: 1 << 0,
  DOWN: 1 << 1,
  LEFT: 1 << 2,
  RIGHT: 1 << 3,
  START: 1 << 4,
  BACK: 1 << 5,
  LS: 1 << 6,
  RS: 1 << 7,
  LB: 1 << 8,
  RB: 1 << 9,
  HOME: 1 << 10,
  A: 1 << 12,
  B: 1 << 13,
  X: 1 << 14,
  Y: 1 << 15,
} as const

export function parseGamepadState(view: DataView): GamepadState {
  // `view` starts after the command byte
  const flags = view.getUint8(1)
  return {
    version: view.getUint8(0),
    interfaceEnabled: (flags & 1) !== 0,
    interfaceOpen: (flags & 2) !== 0,
    active: (flags & 4) !== 0,
    profile: view.getUint8(2),
    profileCount: view.getUint8(3),
    buttons: view.getUint16(4, true),
    leftTrigger: view.getUint8(6),
    rightTrigger: view.getUint8(7),
    leftX: view.getInt16(8, true),
    leftY: view.getInt16(10, true),
    rightX: view.getInt16(12, true),
    rightY: view.getInt16(14, true),
  }
}

/**
 * Whether COMMAND_REBOOT brings the keyboard back by itself. Before firmware
 * v6.1 (controller protocol 2) the reset left the vendor bootloader's flag set,
 * so the keyboard stayed in update mode until it was unplugged.
 */
export function restartReturnsToApp(state: GamepadState | null) {
  return !!state && state.version >= 2
}

/** null when the firmware has no controller commands (before v6) */
export async function getGamepadState(
  commander: Commander,
): Promise<GamepadState | null> {
  try {
    // Older firmware answers "unknown"; fail fast instead of waiting 4 s
    return parseGamepadState(
      await commander.sendCommand({
        command: HMK_Command.GET_GAMEPAD,
        timeout: 600,
      }),
    )
  } catch {
    return null
  }
}

export async function setGamepadActive(commander: Commander, active: boolean) {
  return parseGamepadState(
    await commander.sendCommand({
      command: HMK_Command.SET_GAMEPAD_ACTIVE,
      payload: [active ? 1 : 0],
    }),
  )
}

/** Switch the keyboard's active profile, as the profile keys do */
export async function setActiveProfile(commander: Commander, profile: number) {
  return parseGamepadState(
    await commander.sendCommand({
      command: HMK_Command.SET_PROFILE,
      payload: [profile],
    }),
  )
}
