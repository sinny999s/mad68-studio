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

export const SWITCH_DISTANCE_UNIT = 80
export const SWITCH_DISTANCE_MM = 4

export function unitToDistance(v: number) {
  return Math.max(
    HMK_MIN_DISTANCE,
    Math.round((v * HMK_MAX_DISTANCE) / SWITCH_DISTANCE_UNIT),
  )
}

export function distanceToUnit(v: number) {
  return Math.round((v * SWITCH_DISTANCE_UNIT) / HMK_MAX_DISTANCE)
}

export function displayUnitDistance(v: number, decimal = 2) {
  return ((v * SWITCH_DISTANCE_MM) / SWITCH_DISTANCE_UNIT).toFixed(decimal)
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
  return (v * SWITCH_DISTANCE_MM) / HMK_MAX_DISTANCE
}
