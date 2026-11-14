"""Emulate the custom firmware and check it against the stock firmware.

Runs the libhmk MAD68 build on an emulated Cortex-M4 with the same register
model used to study the stock firmware, feeds it this keyboard's real resting
Hall sensor values (backup/adc-rest.json), simulates the TIM10/ADC/DMA/SysTick
interrupts, and checks:

  1. image: linked for sector 5 only, sane vector table, fits
  2. boot: same GPIO/clock config as stock, ADC rows/columns, USB PHY setup
  3. scan: columns powered one at a time in the stock order, baselines match
     the real keyboard, no key reads as pressed at rest
  4. keys: a press in either magnet polarity registers, release clears it
  5. Fn+Ins (SP_BOOT) hands over to the vendor bootloader the stock way
  6. unconfirmed build without USB: falls back HS->FS, then bootloader
  7. confirmed build without USB: never leaves the application
  8. HardFault: hands over to the vendor bootloader
  9. flash: nothing outside sectors 6/7 is ever erased or written
 10. RGB: stock-identical LED output, Fn keys, reactive, settings journal
 11. RGB raw HID commands
 12. lighting v5: second colour, direction, per-key colours, every effect,
     key depth, ripple, heatmap, Caps Lock / Fn layer / profile indicators,
     idle dimming, typed CRC-checked records, v4 migration, compaction
 13. controller v6: XInput descriptor, interfaces opened through the real
     TinyUSB path inside the FIFO budget, stick/trigger/button output, Fn+G
     on/off with lighting feedback, no stuck keys or buttons across a switch,
     GET_GAMEPAD / SET_GAMEPAD_ACTIVE / SET_PROFILE, Fn+G added to old keymaps
 14. restart (v6.1): the bootloader flag left by the vendor bootloader is
     cleared at start-up, and COMMAND_REBOOT clears it before resetting, as
     stock does, so a restart comes back to the app instead of update mode
 15. lighting v7: Fire (rises, tapers, pressed keys feed it), Digital Rain
     (falls, faster from a deeper press), Spiral (by angle, turns), Reactive
     Cross (row and column only), Ripple reaching further from deeper presses
 16. still frames (v7.1): with the lighting off, or on Solid / Custom /
     Gradient, a frame is drawn once and not again until something on it
     changes (settings, Caps Lock, Fn layer, idle fade, key colours)
 17. stick v7.2: last pressed wins on each stick axis (order, hand-back,
     own depth, dead zone, both axes, Fn+G reset), snappy and cancel unchanged
 18. firmware info (v7.2): GET_FIRMWARE_INFO reports the release, build ID and
     marker, and the marker is in the image for the app to read from files
 19. key scan speed (v7.3): settle time per speed, register-level scan, the
     measured scan rate, rest values captured again after a change (held keys
     keep theirs), the setting saved and restored, stalled scan re-armed
 20. timing test (v7.4 test build): probes measure at another timing while the
     keys stay on their old readings, the parallel read on ADC1/ADC2/ADC3
     (sequences, DMA streams, start order, every key from its own sensor),
     going live only when the readings stay put, and giving a timing up when
     an ADC is missing, dies, or the scan stops
 21. v7.4: scan speeds 4-6 (three ADCs) are asked for through a probe at
     start-up and on SET_SCAN, refused or dropped for one ADC at 8 us when the
     readings do not agree, an ADC is missing or dies, or the scan stops;
     smoothing levels 1-3 run once per reading (median of three, then a
     step of 1/8, 1/4 or 1/2, with a rise band), drop a stray reading,
     change without moving anything, are saved; delay measured on presses
 22. PC sleep (v7.5): the LEDs go dark (one all-off frame, then nothing drawn)
     while the USB bus is suspended after a working connection, and come back
     as they were on resume (host resume, remote wake-up by a key, or a bus
     reset); an idle bus at start-up darkens nothing; settings and flash are
     untouched; idle dimming carries on where it was; the fail-safe neither
     fires on a suspended keyboard nor is held off on a bus that never
     enumerates
 23. v7.5: the paired read (ADC2 converts the bottom row twice per column):
     every key from its own sensor, a stray reading on one of the two
     conversions dropped before the keys see it, counted and logged with a
     timestamp; pin activity and pin override diagnostics
 24. v7.5 main loop: a key whose filtered value did not change is left alone,
     and a changed actuation point still reaches a key held still within a
     few milliseconds, with and without Rapid Trigger, at every kind of
     smoothing
 25. v7.6 typing pause (PAUSE_INPUT): keys held when it starts are released,
     nothing typed, no layer, profile or Fn+Ins action, controller centred,
     while key readings and calibration carry on; it lasts while the host
     repeats it and ends by itself when the time runs out; a key held when
     it ends stays silent until let go; bad requests refused; nothing saved

Usage: python verify_cfw.py <firmware.elf> <firmware raw .bin> <nm.exe> <dir with backup/ and tools/>
"""
from __future__ import annotations

import collections
import json
import re
import struct
import subprocess
import sys
import zlib
from pathlib import Path

from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_MEM_WRITE
from unicorn.arm_const import (UC_ARM_REG_PC, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_R0,
                               UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R12,
                               UC_ARM_REG_XPSR, UC_ARM_REG_APSR, UC_ARM_REG_FPSCR, UC_ARM_REG_PRIMASK,
                               UC_ARM_REG_D0, UC_ARM_REG_D1, UC_ARM_REG_D2, UC_ARM_REG_D3,
                               UC_ARM_REG_D4, UC_ARM_REG_D5, UC_ARM_REG_D6, UC_ARM_REG_D7)

FP_REGS = (UC_ARM_REG_D0, UC_ARM_REG_D1, UC_ARM_REG_D2, UC_ARM_REG_D3, UC_ARM_REG_D4,
           UC_ARM_REG_D5, UC_ARM_REG_D6, UC_ARM_REG_D7, UC_ARM_REG_FPSCR)

from emu import Periph

APP = 0x08020000
STUB_BL = 0x08000200          # stub vendor bootloader entry ('b .')
STUB_SP = 0x20020000
TRAP = 0x0FFF0000             # exception-return trap page
FAKE_EXC_RETURN = TRAP | 9    # bit3=thread, bit2=MSP, thumb
ROWS, COLS = 5, 15
ROW_CH = {3: 0, 2: 1, 1: 2, 0: 3, 7: 4}            # ADC channel -> row
COL_PINS = [('A', 8), ('A', 9), ('A', 10), ('A', 11), ('A', 12), ('A', 15), ('C', 10), ('C', 11),
            ('C', 12), ('D', 2), ('B', 3), ('B', 4), ('B', 5), ('B', 6), ('B', 7)]
PORT = {p: 0x40020000 + 0x400 * i for i, p in enumerate('ABCDEFGHI')}
DMA2 = 0x40026400
DMA2_S0 = DMA2 + 0x10
DMA2_S1 = DMA2 + 0x10 + 0x18
DMA2_S2 = DMA2 + 0x10 + 0x30
TIM10 = 0x40014400
ADC1 = 0x40012000
ADC2 = 0x40012100
ADC3 = 0x40012200
# ADC number -> its registers, the DMA2 stream and request channel that serve
# it on an STM32F407 (RM0090 table 43), and that stream's TCIF bit in LISR
ADC_DMA = {1: (ADC1, DMA2_S0, 0, 1 << 5), 2: (ADC2, DMA2_S2, 1, 1 << 21), 3: (ADC3, DMA2_S1, 2, 1 << 11)}
OTG = 0x40040000
FLASH_CR = 0x40023C10
SECTORS = [(0x08000000, 16), (0x08004000, 16), (0x08008000, 16), (0x0800C000, 16),
           (0x08010000, 64), (0x08020000, 128), (0x08040000, 128), (0x08060000, 128)]

results: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> bool:
    results.append((bool(ok), what))
    print(('  PASS ' if ok else '  FAIL ') + what)
    return bool(ok)


class CfwPeriph(Periph):
    """Register model with RCC ready flags that follow their enable bits."""

    def __init__(self):
        super().__init__()
        self.started: set[int] = set()         # ADCs with a sequence started
        self.idr: dict = {}                    # test hook: GPIO port base -> f(read number) -> input levels
        self.idr_reads = 0
        self.adc_absent = False                # test hook: no ADC2/ADC3 on this chip
        self.erases: list[int] = []
        self.gintsts = 0

    def get(self, a):
        if self.adc_absent and ADC2 <= a < ADC3 + 0x100:
            return 0
        if self.idr and a & 0x3FF == 0x10 and a & ~0x3FF in self.idr:
            self.idr_reads += 1
            return self.idr[a & ~0x3FF](self.idr_reads)
        if a == 0x40023800:
            v = self.regs.get(a, 0x83)
            v = (v & ~((1 << 1) | (1 << 17) | (1 << 25) | (1 << 27)))
            v |= ((v >> 0) & 1) << 1          # HSIRDY = HSION
            v |= ((v >> 16) & 1) << 17        # HSERDY = HSEON
            v |= ((v >> 24) & 1) << 25        # PLLRDY = PLLON
            v |= ((v >> 26) & 1) << 27        # PLLI2SRDY = PLLI2SON
            return v
        if a in (OTG + 0x40, OTG + 0x44, OTG + 0x48, OTG + 0x4C, OTG + 0x50):
            return 0                          # clone: ID/capability regs unknown -> 0
        if a == OTG + 0x14:                   # GINTSTS: device mode; test may inject events
            return self.gintsts
        if a == 0xE0001004:                   # DWT CYCCNT: advanced by Cfw.step, not per read
            return self.regs.get(a, 0)
        return super().get(a)

    def write(self, uc, addr, size, value):
        super().write(uc, addr, size, value)
        a = addr & ~3
        for n, (base, _, _, _) in ADC_DMA.items():
            if a == base + 8 and value & (1 << 30):
                self.started.add(n)
                self.regs[a] &= ~(1 << 30)    # SWSTART clears itself as the sequence starts
        if self.adc_absent and ADC2 <= a < ADC3 + 0x100:
            self.regs.pop(a, None)
        if a == DMA2 + 8:                     # LIFCR: write 1 to clear the LISR flag
            self.regs[DMA2] = self.regs.get(DMA2, 0) & ~value
        if a == FLASH_CR and value & (1 << 16) and value & 2:     # STRT with SER
            sector = (self.regs.get(FLASH_CR, 0) >> 3) & 0xF
            self.erases.append(sector)
            base, kb = SECTORS[sector]
            uc.mem_write(base, b'\xff' * (kb * 1024))


class Cfw:
    def __init__(self, image: bytes, syms: dict, rest: list[list[int]], journal: bytes = b''):
        self.syms = syms
        self.rest = rest
        self.press: dict[tuple[int, int], int] = {}
        self.p = CfwPeriph()
        uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        self.uc = uc
        self.p.uc = uc
        uc.mem_map(0x08000000, 0x80000)
        # Sentinel pattern in vendor bootloader + stock settings + unused
        # areas so any stray write is visible; sectors 6/7 erased.
        uc.mem_write(0x08000000, b'\xa5' * 0x20000)
        uc.mem_write(0x08040000, b'\xff' * 0x40000)
        uc.mem_write(0x08000000, struct.pack('<II', STUB_SP, STUB_BL | 1))
        uc.mem_write(STUB_BL, b'\xfe\xe7')
        uc.mem_write(APP, image)
        if journal:
            uc.mem_write(0x08040000, journal)
        self.flash_before = bytes(uc.mem_read(0x08000000, 0x80000))
        uc.mem_map(0x10000000, 0x10000)
        uc.mem_map(0x20000000, 0x40000)
        uc.mem_map(0x1FFF0000, 0x10000)
        uc.mem_write(0x1FFF7A10, bytes(range(1, 13)))
        uc.mem_write(0x1FFF7A22, struct.pack('<H', 0x200))
        uc.mem_map(TRAP, 0x1000)
        uc.mem_write(TRAP, b'\xfe\xe7' * 16)
        for base, size in ((0x40000000, 0x80000), (0x50000000, 0x61000), (0xE0000000, 0x100000)):
            uc.mmio_map(base, size, lambda u, o, s, b: self.p.read(u, b + o, s), base,
                        lambda u, o, s, v, b: self.p.write(u, b + o, s, v), base)
        # Cortex-M4 bit-band aliases (HAL uses them for single RCC/peripheral bits)
        uc.mmio_map(0x42000000, 0x2000000, self._bb_periph_read, None, self._bb_periph_write, None)
        uc.mmio_map(0x22000000, 0x2000000, self._bb_sram_read, None, self._bb_sram_write, None)
        self.flash_writes: list[int] = []
        uc.hook_add(UC_HOOK_MEM_WRITE, self._on_flash_write, begin=0x08000000, end=0x0807FFFF)
        self.p.quiet = True
        self.drop_adc = False                  # test hook: conversions never complete
        self.dead_adcs: set[int] = set()       # test hook: these ADCs never deliver a result
        # test hook: (parallel read, settle ticks, sample code, row, col) -> counts added
        self.timing_effect = None
        # test hook: (ADC number, rank in its sequence, row, col) -> counts added to that conversion
        self.rank_effect = None
        self.col_log: list[int | None] = []
        self.conv_log = collections.deque(maxlen=4096)   # (column, {ADC: [channels]})
        self.ticks = 0
        self.in_bootloader = False
        self.fault: str | None = None
        vt = struct.unpack_from('<II', image, 0)
        uc.reg_write(UC_ARM_REG_SP, vt[0])
        self.pc = vt[1]

    @staticmethod
    def _bb(off, base):
        word = base + ((off >> 5) & ~3)
        bit = ((off >> 5) & 3) * 8 + ((off >> 2) & 7)
        return word, bit

    def _bb_periph_read(self, uc, off, size, data):
        word, bit = self._bb(off, 0x40000000)
        return (self.p.get(word) >> bit) & 1

    def _bb_periph_write(self, uc, off, size, value, data):
        word, bit = self._bb(off, 0x40000000)
        cur = self.p.regs.get(word, 0)
        new = (cur | (1 << bit)) if value & 1 else (cur & ~(1 << bit))
        self.p.write(uc, word, 4, new)

    def _bb_sram_read(self, uc, off, size, data):
        word, bit = self._bb(off, 0x20000000)
        return (struct.unpack('<I', uc.mem_read(word, 4))[0] >> bit) & 1

    def _bb_sram_write(self, uc, off, size, value, data):
        word, bit = self._bb(off, 0x20000000)
        cur = struct.unpack('<I', uc.mem_read(word, 4))[0]
        new = (cur | (1 << bit)) if value & 1 else (cur & ~(1 << bit))
        uc.mem_write(word, struct.pack('<I', new))

    def _on_flash_write(self, uc, access, addr, size, value, data):
        self.flash_writes.append(addr)

    # helpers --------------------------------------------------------------
    def r8(self, sym, off=0):
        return self.uc.mem_read(self.syms[sym] + off, 1)[0]

    def r16(self, addr):
        return struct.unpack('<H', self.uc.mem_read(addr, 2))[0]

    def r32(self, addr):
        return struct.unpack('<I', self.uc.mem_read(addr, 4))[0]

    def active_cols(self):
        return [i for i, (p, b) in enumerate(COL_PINS) if not (self.p.regs.get(PORT[p] + 0x14, 0) >> b) & 1]

    def is_pressed(self, key):
        return self.r8('key_matrix', key * 10 + 9) != 0

    # execution --------------------------------------------------------------
    def _run(self, start, until, count):
        try:
            self.uc.emu_start(start | 1, until, count=count)
        except UcError as e:
            self.fault = f'{e} at 0x{self.uc.reg_read(UC_ARM_REG_PC):08x}'
            raise

    def run_thread(self, n):
        self._run(self.pc, 0, n)
        # never interrupt inside an IT block
        for _ in range(8):
            xpsr = self.uc.reg_read(UC_ARM_REG_XPSR)
            if not (xpsr & 0x0600FC00):
                break
            self._run(self.uc.reg_read(UC_ARM_REG_PC), 0, 1)
        self.pc = self.uc.reg_read(UC_ARM_REG_PC)
        if self.pc & ~1 == STUB_BL:
            self.in_bootloader = True

    def fire(self, vector):
        uc = self.uc
        handler = struct.unpack('<I', uc.mem_read(APP + 4 * vector, 4))[0]
        sp = uc.reg_read(UC_ARM_REG_SP)
        aligner = sp & 4
        sp = (sp - aligner) - 32
        regs = [uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                         UC_ARM_REG_R3, UC_ARM_REG_R12, UC_ARM_REG_LR)]
        frame = regs + [self.pc & ~1, 0x01000000 | (0x200 if aligner else 0)]
        # Hardware stacks the APSR flags and (lazily) S0-S15/FPSCR on
        # exception entry and restores them on return; do the same here.
        saved_flags = uc.reg_read(UC_ARM_REG_APSR)
        saved_fp = [uc.reg_read(r) for r in FP_REGS]
        uc.mem_write(sp, struct.pack('<8I', *frame))
        uc.reg_write(UC_ARM_REG_SP, sp)
        uc.reg_write(UC_ARM_REG_LR, FAKE_EXC_RETURN)
        self._run(handler, TRAP | 8, 2_000_000)
        if uc.reg_read(UC_ARM_REG_PC) & ~1 != TRAP | 8:
            raise RuntimeError(f'vector {vector} did not return')
        sp2 = uc.reg_read(UC_ARM_REG_SP)
        f = struct.unpack('<8I', uc.mem_read(sp2, 32))
        for r, v in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                         UC_ARM_REG_R12, UC_ARM_REG_LR), f[:6]):
            uc.reg_write(r, v)
        uc.reg_write(UC_ARM_REG_SP, sp2 + 32 + (4 if f[7] & 0x200 else 0))
        uc.reg_write(UC_ARM_REG_APSR, saved_flags)
        for r, val in zip(FP_REGS, saved_fp):
            uc.reg_write(r, val)
        self.pc = f[6]

    def deliver_adc(self):
        """One column: every started ADC converts its sequence and its DMA
        stream stores the results; ADC1's stream then raises its interrupt."""
        regs = self.p.regs
        if 1 not in self.p.started or not (regs.get(DMA2_S0, 0) & 1):
            return False
        started, self.p.started = self.p.started, set()
        if self.drop_adc:
            return False
        cols = self.active_cols()
        col = cols[0] if len(cols) == 1 else None
        self.col_log.append(col)
        ticks = regs.get(TIM10 + 0x2C, 0) + 1
        conv = {}
        for n in sorted(started):
            base, stream, chsel, tcif = ADC_DMA[n]
            cr2, scr = regs.get(base + 8, 0), regs.get(stream, 0)
            # on, DMA requests on, its stream enabled on the right request
            # channel and reading this ADC's data register
            if (n in self.dead_adcs or not cr2 & 1 or not cr2 & (1 << 8) or not scr & 1
                    or (scr >> 25) & 7 != chsel or regs.get(stream + 8, 0) != base + 0x4C):
                continue
            ndtr = regs.get(stream + 4, 0)
            m0 = regs.get(stream + 0xC, 0)
            length = ((regs.get(base + 0x2C, 0) >> 20) & 0xF) + 1
            sqr3, sqr2 = regs.get(base + 0x34, 0), regs.get(base + 0x30, 0)
            smpr2 = regs.get(base + 0x10, 0)
            buf, chans = {}, []
            for rank in range(length):
                ch = ((sqr3 >> (5 * rank)) if rank < 6 else (sqr2 >> (5 * (rank - 6)))) & 0x1F
                chans.append(ch)
                row = ROW_CH.get(ch)
                v = 0
                if row is not None and col is not None:
                    v = self.rest[row][col] + self.press.get((row, col), 0)
                    if self.timing_effect:
                        v += self.timing_effect(len(started) > 1, ticks, (smpr2 >> (3 * ch)) & 7, row, col)
                    if self.rank_effect:
                        v += self.rank_effect(n, rank, row, col)
                    v = max(0, min(4095, v))
                if ndtr:
                    buf[rank % ndtr] = v          # the stream wraps after NDTR results
            for i, v in buf.items():
                self.uc.mem_write(m0 + 2 * i, struct.pack('<H', v))
            conv[n] = chans
            if ndtr and length >= ndtr:
                regs[DMA2] = regs.get(DMA2, 0) | tcif
                if not scr & (1 << 8):            # only circular streams reload NDTR
                    regs[stream + 4] = 0
        self.conv_log.append((col, conv))
        if not regs.get(DMA2, 0) & (1 << 5):
            return False
        self.fire(16 + 56)                          # DMA2_Stream0
        return True

    def step(self, instructions=3000, ticks=1):
        if self.in_bootloader:
            return
        self.run_thread(instructions)
        if self.in_bootloader:
            return
        # Interrupts stay pending while the firmware has them disabled
        # (PRIMASK): let it finish that stretch first, as the chip would,
        # instead of interrupting code that cannot be interrupted
        while self.uc.reg_read(UC_ARM_REG_PRIMASK) & 1 and instructions < 30000:
            self.run_thread(50)
            instructions += 50
            if self.in_bootloader:
                return
        regs = self.p.regs
        # DWT cycle counter: one cycle per thread instruction of each step
        regs[0xE0001004] = (regs.get(0xE0001004, 0) + instructions) & 0xFFFFFFFF
        for _ in range(ticks):
            if regs.get(0xE000E010, 0) & 3 == 3:
                self.fire(15)
                self.ticks += 1
                if self.in_bootloader:
                    return
        if regs.get(TIM10, 0) & 1 and regs.get(TIM10 + 0xC, 0) & 1:
            if regs[TIM10] & 8:                     # one-pulse mode: CEN clears at the update
                regs[TIM10] &= ~1
            regs[TIM10 + 0x10] = 1
            self.fire(16 + 25)
            regs[TIM10 + 0x10] = 0
        self.deliver_adc()
        self.pc = self.pc  # resume thread where it was

    def run_until(self, cond, max_steps, **kw):
        for i in range(max_steps):
            if cond():
                return True
            self.step(**kw)
            if self.pc & ~1 == STUB_BL:
                self.in_bootloader = True
            if self.in_bootloader and not cond():
                return cond()
        return cond()

    def call(self, fn, *args, max_chunks=2):
        """Call a firmware function directly (thread context) and return r0."""
        uc = self.uc
        from unicorn import arm_const as A
        core = [getattr(A, f'UC_ARM_REG_R{i}') for i in range(13)] + [UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_APSR]
        saved = [uc.reg_read(r) for r in core] + [uc.reg_read(r) for r in FP_REGS]
        sp = (saved[13] - 0x400) & ~7
        uc.reg_write(UC_ARM_REG_SP, sp)
        for reg, val in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args):
            uc.reg_write(reg, val)
        for i, val in enumerate(args[4:]):
            uc.mem_write(sp + 4 * i, struct.pack('<I', val & 0xFFFFFFFF))
        uc.reg_write(UC_ARM_REG_LR, (TRAP + 0x10) | 1)
        self._run(fn, TRAP + 0x10, 200_000 * max_chunks)
        ret = uc.reg_read(UC_ARM_REG_R0)
        for r, val in zip(core + list(FP_REGS), saved):
            uc.reg_write(r, val)
        return ret

    def stub_usb_host(self):
        """Pretend a host is polling: HID interfaces always ready, reports accepted."""
        from unicorn import UC_HOOK_CODE

        def ret_true(uc, addr, size, data):
            uc.reg_write(UC_ARM_REG_R0, 1)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        for name in ('tud_hid_n_ready', 'tud_hid_n_report'):
            a = self.syms[name] & ~1
            self.uc.hook_add(UC_HOOK_CODE, ret_true, begin=a, end=a)

    def flash_report(self):
        after = bytes(self.uc.mem_read(0x08000000, 0x80000))
        changed = [i for i in range(0, 0x80000, 0x400) if after[i:i + 0x400] != self.flash_before[i:i + 0x400]]
        return [0x08000000 + i for i in changed]


RGB_JOURNAL = 0x08050000


def complete_frame_after(e: 'Cfw', syms: dict, t: int, limit: int = 300) -> bool:
    """Wait until a whole LED frame that the firmware started at or after its
    tick `t` is in the LED buffer. The firmware draws 8 LEDs per main-loop
    pass, which on the chip is well under a millisecond per frame but in this
    emulator (about 50x slower) about 30 ms, so a fixed wait can land
    mid-frame. A frame only starts once the previous one is finished, so the
    start of the next frame means this one is complete."""
    if not e.run_until(lambda: e.r32(syms['last_frame']) >= t, limit):
        return False
    e.run_until(lambda: False, 1)         # past frame_begin's frame_count++
    k = e.r32(syms['frame_count'])
    return e.run_until(lambda: e.r32(syms['frame_count']) > k, limit)


def enqueue(e: 'Cfw', buf_addr: int) -> bool:
    """Queue a raw HID command like the host does: the firmware holds one
    command at a time and frees the slot only once the reply has gone out, so
    retry while it is still busy with the previous one."""
    for _ in range(200):
        if e.call(e.syms['command_enqueue'], buf_addr, 64):
            return True
        e.step()
    return False


def wait_frames(e: 'Cfw', n: int = 2, limit: int = 800) -> None:
    """Wait for n new lighting frames. A frame only starts once the previous
    one is fully drawn, so after two the LEDs all show the current state.
    (The emulator runs the CPU far slower than the real 168 MHz, so a frame
    takes ~50 emulated ms here instead of under 1 ms.)"""
    fc = e.r32(e.syms['frame_count'])
    e.run_until(lambda: e.r32(e.syms['frame_count']) >= fc + n, limit)


def rgb_record(rtype: int, payload: bytes) -> bytes:
    """A v5 RGB journal record: B7, type, length, payload, CRC-32, 0xFF pad."""
    b = bytes([0xB7, rtype]) + struct.pack('<H', len(payload)) + payload
    b += struct.pack('<I', zlib.crc32(b))
    return b + b'\xff' * (-len(b) % 8)


def rgb_records(e: 'Cfw') -> list[tuple[int, bytes]]:
    """All records in the RGB journal: (type, payload); type 0 = v4 legacy,
    -1 = CRC mismatch."""
    data = bytes(e.uc.mem_read(RGB_JOURNAL, 0x10000))
    out, a = [], 0
    while a + 8 <= len(data):
        w0, w1 = struct.unpack_from('<II', data, a)
        if w0 == 0xFFFFFFFF and w1 == 0xFFFFFFFF:
            break
        if data[a] == 0xA5:
            out.append((0, data[a:a + 8]))
            a += 8
        elif data[a] == 0xB7:
            t, n = data[a + 1], struct.unpack_from('<H', data, a + 2)[0]
            crc = struct.unpack_from('<I', data, a + 4 + n)[0]
            out.append((t if crc == zlib.crc32(data[a:a + 4 + n]) else -1, data[a + 4:a + 4 + n]))
            a += (n + 8 + 7) & ~7
        else:
            a += 8
    return out


def hsv(h: int, s_: int, v: int) -> tuple[int, int, int]:
    """Same integer HSV conversion as the firmware."""
    if s_ == 0:
        return v, v, v
    region = h // 43
    rem = (h - region * 43) * 6
    p_ = (v * (255 - s_)) >> 8
    q = (v * (255 - ((s_ * rem) >> 8))) >> 8
    t = (v * (255 - ((s_ * (255 - rem)) >> 8))) >> 8
    return [(v, t, p_), (q, v, p_), (p_, v, t), (p_, q, v), (t, p_, v), (v, p_, q)][min(region, 5)]


def load_rest(path: Path, settings: Path | None = None) -> list[list[int]]:
    """Median resting value per matrix position; 4095 where there is no switch.

    Which positions hold a switch comes from the stock keymap in the settings
    backup (the read-back sample stream contains occasional glitch values, so
    it is not used for that)."""
    import statistics
    d = json.loads(path.read_text())
    S = d['samples']
    settings = settings or path.with_name('stock-settings.json')
    km = bytes.fromhex(json.loads(settings.read_text(encoding='utf-8-sig'))['keymap']['hex'])
    rest = [[4095] * COLS for _ in range(ROWS)]
    for r in range(ROWS):
        for c in range(COLS):
            if int.from_bytes(km[(r * COLS + c) * 2:][:2], 'big') == 0:
                continue
            rest[r][c] = int(statistics.median(s[r * COLS + c] for s in S))
    return rest


def load_stock_gpio(path: Path) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        if line.strip().startswith("{'"):
            port = line.split("'")[1]
            vals = [int(x, 16) for x in line.split('}')[0].split(',')[1:]]
            out[port] = dict(zip(('MODER', 'OTYPER', 'OSPEEDR', 'PUPDR', 'ODR', 'AFRL', 'AFRH'), vals))
    return out


