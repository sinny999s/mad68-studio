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

// Rest check: how far each key reads while nobody is touching the keyboard,
// and how much room that leaves under the key's actuation point.
//
// Plain logic, no Svelte and no keyboard: the measuring loop takes any
// function that returns one reading of every key, so it runs the same against
// a real keyboard, the demo keyboard or a list of made-up readings. It only
// ever reads.
//
// Distances are on the keyboard's own scale, 0 at rest to 255 at 4.0 mm,
// unless a name says millimetres.

import {
  displayDistance,
  distanceToMM,
  distanceToUnit,
  SWITCH_DISTANCE_MM,
  SWITCH_DISTANCE_UNIT,
  unitToDistance,
} from "$lib/distance"
import type { HMK_Actuation } from "$lib/libhmk/actuation"

/** How long a check can run. The first one is the default. */
export const REST_CHECK_DURATIONS = [
  { seconds: 30, title: "30 seconds" },
  { seconds: 120, title: "2 minutes" },
  { seconds: 600, title: "10 minutes" },
]

/** Time to take your hands off the keys before the measuring starts */
export const REST_CHECK_LEAD_IN_MS = 3000

/** A key that goes past this was pressed by hand: it is left out */
export const HAND_PRESS_MM = 2
/** Less room than this under the actuation point counts as close */
export const CLOSE_ROOM_MM = 0.3
/** The lowest actuation point to try is this far above the deepest reading */
export const LOWEST_MARGIN_MM = 0.2
/** ...and never under this */
export const LOWEST_MINIMUM_MM = 0.2

// A real keyboard takes a while to answer, which paces the loop by itself.
// The demo keyboard answers at once, so leave at least this long between two
// readings. It also gives the page time to draw.
const MIN_READING_INTERVAL_MS = 10

// Measuring

export type RestMeasurement = {
  /** Times every key was read */
  readings: number
  /** From the start of the measuring to the last reading, in milliseconds */
  elapsedMs: number
  /** Per key: the deepest travel seen */
  deepest: number[]
  /** Per key: separate times it left zero */
  departures: number[]
  /** Per key: whether it was off zero in the latest reading */
  offZero: boolean[]
}

/** Why the measuring stopped: time up, cancelled, or a reading failed */
export type RestCheckEnd = "done" | "cancelled" | "failed"

export function newRestMeasurement(numKeys: number): RestMeasurement {
  return {
    readings: 0,
    elapsedMs: 0,
    deepest: Array(numKeys).fill(0),
    departures: Array(numKeys).fill(0),
    offZero: Array(numKeys).fill(false),
  }
}

/** Count one reading of every key into the measurement */
export function addRestReading(
  measurement: RestMeasurement,
  keys: ArrayLike<{ distance: number }>,
) {
  const { deepest, departures, offZero } = measurement
  const numKeys = Math.min(deepest.length, keys.length)

  measurement.readings++
  for (let i = 0; i < numKeys; i++) {
    const { distance } = keys[i]
    if (distance > deepest[i]) deepest[i] = distance
    // Readings off zero in a row are one movement, however long it lasts
    const off = distance > 0
    if (off && !offZero[i]) departures[i]++
    offZero[i] = off
  }
}

/** Wait, but no longer than until `signal` aborts */
export function sleep(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve) => {
    if (signal.aborted) return resolve()
    const done = () => {
      clearTimeout(timer)
      signal.removeEventListener("abort", done)
      resolve()
    }
    const timer = setTimeout(done, ms)
    signal.addEventListener("abort", done)
  })
}

/**
 * Read every key over and over for `durationMs`, one reading straight after
 * the other, and keep the deepest travel and the number of movements of each
 * key. Stops early when `signal` aborts or a reading fails; what was measured
 * until then is returned either way.
 */
