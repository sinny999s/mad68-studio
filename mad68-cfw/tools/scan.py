"""Drive the stock firmware's ADC/DMA scan interrupt with tagged samples.

Each DMA buffer slot is filled with a value encoding (adc slot, mux select)
so that wherever the handler stores a sample in RAM can be traced back to the
physical sensor position it came from.
"""
from __future__ import annotations

import collections
import struct
import sys

from unicorn import UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_SP

from emu import Emu

GPIOD_ODR = 0x40020C14
DMA2_HISR = 0x40026404
DMA2_S4 = 0x40026400 + 0x10 + 0x18 * 4


def tag(slot: int, mux: int) -> int:
    return 0x400 + slot * 0x40 + mux


def main(path: str, iters: int = 40) -> None:
    e = Emu(path)
    e.p.quiet = True
    e.uc.reg_write(UC_ARM_REG_SP, 0x2001FF00)
    e.run(e.vec(1), max_chunks=60)
    e.p.quiet = False

    m0ar = e.p.regs[DMA2_S4 + 0xC]
    ndtr = e.p.regs[DMA2_S4 + 4]
    slots = 7
    reps = ndtr // slots
    handler = e.vec(16 + 60)
    print(f'DMA buffer 0x{m0ar:08x} x{ndtr} ({reps} reps x {slots} slots), IRQ60 handler 0x{handler:08x}')

    writes: list[tuple[int, int, int]] = []

    def on_write(uc, access, addr, size, value, data):
        if 0x20000000 <= addr < 0x20020000 and size in (2, 4):
            writes.append((addr, size, value & 0xFFFF))

    h = e.uc.hook_add(UC_HOOK_MEM_WRITE, on_write)
    seq = []
    odr_hist: list[tuple[int, ...]] = []
    where: dict[tuple[int, int], set[int]] = collections.defaultdict(set)
    for it in range(iters):
        mux = (e.p.regs.get(GPIOD_ODR, 0) >> 12) & 0xF
        buf = b''.join(struct.pack('<H', tag(s, mux)) for _ in range(reps) for s in range(slots))
        e.uc.mem_write(m0ar, buf)
        e.p.regs[DMA2_HISR] = (1 << 5) | (1 << 4)
        writes.clear()
        e.uc.reg_write(UC_ARM_REG_SP, 0x2001F000)
        r = e.call(handler, max_chunks=5)
        after = (e.p.regs.get(GPIOD_ODR, 0) >> 12) & 0xF
        seq.append((mux, after, r['stop']))
        odr_hist.append(tuple(e.p.regs.get(0x40020014 + 0x400 * k, 0) for k in range(5)))
        for addr, size, v in writes:
            if 0x400 <= v < 0x400 + slots * 0x40 and (v & 0x30) == 0:
                s, m = (v - 0x400) >> 6, v & 0xF
                where[(s, m)].add(addr)
    e.uc.hook_del(h)
    print('mux select sequence (before->after, stop):', seq[:20])
    print('output-pin states after each handler call (A,B,C,D,E ODR):')
    for i, st in enumerate(odr_hist):
        print(f'  {i:2}: ' + ' '.join(f'{p}={v:04x}' for p, v in zip('ABCDE', st)))
    other = collections.Counter((a & ~3, k) for (_, _, k, a, _, _) in e.p.log)
    print('peripheral accesses during handler calls:',
          ', '.join(f'{k}:0x{a:08x}x{n}' for (a, k), n in other.most_common(25)))
    print(f'tagged samples traced to RAM: {len(where)} (slot,mux) positions')
    for (s, m), addrs in sorted(where.items()):
        print(f'  slot{s} mux{m:2}: ' + ' '.join(f'0x{a:08x}' for a in sorted(addrs)))


if __name__ == '__main__':
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 40)
