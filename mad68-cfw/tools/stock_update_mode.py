"""Check MadLions' stock update-mode request in the emulator.

MAD68 Studio (from 0.13.0) puts a keyboard that runs the stock firmware into
update mode itself, the way MadLions' updater does: a 32-byte report on the
stock firmware's raw HID interface (usage page 0xFF60, usage 0x61).

  official updater (hub.fgg.com.cn, "IapMode"):  96 96 96 98
  MAD68 Studio:                                   96 96 00 98

In stock v1.06 the report reaches the raw HID receive function at 0x0802CEE8,
which passes command 0x96 to the handler at 0x0802629C:
  byte 1 must be 0x96;
  byte 2 = 0x96 runs the stock settings reset first (the same routine as
    stock's own reset command), any other value skips it;
  byte 3 = 0x98 sets the update-mode flag (0x96969696 at 0x2001FFFC), resets
    the peripherals, sets VTOR to 0x08000000 and jumps into the bootloader;
    0x97 is a plain restart; anything else just replies.

Each case starts a fresh copy of the image and hands one report to the
receive function. There is no start-up first (it takes minutes in the
emulator and this path doesn't need it), so RAM starts zeroed as after a
reset. A fake bootloader at 0x08000000 spins at 0x08000100: ending there
means the firmware jumped into update mode. The reply-send function
(0x0802D948) is replaced by a spin loop too, because USB isn't set up.
The official 96 96 96 98 isn't run: its settings reset needs the start-up.

Usage: python stock_update_mode.py [<stock .bin> ...]
       (default: the three v1.06 files in ../../firmware)
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from unicorn.arm_const import UC_ARM_REG_SP

sys.path.insert(0, str(Path(__file__).resolve().parent))
from emu import Emu  # noqa: E402

RECEIVE = 0x0802CEE8        # stock raw HID receive (data, length)
HANDLER = 0x0802629C        # stock command 0x96 handler
REPLY = 0x0802D948          # raw HID send (the reply)
RESTART_SPIN = 0x08025FDE   # NVIC_SystemReset's wait after SYSRESETREQ
FAKE_BOOT = 0x08000100
FLAG = 0x2001FFFC
UPDATE_MODE_FLAG = 0x96969696
BUF = 0x20030000
FLASH_REGS = range(0x40023C00, 0x40023C20)

STUDIO_REQUEST = bytes([0x96, 0x96, 0x00, 0x98])
CASES = [
    # name, report, expected end
    ('Studio update-mode request 96 96 00 98', STUDIO_REQUEST, 'bootloader'),
    ('restart                    96 96 00 97', bytes([0x96, 0x96, 0x00, 0x97]), 'restart'),
    ('no action                  96 96 00 00', bytes([0x96, 0x96, 0x00, 0x00]), 'reply'),
    ('wrong second byte          96 00 00 98', bytes([0x96, 0x00, 0x00, 0x98]), 'reply'),
    ('VIA protocol version       01',          bytes([0x01]), 'reply'),
]

# The handler's first instructions, so a different stock build is noticed
HANDLER_START = bytes.fromhex('73b50378962b0446')


def run_case(path: Path, report: bytes) -> dict:
    e = Emu(str(path))
    e.p.quiet = True            # don't log reads (the delay loop polls CYCCNT)
    uc = e.uc
    uc.reg_write(UC_ARM_REG_SP, 0x2001FF00)
    uc.mem_write(0x08000000, struct.pack('<II', 0x2001FF00, FAKE_BOOT | 1))
    uc.mem_write(FAKE_BOOT, b'\xfe\xe7')
    uc.mem_write(REPLY, b'\xfe\xe7')
    uc.mem_write(FLAG, b'\0\0\0\0')
    uc.mem_write(BUF, report.ljust(32, b'\0'))
    e.p.log.clear()
    res = e.call(RECEIVE, BUF, 32, chunk=50_000, max_chunks=200)
    w = [x for x in e.p.log if x[2] == 'W']
    pc = res.get('pc', 0) & ~1
    return {
        'end': {FAKE_BOOT: 'bootloader', REPLY: 'reply', RESTART_SPIN: 'restart'}.get(
            pc, f"{res['stop']} at {pc:#x}"),
        'flag': struct.unpack('<I', bytes(uc.mem_read(FLAG, 4)))[0],
        'vtor': [x[5] for x in w if x[3] == 0xE000ED08],
        'aircr': [x[5] for x in w if x[3] == 0xE000ED0C],
        'flash_writes': sum(1 for x in w if x[3] in FLASH_REGS),
    }


def main() -> int:
    here = Path(__file__).resolve().parent
    paths = [Path(p) for p in sys.argv[1:]] or sorted(
        (here.parent.parent / 'firmware').glob('MAD68HE_*_v1.06.bin'))
    passed = failed = 0

    def check(ok: bool, what: str) -> None:
        nonlocal passed, failed
        print(f"  {'PASS' if ok else 'FAIL'} {what}")
        passed += ok
        failed += not ok

    for path in paths:
        print(f'== {path.name}')
        img = path.read_bytes()[14:]
        at = img.find(HANDLER_START)
        check(at == HANDLER - 0x08020000,
              f'command handler at {HANDLER:#x} (found at {0x08020000 + at:#x})')
        for name, report, want in CASES:
            r = run_case(path, report)
            ok = r['end'] == want and r['flash_writes'] == 0
            if want == 'bootloader':
                ok &= r['flag'] == UPDATE_MODE_FLAG and r['vtor'] == [0x08000000]
            else:
                ok &= r['flag'] != UPDATE_MODE_FLAG and not r['vtor']
            if want == 'restart':
                ok &= r['aircr'] == [0x05FA0004]
            check(ok, f"{name}: {r['end']}, flag {r['flag']:#010x}, VTOR "
                      f"{[hex(v) for v in r['vtor']]}, AIRCR {[hex(v) for v in r['aircr']]}, "
                      f"flash writes {r['flash_writes']}")
    print(f'\n{passed}/{passed + failed} checks passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
