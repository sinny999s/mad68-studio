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

import { findFirmwareMarker, type Release } from "./release"

// Firmware updates for the MadLions MAD68 HE through its vendor IAP bootloader.
//
// The protocol is the official configurator's "DuckBread" sector upgrade
// (hub.fgg.com.cn), confirmed against a capture of a working official install.
// 64-byte HID reports sent as report 0 to the bootloader (373B:2024, usage page
// 0xFF / usage 1); byte 63 of every report is the OR of bytes 0..62, and each
// report is echoed back as its acknowledgement:
//   AA 01 <pages u32 BE> <0x08020000 BE>     start (2 KB pages)
//   AA 02 <sectors u32 BE> <0x08020000 BE>   erase (128 KB sectors), then wait
//                                            0.3 s per sector
//   AA 03 <address BE> <index> <len> <last> <data <= 54>
//                                            firmware body (no header/trailer)
//                                            in 2 KB pages, 54-byte pieces;
//                                            the echo's byte 63 must match
//   AA 96                                    after 200 ms: finish, restart
// The upgrade file is checked here exactly like tools/pack.py checks it.

export const MADLIONS_VID = 0x373b
export const STOCK_PID = 0x1058
export const BOOTLOADER_PID = 0x2024
export const CUSTOM_PID = 0x1f58
export const QMK_PID = 0x1f59
const HMK_USAGE_PAGE = 0xffab
const HMK_COMMAND_BOOTLOADER = 2
// The QMK build (mad68-qmk) takes a VIA custom value on its own channel as its
// update-mode request: set value, channel 0, value 'M', "BOOT". QMK's raw HID
// interface is usage page 0xFF60, usage 0x61, 32-byte reports.
const QMK_USAGE_PAGE = 0xff60
const QMK_USAGE = 0x61
const QMK_UPDATE_MODE_REQUEST = [0x07, 0x00, 0x4d, 0x42, 0x4f, 0x4f, 0x54]
// The bootloader exposes more than one HID interface; the update channel is
// the vendor collection the official tool filters for (usage page 0xFF,
// usage 1). Another interface streams input reports on its own, which look
// like acknowledgements but never reach the update code.
const BOOT_USAGE_PAGE = 0x00ff
const BOOT_USAGE = 0x0001

const HEADER = 14
const REPORT = 64
const PAGE = 2048
const SECTOR = 128 * 1024
const PIECE = 54
const APP_BASE = 0x08020000
const APP_MAX = 128 * 1024
const CUSTOM_VERSION = 0x0f01
const QMK_VERSION = 0x0f02

export type FirmwareImage = {
  name: string
  data: Uint8Array
  version: number
  bodyLength: number
  kind: "custom" | "qmk" | "stock"
  /** Custom firmware release (from v7.2); null for older builds and others */
  release: Release | null
}

export type KeyboardMode = "custom" | "qmk" | "stock" | "bootloader" | "none"

function be16(d: Uint8Array, o: number) {
  return (d[o] << 8) | d[o + 1]
}
function be32(d: Uint8Array, o: number) {
  return ((d[o] << 24) | (d[o + 1] << 16) | (d[o + 2] << 8) | d[o + 3]) >>> 0
}
function le32(d: Uint8Array, o: number) {
  return (d[o] | (d[o + 1] << 8) | (d[o + 2] << 16) | (d[o + 3] << 24)) >>> 0
}

export function displayFirmwareVersion(version: number) {
  if (version === CUSTOM_VERSION) return "custom (libhmk)"
  if (version === QMK_VERSION) return "QMK"
  const hex = version.toString(16).padStart(4, "0")
  return `v${parseInt(hex.slice(0, 2), 10)}.${hex.slice(2)}`
}

