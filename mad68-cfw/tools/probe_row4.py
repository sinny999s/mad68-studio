"""Find which stock key slot each (ADC row input, column pin) feeds.

Feeds baseline samples everywhere except one (slot, column-pin) position, then
diffs RAM to see which per-key record changed.
"""
import struct, sys
from unicorn.arm_const import UC_ARM_REG_SP
from emu import Emu

PINS = [('A', 8), ('A', 9), ('A', 10), ('A', 11), ('A', 12), ('A', 15), ('C', 10), ('C', 11),
        ('C', 12), ('D', 2), ('B', 3), ('B', 4), ('B', 5), ('B', 6), ('B', 7)]
ODR = {p: 0x40020014 + 0x400 * i for i, p in enumerate('ABCDE')}
S4 = 0x40026400 + 0x10 + 0x18 * 4
REST, HOT = 2300, 3600
LO, HI = 0x20005000, 0x20006C00

def active(regs):
    low = [i for i, (p, b) in enumerate(PINS) if not (regs.get(ODR[p], 0) >> b) & 1]
    return low[0] if len(low) == 1 else None

e = Emu(sys.argv[1]); e.p.quiet = True
e.uc.reg_write(UC_ARM_REG_SP, 0x2001FF00); e.run(e.vec(1), max_chunks=60)
m0, n = e.p.regs[S4 + 0xC], e.p.regs[S4 + 4]
handler = e.vec(16 + 60)

def cycle(calls, hot=None):
    for _ in range(calls):
        col = active(e.p.regs)
        vals = []
        for _r in range(n // 7):
            for s in range(7):
                vals.append(HOT if hot and hot == (s, col) else (1500 if s == 0 else REST))
        e.uc.mem_write(m0, struct.pack('<%dH' % n, *vals))
        e.p.regs[0x40026404] = 0x30
        e.uc.reg_write(UC_ARM_REG_SP, 0x2001F000)
        e.call(handler, max_chunks=5)

def snap():
    return e.uc.mem_read(LO, HI - LO)

probes = [(1, 0), (6, 0), (1, 2), (6, 2), (1, 6), (6, 6), (1, 14), (6, 14), (4, 3)]
cycle(60)
b0 = snap(); cycle(45); b1 = snap()
noisy = {i for i in range(0, HI - LO, 2) if b0[i:i+2] != b1[i:i+2]}
print('control: addresses that change with baseline only:', len(noisy))
cycle(30)
for slot, col in probes:
    base = snap()
    cycle(120, hot=(slot, col))
    after = snap()
    changed = [i for i in range(0, HI - LO, 2) if base[i:i+2] != after[i:i+2] and i not in noisy]
    hot = [(LO + i, struct.unpack_from("<H", after, i)[0]) for i in changed if struct.unpack_from("<H", after, i)[0] >= 2600 and struct.unpack_from("<H", after, i)[0] < 5000]
    desc = ' '.join(f'key{(a - 0x200050f8) // 0x5C}+{(a - 0x200050f8) % 0x5C:#x}={v}' if a >= 0x200050f8 else f'0x{a:08x}={v}' for a, v in hot[:12])
    print(f'slot{slot} ({["vref","PB1","PA3","PA2","PA1","PA0","PA7"][slot]}) x P{PINS[col][0]}{PINS[col][1]}: {len(changed)} changed: {desc}')
    cycle(60)
