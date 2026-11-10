"""Check each Key Scan Speed on a real MAD68 HE running custom firmware v7.3+.

Talks to the keyboard's libhmk raw-HID config interface (the same one MAD68
Studio uses), so close MAD68 Studio first. For each scan speed it:

  1. sets the speed (hands off: the keyboard re-reads every key's rest value),
  2. reads the scan rate the keyboard measures itself,
  3. idle phase: samples all 68 keys with nobody touching them and records
     noise, the deepest reading and any reading past the key's actuation point
     (that would be a false press),
  4. press phase (skipped with --idle-only): you press Q W E R T Y U I O P one
     at a time, all the way down. Records each key's bottom-out reading, which
     shrinks if the sensors haven't settled when they are read, and how far the
     other keys appear to move while one is held (crosstalk).

Speed 0 (Relaxed, v7.2's timing) is the reference the others are compared to.
The speed you had before is put back at the end. Keystrokes typed into this
window during the test are thrown away when it ends.

With firmware older than v7.3 (no scan speed command) only one idle phase at
the current timing is run, as a reference.

Usage: python scan_check.py [--idle-only] [--speeds 0,1,2,3]
                            [--idle-seconds 4] [--press-seconds 12]
                            [--log scan_check.json]
"""
from __future__ import annotations

import argparse
import gzip
import json
import statistics
import struct
import sys
import time

import hid

VID = 0x373B
PID_CFW = 0x1F58
RAW_USAGE_PAGE = 0xFFAB

CMD_ANALOG_INFO = 5
CMD_GET_PROFILE = 8
CMD_GET_ACTUATION_MAP = 130
CMD_GET_FIRMWARE_INFO = 0x67
CMD_GET_SCAN = 0x68
CMD_SET_SCAN = 0x69
CMD_UNKNOWN = 0xFF

NUM_KEYS = 68
REST = 1024  # ADC_REST_REFERENCE: what the firmware reports for a key at rest
SPEED_NAMES = ['Relaxed', 'Standard', 'Fast', 'Fastest']
LABELS = ('Esc 1 2 3 4 5 6 7 8 9 0 - = Bksp Ins Tab Q W E R T Y U I O P [ ] \\ Del '
          'Caps A S D F G H J K L ; \' Enter PgUp LShift Z X C V B N M , . / RShift Up PgDn '
          'LCtrl Win LAlt Space RAlt Fn RCtrl Left Down Right').split()