/** Validate a MAD68 HE upgrade file; throws with a readable reason. */
export function parseFirmware(name: string, data: Uint8Array): FirmwareImage {
  if (data.length < HEADER + 8 + 4) throw new Error("File is too small.")
  if (
    be16(data, 0) !== MADLIONS_VID ||
    be16(data, 2) !== STOCK_PID ||
    be16(data, 4) !== MADLIONS_VID ||
    be16(data, 6) !== BOOTLOADER_PID
  ) {
    throw new Error("This is not a MAD68 HE RGB firmware file.")
  }
  const version = be16(data, 8)
  const bodyLength = be32(data, 10)
  if (data.length !== HEADER + bodyLength + 4) {
    throw new Error("File length does not match its header (damaged file?).")
  }
  if (bodyLength > APP_MAX) {
    throw new Error("Firmware is larger than the application area.")
  }
  let sum = 0
  for (let i = HEADER; i < HEADER + bodyLength; i++) sum += data[i]
  const expected = (0x55555555 - sum) >>> 0
  if (be32(data, HEADER + bodyLength) !== expected) {
    throw new Error("Checksum mismatch (damaged file?).")
  }
  const sp = le32(data, HEADER)
  const reset = le32(data, HEADER + 4)
  if (!(sp > 0x20000000 && sp <= 0x20020000)) {
    throw new Error("Firmware has an invalid stack pointer.")
  }
  if (
    !(reset & 1) ||
    (reset & ~1) < APP_BASE ||
    (reset & ~1) >= APP_BASE + bodyLength
  ) {
    throw new Error("Firmware entry point is outside the application area.")
  }
  return {
    name,
    data,
    version,
    bodyLength,
    kind:
      version === CUSTOM_VERSION
        ? "custom"
        : version === QMK_VERSION
          ? "qmk"
          : "stock",
    release:
      version === CUSTOM_VERSION
        ? findFirmwareMarker(data.subarray(HEADER, HEADER + bodyLength))
        : null,
  }
}

