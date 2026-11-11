"""Find how fast a MAD68 HE can read its keys without the readings changing.

Needs the timing test firmware (custom firmware 7.3.90, the v7.4 test build),
which can read the keyboard at any timing for a moment and hand back each
key's lowest, highest and average raw reading, while the keys themselves stay
on their readings from before (a "probe"). Nothing here is saved on the
keyboard, and after a restart it is back on its saved scan speed.

A timing is a mode, a settle time and an ADC sample time:

  mode 0  one ADC, results stored before the next column is powered (v7.3)
  mode 2  one ADC, next column powered first
  mode 1  three ADCs at once, results stored first
  mode 3  three ADCs at once, next column powered first

What it does (close MAD68 Studio first; it uses the same config interface):

  1. hands off: probes every timing in the list and compares each one with
     the reference (mode 0, 40 us, 28-cycle samples: the v7.2 timing). Shows
     the scan rate, the noise and how far the rest readings moved.
  2. hold: you hold a few keys all the way down, and it probes every timing
     again. Shows how each timing reads a full press against the reference,
     and whether the keys around a held one read differently.

Then it prints one line per timing, and saves everything it measured.

  python scan_probe.py [--log scan_probe.json] [--no-hold] [--scans 9]

To run timings for real for a while (keys read with them), after step 1 and 2
have shown they read cleanly:

  python scan_probe.py --live 3,8,2 3,5,2 [--seconds 20]

--live takes one or more of mode,settle in us,sample code (0 = 3 cycles,
1 = 15, 2 = 28). With hands off it counts false presses and faults for each
and shows the scan rate, the main loop rate and the noise left after the
keyboard's own filter, then puts the saved scan speed back.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time

from scan_check import (AT, CMD_GET_SCAN, CMD_SET_SCAN, CMD_UNKNOWN, LABELS, NUM_KEYS, POS, Keyboard,
                        countdown, drain_console, mm, studio_running)

CMD_SCAN_PROBE = 0x6A
CMD_SCAN_STATUS = 0x6B
NONE, RUNNING, DONE, FAILED = 0, 1, 2, 3
ERRORS = {0: '', 1: 'ADC2/ADC3 did not answer', 2: 'faults (overrun, DMA error or missing results)',
          3: 'scan stopped', 4: 'readings moved too far to go live', 5: 'replaced by a scan speed change'}
F_TUNED, F_ADC23, F_FELL_BACK, F_CAPTURING = 1, 2, 4, 8
SAMPLE_CYCLES = [3, 15, 28, 56, 84, 112, 144, 480]
MODE = {0: '1 ADC ', 1: '3 ADCs', 2: '1 ADC+', 3: '3 ADCs+'}
SENSORS = 75          # matrix positions read per scan (15 columns x 5 rows): the "total" marketing number
HELD = 200            # distance (0-255) that counts as held all the way down
REF = (0, 40, 2)      # the reference timing: v7.2's, with the longest settle

# (mode, settle in us, sample code)
TIMINGS = (
    [REF, (0, 28, 2), (0, 18, 2), (0, 8, 2)]                                    # today's four speeds
    + [(2, us, 2) for us in (8, 6, 5, 4, 3, 2, 1)]                              # next column first
    + [(1, 8, 2)]                                                               # three ADCs only
    + [(3, us, 2) for us in (40, 8, 6, 5, 4, 3, 2, 1, 0.5)]                     # both
    + [(3, us, 1) for us in (8, 5, 3, 2)] + [(3, us, 0) for us in (8, 5, 3)]    # shorter samples
    + [(2, us, 1) for us in (8, 5)]
)


def name(t):
    mode, us, sample = t
    return f'{MODE[mode]:7} {us:>4g} us {SAMPLE_CYCLES[sample]:>2}cyc'


def parse_status(r):
    w = lambda i: r[i] | r[i + 1] << 8            # noqa: E731
    return dict(proto=r[1], mode=r[2], ticks=w(3), sample=r[5], flags=r[6], state=r[7], err=r[8],
                pmode=r[9], pticks=w(10), psample=r[12], plog2=r[13],
                cycles=int.from_bytes(r[14:18], 'little'), shift=w(18), shift_key=r[20],
                pfaults=w(21), faults=w(23), sync=w(25), overruns=w(27), stalls=w(29), rate=w(31),
                loops=int.from_bytes(r[33:36], 'little'), tpu=r[36], speed=r[37])


def status(kb):
    r = kb.cmd(CMD_SCAN_STATUS, [0])
    return None if r[0] == CMD_UNKNOWN else parse_status(r)


def results(kb):
    out = []
    for first in range(0, NUM_KEYS, 10):
        r = kb.cmd(CMD_SCAN_STATUS, [1, first])
        if r[0] == CMD_UNKNOWN or r[2] != first:
            raise RuntimeError('results refused')
        for i in range(r[3]):
            p = 4 + 6 * i
            out.append(((r[p] | r[p + 1] << 8) / 16, r[p + 2] | r[p + 3] << 8, r[p + 4] | r[p + 5] << 8))
    return out


def probe(kb, timing, log2, tpu, live=False):
    """Run one probe and return what it measured."""
    mode, us, sample = timing
    ticks = max(8, round(us * tpu))
    r = kb.cmd(CMD_SCAN_PROBE, [mode, ticks & 0xFF, ticks >> 8, sample, log2, 1 if live else 0], 1500)
    if r[0] == CMD_UNKNOWN:
        raise RuntimeError(f'the keyboard refused the probe {timing}')
    t0 = time.monotonic()
    st = parse_status(r)
    while st['state'] == RUNNING:
        if time.monotonic() - t0 > 20:
            raise TimeoutError('probe did not end')
        time.sleep(0.005)
        st = status(kb)
    scans = 1 << st['plog2']
    out = {'timing': list(timing), 'ticks': ticks, 'scans': scans, 'state': st['state'], 'error': st['err'],
           'faults': st['pfaults'], 'cycles': st['cycles'], 'shift': st['shift'], 'shift_key': st['shift_key'],
           'flags': st['flags']}
    out['rate'] = scans * tpu * 1e6 / st['cycles'] if st['cycles'] else 0.0
    if st['state'] == DONE or (st['state'] == FAILED and st['err'] == 4):
        k = results(kb)
        out['mean'] = [a for a, _, _ in k]
        out['low'] = [b for _, b, _ in k]
        out['high'] = [c for _, _, c in k]
    return out


def ok(p):
    return p['state'] == DONE and 'mean' in p


def run_list(kb, timings, log2, tpu, label):
    """Probe every timing; the reference again at the end, to see drift."""
    out = []
    todo = list(timings) + [REF]
    parallel_dead = False
    for i, t in enumerate(todo):
        print(f'\r  {label}: {i + 1}/{len(todo)}  {name(t)}      ', end='', flush=True)
        if t[0] & 1 and parallel_dead:
            out.append({'timing': list(t), 'state': FAILED, 'error': 1, 'faults': 0, 'rate': 0.0, 'skipped': True})
            continue
        p = probe(kb, t, log2, tpu)
        if t[0] & 1 and p['state'] == FAILED and p['error'] == 1:
            parallel_dead = True
        out.append(p)
    print(f'\r  {label}: done, {len(todo)} probes' + ' ' * 30, flush=True)
    return out[:-1], out[-1]


def spread(p):
    d = [h - l for l, h in zip(p['low'], p['high'])]
    worst = max(range(NUM_KEYS), key=lambda k: d[k])
    return statistics.median(d), d[worst], worst


def rest_move(p, ref):
    d = [a - b for a, b in zip(p['mean'], ref['mean'])]
    worst = max(range(NUM_KEYS), key=lambda k: abs(d[k]))
    return statistics.median(d), d[worst], worst


def held_keys(kb):
    _, dist = kb.sweep()
    return sorted(k for k in range(NUM_KEYS) if dist[k] >= HELD)


def wait_for_hold(kb, want=3, timeout=60):
    """Wait until at least `want` keys are held all the way down and the set
    has not changed for most of a second."""
    t0 = time.monotonic()
    last, since = [], time.monotonic()
    while time.monotonic() - t0 < timeout:
        now = held_keys(kb)
        if now != last:
            last, since = now, time.monotonic()
            print(f"\r  held: {' '.join(LABELS[k] for k in now) or '(none)'}" + ' ' * 20, end='', flush=True)
        elif len(now) >= want and time.monotonic() - since > 0.7:
            print(flush=True)
            return now
        time.sleep(0.02)
    print(flush=True)
    return []


def press_stats(p, rest_p, ref, rest_ref, held):
    """How a timing reads the held keys and the keys around them, against the
    reference. Travel = distance of the held reading from the rest reading at
    the same timing, so a move of the rest reading itself does not count."""
    ratios = {}
    for k in held:
        full_ref = ref['mean'][k] - rest_ref['mean'][k]
        full = p['mean'][k] - rest_p['mean'][k]
        if abs(full_ref) >= 200:
            ratios[k] = full / full_ref
    # Keys not held: how much further from rest they read than at the
    # reference timing (a held key's magnet moves its neighbours a little at
    # any timing; only the difference is the timing's doing)
    extra = {}
    for k in range(NUM_KEYS):
        if k in held:
            continue
        extra[k] = (p['mean'][k] - rest_p['mean'][k]) - (ref['mean'][k] - rest_ref['mean'][k])
    groups = {'next column': [], 'same column': [], 'elsewhere': []}
    for k, v in extra.items():
        r, c = POS[k]
        if any(POS[h] == (r, c - 1) or (c == 0 and POS[h] == (r, 14)) for h in held):
            groups['next column'].append((abs(v), k))
        elif any(POS[h][1] == c for h in held):
            groups['same column'].append((abs(v), k))
        else:
            groups['elsewhere'].append((abs(v), k))
    worst = {g: max(v) if v else (0.0, None) for g, v in groups.items()}
    return ratios, worst


def judge(p, ref, noise_ref, press=None):
    """Reasons a timing does not read like the reference; empty = it does."""
    why = []
    if not ok(p):
        return [ERRORS.get(p.get('error', 0)) or 'probe failed']
    if p['faults']:
        why.append(f"{p['faults']} faults")
    _, worst, _ = spread(p)
    if worst > max(noise_ref * 1.5, noise_ref + 8):
        why.append('noisier')
    if abs(rest_move(p, ref)[1]) > 15:
        why.append('rest readings moved')
    if press:
        ratios, cross = press
        if ratios and (min(ratios.values()) < 0.97 or max(ratios.values()) > 1.03):
            why.append('full press reads differently')
        if cross['next column'][0] > 12 or cross['same column'][0] > 12 or cross['elsewhere'][0] > 12:
            why.append('other keys move')
    return why


def table(rows, ref, press_rows=None, held=()):
    noise_ref = spread(ref)[1]
    print()
    print(f"{'timing':22} {'scans/s':>8} {'total':>6} {'us/col':>7} {'noise':>9} {'rest moved':>12} "
          f"{'full press':>12} {'others':>7}  verdict")
    summary = []
    for i, p in enumerate(rows):
        t = tuple(p['timing'])
        if not ok(p):
            why = 'skipped: no ADC2/ADC3' if p.get('skipped') else ERRORS.get(p.get('error', 0), 'failed')
            print(f'{name(t):22} {"-":>8} {"-":>6} {"-":>7} {"-":>9} {"-":>12} {"-":>12} {"-":>7}  {why}')
            summary.append({'timing': list(t), 'verdict': [why]})
            continue
        med, worst, _ = spread(p)
        rmed, rworst, rkey = rest_move(p, ref)
        press = None
        press_txt, other_txt = '-', '-'
        if press_rows and ok(press_rows[i][0]):
            hp, href, rest_ref_now = press_rows[i]
            press = press_stats(hp, p, href, rest_ref_now, held)
            ratios, cross = press
            if ratios:
                press_txt = f'{min(ratios.values()) * 100:.1f}-{max(ratios.values()) * 100:.1f}%'
            other_txt = f"{max(v[0] for v in cross.values()):.0f}"
        why = judge(p, ref, noise_ref, press)
        rate = p['rate']
        print(f'{name(t):22} {rate:>8.0f} {rate * SENSORS / 1000:>5.0f}K {1e6 / rate / 15:>7.2f} '
              f'{med:>4.0f}/{worst:<4.0f} {rmed:>+5.0f}/{rworst:<+5.0f} {press_txt:>12} {other_txt:>7}  '
              f"{', '.join(why) or 'reads like the reference'}")
        summary.append({'timing': list(t), 'rate': rate, 'noise_median': med, 'noise_worst': worst,
                        'rest_median': rmed, 'rest_worst': rworst, 'rest_worst_key': LABELS[rkey],
                        'press': {LABELS[k]: v for k, v in press[0].items()} if press else None,
                        'others': {g: [v[0], LABELS[v[1]] if v[1] is not None else None]
                                   for g, v in press[1].items()} if press else None,
                        'verdict': why})
    print('+ = next column powered before the results are stored; total = sensor readings per second (75 per scan);')
    print('noise = highest minus lowest raw reading of a key at rest, median key / worst key (ADC counts);')
    print('rest moved = rest reading against the reference timing, median key / worst key (counts);')
    print('full press = travel of the held keys against the reference; others = most any other key moved (counts)')
    return summary


def restore(kb, speed):
    """Back to the saved scan speed if a timing from a probe is live."""
    try:
        st = status(kb)
        if st and st['flags'] & F_TUNED:
            kb.cmd(CMD_SET_SCAN, [speed], 1500)
            print('Saved scan speed put back.')
    except OSError:
        print('The keyboard disconnected; unplug it and plug it back in to return to the saved scan speed.')


def connect():
    if studio_running():
        print('Close MAD68 Studio to start: it uses the same config interface, and its '
              'replies would mix with this test\'s readings. Waiting...', flush=True)
        while studio_running():
            time.sleep(1)
        time.sleep(1)
    kb = Keyboard()
    fw = kb.firmware()
    print(f'Firmware: {fw[0]} ({fw[1]})' if fw else 'Firmware: (no firmware info)')
    st = status(kb)
    if st is None:
        raise SystemExit('This firmware has no timing test (it needs the v7.4 test build, 7.3.90).')
    return kb, fw, st


def show_status(st):
    live = (st['mode'], st['ticks'] / st['tpu'], st['sample'])
    print(f"Reading keys with: {name(live)} ({'from a probe' if st['flags'] & F_TUNED else 'the saved scan speed'}), "
          f"{st['rate']} scans/s, main loop {st['loops']}/s")
    print(f"Faults since power-up: {st['faults']} ({st['sync']} missing results, {st['overruns']} overruns), "
          f"{st['stalls']} stalled scans" + ('; a live timing was dropped after faults' if st['flags'] & F_FELL_BACK else ''))


def survey(args):
    kb, fw, st = connect()
    tpu = st['tpu']
    show_status(st)
    report = {'firmware': fw, 'status_before': st, 'scans': 1 << args.scans, 'timings': [list(t) for t in TIMINGS]}
    if sys.stdin.isatty():
        print('\nTwo steps: hands off for about 15 seconds, then hold 2, R, J, M and Space down for about 10 seconds.')
        print('Keep this window focused, so that what the held keys type lands here.')
        input('Press Enter to start... ')
    try:
        print('\nStep 1 of 2. Nothing is typed or changed: the keyboard just measures itself.')
        countdown('  Hands off the keyboard:', 3)
        rest_rows, rest_ref_end = run_list(kb, TIMINGS, args.scans, tpu, 'hands off')
        ref = rest_rows[0]
        if not ok(ref):
            raise SystemExit('The reference probe failed: ' + ERRORS.get(ref.get('error', 0), '?'))
        report['rest'] = rest_rows
        report['rest_reference_again'] = rest_ref_end
        if ok(rest_ref_end):
            d = rest_move(rest_ref_end, ref)
            print(f'  reference read again at the end: rest readings moved {d[0]:+.1f} (median) / {d[1]:+.1f} '
                  f'(worst, {LABELS[d[2]]}) counts in the meantime')
        st = status(kb)
        if any(t[0] & 1 for t in TIMINGS):
            print('  ADC2 and ADC3: ' + ('found and working' if st['flags'] & F_ADC23 and
                                         any(ok(p) for p in rest_rows if p['timing'][0] & 1)
                                         else 'NOT usable on this keyboard (three-ADC timings skipped)'))

        press_rows, held = None, []
        if not args.no_hold:
            print('\a\nStep 2 of 2. Press and HOLD a few keys ALL THE WAY DOWN, one in each row if you can:')
            print('  >>>>> HOLD NOW:   2   R   J   M   Space <<<<<')
            print('  Keep them down until it says to let go (a few seconds). Letters typed here are thrown away.')
            held = wait_for_hold(kb)
            if len(held) < 3:
                print('  Fewer than 3 keys held: skipping the full-press part.')
            else:
                rows_held = sorted({POS[k][0] for k in held})
                print(f"  Holding {' '.join(LABELS[k] for k in held)} (rows {', '.join(str(r + 1) for r in rows_held)}). Keep holding...")
                hold_rows, hold_ref_end = run_list(kb, TIMINGS, max(args.scans - 2, 6), tpu, 'holding')
                still = held_keys(kb)
                print('\a  >>>>> LET GO <<<<<', flush=True)
                href = hold_rows[0]
                if not ok(href) or not ok(hold_ref_end):
                    print('  The reference probe failed while holding; full-press part not used.')
                else:
                    drift = max(abs(hold_ref_end['mean'][k] - href['mean'][k]) for k in held)
                    report['hold_drift'] = drift
                    if set(still) != set(held) or drift > 25:
                        print(f'  The held keys moved during the test (up to {drift:.0f} counts): treat the full-press '
                              'column with care, or run it again.')
                    press_rows = [(hp, href, ref) for hp in hold_rows]
                    report['hold'] = hold_rows
                    report['hold_reference_again'] = hold_ref_end
                    report['held'] = [LABELS[k] for k in held]
        report['summary'] = table(rest_rows, ref, press_rows, held)
        st = status(kb)
        report['status_after'] = st
        print()
        show_status(st)
    except OSError:
        report['error'] = 'keyboard disconnected'
        print('\n\nThe keyboard disconnected. If it does not come back by itself, it is in update mode: unplug it '
              'and plug it back in. Nothing was saved, so it starts on its saved scan speed.')
    finally:
        with open(args.log, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=1)
        print(f'Saved {args.log}')
        restore(kb, st['speed'] if st else 1)
        time.sleep(0.5)
        drain_console()


def parse_timing(text):
    mode, us, sample = text.split(',')
    return int(mode), float(us), int(sample)


def live_one(kb, timing, tpu, act, seconds):
    """Read the keys with one timing for a while, hands off, and report how
    the keyboard as a whole runs with it."""
    out = {'timing': list(timing)}
    p = probe(kb, timing, 4, tpu, live=True)
    st = status(kb)
    out['probe'] = {k: v for k, v in p.items() if k not in ('mean', 'low', 'high')}
    if not st['flags'] & F_TUNED and tuple(timing) != (st['mode'], st['ticks'] / tpu, st['sample']):
        out['refused'] = ERRORS.get(p['error'], 'refused')
        return out
    t0 = time.monotonic()
    while status(kb)['flags'] & F_CAPTURING and time.monotonic() - t0 < 3:
        time.sleep(0.02)
    time.sleep(1.2)                   # one full window of the scan rate and main loop counters
    faults0 = status(kb)['faults']
    lo, hi = [4095] * NUM_KEYS, [0] * NUM_KEYS
    false_presses, deepest, sweeps = set(), 0, 0
    rates, loops = [], []
    t0 = time.monotonic()
    last = -1
    while (t := time.monotonic() - t0) < seconds:
        adc, dist = kb.sweep()
        sweeps += 1
        for k in range(NUM_KEYS):
            if adc[k] < lo[k]:
                lo[k] = adc[k]
            if adc[k] > hi[k]:
                hi[k] = adc[k]
        deepest = max(deepest, max(dist))
        false_presses.update(LABELS[k] for k in range(NUM_KEYS) if dist[k] >= act[k])
        if int(t * 2) != last:
            last = int(t * 2)
            st = status(kb)
            if st['rate']:
                rates.append(st['rate'])
            loops.append(st['loops'])
            print(f"\r  {name(timing)}: {seconds - t:4.1f} s left, {st['rate']} scans/s, main loop {st['loops']}/s   ",
                  end='', flush=True)
            if not st['flags'] & F_TUNED and timing[0] != 0:
                break
    st = status(kb)
    p2p = [h - l for l, h in zip(lo, hi)]
    worst = max(range(NUM_KEYS), key=lambda k: p2p[k])
    out.update(rate=statistics.median(rates) if rates else 0, loops=statistics.median(loops) if loops else 0,
               faults=st['faults'] - faults0, dropped=bool(timing[0] != 0 and not st['flags'] & F_TUNED),
               false_presses=sorted(false_presses), deepest=deepest, sweeps=sweeps,
               filtered_median=statistics.median(p2p), filtered_worst=p2p[worst], filtered_worst_key=LABELS[worst],
               shift=p['shift'])
    print('\r' + ' ' * 78 + '\r', end='')
    return out


def live(args):
    timings = [parse_timing(t) for t in args.live]
    kb, fw, st = connect()
    tpu, speed = st['tpu'], st['speed']
    show_status(st)
    _, act = kb.actuation_points()
    report = {'firmware': fw, 'status_before': st, 'runs': []}
    if sys.stdin.isatty():
        total = len(timings) * (args.seconds + 2.5)
        print(f'\nThe keys will be read with {len(timings)} timing(s), {args.seconds:g} s each (about {total:.0f} s). '
              'Keep your hands off the keyboard until it says it is done.')
        input('Press Enter to start... ')
    try:
        countdown('  Hands off the keyboard:', 3)
        for t in timings:
            r = live_one(kb, t, tpu, act, args.seconds)
            report['runs'].append(r)
            if 'refused' in r:
                print(f"  {name(t)}: not taken: {r['refused']}")
            else:
                print(f"  {name(t)}: {r['rate']:.0f} scans/s, main loop {r['loops']:.0f}/s, {r['faults']} faults"
                      + (', DROPPED by the keyboard' if r['dropped'] else ''))
        print('\a\n  >>>>> DONE: you can use the keyboard again <<<<<')
        print(f"\n{'timing':22} {'scans/s':>8} {'total':>6} {'main loop/s':>12} {'faults':>7} {'filtered noise':>15} "
              f"{'deepest':>8}  false presses")
        for r in report['runs']:
            t = tuple(r['timing'])
            if 'refused' in r:
                print(f"{name(t):22} not taken: {r['refused']}")
                continue
            print(f"{name(t):22} {r['rate']:>8.0f} {r['rate'] * SENSORS / 1000:>5.0f}K {r['loops']:>12.0f} {r['faults']:>7} "
                  f"{r['filtered_median']:>6.0f}/{r['filtered_worst']:<4.0f}{r['filtered_worst_key']:>4} "
                  f"{mm(r['deepest']):>6.2f}mm  {', '.join(r['false_presses']) or 'none'}"
                  + ('  DROPPED' if r['dropped'] else ''))
        print('filtered noise = highest minus lowest filtered reading of a key (what the keys act on), median key / worst key;')
        print('deepest = furthest any key read as pressed with hands off')
        st = status(kb)
        report['status_after'] = st
        print()
        show_status(st)
    except OSError:
        report['error'] = 'keyboard disconnected'
        print('\n\nThe keyboard disconnected. If it does not come back by itself, unplug it and plug it back in.')
    finally:
        with open(args.log, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=1)
        print(f'Saved {args.log}')
        if not args.keep:
            restore(kb, speed)
        else:
            print('Left live (--keep): it stays until the keyboard restarts or a scan speed is set in MAD68 Studio.')
        drain_console()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--log', default='scan_probe.json')
    ap.add_argument('--scans', type=int, default=9, help='log2 of the scans per probe with hands off (4-12)')
    ap.add_argument('--no-hold', action='store_true', help='skip the part where keys are held down')
    ap.add_argument('--live', nargs='+', metavar='MODE,US,SAMPLE',
                    help='read the keys with each of these timings for a while, e.g. 3,8,2 3,5,2')
    ap.add_argument('--seconds', type=float, default=20, help='with --live: seconds per timing')
    ap.add_argument('--keep', action='store_true', help='with --live: leave the timing live at the end')
    args = ap.parse_args()
    if args.live:
        live(args)
    else:
        survey(args)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nStopped.')
        drain_console()
        sys.exit(1)
