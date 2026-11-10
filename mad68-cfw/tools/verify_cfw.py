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
                               UC_ARM_REG_XPSR, UC_ARM_REG_APSR, UC_ARM_REG_FPSCR,
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
DMA2_S0 = 0x40026400 + 0x10
TIM10 = 0x40014400
ADC1 = 0x40012000
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
        self.adc_started = False
        self.erases: list[int] = []
        self.gintsts = 0

    def get(self, a):
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
        return super().get(a)

    def write(self, uc, addr, size, value):
        super().write(uc, addr, size, value)
        a = addr & ~3
        if a == ADC1 + 8 and value & (1 << 30):
            self.adc_started = True
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
        self.col_log: list[int | None] = []
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
        regs = self.p.regs
        if not self.p.adc_started or not (regs.get(DMA2_S0, 0) & 1):
            return False
        self.p.adc_started = False
        n = regs.get(DMA2_S0 + 4, 0)
        m0 = regs.get(DMA2_S0 + 0xC, 0)
        sqr3, sqr2 = regs.get(ADC1 + 0x34, 0), regs.get(ADC1 + 0x30, 0)
        cols = self.active_cols()
        self.col_log.append(cols[0] if len(cols) == 1 else None)
        vals = []
        for rank in range(n):
            ch = ((sqr3 >> (5 * rank)) if rank < 6 else (sqr2 >> (5 * (rank - 6)))) & 0x1F
            row = ROW_CH.get(ch)
            if row is None or len(cols) != 1:
                vals.append(0)
                continue
            c = cols[0]
            v = self.rest[row][c] + self.press.get((row, c), 0)
            vals.append(max(0, min(4095, v)))
        self.uc.mem_write(m0, struct.pack('<%dH' % n, *vals))
        regs[DMA2_S0 + 4] = 0
        regs[0x40026400] = 0x20                     # LISR: TCIF0
        self.fire(16 + 56)                          # DMA2_Stream0
        regs[0x40026400] = 0
        return True

    def step(self, instructions=3000, ticks=1):
        if self.in_bootloader:
            return
        self.run_thread(instructions)
        if self.in_bootloader:
            return
        regs = self.p.regs
        for _ in range(ticks):
            if regs.get(0xE000E010, 0) & 3 == 3:
                self.fire(15)
                self.ticks += 1
                if self.in_bootloader:
                    return
        if regs.get(TIM10, 0) & 1 and regs.get(TIM10 + 0xC, 0) & 1:
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
    """[18] GET_FIRMWARE_INFO and the release marker (v7.2)."""
    print('[18] firmware info v7.2 (release, build ID, marker in the image)')
    GET_FIRMWARE_INFO, MARKER = 0x67, b'MAD68CFW v7.2.0'
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
    check(r[0] == GET_FIRMWARE_INFO and tuple(r[1:4]) == (7, 2, 0),
          f'GET_FIRMWARE_INFO reports release 7.2.0 ({tuple(r[1:4])}, reply {r[0]:#x})')
    check(bid == build_id, f'and the ID of this build ({bid:#010x} vs {build_id:#010x})')
    check(r[8:32] == MARKER + bytes(24 - len(MARKER)),
          f'and the marker, zero-padded over whatever was in the buffer ({r[8:32]!r})')
    check(image.count(MARKER + bytes(1)) == 1,
          f'the marker is in the image exactly once, so the app can read the release from a file ({image.count(MARKER)})')
    r = command([0x68])
    check(r[0] == 0xFF, f'the next unused board command (0x68) is still refused ({r[0]:#x})')
    bad = [hex(a) for a in e.flash_report() if not (0x08040000 <= a < 0x08080000)]
    check(not bad, f'no flash changed outside sectors 6/7 {bad}')


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