export async function loadBundledFirmware(path: string, name: string) {
  const response = await fetch(path)
  if (!response.ok) throw new Error(`Could not load ${name}.`)
  return parseFirmware(name, new Uint8Array(await response.arrayBuffer()))
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

// Lines starting with "[flash]" are written to studio.log by the desktop shell
const trace = (message: string) => console.info(`[flash] ${message}`)
const hex = (v: DataView, n = 8) =>
  Array.from(new Uint8Array(v.buffer, v.byteOffset, Math.min(n, v.byteLength)))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join(" ")
const describe = (d: HIDDevice) =>
  `${d.productId.toString(16)} [${d.collections
    .map((c) => `${c.usagePage?.toString(16)}:${c.usage?.toString(16)}`)
    .join(", ")}]`

async function madlionsDevices() {
  if (!("hid" in navigator)) return []
  return (await navigator.hid.getDevices()).filter(
    (d) => d.vendorId === MADLIONS_VID,
  )
}

/** Which firmware the keyboard is currently running, if it is connected. */
export async function detectKeyboardMode(): Promise<KeyboardMode> {
  let devices = await madlionsDevices()
  if (devices.length === 0 && "hid" in navigator) {
    // The desktop app grants MadLions devices without a prompt
    try {
      await navigator.hid.requestDevice({
        filters: [{ vendorId: MADLIONS_VID }],
      })
    } catch {
      // ignore
    }
    devices = await madlionsDevices()
  }
  if (devices.some((d) => d.productId === BOOTLOADER_PID)) return "bootloader"
  if (devices.some((d) => d.productId === CUSTOM_PID)) return "custom"
  if (devices.some((d) => d.productId === QMK_PID)) return "qmk"
  if (devices.some((d) => d.productId === STOCK_PID)) return "stock"
  return "none"
}

/** Ask the custom firmware to restart into the vendor bootloader. */
export async function customFirmwareToBootloader() {
  const device = (await madlionsDevices()).find(
    (d) =>
      d.productId === CUSTOM_PID &&
      d.collections.some((c) => c.usagePage === HMK_USAGE_PAGE),
  )
  if (!device) throw new Error("Custom firmware not found.")
  if (!device.opened) await device.open()
  const report = new Uint8Array(REPORT)
  report[0] = HMK_COMMAND_BOOTLOADER
  // The keyboard drops off the bus immediately; a failed send is expected
  await device.sendReport(0, report).catch(() => undefined)
  await device.close().catch(() => undefined)
}

/** Ask the QMK build to restart into the vendor bootloader. */
export async function qmkFirmwareToBootloader() {
  const device = (await madlionsDevices()).find(
    (d) =>
      d.productId === QMK_PID &&
      d.collections.some(
        (c) => c.usagePage === QMK_USAGE_PAGE && c.usage === QMK_USAGE,
      ),
  )
  if (!device) throw new Error("QMK firmware not found.")
  if (!device.opened) await device.open()
  const report = new Uint8Array(32)
  report.set(QMK_UPDATE_MODE_REQUEST)
  // It answers, then restarts about 50 ms later
  await device.sendReport(0, report).catch(() => undefined)
  await device.close().catch(() => undefined)
}

/** The bootloader's update interface (not just any 373B:2024 interface). */
export function isBootloaderUpdateInterface(d: HIDDevice) {
  return (
    d.vendorId === MADLIONS_VID &&
    d.productId === BOOTLOADER_PID &&
    d.collections.some(
      (c) => c.usagePage === BOOT_USAGE_PAGE && c.usage === BOOT_USAGE,
    )
  )
}

/** Wait for the bootloader's update interface to appear. */
export async function waitForBootloader(
  timeoutMs: number,
  isCancelled: () => boolean = () => false,
) {
  const end = Date.now() + timeoutMs
  while (Date.now() < end && !isCancelled()) {
    let device = (await madlionsDevices()).find(isBootloaderUpdateInterface)
    if (!device) {
      try {
        const granted = await navigator.hid.requestDevice({
          filters: [
            {
              vendorId: MADLIONS_VID,
              productId: BOOTLOADER_PID,
              usagePage: BOOT_USAGE_PAGE,
              usage: BOOT_USAGE,
            },
          ],
        })
        device = granted.find(isBootloaderUpdateInterface)
      } catch {
        // no device yet
      }
    }
    if (device) return device
    await sleep(400)
  }
  return null
}

/**
 * After an install the bootloader restarts into the new firmware. Wait for
 * the keyboard to come back with the expected identity; anything else means
 * the update did not take.
 */
export async function waitForRestart(
  expectedPid: number,
  timeoutMs: number,
): Promise<"restarted" | "still-updating" | "came-back" | "missing"> {
  const start = Date.now()
  const end = start + timeoutMs
  let sawBootloader = true
  // The bootloader leaving and coming back means the new firmware started
  // but went back to update mode (its fail-safe, e.g. no USB connection)
  let leftBootloader = false
  let last = ""
  while (Date.now() < end) {
    const pids = (await madlionsDevices()).map((d) => d.productId)
    const seen =
      [...new Set(pids)]
        .map((p) => p.toString(16))
        .sort()
        .join(", ") || "nothing"
    if (seen !== last) {
      trace(`after ${Date.now() - start} ms: ${seen}`)
      last = seen
    }
    if (pids.includes(expectedPid) && !pids.includes(BOOTLOADER_PID)) {
      return "restarted"
    }
    sawBootloader = pids.includes(BOOTLOADER_PID)
    if (!sawBootloader) leftBootloader = true
    await sleep(300)
  }
  if (sawBootloader) return leftBootloader ? "came-back" : "still-updating"
  return "missing"
}

type Report = Uint8Array<ArrayBuffer>

const bytesBE = (v: number) => [
  (v >>> 24) & 0xff,
  (v >>> 16) & 0xff,
  (v >>> 8) & 0xff,
  v & 0xff,
]

/** A 64-byte update report; byte 63 is the OR of all other bytes. */
export function iapReport(bytes: number[]): Report {
  const report = new Uint8Array(new ArrayBuffer(REPORT))
  report.set(bytes.slice(0, REPORT - 1))
  let check = 0
  for (let i = 0; i < REPORT - 1; i++) check |= report[i]
  report[REPORT - 1] = check
  return report
}

/** Every report of an install, in order (also used by the tests). */
export function buildUpgradeReports(image: FirmwareImage) {
  const body = image.data.subarray(HEADER, HEADER + image.bodyLength)
  const pages = Math.ceil(body.length / PAGE)
  const sectors = Math.ceil(body.length / SECTOR)
  // Sector 5 only: sectors 6 and 7 hold the custom firmware's settings
  if (sectors !== 1)
    throw new Error("Firmware does not fit the application area.")
  const data: Report[] = []
  for (let page = 0; page < pages; page++) {
    const pageStart = page * PAGE
    const pageEnd = Math.min(pageStart + PAGE, body.length)
    const pieces = Math.ceil((pageEnd - pageStart) / PIECE)
    for (let k = 0; k < pieces; k++) {
      const off = pageStart + k * PIECE
      const piece = body.subarray(off, Math.min(off + PIECE, pageEnd))
      data.push(
        iapReport([
          0xaa,
          0x03,
          ...bytesBE(APP_BASE + off),
          k,
          piece.length,
          k === pieces - 1 ? 1 : 0,
          ...piece,
        ]),
      )
    }
  }
  return {
    sectors,
    start: iapReport([0xaa, 0x01, ...bytesBE(pages), ...bytesBE(APP_BASE)]),
    erase: iapReport([0xaa, 0x02, ...bytesBE(sectors), ...bytesBE(APP_BASE)]),
    data,
    finish: iapReport([0xaa, 0x96]),
  }
}

class BootloaderLink {
  #device: HIDDevice
  #waiting: ((v: DataView) => void) | null = null

  constructor(device: HIDDevice) {
    this.#device = device
    device.oninputreport = (e) => {
      const resolve = this.#waiting
      this.#waiting = null
      resolve?.(e.data)
    }
  }

  async transfer(report: Report, timeoutMs = 5000) {
    const reply = new Promise<DataView>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#waiting = null
        reject(new Error("The bootloader stopped responding."))
      }, timeoutMs)
      this.#waiting = (v) => {
        clearTimeout(timer)
        resolve(v)
      }
    })
    await this.#device.sendReport(0, report)
    return reply
  }

  close() {
    this.#device.oninputreport = null
  }
}

