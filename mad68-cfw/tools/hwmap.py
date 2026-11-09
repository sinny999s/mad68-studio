"""Boot the stock firmware in the emulator and summarise the hardware setup.

Prints GPIO pin modes, ADC/DMA/timer/USB configuration and enabled IRQs as
left behind by the vendor init code.
"""
from __future__ import annotations

import collections
import sys

from emu import Emu

PORTS = {chr(ord('A') + i): 0x40020000 + 0x400 * i for i in range(9)}
MODE = {0: 'in', 1: 'out', 2: 'AF', 3: 'analog'}
PULL = {0: '', 1: 'pu', 2: 'pd', 3: '?'}

ADC_CH = {  # STM32F405/407 ADC123 channel -> pin
    0: 'PA0', 1: 'PA1', 2: 'PA2', 3: 'PA3', 4: 'PA4', 5: 'PA5', 6: 'PA6', 7: 'PA7',
    8: 'PB0', 9: 'PB1', 10: 'PC0', 11: 'PC1', 12: 'PC2', 13: 'PC3', 14: 'PC4', 15: 'PC5',
    16: 'temp', 17: 'vref', 18: 'vbat'}


def gpio(e: Emu) -> None:
    g = e.p.regs
    print('== GPIO')
    for name, base in PORTS.items():
        moder = g.get(base + 0, 0)
        if not any(g.get(base + o) for o in (0, 4, 8, 0xC, 0x20, 0x24)):
            continue
        otyper, ospeed, pupd = g.get(base + 4, 0), g.get(base + 8, 0), g.get(base + 0xC, 0)
        afr = g.get(base + 0x20, 0) | (g.get(base + 0x24, 0) << 32)
        odr = g.get(base + 0x14, 0)
        pins = []
        for pin in range(16):
            m = (moder >> (2 * pin)) & 3
            pu = (pupd >> (2 * pin)) & 3
            if m == 0 and pu == 0:
                continue
            s = f'P{name}{pin}:{MODE[m]}'
            if m == 2:
                s += f'{(afr >> (4 * pin)) & 0xF}'
            if m == 1:
                s += ('-od' if (otyper >> pin) & 1 else '') + f'={(odr >> pin) & 1}'
            if PULL[pu]:
                s += f'/{PULL[pu]}'
            pins.append(s)
        print(f'  GPIO{name}: ' + '  '.join(pins))


def regs(e: Emu, title: str, base: int, names: dict[int, str]) -> None:
    g = e.p.regs
    vals = [f'{n}=0x{g.get(base + o, 0):08x}' for o, n in names.items() if g.get(base + o)]
    if vals:
        print(f'== {title} @0x{base:08x}: ' + ' '.join(vals))


def adc(e: Emu) -> None:
    g = e.p.regs
    for i, base in enumerate((0x40012000, 0x40012100, 0x40012200), 1):
        cr2 = g.get(base + 8, 0)
        if not (cr2 or g.get(base + 4)):
            continue
        sqr1, sqr2, sqr3 = g.get(base + 0x2C, 0), g.get(base + 0x30, 0), g.get(base + 0x34, 0)
        n = ((sqr1 >> 20) & 0xF) + 1
        seq = []
        for k in range(n):
            reg, sh = (sqr3, 5 * k) if k < 6 else (sqr2, 5 * (k - 6)) if k < 12 else (sqr1, 5 * (k - 12))
            ch = (reg >> sh) & 0x1F
            seq.append(f'{ch}({ADC_CH.get(ch, "?")})')
        jsqr = g.get(base + 0x38, 0)
        print(f'== ADC{i}: CR1=0x{g.get(base+4,0):08x} CR2=0x{cr2:08x} SMPR1=0x{g.get(base+0xC,0):08x} '
              f'SMPR2=0x{g.get(base+0x10,0):08x} regular seq[{n}]={seq} JSQR=0x{jsqr:08x}')
    regs(e, 'ADC common', 0x40012300, {4: 'CCR'})


