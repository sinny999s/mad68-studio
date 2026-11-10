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

// Update checks: is there a newer MAD68 Studio on GitHub, and is the custom
// firmware on the keyboard older than the one this app installs?

import { toast } from "svelte-sonner"
import { desktop } from "./desktop"
import type { Keyboard } from "./keyboard"
import {
  compareReleases,
  findFirmwareMarker,
  formatAppRelease,
  formatFirmwareRelease,
  parseRelease,
  type Release,
} from "./release"

export const APP_VERSION = __APP_VERSION__
export const RELEASES_URL = "https://github.com/sinny999s/mad68-studio/releases"
const LATEST_RELEASE_API =
  "https://api.github.com/repos/sinny999s/mad68-studio/releases/latest"
export const BUNDLED_CUSTOM_FIRMWARE = "/firmware/MAD68HE_libhmk.bin"

/** The custom firmware on the keyboard, as far as it can be told */
export type InstalledFirmware =
  // v7.2 and later report their release
  | { known: true; release: Release }
  // Older builds don't; the controller protocol narrows it down
  | { known: false; label: string }

class Updates {
  /** A newer MAD68 Studio release, or null (none, offline, or not checked) */
  app = $state<{ version: string; url: string } | null>(null)
  /** The custom firmware this app installs, read from the bundled file */
  bundled = $state<Release | null>(null)
  /** The custom firmware on the connected keyboard; null when not connected */
  installed = $state<InstalledFirmware | null>(null)

  /** The connected keyboard runs an older custom firmware than this app has */
  firmwareOutdated = $derived(
    !!this.bundled &&
      !!this.installed &&
      (!this.installed.known ||
        compareReleases(this.installed.release, this.bundled) < 0),
  )

  #appChecked = false
  #bundledLoad: Promise<Release | null> | null = null

  /** Read which release the bundled custom firmware is (once) */
  loadBundled() {
    this.#bundledLoad ??= fetch(BUNDLED_CUSTOM_FIRMWARE)
      .then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(r.status)))
      .then((buf) => (this.bundled = findFirmwareMarker(new Uint8Array(buf))))
      .catch(() => null)
    return this.#bundledLoad
  }

  /** Ask the connected keyboard which custom firmware it runs */
  async checkKeyboard(keyboard: Keyboard) {
    this.loadBundled()
    const info = await keyboard.getFirmwareInfo().catch(() => null)
    if (info) {
      this.installed = { known: true, release: info }
      return
    }
    const gamepad = await keyboard.getGamepadState().catch(() => null)
    const label =
      gamepad === null
        ? "older than v6"
        : gamepad.version >= 3
          ? "v7.2 (early test build)"
          : gamepad.version === 2
            ? "v7.1 or older"
            : "v6"
    this.installed = { known: false, label }
  }

  forgetKeyboard() {
    this.installed = null
  }

  /**
   * Look for a newer MAD68 Studio on GitHub, once per start of the desktop
   * app. Quiet when offline or when GitHub doesn't answer.
   */
  async checkApp() {
    if (this.#appChecked || !desktop()) return
    this.#appChecked = true
    const current = parseRelease(APP_VERSION)
    if (!current) return
    try {
      const res = await fetch(LATEST_RELEASE_API, {
        headers: { Accept: "application/vnd.github+json" },
      })
      if (!res.ok) return
      const json: { tag_name?: unknown; html_url?: unknown } = await res.json()
      const latest =
        typeof json.tag_name === "string" ? parseRelease(json.tag_name) : null
      if (!latest || compareReleases(latest, current) <= 0) return
      // Only ever link to this project's own release pages
      const url =
        typeof json.html_url === "string" &&
        json.html_url.startsWith(`${RELEASES_URL}/`)
          ? json.html_url
          : RELEASES_URL
      const version = formatAppRelease(latest)
      this.app = { version, url }
      toast.info(`MAD68 Studio ${version} is available`, {
        description: `You have ${APP_VERSION}.`,
        duration: 15000,
        action: {
          label: "Download",
          onClick: () => window.open(url, "_blank"),
        },
      })
    } catch {
      // Offline or blocked: try again next start
    }
  }
}

export const updates = new Updates()

/** "custom firmware v7.2", "custom firmware v7.1 or older", ... */
export function describeInstalled(installed: InstalledFirmware) {
  return `custom firmware ${
    installed.known ? formatFirmwareRelease(installed.release) : installed.label
  }`
}
