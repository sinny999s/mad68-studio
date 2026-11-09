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

// One firmware install at a time, owned by the app rather than by the page
// that started it: the keyboard disconnects when it restarts into update mode,
// which closes the configurator (and its Firmware tab), so progress is shown
// by <FlashOverlay>, which lives on the top-level page.

import {
  installFirmware,
  type FirmwareImage,
  type FlashProgress,
} from "$lib/flasher"

// Picked up by the desktop shell and written to studio.log
const trace = (message: string) => console.info(`[flash] ${message}`)

class FlashSession {
  busy = $state(false)
  status = $state("")
  progress: FlashProgress | null = $state(null)
  result: "success" | "error" | null = $state(null)
  imageName = $state("")
  #cancelled = false

  get visible() {
    return this.busy || this.result !== null
  }

  async run(image: FirmwareImage, enterBootloader?: () => Promise<unknown>) {
    if (this.busy) return
    this.busy = true
    this.#cancelled = false
    this.progress = null
    this.result = null
    this.imageName = image.name
    this.status = "Looking for the keyboard..."
    trace(`start ${image.name} (${image.kind}, ${image.data.length} bytes)`)
    let lastLogged = ""
    try {
      await installFirmware(image, {
        enterBootloader,
        isCancelled: () => this.#cancelled,
        onStatus: (s) => {
          this.status = s
          trace(`status: ${s}`)
        },
        onProgress: (p) => {
          this.progress = p
          // Log each stage and every 25% so the log stays short
          const mark = `${p.stage} ${Math.floor(p.fraction * 4) * 25}%`
          if (mark !== lastLogged) trace((lastLogged = mark))
        },
      })
      this.status = `Installed ${image.name}. The keyboard is restarting and will reconnect by itself.`
      this.result = "success"
      trace("success")
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      trace(`failed: ${message} (last step: ${lastLogged || "none"})`)
      this.status =
        message === "Cancelled."
          ? "Cancelled. Nothing was written to the keyboard."
          : `${message} If the keyboard no longer types, it is waiting in update mode: install the firmware again from the Firmware screen.`
      this.result = "error"
    } finally {
      this.busy = false
    }
  }

  // Only possible before writing starts
  cancel() {
    this.#cancelled = true
  }

  dismiss() {
    if (this.busy) return
    this.result = null
    this.status = ""
    this.progress = null
  }
}

export const flashSession = new FlashSession()