export type FlashProgress = {
  stage: "erase" | "write" | "finish" | "done"
  fraction: number
}

/** Program `image` through an already connected bootloader device. */
export async function flashFirmware(
  device: HIDDevice,
  image: FirmwareImage,
  onProgress: (p: FlashProgress) => void,
) {
  const reports = buildUpgradeReports(image)
  if (!device.opened) await device.open()
  const link = new BootloaderLink(device)
  try {
    onProgress({ stage: "erase", fraction: 0 })
    let t0 = Date.now()
    const started = await link.transfer(reports.start)
    trace(`start reply after ${Date.now() - t0} ms: ${hex(started)}`)
    t0 = Date.now()
    const erased = await link.transfer(reports.erase, 15000)
    trace(`erase reply after ${Date.now() - t0} ms: ${hex(erased)}`)
    // The official tool gives the flash 0.3 s per sector after the reply
    await sleep(reports.sectors * 300)

    const total = reports.data.length
    for (let i = 0; i < total; i++) {
      const sent = reports.data[i]
      const r = await link.transfer(sent)
      const echoed = r.byteLength >= REPORT ? r.getUint8(REPORT - 1) : -1
      if (i === 0) trace(`first write reply: ${hex(r)}`)
      if (echoed !== sent[REPORT - 1]) {
        trace(`write ${i}/${total} rejected: ${hex(r, 16)}`)
        throw new Error(
          "The keyboard rejected the firmware data (write failure).",
        )
      }
      onProgress({ stage: "write", fraction: (i + 1) / total })
    }

    onProgress({ stage: "finish", fraction: 1 })
    await sleep(200)
    // The bootloader restarts right after answering, or sometimes before
    await link
      .transfer(reports.finish, 3000)
      .then((r) => trace(`finish reply: ${hex(r)}`))
      .catch(() => trace("no finish reply (bootloader restarting)"))
    onProgress({ stage: "done", fraction: 1 })
  } finally {
    link.close()
    await device.close().catch(() => undefined)
  }
}

