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

// Release numbers for the MAD68 custom firmware ("v7.2") and for MAD68 Studio
// itself ("0.7.0").

export type Release = { major: number; minor: number; patch: number }

/** "v7.2", or "v7.2.1" when there is a patch number */
export function formatFirmwareRelease({ major, minor, patch }: Release) {
  return `v${major}.${minor}${patch ? `.${patch}` : ""}`
}

/** "0.7.0" */
export function formatAppRelease({ major, minor, patch }: Release) {
  return `${major}.${minor}.${patch}`
}

/** Negative when a is older than b, 0 when equal, positive when newer */
export function compareReleases(a: Release, b: Release) {
  return a.major - b.major || a.minor - b.minor || a.patch - b.patch
}

/** "0.7.0", "v0.7.0" or "7.2" (missing parts are 0); null if not a version */
export function parseRelease(text: string): Release | null {
  const m = /^v?(\d+)\.(\d+)(?:\.(\d+))?$/.exec(text.trim())
  if (!m) return null
  return { major: +m[1], minor: +m[2], patch: m[3] ? +m[3] : 0 }
}

// Every custom firmware build from v7.2 carries "MAD68CFW v<major>.<minor>.
// <patch>" in its image (board.c), so the release can be read from a file.
const MARKER = "MAD68CFW v"

/** The custom firmware release a firmware image holds, or null (before v7.2) */
export function findFirmwareMarker(data: Uint8Array): Release | null {
  const prefix = [...MARKER].map((c) => c.charCodeAt(0))
  outer: for (let i = 0; i + prefix.length < data.length; i++) {
    for (let j = 0; j < prefix.length; j++) {
      if (data[i + j] !== prefix[j]) continue outer
    }
    let text = ""
    for (let k = i + prefix.length; k < data.length && data[k] !== 0; k++) {
      text += String.fromCharCode(data[k])
      if (text.length > 16) break
    }
    const release = parseRelease(text)
    if (release) return release
  }
  return null
}
