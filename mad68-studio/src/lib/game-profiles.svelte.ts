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

import {
  idleWatchState,
  loadGameProfiles,
  normalizeExe,
  saveGameProfiles,
  type GameProfileSettings,
  type WatchState,
} from "./game-profiles"

// App-wide (not per keyboard connection), so a keyboard restart or a page
// change does not forget which game is running or the profile to return to.
class GameProfiles {
  settings = $state<GameProfileSettings>(loadGameProfiles())
  watch = $state<WatchState>(idleWatchState)
  /** false once the keyboard refused SET_PROFILE (firmware before v6) */
  supported = $state<boolean | null>(null)

  #save() {
    saveGameProfiles($state.snapshot(this.settings))
  }

  setEnabled(enabled: boolean) {
    this.settings.enabled = enabled
    this.#save()
  }

  /** Adds the game, or updates its profile if it is already listed */
  add(exeOrPath: string, name: string, profile: number) {
    const exe = normalizeExe(exeOrPath)
    if (!exe) return false
    const existing = this.settings.rules.find((r) => r.exe === exe)
    if (existing) existing.profile = profile
    else this.settings.rules.push({ exe, name: name.trim() || exe, profile })
    this.#save()
    return true
  }

  remove(exe: string) {
    this.settings.rules = this.settings.rules.filter((r) => r.exe !== exe)
    if (this.watch.game === exe) this.watch = idleWatchState
    this.#save()
  }

  setProfile(exe: string, profile: number) {
    const rule = this.settings.rules.find((r) => r.exe === exe)
    if (!rule) return
    rule.profile = profile
    this.#save()
  }
}

export const gameProfiles = new GameProfiles()
