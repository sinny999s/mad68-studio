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

import { keyboardContext } from "$lib/keyboard"
import type { GamepadState } from "$lib/libhmk/commands/gamepad-state"
import { Context } from "runed"

const SLOW_MS = 1000
const FAST_MS = 50

/**
 * Live controller state from firmware v6 (GET_GAMEPAD), polled while the
 * Gamepad page is open: once a second for the status, 20 times a second
 * while the Test view is showing the sticks.
 */
export class GamepadLive {
  /** Latest state; null until the first answer or on older firmware */
  state = $state<GamepadState | null>(null)
  /** null = not known yet, false = firmware before v6 */
  supported = $state<boolean | null>(null)
  fast = $state(false)

  #keyboard = keyboardContext.get()

  /** Poll until the returned function is called. */
  start() {
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const tick = async () => {
      if (stopped) return
      if (!document.hidden) {
        const next = await this.#keyboard.getGamepadState().catch(() => null)
        if (stopped) return
        if (next) {
          this.state = next
          this.supported = true
        } else if (this.supported === null) {
          this.supported = false
        }
      }
      // Older firmware: check again now and then (cheap), never fast
      const wait =
        this.supported === false ? 5000 : this.fast ? FAST_MS : SLOW_MS
      timer = setTimeout(tick, wait)
    }
    tick()
    return () => {
      stopped = true
      clearTimeout(timer)
    }
  }

  async setActive(active: boolean) {
    if (this.state) this.state = { ...this.state, active }
    this.state = await this.#keyboard.setGamepadActive(active)
  }
}

export const gamepadLiveContext = new Context<GamepadLive>("hmk-gamepad-live")
