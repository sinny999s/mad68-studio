"""Record which flash sectors/addresses the stock firmware erases or programs."""
import collections, sys
from unicorn import UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_SP
from emu import Emu
e = Emu(sys.argv[1]); e.p.quiet = False
prog = collections.Counter()
e.uc.hook_add(UC_HOOK_MEM_WRITE, lambda uc, a, addr, s, v, d: prog.update([addr & ~0xFFF]) if 0x08000000 <= addr < 0x08100000 else None)
e.uc.reg_write(UC_ARM_REG_SP, 0x2001FF00); e.run(e.vec(1), max_chunks=60)
erases = collections.Counter()
for _, pc, k, a, s, v in e.p.log:
    if k == 'W' and a == 0x40023C10 and v & 0x2 and v & 0x10000:   # SER + STRT
        erases[(v >> 3) & 0x1F] += 1
    elif k == 'W' and a == 0x40023C10 and v & 0x2:
        erases[('SER-set', (v >> 3) & 0x1F)] += 1
print('sector erases (SNB):', dict(erases))
print('flash pages programmed (4K granularity):', ', '.join(f'0x{a:08x}x{n}' for a, n in sorted(prog.items())))