def dma(e: Emu) -> None:
    g = e.p.regs
    for d, base in ((1, 0x40026000), (2, 0x40026400)):
        for s in range(8):
            sb = base + 0x10 + 0x18 * s
            cr, ndtr, par, m0 = g.get(sb, 0), g.get(sb + 4, 0), g.get(sb + 8, 0), g.get(sb + 0xC, 0)
            if cr or par:
                ch = (cr >> 25) & 7
                print(f'== DMA{d} stream{s} ch{ch}: CR=0x{cr:08x} NDTR={ndtr} PAR=0x{par:08x} M0AR=0x{m0:08x}')


def timers(e: Emu) -> None:
    tims = {1: 0x40010000, 2: 0x40000000, 3: 0x40000400, 4: 0x40000800, 5: 0x40000C00,
            6: 0x40001000, 7: 0x40001400, 8: 0x40010400, 9: 0x40014000, 10: 0x40014400,
            11: 0x40014800, 12: 0x40001800, 13: 0x40001C00, 14: 0x40002000}
    for n, b in tims.items():
        regs(e, f'TIM{n}', b, {0: 'CR1', 4: 'CR2', 0xC: 'DIER', 0x18: 'CCMR1', 0x1C: 'CCMR2',
                               0x20: 'CCER', 0x28: 'PSC', 0x2C: 'ARR', 0x34: 'CCR1', 0x38: 'CCR2',
                               0x3C: 'CCR3', 0x40: 'CCR4', 0x44: 'BDTR', 0x48: 'DCR'})


def usb(e: Emu) -> None:
    for name, b in (('OTG_HS', 0x40040000), ('OTG_FS', 0x50000000)):
        regs(e, name, b, {0x8: 'GAHBCFG', 0xC: 'GUSBCFG', 0x18: 'GINTMSK', 0x24: 'GRXFSIZ',
                          0x28: 'DIEPTXF0', 0x38: 'GCCFG', 0x800: 'DCFG', 0x804: 'DCTL',
                          0xE00: 'PCGCCTL'})
    regs(e, 'RCC', 0x40023800, {0: 'CR', 4: 'PLLCFGR', 8: 'CFGR', 0x30: 'AHB1ENR', 0x34: 'AHB2ENR',
                                0x40: 'APB1ENR', 0x44: 'APB2ENR', 0x84: 'PLLI2SCFGR'})
    regs(e, 'FLASH', 0x40023C00, {0: 'ACR'})


def misc(e: Emu) -> None:
    for name, b in (('SPI1', 0x40013000), ('SPI2', 0x40003800), ('SPI3', 0x40003C00),
                    ('I2C1', 0x40005400), ('I2C2', 0x40005800), ('I2C3', 0x40005C00),
                    ('USART1', 0x40011000), ('USART2', 0x40004400), ('USART3', 0x40004800)):
        regs(e, name, b, {0: 'CR1', 4: 'CR2', 8: 'CR3/I2C_OAR', 0x1C: 'I2SCFGR'})
    g = e.p.regs
    en = []
    for i in range(8):
        v = g.get(0xE000E100 + 4 * i, 0)
        en += [32 * i + b for b in range(32) if (v >> b) & 1]
    print('== NVIC enabled IRQs:', en)
    print('== SysTick LOAD=', g.get(0xE000E014, 0), 'CTRL=', hex(g.get(0xE000E010, 0)))
    print('== SCB VTOR=', hex(g.get(0xE000ED08, 0)))


def write_counts(e: Emu, top: int = 40) -> None:
    c = collections.Counter(a & ~3 for (_, _, k, a, _, _) in e.p.log if k == 'W')
    print('== most-written registers:')
    print('  ' + '  '.join(f'0x{a:08x}x{n}' for a, n in c.most_common(top)))


if __name__ == '__main__':
    e = Emu(sys.argv[1])
    chunks = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    e.p.quiet = True
    res = e.run.__func__  # noqa
    e.uc.reg_write(__import__('unicorn').arm_const.UC_ARM_REG_SP, 0x2001FF00)
    r = e.run(e.vec(1), max_chunks=chunks)
    print('boot ->', {k: (hex(v) if isinstance(v, int) else v) for k, v in r.items()})
    gpio(e); adc(e); dma(e); timers(e); usb(e); misc(e); write_counts(e)