# ADC_COLUMN_SCAN_MATRIX from keyboards/mad68he/config.h: key index + 1 at (row, col)
MATRIX = [
    [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
    [16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30],
    [31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 0, 43, 44],
    [45, 0, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58],
    [59, 60, 61, 0, 0, 0, 62, 0, 0, 63, 64, 65, 66, 67, 68],
]
POS = {}
for _r, _row in enumerate(MATRIX):
    for _c, _k in enumerate(_row):
        if _k:
            POS[_k - 1] = (_r, _c)
AT = {v: k for k, v in POS.items()}
PRESS_KEYS = [LABELS.index(c) for c in 'Q W E R T Y U I O P'.split()]

HELD = 200      # distance (0-255 = 0-4 mm) that counts as held down
OTHER_UP = 100  # every other key must be below this for a clean crosstalk sample


def mm(d):
    return d * 4.0 / 255


class Keyboard:
    def __init__(self):
        devs = [d for d in hid.enumerate(VID, 0) if d.get('usage_page') == RAW_USAGE_PAGE]
        if not devs:
            pids = sorted({hex(d['product_id']) for d in hid.enumerate(VID, 0)})
            raise SystemExit('No MAD68 custom-firmware config interface found '
                             f'(MadLions devices present: {pids or "none"}).')
        self.h = hid.device()
        self.h.open_path(devs[0]['path'])
        self.flush()

    def flush(self):
        self.h.set_nonblocking(1)
        while self.h.read(64):
            pass
        self.h.set_nonblocking(0)

    def cmd(self, cid, payload=b'', timeout_ms=500):
        payload = bytes(payload)
        self.h.write(b'\x00' + bytes([cid]) + payload + bytes(63 - len(payload)))
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            left = int((deadline - time.monotonic()) * 1000)
            if left <= 0:
                raise TimeoutError(f'no reply to command 0x{cid:02x}')
            r = self.h.read(64, timeout_ms=left)
            if r and r[0] in (cid, CMD_UNKNOWN):
                return bytes(r)

    def sweep(self):
        adc, dist = [], []
        off = 0
        while off < NUM_KEYS:
            r = self.cmd(CMD_ANALOG_INFO, [off])
            if r[0] != CMD_ANALOG_INFO:
                raise RuntimeError('analog info refused')
            n = min(21, NUM_KEYS - off)
            for i in range(n):
                a, d = struct.unpack_from('<HB', r, 1 + i * 3)
                adc.append(a)
                dist.append(d)
            off += n
        return adc, dist

    def firmware(self):
        r = self.cmd(CMD_GET_FIRMWARE_INFO)
        if r[0] != CMD_GET_FIRMWARE_INFO:
            return None
        return f'{r[1]}.{r[2]}.{r[3]}', r[8:32].split(b'\0')[0].decode('ascii', 'replace')

    def actuation_points(self):
        prof = self.cmd(CMD_GET_PROFILE)[1]
        pts = []
        off = 0
        while off < NUM_KEYS:
            r = self.cmd(CMD_GET_ACTUATION_MAP, [prof, off])
            n = min(15, NUM_KEYS - off)
            pts += [r[1 + i * 4] for i in range(n)]
            off += n
        return prof, pts

    def scan(self, speed=None):
        r = self.cmd(CMD_GET_SCAN) if speed is None else self.cmd(CMD_SET_SCAN, [speed], 1500)
        if r[0] == CMD_UNKNOWN:
            return None
        count = r[3]
        return {'speed': r[2], 'rate': r[4] | r[5] << 8, 'capturing': bool(r[6]),
                'settle_us': list(r[7:7 + count])}


def countdown(msg, seconds):
    print(msg, end='', flush=True)
    for s in range(seconds, 0, -1):
        print(f' {s}', end='', flush=True)
        time.sleep(1)
    print(flush=True)


def collect(kb, seconds, label, raw):
    out = []
    t0 = time.monotonic()
    last = -1
    while (t := time.monotonic() - t0) < seconds:
        left = int(seconds - t)
        if left != last:
            print(f'\r    {label}: {left:2d} s left ', end='', flush=True)
            last = left
        adc, dist = kb.sweep()
        out.append((t, adc, dist))
        raw.append({'phase': label, 't': round(t, 4), 'adc': adc, 'dist': dist})
    print(f'\r    {label}: done, {len(out)} readings of all keys ({len(out) / seconds:.0f}/s)   ',
          flush=True)
    return out


def idle_stats(samples, act):
    per_key = []
    false_presses = []
    for k in range(NUM_KEYS):
        a = [s[1][k] for s in samples]
        d = [s[2][k] for s in samples]
        per_key.append({'p2p': max(a) - min(a), 'sd': statistics.pstdev(a),
                        'offset': statistics.fmean(a) - REST, 'max_dist': max(d)})
        if act and max(d) >= act[k]:
            false_presses.append(LABELS[k])
    worst = max(range(NUM_KEYS), key=lambda k: per_key[k]['p2p'])
    deepest = max(range(NUM_KEYS), key=lambda k: per_key[k]['max_dist'])
    return {
        'median_p2p': statistics.median(p['p2p'] for p in per_key),
        'worst_p2p': per_key[worst]['p2p'], 'worst_p2p_key': LABELS[worst],
        'max_dist': per_key[deepest]['max_dist'], 'max_dist_key': LABELS[deepest],
        'false_presses': false_presses,
        'keys_moving': [LABELS[k] for k in range(NUM_KEYS) if per_key[k]['max_dist'] > 0],
        'per_key': per_key,
    }


def press_stats(samples):
    peak = {}
    held = {}  # held key -> list of distance vectors
    for _, adc, dist in samples:
        for k in PRESS_KEYS:
            if dist[k] >= HELD:
                peak[k] = max(peak.get(k, 0), adc[k] - REST)
        top = max(range(NUM_KEYS), key=lambda k: dist[k])
        if dist[top] >= HELD and all(dist[j] < OTHER_UP for j in range(NUM_KEYS) if j != top):
            held.setdefault(top, []).append(dist)
    cross = []
    for k, rows in held.items():
        if len(rows) < 5:
            continue
        for j in range(NUM_KEYS):
            if j == k:
                continue
            med = statistics.median(r[j] for r in rows)
            if med > 0:
                cross.append((med, LABELS[k], LABELS[j]))
    cross.sort(reverse=True)
    nxt = []  # same row, next scanned column: the neighbor most exposed to a short settle
    for k, rows in held.items():
        r, c = POS[k]
        j = AT.get((r, c + 1))
        if j is not None and len(rows) >= 5:
            nxt.append((statistics.median(x[j] for x in rows), LABELS[k], LABELS[j]))
    nxt.sort(reverse=True)
    return {'peak_delta': {LABELS[k]: v for k, v in peak.items()},
            'held_keys': sorted(LABELS[k] for k in held),
            'worst_cross': cross[0] if cross else (0, None, None),
            'worst_next_col': nxt[0] if nxt else (0, None, None)}


def wait_ready(kb):
    t0 = time.monotonic()
    info = kb.scan()
    while (info['capturing'] or info['rate'] == 0) and time.monotonic() - t0 < 5:
        time.sleep(0.05)
        info = kb.scan()
    return info


def studio_running():
    if sys.platform != 'win32':
        return False
    import subprocess
    out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq MAD68*', '/NH'],
                         capture_output=True, text=True).stdout
    return 'MAD68' in out


