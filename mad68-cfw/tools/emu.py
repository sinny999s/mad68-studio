"""Run the stock MAD68 HE app firmware in Unicorn and record peripheral access.

The vendor .bin is a 14-byte header followed by an STM32F4 image linked at
0x08020000. Nothing here talks to the keyboard; it only executes the file on an
emulated Cortex-M4 with a permissive register model, so polling loops on
"ready" flags complete and init code runs through to the main loop.

Usage: python emu.py <firmware.bin> [--json out.json]
"""
from __future__ import annotations

import collections
import json
import struct
import sys

from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS
from unicorn.arm_const import (UC_ARM_REG_PC, UC_ARM_REG_SP, UC_ARM_REG_LR,
                               UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                               UC_ARM_REG_R3, UC_ARM_REG_R12)

HDR = 14
BASE = 0x08020000
RET_STUB = 0x0FFF0000  # function-call return trap


class Periph:
    """Register file with a few status bits forced 'ready'."""

    TIM_CNT = frozenset(b + 0x24 for b in (
        0x40010000, 0x40000000, 0x40000400, 0x40000800, 0x40000C00, 0x40001000,
        0x40001400, 0x40010400, 0x40014000, 0x40014400, 0x40014800, 0x40001800,
        0x40001C00, 0x40002000))

    def __init__(self) -> None:
        self.tim_cnt: dict[int, int] = {}
        self.regs: dict[int, int] = {}
        self.log: list[tuple] = []          # (seq, pc, 'R'/'W', addr, size, value)
        self.seq = 0
        self.uc: Uc | None = None
        self.cyccnt = 0
        self.systick = 0
        self.adc_value = 2048
        self.quiet = False

    def pc(self) -> int:
        try:
            return self.uc.reg_read(UC_ARM_REG_PC) if self.uc else 0
        except Exception:
            return 0

    def get(self, a: int) -> int:
        v = self.regs.get(a, 0)
        # RCC
        if a == 0x40023800:
            v |= (1 << 1) | (1 << 17) | (1 << 25) | (1 << 27) | (1 << 29)
        elif a == 0x40023808:
            v = (v & ~0xC) | ((v & 3) << 2)
        elif a in (0x40023870, 0x40023874):
            v |= 0x2
        # PWR CSR: VOSRDY, ODRDY, ODSWRDY
        elif a == 0x40007004:
            v |= (1 << 14) | (1 << 16) | (1 << 17)
        # FLASH SR
        elif a == 0x40023C0C:
            v = 0
        # RTC ISR
        elif a == 0x4000280C:
            v |= 0x65
        # USB OTG HS / FS: GRSTCTL idle, no soft reset pending
        elif a in (0x40040010, 0x50000010):
            v = 0x80000000
        # ADCs: SR EOC/STRT, DR value
        elif a in (0x40012000, 0x40012100, 0x40012200):
            v |= 0x12
        elif a in (0x4001204C, 0x4001214C, 0x4001224C):
            v = self.adc_value
        # SPI SR: TXE|RXNE, not busy
        elif a in (0x40013008, 0x40003808, 0x40003C08, 0x40013408):
            v = 0x3
        # I2C SR1 / SR2
        elif a in (0x40005414, 0x40005814, 0x40005C14):
            v = 0xC7
        elif a in (0x40005418, 0x40005818, 0x40005C18):
            v = 0x3
        # USART SR
        elif a in (0x40011000, 0x40004400, 0x40004800, 0x40011400):
            v = 0xC0
        # SysTick CTRL countflag, VAL counting down
        elif a == 0xE000E010:
            v |= 1 << 16
        elif a == 0xE000E018:
            self.systick = (self.systick - 997) % 0x1000000
            v = self.systick
        # DWT CYCCNT
        elif a == 0xE0001004:
            self.cyccnt = (self.cyccnt + 20000) & 0xFFFFFFFF
            v = self.cyccnt
        # Timer counters tick on every read so delay loops finish
        elif a in self.TIM_CNT:
            self.tim_cnt[a] = (self.tim_cnt.get(a, 0) + 7) & 0xFFFFFFFF
            arr = self.regs.get(a + 8, 0xFFFFFFFF) or 0xFFFFFFFF
            v = self.tim_cnt[a] % (arr + 1)
        # DBGMCU IDCODE: STM32F405/407 rev A as the firmware expects
        elif a == 0xE0042000:
            v = 0x20006411
        elif a == 0xE000ED00:
            v = 0x410FC241
        return v & 0xFFFFFFFF

    def read(self, uc, addr, size):
        a = addr & ~3
        sh = (addr & 3) * 8
        v = (self.get(a) >> sh) & ((1 << (size * 8)) - 1)
        if not self.quiet:
            self.log.append((self.seq, self.pc(), 'R', addr, size, v))
        self.seq += 1
        return v

    def write(self, uc, addr, size, value):
        a = addr & ~3
        sh = (addr & 3) * 8
        mask = ((1 << (size * 8)) - 1) << sh
        old = self.regs.get(a, 0)
        new = (old & ~mask) | ((value << sh) & mask)
        # GPIO BSRR updates ODR instead of latching
        if 0x40020000 <= a < 0x40022400 and (a & 0x3FF) == 0x18:
            port = a - 0x18
            odr = self.regs.get(port + 0x14, 0)
            setb = new & 0xFFFF
            clrb = (new >> 16) & 0xFFFF
            self.regs[port + 0x14] = (odr | setb) & ~clrb
        else:
            self.regs[a] = new
        self.log.append((self.seq, self.pc(), 'W', addr, size, value))
        self.seq += 1