export async function measureAtRest(options: {
  /** One reading of every key's travel */
  read: () => Promise<ArrayLike<{ distance: number }>>
  numKeys: number
  durationMs: number
  signal: AbortSignal
  /** Called after every reading with the totals so far */
  onReading?: (measurement: RestMeasurement) => void
  /** A clock in milliseconds */
  now?: () => number
}): Promise<{ measurement: RestMeasurement; ended: RestCheckEnd }> {
  const { read, numKeys, durationMs, signal, onReading } = options
  const now = options.now ?? (() => performance.now())

  const measurement = newRestMeasurement(numKeys)
  const startedAt = now()

  for (;;) {
    if (signal.aborted) return { measurement, ended: "cancelled" }

    const readAt = now()
    if (readAt - startedAt >= durationMs) return { measurement, ended: "done" }

    let keys: ArrayLike<{ distance: number }>
    try {
      keys = await read()
    } catch {
      return { measurement, ended: signal.aborted ? "cancelled" : "failed" }
    }
    // A reading that came back after the cancel is not counted
    if (signal.aborted) return { measurement, ended: "cancelled" }

    addRestReading(measurement, keys)
    measurement.elapsedMs = now() - startedAt
    onReading?.(measurement)

    const wait = MIN_READING_INTERVAL_MS - (now() - readAt)
    if (wait > 0) await sleep(wait, signal)
  }
}

// Millimetres
//
// The report does its sums on the numbers as they are shown, in hundredths of
// a millimetre, so that what is on screen adds up: a reading to 0.01 mm, and
// an actuation point as the page shows it, on one of its 0.05 mm slider
// steps. (An actuation point is stored on the 0-255 scale, which can be up to
// 0.008 mm away from the step it is shown as.)

const HUNDREDTHS_PER_UNIT = (100 * SWITCH_DISTANCE_MM) / SWITCH_DISTANCE_UNIT

const hundredths = (mm: number) => Math.round(mm * 100)
const readingHundredths = (distance: number) =>
  hundredths(distanceToMM(distance))
const actuationHundredths = (distance: number) =>
  distanceToUnit(distance) * HUNDREDTHS_PER_UNIT

// Verdicts

/**
 * - `fine`: at least 0.3 mm between the deepest resting reading and the
 *   actuation point
 * - `close`: less than that
 * - `pressed`: the resting reading reached the actuation point, so the
 *   keyboard would have pressed the key by itself
 * - `by-hand`: the key went past 2.0 mm, so somebody pressed it. No verdict.
 */
export type RestVerdict = "fine" | "close" | "pressed" | "by-hand"

export type RestKeyReport = {
  key: number
  /** The deepest travel read during the check */
  deepest: number
  /** Separate times the key left zero */
  departures: number
  actuationPoint: number
  rapidTrigger: boolean
  /** In millimetres: the actuation point minus the deepest reading */
  room: number
  /** The lowest actuation point to try */
  lowest: number
  verdict: RestVerdict
}

/** In millimetres: how far under the actuation point the deepest reading was */
export function restRoom(deepest: number, actuationPoint: number) {
  return (
    (actuationHundredths(actuationPoint) - readingHundredths(deepest)) / 100
  )
}

/**
 * The lowest actuation point to try for a key that read up to `deepest` at
 * rest: 0.2 mm above that reading and never under 0.2 mm, on the first of the
 * page's 0.05 mm slider steps that is not below it, so it is a value the
 * sliders can show and set.
 */
export function lowestActuationPoint(deepest: number) {
  const floor = Math.max(
    readingHundredths(deepest) + hundredths(LOWEST_MARGIN_MM),
    hundredths(LOWEST_MINIMUM_MM),
  )
  const unit = Math.ceil(floor / HUNDREDTHS_PER_UNIT)
  return unitToDistance(Math.min(unit, SWITCH_DISTANCE_UNIT))
}

export function restVerdict(
  deepest: number,
  { actuationPoint, rtDown }: HMK_Actuation,
): RestVerdict {
  if (distanceToMM(deepest) > HAND_PRESS_MM) return "by-hand"
  // As the firmware decides it, on its own 0-255 scale: a key presses once it
  // is at its actuation point, and a Rapid Trigger key once it is past it
  const pressed =
    rtDown > 0 ? deepest > actuationPoint : deepest >= actuationPoint
  if (pressed) return "pressed"
  return hundredths(restRoom(deepest, actuationPoint)) <
    hundredths(CLOSE_ROOM_MM)
    ? "close"
    : "fine"
}