def drain_console():
    try:
        import msvcrt
        while msvcrt.kbhit():
            msvcrt.getwch()
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--idle-only', action='store_true')
    ap.add_argument('--speeds', default='0,1,2,3')
    ap.add_argument('--idle-seconds', type=float, default=4)
    ap.add_argument('--press-seconds', type=float, default=12)
    ap.add_argument('--log', default='scan_check.json')
    args = ap.parse_args()
    speeds = [int(s) for s in args.speeds.split(',')]

    if studio_running():
        print('Close MAD68 Studio to start: it uses the same config interface, and its '
              'replies would mix with this test\'s readings. Waiting...', flush=True)
        while studio_running():
            time.sleep(1)
        time.sleep(1)
    kb = Keyboard()
    fw = kb.firmware()
    print(f'Firmware: {fw[0]} ({fw[1]})' if fw else 'Firmware: (no firmware info)')
    prof, act = kb.actuation_points()
    print(f'Profile {prof + 1}, actuation points {mm(min(act)):.2f}-{mm(max(act)):.2f} mm')
    raw = []
    report = {'firmware': fw, 'profile': prof, 'actuation': act, 'speeds': []}

    original = kb.scan()
    try:
        if original is None:
            print('\nThis firmware has no scan speed setting (older than v7.3).')
            countdown('Hands off the keyboard for a reference reading:', 3)
            s = collect(kb, args.idle_seconds, 'idle', raw)
            st = idle_stats(s, act)
            report['speeds'].append({'speed': None, 'idle': st})
            print_idle(st)
            return
        print(f"Current speed: {SPEED_NAMES[original['speed']]}, "
              f"{original['rate']} scans/s measured")
        if not args.idle_only and sys.stdin.isatty():
            print('\nFor each speed: hands off while it says so, then press Q W E R T Y U I O P')
            print('one at a time, all the way down, when it asks. Keep this window focused.')
            input('Press Enter to start... ')

        for sp in speeds:
            print(f'\n=== {SPEED_NAMES[sp]} ({original["settle_us"][sp]} us wait per column) ===')
            countdown('  Hands off the keyboard:', 3)
            kb.scan(sp)
            info = wait_ready(kb)
            print(f"  Measured {info['rate']} full scans per second")
            entry = {'speed': sp, 'settle_us': info['settle_us'][sp], 'rate': info['rate']}
            s = collect(kb, args.idle_seconds, f'idle {SPEED_NAMES[sp]}', raw)
            entry['idle'] = idle_stats(s, act)
            print_idle(entry['idle'])
            if not args.idle_only:
                print('\a')
                print(f'  >>>>> {SPEED_NAMES[sp].upper()}: PRESS NOW <<<<<')
                print('  Press Q W E R T Y U I O P one at a time, all the way down,')
                print('  holding each about half a second. Keep going until the timer ends.')
                countdown('  Starting in', 2)
                s = collect(kb, args.press_seconds, f'press {SPEED_NAMES[sp]}', raw)
                entry['press'] = press_stats(s)
                print_press(entry['press'])
            report['speeds'].append(entry)
        summarize(report)
    except OSError:
        report['error'] = 'keyboard disconnected'
        print('\n\nThe keyboard disconnected (unplugged or restarted?). Stopping; '
              'run the test again once it is back.')
        original = None  # nothing to talk to any more
    finally:
        with open(args.log, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=1)
        with gzip.open(args.log + '.raw.jsonl.gz', 'wt', encoding='utf-8') as f:
            for r in raw:
                f.write(json.dumps(r) + '\n')
        print(f'Saved {args.log}')
        if original is not None:
            try:
                countdown(f"\nPutting back {SPEED_NAMES[original['speed']]}; hands off:", 2)
                kb.scan(original['speed'])
                info = wait_ready(kb)
                print(f"Back on {SPEED_NAMES[info['speed']]}, {info['rate']} scans/s")
            except OSError:
                print('The keyboard disconnected before the speed could be put back; '
                      'set it again in MAD68 Studio.')
        drain_console()


