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

// Switch travel: how far the keys go down, for showing distances in
// millimetres. The keyboard knows nothing about millimetres: it measures every
// key from rest (0) to its calibrated bottom (255). This setting only decides
// what that scale means on screen, so it is kept on this computer and changing
// it changes no setting on the keyboard. An actuation point keeps its place
// on the key and is shown in the new millimetres.

export type SwitchTravel = {
  mm: number
  title: string
  description: string
}

export const SWITCH_TRAVELS: SwitchTravel[] = [
  {
    mm: 4,
    title: "4.0 mm",
    description: "The default, and what earlier versions of the app assumed.",
  },
  {
    mm: 3.5,
    title: "3.5 mm",
    description: "Gateron Magnetic Jade, Jade Pro and Jade Max, and TTC King.",
  },
]

export const DEFAULT_SWITCH_TRAVEL_MM = 4

const STORAGE_KEY = "mad68-studio.switch-travel"

function load() {
  try {
    const mm = Number(localStorage.getItem(STORAGE_KEY))
    if (SWITCH_TRAVELS.some((t) => t.mm === mm)) return mm
  } catch {
    // storage unavailable: use the default
  }
  return DEFAULT_SWITCH_TRAVEL_MM
}

export const switchTravel = $state({ mm: load() })

export function setSwitchTravel(mm: number) {
  if (!SWITCH_TRAVELS.some((t) => t.mm === mm)) return
  switchTravel.mm = mm
  try {
    localStorage.setItem(STORAGE_KEY, String(mm))
  } catch {
    // storage unavailable: the choice lasts until the app closes
  }
}
