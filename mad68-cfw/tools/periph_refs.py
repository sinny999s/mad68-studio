"""List every peripheral base the stock image addresses, via literal pools AND movw/movt pairs."""
import struct, sys, collections
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS
from capstone.arm import ARM_OP_IMM, ARM_OP_REG
b = open(sys.argv[1], 'rb').read()[14:]
BASE = 0x08020000
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS); md.detail = True; md.skipdata = True
ins = list(md.disasm(b, BASE))
refs = collections.defaultdict(list)
last_movw = {}
for i in ins:
    if i.mnemonic == 'ldr' and '[pc' in i.op_str and len(i.operands) == 2:
        a = ((i.address + 4) & ~3) + i.operands[1].mem.disp
        if BASE <= a < BASE + len(b) - 3:
            v = struct.unpack_from('<I', b, a - BASE)[0]
            if 0x40000000 <= v < 0x50100000 or 0xA0000000 <= v < 0xA0001000:
                refs[v & ~0x3FF].append(i.address)
    elif i.mnemonic in ('movw', 'mov.w', 'movs') and len(i.operands) == 2 and i.operands[1].type == ARM_OP_IMM:
        last_movw[i.operands[0].reg] = (i.operands[1].imm & 0xFFFF, i.address)
    elif i.mnemonic == 'movt' and len(i.operands) == 2:
        r = i.operands[0].reg
        hi = i.operands[1].imm & 0xFFFF
        lo = last_movw.get(r, (0, 0))[0]
        v = (hi << 16) | lo
        if 0x40000000 <= v < 0x50100000:
            refs[v & ~0x3FF].append(i.address)
NAMES = {0x40000000:'TIM2',0x40000400:'TIM3',0x40000800:'TIM4',0x40000C00:'TIM5',0x40001000:'TIM6',0x40001400:'TIM7',
 0x40001800:'TIM12',0x40001C00:'TIM13',0x40002000:'TIM14',0x40002400:'?0x40002400',0x40002800:'RTC',0x40002C00:'WWDG',0x40003000:'IWDG',
 0x40003800:'SPI2/I2S2',0x40003C00:'SPI3/I2S3',0x40004400:'USART2',0x40004800:'USART3',0x40004C00:'UART4',0x40005000:'UART5',
 0x40005400:'I2C1',0x40005800:'I2C2',0x40005C00:'I2C3',0x40007000:'PWR',0x40007400:'DAC',0x40010000:'TIM1',0x40010400:'TIM8',
 0x40011000:'USART1',0x40011400:'USART6',0x40012000:'ADC',0x40012C00:'SDIO',0x40013000:'SPI1',0x40013800:'SYSCFG',0x40013C00:'EXTI',
 0x40014000:'TIM9',0x40014400:'TIM10',0x40014800:'TIM11',0x40020000:'GPIOA',0x40020400:'GPIOB',0x40020800:'GPIOC',0x40020C00:'GPIOD',
 0x40021000:'GPIOE',0x40023000:'CRC',0x40023800:'RCC',0x40023C00:'FLASH',0x40024000:'BKPSRAM',0x40026000:'DMA1',0x40026400:'DMA2',
 0x40040000:'OTG_HS',0x50000000:'OTG_FS'}
for base in sorted(refs):
    print(f'{NAMES.get(base, hex(base)):14} 0x{base:08x}  refs={len(refs[base]):3}  at {", ".join(hex(a) for a in refs[base][:6])}')