export function reportRestKey(
  key: number,
  measurement: RestMeasurement,
  actuation: HMK_Actuation,
): RestKeyReport {
  const deepest = measurement.deepest[key] ?? 0
  return {
    key,
    deepest,
    departures: measurement.departures[key] ?? 0,
    actuationPoint: actuation.actuationPoint,
    rapidTrigger: actuation.rtDown > 0,
    room: restRoom(deepest, actuation.actuationPoint),
    lowest: lowestActuationPoint(deepest),
    verdict: restVerdict(deepest, actuation),
  }
}

export type RestReport = {
  /** Every key asked for, by key index */
  keys: Map<number, RestKeyReport>
  /** Keys worth a line of their own: they moved, or they are not fine. The
   * ones that moved furthest come first. */
  listed: RestKeyReport[]
  /** Keys pressed by hand during the check */
  byHand: RestKeyReport[]
  /** Number of keys that never moved and are fine */
  still: number
}

const severity: Record<RestVerdict, number> = {
  pressed: 0,
  close: 1,
  fine: 2,
  "by-hand": 3,
}

/**
 * What the measurement means for one profile's actuation points. `keys` are
 * the keys to report on (the ones the page draws).
 */
export function buildRestReport(
  measurement: RestMeasurement,
  actuationMap: HMK_Actuation[],
  keys: number[],
): RestReport {
  const report: RestReport = {
    keys: new Map(),
    listed: [],
    byHand: [],
    still: 0,
  }

  for (const key of keys) {
    const actuation = actuationMap[key]
    if (!actuation) continue

    const keyReport = reportRestKey(key, measurement, actuation)
    report.keys.set(key, keyReport)
    if (keyReport.verdict === "by-hand") report.byHand.push(keyReport)
    else if (keyReport.deepest > 0 || keyReport.verdict !== "fine")
      report.listed.push(keyReport)
    else report.still++
  }

  // By what was measured only, so that the lines stay where they are when an
  // actuation point changes while the report is on screen
  report.listed.sort(
    (a, b) =>
      b.deepest - a.deepest || b.departures - a.departures || a.key - b.key,
  )

  return report
}

// Words

/** A reading in millimetres, as exact as two decimals allow */
export function formatReading(distance: number) {
  return distanceToMM(distance).toFixed(2)
}

/** Room in millimetres. A Rapid Trigger key resting exactly at its actuation
 * point has none left without having pressed. */
export function formatRoom(room: number) {
  return Math.max(0, room).toFixed(2)
}

/** One line on the whole result. `keyName` turns a key index into its name. */
export function summarizeRest(
  report: RestReport,
  keyName: (key: number) => string,
) {
  const moved = report.listed.filter(({ deepest }) => deepest > 0)
  if (moved.length === 0) {
    const pressed = report.byHand.length
    if (pressed === 0) return "No key moved at rest."
    return `Apart from the ${pressed === 1 ? "key" : `${pressed} keys`} pressed during the check, no key moved at rest.`
  }

  // The key that came off worst: by verdict, then by the least room
  const worst = moved.reduce((a, b) =>
    (severity[b.verdict] - severity[a.verdict] || b.room - a.room) < 0 ? b : a,
  )
  const read = `${keyName(worst.key)} read up to ${formatReading(worst.deepest)} mm at rest.`
  const set = `It is set to ${displayDistance(worst.actuationPoint)} mm`
  const outcome =
    worst.verdict === "pressed"
      ? `${set}, so it would have pressed by itself.`
      : `${set}, so it has ${formatRoom(worst.room)} mm to spare.`

  const others = moved.length - 1
  if (others === 0) return `${read} ${outcome}`
  return `${read} ${outcome} ${others} other ${others === 1 ? "key" : "keys"} moved too.`
}

/** "30 seconds", "2 minutes", "1 minute 5 seconds" */
export function formatDuration(ms: number) {
  const total = Math.max(0, Math.round(ms / 1000))
  const minutes = Math.floor(total / 60)
  const seconds = total % 60

  const parts = []
  if (minutes > 0)
    parts.push(`${minutes} ${minutes === 1 ? "minute" : "minutes"}`)
  if (seconds > 0 || minutes === 0)
    parts.push(`${seconds} ${seconds === 1 ? "second" : "seconds"}`)
  return parts.join(" ")
}

/** Time left as "1:05", rounded up so it reaches 0:00 only at the end */
export function formatCountdown(ms: number) {
  const total = Math.max(0, Math.ceil(ms / 1000))
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`
}
