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

// Some settings (the controller interface, 8 kHz polling) only take effect
// when the keyboard reconnects. The app restarts the keyboard for the user and
// remembers where they were, so the configurator opens on the same page once
// the keyboard is back.

const KEY = "mad68-studio.restart"
const MAX_AGE_MS = 30_000

export type PendingRestart = { tab: string; sub?: string; at: number }

export function markRestart(tab: string, sub?: string) {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ tab, sub, at: Date.now() }))
  } catch {
    // storage unavailable: the app just opens on its default page
  }
}

/** The restart the app asked for in the last 30 s, if any (not consumed). */
export function pendingRestart(): PendingRestart | null {
  try {
    const raw = sessionStorage.getItem(KEY)
    if (!raw) return null
    const pending = JSON.parse(raw) as PendingRestart
    return Date.now() - pending.at < MAX_AGE_MS ? pending : null
  } catch {
    return null
  }
}

/** Like pendingRestart(), but clears it: call once the keyboard is back. */
export function takeRestart(): PendingRestart | null {
  const pending = pendingRestart()
  try {
    sessionStorage.removeItem(KEY)
  } catch {
    // ignore
  }
  return pending
}
