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

// Typing pause on the Calibration page (custom firmware v7.6): while it is on,
// keys pressed to calibrate don't type, open the Start menu or trigger
// shortcuts. The keyboard ends a pause by itself 3 s after the app last asked
// for it, so closing the app, a crash or a lost connection can never leave
// the keyboard silent; the app also ends it as soon as its window loses focus
// or the page is left.

import { keyboardContext } from "$lib/keyboard"
import type { HMK_AnalogInfo } from "$lib/libhmk/commands"
import { Context } from "runed"
import { toast } from "svelte-sonner"
import { SvelteSet } from "svelte/reactivity"

/** The keyboard types again this long after the app stops asking */
const PAUSE_MS = 3000
/** How often the app asks again while it wants the pause */
const REPEAT_MS = 1000
/** A key counts as pressed all the way from this travel (0-255) */
export const FULL_PRESS = 250

export class TypingPause {
  /** null until known; false when the firmware is older than v7.6 */
  supported = $state<boolean | null>(null)
  paused = $state(false)
  /** Guided calibration, from Recalibrate until Done */
  calibrating = $state(false)
  /** Keys pressed all the way during the guided calibration */
  pressed = new SvelteSet<number>()
  /** Why the last pause ended without being asked to, until the next one */
  endedBecause = $state<string | null>(null)

  #keyboard = keyboardContext.get()
  #timer: ReturnType<typeof setInterval> | null = null

  /** Find out whether the keyboard's firmware can pause typing */
  async check() {
    const state = await this.#keyboard.getInputPause().catch(() => null)
    this.supported = state !== null
    // A pause left over from an earlier visit ends now
    if (state?.paused) this.#send("resume")
  }

  /** Pause typing until stop() (the app keeps asking while it is on) */
  async start(): Promise<boolean> {
    if (this.paused) return true
    try {
      const state = await this.#keyboard.setInputPause({
        action: "pause",
        ms: PAUSE_MS,
      })
      if (!state.paused) throw new Error("the keyboard did not pause")
    } catch (err) {
      console.error(err)
      toast.error("Couldn't pause typing", {
        description: "Keys still type while you calibrate.",
      })
      return false
    }
    this.paused = true
    this.endedBecause = null
    this.#timer = setInterval(() => this.#repeat(), REPEAT_MS)
    return true
  }

  async #repeat() {
    try {
      await this.#keyboard.setInputPause({ action: "pause", ms: PAUSE_MS })
    } catch {
      if (this.paused) this.stop("the keyboard stopped answering")
    }
  }

  #send(action: "resume") {
    this.#keyboard.setInputPause({ action }).catch(() => {})
  }

  /**
   * End the pause (and a guided calibration). `because` says why when the
   * user didn't ask for it, and is shown on the page.
   */
  stop(because?: string) {
    if (this.#timer !== null) clearInterval(this.#timer)
    this.#timer = null
    const was = this.paused
    this.paused = false
    this.calibrating = false
    if (!was) return
    if (because) this.endedBecause = because
    this.#send("resume")
  }

  /** Start a guided calibration (typing paused first, if it can be) */
  async beginCalibration() {
    this.pressed.clear()
    this.calibrating = (await this.start()) && this.paused
  }

  /** Note the keys pressed all the way during a guided calibration */
  track(analogInfo: HMK_AnalogInfo[]) {
    if (!this.calibrating) return
    for (let i = 0; i < analogInfo.length; i++)
      if (analogInfo[i].distance >= FULL_PRESS && !this.pressed.has(i))
        this.pressed.add(i)
  }
}

export const typingPauseContext = new Context<TypingPause>("mad68-typing-pause")