class Emu:
    def __init__(self, path: str) -> None:
        raw = open(path, 'rb').read()
        self.hdr = raw[:HDR]
        self.img = raw[HDR:]
        self.p = Periph()
        uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        self.uc = uc
        self.p.uc = uc
        uc.mem_map(0x08000000, 0x100000)
        uc.mem_write(BASE, self.img)
        uc.mem_map(0x10000000, 0x10000)             # CCM
        uc.mem_map(0x20000000, 0x40000)             # SRAM (oversized)
        uc.mem_map(0x1FFF0000, 0x10000)             # system memory
        uc.mem_write(0x1FFF7A10, bytes(range(1, 13)))   # UID
        uc.mem_write(0x1FFF7A22, struct.pack('<H', 0x400))  # 1 MB flash
        uc.mem_map(RET_STUB, 0x1000)
        uc.mem_write(RET_STUB, b'\xfe\xe7' * 8)     # b .
        for base, size in ((0x40000000, 0x80000), (0x50000000, 0x61000),
                           (0xA0000000, 0x1000), (0xE0000000, 0x100000)):
            uc.mmio_map(base, size, self._r, base, self._w, base)

    def _r(self, uc, off, size, base):
        return self.p.read(uc, base + off, size)

    def _w(self, uc, off, size, value, base):
        self.p.write(uc, base + off, size, value)

    def vec(self, n: int) -> int:
        return struct.unpack_from('<I', self.img, n * 4)[0]

    def run(self, start: int, chunk: int = 200_000, max_chunks: int = 400) -> dict:
        """Run from `start` until it returns to RET_STUB, stalls, or faults."""
        uc = self.uc
        pc = start | 1
        history = collections.deque(maxlen=12)
        for i in range(max_chunks):
            try:
                uc.emu_start(pc, RET_STUB, count=chunk)
            except UcError as e:
                return {'stop': 'fault', 'err': str(e), 'pc': uc.reg_read(UC_ARM_REG_PC), 'chunks': i}
            cur = uc.reg_read(UC_ARM_REG_PC)
            if cur & ~1 == RET_STUB:
                return {'stop': 'returned', 'chunks': i}
            history.append(cur & ~0x3F)
            if len(history) == history.maxlen and len(set(history)) == 1:
                return {'stop': 'stall', 'pc': cur, 'chunks': i}
            pc = cur | 1
        return {'stop': 'budget', 'pc': uc.reg_read(UC_ARM_REG_PC)}

    def call(self, fn: int, *args: int, **kw) -> dict:
        uc = self.uc
        for reg, val in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args):
            uc.reg_write(reg, val)
        uc.reg_write(UC_ARM_REG_LR, RET_STUB | 1)
        return self.run(fn, **kw)

    def reset(self) -> dict:
        self.uc.reg_write(UC_ARM_REG_SP, 0x2001FF00)
        self.uc.reg_write(UC_ARM_REG_LR, RET_STUB | 1)
        return self.run(self.vec(1))


if __name__ == '__main__':
    e = Emu(sys.argv[1])
    res = e.reset()
    print('reset ->', {k: (hex(v) if isinstance(v, int) else v) for k, v in res.items()})
    print('mmio events:', len(e.p.log))