// Holding Esc while plugging in does not enter update mode on this keyboard
// (except with the QMK build, which checks for it itself)
export const MANUAL_UPDATE_MODE_INSTRUCTIONS =
  "With the custom firmware, press Fn+Ins to switch it to update mode. With the QMK firmware, press Fn+Ins, or unplug the keyboard and hold Esc while plugging it back in. With the stock firmware, start the update from MadLions' own updater."

export type InstallEvents = {
  onStatus: (status: string) => void
  onProgress: (p: FlashProgress) => void
  isCancelled?: () => boolean
  // Restarts the connected custom firmware into update mode
  enterBootloader?: () => Promise<unknown>
}

/**
 * Get the keyboard into update mode by whatever route its current firmware
 * allows, then install `image`.
 */
export async function installFirmware(image: FirmwareImage, ev: InstallEvents) {
  const isCancelled = ev.isCancelled ?? (() => false)
  const current = await detectKeyboardMode()
  let device: HIDDevice | null = null

  if (current === "custom" || current === "qmk") {
    ev.onStatus("Restarting the keyboard into update mode...")
    // The firmware restarts without answering, so look for the bootloader
    // straight away instead of waiting for the command to time out
    const request =
      current === "qmk"
        ? qmkFirmwareToBootloader
        : (ev.enterBootloader ?? customFirmwareToBootloader)
    void request().catch(() => undefined)
    device = await waitForBootloader(10000, isCancelled)
  }
  if (!device && !isCancelled()) {
    if (current === "custom" || current === "qmk") {
      ev.onStatus(
        `The keyboard did not restart by itself. ${MANUAL_UPDATE_MODE_INSTRUCTIONS}`,
      )
    } else if (current !== "bootloader") {
      ev.onStatus(MANUAL_UPDATE_MODE_INSTRUCTIONS)
    }
    device = await waitForBootloader(120000, isCancelled)
  }
  if (!device) {
    throw new Error(
      isCancelled() ? "Cancelled." : "The keyboard did not enter update mode.",
    )
  }

  trace(
    `update interface ${describe(device)}; all MadLions interfaces: ${(
      await madlionsDevices()
    )
      .map(describe)
      .join(" | ")}`,
  )
  ev.onStatus(`Installing ${image.name}. Do not unplug the keyboard.`)
  await flashFirmware(device, image, ev.onProgress)

  // Only a keyboard that comes back on the new firmware counts as success
  ev.onStatus("Waiting for the keyboard to restart...")
  const outcome = await waitForRestart(
    image.kind === "custom"
      ? CUSTOM_PID
      : image.kind === "qmk"
        ? QMK_PID
        : STOCK_PID,
    15000,
  )
  if (outcome === "came-back") {
    throw new Error(
      "The new firmware started but did not connect over USB, so the keyboard went back to update mode by itself.",
    )
  }
  if (outcome === "still-updating") {
    throw new Error(
      "The keyboard did not restart after the update, so the new firmware was not applied.",
    )
  }
  if (outcome === "missing") {
    throw new Error(
      "The keyboard did not come back after the update. Unplug it and plug it back in.",
    )
  }
}
