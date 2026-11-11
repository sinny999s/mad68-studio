"""Check custom firmware v7.4's scan speeds and smoothing levels on a real
MAD68 HE, and measure how far the smoothing puts the keys behind your finger.

Close MAD68 Studio first (it uses the same config interface). Two parts:

  1. Hands off (about a minute and a half). Every combination of the four
     fastest scan speeds and the four smoothing levels is used for real for a
     few seconds. For each: the scan rate and main loop rate the keyboard
     measures, faults, how much the filtered readings move at rest, the
     deepest any key reads with nobody touching it, and whether any key
     would have pressed at your actuation points.

  2. Tapping (under a minute). At a few settings you tap keys for a few
     seconds, and the keyboard times each press itself: from the moment a
     key's reading passes half travel to the moment its filtered value does.
     That is the delay the smoothing adds, measured on your own presses.

Your scan speed and smoothing are put back at the end. Keystrokes typed into
this window during the test are thrown away when it ends.

Usage: python speed_test.py [--seconds 5] [--tap-seconds 8] [--no-tap]
                            [--log speed_test.json]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time

from scan_check import CMD_UNKNOWN, LABELS, NUM_KEYS, Keyboard, countdown, drain_console, mm, studio_running

CMD_GET_SCAN = 0x68
CMD_SET_SCAN = 0x69
CMD_SET_SCAN_FILTER = 0x6C
CMD_SCAN_STATUS = 0x6B
IN_EFFECT, SWITCHING, REFUSED = 0, 1, 2
SPEEDS = ['Relaxed', 'Standard', 'Fast', 'Fastest', 'Turbo', 'Extreme', 'Max']
LEVELS = ['Classic', 'Smooth', 'Balanced', 'Fast']
SENSORS = 75


def get_scan(kb, cid=CMD_GET_SCAN, payload=()):
    r = kb.cmd(cid, payload, 1500)
    if r[0] == CMD_UNKNOWN:
        return None
    n = r[3]
    out = {'protocol': r[1], 'speed': r[2], 'count': n, 'rate': r[4] | r[5] << 8, 'busy': bool(r[6]),
           'settle_us': list(r[7:7 + n])}
    if r[1] >= 2:
        out.update(timing=list(r[7 + n:7 + 2 * n]), state=r[7 + 2 * n], level=r[8 + 2 * n], levels=r[9 + 2 * n],
                   loops=r[10 + 2 * n] | r[11 + 2 * n] << 8)
    return out


def status(kb, request=0):
    r = kb.cmd(CMD_SCAN_STATUS, [request])
    w = lambda i: r[i] | r[i + 1] << 8            # noqa: E731
    return {'faults': w(23), 'sync': w(25), 'overruns': w(27), 'stalls': w(29), 'rate': w(31),
            'loops': int.from_bytes(r[33:36], 'little'), 'state': r[38], 'level': r[39],
            'lag_on': r[40], 'lag_n': w(41), 'lag_mean': w(43), 'lag_min': w(45), 'lag_max': w(47)}


def set_speed(kb, speed):
    """Set a scan speed and wait until it is in effect or refused."""
    g = get_scan(kb, CMD_SET_SCAN, [speed])
    if g is None:
        raise RuntimeError(f'speed {speed} not accepted')
    t0 = time.monotonic()
    while (g['busy'] or g['rate'] == 0) and time.monotonic() - t0 < 5:
        time.sleep(0.05)
        g = get_scan(kb)
    return g


def set_level(kb, level):
    g = get_scan(kb, CMD_SET_SCAN_FILTER, [level])
    if g is None:
        raise RuntimeError(f'smoothing level {level} not accepted')
    time.sleep(0.3)
    return g


def expected_delay_us(level, rate, loops):
    """What the filter's delay should be: classic trails by about 15 main loop
    passes; the others by one reading for the median and then 7, 3 or 1."""
    if level == 0:
        return 15e6 / loops if loops else 0
    return (1 + (1 << (4 - level)) - 1) * 1e6 / rate if rate else 0


def rest_run(kb, seconds, act, label):
    lo, hi = [4095] * NUM_KEYS, [0] * NUM_KEYS
    deepest, deep_key, false_presses, sweeps = 0, 0, set(), 0
    faults0 = status(kb)['faults']
    t0 = time.monotonic()
    last = -1
    while (t := time.monotonic() - t0) < seconds:
        if int(t) != last:
            last = int(t)
            print(f'\r  {label}: {seconds - t:3.0f} s left   ', end='', flush=True)
        adc, dist = kb.sweep()
        sweeps += 1
        for k in range(NUM_KEYS):
            if adc[k] < lo[k]:
                lo[k] = adc[k]
            if adc[k] > hi[k]:
                hi[k] = adc[k]
            if dist[k] > deepest:
                deepest, deep_key = dist[k], k
            if dist[k] >= act[k]:
                false_presses.add(LABELS[k])
    st = status(kb)
    g = get_scan(kb)
    p2p = [h - l for l, h in zip(lo, hi)]
    worst = max(range(NUM_KEYS), key=lambda k: p2p[k])
    print('\r' + ' ' * 60 + '\r', end='')
    return {'rate': g['rate'], 'loops': st['loops'], 'faults': st['faults'] - faults0, 'state': g['state'],
            'moved_median': statistics.median(p2p), 'moved_worst': p2p[worst], 'moved_worst_key': LABELS[worst],
            'deepest': deepest, 'deepest_key': LABELS[deep_key], 'false_presses': sorted(false_presses),
            'sweeps': sweeps}


def tap_run(kb, seconds, label):
    status(kb, 2)                                  # clear the statistics and start measuring
    print(f'\a  >>>>> TAP NOW ({label}): tap any keys, all the way down, about three a second <<<<<')
    t0 = time.monotonic()
    last = -1
    while (t := time.monotonic() - t0) < seconds:
        if int(t) != last:
            last = int(t)
            st = status(kb)
            print(f"\r    {seconds - t:3.0f} s left, {st['lag_n']} presses timed   ", end='', flush=True)
        time.sleep(0.05)
    st = status(kb)
    status(kb, 3)
    print(f"\r    done: {st['lag_n']} presses timed" + ' ' * 20)
    return {'presses': st['lag_n'], 'mean_us': st['lag_mean'], 'min_us': st['lag_min'], 'max_us': st['lag_max'],
            'rate': st['rate'], 'loops': st['loops']}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--seconds', type=float, default=5, help='hands-off seconds per combination')
    ap.add_argument('--tap-seconds', type=float, default=8)
    ap.add_argument('--no-tap', action='store_true')
    ap.add_argument('--speeds', default='3,4,5,6')
    ap.add_argument('--log', default='speed_test.json')
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
    start = get_scan(kb)
    if start is None or start['protocol'] < 2:
        raise SystemExit('This needs custom firmware v7.4 or newer.')
    prof, act = kb.actuation_points()
    state_txt = {IN_EFFECT: '', SWITCHING: ' (switching)', REFUSED: ' (NOT in effect: running Fastest instead)'}
    print(f"Now: {SPEEDS[start['speed']]}{state_txt[start['state']]}, smoothing {LEVELS[start['level']]}, "
          f"{start['rate']} scans/s, main loop {start['loops']}/s")
    print(f'Profile {prof + 1}, actuation points {mm(min(act)):.2f}-{mm(max(act)):.2f} mm')
    report = {'firmware': fw, 'start': start, 'actuation': act, 'rest': [], 'tap': []}
    total = len(speeds) * 4 * (args.seconds + 1)
    if sys.stdin.isatty():
        print(f'\nPart 1: hands off the keyboard for about {total:.0f} seconds.')
        if not args.no_tap:
            print(f'Part 2: tap keys when it says so, {args.tap_seconds:g} seconds at a time, five times.')
        print('Keep this window focused, so that what you type lands here.')
        input('Press Enter to start... ')
    try:
        countdown('\nPart 1. Hands off the keyboard:', 3)
        print(f"{'speed':8} {'smoothing':9} {'scans/s':>8} {'total':>6} {'loop/s':>7} {'faults':>6} "
              f"{'at rest moves':>14} {'deepest':>12} {'est. delay':>11}  false presses")
        refused = set()
        for sp in speeds:
            g = set_speed(kb, sp)
            if g['state'] != IN_EFFECT:
                refused.add(sp)
                print(f"{SPEEDS[sp]:8} NOT in effect on this keyboard (it runs Fastest instead); skipped")
                report['rest'].append({'speed': sp, 'refused': True})
                continue
            for level in range(4):
                set_level(kb, level)
                r = rest_run(kb, args.seconds, act, f'{SPEEDS[sp]} / {LEVELS[level]}')
                r.update(speed=sp, level=level, delay_us=expected_delay_us(level, r['rate'], r['loops']))
                report['rest'].append(r)
                print(f"{SPEEDS[sp]:8} {LEVELS[level]:9} {r['rate']:>8} {r['rate'] * SENSORS / 1000:>5.0f}K "
                      f"{r['loops']:>7} {r['faults']:>6} {r['moved_median']:>6.0f}/{r['moved_worst']:<3.0f}"
                      f"{r['moved_worst_key']:>5} {mm(r['deepest']):>6.2f}mm {r['deepest_key']:>4} "
                      f"{r['delay_us'] / 1000:>8.2f} ms  {', '.join(r['false_presses']) or 'none'}"
                      + ('' if r['state'] == IN_EFFECT else '  SPEED DROPPED'))
        print('at rest moves = highest minus lowest filtered reading of a key, median key / worst key (counts);')
        print('deepest = furthest any key read as pressed with hands off; est. delay = what the smoothing should add')

        if not args.no_tap:
            best = max((s for s in speeds if s not in refused), default=3)
            rounds = [(3, 0), (best, 0), (best, 1), (best, 2), (best, 3)]
            print(f'\a\nPart 2. Five rounds of tapping, {args.tap_seconds:g} seconds each. Any keys; normal full presses.')
            for sp, level in rounds:
                set_speed(kb, sp)
                set_level(kb, level)
                time.sleep(0.5)
                r = tap_run(kb, args.tap_seconds, f'{SPEEDS[sp]} / {LEVELS[level]}')
                r.update(speed=sp, level=level, expected_us=expected_delay_us(level, r['rate'], r['loops']))
                report['tap'].append(r)
            print('\a\n  >>>>> DONE <<<<<\n')
            print(f"{'speed':8} {'smoothing':9} {'presses':>8} {'measured delay':>15} {'shortest':>9} {'longest':>8} {'expected':>9}")
            for r in report['tap']:
                if r['presses']:
                    print(f"{SPEEDS[r['speed']]:8} {LEVELS[r['level']]:9} {r['presses']:>8} {r['mean_us'] / 1000:>12.2f} ms "
                          f"{r['min_us'] / 1000:>6.2f} ms {r['max_us'] / 1000:>5.2f} ms {r['expected_us'] / 1000:>6.2f} ms")
                else:
                    print(f"{SPEEDS[r['speed']]:8} {LEVELS[r['level']]:9} {'none':>8}  (no full presses seen)")
            print('measured delay = time from a key\'s reading passing half travel to its filtered value doing so')
    except OSError:
        report['error'] = 'keyboard disconnected'
        print('\n\nThe keyboard disconnected (unplugged or restarted?). Run the test again once it is back.')
        start = None
    finally:
        with open(args.log, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=1)
        print(f'Saved {args.log}')
        if start is not None:
            try:
                status(kb, 3)
                set_level(kb, start['level'])
                g = set_speed(kb, start['speed'])
                print(f"Back on {SPEEDS[g['speed']]}, smoothing {LEVELS[g['level']]}, {g['rate']} scans/s")
            except OSError:
                print('The keyboard disconnected before the settings could be put back; set them in MAD68 Studio.')
        drain_console()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nStopped.')
        drain_console()
        sys.exit(1)
