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

import { HMK_MAX_DISTANCE, HMK_MIN_DISTANCE } from "./libhmk"
import { switchTravel } from "./switch-travel.svelte"

// Distances on the keyboard run from 0 at rest to 255 at the bottom of the
// key. On screen they are millimetres of the chosen switch travel (Settings >
// Switch Travel), and the sliders move in steps of 0.05 mm: a "unit" is one
// such step, so 4.0 mm switches have 80 of them and 3.5 mm switches 70.
// Everything here reads the travel when it is called, so pages showing
// distances follow a change of the setting.

/** One slider step, in millimetres */
export const DISTANCE_STEP_MM = 0.05

/** Full travel of the keys, in millimetres */
export function switchDistanceMM() {
  return switchTravel.mm
}

/** Full travel in slider steps */
export function switchDistanceUnit() {
  return Math.round(switchTravel.mm / DISTANCE_STEP_MM)
}

export function unitToDistance(v: number) {
  return Math.max(
    HMK_MIN_DISTANCE,
    Math.round((v * HMK_MAX_DISTANCE) / switchDistanceUnit()),
  )
}

export function distanceToUnit(v: number) {
  return Math.round((v * switchDistanceUnit()) / HMK_MAX_DISTANCE)
}

export function displayUnitDistance(v: number, decimal = 2) {
  return (v * DISTANCE_STEP_MM).toFixed(decimal)
}

export function displayDistance(v: number, decimal = 2) {
  return displayUnitDistance(distanceToUnit(v), decimal)
}

/**
 * Exact travel in millimetres of a 0-255 distance. `displayDistance` snaps to
 * the 0.05 mm steps of the sliders, which suits a setting; this is for
 * readings, where 0.33 mm should not become 0.35.
 */
export function distanceToMM(v: number) {
  return (v * switchDistanceMM()) / HMK_MAX_DISTANCE
}
