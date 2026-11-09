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

// Per-game profiles: while a listed game is running, the keyboard uses that
// game's profile; when it closes, the profile from before comes back.
//
// Switching happens only when a game starts, changes or closes, never in
// between, so a profile the user picks by hand mid-game is left alone. Each
// switch is one flash write on the keyboard (like pressing a profile key),
// which is why this follows running programs and not window focus.

export type GameRule = {
  /** Program file name, lower case, e.g. "forzahorizon5.exe" */
  exe: string
  /** Name shown in the app */
  name: string
  profile: number
}

export type GameProfileSettings = {
  enabled: boolean
  rules: GameRule[]
}

export type WatchState = {
  /** The listed game currently in charge of the profile, if any */
  game: string | null
  /** Profile to return to when no listed game is running */
  previousProfile: number | null
}

export type Decision = {
  state: WatchState
  /** Profile to switch the keyboard to now, if any */
  switchTo: number | null
  message: string | null
}

export const idleWatchState: WatchState = { game: null, previousProfile: null }

export function normalizeExe(name: string) {
  const exe = name.trim().toLowerCase().split(/[\\/]/).pop() ?? ""
  return exe.endsWith(".exe") ? exe : exe ? `${exe}.exe` : ""
}

export function decide(
  rules: GameRule[],
  running: Set<string>,
  state: WatchState,
  currentProfile: number,
): Decision {
  // The first listed game that is running wins
  const rule = rules.find((r) => running.has(r.exe)) ?? null

  if (rule) {
    if (state.game === rule.exe) return { state, switchTo: null, message: null }
    const previousProfile =
      state.game === null ? currentProfile : state.previousProfile
    return {
      state: { game: rule.exe, previousProfile },
      switchTo: rule.profile !== currentProfile ? rule.profile : null,
      message: `${rule.name} is running: Profile ${rule.profile}`,
    }
  }

  if (state.game !== null) {
    const name = rules.find((r) => r.exe === state.game)?.name ?? state.game
    const back = state.previousProfile
    return {
      state: idleWatchState,
      switchTo: back !== null && back !== currentProfile ? back : null,
      message: back !== null ? `${name} closed: back to Profile ${back}` : null,
    }
  }

  return { state, switchTo: null, message: null }
}

const STORAGE_KEY = "mad68-studio.game-profiles"

export function loadGameProfiles(): GameProfileSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<GameProfileSettings>
      return {
        enabled: parsed.enabled !== false,
        rules: Array.isArray(parsed.rules)
          ? parsed.rules
              .filter(
                (r) =>
                  r &&
                  typeof r.exe === "string" &&
                  typeof r.name === "string" &&
                  Number.isInteger(r.profile),
              )
              .map((r) => ({ ...r, exe: normalizeExe(r.exe) }))
          : [],
      }
    }
  } catch {
    // unreadable: start empty
  }
  return { enabled: true, rules: [] }
}

export function saveGameProfiles(settings: GameProfileSettings) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings))
  } catch {
    // storage unavailable: settings last until the app closes
  }
}