def print_idle(st):
    print(f"    noise: median key {st['median_p2p']} ADC counts peak-to-peak, "
          f"worst {st['worst_p2p']} ({st['worst_p2p_key']})")
    print(f"    deepest reading with hands off: {mm(st['max_dist']):.2f} mm ({st['max_dist_key']})")
    print('    false presses: ' + (', '.join(st['false_presses']) if st['false_presses'] else 'none'))


def print_press(ps):
    keys = ', '.join(ps['held_keys']) or 'none'
    print(f'    keys seen held down: {keys}')
    c = ps['worst_cross']
    if c[1]:
        print(f'    worst crosstalk: {c[2]} reads {mm(c[0]):.2f} mm while {c[1]} is held')
    else:
        print('    crosstalk: none seen')


def summarize(report):
    rows = report['speeds']
    ref = next((r for r in rows if r['speed'] == 0), None)
    print('\n================ Summary ================')
    print(f"{'Speed':9} {'wait':>5} {'scans/s':>8} {'noise':>6} {'idle max':>9} "
          f"{'false':>6} {'bottom-out':>11} {'crosstalk':>10}  verdict")
    for r in rows:
        st = r['idle']
        amp_txt, cross_txt, problems = '-', '-', []
        if st['false_presses']:
            problems.append('false presses')
        if ref and r is not ref and st['max_dist'] > max(6, 2 * ref['idle']['max_dist']):
            problems.append('noisier at rest')
        if 'press' in r:
            ps = r['press']
            if ref and 'press' in ref and r is not ref:
                ratios = [ps['peak_delta'][k] / ref['press']['peak_delta'][k]
                          for k in ps['peak_delta'] if ref['press']['peak_delta'].get(k)]
                if ratios:
                    med, worst = statistics.median(ratios), min(ratios)
                    amp_txt = f'{med * 100:.0f}%/{worst * 100:.0f}%'
                    if med < 0.97 or worst < 0.93:
                        problems.append('weaker readings (not settled)')
            elif r is ref:
                amp_txt = 'reference'
            cross = ps['worst_cross'][0]
            cross_txt = f'{mm(cross):.2f} mm' if ps['held_keys'] else 'no presses'
            ref_cross = ref['press']['worst_cross'][0] if ref and 'press' in ref else 0
            if cross > max(6, ref_cross + 3):
                problems.append('crosstalk')
            if not ps['held_keys']:
                problems.append('no presses seen, press part not checked')
        print(f"{SPEED_NAMES[r['speed']]:9} {r['settle_us']:>4}u {r['rate']:>8} "
              f"{st['worst_p2p']:>6} {mm(st['max_dist']):>7.2f}mm {len(st['false_presses']):>6} "
              f"{amp_txt:>11} {cross_txt:>10}  {', '.join(problems) or 'looks fine'}")
        r['problems'] = problems
    print('noise = worst key, ADC counts peak-to-peak at rest; idle max = deepest reading at rest;')
    print('bottom-out = full-press reading vs Relaxed (median/worst key); crosstalk = worst other key while one is held')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nStopped.')
        drain_console()
        sys.exit(1)
