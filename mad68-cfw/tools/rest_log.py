"""Log how a MAD68 HE's keys read at rest over time: stray readings and drift.

Needs custom firmware v7.4 (the probe commands) and nobody touching the
keyboard. Close MAD68 Studio first. It runs probes back to back at one timing
(256 full scans each, with the keys frozen meanwhile) and keeps, per key:

  - stray readings: probes in which the key's highest or lowest raw reading
    was more than --stray counts from its average;
  - drift: the key's average raw reading, minute by minute.

Run it once per condition and compare. --name labels the run; every run is
appended to the same log.

  python rest_log.py --name live --minutes 9
  python rest_log.py --name one-adc --timing 0,8,2 --minutes 4
  python rest_log.py --name lights-off --lights off --minutes 5

--timing is mode,settle us,sample code (default: the timing in use).
--lights off turns the lighting off for the run and puts it back afterwards
(the LED data line keeps running; the LEDs just draw no current).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time

import scan_probe
from scan_check import LABELS, NUM_KEYS, POS, Keyboard, studio_running

CMD_GET_RGB = 0x60
CMD_SET_RGB = 0x61


def lights(kb, on=None):
    """The lighting settings as SET_RGB takes them; with `on`, switch the
    lighting on or off first (everything else unchanged)."""
    r = kb.cmd(CMD_GET_RGB)
    settings = [r[1], r[2], r[3], r[4], r[5], r[6], 2, r[11], r[12], r[13], r[14], r[15], r[16], r[17]]
    if on is not None:
        new = list(settings)
        new[0] = (new[0] | 1) if on else (new[0] & ~1)
        kb.cmd(CMD_SET_RGB, new, 1500)
    return settings


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--name', required=True)
    ap.add_argument('--timing', help='mode,settle us,sample code (default: the timing in use)')
    ap.add_argument('--minutes', type=float, default=5)
    ap.add_argument('--scans', type=int, default=8, help='log2 of the scans per probe')
    ap.add_argument('--stray', type=float, default=20, help='counts from the average that make a reading stray')
    ap.add_argument('--lights', choices=['off'], help='run with the lighting switched off')
    ap.add_argument('--log', default='rest_log.jsonl')
    args = ap.parse_args()

    if studio_running():
        raise SystemExit('MAD68 Studio is running: close it first.')
    kb = Keyboard()
    st = scan_probe.status(kb)
    if st is None:
        raise SystemExit('This firmware has no probe commands (it needs v7.4).')
    tpu = st['tpu']
    live = (st['mode'], st['ticks'] / tpu, st['sample'])
    timing = scan_probe.parse_timing(args.timing) if args.timing else live
    print(f"{args.name}: {scan_probe.name(timing)} for {args.minutes:g} min, "
          f"{1 << args.scans} scans per probe (keys read live with {scan_probe.name(live)})", flush=True)

    restore_lights = None
    if args.lights == 'off':
        restore_lights = lights(kb, on=False)
        time.sleep(1.0)

    run = {'name': args.name, 'timing': list(timing), 'live': list(live), 'started': time.time(),
           'scans_per_probe': 1 << args.scans, 'stray_counts': args.stray, 'lights': args.lights or 'as set',
           'minutes': [], 'events': [], 'probes': 0, 'failed': 0, 'faults': 0}
    up = [0] * NUM_KEYS          # probes with a reading stray above the average
    down = [0] * NUM_KEYS
    spread_sum = [0.0] * NUM_KEYS
    first_mean = None
    msum = [0.0] * NUM_KEYS
    mcount = 0
    rates = []
    t0 = time.monotonic()
    next_minute = 60.0
    try:
        while (t := time.monotonic() - t0) < args.minutes * 60:
            p = scan_probe.probe(kb, timing, args.scans, tpu)
            if not scan_probe.ok(p):
                run['failed'] += 1
                if run['failed'] > 20:
                    print('  too many failed probes: stopping', flush=True)
                    break
                continue
            run['probes'] += 1
            run['faults'] += p['faults']
            rates.append(p['rate'])
            if first_mean is None:
                first_mean = list(p['mean'])
            for k in range(NUM_KEYS):
                m, lo, hi = p['mean'][k], p['low'][k], p['high'][k]
                msum[k] += m
                spread_sum[k] += hi - lo
                if hi - m > args.stray:
                    up[k] += 1
                    if len(run['events']) < 20000:
                        run['events'].append([round(t, 3), k, round(hi - m, 1)])
                if m - lo > args.stray:
                    down[k] += 1
                    if len(run['events']) < 20000:
                        run['events'].append([round(t, 3), k, round(lo - m, 1)])
            mcount += 1
            if t >= next_minute:
                run['minutes'].append({'t': round(t, 1), 'mean': [round(v / mcount, 2) for v in msum]})
                msum, mcount = [0.0] * NUM_KEYS, 0
                next_minute += 60.0
                print(f"  {t / 60:4.1f} min, {run['probes']} probes, "
                      f"{sum(up) + sum(down)} stray probes so far", flush=True)
    except OSError:
        run['error'] = 'keyboard disconnected'
        print('  the keyboard disconnected', flush=True)
        restore_lights = None
    finally:
        if mcount:
            run['minutes'].append({'t': round(time.monotonic() - t0, 1), 'mean': [round(v / mcount, 2) for v in msum]})
        if restore_lights is not None:
            kb.cmd(CMD_SET_RGB, restore_lights, 1500)
        run['seconds'] = round(time.monotonic() - t0, 1)
        run['up'], run['down'] = up, down
        run['spread'] = [round(v / max(run['probes'], 1), 2) for v in spread_sum]
        run['rate'] = round(statistics.median(rates)) if rates else 0
        run['first_mean'] = first_mean
        with open(args.log, 'a', encoding='utf-8') as f:
            f.write(json.dumps(run) + '\n')

    n = max(run['probes'], 1)
    scans = n * run['scans_per_probe']
    print(f"  {run['probes']} probes ({scans:,} scans at {run['rate']}/s, measuring "
          f"{scans / max(run['rate'], 1) / max(run['seconds'], 1) * 100:.0f}% of the time), "
          f"{run['failed']} failed, {run['faults']} faults")
    print('  stray readings by row (probes with one, per 1,000 probes of a key; above / below the average):')
    for row in range(5):
        ks = [k for k in range(NUM_KEYS) if POS[k][0] == row]
        u = sum(up[k] for k in ks) / (n * len(ks)) * 1000
        d = sum(down[k] for k in ks) / (n * len(ks)) * 1000
        worst = max(ks, key=lambda k: up[k] + down[k])
        print(f"    row {row + 1}: {u:7.2f} / {d:7.2f}   most on {LABELS[worst]} ({up[worst]} + {down[worst]}); "
              f"usual spread {statistics.median(run['spread'][k] for k in ks):.1f} counts")
    if len(run['minutes']) >= 2:
        a, b = run['minutes'][0]['mean'], run['minutes'][-1]['mean']
        print(f"  drift from the first to the last minute ({run['minutes'][-1]['t'] / 60:.0f} min apart), by row "
              '(median / largest, counts):')
        for row in range(5):
            ks = [k for k in range(NUM_KEYS) if POS[k][0] == row]
            d = [b[k] - a[k] for k in ks]
            big = max(ks, key=lambda k: abs(b[k] - a[k]))
            print(f"    row {row + 1}: {statistics.median(d):+6.2f} / {b[big] - a[big]:+6.2f} ({LABELS[big]})")
    print(f'  appended to {args.log}', flush=True)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nStopped.')
        sys.exit(1)
