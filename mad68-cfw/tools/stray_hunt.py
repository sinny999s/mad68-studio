"""Check custom firmware v7.5 on a MAD68 HE, and find out where the stray
readings on its bottom row come from.

Needs custom firmware v7.5 (test protocol 3: the paired read, the stray
reading log, the pin diagnostics) and nobody touching the keyboard. Close MAD68
Studio first. Nothing is saved on the keyboard, and it is put back on its own
scan speed at the end. It takes about eight minutes:

  1. the three fast scan speeds with the bottom row read twice: scan rate,
     main loop rate, faults, stray readings dropped;
  2. how often the bottom row's two readings disagree (a stray reading on one
     of them), with the USB link quiet and with it busy, and whether any
     still reach the keys;
  3. which pins of the chip change level by themselves;
  4. when the stray readings happen within each USB microframe and each
     millisecond tick, against ordinary readings logged for comparison;
  5. whether giving the unused pins next to the bottom row's sensor line a
     pull or analog mode changes how often they happen.

Usage: python stray_hunt.py [--seconds 20] [--log stray_hunt.json] [--skip speeds,pins,timing,overrides]
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import struct
import sys
import time

import scan_probe
from scan_check import CMD_UNKNOWN, LABELS, NUM_KEYS, POS, Keyboard, studio_running

CMD_GET_SCAN = 0x68
CMD_SET_SCAN = 0x69
CMD_STATUS = 0x6B
PAIRED = 7
SENSORS = 75
# The timings of the three fast scan speeds from v7.5: mode, settle in us, sample code
FAST = [('Turbo', (PAIRED, 8, 2)), ('Extreme', (PAIRED, 5, 2)), ('Max', (PAIRED, 5, 1))]
PORTS = 'ABCDE'
SAMPLE_CYCLES = [3, 15, 28, 56, 84, 112, 144, 480]
# Pins this firmware drives itself, which are expected to change level
EXPECTED = {('A', p): 'key column' for p in (8, 9, 10, 11, 12, 15)}
EXPECTED.update({('C', p): 'key column' for p in (10, 11, 12)})
EXPECTED.update({('B', p): 'key column' for p in (3, 4, 5, 6, 7)})
EXPECTED[('D', 2)] = 'key column'
EXPECTED[('C', 9)] = 'LED data'
EXPECTED[('A', 13)] = 'debug port'
EXPECTED[('A', 14)] = 'debug port'
# (port, pin, mode, what it is): the unused pins next to PA7
OVERRIDES = [
    ('A', 6, 4, 'PA6 pull-down'), ('A', 6, 3, 'PA6 pull-up'), ('A', 6, 1, 'PA6 analog'),
    ('A', 5, 4, 'PA5 pull-down'), ('A', 5, 1, 'PA5 analog'),
    ('A', 4, 4, 'PA4 pull-down'), ('A', 4, 1, 'PA4 analog'),
    ('C', 4, 1, 'PC4 analog'), ('C', 5, 1, 'PC5 analog'), ('B', 0, 1, 'PB0 analog'),
]


def status(kb, *request):
    r = kb.cmd(CMD_STATUS, list(request) or [0], 1500)
    if r[0] == CMD_UNKNOWN:
        return None
    st = scan_probe.parse_status(r)
    st['pairs'] = int.from_bytes(r[49:53], 'little')
    return st


def pair_rate(kb, seconds, busy=False):
    """Disagreeing pairs per second over a while; `busy` keeps the USB link
    busy with commands meanwhile."""
    a = status(kb)
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        if busy:
            kb.cmd(CMD_GET_SCAN)
        else:
            time.sleep(0.25)
    b = status(kb)
    dt = time.monotonic() - t0
    n = (b['pairs'] - a['pairs']) & 0xFFFFFFFF
    return {'pairs': n, 'seconds': dt, 'per_s': n / dt, 'scan_rate': b['rate'], 'faults': b['faults'] - a['faults']}


def read_log(kb, seconds):
    """Collect the stray reading log for a while."""
    r = kb.cmd(CMD_STATUS, [4, 0, 0])
    total = r[1] | r[2] << 8                           # low 16 bits of the count
    nxt = total                                        # start from now
    out, lost = [], 0
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        r = kb.cmd(CMD_STATUS, [4, nxt & 0xFF, (nxt >> 8) & 0xFF])
        n = r[4]
        if not n:
            time.sleep(0.002)
            continue
        first = (nxt & ~0xFF) | r[3]
        if first < nxt - 128:
            first += 0x100
        lost += max(0, first - nxt)
        for i in range(n):
            cyc, sof, tick_lo, tick_hi, late, _zero, key, kind, a, b = struct.unpack_from('<IIHBBBBBhh', r, 5 + 19 * i)
            out.append({'n': first + i, 'cycles': cyc, 'sof': sof, 'systick': tick_lo | tick_hi << 16, 'late': late,
                        'key': key, 'kind': kind, 'a': a, 'b': b})
        nxt = first + n
    return out, lost


def histogram(values, period, bins=30):
    h = [0] * bins
    for v in values:
        h[min(bins - 1, int((v % period) * bins / period))] += 1
    return h


def rayleigh(values, period):
    """How strongly values bunch at one phase of a period: 0 = evenly spread,
    1 = all at the same phase."""
    if not values:
        return 0.0
    c = sum(math.cos(2 * math.pi * v / period) for v in values)
    s = sum(math.sin(2 * math.pi * v / period) for v in values)
    return math.hypot(c, s) / len(values)


def show_phase(title, strays, controls, period, unit_cycles, unit):
    bins = 30
    hs, hc = histogram(strays, period, bins), histogram(controls, period, bins)
    top = max(range(bins), key=lambda i: hs[i])
    width = period / bins / unit_cycles
    print(f'   {title}: stray readings bunch {rayleigh(strays, period):.2f}, ordinary readings '
          f'{rayleigh(controls, period):.2f} (0 = spread evenly, 1 = all at one moment)')
    print(f'     fullest of {bins} slices ({width:.1f} {unit} each): {hs[top] / max(len(strays), 1) * 100:.0f}% of the stray '
          f'readings at {top * width:.0f}-{(top + 1) * width:.0f} {unit}; ordinary readings there: '
          f'{hc[top] / max(len(controls), 1) * 100:.0f}%')
    scale = max(max(hs), 1)
    print('     stray:    ' + ''.join(' .:-=+*#%@'[min(9, h * 9 // scale)] for h in hs))
    scale = max(max(hc), 1)
    print('     ordinary: ' + ''.join(' .:-=+*#%@'[min(9, h * 9 // scale)] for h in hc))
    return {'bunching_stray': rayleigh(strays, period), 'bunching_ordinary': rayleigh(controls, period),
            'stray': hs, 'ordinary': hc}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--seconds', type=float, default=20, help='seconds per rate measurement')
    ap.add_argument('--log-seconds', type=float, default=60, help='seconds of the stray reading log to collect')
    ap.add_argument('--log', default='stray_hunt.json')
    ap.add_argument('--skip', default='', help='parts to leave out: speeds,pins,timing,overrides')
    args = ap.parse_args()
    skip = set(filter(None, args.skip.split(',')))

    if studio_running():
        raise SystemExit('MAD68 Studio is running: close it first.')
    kb = Keyboard()
    fw = kb.firmware()
    st = status(kb)
    if st is None or st['proto'] < 3:
        raise SystemExit('This needs custom firmware v7.5 or newer.')
    print(f'Firmware: {fw[0]} ({fw[1]})')
    tpu = st['tpu']
    live = (st['mode'], st['ticks'] / tpu, st['sample'])
    report = {'firmware': fw, 'live': list(live)}
    went_live = False
    print(f"Saved scan speed {st['speed']}, reading keys with: {scan_probe.name(live)}, {st['rate']} scans/s, "
          f"main loop {st['loops']:,} passes/s")
    try:
        if 'speeds' not in skip:
            print(f'\n1. The fast scan speeds, bottom row read twice ({args.seconds:g} s each)')
            speeds = []
            for title, timing in FAST:
                p = scan_probe.probe(kb, timing, 4, tpu, live=True)
                st = status(kb)
                if not st['flags'] & 1 or (st['mode'], st['sample']) != (timing[0], timing[2]):
                    why = scan_probe.ERRORS.get(p['error'], p['error'])
                    print(f'   {title:8} not taken: {why}')
                    speeds.append({'speed': title, 'refused': why})
                    continue
                went_live = True
                time.sleep(1.5)
                r = pair_rate(kb, args.seconds)
                st = status(kb)
                print(f"   {title:8} {r['scan_rate']:>5} scans/s ({r['scan_rate'] * SENSORS / 1000:.0f}K), main loop "
                      f"{st['loops']:,} passes/s, {r['faults']} faults, {r['per_s']:.1f} stray readings dropped a second")
                speeds.append({'speed': title, 'loops': st['loops'], **r})
            report['speeds'] = speeds
            st = status(kb)
            live = (st['mode'], st['ticks'] / tpu, st['sample'])
        if not live[0] & 4:
            timing = (PAIRED, 5, 1)
            p = scan_probe.probe(kb, timing, 4, tpu, live=True)
            st = status(kb)
            if not st['flags'] & 1:
                raise SystemExit(f"The keyboard did not take the paired read ({scan_probe.ERRORS.get(p['error'])}).")
            went_live = True
            time.sleep(1.5)
            live = timing
        print(f'\nFrom here on with: {scan_probe.name(live)}, {status(kb)["rate"]} scans/s\n')
        conv = (SAMPLE_CYCLES[live[2]] + 12) * 8          # CPU cycles per conversion (ADC clock = F_CPU / 8)

        print(f'2. How often the bottom row\'s two readings disagree ({args.seconds:g} s each)')
        quiet = pair_rate(kb, args.seconds)
        busy = pair_rate(kb, args.seconds, busy=True)
        readings = quiet['scan_rate'] * 10                 # ten keys on the bottom row, read once per scan (twice over)
        print(f"   USB link quiet: {quiet['per_s']:6.1f} a second (1 pair in {readings / max(quiet['per_s'], 1e-9):,.0f})")
        print(f"   USB link busy:  {busy['per_s']:6.1f} a second")
        report['quiet'], report['busy'] = quiet, busy
        seen = []
        for mode, label in ((live[0] & ~4, 'each row read once'), (live[0], 'bottom row read twice')):
            n = stray = 0
            t0 = time.monotonic()
            while time.monotonic() - t0 < args.seconds:
                p = scan_probe.probe(kb, (mode, live[1], live[2]), 8, tpu)
                if not scan_probe.ok(p):
                    continue
                n += 1
                for k in range(NUM_KEYS):
                    if POS[k][0] == 4 and (p['high'][k] - p['mean'][k] > 20 or p['mean'][k] - p['low'][k] > 20):
                        stray += 1
            seen.append({'mode': mode, 'probes': n, 'stray_probes': stray})
            print(f'   {label}: a stray reading reached a bottom-row key in {stray} of {n * 10} probes of a key '
                  f'({stray / max(n * 10, 1) * 1000:.1f} per 1,000)')
        report['reaching_keys'] = seen

        if 'pins' not in skip:
            print('\n3. Pins that change level by themselves (each port watched for 20 ms)')
            pins = {}
            for i, port in enumerate(PORTS):
                r = kb.cmd(CMD_STATUS, [5, i], 1500)
                if r[0] == CMD_UNKNOWN:
                    continue
                samples = (r[2] | r[3] << 8) * 256
                for pin in range(16):
                    changes = r[4 + 2 * pin] | r[5 + 2 * pin] << 8
                    high = r[36 + pin] * 100 // 255
                    pins[f'P{port}{pin}'] = {'changes': changes, 'high_percent': high}
                    if changes:
                        why = EXPECTED.get((port, pin))
                        print(f"   P{port}{pin:<2} changed {changes:>6} times in 20 ms (about {changes * 50:,} a second), "
                              f"high {high}% of the time" + (f'   [{why}: expected]' if why else '   <-- NOT driven by this firmware'))
                print(f'   port {port}: {samples:,} samples')
            quiet_pins = [n for n in ('PA4', 'PA5', 'PA6', 'PC4', 'PC5', 'PB0', 'PB1', 'PB2')]
            print('   levels of the pins next to the sensor lines: '
                  + ', '.join(f"{n} {'high' if pins[n]['high_percent'] > 50 else 'low'}"
                              f"{' (moving)' if pins[n]['changes'] else ''}" for n in quiet_pins if n in pins))
            report['pins'] = pins

        if 'timing' not in skip:
            print(f'\n4. When the stray readings happen ({args.log_seconds:g} s of the log)')
            status(kb, 7, 1)
            time.sleep(0.3)
            entries, lost = read_log(kb, args.log_seconds)
            status(kb, 7, 0)
            report['log'] = entries
            kinds = {k: [e for e in entries if e['kind'] == k] for k in (0, 1, 2)}
            print(f"   {len(kinds[1])} disagreeing pairs, {len(kinds[2])} jumps, {len(kinds[0])} ordinary readings logged "
                  f"({lost} entries missed)")
            first_hit = [e for e in kinds[1] if abs(e['a']) > abs(e['b'])]
            print(f"   the stray reading was the first of the pair in {len(first_hit)} and the second in "
                  f"{len(kinds[1]) - len(first_hit)}")
            amps = [e['a'] if abs(e['a']) > abs(e['b']) else e['b'] for e in kinds[1]]
            if amps:
                print(f"   sizes: median {statistics.median(abs(a) for a in amps):.0f} counts, "
                      f"{sum(1 for a in amps if a > 0) * 100 // len(amps)}% upward")
            by_key = {}
            for e in kinds[1]:
                by_key[LABELS[e['key']]] = by_key.get(LABELS[e['key']], 0) + 1
            print('   by key: ' + ', '.join(f'{k} {v}' for k, v in sorted(by_key.items(), key=lambda kv: -kv[1])))

            def sample_time(e):
                """Cycle counter when the reading in question was sampled."""
                which_first = abs(e['a']) > abs(e['b']) if e['kind'] == 1 else False
                return e['cycles'] - 24 - 96 - (conv if which_first else 0)

            with_sof = [e for e in entries if e['sof']]
            if with_sof:
                frame = 168_000_000 // 8000
                ph = {k: [(sample_time(e) - e['sof']) % (1 << 32) for e in with_sof if e['kind'] == k] for k in (0, 1)}
                report['usb_phase'] = show_phase('within the USB microframe (125 us)', ph[1], ph[0], frame, tpu, 'us')
            else:
                print('   no USB frame timestamps came through')
            tick = tpu * 1000
            ph = {k: [(tick - 1 - e['systick'] - 2 * e['late'] - (e['cycles'] - sample_time(e))) % tick
                      for e in entries if e['kind'] == k] for k in (0, 1)}
            report['tick_phase'] = show_phase('within the millisecond tick', ph[1], ph[0], tick, tpu, 'us')

        if 'overrides' not in skip:
            print(f'\n5. Does a pull or analog mode on a neighbouring pin change it? ({args.seconds:g} s each)')
            base = pair_rate(kb, args.seconds)
            sigma = math.sqrt(max(base['pairs'], 1)) / base['seconds']
            print(f"   as the stock firmware leaves them: {base['per_s']:6.1f} a second (+-{sigma:.1f})")
            trials = []
            for port, pin, mode, label in OVERRIDES:
                r = kb.cmd(CMD_STATUS, [6, PORTS.index(port), pin, mode], 1500)
                if r[0] == CMD_UNKNOWN:
                    print(f'   {label}: refused')
                    continue
                time.sleep(0.3)
                t = pair_rate(kb, args.seconds)
                kb.cmd(CMD_STATUS, [6, PORTS.index(port), pin, 0], 1500)
                change = (t['per_s'] - base['per_s']) / max(base['per_s'], 1e-9) * 100
                verdict = 'no real change' if abs(t['per_s'] - base['per_s']) < 4 * sigma else 'CHANGED'
                print(f"   {label:14}: {t['per_s']:6.1f} a second ({change:+5.0f}%)  {verdict}")
                trials.append({'pin': f'P{port}{pin}', 'mode': mode, 'label': label, **t})
            # all three of the pins on an alternate function at once
            for pin in (4, 5, 6):
                kb.cmd(CMD_STATUS, [6, 0, pin, 4], 1500)
            time.sleep(0.3)
            t = pair_rate(kb, args.seconds)
            for pin in (4, 5, 6):
                kb.cmd(CMD_STATUS, [6, 0, pin, 0], 1500)
            change = (t['per_s'] - base['per_s']) / max(base['per_s'], 1e-9) * 100
            verdict = 'no real change' if abs(t['per_s'] - base['per_s']) < 4 * sigma else 'CHANGED'
            print(f"   {'PA4-6 pull-down':14}: {t['per_s']:6.1f} a second ({change:+5.0f}%)  {verdict}")
            trials.append({'pin': 'PA4-6', 'mode': 4, 'label': 'PA4-6 pull-down', **t})
            report['overrides'] = {'base': base, 'trials': trials}
    except OSError:
        report['error'] = 'keyboard disconnected'
        print('\nThe keyboard disconnected. If it does not come back by itself, unplug it and plug it back in.')
        went_live = False
    finally:
        with open(args.log, 'w', encoding='utf-8') as f:
            json.dump(report, f)
        print(f'\nSaved {args.log}')
        if went_live:
            try:
                kb.cmd(CMD_SET_SCAN, [status(kb)['speed']], 1500)
                time.sleep(2.5)
                st = status(kb)
                print(f"Saved scan speed put back: {scan_probe.name((st['mode'], st['ticks'] / tpu, st['sample']))}, "
                      f"{st['rate']} scans/s, {st['faults']} faults since the keyboard was plugged in.")
            except OSError:
                pass


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nStopped.')
        sys.exit(1)
