"""Tag DMA samples with the active column and find exact stores in RAM."""
import collections, struct, sys
from unicorn import UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_SP
from emu import Emu

COLS = [('A', 9), ('A', 10), ('A', 11), ('A', 12), ('A', 15), ('C', 10), ('C', 11), ('C', 12),
        ('D', 2), ('B', 3), ('B', 4), ('B', 5), ('B', 6), ('B', 7), ('A', 8)]
PORT = {p: 0x40020014 + 0x400 * i for i, p in enumerate('ABCDE')}
DMA2_S4 = 0x40026400 + 0x10 + 0x18 * 4

def active_col(regs):
    low = [i for i, (p, b) in enumerate(COLS) if not (regs.get(PORT[p], 0) >> b) & 1]
    return low[0] if len(low) == 1 else None

e = Emu(sys.argv[1]); e.p.quiet = True
e.uc.reg_write(UC_ARM_REG_SP, 0x2001FF00); e.run(e.vec(1), max_chunks=60)
m0ar, ndtr = e.p.regs[DMA2_S4 + 0xC], e.p.regs[DMA2_S4 + 4]
handler = e.vec(16 + 60)
writes = []
e.uc.hook_add(UC_HOOK_MEM_WRITE, lambda uc, acc, a, s, v, d: writes.append((a, s, v & 0xFFFF)) if 0x20000000 <= a < 0x20020000 else None)
found = collections.defaultdict(collections.Counter)
for it in range(int(sys.argv[2]) if len(sys.argv) > 2 else 45):
    col = active_col(e.p.regs)
    tagv = lambda s: 0x800 + s * 0x20 + (col if col is not None else 31)
    e.uc.mem_write(m0ar, b''.join(struct.pack('<H', tagv(s)) for _ in range(ndtr // 7) for s in range(7)))
    e.p.regs[0x40026404] = 0x30
    writes.clear(); e.uc.reg_write(UC_ARM_REG_SP, 0x2001F000); e.call(handler, max_chunks=5)
    for a, s, v in writes:
        if 0x800 <= v < 0x800 + 7 * 0x20:
            slot, c = (v - 0x800) >> 5, v & 0x1F
            found[(slot, c)][a] += 1
print('exact tag stores (slot=ADC seq index, col=active column at IRQ time):')
for (slot, c), addrs in sorted(found.items()):
    print(f'  slot{slot} col{c:2}: ' + ' '.join(f'0x{a:08x}x{n}' for a, n in sorted(addrs.items())[:6]))