def main():
    elf, binpath, nm, base = sys.argv[1:5]
    base = Path(base)
    image = Path(binpath).read_bytes()
    syms = {}
    for line in subprocess.run([nm, elf], capture_output=True, text=True).stdout.splitlines():
        parts = line.split()
        if len(parts) == 3:
            syms[parts[2]] = int(parts[0], 16)
    rest = load_rest(base / 'backup' / 'adc-rest.json')
    stock = load_stock_gpio(base / 'tools' / 'stock_gpio.txt')
    sys.path.insert(0, str(base / 'tools'))
    import pack
    sym = syms['board_firmware_build_id']
    off = (sym - APP) if APP <= sym < 0x08040000 else (syms['_sidata'] - APP) + (sym - syms['_sdata'])
    build_id = struct.unpack('<I', image[off:off + 4])[0]
    keys = [(r, c) for r in range(ROWS) for c in range(COLS) if rest[r][c] < 4095]
    kidx = {rc: i for i, rc in enumerate(keys)}

    print('[1] image')
    probs = pack.check_app_image(image + b'\xff' * (-len(image) % 4))
    check(not probs, f'static image checks ({len(image)} bytes, build id 0x{build_id:08x}) {probs or ""}')
    check(len(keys) == 68, f'68 populated matrix positions from the real keyboard ({len(keys)})')

    print('[2] boot (unconfirmed build)')
    e = Cfw(image, syms, rest)
    ok = e.run_until(lambda: e.r8('baseline_ready') == 1 and e.p.regs.get(OTG + 0x18, 0) != 0, 3000)
    check(ok, f'reaches main loop with scan running and USB core started (ticks={e.ticks}, fault={e.fault})')
    # Stock's own LED setup (v1.06 @ 0x0802BC14) later turns PC9 into TIM3_CH4
    # (AF2, no pull); compare against stock after that step.
    stock['C']['MODER'] = (stock['C']['MODER'] & ~(3 << 18)) | (2 << 18)
    stock['C']['PUPDR'] &= ~(3 << 18)
    stock['C']['AFRH'] = (stock['C']['AFRH'] & ~(0xF << 4)) | (2 << 4)
    for p in 'ABCDE':
        b = PORT[p]
        for name, off in (('MODER', 0), ('OTYPER', 4), ('OSPEEDR', 8), ('PUPDR', 0xC), ('AFRL', 0x20), ('AFRH', 0x24)):
            got = e.p.regs.get(b + off, 0)
            check(got == stock[p][name], f'GPIO{p} {name} = stock (0x{got:08x} vs 0x{stock[p][name]:08x})')
    col_mask = {p: sum(1 << b for (pp, b) in COL_PINS if pp == p) for p in 'ABCD'}
    for p in 'ABCDE':
        got = e.p.regs.get(PORT[p] + 0x14, 0) | col_mask.get(p, 0)
        want = stock[p]['ODR'] | col_mask.get(p, 0)
        check(got == want, f'GPIO{p} ODR = stock outside the column lines (0x{got:04x} vs 0x{want:04x})')
    check(e.p.regs.get(0x40023804) == 0x07405410, f'PLLCFGR = stock 0x07405410 (0x{e.p.regs.get(0x40023804, 0):08x})')
    cfgr = e.p.regs.get(0x40023808, 0)
    check((cfgr & 3) == 2 and (cfgr >> 10 & 7) == 5 and (cfgr >> 13 & 7) == 4 and (cfgr >> 4 & 0xF) == 0,
          f'CFGR: SYSCLK=PLL, AHB/1, APB1/4, APB2/2 (0x{cfgr:08x})')
    check((e.p.regs.get(0x40023C00, 0) & 0xF) == 5, 'flash latency 5 wait states')
    sqr1, sqr3 = e.p.regs.get(ADC1 + 0x2C, 0), e.p.regs.get(ADC1 + 0x34, 0)
    chans = [(sqr3 >> (5 * i)) & 0x1F for i in range(5)]
    check(((sqr1 >> 20) & 0xF) + 1 == 5 and chans == [3, 2, 1, 0, 7], f'ADC sequence rows PA3,PA2,PA1,PA0,PA7 ({chans})')
    smpr2 = e.p.regs.get(ADC1 + 0x10, 0)
    check(all(((smpr2 >> (3 * ch)) & 7) == 2 for ch in (0, 1, 2, 3, 7)), 'ADC sample time 28 cycles on row channels (as stock)')
    check(((e.p.regs.get(0x40012300 + 4, 0) >> 16) & 3) == 1, 'ADC clock PCLK2/4 (as stock)')
    w = [(i, a, v) for i, (_, _, k, a, _, v) in enumerate(e.p.log) if k == 'W' and OTG <= a < OTG + 0x1000]
    first_core = next((i for i, a, v in w if a in (OTG + 0xC, OTG + 0x10)), None)
    phy_writes = [(a - OTG, v) for i, a, v in w if OTG + 0x200 <= a < OTG + 0x228 and (first_core is None or i < first_core)]
    phy = {off: e.p.regs.get(OTG + off, 0) for off in (0x200, 0x204, 0x210, 0x224)}
    check(phy == {0x200: 1, 0x204: 1, 0x210: 1, 0x224: 5} and len(phy_writes) == 4,
          f'stock vendor PHY block writes (+0x200/204/210/224 = 1/1/1/5, byte-wide) before core setup {phy_writes}')
    pre = [(a - OTG, v) for i, a, v in w[:12] if a in (OTG + 0xC, OTG + 0x800, OTG + 0xE00, OTG + 0x38)]
    check(pre[:4] == [(0xC, 0x40002400), (0x800, 0x02200000), (0xE00, 0), (0x38, 0x00210000)],
          f'GUSBCFG/DCFG/PCGCCTL/GCCFG preset to stock values in stock order {pre[:4]}')
    for ev in (1 << 12, 1 << 13):             # host: bus reset, then enumeration done (HS)
        e.p.gintsts = ev
        e.fire(16 + 77)
    e.p.gintsts = 0
    check(e.p.regs.get(OTG + 0x24, 0) and e.p.regs.get(OTG + 0x28, 0) & 0xFFFF < 320,
          f'FIFOs allocated inside 1280 bytes (GRXFSIZ 0x{e.p.regs.get(OTG + 0x24, 0):x}, DIEPTXF0 0x{e.p.regs.get(OTG + 0x28, 0):08x})')
    gusb = e.p.regs.get(OTG + 0xC, 0)
    check(not (gusb & (1 << 6)) and not (gusb & (1 << 4)) and not (gusb & (1 << 3)) and ((gusb >> 10) & 0xF) == 9,
          f'USB GUSBCFG: UTMI+ HS PHY, 8-bit, TRDT 9 like stock 0x40002400 (0x{gusb:08x})')
    gccfg = e.p.regs.get(OTG + 0x38, 0)
    check(gccfg & (1 << 16) and gccfg & (1 << 21), f'USB GCCFG: PWRDWN + NOVBUSSENS like stock 0x00210000 (0x{gccfg:08x})')
    check((e.p.regs.get(OTG + 0x800, 0) & 3) == 0, 'USB DCFG: high-speed device like stock')

    print('[3] scan')
    e.col_log.clear()
    e.run_until(lambda: len(e.col_log) >= 45, 2000)
    seq = e.col_log[:45]
    start = seq.index(0) if 0 in seq else 0
    want = [(start_i % 15) for start_i in range(len(seq))]
    rotated = seq[start:start + 30]
    check(None not in seq, 'exactly one column powered at every conversion')
    check(rotated == list(range(15)) * 2, f'column order PA8..PB7 as stock ({rotated[:15]})')
    bl = [e.r16(syms['baseline'] + 2 * i) for i in range(68)]
    err = max(abs(bl[kidx[rc]] - rest[rc[0]][rc[1]]) for rc in keys)
    check(err <= 2, f'per-key baselines match the real keyboard rest values (max error {err})')
    e.run_until(lambda: False, 200)
    pressed = [i for i in range(68) if e.is_pressed(i)]
    check(not pressed, f'no key pressed at rest ({pressed})')

    print('[4] keys (confirmed build, stub USB host accepting reports)')
    e = Cfw(image, syms, rest, journal=struct.pack('<I', build_id))
    e.stub_usb_host()
    e.run_until(lambda: e.r8('baseline_ready') == 1, 3000)
    e.run_until(lambda: False, 200)
    check(not [i for i in range(68) if e.is_pressed(i)], 'no key pressed at rest (confirmed build)')
    for (rc, delta, label) in (((1, 2), -900, 'W, reading falls when pressed'),
                               ((4, 6), +900, 'Space, reading rises when pressed')):
        k = kidx[rc]
        e.press[rc] = delta
        e.run_until(lambda: e.is_pressed(k), 400)
        check(e.is_pressed(k), f'{label}: registers as pressed')
        others = [i for i in range(68) if i != k and e.is_pressed(i)]
        check(not others, f'{label}: no other key affected ({others})')
        e.press.pop(rc)
        e.run_until(lambda: not e.is_pressed(k), 400)
        check(not e.is_pressed(k), f'{label}: release clears it')

    print('[5] Fn + Ins (SP_BOOT)')
    e.press[(4, 10)] = -900
    e.run_until(lambda: e.is_pressed(kidx[(4, 10)]), 300)
    e.press[(0, 14)] = -900
    e.run_until(lambda: e.in_bootloader, 600)
    check(e.in_bootloader, 'reaches the vendor bootloader entry')
    check_handoff(e)
    bad = [hex(a) for a in e.flash_report() if not (0x08040000 <= a < 0x08080000)]
    check(not bad, f'no flash changed outside sectors 6/7 {bad}')

    print('[6] unconfirmed build, USB never enumerates')
    e = Cfw(image, syms, rest)
    e.run_until(lambda: e.ticks >= 4100, 6000, ticks=4)
    check(e.r8('usb_use_hs') == 0 and e.r8('usb_fallback_done') == 1, 'after 4 s: restarts USB on the full-speed PHY')
    gusb = e.p.regs.get(OTG + 0xC, 0)
    check(gusb & (1 << 6), f'full-speed fallback selects PHYSEL=1 (0x{gusb:08x})')
    check(not e.in_bootloader, 'still in the application during the full-speed attempt')
    e.run_until(lambda: e.in_bootloader, 3000, ticks=4)
    check(e.in_bootloader and 8900 <= e.ticks <= 9100, f'after ~9 s: hands over to the vendor bootloader (t={e.ticks} ms)')
    check_handoff(e)
    bad = [hex(a) for a in e.flash_report() if not (0x08040000 <= a < 0x08080000)]
    check(not bad, f'flash changes only in sectors 6/7 {bad}')
    check(not [s for s in e.p.erases if s < 6], f'no erase below sector 6 ({e.p.erases})')

    print('[7] confirmed build, USB never enumerates')
    e = Cfw(image, syms, rest, journal=struct.pack('<I', build_id))
    e.run_until(lambda: e.ticks >= 12000, 6000, ticks=4)
    check(not e.in_bootloader and e.ticks >= 12000, f'stays in the application for 12 s (t={e.ticks})')
    check(e.r8('usb_use_hs') == 1 and e.r8('failsafe_armed') == 0, 'keeps high speed, fail-safe disarmed')
    e2 = Cfw(image, syms, rest, journal=struct.pack('<I', build_id ^ 0x5A5A5A5A))
    e2.run_until(lambda: e2.r8('baseline_ready') == 1 and e2.p.regs.get(OTG + 0x18, 0) != 0, 3000)
    check(e2.r8('usb_use_hs') == 0 and e2.p.regs.get(OTG + 0xC, 0) & (1 << 6),
          'build confirmed at full speed comes straight up on the full-speed PHY')

    print('[8] HardFault')
    e = Cfw(image, syms, rest, journal=struct.pack('<I', build_id))
    e.run_until(lambda: e.r8('baseline_ready') == 1, 3000)
    e.fire(3)
    e.run_until(lambda: e.in_bootloader, 50)
    check(e.in_bootloader, 'fault handler returns into the bootloader hand-off')
    check_handoff(e)

    print('[10] RGB (confirmed build, stub USB host)')
    gamma = json.loads((base / 'backup' / 'stock-gamma.json').read_text())
    led_map = json.loads((base / 'backup' / 'stock-led-map.json').read_text())
    e = Cfw(image, syms, rest, journal=struct.pack('<I', build_id))
    e.stub_usb_host()
    e.run_until(lambda: e.r8('baseline_ready') == 1 and e.p.regs.get(OTG + 0x18, 0) != 0, 3000)
    e.run_until(lambda: False, 120)
    R = e.p.regs
    T3 = 0x40000400
    tim = {n: R.get(T3 + o, 0) for n, o in (('CR1', 0), ('DIER', 0xC), ('CCMR1', 0x18), ('CCMR2', 0x1C),
                                           ('CCER', 0x20), ('PSC', 0x28), ('ARR', 0x2C))}
    check(tim == {'CR1': 0x85, 'DIER': 0x100, 'CCMR1': 0x6868, 'CCMR2': 0x6868, 'CCER': 0x1000, 'PSC': 0, 'ARR': 1049},
          f'TIM3 identical to stock (PSC 0, ARR 1049, PWM1, CC4E, UDE, CR1 0x85): {tim}')
    S2 = 0x40026000 + 0x10 + 0x18 * 2
    dma = (R.get(S2, 0), R.get(S2 + 4, 0), R.get(S2 + 8, 0), R.get(S2 + 0xC, 0), R.get(S2 + 0x14, 0))
    check(dma == (0x0A032D41, 1856, 0x40000440, syms['rgb_slots'], 0x21),
          f'DMA1 Stream2 identical to stock (ch5 circular 16-bit, 1856 slots -> TIM3_CCR4): ' + ' '.join(hex(x) for x in dma))
    gc = 0x40020800
    pc9 = ((R[gc] >> 18) & 3, (R.get(gc + 0x24, 0) >> 4) & 0xF, (R.get(gc + 4, 0) >> 9) & 1,
           (R.get(gc + 8, 0) >> 18) & 3, (R.get(gc + 0xC, 0) >> 18) & 3)
    check(pc9 == (2, 2, 0, 3, 0), f'PC9 = AF2 push-pull very-high-speed no-pull, as stock {pc9}')

    def slots(n=1856):
        return struct.unpack('<%dH' % n, e.uc.mem_read(syms['rgb_slots'], 2 * n))

    def leds():
        sl = slots(68 * 24)
        out = []
        for i in range(68):
            bits = [1 if v == 67 else 0 if v == 29 else None for v in sl[i * 24:(i + 1) * 24]]
            if None in bits:
                out.append(None)
                continue
            byte = lambda k: sum(b << (7 - j) for j, b in enumerate(bits[k * 8:(k + 1) * 8]))
            out.append((byte(1), byte(0), byte(2)))   # (R, G, B) from wire G, R, B
        return out

    check(all(v == 0 for v in slots()[68 * 24:]), '224 trailing latch slots are zero, as stock')
    sp = e.uc.reg_read(UC_ARM_REG_SP)
    e.call(syms['rgb_write_grb'], 0, 0x34, 0x12, 0x56, max_chunks=2)
    stock_led0 = (29, 29, 67, 67, 29, 67, 29, 29, 29, 29, 29, 67, 29, 29, 67, 29, 29, 67, 29, 67, 29, 67, 67, 29)
    check(slots(24) == stock_led0, 'colour encoding bit-identical to stock for R=0x12 G=0x34 B=0x56')
    # That wrote LED 0 behind the firmware's back; a still frame (v7.1) is not
    # redrawn on its own, so ask for one
    e.uc.mem_write(syms['drawn_still'], bytes([0]))
    e.run_until(lambda: False, 150)
    want = (gamma[97], 0, 0)
    got = leds()
    check(all(x == want for x in got), f'all 68 LEDs show the stock look (solid #800000 @194 -> {want}); got {set(got)}')

    def tap(rc_list, steps=600):
        # Wait for the firmware to actually see each press and release
        for rc in rc_list:
            e.press[rc] = -900
        e.run_until(lambda: all(e.is_pressed(kidx[rc]) for rc in rc_list), steps)
        e.run_until(lambda: False, 10)
        for rc in rc_list:
            e.press.pop(rc)
        e.run_until(lambda: not any(e.is_pressed(kidx[rc]) for rc in rc_list), steps)
        e.run_until(lambda: False, 10)

    FN = (4, 10)
    cfg = lambda: tuple(e.uc.mem_read(syms['rgb_config'], 6))
    e.press[FN] = -900
    e.run_until(lambda: e.is_pressed(kidx[FN]), 600)
    tap([(1, 0)])                                  # Fn+Tab
    check(cfg()[0] & 1 == 0, f'Fn+Tab turns RGB off (flags {cfg()[0]:#x})')
    e.run_until(lambda: False, 150)
    check(all(x == (0, 0, 0) for x in leds()), 'all LEDs dark while off')
    for _ in range(8):
        tap([(1, 1)])                              # Fn+Q x8 (also turns back on)
    check(cfg()[4] == 210 and cfg()[0] & 1, f'Fn+Q raises brightness and stops at the stock cap 210 (val {cfg()[4]})')
    tap([(2, 2)])                                  # Fn+S: back from the first effect
    check(cfg()[1] == 18, f'Fn+S steps back from the first effect to the last (mode {cfg()[1]} = reactive cross)')
    tap([(1, 2)])                                  # Fn+W: forward again
    check(cfg()[1] == 0, f'Fn+W steps forward again (mode {cfg()[1]} = solid)')
    e.press.pop(FN)
    e.run_until(lambda: not e.is_pressed(kidx[FN]), 600)
    e.run_until(lambda: False, 150)
    check(leds()[0] == (gamma[210], 0, 0) and gamma[210] == 156,
          f'max output per channel = stock gamma[210] = 156 ({leds()[0]})')
    e.uc.mem_write(syms['rgb_config'] + 1, bytes([4]))   # reactive (effect keys checked above)
    e.run_until(lambda: False, 40)
    wait_frames(e)
    base = leds()
    d_led = led_map[2 * 15 + 3]

    def brighter_now():
        lit = leds()
        return [i for i in range(68) if lit[i] and base[i] and sum(lit[i]) > sum(base[i]) + 30]
    e.press[(2, 3)] = -900                         # press D
    e.run_until(lambda: bool(brighter_now()), 600)
    brighter = brighter_now()
    lit_now = leds()
    check(brighter == [d_led], f'reactive: pressing D lights exactly its LED #{d_led} ({brighter}; '
          + ', '.join(f'#{i} {base[i]}->{lit_now[i]}' for i in sorted(set(brighter) | {d_led})) + ')')
    e.press.pop((2, 3))
    e.run_until(lambda: False, 700, ticks=4)        # > 2 s save delay
    recs = rgb_records(e)
    cfgs = [pl for t, pl in recs if t == 1]
    last = cfgs[-1] if cfgs else b''
    check(bool(cfgs) and -1 not in [t for t, _ in recs] and last[4] == 210 and last[1] == 4 and last[0] & 2,
          f'settings saved as CRC-checked records in the RGB journal at 0x08050000 ({len(recs)} records, last {last[:6].hex()})')
    bad = [hex(a) for a in e.flash_report() if not (0x08040000 <= a < 0x08080000)]
    check(not bad, f'RGB never writes flash outside sectors 6/7 {bad}')

    print('[11] RGB raw HID commands (0x60 GET / 0x61 SET)')
    e.press.clear()
    e.run_until(lambda: False, 50)
    scratch = (syms['_ebss'] + 0x100) & ~3

    def command(payload):
        buf = bytes(payload) + bytes(64 - len(payload))
        e.uc.mem_write(scratch, buf)
        e.uc.mem_write(syms['out_buf'], bytes(64))
        ok = enqueue(e, scratch)
        e.run_until(lambda: e.uc.mem_read(syms['out_buf'], 1)[0] != 0, 400)
        return bool(ok), bytes(e.uc.mem_read(syms['out_buf'], 64))

    ok, r = command([0x60])
    check(ok and r[0] == 0x60 and r[7:11] == bytes([19, 210, 68, 2]) and r[18:20] == bytes([6, 68]),
          f'GET_RGB replies with settings and limits (19 effects, max 210, 68 LEDs, v2, 6 directions, 68 keys): {r[:20].hex(" ")}')
    ok, r = command([0x61, 1, 2, 100, 200, 250, 50])
    check(r[0] == 0x61 and r[1:7] == bytes([1, 2, 100, 200, 210, 50]),
          f'SET_RGB applies settings and caps brightness 250 -> 210: {r.hex(" ")}')
    check(tuple(e.uc.mem_read(syms['rgb_config'], 6))[1:] == (2, 100, 200, 210, 50), 'SET_RGB updates the live config')
    ok, r = command([0x61, 1, 19, 0, 0, 0, 0])
    check(r[0] == 0xFF and tuple(e.uc.mem_read(syms['rgb_config'], 6))[1] == 2,
          f'SET_RGB with an invalid effect is refused and changes nothing ({r[0]:#x})')
    ok, r = command([0x6F])
    check(r[0] == 0xFF, f'unknown board command answered as unknown ({r[0]:#x})')
    e.run_until(lambda: False, 700, ticks=4)
    cfgs = [pl for t, pl in rgb_records(e) if t == 1]
    check(cfgs and cfgs[-1][1:6] == bytes([2, 100, 200, 210, 50]), f'SET_RGB result saved to flash ({cfgs[-1][:6].hex() if cfgs else None})')

    lighting_v5(e, image, syms, rest, build_id, Path(sys.argv[4]), gamma, led_map, keys, kidx, command, leds, tap, FN)
    gamepad = gaming_v6(image, syms, rest, build_id, led_map, keys, kidx)
    restart_v61(image, syms, rest, build_id)
    lighting_v7(image, syms, rest, build_id, Path(sys.argv[4]), led_map, keys, kidx)
    still_frames_v71(image, syms, rest, build_id, led_map, keys, kidx)
    stick_v72(gamepad, syms, kidx)
    firmware_info_v72(image, syms, rest, build_id)
    scan_speed_v73(image, syms, rest, build_id, keys, kidx)
    timing_test_v74(image, syms, rest, build_id, keys, kidx)
    scan_v74(image, syms, rest, build_id, keys, kidx, Path(sys.argv[4]))
    sleep_lights_v75(image, syms, rest, build_id, Path(sys.argv[4]), led_map, keys, kidx)
    paired_v75(image, syms, rest, build_id, keys, kidx)
    main_loop_v75(image, syms, rest, build_id, keys, kidx)
    input_pause_v76(image, syms, rest, build_id, keys, kidx)

    print('[9] flash integrity across all runs: checked per scenario above')
    failed = [w for ok, w in results if not ok]
    print(f'\n{len(results) - len(failed)}/{len(results)} checks passed')
    return 1 if failed else 0


