"""Read-only: sample per-key raw Hall sensor values from the stock firmware.

Uses OpenMAD's transport opened with writes=False, so only GET packets can be
sent. Prints rest value statistics per matrix position, plus any key whose
value moves during the sampling window (press keys while it runs to see the
direction the reading moves when a key goes down).
"""
import json, statistics, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])  # OpenMAD src dir
from mad68 import Mad68  # noqa: E402

seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 3
samples = []
with Mad68(writes=False) as kb:
    pop = kb.populated_mask()
    t0 = time.time()
    while time.time() - t0 < seconds:
        samples.append(kb.read_adc_raw())
cols = 15
out = {'populated': pop, 'samples': samples}
Path(sys.argv[3]).write_text(json.dumps(out))
print(f'{len(samples)} sweeps; populated {sum(pop)}/75')
for r in range(5):
    row = []
    for c in range(cols):
        i = r * cols + c
        vals = [s[i] for s in samples]
        row.append('  ----' if not pop[i] else f'{statistics.median(vals):6.0f}')
    print(f'row{r}:' + ''.join(row))
spread = [(max(s[i] for s in samples) - min(s[i] for s in samples), i) for i in range(75) if pop[i]]
spread.sort(reverse=True)
print('largest movement during window (counts, row, col, first->extreme):')
for d, i in spread[:6]:
    vals = [s[i] for s in samples]
    ext = max(vals, key=lambda v: abs(v - vals[0]))
    print(f'  {d:5}  r{i // cols} c{i % cols}  {vals[0]} -> {ext}')
