"""Watch the keyboard's USB identity while flashing / testing (read-only).

Polls HID devices every 50 ms and logs, with timestamps, whenever the set of
MadLions (VID 0x373B) devices changes:

    1058  stock firmware          2024  vendor bootloader (update mode)
    1F58  this custom firmware

When the custom firmware appears it is asked (over its libhmk raw-HID config
interface, read-only commands only) for its firmware version and the live
per-key sensor values, so key wiring can be checked while you press keys.

Usage: python usb_watch.py [seconds] [log file]
"""
from __future__ import annotations

import struct
import sys
import time

import hid

VID = 0x373B
NAMES = {0x1058: 'stock firmware', 0x2024: 'UPDATE MODE (bootloader)', 0x1F58: 'CUSTOM FIRMWARE'}
RAW_USAGE_PAGE = 0xFFAB
CMD_FIRMWARE_VERSION, CMD_ANALOG_INFO = 0, 5
NUM_KEYS = 68
LABELS = ('Esc 1 2 3 4 5 6 7 8 9 0 - = Bksp Ins Tab Q W E R T Y U I O P [ ] \\ Del '
          'Caps A S D F G H J K L ; \' Enter PgUp LShift Z X C V B N M , . / RShift Up PgDn '
          'LCtrl Win LAlt Space RAlt Fn RCtrl Left Down Right').split()


def snapshot():
    found = {}
    for d in hid.enumerate(VID, 0):
        found.setdefault(d['product_id'], []).append(d)
    return found


def cfw_query(devs, log):
    raw = [d for d in devs if d.get('usage_page') == RAW_USAGE_PAGE]
    if not raw:
        log('  custom firmware has no config interface visible yet')
        return None
    h = hid.device()
    h.open_path(raw[0]['path'])

    def cmd(cid, payload=b''):
        h.write(b'\x00' + bytes([cid]) + payload + bytes(63 - len(payload)))
        r = h.read(64, timeout_ms=500)
        return bytes(r) if r else None

    r = cmd(CMD_FIRMWARE_VERSION)
    log(f'  config interface OK, firmware version reply: {r[:4].hex(" ") if r else "none"}')
    return h, cmd


def analog_sweep(cmd):
    vals = []
    off = 0
    while off < NUM_KEYS:
        r = cmd(CMD_ANALOG_INFO, bytes([off]))
        if not r or r[0] != CMD_ANALOG_INFO:
            return None
        body = r[1:]
        n = min(21, NUM_KEYS - off)  # 3 bytes per key: u16 adc, u8 distance
        for i in range(n):
            adc, dist = struct.unpack_from('<HB', body, i * 3)
            vals.append((adc, dist))
        off += n
    return vals


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 300
    logf = open(sys.argv[2], 'a', encoding='utf-8') if len(sys.argv) > 2 else None
    t0 = time.time()

    def log(msg):
        line = f'[{time.time() - t0:7.2f}s {time.strftime("%H:%M:%S")}] {msg}'
        print(line, flush=True)
        if logf:
            logf.write(line + '\n')
            logf.flush()

    prev = None
    session = None
    last_sweep = 0
    pressed_seen = set()
    log('watching MadLions USB devices (read-only)...')
    while time.time() - t0 < seconds:
        snap = snapshot()
        cur = tuple(sorted(snap))
        if cur != prev:
            desc = ', '.join(f'{p:04X} {NAMES.get(p, "?")}' for p in cur) or 'nothing connected'
            log(f'now: {desc}')
            if 0x1F58 in snap and (prev is None or 0x1F58 not in prev):
                try:
                    session = cfw_query(snap[0x1F58], log)
                except Exception as exc:  # noqa: BLE001
                    log(f'  could not open config interface: {exc}')
                    session = None
            if 0x1F58 not in snap:
                session = None
            prev = cur
        if session and time.time() - last_sweep > 0.25:
            last_sweep = time.time()
            try:
                vals = analog_sweep(session[1])
            except Exception as exc:  # noqa: BLE001
                log(f'  analog read failed: {exc}')
                session = None
                vals = None
            if vals:
                down = {i for i, (_, d) in enumerate(vals) if d > 40}
                for i in sorted(down - pressed_seen):
                    log(f'  key {i:2} ({LABELS[i] if i < len(LABELS) else "?"}) moving: '
                        f'adc {vals[i][0]}, travel {vals[i][1]}/255')
                pressed_seen |= down
        time.sleep(0.05)
    log('done')


if __name__ == '__main__':
    main()