def lighting_v5(e, image, syms, rest, build_id, base, gamma, led_map, keys, kidx, command, leds, tap, FN):
    """[12] Firmware v5 lighting, continuing on the emulator `e` from [10]/[11]."""
    print('[12] lighting v5 (effects, per-key colours, indicators, idle, storage)')
    kb = json.loads((base / 'firmware' / 'keyboards' / 'mad68he' / 'keyboard.json').read_text())
    cfgh = (base / 'firmware' / 'keyboards' / 'mad68he' / 'config.h').read_text()
    led_x = [int(v) for v in re.search(r'#define RGB_LED_X \{([^}]*)\}', cfgh).group(1).split(',')]
    led_y = [int(v) for v in re.search(r'#define RGB_LED_Y \{([^}]*)\}', cfgh).group(1).split(',')]
    key_to_led = [led_map[r * COLS + c] for r, c in keys]
    led_of = lambda rc: key_to_led[kidx[rc]]
    conf = lambda: bytes(e.uc.mem_read(syms['rgb_config'], 16))
    run = lambda ms: e.run_until(lambda: False, ms)
    lum = lambda c: sum(c)

    ok, r = command([0x60])
    check(r[11:20] == bytes([128, 255, 0, 0, 7, 0, 0, 6, 68]),
          f'GET_RGB v2 extras: second colour, direction, idle off, all indicators on (white): {r[11:20].hex(" ")}')
    ok, r = command([0x61, 1, 11, 0, 255, 200, 128, 2, 170, 255, 1, 5, 7, 85, 255])
    check(r[0] == 0x61 and conf()[1:13] == bytes([11, 0, 255, 200, 128, 170, 255, 1, 5, 7, 85, 255]),
          f'SET_RGB with the extended block sets colour 2, direction, idle and indicators: {conf().hex(" ")}')
    ok, r = command([0x61, 1, 0, 10, 255, 200, 128])
    check(conf()[1:3] == bytes([0, 10]) and conf()[6:13] == bytes([170, 255, 1, 5, 7, 85, 255]),
          'SET_RGB without the extended block (older apps) keeps the v5 settings')
    for bad, why in (([0x61, 1, 0, 0, 0, 0, 0, 2, 0, 0, 6, 0, 0, 0, 0], 'direction 6'),
                     ([0x61, 1, 0, 0, 0, 0, 0, 2, 0, 0, 0, 121, 0, 0, 0], 'idle 121 min'),
                     ([0x61, 1, 19, 0, 0, 0, 0], 'effect 19')):
        before = conf()
        ok, r = command(bad)
        check(r[0] == 0xFF and conf() == before, f'SET_RGB refuses {why} and changes nothing')

    # Per-key colours
    want = [((k * 37) & 255, (k * 91 + 7) & 255, (255 - k * 3) & 255) for k in range(68)]
    set_replies = []
    for first in range(0, 68, 20):
        n = min(20, 68 - first)
        ok_set, r_set = command([0x63, first, n] + [v for k in range(first, first + n) for v in want[k]])
        set_replies.append((ok_set, r_set[:3].hex(' ')))
    got = []
    for first in range(0, 68, 20):
        ok, r = command([0x62, first])
        got += [tuple(r[3 + 3 * i:6 + 3 * i]) for i in range(r[2])]
    bad_keys = [(k, want[k], got[k] if k < len(got) else None) for k in range(68) if k >= len(got) or got[k] != want[k]]
    check(got == want, 'SET_RGB_KEYS / GET_RGB_KEYS round-trip all 68 key colours in 20-key reports'
          + (f' ({len(got)} read back; first differences {bad_keys[:3]}; SET replies {set_replies})' if bad_keys else ''))
    for bad, why in (([0x63, 0, 0], 'count 0'), ([0x63, 0, 21], 'count 21'),
                     ([0x63, 60, 9], 'a range past the last key'), ([0x62, 68], 'reading past the last key')):
        ok, r = command(bad)
        check(r[0] == 0xFF, f'per-key command refuses {why}')
    command([0x61, 1, 5, 0, 255, 210, 128, 2, 170, 255, 0, 0, 7, 85, 255])    # Custom, idle off
    complete_frame_after(e, syms, e.r32(syms['uwTick']))
    exp = [None] * 68
    for k in range(68):
        exp[key_to_led[k]] = tuple(gamma[v * 210 // 255] for v in want[k])
    got = leds()
    diff = [(i, got[i], exp[i]) for i in range(68) if got[i] != exp[i]]
    check(got == exp, 'Custom effect: every key shows its own colour through the stock gamma (brightness 210)'
          + (f' ({len(diff)} LEDs differ, {got.count(None)} mid-write; first {diff[:3]}; tick {e.ticks})' if diff else ''))

    # Every effect renders
    frames, bad = [], []
    for mode in range(19):
        command([0x61, 1, mode, 30, 255, 150, 128])
        run(150)
        lit = leds()
        if e.fault or e.in_bootloader or None in lit:
            bad.append(mode)
        frames.append(tuple(lit))
    check(not bad and len(set(frames)) >= 17,
          f'all 19 effects render complete frames without faults ({len(set(frames))} distinct looks, bad {bad})')

    # Key depth: brighter and closer to colour 2 the deeper a key is pressed
    command([0x61, 1, 6, 0, 255, 210, 128, 2, 85, 255, 0, 0, 7, 85, 255])
    run(400)
    D = (2, 3)
    d_led, far = led_of(D), led_of((0, 0))
    rest_lv = leds()
    e.press[D] = -450
    run(80)
    half = leds()
    e.press[D] = -900
    run(80)
    full = leds()
    e.press.pop(D)
    run(400)
    check(lum(rest_lv[d_led]) < lum(half[d_led]) < lum(full[d_led]) and rest_lv[far] == half[far] == full[far],
          f'Key depth: D glows brighter the deeper it goes ({rest_lv[d_led]} -> {half[d_led]} -> {full[d_led]}), other keys unchanged')
    check(full[d_led][1] > full[d_led][0], f'Key depth: fully pressed key reaches the second colour ({full[d_led]})')

    # Ripple: a ring spreads from the pressed key
    command([0x61, 1, 7, 0, 255, 210, 128, 2, 170, 255, 0, 0, 7, 85, 255])
    run(1500)
    before = leds()
    G = (2, 5)
    g_led = led_of(G)
    e.press[G] = -900
    e.run_until(lambda: e.is_pressed(kidx[G]), 300)
    t0 = e.ticks
    e.run_until(lambda: e.ticks >= t0 + 120, 400)
    mid = leds()
    e.press.pop(G)
    dist = lambda i: max(abs(led_x[i] - led_x[g_led]), abs(led_y[i] - led_y[g_led]))
    changed = [i for i in range(68) if lum(mid[i]) > lum(before[i])]
    check(changed and all(dist(i) <= 70 for i in changed) and mid[far] == before[far],
          f'Ripple: 120 ms after a press only keys around G light up ({len(changed)} keys, farthest {max(map(dist, changed)) if changed else None})')

    # Heatmap: typed keys run hot
    command([0x61, 1, 12, 0, 255, 210, 128])
    run(60)
    for _ in range(5):
        tap([D])
    run(40)
    lit = leds()
    hot, cold = lit[d_led], lit[led_of((2, 9))]
    check(hot[0] > hot[2] and cold[2] > cold[0], f'Heatmap: D (typed 5x) runs hot {hot}, untouched L stays cool {cold}')

    # Caps Lock indicator (host LED report on the keyboard interface)
    command([0x61, 1, 0, 0, 255, 150, 128, 2, 170, 255, 0, 0, 7, 85, 255])   # solid red, green indicators
    run(60)
    caps, a_led = led_of((2, 0)), led_of((2, 1))
    buf = (syms['_ebss'] + 0x200) & ~3

    def host_leds(v):
        e.uc.mem_write(buf, bytes([v]))
        e.call(syms['tud_hid_set_report_cb'], 0, 0, 2, buf, 1)

    host_leds(0x02)
    run(60)
    on = leds()
    check(on[caps][1] > on[caps][0] and on[a_led][0] > on[a_led][1],
          f'Caps Lock on: the Caps key shows the indicator colour {on[caps]}, others the effect {on[a_led]}')
    command([0x61, 0, 0, 0, 255, 150, 128])
    run(60)
    off = leds()
    lit_off = {i: off[i] for i in range(68) if i != caps and off[i] != (0, 0, 0)}
    check(off[caps][1] > 0 and not lit_off,
          f'Caps Lock indicator stays visible with the lighting switched off (Caps {off[caps]}, others lit {lit_off})')
    command([0x61, 1, 0, 0, 255, 150, 128])
    host_leds(0x00)
    run(60)
    check(leds()[caps] == leds()[a_led], 'Caps Lock off: the Caps key is back to the effect')

    # Fn layer indicator
    fn_map = kb['keymap'][1]
    bound = [k for k, kc in enumerate(fn_map) if kc not in ('KC_TRNS', 'KC_NO')]
    e.press[FN] = -900
    e.run_until(lambda: e.is_pressed(kidx[FN]), 600)
    run(60)
    wait_frames(e)
    held = leds()
    normal = hsv(0, 255, 255)
    ok_bound = all(held[key_to_led[k]][1] > held[key_to_led[k]][0] for k in bound)
    ok_dim = all(lum(held[key_to_led[k]]) < gamma[150] for k in range(68) if k not in bound)
    e.press.pop(FN)
    e.run_until(lambda: not e.is_pressed(kidx[FN]), 600)
    run(60)
    not_ind = [(k, held[key_to_led[k]]) for k in bound if not held[key_to_led[k]][1] > held[key_to_led[k]][0]]
    not_dim = [(k, held[key_to_led[k]]) for k in range(68) if k not in bound and lum(held[key_to_led[k]]) >= gamma[150]]
    check(ok_bound and ok_dim and len(bound) > 10,
          f'Fn held: the {len(bound)} keys bound on the Fn layer show the indicator colour, the rest dim'
          + (f' (not indicator: {not_ind[:6]}; not dim: {not_dim[:6]})' if not (ok_bound and ok_dim) else ''))
    check(leds()[a_led] == (gamma[normal[0] * 150 // 255], 0, 0), 'Fn released: back to the effect')

    # Profile indicator: number key of the new profile flashes for 1.5 s
    ee = e.r32(syms['eeconfig'])
    e.uc.mem_write(ee + 148, bytes([1]))       # current_profile = 1 (as a profile key would)
    run(80)
    two = led_of((0, 2))
    flash = leds()
    run(1600)
    after = leds()
    e.uc.mem_write(ee + 148, bytes([0]))
    run(1600)
    check(flash[two][1] > flash[two][0] and after[two] == after[a_led],
          f'profile switch: key 2 flashes the indicator colour {flash[two]}, then returns to the effect')

    # Idle dimming
    command([0x61, 1, 0, 0, 255, 150, 128, 2, 170, 255, 0, 1, 7, 85, 255])   # idle after 1 min
    run(40)
    tick = e.r32(syms['uwTick'])
    e.uc.mem_write(syms['rgb_last_activity'], struct.pack('<I', (tick - 61000) & 0xFFFFFFFF))
    # About 1 s at the real 16 ms frame rate; the emulated main loop is slower
    e.run_until(lambda: e.r8('rgb_idle_level') == 0, 6000)
    run(150)
    check(all(x == (0, 0, 0) for x in leds()), 'idle: the lighting fades out after the set time without typing')
    tap([D])
    run(150)
    check(all(lum(x) > 0 for x in leds()), 'idle: any key press brings the lighting straight back')

    # Persistence of settings and per-key colours across a restart
    command([0x61, 1, 5, 0, 255, 210, 128, 2, 170, 255, 0, 0, 7, 85, 255])
    run(2600)
    recs = rgb_records(e)
    cfgs = [pl for t, pl in recs if t == 1]
    keyrecs = [pl for t, pl in recs if t == 2]
    flat = bytes(v for c in want for v in c)
    check(keyrecs and keyrecs[-1] == flat and cfgs[-1][1] == 5 and -1 not in [t for t, _ in recs],
          f'per-key colours and settings saved as CRC-checked records ({len(recs)} records)')
    journal = bytes(e.uc.mem_read(0x08040000, 0x20000))
    e2 = Cfw(image, syms, rest, journal=journal)
    e2.stub_usb_host()
    e2.run_until(lambda: e2.r8('baseline_ready') == 1, 3000)
    check(bytes(e2.uc.mem_read(syms['rgb_config'], 16))[1:13] == cfgs[-1][1:13]
          and bytes(e2.uc.mem_read(syms['rgb_key_colors'], 204)) == flat,
          'after a restart, the settings and all 68 key colours come back from flash')
    bad = [hex(a) for a in e.flash_report() if not (0x08040000 <= a < 0x08080000)]
    check(not bad, f'v5 lighting never writes flash outside sectors 6/7 {bad}')

    # Settings saved by firmware v4 carry over
    legacy = bytes([0xA5, 0x03, 2, 100, 200, 150, 60])
    cs = 0x5A
    for b in legacy:
        cs ^= b
    j = struct.pack('<I', build_id) + b'\xff' * (0x10000 - 4) + legacy + bytes([cs])
    e3 = Cfw(image, syms, rest, journal=j)
    e3.stub_usb_host()
    e3.run_until(lambda: e3.r8('baseline_ready') == 1, 3000)
    c3 = bytes(e3.uc.mem_read(syms['rgb_config'], 16))
    check(c3[:7] == bytes([3, 2, 100, 200, 150, 60, 228]) and c3[10] == 7,
          f'v4 settings carry over (rainbow wave, hue 100, brightness 150; reactive flash colour kept): {c3.hex(" ")}')
    e3.run_until(lambda: e3.ticks >= 3000, 4000)
    r3 = rgb_records(e3)
    check(r3 and r3[0][0] == 0 and any(t == 1 and pl[0] == c3[0] | 4 and pl[1:6] == c3[1:6] for t, pl in r3),
          'and are re-saved in the new record form after 2 s (plus the Fn+G-added flag)')

    # A full journal is compacted: one erase, latest settings and colours kept
    cfg_pl = bytes([7, 0, 0, 255, 100, 128, 128, 255, 0, 0, 7, 0, 0, 0, 0, 0])
    keyrec = rgb_record(2, flat)
    fill = rgb_record(1, cfg_pl)
    n = (0x10000 - len(keyrec)) // len(fill)
    rgb = keyrec + fill * n
    j = struct.pack('<I', build_id) + b'\xff' * (0x10000 - 4) + rgb
    e4 = Cfw(image, syms, rest, journal=j)
    e4.stub_usb_host()
    e4.run_until(lambda: e4.r8('baseline_ready') == 1 and e4.p.regs.get(OTG + 0x18, 0) != 0, 3000)
    e4.run_until(lambda: False, 120)          # main loop running (commands served)
    scratch = (syms['_ebss'] + 0x100) & ~3
    e4.uc.mem_write(scratch, bytes([0x61, 1, 3, 9, 255, 120, 128]) + bytes(57))
    e4.uc.mem_write(syms['out_buf'], bytes(64))
    enqueue(e4, scratch)
    t0 = e4.ticks
    e4.run_until(lambda: e4.ticks >= t0 + 2600, 3200)
    r4 = rgb_records(e4)
    c4 = [pl for t, pl in r4 if t == 1]
    k4 = [pl for t, pl in r4 if t == 2]
    check(len(rgb) > 0x10000 - 24 and e4.p.erases.count(6) == 1,
          f'full RGB journal ({len(rgb)} bytes used): sector 6 erased exactly once to compact it ({e4.p.erases})')
    check(e4.r32(0x08040000) == build_id, 'compaction keeps the build confirmation')
    check(c4 and c4[-1][1:6] == bytes([3, 9, 255, 120, 128]) and k4 == [flat] and len(r4) == 2,
          f'compaction keeps exactly the latest settings and the per-key colours ({len(r4)} records)')
    bad = [hex(a) for a in e4.flash_report() if not (0x08040000 <= a < 0x08080000)]
    check(not bad, f'compaction never writes flash outside sectors 6/7 {bad}')


def gaming_v6(image, syms, rest, build_id, led_map, keys, kidx):
    """[13] Firmware v6 controller (XInput) support."""
    print('[13] controller v6 (XInput descriptor, FIFOs, sticks, Fn+G, commands)')
    from unicorn import UC_HOOK_CODE
    W, A_, S, D, E_, SPACE, G, FN = (1, 2), (2, 1), (2, 2), (2, 3), (1, 3), (4, 6), (2, 5), (4, 10)
    KC_TRNS, KC_D, KC_F, KC_W, KC_SPC = 0x01, 0x05, 0x07, 0x18, 0x2A
    GP = {'A': 1, 'LS_UP': 16, 'LS_DOWN': 17, 'LS_LEFT': 18, 'LS_RIGHT': 19, 'RT': 25}
    SET_OPTIONS, GET_KEYMAP, SET_KEYMAP, SET_GP_BUTTONS, SET_GP_OPTIONS = 10, 128, 129, 137, 139
    GET_GAMEPAD, SET_GAMEPAD_ACTIVE, SET_PROFILE = 0x64, 0x65, 0x66
    key_to_led = [led_map[r * COLS + c] for r, c in keys]

    def make(stub_controller):
        e = Cfw(image, syms, rest, journal=struct.pack('<I', build_id))
        e.stub_usb_host()
        sent = []
        if stub_controller:
            # A host that has the controller interface configured and polls it
            def ret(val):
                def h(uc, addr, size, data):
                    uc.reg_write(UC_ARM_REG_R0, val)
                    uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
                return h

            def xfer(uc, addr, size, data):
                ep, buf, n = (uc.reg_read(r) for r in (UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
                if ep == 0x84:
                    sent.append(bytes(uc.mem_read(buf, n)))
                uc.reg_write(UC_ARM_REG_R0, 1)
                uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
            for name, h in (('tud_mounted', ret(1)), ('tud_suspended', ret(0)), ('usbd_edpt_busy', ret(0)),
                            ('usbd_edpt_claim', ret(1)), ('usbd_edpt_release', ret(1)), ('usbd_edpt_xfer', xfer)):
                a = syms[name] & ~1
                e.uc.hook_add(UC_HOOK_CODE, h, begin=a, end=a)
        e.run_until(lambda: e.r8('baseline_ready') == 1 and e.p.regs.get(OTG + 0x18, 0) != 0, 3000)
        e.run_until(lambda: False, 120)
        return e, sent

    def command(e, payload):
        scratch = (syms['_ebss'] + 0x100) & ~3
        e.uc.mem_write(scratch, bytes(payload) + bytes(64 - len(payload)))
        e.uc.mem_write(syms['out_buf'], bytes(64))
        enqueue(e, scratch)
        e.run_until(lambda: e.uc.mem_read(syms['out_buf'], 1)[0] != 0, 400)
        return bytes(e.uc.mem_read(syms['out_buf'], 64))

    def config_descriptor(e):
        ptr = e.call(syms['tud_descriptor_configuration_cb'], 0)
        total = struct.unpack('<H', e.uc.mem_read(ptr + 2, 2))[0]
        d = bytes(e.uc.mem_read(ptr, total))
        itfs, eps, i, cur = [], [], 0, None
        while i < len(d):
            if d[i + 1] == 4:
                cur = (d[i + 5], d[i + 6], d[i + 7])
                itfs.append((ptr + i, cur))
            elif d[i + 1] == 5:
                eps.append({'itf': cur, 'addr': d[i + 2], 'size': struct.unpack_from('<H', d, i + 4)[0],
                            'interval': d[i + 6]})
            i += d[i]
        return ptr, d, itfs, eps

    def state(e):
        r = command(e, [GET_GAMEPAD])
        return r[0], r[1], r[2], r[3], r[4], r[5:19]

    def stick(report):           # (buttons, lt, rt, lx, ly, rx, ry) from a 20-byte report
        return struct.unpack_from('<HBBhhhh', report, 2)

    def leds(e):
        sl = struct.unpack('<%dH' % (68 * 24), e.uc.mem_read(syms['rgb_slots'], 2 * 68 * 24))
        out = []
        for i in range(68):
            bits = [1 if v == 67 else 0 for v in sl[i * 24:(i + 1) * 24]]
            byte = lambda k: sum(b << (7 - j) for j, b in enumerate(bits[k * 8:(k + 1) * 8]))
            out.append((byte(1), byte(0), byte(2)))
        return out

    def hold(e, rc, delta=-900):
        e.press[rc] = delta
        e.run_until(lambda: False, 40)

    def let_go(e, rc):
        e.press.pop(rc, None)
        e.run_until(lambda: not e.is_pressed(kidx[rc]), 400)
        e.run_until(lambda: False, 40)

    def typed(e, rc):
        return e.r8('active_keycodes', kidx[rc])

    # --- descriptor and the real interface-open path (no controller stubs) ---
    e6, _ = make(False)
    _, _, itfs, _ = config_descriptor(e6)
    check(len(itfs) == 3 and all(c[0] == 3 for _, c in itfs),
          f'controller interface off by default: 3 HID interfaces {[c for _, c in itfs]}')
    r = command(e6, [SET_OPTIONS, 5, 0])
    check(r[0] == SET_OPTIONS and e6.r8('xinput_active') == 1,
          'SET_OPTIONS turns the controller interface on (8 kHz kept)')
    ptr, desc, itfs, eps = config_descriptor(e6)
    xi = [(a, c) for a, c in itfs if c == (0xFF, 0x5D, 0x01)]
    xe = sorted((ep['addr'], ep['size'], ep['interval']) for ep in eps if ep['itf'] == (0xFF, 0x5D, 0x01))
    check(desc[4] == 4 and len(itfs) == 4 and len(xi) == 1 and xe == [(0x02, 32, 1), (0x84, 32, 1)],
          f'with it on: 4 interfaces, XInput (FF/5D/01) on endpoints 0x84 IN / 0x02 OUT, 32 bytes, every microframe {xe}')
    nums = sorted({ep['addr'] & 0x7F for ep in eps})
    check(max(nums) < 6 and len([ep for ep in eps if ep['addr'] & 0x80]) <= 5,
          f'endpoints fit the 6 the USB core has (EP0 + {nums})')
    for ev in (1 << 12, 1 << 13):                 # host: bus reset, enumeration done (HS)
        e6.p.gintsts = ev
        e6.fire(16 + 77)
    e6.p.gintsts = 0
    e6.run_until(lambda: False, 40)               # tud_task handles the bus reset
    opened = []
    for a, c in itfs:
        fn = syms['hidd_open'] if c[0] == 3 else syms['xinput_driver_open']
        opened.append(e6.call(fn, 1, a, ptr + len(desc) - a, max_chunks=4) & 0xFFFF)
    check(all(opened) and opened[-1] == 39,
          f'all 4 interfaces open through TinyUSB, XInput driver takes its 39 bytes {opened}')
    R = lambda off: e6.p.regs.get(OTG + off, 0)
    regions = [('RX', 0, R(0x24) & 0xFFFF), ('TX0', R(0x28) & 0xFFFF, R(0x28) >> 16)]
    for n in range(1, 6):
        v = R(0x104 + 4 * (n - 1))
        if v:
            regions.append((f'TX{n}', v & 0xFFFF, v >> 16))
    spans = sorted((start, start + size, name) for name, start, size in regions)
    overlap = any(spans[i][1] > spans[i + 1][0] for i in range(len(spans) - 1))
    top = max(end for _, end, _ in spans)
    used = sum(size for _, _, size in regions)
    check(len(regions) == 6 and not overlap and top <= 320,
          f'USB FIFOs: RX + 5 IN FIFOs, no overlap, inside the 1280-byte budget ({used} of 320 words used: '
          + ', '.join(f'{n} {a}-{b}' for a, b, n in spans) + ')')
    _, ver, flags, prof, nprof, live = state(e6)
    check(ver == 3 and flags == 7 and prof == 0 and nprof == 4,
          f'GET_GAMEPAD: protocol 3 (v7.2), interface on + opened by host + output on (flags {flags}), profile 0 of 4')
    bad = [hex(a) for a in e6.flash_report() if not (0x08040000 <= a < 0x08080000)]
    check(not bad, f'controller setup never writes flash outside sectors 6/7 {bad}')

    # --- live output (host polling the controller) ---
    e5, sent = make(True)
    command(e5, [SET_OPTIONS, 5, 0])
    for rc, b in ((W, 'LS_UP'), (S, 'LS_DOWN'), (A_, 'LS_LEFT'), (D, 'LS_RIGHT'), (SPACE, 'A'), (E_, 'RT')):
        command(e5, [SET_GP_BUTTONS, 0, kidx[rc], 1, GP[b]])
    # default curve; keyboard on, gamepad override (bound keys only drive the controller), snappy
    r = command(e5, [SET_GP_OPTIONS, 0, 4, 20, 85, 95, 165, 170, 255, 255, 0x0B])
    check(r[0] == SET_GP_OPTIONS, 'controller bindings set: WASD left stick, Space A, E right trigger, override on')
    e5.uc.mem_write(syms['endpoint_in'], bytes([0x84]))      # host opened the interface
    e5.run_until(lambda: False, 40)

    def last():
        return stick(sent[-1]) if sent else (0, 0, 0, 0, 0, 0, 0)
    hold(e5, W, -300)
    e5.run_until(lambda: last()[4] > 0, 400)
    half = last()[4]
    hold(e5, W, -900)
    e5.run_until(lambda: last()[4] >= 32000, 400)
    e5.run_until(lambda: False, 80)               # key travel settles over a few scans
    full = last()[4]
    check(0 < half < full and full == 32640,
          f'W pressed part way moves the left stick part way up ({half}), fully: all the way ({full})')
    check(typed(e5, W) == 0, f'with override on, W drives only the controller (keycode {typed(e5, W):#x})')
    hold(e5, A_, -900)
    e5.run_until(lambda: last()[3] < 0, 400)
    e5.run_until(lambda: False, 80)               # key travel settles over a few scans
    b, lt, rt, lx, ly, rx, ry = last()
    check(lx < 0 and ly > 0 and abs(abs(lx) - ly) < 600 and 22000 < ly < 24000,
          f'W+A: diagonal up-left on a round stick ({lx}, {ly})')
    let_go(e5, A_)
    let_go(e5, W)
    e5.run_until(lambda: last()[3:5] == (0, 0), 400)   # follows the keys back up
    check(last()[3:5] == (0, 0), f'released: stick back to centre {last()[3:5]}')
    hold(e5, E_, -900)
    e5.run_until(lambda: last()[2] == 255, 400)
    check(last()[2] == 255, f'E fully pressed: right trigger 255 ({last()[2]})')
    let_go(e5, E_)
    hold(e5, SPACE)
    e5.run_until(lambda: last()[0] & 0x1000, 400)
    check(last()[0] == 0x1000, f'Space: A button ({last()[0]:#06x})')
    _, _, flags, _, _, live = state(e5)
    check(flags == 7 and live == sent[-1][2:16], 'GET_GAMEPAD live state matches the report sent to the host')

    # SET_GAMEPAD_ACTIVE off while A is held: button released, Space types
    r = command(e5, [SET_GAMEPAD_ACTIVE, 0])
    e5.run_until(lambda: False, 40)
    check(r[0] == SET_GAMEPAD_ACTIVE and r[2] == 3 and last()[0] == 0,
          f'controller output off: held A released in the report (buttons {last()[0]:#x}), flags {r[2]}')
    check(typed(e5, SPACE) == KC_SPC, f'and the still-held Space now types ({typed(e5, SPACE):#x})')
    let_go(e5, SPACE)
    hold(e5, W)
    check(typed(e5, W) == KC_W and last()[4] == 0, f'output off: W types and the stick stays centred ({last()[4]})')
    # Output back on while W is held as a typing key: W must not stay held
    command(e5, [SET_GAMEPAD_ACTIVE, 1])
    e5.run_until(lambda: last()[4] == 32640, 400)
    check(typed(e5, W) == 0 and last()[4] == 32640,
          f'output on with W held: W is released from the keyboard (no stuck key) and drives the stick ({last()[4]})')
    let_go(e5, W)
    r = command(e5, [SET_GAMEPAD_ACTIVE, 2])
    check(r[0] == 0xFF, f'SET_GAMEPAD_ACTIVE with an invalid value is refused ({r[0]:#x})')

    # Fn+G toggles output, with lighting feedback on the bound keys
    def fn_g(want):
        hold(e5, FN)
        e5.run_until(lambda: e5.is_pressed(kidx[FN]), 400)
        hold(e5, G)
        e5.run_until(lambda: e5.r8('xinput_active') == want, 400)
        let_go(e5, G)
        let_go(e5, FN)
        e5.run_until(lambda: False, 60)

    w_led, l_led = key_to_led[kidx[W]], key_to_led[kidx[(2, 9)]]
    fn_g(0)
    seen_w, seen_l = set(), set()
    for _ in range(10):                           # ~0.6 s of the 1 s blink
        lit = leds(e5)
        seen_w.add(lit[w_led])
        seen_l.add(lit[l_led])
        e5.run_until(lambda: False, 30)
    red = {c for c in seen_w if c[0] > 0 and c[1] == 0 and c[2] == 0}
    check(e5.r8('xinput_active') == 0 and red and (0, 0, 0) in seen_w and (0, 0, 0) not in seen_l,
          f'Fn+G: controller output off, bound keys blink red/dark {sorted(seen_w)} '
          f'over the red effect on unbound keys {sorted(seen_l)}')
    e5.run_until(lambda: False, 1200)
    check(leds(e5)[w_led] == leds(e5)[l_led], 'the flash ends after about 1 s')
    fn_g(1)
    lit = leds(e5)
    check(e5.r8('xinput_active') == 1 and lit[w_led][1] > lit[w_led][0] and lit[l_led][0] > lit[l_led][1],
          f'Fn+G again: output back on, bound keys light green {lit[w_led]} (unbound L {lit[l_led]})')
    check(typed(e5, G) == 0 and typed(e5, FN) == 0, 'Fn+G itself types nothing')

    # SET_PROFILE: profile 1 has no bindings, so D types there
    hold(e5, D)
    e5.run_until(lambda: last()[3] > 0, 400)
    hold(e5, SPACE)
    e5.run_until(lambda: last()[0] & 0x1000, 400)
    ee = e5.r32(syms['eeconfig'])
    r = command(e5, [SET_PROFILE, 1])
    e5.run_until(lambda: False, 60)
    check(r[0] == SET_PROFILE and r[3] == 1 and e5.uc.mem_read(ee + 148, 1)[0] == 1,
          f'SET_PROFILE 1 switches profile (reply profile {r[3]})')
    check(last()[0] == 0 and last()[3] == 0, f'switching profile releases held controller input {last()[:4]}')
    let_go(e5, SPACE)
    let_go(e5, D)
    hold(e5, D)
    check(typed(e5, D) == KC_D, f'profile 1 has no controller bindings: D types ({typed(e5, D):#x})')
    let_go(e5, D)
    writes = len(e5.flash_writes)
    command(e5, [SET_PROFILE, 1])
    check(len(e5.flash_writes) == writes,
          f'SET_PROFILE to the current profile writes no flash ({len(e5.flash_writes) - writes} writes)')
    r = command(e5, [SET_PROFILE, 4])
    check(r[0] == 0xFF and e5.uc.mem_read(ee + 148, 1)[0] == 1,
          f'SET_PROFILE 4 (no such profile) is refused ({r[0]:#x})')
    command(e5, [SET_PROFILE, 0])

    # Fn+G is added once to keymaps saved by an earlier build
    gkey = kidx[G]
    command(e5, [SET_KEYMAP, 0, 1, gkey, 1, KC_TRNS])
    command(e5, [SET_KEYMAP, 2, 1, gkey, 1, KC_F])
    flags_addr = syms['rgb_config']
    e5.uc.mem_write(flags_addr, bytes([e5.uc.mem_read(flags_addr, 1)[0] & ~4 & 0xFF]))
    e5.uc.mem_write(syms['fn_keys_checked'], bytes([0]))
    e5.run_until(lambda: False, 80)
    k0 = command(e5, [GET_KEYMAP, 0, 1, gkey])[1]
    k2 = command(e5, [GET_KEYMAP, 2, 1, gkey])[1]
    cfgs = [pl for t, pl in rgb_records(e5) if t == 1]
    check(k0 == 0xDE and k2 == KC_F and e5.uc.mem_read(flags_addr, 1)[0] & 4 and cfgs and cfgs[-1][0] & 4,
          f'Fn+G added once to older keymaps where Fn+G was empty ({k0:#x}), kept where it was used ({k2:#x}), flag saved')
    bad = [hex(a) for a in e5.flash_report() if not (0x08040000 <= a < 0x08080000)]
    check(not bad, f'controller features never write flash outside sectors 6/7 {bad}')
    return {'make': make, 'command': command, 'hold': hold, 'let_go': let_go, 'stick': stick}


def restart_v61(image, syms, rest, build_id):
    """[14] Restarting returns to the app: the bootloader flag is cleared."""
    print('[14] restart v6.1 (bootloader flag cleared at start-up and before COMMAND_REBOOT)')
    FLAG, MAGIC, AIRCR = 0x2001FFFC, 0x96969696, 0xE000ED0C
    e = Cfw(image, syms, rest, journal=struct.pack('<I', build_id))
    e.stub_usb_host()
    e.uc.mem_write(FLAG, struct.pack('<I', MAGIC))     # as the bootloader leaves it
    # main loop running (USB core started), so commands are served
    e.run_until(lambda: e.r8('baseline_ready') == 1 and e.p.regs.get(OTG + 0x18, 0) != 0, 3000)
    e.run_until(lambda: False, 120)
    check(e.r32(FLAG) == 0, f'start-up clears the flag the bootloader left (now {e.r32(FLAG):#010x}), as stock does')
    check(not e.in_bootloader, 'and keeps running the app')

    # COMMAND_REBOOT with the flag set again: it must be cleared before the reset
    e.uc.mem_write(FLAG, struct.pack('<I', MAGIC))
    scratch = (syms['_ebss'] + 0x100) & ~3
    e.uc.mem_write(scratch, bytes([1]) + bytes(63))      # COMMAND_REBOOT
    enqueue(e, scratch)
    e.run_until(lambda: e.p.regs.get(AIRCR, 0) & 4, 800)
    aircr = e.p.regs.get(AIRCR, 0)
    check(aircr >> 16 == 0x05FA and aircr & 4, f'COMMAND_REBOOT requests a system reset (AIRCR {aircr:#010x})')
    check(e.r32(FLAG) == 0, f'with the bootloader flag cleared first ({e.r32(FLAG):#010x}), so the bootloader starts the app again')
    bad = [hex(a) for a in e.flash_report() if not (0x08040000 <= a < 0x08080000)]
    check(not bad, f'no flash changed outside sectors 6/7 {bad}')


def lighting_v7(image, syms, rest, build_id, base, led_map, keys, kidx):
    """[15] Firmware v7 effects."""
    print('[15] lighting v7 (Fire, Digital Rain, Spiral, Reactive Cross, pressure ripples)')
    cfgh = (base / 'firmware' / 'keyboards' / 'mad68he' / 'config.h').read_text()
    led_x = [int(v) for v in re.search(r'#define RGB_LED_X \{([^}]*)\}', cfgh).group(1).split(',')]
    led_y = [int(v) for v in re.search(r'#define RGB_LED_Y \{([^}]*)\}', cfgh).group(1).split(',')]
    key_to_led = [led_map[r * COLS + c] for r, c in keys]
    led_of = lambda rc: key_to_led[kidx[rc]]
    lum = lambda c: sum(c)

    e = Cfw(image, syms, rest, journal=struct.pack('<I', build_id))
    e.stub_usb_host()
    e.run_until(lambda: e.r8('baseline_ready') == 1 and e.p.regs.get(OTG + 0x18, 0) != 0, 3000)
    e.run_until(lambda: False, 120)
    run = lambda ms: e.run_until(lambda: False, ms)

    def command(payload):
        scratch = (syms['_ebss'] + 0x100) & ~3
        e.uc.mem_write(scratch, bytes(payload) + bytes(64 - len(payload)))
        e.uc.mem_write(syms['out_buf'], bytes(64))
        enqueue(e, scratch)
        e.run_until(lambda: e.uc.mem_read(syms['out_buf'], 1)[0] != 0, 400)
        return bytes(e.uc.mem_read(syms['out_buf'], 64))

    def leds():
        sl = struct.unpack('<%dH' % (68 * 24), e.uc.mem_read(syms['rgb_slots'], 2 * 68 * 24))
        out = []
        for i in range(68):
            bits = [1 if v == 67 else 0 for v in sl[i * 24:(i + 1) * 24]]
            byte = lambda k: sum(b << (7 - j) for j, b in enumerate(bits[k * 8:(k + 1) * 8]))
            out.append((byte(1), byte(0), byte(2)))
        return out

    def effect(mode, hue=0, sat=255, val=210, speed=128, hue2=170, sat2=255):
        return command([0x61, 1, mode, hue, sat, val, speed, 2, hue2, sat2, 0, 0, 7, 0, 0])

    def press_until_registered(rc, delta):
        e.press[rc] = delta
        e.run_until(lambda: e.is_pressed(kidx[rc]), 400)
        return e.ticks

    def release(rc):
        e.press.pop(rc, None)
        e.run_until(lambda: not e.is_pressed(kidx[rc]), 400)

    r = command([0x60])
    check(r[7] == 19, f'GET_RGB reports 19 effects ({r[7]})')

    # Fire: bright at the bottom, tapering to the top, flickering
    effect(15, hue=0, speed=200)
    run(400)
    rows = {y: [i for i in range(68) if led_y[i] == y] for y in sorted(set(led_y))}
    sums = {y: 0 for y in rows}
    frames = []
    for _ in range(8):
        run(40)
        lit = leds()
        frames.append(tuple(lit))
        for y, members in rows.items():
            sums[y] += sum(lum(lit[i]) for i in members) / len(members)
    top, bottom = sums[min(rows)], sums[max(rows)]
    check(bottom > 2 * top and bottom > 0 and len(set(frames)) >= 6,
          f'Fire: flames bright at the bottom and tapering to the top (row brightness bottom {bottom / 8:.0f} vs top {top / 8:.0f}), flickering ({len(set(frames))} of 8 frames differ)')
    five = led_of((0, 5))
    calm = [lum(leds()[five]) for _ in range(1)][0]
    e.press[(0, 5)] = -900
    run(120)
    lit = leds()
    others = [lum(lit[i]) for i in rows[min(rows)] if i != five]
    fed = lum(lit[five])
    release((0, 5))
    check(fed > 3 * (sum(others) / len(others)) and fed > calm,
          f'Fire: holding 5 (top row) feeds the flames there ({fed} vs {sum(others) / len(others):.0f} for the rest of the row)')

    # Digital Rain: a press drops a streak that falls down its column,
    # faster from a deeper press
    def fall_time(delta):
        effect(16, hue=85, speed=0)
        e.uc.mem_write(syms['rain_active'], bytes(2))   # clear the sky first
        run(60)
        col_leds = [i for i in range(68) if led_x[i] // 15 == led_x[five] // 15]
        bottom_led = max(col_leds, key=lambda i: led_y[i])   # lowest key in the column
        t0 = press_until_registered((0, 5), delta)
        ys = []
        reached = None
        for _ in range(200):
            run(10)
            lit = leds()
            on = [i for i in col_leds if lum(lit[i]) > 60]
            if on:
                ys.append(max(led_y[i] for i in on))
            if lum(lit[bottom_led]) > 60 and reached is None:
                reached = e.ticks - t0
                break
        release((0, 5))
        run(300)
        return reached, ys

    t_hard, ys = fall_time(-900)
    check(t_hard is not None and ys == sorted(ys) and len(set(ys)) >= 3,
          f'Digital Rain: pressing 5 drops a streak down its column (lowest lit row over time {sorted(set(ys))}, bottom after {t_hard} ms)')
    t_light, _ = fall_time(-520)
    check(t_hard is not None and t_light is not None and t_hard < t_light,
          f'Digital Rain: a full press falls faster than a light one ({t_hard} ms vs {t_light} ms to the bottom row)')

    # Spiral: colour by angle around the centre, turning over time
    effect(17, sat=255, speed=128)
    run(200)
    a = leds()
    run(300)
    b = leds()
    left = [i for i in range(68) if led_x[i] < 60]
    right = [i for i in range(68) if led_x[i] > 164]
    moved = sum(1 for i in range(68) if a[i] != b[i])
    check(len(set(a)) >= 25 and set(a[i] for i in left).isdisjoint(set(a[i] for i in right)) and moved > 50,
          f'Spiral: colours go round the centre ({len(set(a))} distinct, left and right sides differ) and turn ({moved} of 68 keys changed in 300 ms)')

    # Reactive Cross: a press lights its row and column only
    effect(18, hue=0, speed=128, hue2=170)
    run(800)
    before = leds()
    G = (2, 5)
    g = led_of(G)
    t0 = press_until_registered(G, -900)
    complete_frame_after(e, syms, e.r32(syms['uwTick']) + 120)
    mid = leds()
    release(G)
    changed = [i for i in range(68) if lum(mid[i]) > lum(before[i]) + 6]
    on_lines = [i for i in changed if abs(led_y[i] - led_y[g]) < 8 or abs(led_x[i] - led_x[g]) < 8]
    far_on_row = [i for i in changed if abs(led_y[i] - led_y[g]) < 8 and abs(led_x[i] - led_x[g]) >= 28]
    check(changed and len(on_lines) == len(changed) and far_on_row,
          f'Reactive Cross: pressing G lights only its row and column ({len(changed)} keys, '
          f'{len(changed) - len(on_lines)} off the lines, {len(far_on_row)} at least 2 keys along the row'
          + (f'; lit {[(i, led_x[i] - led_x[g], led_y[i] - led_y[g]) for i in changed]}, '
             f'{e.ticks - t0} ms after the press' if not far_on_row else '') + ')')

    # Ripple: a deeper press sends the ring further (brighter at the same age)
    def ring_energy(delta):
        effect(7, hue=0, speed=128, hue2=170)
        run(1500)
        quiet = leds()
        A = (2, 1)
        t0 = press_until_registered(A, delta)
        e.run_until(lambda: e.ticks >= t0 + 150, 400)
        e.press.pop(A, None)
        e.run_until(lambda: e.ticks >= t0 + 600, 800)
        lit = leds()
        release(A)
        rip = e.uc.mem_read(syms['ripples'], 12 * 8)
        newest = max((i for i in range(8) if rip[i * 12 + 8] == kidx[A]),
                     key=lambda i: struct.unpack_from('<I', rip, i * 12)[0], default=None)
        peaks.append(rip[newest * 12 + 9] if newest is not None else None)
        return sum(max(0, lum(lit[i]) - lum(quiet[i])) for i in range(68))

    peaks = []
    ring_energy(-900)                     # learn the key's full travel first
    hard = ring_energy(-900)
    light = ring_energy(-380)
    check(peaks[2] is not None and 128 <= peaks[2] <= 190 and peaks[1] >= 240 and hard > light * 1.5,
          f'Ripple: a full press sends a stronger ring than a light one (ring brightness {hard} vs {light} at the same age; press depths {peaks[1]} vs {peaks[2]} of 255)')
    bad = [hex(a) for a in e.flash_report() if not (0x08040000 <= a < 0x08080000)]
    check(not bad, f'v7 effects never write flash outside sectors 6/7 {bad}')


def still_frames_v71(image, syms, rest, build_id, led_map, keys, kidx):
    """[16] Still pictures are drawn once, then left to the DMA."""
    print('[16] still frames v7.1 (no redraw while nothing changes)')
    from unicorn import UC_HOOK_CODE
    key_to_led = [led_map[r * COLS + c] for r, c in keys]
    e = Cfw(image, syms, rest, journal=struct.pack('<I', build_id))
    e.stub_usb_host()
    writes = {'n': 0}
    a = syms['rgb_write_grb'] & ~1
    e.uc.hook_add(UC_HOOK_CODE, lambda uc, ad, sz, d: writes.__setitem__('n', writes['n'] + 1), begin=a, end=a)
    e.run_until(lambda: e.r8('baseline_ready') == 1 and e.p.regs.get(OTG + 0x18, 0) != 0, 3000)
    e.run_until(lambda: False, 120)

    def command(payload):
        scratch = (syms['_ebss'] + 0x100) & ~3
        e.uc.mem_write(scratch, bytes(payload) + bytes(64 - len(payload)))
        e.uc.mem_write(syms['out_buf'], bytes(64))
        enqueue(e, scratch)
        e.run_until(lambda: e.uc.mem_read(syms['out_buf'], 1)[0] != 0, 400)

    def leds():
        sl = struct.unpack('<%dH' % (68 * 24), e.uc.mem_read(syms['rgb_slots'], 2 * 68 * 24))
        out = []
        for i in range(68):
            bits = [1 if v == 67 else 0 for v in sl[i * 24:(i + 1) * 24]]
            byte = lambda k: sum(b << (7 - j) for j, b in enumerate(bits[k * 8:(k + 1) * 8]))
            out.append((byte(1), byte(0), byte(2)))
        return out

    def draws_over(steps):
        n0 = writes['n']
        e.run_until(lambda: False, steps)
        return writes['n'] - n0

    buf = (syms['_ebss'] + 0x200) & ~3

    def host_leds(v):
        e.uc.mem_write(buf, bytes([v]))
        e.call(syms['tud_hid_set_report_cb'], 0, 0, 2, buf, 1)

    caps = key_to_led[kidx[(2, 0)]]
    frames = lambda: e.r32(syms['frame_count'])

    # Lighting off (on an animated effect): one black frame, then nothing
    command([0x61, 0, 2, 0, 255, 150, 128])
    wait_frames(e, 3)
    f0 = frames()
    n = draws_over(300)
    check(n == 0 and frames() > f0 + 3 and all(x == (0, 0, 0) for x in leds()),
          f'lighting off: dark, and no LED is redrawn over {frames() - f0} frames ({n} LED writes)')
    host_leds(0x02)
    wait_frames(e, 3)
    on = leds()
    n = draws_over(300)
    check(on[caps][1] > 0 and n == 0,
          f'Caps Lock while off: the indicator appears {on[caps]}, then no more redraws ({n} LED writes)')
    host_leds(0x00)
    wait_frames(e, 3)
    check(all(x == (0, 0, 0) for x in leds()), 'Caps Lock off again: all dark')

    # Solid: drawn once; a new brightness is drawn
    command([0x61, 1, 0, 0, 255, 150, 128])
    wait_frames(e, 3)
    bright = leds()[0]
    n = draws_over(300)
    check(n == 0 and bright[0] > 0, f'Solid: drawn once, then left alone ({n} LED writes in 300 ms)')
    command([0x61, 1, 0, 0, 255, 60, 128])
    wait_frames(e, 3)
    dim = leds()[0]
    check(0 < dim[0] < bright[0], f'changing the brightness redraws it ({bright} -> {dim})')

    # Fn held on Solid: the layer indicator still shows
    FN = (4, 10)
    e.press[FN] = -900
    e.run_until(lambda: e.is_pressed(kidx[FN]), 600)
    wait_frames(e, 3)
    held = leds()
    e.press.pop(FN)
    e.run_until(lambda: not e.is_pressed(kidx[FN]), 600)
    wait_frames(e, 3)
    after = leds()
    tab = key_to_led[kidx[(1, 0)]]                 # Fn+Tab is bound (RGB toggle)
    check(held[tab] != after[tab] and after[tab] == after[0],
          f'Solid with Fn held: the Fn-layer keys light up {held[tab]} and go back {after[tab]}')

    # Animated effects still draw every frame
    command([0x61, 1, 2, 0, 255, 150, 128])
    wait_frames(e, 2)
    f0 = frames()
    n = draws_over(300)
    check(n >= 68 * (frames() - f0 - 1) and frames() > f0 + 3,
          f'Rainbow Wave still draws every frame ({n} LED writes over {frames() - f0} frames)')


def stick_v72(gamepad, syms, kidx):
    """[17] Last pressed wins on the controller stick (v7.2)."""
    print('[17] stick v7.2 (last pressed wins; cancel and snappy unchanged)')
    make, command, hold, let_go, stick = (gamepad[k] for k in ('make', 'command', 'hold', 'let_go', 'stick'))
    W, A_, S, D = (1, 2), (2, 1), (2, 2), (2, 3)
    GP = {'LS_UP': 16, 'LS_DOWN': 17, 'LS_LEFT': 18, 'LS_RIGHT': 19}
    SET_OPTIONS, SET_GP_BUTTONS, GET_GP_OPTIONS, SET_GP_OPTIONS = 10, 137, 138, 139
    GET_GAMEPAD, SET_GAMEPAD_ACTIVE = 0x64, 0x65
    LINEAR = [4, 20, 85, 95, 165, 170, 255, 255]
    KEYBOARD, OVERRIDE, SNAPPY, LAST = 1, 2, 8, 16
    FULL = 255 << 7

    e, sent = make(True)
    command(e, [SET_OPTIONS, 5, 0])
    for rc, b in ((W, 'LS_UP'), (S, 'LS_DOWN'), (A_, 'LS_LEFT'), (D, 'LS_RIGHT')):
        command(e, [SET_GP_BUTTONS, 0, kidx[rc], 1, GP[b]])
    e.uc.mem_write(syms['endpoint_in'], bytes([0x84]))      # host opened the interface
    e.run_until(lambda: False, 40)

    def lx():
        return stick(sent[-1])[3] if sent else 0

    def ly():
        return stick(sent[-1])[4] if sent else 0

    def wait(cond, ms=400):
        e.run_until(cond, ms)
        e.run_until(lambda: False, 80)            # key travel settles over a few scans

    def mode(flags, curve=LINEAR):
        r = command(e, [SET_GP_OPTIONS, 0, *curve, flags])
        e.run_until(lambda: False, 40)
        return r[0] == SET_GP_OPTIONS

    def release(*rcs):
        for rc in rcs:
            let_go(e, rc)
        wait(lambda: (lx(), ly()) == (0, 0))

    def distance(rc):
        return e.r8('key_matrix', kidx[rc] * 10 + 6)

    def steady(*rcs):
        # Wait until these keys' travel stops changing (the ADC filter takes
        # a while to settle on a part-way press)
        prev, same = None, 0
        for _ in range(150):
            cur = tuple(distance(rc) for rc in rcs)
            same = same + 1 if cur == prev else 0
            if same >= 5:
                break
            prev = cur
            e.run_until(lambda: False, 10)
        e.run_until(lambda: False, 40)

    ver = command(e, [GET_GAMEPAD])[1]
    check(ver == 3, f'GET_GAMEPAD reports protocol 3, so the app knows this build has last pressed wins ({ver})')
    ok = mode(KEYBOARD | OVERRIDE | LAST)
    opts = command(e, [GET_GP_OPTIONS, 0])
    check(ok and opts[1:9] == bytes(LINEAR) and opts[9] == KEYBOARD | OVERRIDE | LAST,
          f'the new option (bit 4) is saved and read back with the curve ({opts[9]:#04x})')

    # --- last pressed wins ---
    hold(e, A_)
    wait(lambda: lx() == -FULL)
    left = lx()
    hold(e, D)
    wait(lambda: lx() == FULL)
    check(left == -FULL and lx() == FULL,
          f'A held, then D: the stick goes from full left ({left}) to full right ({lx()})')
    let_go(e, D)
    wait(lambda: lx() == -FULL)
    check(lx() == -FULL, f'D let go while A is still held: back to full left ({lx()})')
    release(A_)
    hold(e, D)
    wait(lambda: lx() == FULL)
    hold(e, A_)
    wait(lambda: lx() == -FULL)
    check(lx() == -FULL, f'D held, then A: full left ({lx()}), so the order decides, not the side')
    let_go(e, A_)
    wait(lambda: lx() == FULL)
    check(lx() == FULL, f'A let go: back to D, full right ({lx()})')
    release(D)
    check((lx(), ly()) == (0, 0), f'both let go: stick centred {(lx(), ly())}')

    hold(e, A_)
    wait(lambda: lx() == -FULL)
    hold(e, D, -300)
    wait(lambda: lx() > 0)
    part = lx()
    check(0 < part < FULL,
          f'A fully held, then D part way: the stick goes right only part way ({part}), at D\'s own depth')
    release(A_, D)

    hold(e, W)
    hold(e, A_)
    steady(W, A_)
    hold(e, D)
    steady(W, A_, D)
    x, y = lx(), ly()
    check(x > 0 and y > 0 and abs(x - y) < 600 and 22000 < y < 24000,
          f'W+A held, then D: diagonal up-right on a round stick ({x}, {y})')
    hold(e, S)
    steady(W, A_, S, D)
    check(lx() > 0 and ly() < 0, f'then S as well: the up/down axis follows the last press too, down-right {(lx(), ly())}')
    release(W, A_, S, D)

    # A light touch inside the curve's start dead zone does not take over
    hold(e, D, -300)
    steady(D)
    light = distance(D)
    release(D)
    start = min(light + 10, 220)
    curve = [start, 0, start + 15, 120, start + 30, 200, 255, 255]
    mode(KEYBOARD | OVERRIDE | LAST, curve)
    hold(e, A_)
    wait(lambda: lx() == -FULL)
    hold(e, D, -300)
    steady(A_, D)
    touched, light = lx(), distance(D)
    hold(e, D)
    wait(lambda: lx() == FULL)
    check(light <= start and touched == -FULL and lx() == FULL,
          f'with the dead zone at {start}: D touched to {light} keeps A in charge ({touched}), '
          f'pressing D past it takes over ({lx()})')
    release(A_, D)

    # Turning controller output off and on forgets the order
    mode(KEYBOARD | OVERRIDE | LAST)
    hold(e, A_)
    hold(e, D)
    wait(lambda: lx() == FULL)
    command(e, [SET_GAMEPAD_ACTIVE, 0])
    e.run_until(lambda: False, 40)
    check(lx() == 0, f'controller output off with A and D held: stick centred ({lx()})')
    command(e, [SET_GAMEPAD_ACTIVE, 1])
    wait(lambda: lx() != 0)
    check(abs(lx()) == FULL, f'back on with both still held: one side wins fully, no stuck half state ({lx()})')
    release(A_, D)

    # Both options set: last pressed wins takes precedence
    mode(KEYBOARD | OVERRIDE | SNAPPY | LAST)
    hold(e, A_)
    wait(lambda: lx() == -FULL)
    hold(e, D)
    wait(lambda: lx() == FULL)
    check(lx() == FULL, f'snappy and last pressed wins both set: last pressed wins ({lx()})')
    release(A_, D)

    # --- the other two modes are unchanged ---
    mode(KEYBOARD | OVERRIDE | SNAPPY)
    hold(e, A_)
    wait(lambda: lx() == -FULL)
    hold(e, D)
    wait(lambda: False, 200)
    tie = lx()
    let_go(e, A_)
    wait(lambda: lx() == FULL)
    check(tie == -FULL and lx() == FULL,
          f'snappy (as before): both fully held is a tie that points left ({tie}); A let go: right ({lx()})')
    release(D)
    hold(e, A_)
    wait(lambda: lx() == -FULL)
    hold(e, D, -300)
    wait(lambda: False, 200)
    check(lx() == -FULL, f'snappy (as before): the deeper key wins, A full beats D part way ({lx()})')
    release(A_, D)

    mode(KEYBOARD | OVERRIDE)
    hold(e, A_)
    wait(lambda: lx() == -FULL)
    hold(e, D)
    wait(lambda: lx() == 0)
    check(lx() == 0, f'neither option (as before): A and D fully held cancel out ({lx()})')
    release(A_, D)

    bad = [hex(a) for a in e.flash_report() if not (0x08040000 <= a < 0x08080000)]
    check(not bad, f'no flash changed outside sectors 6/7 {bad}')


def firmware_info_v72(image, syms, rest, build_id):
    """[18] GET_FIRMWARE_INFO and the release marker (v7.2; release 7.6.0 now)."""
    print('[18] firmware info v7.2 (release, build ID, marker in the image)')
    GET_FIRMWARE_INFO, MARKER = 0x67, b'MAD68CFW v7.6.0'
    e = Cfw(image, syms, rest, journal=struct.pack('<I', build_id))
    e.stub_usb_host()
    e.run_until(lambda: e.r8('baseline_ready') == 1 and e.p.regs.get(OTG + 0x18, 0) != 0, 3000)
    e.run_until(lambda: False, 120)

    def command(payload):
        scratch = (syms['_ebss'] + 0x100) & ~3
        e.uc.mem_write(scratch, bytes(payload) + bytes(64 - len(payload)))
        e.uc.mem_write(syms['out_buf'], bytes([0]) + bytes([0xAA]) * 63)   # junk the reply must clear
        enqueue(e, scratch)
        e.run_until(lambda: e.uc.mem_read(syms['out_buf'], 1)[0] != 0, 400)
        return bytes(e.uc.mem_read(syms['out_buf'], 64))

    r = command([GET_FIRMWARE_INFO])
    bid = struct.unpack_from('<I', r, 4)[0]
    check(r[0] == GET_FIRMWARE_INFO and tuple(r[1:4]) == (7, 6, 0),
          f'GET_FIRMWARE_INFO reports release 7.6.0 ({tuple(r[1:4])}, reply {r[0]:#x})')
    check(bid == build_id, f'and the ID of this build ({bid:#010x} vs {build_id:#010x})')
    check(r[8:32] == MARKER + bytes(24 - len(MARKER)),
          f'and the marker, zero-padded over whatever was in the buffer ({r[8:32]!r})')
    check(image.count(MARKER + bytes(1)) == 1,
          f'the marker is in the image exactly once, so the app can read the release from a file ({image.count(MARKER)})')
    r = command([0x6E])
    check(r[0] == 0xFF, f'the next unused board command (0x6E) is still refused ({r[0]:#x})')
    bad = [hex(a) for a in e.flash_report() if not (0x08040000 <= a < 0x08080000)]
    check(not bad, f'no flash changed outside sectors 6/7 {bad}')


def scan_speed_v73(image, syms, rest, build_id, keys, kidx):
    """[19] Key scan speed (v7.3): the settle time per speed, register-level
    scan (one-pulse TIM10 -> ADC sequence -> circular DMA), the measured scan
    rate, rest values captured again after a change (held keys keep theirs),
    the setting saved and restored, and recovery from a stalled scan."""
    print('[19] key scan speed v7.3 (settle per speed, scan rate, rest values, saved, stall recovery)')
    GET, SET, SETTLE = 0x68, 0x69, [40, 28, 18, 8]
    arr = lambda us: us * 168 - 1                     # noqa: E731  TIM10 counts at 168 MHz

    def boot(journal):
        e = Cfw(image, syms, rest, journal=journal)
        e.stub_usb_host()
        e.run_until(lambda: e.r8('baseline_ready') == 1 and e.p.regs.get(OTG + 0x18, 0) != 0, 3000)
        e.run_until(lambda: False, 50)
        return e

    def command(e, payload):
        scratch = (syms['_ebss'] + 0x100) & ~3
        e.uc.mem_write(scratch, bytes(payload) + bytes(64 - len(payload)))
        e.uc.mem_write(syms['out_buf'], bytes([0]) + bytes([0xAA]) * 63)   # junk the reply must clear
        enqueue(e, scratch)
        e.run_until(lambda: e.uc.mem_read(syms['out_buf'], 1)[0] != 0, 400)
        return bytes(e.uc.mem_read(syms['out_buf'], 64))

    def busy(e):
        return command(e, [GET])[6] == 1

    def wait_capture(e):
        e.run_until(lambda: e.r8('baseline_pass') == 0xFF, 600)

    def baseline_err(e, rest_now, skip=()):
        bl = [e.r16(syms['baseline'] + 2 * i) for i in range(68)]
        return max(abs(bl[kidx[rc]] - rest_now[rc[0]][rc[1]]) for rc in keys if rc not in skip)

    R = lambda e, a: e.p.regs.get(a, 0)              # noqa: E731
    e = boot(struct.pack('<I', build_id))

    # --- defaults and the register setup ---
    r = command(e, [GET])
    check(r[0] == GET and r[1] == 2 and r[2] == 1 and r[3] == 7 and list(r[7:11]) == SETTLE and not any(r[26:32]),
          f'GET_SCAN: Standard (1), the four one-ADC speeds first with settle times 40/28/18/8 us, rest of the reply '
          f'cleared: {r[:12].hex(" ")}')
    check(R(e, TIM10 + 0x2C) == arr(28) and R(e, TIM10) & 8 and R(e, TIM10 + 0x28) == 0 and R(e, TIM10 + 0xC) & 1,
          f'TIM10: one-pulse, 28 us settle at 168 MHz (ARR {R(e, TIM10 + 0x2C)} = {arr(28)}), update interrupt')
    dcr = R(e, DMA2_S0)
    check(dcr & (1 << 8) and dcr & (1 << 4) and not dcr & (1 << 3) and R(e, DMA2_S0 + 4) == 5
          and R(e, DMA2_S0 + 8) == ADC1 + 0x4C and R(e, DMA2_S0 + 0xC) == syms['adc_buffer'],
          f'DMA2 Stream0: circular, 5 results ADC1->DR -> adc_buffer, one interrupt per sequence (CR 0x{dcr:08x})')
    cr2 = R(e, ADC1 + 8)
    check(cr2 & 1 and cr2 & (1 << 8) and cr2 & (1 << 9) and not cr2 & 2,
          f'ADC1: on, DMA with continuous requests, one sequence per start (CR2 0x{cr2:08x})')
    check(R(e, ADC1 + 4) & (1 << 26), 'ADC overrun interrupt enabled (restarts the column)')

    # --- every conversion follows a column switch and a full settle ---
    col_bits = {(PORT[p] + 0x18, 1 << (b + 16)) for p, b in COL_PINS}
    tim_isr = syms['TIM1_UP_TIM10_IRQHandler'] & ~1
    after = sorted(a & ~1 for a in syms.values() if a & ~1 > tim_isr and a < 0x08080000)[0]
    e.p.log.clear()
    e.col_log.clear()
    e.run_until(lambda: len(e.col_log) >= 32, 200)
    last_sel = last_cen = -1
    bad, starts = [], 0
    for seq, pc, k, a, size, v in e.p.log:
        if k != 'W':
            continue
        if any(a == ba and v & bit for ba, bit in col_bits):
            last_sel = seq
        elif a == TIM10 and v & 1:
            last_cen = seq
        elif a == ADC1 + 8 and v & (1 << 30) and last_sel >= 0:   # columns switched inside the window
            starts += 1
            if not (last_sel < last_cen < seq and tim_isr <= pc < after):
                bad.append(seq)
    check(starts >= 30 and not bad,
          f'each of {starts} conversions is started by the TIM10 interrupt after the column switch and settle timer ({len(bad)} not)')

    # --- measured scan rate ---
    e.run_until(lambda: False, 1000)
    r = command(e, [GET])
    rate = r[4] | r[5] << 8
    want = round(168e6 / (15 * 3000))                 # emulator: one column per 3000-cycle step
    check(abs(rate - want) <= 2, f'scan rate measured with the cycle counter over 64 scans ({rate}/s, emulated clock gives {want}/s)')

    # --- change speed: applied at once, saved, rest values captured again ---
    r = command(e, [SET, 3])
    check(r[0] == SET and r[2] == 3 and r[6] == 1 and (r[4] | r[5] << 8) == 0,
          f'SET_SCAN 3 (Fastest): reply shows it, rest capture running, rate re-measuring: {r[:8].hex(" ")}')
    check(R(e, TIM10 + 0x2C) == arr(8), f'Fastest: 8 us settle (ARR {R(e, TIM10 + 0x2C)})')
    recs = [pl for t, pl in rgb_records(e) if t == 3]
    check(recs and recs[-1] == bytes([3, 0, 0, 0]), f'saved as a type-3 record in the sector 6 journal ({recs})')
    wait_capture(e)
    check(not busy(e), 'rest capture finishes')
    check(baseline_err(e, rest) <= 2, f'rest values unchanged when the readings are (max error {baseline_err(e, rest)})')
    check(not [i for i in range(68) if e.is_pressed(i)], 'no key pressed after the change')
    n = len([1 for t, _ in rgb_records(e) if t == 3])
    command(e, [SET, 3])
    check(len([1 for t, _ in rgb_records(e) if t == 3]) == n, 'setting the same speed again writes nothing')
    r = command(e, [SET, 7])
    check(r[0] == 0xFF and command(e, [GET])[2] == 3, f'unknown speed 7 refused, speed unchanged ({r[0]:#x})')

    # A shorter settle can read every sensor slightly lower: rest values follow
    shifted = [[v - 40 if v < 4095 else v for v in row] for row in rest]
    e.rest = shifted
    command(e, [SET, 2])
    wait_capture(e)
    check(baseline_err(e, shifted) <= 2, f'readings 40 lower after a change: rest values follow (max error {baseline_err(e, shifted)})')
    e.run_until(lambda: False, 60)
    check(not [i for i in range(68) if e.is_pressed(i)], 'and no key reads as pressed')

    # A key held down during the capture keeps its old rest value
    w = (1, 2)
    e.press[w] = -900
    e.run_until(lambda: e.is_pressed(kidx[w]), 300)
    command(e, [SET, 1])
    wait_capture(e)
    bw = e.r16(syms['baseline'] + 2 * kidx[w])
    check(abs(bw - shifted[1][2]) <= 2, f'W held during the capture keeps its rest value ({bw} vs {shifted[1][2]})')
    check(baseline_err(e, shifted, skip=(w,)) <= 2, 'every other key captured as usual')
    e.press.pop(w)
    e.run_until(lambda: not e.is_pressed(kidx[w]), 300)
    check(not e.is_pressed(kidx[w]), 'W released afterwards: not pressed')
    e.rest = rest

    # --- saved speed restored after a restart ---
    command(e, [SET, 3])
    wait_capture(e)
    sector6 = bytes(e.uc.mem_read(0x08040000, 0x20000))
    e2 = boot(sector6)
    r = command(e2, [GET])
    check(r[2] == 3 and R(e2, TIM10 + 0x2C) == arr(8),
          f'after a restart: comes up at the saved speed (Fastest, ARR {R(e2, TIM10 + 0x2C)})')
    check(baseline_err(e2, rest) <= 2, 'and captures rest values at that speed')

    # --- a stalled scan is re-armed ---
    cc = lambda: e2.r32(syms['column_count'])        # noqa: E731
    e2.drop_adc = True
    e2.run_until(lambda: False, 5)
    frozen = cc()
    e2.p.log.clear()
    e2.run_until(lambda: False, 150)
    rearms = sum(1 for _, _, k, a, _, v in e2.p.log if k == 'W' and a == ADC1 + 8 and not v & 1)
    check(cc() == frozen and rearms >= 1,
          f'conversions stop: no column completes, and within 100 ms the ADC is power-cycled and re-armed ({rearms}x)')
    e2.drop_adc = False
    e2.run_until(lambda: cc() > frozen + 30, 300)
    check(cc() > frozen + 30, f'once conversions work again the scan carries on ({cc() - frozen} columns)')
    e2.run_until(lambda: False, 40)
    check(baseline_err(e2, rest) <= 2 and not [i for i in range(68) if e2.is_pressed(i)],
          'rest values intact and no key pressed after the stall')
    bad = [hex(a) for a in e.flash_report() + e2.flash_report() if not (0x08040000 <= a < 0x08080000)]
    check(not bad, f'no flash changed outside sectors 6/7 {bad}')


def timing_test_v74(image, syms, rest, build_id, keys, kidx):
    """[20] Timing test (v7.4 test build): SCAN_PROBE / SCAN_STATUS. A probe
    measures at another timing with the keys left on their old readings; the
    parallel read uses ADC1, ADC2 and ADC3 with a DMA stream each; the next
    column can be powered before the results are stored; a timing only goes
    live if the readings stay put, and is given up when an ADC is missing or
    dies or the scan stops. Nothing of it is saved."""
    print('[20] timing test v7.4 (probes, parallel read on three ADCs, go live, fall back)')
    PROBE, STATUS, GET, SET = 0x6A, 0x6B, 0x68, 0x69
    NONE, RUNNING, DONE, FAILED = 0, 1, 2, 3
    E_NO_ADC, E_FAULTS, E_STALL, E_SHIFT, E_REPLACED = 1, 2, 3, 4, 5
    F_TUNED, F_ADC23, F_FELL_BACK = 1, 2, 4
    US = 168                                          # TIM10 ticks per microsecond
    PAR = 3                                           # mode: three ADCs, next column powered first
    MODES = {0: 'one ADC, results stored first (v7.3)', 1: 'three ADCs, results stored first',
             2: 'one ADC, next column first', 3: 'three ADCs, next column first'}
    STEP = 3000                                       # emulated cycles per column

    def boot(journal, rest_values=rest):
        e = Cfw(image, syms, rest_values, journal=journal)
        e.stub_usb_host()
        e.run_until(lambda: e.r8('baseline_ready') == 1 and e.p.regs.get(OTG + 0x18, 0) != 0, 3000)
        e.run_until(lambda: False, 50)
        return e

    def command(e, payload):
        scratch = (syms['_ebss'] + 0x100) & ~3
        e.uc.mem_write(scratch, bytes(payload) + bytes(64 - len(payload)))
        e.uc.mem_write(syms['out_buf'], bytes([0]) + bytes([0xAA]) * 63)   # junk the reply must clear
        enqueue(e, scratch)
        e.run_until(lambda: e.uc.mem_read(syms['out_buf'], 1)[0] != 0, 400)
        return bytes(e.uc.mem_read(syms['out_buf'], 64))

    def parse(r):
        w = lambda i: r[i] | r[i + 1] << 8            # noqa: E731
        return dict(reply=r[0], proto=r[1], mode=r[2], ticks=w(3), sample=r[5], flags=r[6], state=r[7],
                    err=r[8], pmode=r[9], pticks=w(10), psample=r[12], plog2=r[13],
                    cycles=int.from_bytes(r[14:18], 'little'), shift=w(18), shift_key=r[20],
                    pfaults=w(21), faults=w(23), sync=w(25), overruns=w(27), stalls=w(29), rate=w(31),
                    loops=int.from_bytes(r[33:36], 'little'), tpu=r[36], speed=r[37], tail=r[38:])

    def status(e):
        return parse(command(e, [STATUS, 0]))

    def probe(e, mode, us, sample=2, log2=4, live=0):
        t = round(us * US)
        return command(e, [PROBE, mode, t & 0xFF, t >> 8, sample, log2, live])

    def finish(e, limit=1500):
        e.run_until(lambda: e.r8('probe_state') != RUNNING, limit)
        return status(e)

    def results(e):
        out = []
        for first in range(0, 68, 10):
            r = command(e, [STATUS, 1, first])
            if r[0] != STATUS or r[2] != first:
                return None
            out += [struct.unpack_from('<HHH', r, 4 + 6 * i) for i in range(r[3])]
        return out

    def wait_capture(e):
        e.run_until(lambda: e.r8('baseline_pass') == 0xFF, 800)

    def baseline_err(e, rest_now=rest, skip=()):
        bl = [e.r16(syms['baseline'] + 2 * i) for i in range(68)]
        return max(abs(bl[kidx[rc]] - rest_now[rc[0]][rc[1]]) for rc in keys if rc not in skip)

    def raw(e):
        return [e.r16(syms['adc_raw'] + 2 * i) for i in range(68)]

    def pressed(e):
        return [i for i in range(68) if e.is_pressed(i)]

    def run_watching(e, steps):
        """Run and collect every key that reads as pressed at any point."""
        seen = set()
        for _ in range(steps):
            e.step()
            seen.update(pressed(e))
            travel[0] = max(travel[0], max(e.r8('key_matrix', k * 10 + 6) for k in range(68)))
        return sorted(seen)

    travel = [0]                                      # deepest travel (0-255) any key showed in run_watching

    def store_order(e, columns=24):
        """For each of some columns: True if its results were stored before
        the next column's settle timer was started, False if after."""
        state = {'dma': False, 'snap': None}
        fire, write, plain = e.fire, e.p.write, e.rest

        def w(uc, addr, size, value):
            if state['dma'] and state['snap'] is None and addr & ~3 == TIM10 and value & 1:
                state['snap'] = raw(e)
            write(uc, addr, size, value)

        def f(vector):
            state['dma'] = vector == 16 + 56
            try:
                fire(vector)
            finally:
                state['dma'] = False
        e.p.write, e.fire = w, f
        out = []
        for i in range(columns):
            # every column reads a little differently from the last time it was read
            e.rest = [[v + i + 1 if v < 4095 else v for v in row] for row in plain]
            state['snap'] = None
            n = len(e.col_log)
            e.step()
            if len(e.col_log) == n or e.col_log[-1] is None or state['snap'] is None:
                continue
            c = e.col_log[-1]
            ks = [(kidx[(r, c)], e.rest[r][c]) for r in range(ROWS) if (r, c) in kidx]
            after = raw(e)
            if any(after[k] != v for k, v in ks):
                out.append(None)                      # not stored at all
            else:
                out.append(all(state['snap'][k] == v for k, v in ks))
        e.p.write, e.fire, e.rest = write, fire, plain
        return out

    def seq(e, base):
        n = ((R(e, base + 0x2C) >> 20) & 0xF) + 1
        return [(R(e, base + 0x34) >> (5 * i)) & 0x1F for i in range(n)]

    def single_setup(e):
        return (seq(e, ADC1) == [3, 2, 1, 0, 7] and R(e, DMA2_S0 + 4) == 5
                and R(e, DMA2_S0 + 0xC) == syms['adc_buffer'] and R(e, ADC1 + 8) & 0x301 == 0x301)

    R = lambda e, a: e.p.regs.get(a, 0)              # noqa: E731
    cc = lambda e: e.r32(syms['column_count'])       # noqa: E731
    arr = lambda us: round(us * US) - 1              # noqa: E731
    EXTRA = (ADC2, ADC3 + 0x100), (DMA2_S1, DMA2_S2 + 0x18)   # ADC2/ADC3 and their DMA streams
    touched = lambda e: [hex(a) for _, _, k, a, _, _ in e.p.log   # noqa: E731
                         if k == 'W' and any(lo <= a < hi for lo, hi in EXTRA)]

    # --- a keyboard that never runs a probe reads its keys as v7.3 does ---
    e = boot(struct.pack('<I', build_id))
    st = status(e)
    check(st['reply'] == STATUS and st['proto'] == 3 and (st['mode'], st['ticks'], st['sample']) == (0, 28 * US, 2)
          and st['flags'] == 0 and st['state'] == NONE and st['tpu'] == US and st['speed'] == 1 and not any(st['tail']),
          f"SCAN_STATUS after start-up: one ADC, 28 us, 28-cycle samples, nothing tuned, no probe ({st['mode']}, "
          f"{st['ticks']}, {st['sample']}, flags {st['flags']})")
    check(not touched(e) and not R(e, 0x40023844) & (3 << 9),
          f'start-up never touches ADC2, ADC3 or their DMA streams, and leaves their clocks off ({touched(e)[:4]})')
    e.run_until(lambda: status(e)['loops'] > 0, 1200)
    check(status(e)['loops'] > 0, f"main loop passes per second are counted ({status(e)['loops']}/s in the emulator)")

    # --- refused requests change nothing ---
    before = R(e, TIM10 + 0x2C)
    bad = {'mode 4': [PROBE, 4, 0x40, 5, 2, 4, 0], 'sample code 8': [PROBE, 0, 0x40, 5, 8, 4, 0],
           'settle of 7 ticks': [PROBE, 0, 7, 0, 2, 4, 0], '8 scans': [PROBE, 0, 0x40, 5, 2, 3, 0],
           '8,192 scans': [PROBE, 0, 0x40, 5, 2, 13, 0], 'go-live value 2': [PROBE, 0, 0x40, 5, 2, 4, 2],
           'status request 8': [STATUS, 8], 'results from key 68': [STATUS, 1, 68]}
    refused = {why: command(e, c)[0] for why, c in bad.items()}
    check(all(v == 0xFF for v in refused.values()) and R(e, TIM10 + 0x2C) == before and status(e)['state'] == NONE,
          f'out-of-range requests are refused and change nothing ({[w for w, v in refused.items() if v != 0xFF]})')

    # --- a probe on one ADC at a shorter settle, where every sensor reads 60 lower ---
    e.timing_effect = lambda par, ticks, code, r, c: -60 if ticks < 28 * US else 0
    frozen = raw(e)
    r = probe(e, 0, 8, log2=6)
    st = parse(r)
    check(r[0] == PROBE and st['state'] == RUNNING
          and (st['pmode'], st['pticks'], st['psample'], st['plog2']) == (0, 8 * US, 2, 6)
          and (st['mode'], st['ticks']) == (0, 28 * US),
          f"SCAN_PROBE replies with the status: running at 8 us, live timing still 28 us ({r[:14].hex(' ')})")
    check(R(e, TIM10 + 0x2C) == arr(8), f'the settle timer is on the probe timing while it runs (ARR {R(e, TIM10 + 0x2C)})')
    check(probe(e, 0, 18)[0] == 0xFF and command(e, [STATUS, 1, 0])[3] == 0,
          'a second probe is refused while one runs, and no results are handed out yet')
    w = (1, 2)
    e.press[w] = -900                                 # W goes down during the probe
    seen = run_watching(e, 300)
    check(raw(e) == frozen and not seen and e.r8('probe_state') == RUNNING,
          f'while it runs the keys stay on their readings from before: nothing changes, nothing presses ({seen})')
    e.press.pop(w)
    st = finish(e)
    check(st['state'] == DONE and st['err'] == 0 and st['pfaults'] == 0 and st['cycles'] == 64 * COLS * STEP,
          f"it ends by itself after 64 full scans, timed with the cycle counter ({st['cycles']} cycles, "
          f"{64 * COLS * STEP} expected)")
    check(R(e, TIM10 + 0x2C) == arr(28) and single_setup(e)
          and (st['mode'], st['ticks'], st['flags'] & F_TUNED) == (0, 28 * US, 0),
          f'and the keyboard is back on its own timing (ARR {R(e, TIM10 + 0x2C)})')
    res = results(e)
    got = {rc: res[kidx[rc]] for rc in keys} if res else {}
    quiet = [rc for rc in keys if rc != w]
    check(res is not None and len(res) == 68
          and all(got[rc] == ((rest[rc[0]][rc[1]] - 60) * 16,) + (rest[rc[0]][rc[1]] - 60,) * 2 for rc in quiet),
          'results: average x 16, lowest and highest raw reading of every key, each 60 below its rest value')
    lo, hi = got[w][1], got[w][2]
    check(lo == rest[1][2] - 960 and hi == rest[1][2] - 60 and lo * 16 < got[w][0] < hi * 16,
          f'and W, pressed part of the time, shows its lowest and highest reading ({lo}..{hi})')
    check(st['shift'] >= 60 and baseline_err(e) <= 2 and not pressed(e),
          f"largest move from the live readings reported ({st['shift']} counts, key {st['shift_key']}); rest values untouched")
    e.run_until(lambda: False, 60)
    check(not pressed(e) and max(abs(a - b) for a, b in zip(raw(e), frozen)) <= 2,
          'after the probe the keys read live again, as before')

    # --- sample time is part of a timing ---
    probe(e, 0, 28, sample=1)
    smp = R(e, ADC1 + 0x10)
    check(all((smp >> (3 * ch)) & 7 == 1 for ch in (0, 1, 2, 3, 7)),
          f'a probe with sample code 1 sets 15-cycle samples (SMPR2 0x{smp:08x})')
    finish(e)
    smp = R(e, ADC1 + 0x10)
    check(all((smp >> (3 * ch)) & 7 == 2 for ch in (0, 1, 2, 3, 7)), 'and 28 cycles are back afterwards')
    e.timing_effect = None

    # --- parallel read: three ADCs, a DMA stream each ---
    uniq = [[4095 if rest[r][c] == 4095 else 1500 + 300 * r + 11 * c for c in range(COLS)] for r in range(ROWS)]
    e = boot(struct.pack('<I', build_id), uniq)
    r = probe(e, PAR, 8, log2=4)
    st = parse(r)
    e.p.log.clear()                                   # from here on every column is a parallel read
    e.conv_log.clear()
    check(r[0] == PROBE and st['state'] == RUNNING and st['flags'] & F_ADC23 and R(e, 0x40023844) & (3 << 9) == 3 << 9,
          f"parallel probe: ADC2 and ADC3 clocked and found (flags {st['flags']}, APB2ENR 0x{R(e, 0x40023844):08x})")
    s1, s2, s3 = seq(e, ADC1), seq(e, ADC2), seq(e, ADC3)
    check((s1, s2, s3) == ([3, 0], [2, 7], [1]),
          f'sequences: ADC1 PA3,PA0 (rows 0,3)  ADC2 PA2,PA7 (rows 1,4)  ADC3 PA1 (row 2): {s1} {s2} {s3}')
    smp = [R(e, b + 0x10) for b in (ADC1, ADC2, ADC3)]
    check(all((smp[i] >> (3 * ch)) & 7 == 2 for i, chs in enumerate((s1, s2, s3)) for ch in chs),
          '28-cycle samples on every channel of every ADC')
    buf = syms['adc_buffer']
    d = {n: (R(e, st_), R(e, st_ + 4), R(e, st_ + 8), R(e, st_ + 0xC)) for n, (_, st_, _, _) in ADC_DMA.items()}
    ok = all(cr & 1 and cr & (1 << 8) and (cr >> 25) & 7 == ADC_DMA[n][2] and par == ADC_DMA[n][0] + 0x4C
             for n, (cr, _, par, _) in d.items())
    check(ok and (d[1][1], d[1][3]) == (2, buf) and (d[2][1], d[2][3]) == (2, buf + 4) and (d[3][1], d[3][3]) == (1, buf + 8),
          f'DMA2 streams 0/2/1 on request channels 0/1/2: ADC1 -> slots 0-1, ADC2 -> slots 2-3, ADC3 -> slot 4, circular '
          f'({[(hex(cr), n_) for cr, n_, _, _ in d.values()]})')
    check(d[1][0] & 0x1E == 0x16 and not d[2][0] & 0x1E and not d[3][0] & 0x1E
          and (d[2][0] >> 16) & 3 == 3 and (d[3][0] >> 16) & 3 == 3 and (d[1][0] >> 16) & 3 == 2,
          "only ADC1's stream raises interrupts; ADC2 and ADC3 deliver at a higher priority, so first")
    c2 = [(R(e, b + 4), R(e, b + 8)) for b in (ADC2, ADC3)]
    check(all(cr1 == (1 << 8) | (1 << 26) and cr2 == 0x301 for cr1, cr2 in c2),
          f'ADC2 and ADC3: 12-bit sequence, overrun interrupt, DMA with continuous requests, on ({c2})')
    e.run_until(lambda: len(e.conv_log) >= 40, 200)
    col_bits = {(PORT[p] + 0x18, 1 << (b + 16)) for p, b in COL_PINS}
    tim_isr = syms['TIM1_UP_TIM10_IRQHandler'] & ~1
    after = sorted(a & ~1 for a in syms.values() if a & ~1 > tim_isr and a < 0x08080000)[0]
    order, bad, starts, armed, switched = [], [], 0, False, False
    for q, pc, k, a, size, v in e.p.log:
        if k != 'W':
            continue
        if any(a == ba and v & bit for ba, bit in col_bits):
            order, armed, switched = [], False, True
        elif a == TIM10 and v & 1:
            armed = True
        elif v & (1 << 30) and a in (ADC1 + 8, ADC2 + 8, ADC3 + 8) and switched:   # whole columns only
            order.append({ADC1 + 8: 1, ADC2 + 8: 2, ADC3 + 8: 3}[a])
            if not (armed and tim_isr <= pc < after):
                bad.append(q)
            if a == ADC1 + 8:
                starts += 1
                if order != [3, 2, 1]:
                    bad.append(q)
    check(starts >= 30 and not bad,
          f'each of {starts} columns: after the column switch and settle, the timer interrupt starts ADC3, ADC2, '
          f'then ADC1 ({len(bad)} not)')
    convs = [c for col, c in e.conv_log if col is not None][-30:]
    check(all(c == {1: [3, 0], 2: [2, 7], 3: [1]} for c in convs),
          'every column: each of the five rows converted exactly once')
    st = finish(e)
    res = results(e)
    wrong = [rc for rc in keys if res[kidx[rc]] != (uniq[rc[0]][rc[1]] * 16,) + (uniq[rc[0]][rc[1]],) * 2]
    check(st['state'] == DONE and st['pfaults'] == 0 and st['faults'] == 0 and not wrong,
          f'every key gets the reading of its own sensor (68 different rest values; wrong: {wrong[:5]})')
    check(st['cycles'] == 16 * COLS * STEP and st['shift'] <= 2,
          f"16 scans timed, readings where the live ones are ({st['shift']})")
    off = ([R(e, b + 8) & 1 for b in (ADC2, ADC3)] + [R(e, s_) & 1 for s_ in (DMA2_S1, DMA2_S2)]
           + [R(e, b + 4) for b in (ADC2, ADC3)])
    check(single_setup(e) and not any(off) and baseline_err(e, uniq) <= 2 and not pressed(e),
          f'afterwards: ADC1 back on all five rows; ADC2/ADC3 off with their interrupts and streams disabled ({off}); '
          f'rest values intact')

    # --- every mode reads every key from its own sensor, probed and live ---
    slots = (((0, 1), -900), ((3, 2), -900), ((1, 2), -900), ((4, 6), +900), ((2, 4), -900))
    for mode in (2, 1, 3):
        e = boot(struct.pack('<I', build_id), uniq)
        e.p.log.clear()
        probe(e, mode, 8)
        st = finish(e)
        res = results(e)
        wrong = [rc for rc in keys if res[kidx[rc]] != (uniq[rc[0]][rc[1]] * 16,) + (uniq[rc[0]][rc[1]],) * 2]
        check(st['state'] == DONE and st['pfaults'] == 0 and st['cycles'] == 16 * COLS * STEP and not wrong
              and (mode & 1 or not touched(e)),
              f'mode {mode} ({MODES[mode]}): a probe reads every key from its own sensor'
              + ('' if mode & 1 else ', ADC2 and ADC3 untouched') + f' (wrong: {wrong[:5]})')
        probe(e, mode, 8, live=1)
        seen = run_watching(e, 320)
        st = status(e)
        wait_capture(e)
        seen += run_watching(e, 40)
        hits = []
        for rc, delta in slots:
            k = kidx[rc]
            e.press[rc] = delta
            e.run_until(lambda: e.is_pressed(k), 400)
            hits.append(pressed(e) == [k])
            e.press.pop(rc)
            e.run_until(lambda: not e.is_pressed(k), 400)
            hits.append(not pressed(e))
        check(st['flags'] & F_TUNED and st['mode'] == mode and baseline_err(e, uniq) <= 2 and not seen and all(hits),
              f'mode {mode} live: rest values right, and a key in each result slot registers alone and releases')
        order = store_order(e)
        check(len(order) >= 20 and all(o is (not mode & 2) for o in order),
              f'mode {mode}: results are stored ' + ('after' if mode & 2 else 'before')
              + f' the next column is powered and its settle timer started ({len(order)} columns)')
        command(e, [SET, 1])
        wait_capture(e)
        order = store_order(e)
        check(single_setup(e) and status(e)['mode'] == 0 and len(order) >= 20 and all(o is True for o in order)
              and baseline_err(e, uniq) <= 2 and not pressed(e),
              'back on a scan speed: one ADC, results stored first, as v7.3')

    # --- going live with the parallel read ---
    e = boot(struct.pack('<I', build_id))
    probe(e, PAR, 8, live=1)
    seen = run_watching(e, 60)
    st = finish(e)
    check(st['state'] == DONE and st['flags'] & F_TUNED and (st['mode'], st['ticks'], st['sample']) == (PAR, 8 * US, 2)
          and not seen,
          f"go live: after 16 scans with the readings in place the keyboard keeps the timing (flags {st['flags']}, "
          f"shift {st['shift']})")
    check(command(e, [GET])[6] == 1, 'and captures every rest value again')
    wait_capture(e)
    seen = run_watching(e, 80)
    check(baseline_err(e) <= 2 and not seen, f'rest values match the sensors, no key pressed ({seen})')
    for rc, delta, label in (((0, 1), -900, '1 (row 0: ADC1, first)'), ((3, 2), -900, 'Z (row 3: ADC1, second)'),
                             ((1, 2), -900, 'W (row 1: ADC2, first)'), ((4, 6), +900, 'Space (row 4: ADC2, second)'),
                             ((2, 4), -900, 'F (row 2: ADC3)')):
        k = kidx[rc]
        e.press[rc] = delta
        e.run_until(lambda: e.is_pressed(k), 400)
        others = [i for i in pressed(e) if i != k]
        hit = e.is_pressed(k)
        e.press.pop(rc)
        e.run_until(lambda: not e.is_pressed(k), 400)
        check(hit and not others and not e.is_pressed(k), f'{label}: registers alone and releases ({others})')
    e.run_until(lambda: False, 1000)
    g = command(e, [GET])
    want = round(168e6 / (COLS * STEP))
    check(g[2] == 1 and abs((g[4] | g[5] << 8) - want) <= 2 and status(e)['rate'] == g[4] | g[5] << 8,
          f"GET_SCAN still answers: saved speed unchanged (Standard), rate measured ({g[4] | g[5] << 8}/s)")
    check(not [pl for t, pl in rgb_records(e) if t == 3], 'a live timing is not saved')
    sector6 = bytes(e.uc.mem_read(0x08040000, 0x20000))
    e2 = boot(sector6)
    st2 = status(e2)
    check((st2['mode'], st2['ticks'], st2['flags']) == (0, 28 * US, 0) and not touched(e2) and single_setup(e2),
          'after a restart the keyboard is back on its saved scan speed, ADC2 and ADC3 untouched')

    # a scan speed set from the app takes over from a live timing
    g = command(e, [SET, 3])
    st = status(e)
    check(g[0] == SET and g[2] == 3
          and (st['mode'], st['ticks'], st['flags'] & (F_TUNED | F_FELL_BACK)) == (0, 8 * US, 0)
          and single_setup(e) and R(e, TIM10 + 0x2C) == arr(8),
          'SET_SCAN while a timing is live: back to one ADC at that speed')
    wait_capture(e)
    seen = run_watching(e, 60)
    check(baseline_err(e) <= 2 and not seen, 'rest values captured again, no key pressed')

    # --- going live is refused when the readings move ---
    e = boot(struct.pack('<I', build_id))
    e.timing_effect = lambda par, ticks, code, r, c: -400 if par and r == 2 else 0    # ADC3 reads wrong
    probe(e, PAR, 8, live=1)
    seen = run_watching(e, 320)
    st = status(e)
    check(st['state'] == FAILED and st['err'] == E_SHIFT and st['shift'] == 400 and keys[st['shift_key']][0] == 2,
          f"readings of one ADC 400 off: go live refused, the move and a key of that row reported ({st['shift']}, "
          f"key {st['shift_key']})")
    check(not seen and not st['flags'] & F_TUNED and (st['mode'], st['ticks']) == (0, 28 * US) and single_setup(e)
          and baseline_err(e) <= 2,
          f'the keyboard stays on its own timing and no key ever reads as pressed ({seen})')
    e.timing_effect = lambda par, ticks, code, r, c: -100 if par else 0               # within the limit
    probe(e, PAR, 8, live=1)
    travel[0] = 0
    seen = run_watching(e, 20)
    check(e.r8('probe_state') == RUNNING, 'go live with readings 100 lower: probe under way')
    seen += run_watching(e, 300)
    st = status(e)
    shifted = [[v - 100 if v < 4095 else v for v in row] for row in rest]
    check(st['flags'] & F_TUNED and st['shift'] == 100 and not seen and travel[0] <= 1,
          f"inside the limit, so it goes live; rest values move with the readings at that instant, so no key's "
          f"travel moves at all (deepest {travel[0]} of 255, shift {st['shift']})")
    wait_capture(e)
    seen += run_watching(e, 60)
    check(baseline_err(e, shifted) <= 2 and not seen and travel[0] <= 1,
          f'and the capture that follows settles them on the sensors (error {baseline_err(e, shifted)})')
    command(e, [SET, 1])
    seen = run_watching(e, 30)
    check(not seen and travel[0] <= 1 and (status(e)['mode'], status(e)['flags'] & F_TUNED) == (0, 0),
          f'leaving it for a scan speed puts the old rest values back at once: again no travel (deepest {travel[0]})')
    wait_capture(e)
    check(baseline_err(e) <= 2 and not pressed(e), 'rest values right for the one-ADC readings')
    e.timing_effect = None

    # --- an ADC that is not there, or stops delivering ---
    e = boot(struct.pack('<I', build_id))
    e.p.adc_absent = True
    before = R(e, TIM10 + 0x2C)
    r = probe(e, PAR, 8)
    st = parse(r)
    c0 = cc(e)
    seen = run_watching(e, 60)
    check(r[0] == PROBE and st['state'] == FAILED and st['err'] == E_NO_ADC and not st['flags'] & F_ADC23
          and R(e, TIM10 + 0x2C) == before and single_setup(e) and cc(e) > c0 + 30 and not seen,
          f"ADC2/ADC3 registers do not answer: probe fails at once (error {st['err']}), the scan carries on untouched")
    check(parse(probe(e, 0, 8))['state'] == RUNNING and finish(e)['state'] == DONE, 'a one-ADC probe still works there')

    e = boot(struct.pack('<I', build_id))
    e.dead_adcs = {2}
    probe(e, PAR, 8)
    seen = run_watching(e, 60)
    st = status(e)
    c0 = cc(e)
    seen += run_watching(e, 40)
    check(st['state'] == FAILED and st['err'] == E_FAULTS and st['pfaults'] == 9 and st['sync'] == 9 and not seen,
          f"ADC2 never delivers: its results are missed, and after 9 tries the probe gives up ({st['pfaults']} faults, "
          f"error {st['err']})")
    check(single_setup(e) and cc(e) > c0 + 30 and baseline_err(e) <= 2 and (st['mode'], st['flags'] & F_TUNED) == (0, 0),
          'the keyboard is back on its own timing and scanning')
    e.dead_adcs = set()

    probe(e, PAR, 8, live=1)
    finish(e)
    wait_capture(e)
    check(status(e)['flags'] & F_TUNED, 'parallel read live again (ADC2 working)')
    e.dead_adcs = {3}
    seen = run_watching(e, 80)
    st = status(e)
    c0 = cc(e)
    wait_capture(e)
    seen += run_watching(e, 60)
    check(st['flags'] & F_FELL_BACK and not st['flags'] & F_TUNED and (st['mode'], st['ticks']) == (0, 28 * US)
          and single_setup(e) and st['faults'] >= 33,
          f"ADC3 dies while live: after {st['faults'] - 9} faults the timing is dropped for the saved scan speed "
          f"(flags {st['flags']})")
    check(cc(e) > c0 + 30 and baseline_err(e) <= 2 and not seen,
          f'scanning on one ADC, rest values right, no key ever pressed ({seen})')
    e.dead_adcs = set()

    # --- the scan stops ---
    probe(e, PAR, 8, live=1)
    finish(e)
    wait_capture(e)
    e.drop_adc = True
    seen = run_watching(e, 160)
    st = status(e)
    check(st['stalls'] >= 1 and st['flags'] & F_FELL_BACK and not st['flags'] & F_TUNED and single_setup(e),
          f"no column finishes for 100 ms on a live timing: dropped for the saved scan speed ({st['stalls']} stall)")
    e.drop_adc = False
    c0 = cc(e)
    wait_capture(e)
    seen += run_watching(e, 60)
    check(cc(e) > c0 + 30 and baseline_err(e) <= 2 and not seen,
          'once conversions work again the scan carries on, no key pressed')

    probe(e, 0, 18, log2=8)
    e.run_until(lambda: False, 30)
    e.drop_adc = True
    seen = run_watching(e, 160)
    st = status(e)
    e.drop_adc = False
    check(st['state'] == FAILED and st['err'] == E_STALL and R(e, TIM10 + 0x2C) == arr(28) and not seen,
          f"the scan stops during a probe: the probe ends (error {st['err']}) and the live timing is set again")
    c0 = cc(e)
    e.run_until(lambda: cc(e) > c0 + 30, 300)
    check(cc(e) > c0 + 30 and not pressed(e), 'and the scan carries on')

    # --- probes and scan speed changes ---
    probe(e, PAR, 8, log2=8)
    e.run_until(lambda: False, 40)
    g = command(e, [SET, 2])
    st = status(e)
    check(g[0] == SET and g[2] == 2 and st['state'] == FAILED and st['err'] == E_REPLACED
          and (st['mode'], st['ticks']) == (0, 18 * US) and single_setup(e) and R(e, TIM10 + 0x2C) == arr(18),
          f"SET_SCAN during a probe: the probe is dropped (error {st['err']}) and the speed applied")
    wait_capture(e)
    check(baseline_err(e) <= 2 and not pressed(e), 'rest values captured at the new speed')
    command(e, [SET, 1])
    e.run_until(lambda: e.r8('baseline_pass') not in (0, 0xFF), 200)    # capture under way
    e.run_until(lambda: False, 37)                                      # ...and part-way through a scan
    probe(e, 0, 8)
    finish(e)
    wait_capture(e)
    seen = run_watching(e, 60)
    check(baseline_err(e) <= 2 and not seen,
          f'a probe in the middle of a rest value capture: the capture starts over and ends right '
          f'(error {baseline_err(e)})')
    bad = [hex(a) for a in e.flash_report() + e2.flash_report() if not (0x08040000 <= a < 0x08080000)]
    check(not bad, f'no flash changed outside sectors 6/7 {bad}')


def scan_v74(image, syms, rest, build_id, keys, kidx, base):
    """[21] v7.4: scan speeds 4-6 read with three ADCs and are only taken when
    the readings agree; smoothing levels 1-3 filter once per reading."""
    print('[21] v7.4 (three-ADC scan speeds as saved speeds, per-reading smoothing)')
    GET, SET, SETF, PROBE, STATUS, ANALOG_INFO = 0x68, 0x69, 0x6C, 0x6A, 0x6B, 5
    IN_EFFECT, SWITCHING, REFUSED = 0, 1, 2
    F_TUNED, F_FELL_BACK = 1, 4
    US, STEP, N = 168, 3000, 7
    SPEEDS = {4: (7, 8, 2), 5: (7, 5, 2), 6: (7, 5, 1)}     # speed: mode, settle us, sample code

    def boot(journal, rest_values=rest, absent=False, effect=None, watch=None):
        e = Cfw(image, syms, rest_values, journal=journal)
        e.p.adc_absent = absent
        e.timing_effect = effect
        e.stub_usb_host()
        seen = set()
        for _ in range(3000):
            if e.r8('baseline_ready') == 1 and e.p.regs.get(OTG + 0x18, 0) != 0:
                break
            e.step()
        for _ in range(700):                      # the matrix calibration (500 ms) and a speed switch
            e.step()
            if watch is not None:
                seen.update(i for i in range(68) if e.is_pressed(i))
        if watch is not None:
            watch.extend(sorted(seen))
        return e

    def command(e, payload):
        scratch = (syms['_ebss'] + 0x100) & ~3
        e.uc.mem_write(scratch, bytes(payload) + bytes(64 - len(payload)))
        e.uc.mem_write(syms['out_buf'], bytes([0]) + bytes([0xAA]) * 63)
        enqueue(e, scratch)
        e.run_until(lambda: e.uc.mem_read(syms['out_buf'], 1)[0] != 0, 400)
        return bytes(e.uc.mem_read(syms['out_buf'], 64))

    def scan(e):
        r = command(e, [GET])
        return dict(speed=r[2], count=r[3], rate=r[4] | r[5] << 8, busy=r[6], settle=list(r[7:7 + N]),
                    timing=list(r[7 + N:7 + 2 * N]), state=r[7 + 2 * N], level=r[8 + 2 * N],
                    levels=r[9 + 2 * N], loops=r[10 + 2 * N] | r[11 + 2 * N] << 8, tail=r[12 + 2 * N:32], raw=r)

    def status(e):
        r = command(e, [STATUS, 0])
        w = lambda i: r[i] | r[i + 1] << 8            # noqa: E731
        return dict(mode=r[2], us=w(3) / US, sample=r[5], flags=r[6], pstate=r[7], err=r[8], faults=w(23),
                    stalls=w(29), speed=r[37], state=r[38], level=r[39], lag_on=r[40], lag_n=w(41),
                    lag_mean=w(43), lag_min=w(45), lag_max=w(47))

    def live(e):
        st = status(e)
        return st['mode'], st['us'], st['sample']

    def settle(e, limit=900):
        """Until a speed switch and the rest value capture after it are over."""
        e.run_until(lambda: e.r8('speed_state') != SWITCHING and e.r8('probe_state') != 1
                    and e.r8('baseline_pass') == 0xFF, limit)

    def baseline_err(e, rest_now=rest):
        bl = [e.r16(syms['baseline'] + 2 * i) for i in range(68)]
        return max(abs(bl[kidx[rc]] - rest_now[rc[0]][rc[1]]) for rc in keys)

    def pressed(e):
        return [i for i in range(68) if e.is_pressed(i)]

    def filtered(e, k):
        return e.r16(syms['key_matrix'] + k * 10)

    def travel_of(e, k):
        return e.r8('key_matrix', k * 10 + 6)

    travel = [0]

    def run_watching(e, steps, **kw):
        seen = set()
        for _ in range(steps):
            e.step(**kw)
            seen.update(pressed(e))
            travel[0] = max(travel[0], max(travel_of(e, k) for k in range(68)))
        return sorted(seen)

    def seq(e, base_):
        n = ((R(e, base_ + 0x2C) >> 20) & 0xF) + 1
        return [(R(e, base_ + 0x34) >> (5 * i)) & 0x1F for i in range(n)]

    def single_setup(e):
        return (seq(e, ADC1) == [3, 2, 1, 0, 7] and R(e, DMA2_S0 + 4) == 5
                and R(e, ADC1 + 8) & 0x301 == 0x301 and not R(e, ADC2 + 8) & 1 and not R(e, ADC3 + 8) & 1)

    def parallel_setup(e, sample):
        smp = [R(e, b + 0x10) for b in (ADC1, ADC2, ADC3)]
        seqs = (seq(e, ADC1), seq(e, ADC2), seq(e, ADC3))
        return (seqs == ([3, 0], [7, 7], [2, 1])
                and all((smp[i] >> (3 * ch)) & 7 == sample for i, chs in enumerate(seqs) for ch in chs)
                and all(R(e, b + 8) & 0x301 == 0x301 for b in (ADC1, ADC2, ADC3)))

    def tap(e, rc, delta=-900):
        k = kidx[rc]
        e.press[rc] = delta
        e.run_until(lambda: e.is_pressed(k), 500)
        alone = pressed(e) == [k]
        e.press.pop(rc)
        e.run_until(lambda: not e.is_pressed(k), 500)
        return alone and not pressed(e)

    R = lambda e, a: e.p.regs.get(a, 0)              # noqa: E731
    arr = lambda us: round(us * US) - 1              # noqa: E731
    cc = lambda e: e.r32(syms['column_count'])       # noqa: E731
    EXTRA = (ADC2, ADC3 + 0x100), (DMA2_S1, DMA2_S2 + 0x18)
    touched = lambda e: [hex(a) for _, _, k, a, _, _ in e.p.log   # noqa: E731
                         if k == 'W' and any(lo <= a < hi for lo, hi in EXTRA)]
    confirmed = struct.pack('<I', build_id)
    saved = lambda speed, level=0: confirmed.ljust(0x10000, b'\xff') + rgb_record(3, bytes([speed, level, 0, 0]))  # noqa: E731
    SLOTS = (((0, 1), -900), ((3, 2), -900), ((1, 2), -900), ((4, 6), +900), ((2, 4), -900))

    # ---------------- scan speeds 4-6 ----------------
    e = boot(confirmed)
    g = scan(e)
    check(g['raw'][1] == 2 and g['speed'] == 1 and g['count'] == N and g['settle'] == [40, 28, 18, 8, 8, 5, 5]
          and g['timing'] == [0x20] * 4 + [0x27, 0x27, 0x17] and g['state'] == IN_EFFECT
          and (g['level'], g['levels']) == (0, 4) and g['loops'] > 0 and not any(g['tail']),
          f"GET_SCAN v2: 7 speeds with their settle time, mode and sample code, the speed in effect, smoothing 0 of 4, "
          f"main loop rate ({g['raw'][:26].hex(' ')})")
    check(command(e, [SET, 7])[0] == 0xFF and command(e, [SETF, 4])[0] == 0xFF and scan(e)['speed'] == 1,
          'speed 7 and smoothing level 4 are refused')

    for speed, (mode, us, sample) in SPEEDS.items():
        travel[0] = 0
        r = command(e, [SET, speed])
        g = scan(e)
        check(r[0] == SET and r[2] == speed and r[6] == 1 and r[7 + 2 * N] == SWITCHING,
              f'SET_SCAN {speed}: the reply shows the speed and that the keyboard is switching to it')
        seen = run_watching(e, 60)
        settle(e)
        seen += run_watching(e, 60)
        g, st = scan(e), status(e)
        check(g['state'] == IN_EFFECT and g['busy'] == 0 and live(e) == (mode, us, sample) and parallel_setup(e, sample)
              and R(e, TIM10 + 0x2C) == arr(us) and not st['flags'] & (F_TUNED | F_FELL_BACK),
              f'speed {speed} in effect: three ADCs, bottom row twice, next column first, {us} us, sample code {sample} '
              f'({live(e)}, ARR {R(e, TIM10 + 0x2C)})')
        check(not seen and travel[0] <= 1 and baseline_err(e) <= 2,
              f"no key's travel moved while switching (deepest {travel[0]}), rest values right")
        recs = [pl for t, pl in rgb_records(e) if t == 3]
        check(recs and recs[-1] == bytes([speed, 0, 0, 0]), f'saved as [{speed}, 0, 0, 0] ({recs[-1].hex() if recs else None})')
    check(all(tap(e, rc, d) for rc, d in SLOTS), 'on speed 6 a key in each result slot registers alone and releases')

    # a probe from the timing test, and back
    t = 40 * US
    command(e, [PROBE, 0, t & 0xFF, t >> 8, 2, 4, 0])
    e.run_until(lambda: e.r8('probe_state') != 1, 600)
    check(status(e)['pstate'] == 2 and live(e) == (7, 5, 1) and parallel_setup(e, 1) and scan(e)['state'] == IN_EFFECT,
          'a measuring probe on speed 6 returns to speed 6')

    # back to a one-ADC speed
    travel[0] = 0
    command(e, [SET, 1])
    seen = run_watching(e, 40)
    settle(e)
    check(live(e) == (0, 28, 2) and single_setup(e) and scan(e)['state'] == IN_EFFECT and not seen and travel[0] <= 1
          and baseline_err(e) <= 2, f'SET_SCAN 1 from speed 6: one ADC at 28 us again, no travel (deepest {travel[0]})')

    # restart with a three-ADC speed saved
    watch = []
    e2 = boot(saved(6), watch=watch)
    order = [(a, v) for _, _, k, a, _, v in e2.p.log if k == 'W' and (a == TIM10 + 0x2C or ADC2 <= a < ADC3 + 0x100)]
    first_extra = next((i for i, (a, _) in enumerate(order) if a != TIM10 + 0x2C), None)
    arrs_before = [v for a, v in order[:first_extra] if a == TIM10 + 0x2C]
    check(live(e2) == (7, 5, 1) and scan(e2)['state'] == IN_EFFECT and scan(e2)['speed'] == 6 and parallel_setup(e2, 1),
          f'after a restart with speed 6 saved: speed 6 in effect ({live(e2)})')
    check(arrs_before and set(arrs_before) == {arr(8)} and not watch and baseline_err(e2) <= 2,
          f'it starts on one ADC at 8 us and only then asks for three; no key pressed on the way ({watch})')
    check(all(tap(e2, rc, d) for rc, d in SLOTS), 'and its keys work')

    # an older firmware meets a record it does not know
    old = base / 'out' / 'MAD68HE_libhmk_v7.3.bin'
    if old.exists():
        eo = Cfw(old.read_bytes()[14:-4], {}, rest, journal=saved(5, 2))
        eo.run_until(lambda: len(eo.col_log) >= 400, 4000)
        check(R(eo, TIM10 + 0x2C) == arr(28) and len(eo.col_log) >= 400,
              f'v7.3 with speed 5 and smoothing 2 saved: starts on its default, Standard (ARR {R(eo, TIM10 + 0x2C)})')

    # the readings do not agree: refused
    bad = lambda par, ticks, code, r, c: -400 if par and r == 2 else 0          # noqa: E731
    e = boot(confirmed, effect=bad)
    travel[0] = 0
    command(e, [SET, 4])
    seen = run_watching(e, 400)
    check(scan(e)['state'] == REFUSED and status(e)['err'] == 4, 'one ADC\'s readings 400 off: speed 4 is refused')
    settle(e)
    seen += run_watching(e, 60)
    g, st = scan(e), status(e)
    check(g['speed'] == 4 and g['state'] == REFUSED and g['busy'] == 0 and live(e) == (0, 8, 2) and single_setup(e)
          and not st['flags'] & F_TUNED,
          f"it stays the saved speed but is not in effect; the keyboard runs one ADC at 8 us instead "
          f"(state {g['state']}, {live(e)})")
    check(not seen and travel[0] <= 1 and baseline_err(e) <= 2, f'no key ever pressed, no travel (deepest {travel[0]})')
    e.timing_effect = None
    command(e, [SET, 4])
    settle(e)
    check(scan(e)['state'] == IN_EFFECT and live(e) == (7, 8, 2), 'asked again once the readings agree: in effect')

    watch = []
    e2 = boot(saved(5), effect=bad, watch=watch)
    check(scan(e2)['state'] == REFUSED and live(e2) == (0, 8, 2) and single_setup(e2) and not watch
          and baseline_err(e2) <= 2 and tap(e2, (1, 2)),
          'the same at start-up with speed 5 saved: refused, one ADC at 8 us, keys work')

    # no ADC2/ADC3 at all
    watch = []
    e2 = boot(saved(4), absent=True, watch=watch)
    check(scan(e2)['state'] == REFUSED and live(e2) == (0, 8, 2) and single_setup(e2) and not watch and tap(e2, (1, 2)),
          'start-up with speed 4 saved on a chip without ADC2/ADC3: refused at once, one ADC at 8 us, keys work')
    r = command(e2, [SET, 6])
    check(r[2] == 6 and scan(e2)['state'] == REFUSED and live(e2) == (0, 8, 2), 'and SET_SCAN 6 there is refused the same way')
    command(e2, [SET, 2])
    settle(e2)
    check(scan(e2)['state'] == IN_EFFECT and live(e2) == (0, 18, 2), 'a one-ADC speed is in effect as always')

    # an ADC dies, or the scan stops, on a three-ADC speed
    e = boot(saved(4))
    check(live(e) == (7, 8, 2), 'speed 4 in effect after start-up')
    e.dead_adcs = {3}
    travel[0] = 0
    seen = run_watching(e, 90)
    st, g = status(e), scan(e)
    check(g['state'] == REFUSED and st['flags'] & F_FELL_BACK and live(e) == (0, 8, 2) and single_setup(e)
          and st['faults'] == 33,
          f"ADC3 stops delivering on speed 4: after {st['faults']} faults the keyboard drops to one ADC at 8 us")
    settle(e)
    seen += run_watching(e, 60)
    check(not seen and travel[0] <= 1 and baseline_err(e) <= 2 and tap(e, (2, 4)),
          f'no key pressed, no travel (deepest {travel[0]}), and the keys go on working')
    e.dead_adcs = set()
    command(e, [SET, 4])
    settle(e)
    check(live(e) == (7, 8, 2) and scan(e)['state'] == IN_EFFECT, 'SET_SCAN 4 again brings it back')
    e.drop_adc = True
    seen = run_watching(e, 160)
    e.drop_adc = False
    check(status(e)['stalls'] >= 1 and scan(e)['state'] == REFUSED and live(e) == (0, 8, 2) and not seen,
          'the scan stops on speed 4: after 100 ms the keyboard is on one ADC at 8 us')
    c0 = cc(e)
    settle(e)
    check(cc(e) > c0 + 30 and baseline_err(e) <= 2 and not pressed(e), 'and scanning again')

    # ---------------- smoothing ----------------
    e = boot(confirmed)
    w = (1, 2)
    kw = kidx[w]
    r = command(e, [SETF, 3])
    recs = [pl for t, pl in rgb_records(e) if t == 3]
    check(r[0] == SETF and r[8 + 2 * N] == 3 and recs and recs[-1] == bytes([1, 3, 0, 0]),
          f'SET_SCAN_FILTER 3: replies like GET_SCAN, saved next to the speed ({recs[-1].hex() if recs else None})')
    command(e, [SET, 3])
    recs = [pl for t, pl in rgb_records(e) if t == 3]
    check(recs[-1] == bytes([3, 3, 0, 0]) and scan(e)['level'] == 3, 'a speed change keeps the smoothing level')
    settle(e)

    # changing level does not move anything
    travel[0] = 0
    before = [filtered(e, k) for k in range(68)]
    seen = []
    for level in (1, 0, 2, 3, 0):
        command(e, [SETF, level])
        seen += run_watching(e, 40)
    check(not seen and travel[0] == 0 and [filtered(e, k) for k in range(68)] == before,
          'switching between the levels with the keys at rest: no filtered value or travel changes')

    from unicorn import UC_HOOK_CODE
    passes = [0]

    def count_pass(uc, addr, size, data):
        passes[0] += 1
    task = syms['analog_task'] & ~1
    e.uc.hook_add(UC_HOOK_CODE, count_pass, begin=task, end=task)      # once per main loop pass

    def readings_to_half(e, level, **kw_step):
        """Readings taken of W, and main loop passes, from a full press
        appearing to its filtered value being past half travel."""
        command(e, [SETF, level])
        e.run_until(lambda: False, 60)
        col = w[1]
        seq_addr = syms['analog_col_seq'] + col
        rest_v = e.r16(syms['key_matrix'] + kw * 10 + 2)
        e.run_until(lambda: e.col_log and e.col_log[-1] == col, 40)     # W has just been read
        s0, p0 = e.uc.mem_read(seq_addr, 1)[0], passes[0]
        e.press[w] = -900
        for _ in range(4000):
            e.step(**kw_step)
            if filtered(e, kw) >= rest_v + 450:
                break
        n = (e.uc.mem_read(seq_addr, 1)[0] - s0) & 0xFF
        np_ = passes[0] - p0
        hit = e.run_until(lambda: e.is_pressed(kw), 800, **kw_step)
        e.press.pop(w)
        e.run_until(lambda: not e.is_pressed(kw) and filtered(e, kw) <= rest_v + 2, 3000, **kw_step)
        return n, hit, np_

    got = {level: readings_to_half(e, level) for level in (3, 2, 1)}
    check([got[level][0] for level in (3, 2, 1)] == [3, 4, 7] and all(v[1] for v in got.values()),
          f'a full press is past half travel on the 3rd, 4th and 7th reading with levels 3, 2 and 1 '
          f'(one for the median, then steps of 1/2, 1/4, 1/8): {[got[level][0] for level in (3, 2, 1)]}')
    slow = {level: readings_to_half(e, level, instructions=150) for level in (3, 2, 1)}
    check(all(v[0] / max(v[2], 1) >= 2.5 for v in slow.values()) and all(2 <= v[2] <= 6 for v in slow.values()),
          'with a main loop several times slower than the scan, a pass takes the steps of the readings it missed: '
          f"past half travel within {[v[2] for v in slow.values()]} passes "
          f"({[v[0] for v in slow.values()]} readings went by)")

    def glitch(e, rc, level, size=60):
        """One reading of a resting key `size` counts off; how far its
        filtered value and travel went."""
        command(e, [SETF, level])
        e.run_until(lambda: False, 80)
        k = kidx[rc]
        before = filtered(e, k)
        prev = (rc[1] - 1) % COLS
        e.run_until(lambda: e.col_log and e.col_log[-1] == prev, 40)
        e.press[rc] = size
        e.step()                                       # the one reading
        e.press.pop(rc)
        top = deep = 0
        for _ in range(120):
            e.step()
            top = max(top, filtered(e, k) - before)
            deep = max(deep, travel_of(e, k))
        return top, deep, pressed(e)

    space = (4, 6)
    g0 = glitch(e, space, 0)
    gl = {level: glitch(e, space, level) for level in (1, 2, 3)}
    check(g0[0] >= 10 and g0[1] > 0, f'one stray reading 60 counts off, classic: the filtered value moves {g0[0]} counts '
          f'(travel {g0[1]} of 255)')
    check(all(v[0] == 0 and v[1] == 0 and not v[2] for v in gl.values()),
          f'levels 1-3: the median drops it, nothing moves ({gl})')
    gs = glitch(e, space, 3, size=60)
    check(gs[0] == 0, 'and again with the main loop as it is (nothing accumulates between glitches)')

    # the rise band
    command(e, [SETF, 2])
    e.run_until(lambda: False, 80)
    before = filtered(e, kw)
    up = lambda d: [[v - d if v < 4095 else v for v in row] for row in rest]     # noqa: E731  W reads lower pressed
    e.rest = up(6)
    e.run_until(lambda: False, 200)
    small = filtered(e, kw) - before
    e.rest = up(20)
    e.run_until(lambda: False, 200)
    big = filtered(e, kw) - before
    e.rest = rest
    e.run_until(lambda: False, 500)
    back = filtered(e, kw) - before
    check((small, big, back) == (0, 12, 0),
          f'readings 6 counts up: the filtered value stays; 20 up: it follows 8 below; back down: at once ({small}, {big}, {back})')

    # delay statistics on real presses
    lag = {}
    for level in (0, 3, 1):
        command(e, [SETF, level])
        e.run_until(lambda: False, 80)
        r = command(e, [STATUS, 2])
        e.run_until(lambda: False, 40)
        for _ in range(2):
            e.press[w] = -900
            e.run_until(lambda: e.is_pressed(kw), 800)
            e.run_until(lambda: False, 200)
            e.press.pop(w)
            e.run_until(lambda: not e.is_pressed(kw), 800)
            e.run_until(lambda: False, 200)
        lag[level] = status(e)
    scan_us = COLS * STEP / US
    check(all(v['lag_on'] == 1 and v['lag_n'] == 2 for v in lag.values()),
          f"delay measurement: two presses counted at each level ({[v['lag_n'] for v in lag.values()]})")
    check(1.5 * scan_us <= lag[3]["lag_mean"] <= 2.6 * scan_us and 5.0 * scan_us <= lag[1]["lag_mean"] <= 6.6 * scan_us
          and lag[3]['lag_min'] <= lag[3]['lag_mean'] <= lag[3]['lag_max'],
          f"filtered value behind the reading at half travel: level 3 about 2 scans, level 1 about 6 "
          f"({lag[3]['lag_mean']} and {lag[1]['lag_mean']} us; a scan is {scan_us:.0f} us here; classic {lag[0]['lag_mean']} us)")
    check(command(e, [STATUS, 3])[40] == 0 and status(e)['lag_n'] == 0, 'measurement off, statistics cleared')

    # per-reading smoothing on a three-ADC speed, saved and restored
    command(e, [SETF, 3])
    command(e, [SET, 6])
    settle(e)
    check(live(e) == (7, 5, 1) and all(tap(e, rc, d) for rc, d in SLOTS),
          'level 3 on speed 6: a key in each result slot registers alone and releases')
    sector6 = bytes(e.uc.mem_read(0x08040000, 0x20000))
    watch = []
    e2 = boot(sector6, watch=watch)
    g = scan(e2)
    check((g['speed'], g['level'], g['state']) == (6, 3, IN_EFFECT) and live(e2) == (7, 5, 1) and not watch,
          f"after a restart: speed 6 and level 3 again, calibrated with that filter, no key pressed ({watch})")
    check(all(tap(e2, rc, d) for rc, d in SLOTS) and travel_of(e2, kw) == 0, 'keys register and come back to rest')
    scratch = (syms['_ebss'] + 0x100) & ~3              # COMMAND_RECALIBRATE answers after its 500 ms
    e2.uc.mem_write(scratch, bytes([4]) + bytes(63))
    e2.uc.mem_write(syms['out_buf'], bytes(64))
    enqueue(e2, scratch)
    seen = []
    for _ in range(1500):
        e2.step()
        seen += pressed(e2)
        if e2.uc.mem_read(syms['out_buf'], 1)[0] != 0:
            break
    r0 = e2.uc.mem_read(syms['out_buf'], 1)[0]
    seen += run_watching(e2, 100)
    check(r0 == 4 and not seen and max(travel_of(e2, k) for k in range(68)) == 0 and tap(e2, w),
          f'recalibrating with level 3: every key at rest throughout, and W still registers ({r0}, {sorted(set(seen))})')
    bad_flash = [hex(a) for a in e.flash_report() + e2.flash_report() if not (0x08040000 <= a < 0x08080000)]
    check(not bad_flash, f'no flash changed outside sectors 6/7 {bad_flash}')


def paired_v75(image, syms, rest, build_id, keys, kidx):
    """[23] v7.5: the paired read (one row converted twice per column, a stray
    reading dropped before the keys ever see it), the stray reading log, and
    the pin diagnostics."""
    print('[23] v7.5 (bottom row read twice, stray reading log, pin diagnostics)')
    PROBE, STATUS, GET, SET = 0x6A, 0x6B, 0x68, 0x69
    RUNNING, DONE = 1, 2
    US, STEP = 168, 3000
    PAIRED = 7                                        # three ADCs, next column first, paired row
    ROW = 4                                           # the paired row (PA7, channel 7)

    def boot(journal, rest_values=rest):
        e = Cfw(image, syms, rest_values, journal=journal)
        e.stub_usb_host()
        e.run_until(lambda: e.r8('baseline_ready') == 1 and e.p.regs.get(OTG + 0x18, 0) != 0, 3000)
        e.run_until(lambda: False, 700)
        return e

    def command(e, payload, limit=400):
        scratch = (syms['_ebss'] + 0x100) & ~3
        e.uc.mem_write(scratch, bytes(payload) + bytes(64 - len(payload)))
        e.uc.mem_write(syms['out_buf'], bytes([0]) + bytes([0xAA]) * 63)
        enqueue(e, scratch)
        e.run_until(lambda: e.uc.mem_read(syms['out_buf'], 1)[0] != 0, limit)
        return bytes(e.uc.mem_read(syms['out_buf'], 64))

    def status(e):
        r = command(e, [STATUS, 0])
        w = lambda i: r[i] | r[i + 1] << 8            # noqa: E731
        return dict(proto=r[1], mode=r[2], us=w(3) / US, sample=r[5], pstate=r[7], err=r[8],
                    cycles=int.from_bytes(r[14:18], 'little'), shift=w(18), pfaults=w(21), faults=w(23),
                    pairs=int.from_bytes(r[49:53], 'little'))

    def probe(e, mode, us, sample=2, log2=4, live=0):
        t = round(us * US)
        return command(e, [PROBE, mode, t & 0xFF, t >> 8, sample, log2, live])

    def finish(e, limit=1500):
        e.run_until(lambda: e.r8('probe_state') != RUNNING, limit)
        return status(e)

    def results(e):
        out = []
        for first in range(0, 68, 10):
            r = command(e, [STATUS, 1, first])
            out += [struct.unpack_from('<HHH', r, 4 + 6 * i) for i in range(r[3])]
        return out

    sofs = {}                                         # entry number -> cycle counter at the last USB frame start

    def strays(e, first=0):
        """The stray reading log from entry `first`: total, [(number, cycles, key, kind, a, b)]."""
        out, total = [], 0
        for _ in range(80):
            r = command(e, [STATUS, 4, first & 0xFF, (first >> 8) & 0xFF])
            total = r[1] | r[2] << 8
            n = r[4]
            if not n:
                break
            base_no = (first & ~0xFF) | r[3]
            if base_no < first:
                base_no += 0x100
            for i in range(n):
                cyc, sof, tick_lo, tick_hi, _late, _zero, key, kind, a, b = struct.unpack_from('<IIHBBBBBhh', r, 5 + 19 * i)
                sofs[base_no + i] = sof
                if kind:                              # ordinary readings logged for comparison are left out
                    out.append((base_no + i, cyc, key, kind, a, b))
            first = base_no + n
        return total, out

    def raw(e):
        return [e.r16(syms['adc_raw'] + 2 * i) for i in range(68)]

    def pressed(e):
        return [i for i in range(68) if e.is_pressed(i)]

    def seq(e, base_):
        n = ((R(e, base_ + 0x2C) >> 20) & 0xF) + 1
        return [(R(e, base_ + 0x34) >> (5 * i)) & 0x1F for i in range(n)]

    def settle(e, limit=900):
        e.run_until(lambda: e.r8('speed_state') != 1 and e.r8('probe_state') != RUNNING
                    and e.r8('baseline_pass') == 0xFF, limit)

    R = lambda e, a: e.p.regs.get(a, 0)              # noqa: E731
    confirmed = struct.pack('<I', build_id)
    bottom = [rc for rc in keys if rc[0] == ROW]

    # --- every key from its own sensor ---
    uniq = [[4095 if rest[r][c] == 4095 else 1500 + 300 * r + 11 * c for c in range(COLS)] for r in range(ROWS)]
    e = boot(confirmed, uniq)
    refused = [command(e, [PROBE, m, 0x40, 5, 2, 4, 0])[0] for m in (4, 6, 8)]
    check(refused == [0xFF] * 3, f'the paired read needs three ADCs: modes 4, 6 (and 8) are refused ({refused})')
    for mode, name in ((PAIRED, 'next column first'), (5, 'results stored first')):
        r = probe(e, mode, 8)
        buf = syms['adc_buffer']
        seqs = (seq(e, ADC1), seq(e, ADC2), seq(e, ADC3))
        d = {n: (R(e, st_ + 4), R(e, st_ + 0xC)) for n, (_, st_, _, _) in ADC_DMA.items()}
        st = finish(e)
        res = results(e)
        wrong = [rc for rc in keys if res[kidx[rc]] != (uniq[rc[0]][rc[1]] * 16,) + (uniq[rc[0]][rc[1]],) * 2]
        check(r[0] == PROBE and seqs == ([3, 0], [7, 7], [2, 1])
              and d == {1: (2, buf), 2: (2, buf + 8), 3: (2, buf + 4)},
              f'mode {mode}: ADC1 PA3,PA0  ADC2 PA7 twice  ADC3 PA2,PA1, each into its own slots ({seqs})')
        check(st['pstate'] == DONE and st['pfaults'] == 0 and st['cycles'] == 16 * COLS * STEP and not wrong
              and st['shift'] <= 2,
              f'mode {mode} ({name}): every key gets the reading of its own sensor (wrong: {wrong[:5]})')

    # --- a stray reading never reaches the keys ---
    e = boot(confirmed)
    probe(e, PAIRED, 8, live=1)
    finish(e)
    settle(e)
    check(status(e)['mode'] == PAIRED and not pressed(e), 'paired read live')
    hit = {}                                          # (rank, col) -> counts added to that one conversion

    def rank_effect(n, rank, row, col):
        if n == 2 and row == ROW:
            return hit.pop((rank, col), 0)
        return 0
    e.rank_effect = rank_effect
    space, lctrl = (4, 6), (4, 0)

    def one_visit(e, plan, steps=20):
        """Add counts to chosen conversions of one visit to a column, and
        return how far any key's reading and travel went meanwhile."""
        col = next(iter(plan))[1]
        e.run_until(lambda: e.col_log and e.col_log[-1] == (col - 1) % COLS, 40)
        before = raw(e)
        hit.update(plan)
        worst = travel = 0
        seen = set()
        for _ in range(steps):
            e.step()
            now_ = raw(e)
            worst = max(worst, max(abs(a - b) for a, b in zip(now_, before)))
            travel = max(travel, max(e.r8('key_matrix', k * 10 + 6) for k in range(68)))
            seen.update(pressed(e))
        return worst, travel, sorted(seen)

    total0, _ = strays(e)
    pairs0 = status(e)['pairs']
    a = one_visit(e, {(0, 6): 50})
    b = one_visit(e, {(1, 0): -45})
    st = status(e)
    total, log = strays(e, total0)
    check(a[:2] == (0, 0) and b[:2] == (0, 0) and not a[2] and not b[2],
          f'one of the two readings 50 (or 45) counts off: the key\'s reading does not move at all ({a}, {b})')
    check(st['pairs'] == pairs0 + 2 and total == total0 + 2
          and [(k, kind, x, y) for _, _, k, kind, x, y in log] == [(kidx[space], 1, 50, 0), (kidx[lctrl], 1, 0, -45)],
          f'both are counted and logged with the key and the two readings ({[t[2:] for t in log]})')
    check(len(log) == 2 and log[1][1] > log[0][1], 'with the cycle counter of each, in order')
    c = one_visit(e, {(0, 6): 50, (1, 6): 50})
    total2, log2 = strays(e, total)
    check(c[0] == 50 and status(e)['pairs'] == pairs0 + 2 and log2 and log2[0][2:] == (kidx[space], 2, 50, 50),
          f'both readings off the same way (a disturbance longer than the gap): it gets through, and is logged as '
          f'a jump ({c[0]} counts; {log2[0][2:] if log2 else None})')
    e.run_until(lambda: False, 60)

    # many strays, smoothing as it was before v7.4
    import random
    rng = random.Random(5)
    injected = [0]

    plan = {}

    def noisy(n, rank, row, col):
        # At most one of a visit's two readings is stray, as on the keyboard,
        # where they are rare; only columns with a bottom-row key count
        if n != 2 or row != ROW:
            return 0
        if rank == 0:
            plan.clear()
            if rng.random() < 1 / 20:
                plan[rng.randint(0, 1)] = rng.choice((-1, 1)) * rng.randint(25, 60)
                injected[0] += (ROW, col) in kidx
        return plan.get(rank, 0)
    e.rank_effect = noisy
    pairs0 = status(e)['pairs']
    before = raw(e)
    worst = travel = 0
    for _ in range(1500):                              # 100 scans, about 50 stray readings
        e.step()
        now_ = raw(e)
        worst = max(worst, max(abs(x - y) for x, y in zip(now_, before)))
        travel = max(travel, max(e.r8('key_matrix', k * 10 + 6) for k in range(68)))
    e.rank_effect = None
    got = status(e)['pairs'] - pairs0
    check(injected[0] >= 15 and worst == 0 and travel == 0 and not pressed(e),
          f'{injected[0]} stray readings in 100 scans, classic smoothing: no reading and no travel moves '
          f'(largest move {worst})')
    check(abs(got - injected[0]) <= 2, f'and they are all counted ({got} of {injected[0]})')

    # a real press still gets through at once, alone
    k = kidx[space]
    e.press[space] = 900
    e.run_until(lambda: e.is_pressed(k), 400)
    alone = pressed(e) == [k]
    e.press.pop(space)
    e.run_until(lambda: not e.is_pressed(k), 400)
    check(alone and not pressed(e) and status(e)['pairs'] == pairs0 + got,
          'a real press of Space registers alone and releases, and is not counted as a disagreement')

    # the log keeps the last 64 and hands them out six at a time
    e.rank_effect = lambda n, rank, row, col: 30 if n == 2 and row == ROW and rank == 0 else 0
    e.run_until(lambda: False, 150)                   # 10 scans x 10 bottom-row keys = 100 entries
    e.rank_effect = None
    total, log = strays(e, 0)
    numbers = [t[0] for t in log]
    check(total >= 100 and len(log) == 64 and numbers == list(range(total - 64, total)),
          f'log: {total} entries so far, the last 64 kept and read back three at a time, in order '
          f'({numbers[0]}..{numbers[-1]})')
    r = command(e, [STATUS, 4, total & 0xFF, total >> 8])
    check(r[4] == 0 and (r[1] | r[2] << 8) == total, 'asked from the end: nothing new')

    # an ordinary reading is logged now and then for comparison
    e.run_until(lambda: False, 15 * 230)              # 230 scans x 10 keys: over 2,048 paired readings
    r = command(e, [STATUS, 4, 0, 0])
    kinds = []
    first = e.r32(syms['stray_count']) - 64
    for _ in range(30):
        r = command(e, [STATUS, 4, first & 0xFF, (first >> 8) & 0xFF])
        if not r[4]:
            break
        kinds += [r[5 + 19 * i + 14] for i in range(r[4])]
        first += r[4]
    check(0 in kinds, f'one ordinary paired reading in 2,048 is logged as kind 0, for comparison ({kinds.count(0)} in the log)')

    # USB frame timestamps
    GINTMSK = OTG + 0x18
    check(not R(e, GINTMSK) & (1 << 3) and e.r32(syms['board_sof_cycles']) == 0, 'USB frame starts are not timestamped by default')
    r = command(e, [STATUS, 7, 1])
    check(r[0] == STATUS and R(e, GINTMSK) & (1 << 3), 'request 7 turns the start-of-frame interrupt on')
    e.p.gintsts = 1 << 3
    e.fire(16 + 77)
    e.p.gintsts = 0
    stamp = e.r32(syms['board_sof_cycles'])
    check(stamp == R(e, 0xE0001004) and stamp != 0, f'each frame start notes the cycle counter ({stamp})')
    e.rank_effect = rank_effect
    hit[(0, 6)] = 40
    e.run_until(lambda: (0, 6) not in hit, 40)
    e.rank_effect = None
    e.run_until(lambda: False, 5)
    total, log = strays(e, e.r32(syms['stray_count']) - 3)
    mine = [t for t in log if t[2:] == (kidx[space], 1, 40, 0)]
    check(mine and sofs[mine[-1][0]] == stamp, 'and a logged stray reading carries the last one')
    command(e, [STATUS, 7, 0])
    e.run_until(lambda: False, 5)
    check(e.r32(syms['board_sof_cycles']) == 0 and command(e, [STATUS, 7, 2])[0] == 0xFF,
          'off again: no timestamp kept; any other value is refused')

    # --- pin diagnostics ---
    PA, PB, PC = PORT['A'], PORT['B'], PORT['C']
    reads = [0]

    def wave(port):
        def read(_n):
            reads[0] += 1
            # PA4 high all the time, PA6 toggling every third sample
            return (1 << 4) | (((reads[0] // 3) & 1) << 6) if port == PA else 0
        return read
    e.p.idr = {PA: wave(PA), PC: wave(PC)}
    r = command(e, [STATUS, 5, 0], limit=600)
    samples = (r[2] | r[3] << 8) * 256
    ch = [r[4 + 2 * p] | r[5 + 2 * p] << 8 for p in range(16)]
    hi = [r[36 + p] for p in range(16)]
    check(r[0] == STATUS and r[1] == 0 and samples >= 1024 and abs(ch[6] - samples / 3) <= samples / 30 + 2
          and all(ch[p] == 0 for p in range(16) if p != 6) and hi[4] == 255 and 40 <= hi[6] <= 215 and hi[0] == 0,
          f'pin activity: port A watched for 20 ms ({samples} samples): PA6 changes level {ch[6]} times and is high '
          f'{hi[6] * 100 // 255}% of the time, PA4 is high throughout, nothing else moves')
    r = command(e, [STATUS, 5, 2], limit=600)
    check(r[1] == 2 and not any(r[4:36]), 'port C: nothing changes')
    check(command(e, [STATUS, 5, 5])[0] == 0xFF, 'there is no port 5')
    e.p.idr = {}

    before = {p: (R(e, p), R(e, p + 0xC)) for p in (PA, PB, PC)}
    bits = lambda port, pin: ((R(e, port) >> (2 * pin)) & 3, (R(e, port + 0xC) >> (2 * pin)) & 3)   # noqa: E731
    r = command(e, [STATUS, 6, 0, 6, 4])
    check(r[0] == STATUS and (r[1], r[2], r[3], r[4]) == (0, 6, 0, 2) and bits(PA, 6) == (0, 2),
          'pin override: PA6 becomes an input with pull-down')
    others = all((R(e, p) | m) == (before[p][0] | m) and (R(e, p + 0xC) | m) == (before[p][1] | m)
                 for p, m in ((PA, 3 << 12), (PB, 0), (PC, 0)))
    check(others, 'and no other pin\'s mode or pull changes')
    command(e, [STATUS, 6, 0, 5, 1])
    command(e, [STATUS, 6, 2, 4, 3])
    check(bits(PA, 5) == (3, 0) and bits(PC, 4) == (0, 1), 'PA5 analog, PC4 input with pull-up')
    for port, pin in ((0, 6), (0, 5), (2, 4)):
        command(e, [STATUS, 6, port, pin, 0])
    check(all((R(e, p), R(e, p + 0xC)) == before[p] for p in (PA, PB, PC)),
          'mode 0 puts every pin back as the stock firmware leaves it')
    bad = {'PA7 (a sensor line)': [0, 7, 1], 'PA8 (a column line)': [0, 8, 1], 'PC9 (the LEDs)': [2, 9, 1],
           'PA13 (debug)': [0, 13, 1], 'port 3': [3, 4, 1], 'mode 5 (no outputs)': [0, 6, 5]}
    refused = {why: command(e, [STATUS, 6] + c)[0] for why, c in bad.items()}
    check(all(v == 0xFF for v in refused.values()) and all((R(e, p), R(e, p + 0xC)) == before[p] for p in (PA, PB, PC)),
          f'refused for any other pin or mode ({[w for w, v in refused.items() if v != 0xFF]})')
    e.run_until(lambda: False, 60)
    check(not pressed(e) and status(e)['faults'] == 0, 'and the keyboard carries on scanning')
    bad_flash = [hex(a) for a in e.flash_report() if not (0x08040000 <= a < 0x08080000)]
    check(not bad_flash, f'no flash changed outside sectors 6/7 {bad_flash}')


class UsbHost:
    """A host on the other end of the emulated OTG_HS core, for [22].

    Bus events (reset, suspend, resume, SOF) set their GINTSTS bits, and the
    interrupt runs only when the firmware has unmasked the event and has the
    USB interrupt enabled. SETUP packets arrive the way the core delivers
    them: RXFLVL, the status word popped from GRXSTSP, the packet read from
    the FIFO, then DOEPINT0.STUP. An IN transfer the firmware arms completes
    at once (the host took the packet). That is enough for TinyUSB's own
    enumeration, suspend, resume and remote wake-up code to run unmodified."""

    SOF, RXFLVL, ESUSP, USBSUSP, USBRST, ENUMDNE = 1 << 3, 1 << 4, 1 << 10, 1 << 11, 1 << 12, 1 << 13
    IEPINT, OEPINT, WKUINT = 1 << 18, 1 << 19, 1 << 31
    GINTSTS, GINTMSK, GRXSTSP, FIFO0 = OTG + 0x14, OTG + 0x18, OTG + 0x20, OTG + 0x1000
    DCFG, DCTL, DIEPMSK, DOEPMSK, DAINT, DAINTMSK = (OTG + o for o in (0x800, 0x804, 0x810, 0x814, 0x818, 0x81C))
    NVIC_ISER2, NVIC_ICER2, IRQ_BIT = 0xE000E108, 0xE000E188, 1 << (77 - 64)   # OTG_HS is interrupt 77

    def __init__(self, e: 'Cfw'):
        self.e = e
        self.rx = collections.deque()          # (GRXSTSP word, FIFO words) not yet read
        self.words = collections.deque()       # FIFO words of the entry popped last
        self.diepint = [0] * 6
        self.doepint = [0] * 6
        self.irq_on = False                    # NVIC enable for the OTG_HS interrupt
        self.acks = 0                          # EP0 IN transfers armed (status stages)
        self.stalled = False
        self.wake = []                         # (tick, 1 or 0): DCTL.RWUSIG set / cleared
        p = e.p
        get, write, step = p.get, p.write, e.step

        def get_(a):
            if a == self.GINTSTS:
                return self.status()
            if a == self.GRXSTSP:
                if not self.rx:
                    return 0
                word, data = self.rx.popleft()
                self.words = collections.deque(data)
                if (word >> 17) & 0xF == 4:    # SETUP stage done popped: the core raises STUP
                    self.doepint[0] |= 1 << 3
                return word
            if a == self.FIFO0:
                return self.words.popleft() if self.words else 0
            if a == self.DAINT:
                return self.daint()
            n, reg = divmod(a - (OTG + 0x900), 0x20)
            if 0 <= n < 6 and reg == 8:
                return self.diepint[n]
            n, reg = divmod(a - (OTG + 0xB00), 0x20)
            if 0 <= n < 6 and reg == 8:
                return self.doepint[n]
            return get(a)

        def write_(uc, addr, size, value):
            a = addr & ~3
            if size == 4:
                if a == self.GINTSTS:          # write 1 to clear
                    p.gintsts &= ~value
                elif a == self.NVIC_ISER2 and value & self.IRQ_BIT:
                    self.irq_on = True
                elif a == self.NVIC_ICER2 and value & self.IRQ_BIT:
                    self.irq_on = False
                elif a == self.DCTL and (value ^ p.regs.get(a, 0)) & 1:
                    self.wake.append((e.ticks, value & 1))
                n, reg = divmod(a - (OTG + 0x900), 0x20)
                if 0 <= n < 6 and reg == 8:
                    self.diepint[n] &= ~value
                elif 0 <= n < 6 and reg == 0:
                    if value & (1 << 21) and n == 0:
                        self.stalled = True
                    if value & (1 << 31):      # EPENA: armed, and the host takes it
                        self.diepint[n] |= 1   # XFRC
                        self.acks += n == 0
                    value &= ~(0x33 << 26)     # EPENA, EPDIS, SNAK, CNAK clear themselves
                n, reg = divmod(a - (OTG + 0xB00), 0x20)
                if 0 <= n < 6 and reg == 8:
                    self.doepint[n] &= ~value
            write(uc, addr, size, value)

        def step_(*args, **kw):
            step(*args, **kw)
            self.pump()
        p.get, p.write, e.step = get_, write_, step_

    def daint(self):
        r = self.e.p.regs
        im, om = r.get(self.DIEPMSK, 0), r.get(self.DOEPMSK, 0)
        return (sum(1 << n for n in range(6) if self.diepint[n] & im)
                | sum(1 << (16 + n) for n in range(6) if self.doepint[n] & om))

    def status(self):
        s = self.e.p.gintsts
        if self.rx:
            s |= self.RXFLVL
        d = self.daint() & self.e.p.regs.get(self.DAINTMSK, 0)
        return s | (self.IEPINT if d & 0xFFFF else 0) | (self.OEPINT if d >> 16 else 0)

    def unmasked(self, bits):
        return self.e.p.regs.get(self.GINTMSK, 0) & bits == bits

    def pump(self):
        """Run the OTG_HS interrupt for as long as its line is asserted and
        the firmware has the interrupt enabled (TinyUSB switches it off for
        short stretches; it then runs after a later step)."""
        e = self.e
        for _ in range(16):
            if (e.in_bootloader or not self.irq_on or not e.p.regs.get(OTG + 8, 0) & 1
                    or not self.status() & e.p.regs.get(self.GINTMSK, 0)):
                return
            e.fire(16 + 77)
        raise RuntimeError('OTG_HS interrupt stays asserted')

    def signal(self, bits):
        """A bus event: its status bits, and the interrupt if unmasked.
        Returns whether the firmware had it unmasked."""
        seen = bool(self.e.p.regs.get(self.GINTMSK, 0) & bits)
        self.e.p.gintsts |= bits
        self.pump()
        return seen

    def reset(self):
        """Bus reset, then the end of reset (speed detected: high speed)."""
        self.signal(self.USBRST)
        self.signal(self.ENUMDNE)
        self.e.run_until(lambda: False, 20)       # tud_task handles the reset

    def control(self, request_type, request, value=0, index=0, steps=80):
        """A control request without a data stage. True if the keyboard
        answered it with a status stage and did not stall."""
        w0, w1 = struct.unpack('<II', struct.pack('<BBHHH', request_type, request, value, index, 0))
        acks, self.stalled = self.acks, False
        self.rx.append((6 << 17 | 8 << 4, (w0, w1)))   # SETUP packet, 8 bytes, endpoint 0
        self.rx.append((4 << 17, ()))                  # SETUP stage done
        self.pump()
        self.e.run_until(lambda: self.acks > acks or self.stalled, steps)
        self.e.run_until(lambda: False, 5)             # status stage completion handled
        return self.acks > acks and not self.stalled

    def enumerate(self, address=7):
        """Reset, SET_ADDRESS, SET_CONFIGURATION 1, then SET_FEATURE
        DEVICE_REMOTE_WAKEUP as a PC does before it goes to sleep."""
        self.reset()
        return (self.control(0x00, 5, address) and self.control(0x00, 9, 1)
                and self.control(0x00, 3, 1))


def sleep_lights_v75(image, syms, rest, build_id, base, led_map, keys, kidx):
    """[22] Lighting while the PC is asleep (v7.5): dark while the USB bus is
    suspended after a working connection, back as it was on resume."""
    print('[22] PC sleep v7.5 (LEDs dark while the USB bus is suspended, back as they were on resume)')
    from unicorn import UC_HOOK_CODE
    H = UsbHost
    gamma = json.loads((base / 'backup' / 'stock-gamma.json').read_text())
    key_to_led = [led_map[r * COLS + c] for r, c in keys]
    DEFAULT = [(gamma[97], 0, 0)] * 68                 # the start-up look checked in [10]
    DARK = [(0, 0, 0)] * 68
    W, CAPS, KEYBOARD = (1, 2), (2, 0), 0
    confirmed = struct.pack('<I', build_id)
    slots_at, slots_end = syms['rgb_slots'], syms['rgb_slots'] + 2 * 24 * 68
    LED_STREAM = 0x40026000 + 0x10 + 0x18 * 2          # DMA1 Stream 2 control register

    def decode(sl):
        bits = [1 if v == 67 else 0 for v in sl]
        byte = lambda k: sum(b << (7 - j) for j, b in enumerate(bits[k * 8:(k + 1) * 8]))   # noqa: E731
        return byte(1), byte(0), byte(2)               # (R, G, B) from wire G, R, B

    def boot(journal):
        """A keyboard with a host model on its USB core. Every LED the
        firmware writes is logged from the LED buffer itself, from the moment
        the buffer is being sent to the LEDs (rgb_init() fills it with an
        all-off frame first, as it always has). The host takes every HID
        report at once; whether an interface is ready to send is left to
        TinyUSB (mounted, not suspended)."""
        e = Cfw(image, syms, rest, journal=journal)
        e.host = UsbHost(e)
        e.drawn = []                                   # (LED, (R, G, B)) per LED written
        e.reports = []                                 # (interface, report id, bytes)
        pending = {}

        def on_slot(uc, access, addr, size, value, data):
            if not e.p.regs.get(LED_STREAM, 0) & 1:
                return
            led, slot = divmod((addr - slots_at) // 2, 24)
            pending.setdefault(led, [29] * 24)[slot] = value
            if slot == 23:
                e.drawn.append((led, decode(pending.pop(led))))

        def on_report(uc, addr, size, data):
            itf, rid, buf, n = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
            e.reports.append((itf, rid, bytes(uc.mem_read(buf, n))))
            uc.reg_write(UC_ARM_REG_R0, 1)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        e.uc.hook_add(UC_HOOK_MEM_WRITE, on_slot, begin=slots_at, end=slots_end - 1)
        a = syms['tud_hid_n_report'] & ~1
        e.uc.hook_add(UC_HOOK_CODE, on_report, begin=a, end=a)
        e.run_until(lambda: e.r8('baseline_ready') == 1 and e.p.regs.get(OTG + 0x18, 0) != 0, 3000)
        e.run_until(lambda: False, 120)
        return e

    def leds(e):
        sl = struct.unpack('<%dH' % (68 * 24), e.uc.mem_read(slots_at, 2 * 68 * 24))
        return [decode(sl[i * 24:(i + 1) * 24]) for i in range(68)]

    run = lambda e, steps, **kw: e.run_until(lambda: False, steps, **kw)           # noqa: E731
    asleep = lambda e: e.r8('rgb_asleep') == 1                                     # noqa: E731
    mounted = lambda e: e.call(syms['tud_mounted']) & 1                            # noqa: E731
    suspended = lambda e: e.call(syms['tud_suspended']) & 1                        # noqa: E731
    frames = lambda e: e.r32(syms['frame_count'])                                  # noqa: E731
    tick = lambda e: e.r32(syms['uwTick'])                                         # noqa: E731
    dark_writes = lambda e, since=0: [d for d in e.drawn[since:] if d[1] == (0, 0, 0)]   # noqa: E731
    # Keys held in a keyboard report: modifiers and the key bitmap (the six
    # bytes between them are only there for hosts that use the boot protocol)
    keys_down = lambda rep: rep[0] != 0 or any(rep[8:])                            # noqa: E731
    flash = lambda e: bytes(e.uc.mem_read(0x08000000, 0x80000))                    # noqa: E731

    def settle(e):
        """The picture for the current state is fully in the LED buffer."""
        complete_frame_after(e, syms, tick(e))
        return leds(e)

    def sleep(e, limit=80):
        """The host stops talking (bus suspended). Returns whether the
        suspend interrupt was unmasked, and the LED writes that followed."""
        n = len(e.drawn)
        seen = e.host.signal(H.USBSUSP | H.ESUSP)
        e.run_until(lambda: asleep(e), limit)
        run(e, 10)
        return seen, e.drawn[n:]

    def one_dark_frame(writes):
        """Whatever was left of the frame in progress, then every LED
        written dark exactly once."""
        return (len(writes) >= 68 and sorted(led for led, _ in writes[-68:]) == list(range(68))
                and all(c == (0, 0, 0) for _, c in writes[-68:]))

    def wake(e, bits, limit=80):
        seen = e.host.signal(bits)
        e.run_until(lambda: not asleep(e), limit)
        return seen

    # --- 1. start-up: nothing before a working connection darkens the LEDs ---
    e = boot(confirmed)
    host = e.host
    check(leds(e) == DEFAULT and not asleep(e) and not dark_writes(e),
          f'start-up on a silent bus: the usual start-up look, no LED ever written dark ({len(e.drawn)} LED writes)')
    seen = host.signal(H.USBSUSP | H.ESUSP)
    run(e, 100)
    check(not seen and not suspended(e) and not asleep(e) and not dark_writes(e),
          'bus idle before the host has reset it: the suspend interrupt is still masked, nothing happens')
    host.reset()
    seen = host.signal(H.USBSUSP | H.ESUSP)
    run(e, 100)
    check(seen and not suspended(e) and not asleep(e) and not dark_writes(e) and leds(e) == DEFAULT,
          'bus idle after a reset, before the host has sent anything: TinyUSB ignores the suspend, LEDs unchanged')
    host.reset()
    ok = host.control(0x00, 5, 7)                      # SET_ADDRESS 7
    addr = (e.p.regs.get(H.DCFG, 0) >> 4) & 0x7F
    seen = host.signal(H.USBSUSP | H.ESUSP)
    run(e, 150)
    check(ok and addr == 7 and seen and suspended(e) and not mounted(e) and not asleep(e)
          and not dark_writes(e) and leds(e) == DEFAULT,
          f'addressed (address {addr}) but not configured, bus suspended: TinyUSB reports the suspend, '
          'but the LEDs stay on (no working connection yet)')
    host.signal(H.WKUINT)
    ok = host.control(0x00, 9, 1) and host.control(0x00, 3, 1)    # SET_CONFIGURATION 1, wake-up allowed
    run(e, 60)
    ready = e.call(syms['tud_hid_n_ready'], KEYBOARD) & 1
    check(ok and mounted(e) and not suspended(e) and ready and leds(e) == DEFAULT and not dark_writes(e),
          f'the host configures the keyboard: mounted, keyboard interface ready, and the LEDs never went dark '
          f'on the way ({len(e.drawn)} LED writes, none dark)')

    # --- 2. suspend on a still picture (Solid, Caps Lock indicator showing) ---
    buf = (syms['_ebss'] + 0x200) & ~3
    e.uc.mem_write(buf, bytes([0x02]))
    e.call(syms['tud_hid_set_report_cb'], KEYBOARD, 0, 2, buf, 1)    # host: Caps Lock on
    before = settle(e)
    caps = key_to_led[kidx[CAPS]]
    cfg = bytes(e.uc.mem_read(syms['rgb_config'], 16))
    colours = bytes(e.uc.mem_read(syms['rgb_key_colors'], 204))
    flash0, writes0, erases0 = flash(e), len(e.flash_writes), len(e.p.erases)
    seen, writes = sleep(e)
    check(seen and suspended(e) and asleep(e) and len(writes) == 68 and one_dark_frame(writes) and leds(e) == DARK,
          f'bus suspended after a working connection: one all-off frame ({len(writes)} LED writes, all dark), '
          'every LED off')
    n, f0, cols0, t0 = len(e.drawn), frames(e), len(e.col_log), e.ticks
    run(e, 400)
    check(len(e.drawn) == n and frames(e) == f0 and leds(e) == DARK and len(e.col_log) > cols0 + 300,
          f'asleep for {e.ticks - t0} ms: no LED written, no frame started, '
          f'key scan still running ({len(e.col_log) - cols0} columns converted)')

    # --- 3. a key press while suspended asks the host to wake up ---
    r0 = len(e.reports)
    host.wake.clear()
    e.press[W] = -900
    e.run_until(lambda: e.is_pressed(kidx[W]), 400)
    pressed = e.is_pressed(kidx[W])
    e.run_until(lambda: len(host.wake) >= 2, 900)      # RWUSIG is held for about 1 ms of CPU time
    sof = host.unmasked(H.SOF)
    check(pressed and [v for _, v in host.wake] == [1, 0] and sof,
          f'W pressed while suspended: it registers, and the keyboard signals remote wake-up '
          f'(DCTL.RWUSIG set, then cleared: {host.wake}); SOF interrupt unmasked to see the bus come back')
    run(e, 100)
    held = [r for r in e.reports[r0:] if r[0] == KEYBOARD]
    check(asleep(e) and len(e.drawn) == n and leds(e) == DARK and not held,
          'until the host answers: still dark, nothing drawn, the key report held back')
    host.signal(H.SOF)                                 # the host resumed the bus: frames are back
    e.run_until(lambda: not asleep(e), 80)
    e.run_until(lambda: any(r[0] == KEYBOARD and keys_down(r[2]) for r in e.reports[r0:]), 200)
    sent = [r[2] for r in e.reports[r0:] if r[0] == KEYBOARD and keys_down(r[2])]
    after = settle(e)
    check(not suspended(e) and not asleep(e) and after == before and before[caps] != before[0]
          and before != DARK,
          f'the bus comes back (first SOF): lighting exactly as before the sleep, Caps Lock indicator '
          f'included (Caps {after[caps]}, others {after[0]})')
    check(bool(sent), f'and the key that woke the PC is then sent ({len(sent)} keyboard report(s) with a key)')
    e.press.pop(W)
    e.run_until(lambda: not e.is_pressed(kidx[W]), 400)
    run(e, 40)
    check(bytes(e.uc.mem_read(syms['rgb_config'], 16)) == cfg
          and bytes(e.uc.mem_read(syms['rgb_key_colors'], 204)) == colours
          and flash(e) == flash0 and len(e.flash_writes) == writes0 and len(e.p.erases) == erases0,
          f'settings and key colours untouched, nothing written to flash by the suspend and resume '
          f'({len(e.flash_writes) - writes0} flash writes)')

    # --- 4. suspend on an animated effect, resume from the host (WKUINT) ---
    e.uc.mem_write(syms['rgb_config'] + 1, bytes([2]))             # Rainbow Wave (not a settings change)
    wait_frames(e, 3)
    cfg = bytes(e.uc.mem_read(syms['rgb_config'], 16))
    seen, writes = sleep(e)
    n, f0 = len(e.drawn), frames(e)
    run(e, 400)
    check(seen and one_dark_frame(writes) and leds(e) == DARK and len(e.drawn) == n and frames(e) == f0,
          f'Rainbow Wave, bus suspended: the frame in progress is finished, then one all-off frame, '
          f'and nothing is drawn for 400 ms ({len(e.drawn) - n} LED writes, {frames(e) - f0} frames)')
    seen = wake(e, H.WKUINT)
    f0, n = frames(e), len(e.drawn)
    wait_frames(e, 3)
    pic1 = leds(e)
    wait_frames(e, 3)
    pic2 = leds(e)
    drawn, nf = len(e.drawn) - n, frames(e) - f0
    check(seen and not suspended(e) and drawn >= 68 * (nf - 1) and nf >= 6 and pic1 != pic2
          and all(sum(c) > 0 for c in pic1 + pic2) and not dark_writes(e, n)
          and bytes(e.uc.mem_read(syms['rgb_config'], 16)) == cfg,
          f'the host resumes the bus (WKUINT): Rainbow Wave is running again ({drawn} LED writes over {nf} '
          'frames, every LED lit, the picture moving), same settings')

    # --- 5. a PC starting from shut-down resets the bus without resuming it ---
    e.uc.mem_write(syms['rgb_config'] + 1, bytes([0]))             # Solid
    wait_frames(e, 3)
    before = settle(e)
    seen, writes = sleep(e)
    was_dark = one_dark_frame(writes) and leds(e) == DARK
    host.reset()
    e.run_until(lambda: not asleep(e), 80)
    unmounted = not mounted(e)
    after = settle(e)
    check(seen and was_dark and unmounted and after == before,
          'suspended, then a bus reset with no resume before it (PC switched on): the lighting is back at once, '
          'before the host has configured the keyboard again')
    check(host.enumerate() and mounted(e), 'and the keyboard enumerates again')

    # --- 6. idle dimming carries on where it was ---
    def set_idle(minutes):
        e.uc.mem_write(syms['rgb_config'] + 9, bytes([minutes]))

    travel = lambda: max(e.r8('key_matrix', k * 10 + 6) for k in range(68))        # noqa: E731

    def tap(rc):
        e.press[rc] = -900
        e.run_until(lambda: e.is_pressed(kidx[rc]), 400)
        run(e, 30)
        e.press.pop(rc)
        e.run_until(lambda: not e.is_pressed(kidx[rc]) and travel() == 0, 600)     # fully back up
        run(e, 30)

    set_idle(1)                                        # fade out after 1 minute without typing
    tap(W)                                             # typed just before the PC goes to sleep
    lit = settle(e)
    seen, writes = sleep(e)
    t0, n, f0 = e.ticks, len(e.drawn), frames(e)
    writes1 = len(e.flash_writes)
    e.run_until(lambda: e.ticks >= t0 + 70000, 8000, ticks=10)     # asleep for longer than the idle time
    slept = e.ticks - t0
    check(seen and lit == before and one_dark_frame(writes) and slept >= 70000 and not e.in_bootloader
          and asleep(e) and leds(e) == DARK and len(e.drawn) == n and frames(e) == f0
          and len(e.flash_writes) == writes1,
          f'confirmed build asleep for {slept // 1000} s: stays in the application, dark, nothing drawn, '
          'nothing written to flash')
    wake(e, H.WKUINT)
    after = settle(e)
    run(e, 300)
    check(after == lit and leds(e) == lit and e.r8('rgb_idle_level') == 255,
          f'idle dimming set to 1 minute, lit before a {slept // 1000} s sleep: lit again after it and stays lit '
          '(the time asleep does not count as time without typing)')
    e.uc.mem_write(syms['rgb_last_activity'], struct.pack('<I', (tick(e) - 61000) & 0xFFFFFFFF))
    e.uc.mem_write(syms['rgb_idle_level'], bytes([0]))             # as after a minute without typing
    faded = settle(e)
    seen, writes = sleep(e)
    was_dark = one_dark_frame(writes) and leds(e) == DARK
    wake(e, H.WKUINT)
    after = settle(e)
    run(e, 200)
    check(all(c == (0, 0, 0) for i, c in enumerate(faded) if i != caps) and faded[caps] == lit[caps]
          and was_dark and after == faded and leds(e) == faded and e.r8('rgb_idle_level') == 0 and not asleep(e),
          'faded out by idle dimming before the sleep (only the Caps Lock indicator showing): the same after a '
          'wake without a key press')
    sleep(e)
    host.wake.clear()
    r0 = len(e.reports)
    e.press[W] = -900                                  # a tap: down, wake-up signalled, up again
    e.run_until(lambda: len(host.wake) >= 2, 1300)
    e.press.pop(W)
    run(e, 150)
    still_dark = asleep(e) and leds(e) == DARK
    host.signal(H.SOF)                                 # only now does the host bring the bus back
    e.run_until(lambda: not asleep(e), 80)
    e.run_until(lambda: e.r8('rgb_idle_level') == 255 and not e.is_pressed(kidx[W]), 600)
    after = settle(e)
    run(e, 40)
    typed = [keys_down(r[2]) for r in e.reports[r0:] if r[0] == KEYBOARD]
    check([v for _, v in host.wake] == [1, 0] and still_dark and after == lit,
          'and when a key is tapped there, the PC is asked to wake, and the lighting comes back once it has')
    check(typed[:1] == [True] and typed[-1:] == [False] and not e.is_pressed(kidx[W]),
          f'the tap reaches the host after the wake as a press and a release, no key left stuck ({typed})')
    set_idle(0)
    bad = [hex(a) for a in e.flash_report() if not (0x08040000 <= a < 0x08080000)]
    check(not bad and not e.in_bootloader and not e.fault, f'no flash changed outside sectors 6/7 {bad}')

    # --- 7. fail-safe: an unconfirmed build that is put to sleep ---
    e = boot(b'')
    host = e.host
    ok = host.enumerate()
    t_mount = e.ticks
    run(e, 40)
    flash0 = flash(e)
    seen, writes = sleep(e)
    t_sleep, n = e.ticks, len(e.drawn)
    armed = e.r8('failsafe_armed')
    e.run_until(lambda: e.ticks >= 9600, 3000, ticks=4)
    flash1 = flash(e)
    diff = [0x08000000 + i for i in range(0, 0x80000, 4) if flash1[i:i + 4] != flash0[i:i + 4]]
    check(ok and seen and armed == 1 and t_sleep < 4000 and one_dark_frame(writes),
          f'unconfirmed build: enumerates at {t_mount} ms and is suspended at {t_sleep} ms, '
          'before its 5 s confirmation: LEDs off')
    check(e.ticks >= 9600 and not e.in_bootloader and e.r8('usb_fallback_done') == 0 and e.r8('usb_use_hs') == 1
          and asleep(e) and leds(e) == DARK and len(e.drawn) == n,
          f'still suspended at {e.ticks} ms: no full-speed restart at 4 s, no hand-over to the bootloader at 9 s, '
          'still dark')
    check(e.r8('failsafe_armed') == 0 and diff == [0x08040000] and e.r32(0x08040000) == build_id,
          f'the build is confirmed after its 5 s mounted as usual, and that entry is the only flash write '
          f'while suspended ({[hex(a) for a in diff]})')
    wake(e, H.WKUINT)
    check(settle(e) == DEFAULT and not e.in_bootloader, 'and its lighting comes back on resume')

    # --- 8. fail-safe: an unconfirmed build on a bus that only ever looks suspended ---
    e = boot(b'')
    host = e.host
    host.signal(H.USBSUSP | H.ESUSP)                   # idle bus from the start
    host.reset()                                       # a reset, but the host never sends anything
    seen = host.signal(H.USBSUSP | H.ESUSP)
    e.run_until(lambda: e.ticks >= 4100, 6000, ticks=4)
    fell_back = e.r8('usb_use_hs') == 0 and e.r8('usb_fallback_done') == 1 and not e.in_bootloader
    host.signal(H.USBSUSP | H.ESUSP)
    never_dark = not dark_writes(e) and not asleep(e) and leds(e) == DEFAULT
    e.run_until(lambda: e.in_bootloader, 3000, ticks=4)
    check(seen and fell_back and never_dark and e.in_bootloader and 8900 <= e.ticks <= 9100,
          f'unconfirmed build that never enumerates on an idle bus: LEDs stay on, full-speed restart at 4 s, '
          f'and the vendor bootloader after ~9 s as before (t={e.ticks} ms)')
    bad = [hex(a) for a in e.flash_report() if not (0x08040000 <= a < 0x08080000)]
    check(not bad, f'no flash changed outside sectors 6/7 {bad}')


def main_loop_v75(image, syms, rest, build_id, keys, kidx):
    """[24] v7.5: a main loop pass leaves a key alone when its filtered value
    did not change, and a changed setting still reaches such a key (one pass
    per millisecond goes through every key)."""
    print('[24] v7.5 main loop (keys nothing happened to are left alone, settings still reach them)')
    SET_ACTUATION, STATUS = 131, 0x6B
    RUNNING = 1
    confirmed = struct.pack('<I', build_id)
    rc = (1, 2)                                      # W
    k = kidx[rc]

    def boot(speed, level):
        journal = confirmed.ljust(0x10000, b'\xff') + rgb_record(3, bytes([speed, level, 0, 0]))
        e = Cfw(image, syms, rest, journal=journal)
        e.stub_usb_host()
        e.run_until(lambda: e.r8('baseline_ready') == 1 and e.p.regs.get(OTG + 0x18, 0) != 0, 3000)
        e.run_until(lambda: False, 700)
        e.run_until(lambda: e.r8('speed_state') != 1 and e.r8('probe_state') != RUNNING
                    and e.r8('baseline_pass') == 0xFF, 900)
        return e

    def command(e, payload):
        scratch = (syms['_ebss'] + 0x100) & ~3
        e.uc.mem_write(scratch, bytes(payload) + bytes(64 - len(payload)))
        e.uc.mem_write(syms['out_buf'], bytes([0]) + bytes([0xAA]) * 63)
        enqueue(e, scratch)
        e.run_until(lambda: e.uc.mem_read(syms['out_buf'], 1)[0] != 0, 400)
        return bytes(e.uc.mem_read(syms['out_buf'], 64))

    def filtered(e):
        return e.r16(syms['key_matrix'] + k * 10)

    def travel(e):
        return e.r8('key_matrix', k * 10 + 6)

    def seen_by_layout(e):
        # the keycode the layout holds for the key while it is down (0 = none)
        return e.r8('active_keycodes', k) != 0

    def actuation(e, point, rt_down=0):
        """Set W's actuation point; returns the milliseconds until the key's state follows, and
        whether its filtered value stayed the same throughout."""
        want = travel(e) >= point if rt_down == 0 else travel(e) > point
        before = filtered(e)
        r = command(e, [SET_ACTUATION, 0, k, 1, point, rt_down, 0, 0])
        t0 = e.ticks
        e.run_until(lambda: bool(e.is_pressed(k)) == want and seen_by_layout(e) == want, 40)
        took = e.ticks - t0
        ok = r[0] == SET_ACTUATION and bool(e.is_pressed(k)) == want and seen_by_layout(e) == want
        e.run_until(lambda: False, 5)
        moved.append((before, filtered(e)))
        return ok, took, filtered(e) == before, want

    moved = []
    for speed, level, name in ((6, 3, 'Max, Fast smoothing'), (3, 1, 'Fastest, Smooth'), (6, 0, 'Max, Classic')):
        e = boot(speed, level)
        mode = command(e, [STATUS, 0])[2]
        # hold W part of the way down, until its travel stands still (Smooth at
        # one-ADC speeds can take a while to settle its last count)
        e.press[rc] = -300
        prev, same = None, 0
        for _ in range(3000):
            e.step()
            now = (filtered(e), travel(e))
            same = same + 1 if now == prev else 0
            prev = now
            if same >= 200:
                break
        d = travel(e)
        check(same >= 200 and 30 < d < 220 and mode == (7 if speed > 3 else 0),
              f'{name}: W held part of the way down reads a steady travel of {d} (read mode {mode})')
        steps = []
        ok1, t1, still1, want1 = actuation(e, d - 15)
        ok2, t2, still2, want2 = actuation(e, d + 15)
        ok3, t3, still3, want3 = actuation(e, d - 15, rt_down=10)
        ok4, t4, still4, want4 = actuation(e, d + 15, rt_down=10)
        steps = [t1, t2, t3, t4]
        check(ok1 and ok2 and (want1, want2) == (True, False) and still1 and still2 and max(t1, t2) <= 3,
              f'{name}: its actuation point moved below, then above where it is held: pressed, then released, '
              f'within {max(t1, t2)} ms and without its filtered value changing '
              f'(ok {ok1, ok2}, want {want1, want2}, filtered before/after {moved[-4:-2]})')
        check(ok3 and ok4 and (want3, want4) == (True, False) and still3 and still4 and max(t3, t4) <= 3,
              f'{name}: the same with Rapid Trigger on ({steps[2:]} ms)')
        e.press.pop(rc)
        e.run_until(lambda: travel(e) == 0 and not e.is_pressed(k), 800)
        others = [i for i in range(68) if e.is_pressed(i)]
        check(travel(e) == 0 and not e.is_pressed(k) and not seen_by_layout(e) and not others,
              f'{name}: let go, W is back at rest and no key is pressed')
        bad_flash = [hex(a) for a in e.flash_report() if not (0x08040000 <= a < 0x08080000)]
        check(not bad_flash, f'{name}: no flash changed outside sectors 6/7 {bad_flash}')


def input_pause_v76(image, syms, rest, build_id, keys, kidx):
    """[25] v7.6: PAUSE_INPUT (0x6D) silences the keys for calibration. Keys
    held when it starts are released, nothing reaches the PC (keys, layers,
    Fn+Ins, controller) while readings and calibration carry on, it lasts
    while the host repeats it, ends by itself, and a key held when it ends
    stays silent until it is let go."""
    print('[25] v7.6 typing pause (silent keys, readings and calibration go on, ends by itself)')
    from unicorn import UC_HOOK_CODE
    PAUSE, RECALIBRATE = 0x6D, 4
    SET_OPTIONS, SET_GP_BUTTONS, SET_GP_OPTIONS = 10, 137, 139
    LS_UP = 16
    W, A_, S, LSHIFT, FN, INS = (1, 2), (2, 1), (2, 2), (3, 0), (4, 10), (0, 14)
    check(all(rc in kidx for rc in (W, A_, S, LSHIFT, FN, INS)), 'test keys are populated positions')

    def boot(controller=False):
        e = Cfw(image, syms, rest, journal=struct.pack('<I', build_id))
        e.stub_usb_host()
        sent = []
        if controller:
            # A host that has the controller interface configured and polls it
            def ret(val):
                def h(uc, addr, size, data):
                    uc.reg_write(UC_ARM_REG_R0, val)
                    uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
                return h

            def xfer(uc, addr, size, data):
                ep, buf, n = (uc.reg_read(r) for r in (UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
                if ep == 0x84:
                    sent.append(bytes(uc.mem_read(buf, n)))
                uc.reg_write(UC_ARM_REG_R0, 1)
                uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
            for name, h in (('tud_mounted', ret(1)), ('tud_suspended', ret(0)), ('usbd_edpt_busy', ret(0)),
                            ('usbd_edpt_claim', ret(1)), ('usbd_edpt_release', ret(1)), ('usbd_edpt_xfer', xfer)):
                a = syms[name] & ~1
                e.uc.hook_add(UC_HOOK_CODE, h, begin=a, end=a)
        e.run_until(lambda: e.r8('baseline_ready') == 1 and e.p.regs.get(OTG + 0x18, 0) != 0, 3000)
        e.run_until(lambda: False, 200)
        return e, sent

    def command(e, payload, limit=400):
        scratch = (syms['_ebss'] + 0x100) & ~3
        e.uc.mem_write(scratch, bytes(payload) + bytes(64 - len(payload)))
        e.uc.mem_write(syms['out_buf'], bytes(64))
        enqueue(e, scratch)
        e.run_until(lambda: e.uc.mem_read(syms['out_buf'], 1)[0] != 0, limit)
        return bytes(e.uc.mem_read(syms['out_buf'], 64))

    def pause(e, ms):
        return command(e, [PAUSE, 1, ms & 0xFF, ms >> 8])

    def state(e, action=0):
        r = command(e, [PAUSE, action])
        return {'id': r[0], 'proto': r[1], 'paused': r[2], 'left': r[3] | r[4] << 8, 'held': r[5]}

    def kb(e):
        return bytes(e.uc.mem_read(syms['kb_report'], 28))

    def typed(e, rc):
        return e.r8('active_keycodes', kidx[rc])

    def travel(e, rc):
        return e.r8('key_matrix', kidx[rc] * 10 + 6)

    def bottom(e, rc):
        return e.r16(syms['key_matrix'] + kidx[rc] * 10 + 4)

    def hold(e, rc, delta=-900):
        e.press[rc] = delta
        e.run_until(lambda: e.is_pressed(kidx[rc]), 400)
        e.run_until(lambda: False, 30)

    def let_go(e, rc):
        e.press.pop(rc, None)
        e.run_until(lambda: not e.is_pressed(kidx[rc]), 400)
        e.run_until(lambda: False, 30)

    silent = lambda e: not any(kb(e))

    e, _ = boot()
    s = state(e)
    check(s['id'] == PAUSE and s['proto'] == 1 and not s['paused'] and s['held'] == 0,
          f'PAUSE_INPUT status: protocol 1, not paused after power-up ({s})')
    hold(e, W)
    check(typed(e, W) != 0 and not silent(e), 'before the pause: W held types')

    # --- pause with W held ---
    r = pause(e, 3000)
    e.run_until(lambda: False, 10)
    left = r[3] | r[4] << 8
    check(r[0] == PAUSE and r[2] == 1 and 2950 <= left <= 3000, f'pause for 3 s: paused, {left} ms left')
    check(typed(e, W) == 0 and silent(e), f'the held W is released: nothing in the keyboard report ({kb(e)[:8].hex()})')
    check(e.is_pressed(kidx[W]) and travel(e, W) > 200,
          f'while the keyboard still reads W as held all the way ({travel(e, W)})')
    hold(e, LSHIFT)
    hold(e, A_)
    check(silent(e) and typed(e, LSHIFT) == 0 and typed(e, A_) == 0 and e.is_pressed(kidx[A_]),
          'Shift and A pressed while paused: read by the keyboard, nothing typed')
    hold(e, FN)
    hold(e, INS)
    e.run_until(lambda: e.in_bootloader, 60)
    check(not e.in_bootloader and e.r8('layer_mask') == 0 and silent(e),
          f'Fn+Ins while paused: no Fn layer, no update mode (layer mask {e.r8("layer_mask")})')
    for rc in (INS, FN, A_, LSHIFT, W):
        let_go(e, rc)
    check(silent(e) and state(e)['paused'] == 1, 'all let go: still paused, still silent')

    # --- calibration carries on while paused ---
    command(e, [RECALIBRATE], limit=2000)
    e.run_until(lambda: False, 50)
    pause(e, 3000)
    b0 = bottom(e, S)
    hold(e, S)
    e.run_until(lambda: False, 60)
    b1 = bottom(e, S)
    check(b1 > b0 + 200 and typed(e, S) == 0 and silent(e),
          f'after Recalibrate, S pressed all the way while paused: its bottom-out value is learned ({b0} -> {b1}), nothing typed')

    # --- the host keeps it going ---
    for _ in range(4):
        e.run_until(lambda: False, 900)
        pause(e, 1500)
    s = state(e)
    check(s['paused'] == 1 and typed(e, S) == 0 and silent(e),
          f'repeated every 0.9 s for 3.6 s: still paused, S still silent ({s["left"]} ms left)')

    # --- the host stops asking: the pause ends by itself ---
    e.run_until(lambda: False, 1600)
    s = state(e)
    check(s['paused'] == 0 and s['left'] == 0 and s['held'] == 1,
          f'no repeat for 1.6 s: the pause has ended by itself, S counted as held over ({s})')
    e.run_until(lambda: False, 50)
    check(typed(e, S) == 0 and silent(e), 'S held since the pause stays silent')
    let_go(e, S)
    check(state(e)['held'] == 0, 'S let go: no key held over')
    hold(e, S)
    check(typed(e, S) != 0 and not silent(e), 'S pressed again: types')
    let_go(e, S)

    # --- ended by the host with keys held ---
    pause(e, 3000)
    hold(e, W)
    hold(e, LSHIFT)
    s = state(e, 2)
    e.run_until(lambda: False, 50)
    check(s['paused'] == 0 and s['held'] == 2 and silent(e) and typed(e, W) == 0 and typed(e, LSHIFT) == 0,
          f'pause ended with W and Shift held: both stay silent ({s})')
    let_go(e, W)
    hold(e, W)
    rep = kb(e)
    check(typed(e, W) != 0 and typed(e, LSHIFT) == 0 and rep[0] == 0 and any(rep[8:]),
          f'W let go and pressed again types, Shift still held from the pause does not (modifiers {rep[0]:#x})')
    let_go(e, W)
    let_go(e, LSHIFT)
    check(state(e)['held'] == 0 and silent(e), 'everything let go: nothing held over, report empty')
    s = state(e, 2)
    check(s['id'] == PAUSE and s['paused'] == 0, 'ending a pause that is not on is harmless')

    # --- bad requests ---
    bad = [command(e, p)[0] for p in ([PAUSE, 1, 0, 0], [PAUSE, 1, 0x11, 0x27], [PAUSE, 3])]
    check(bad == [0xFF] * 3, f'0 ms, 10001 ms and an unknown action are refused {[hex(b) for b in bad]}')
    r = pause(e, 10000)
    check(r[0] == PAUSE and r[2] == 1 and (r[3] | r[4] << 8) == 10000, 'the longest pause (10 s) is accepted')
    state(e, 2)
    bad_flash = [hex(a) for a in e.flash_report() if not (0x08040000 <= a < 0x08080000)]
    check(not bad_flash and not e.in_bootloader, f'no flash changed outside sectors 6/7 {bad_flash}')

    # --- controller output ---
    e, sent = boot(controller=True)
    command(e, [SET_OPTIONS, 5, 0])
    command(e, [SET_GP_BUTTONS, 0, kidx[W], 1, LS_UP])
    command(e, [SET_GP_OPTIONS, 0, 4, 20, 85, 95, 165, 170, 255, 255, 0x0B])
    e.uc.mem_write(syms['endpoint_in'], bytes([0x84]))      # host opened the interface
    e.run_until(lambda: False, 40)

    def stick():
        return struct.unpack_from('<HBBhhhh', sent[-1], 2) if sent else (0, 0, 0, 0, 0, 0, 0)
    hold(e, W)
    e.run_until(lambda: stick()[4] >= 32000, 400)
    e.run_until(lambda: False, 80)               # key travel settles over a few scans
    check(stick()[4] == 32640, f'controller: W bound to the left stick pushes it all the way up ({stick()[4]})')
    pause(e, 3000)
    e.run_until(lambda: stick()[4] == 0, 100)
    e.run_until(lambda: False, 50)
    check(stick()[:5] == (0, 0, 0, 0, 0), f'paused with W held: stick centred, no buttons {stick()[:5]}')
    state(e, 2)
    e.run_until(lambda: False, 60)
    check(stick()[4] == 0, f'pause ended with W held: the stick stays centred until W is let go ({stick()[4]})')
    let_go(e, W)
    hold(e, W)
    e.run_until(lambda: stick()[4] >= 32000, 400)
    e.run_until(lambda: False, 80)
    check(stick()[4] == 32640, f'W pressed again: stick up again ({stick()[4]})')


def check_handoff(e: Cfw):
    uc = e.uc
    check(e.r32(0x2001FFFC) == 0x96969696, 'RAM magic 0x96969696 at 0x2001FFFC (as stock)')
    check(e.p.regs.get(0xE000ED08) == 0x08000000, 'VTOR = 0x08000000')
    check(uc.reg_read(UC_ARM_REG_SP) == STUB_SP, 'MSP loaded from the bootloader vector table')
    log = e.p.log[-400:] if e.p.log else []
    check(e.p.regs.get(0x40023808) == 0 and e.p.regs.get(0x40023804) == 0x24003010,
          'RCC back to reset values (CFGR 0, PLLCFGR 0x24003010) as stock')
    check(all(e.p.regs.get(0xE000E180 + 4 * i) == 0xFFFFFFFF for i in range(8)), 'all NVIC interrupts disabled')
    check(e.p.regs.get(0xE000E010, 1) == 0, 'SysTick stopped')


if __name__ == '__main__':
    sys.exit(main())
