"""Wrap a raw application image in the MadLions/FGG upgrade-file format.

Format (recovered from the four official MAD68 HE RGB images):

    offset  size  field
    0       2     USB vendor ID, big-endian            (0x373B)
    2       2     application product ID, big-endian   (0x1058 for MAD68 HE RGB)
    4       2     USB vendor ID again                   (0x373B)
    6       2     bootloader product ID, big-endian     (0x2024)
    8       2     version, BCD-ish big-endian           (0x0106 = v1.06)
    10      4     body length in bytes, big-endian
    14      N     body: the application image linked at 0x08020000
    14+N    4     0x55555555 - (sum of body bytes), big-endian

The web tool streams the whole file to the bootloader; the bootloader checks
the header against itself, so the IDs in the header must stay the stock ones
even though the custom firmware enumerates with its own product ID.

Usage:
    python pack.py --selftest <dir with official .bin files>
    python pack.py <firmware.bin> <out.bin> [--version 0x0F01]
"""
from __future__ import annotations

import argparse
import glob
import os
import struct
import sys

VID = 0x373B
APP_PID = 0x1058
BOOT_PID = 0x2024
HEADER = 14
APP_BASE = 0x08020000
APP_MAX = 128 * 1024


def trailer(body: bytes) -> bytes:
    return struct.pack('>I', (0x55555555 - sum(body)) & 0xFFFFFFFF)


def pack(body: bytes, version: int, app_pid: int = APP_PID, boot_pid: int = BOOT_PID) -> bytes:
    hdr = struct.pack('>HHHHHI', VID, app_pid, VID, boot_pid, version, len(body))
    return hdr + body + trailer(body)


def parse(blob: bytes) -> dict:
    vid, pid, vid2, bpid, ver, n = struct.unpack_from('>HHHHHI', blob, 0)
    body = blob[HEADER:HEADER + n]
    tail = blob[HEADER + n:]
    return {'vid': vid, 'pid': pid, 'vid2': vid2, 'boot_pid': bpid, 'version': ver,
            'length': n, 'body': body, 'trailer_ok': tail == trailer(body),
            'size_ok': len(blob) == HEADER + n + 4}


def check_app_image(body: bytes) -> list[str]:
    """Static sanity checks on a Cortex-M image meant for 0x08020000."""
    problems = []
    if len(body) > APP_MAX:
        problems.append(f'image is {len(body)} bytes, sector 5 holds {APP_MAX}')
    if len(body) % 4:
        problems.append('image length is not a multiple of 4')
    sp, reset = struct.unpack_from('<II', body, 0)
    if not (0x20000000 < sp <= 0x2001FFF0):
        problems.append(f'initial SP 0x{sp:08x} outside SRAM below the reserved top 16 bytes')
    if not (reset & 1 and APP_BASE <= (reset & ~1) < APP_BASE + len(body)):
        problems.append(f'reset vector 0x{reset:08x} not a Thumb address inside the image')
    for i in range(2, 16):
        v = struct.unpack_from('<I', body, i * 4)[0]
        if v and not (v & 1 and APP_BASE <= (v & ~1) < APP_BASE + len(body)):
            problems.append(f'core vector {i} = 0x{v:08x} points outside the image')
    return problems


def selftest(directory: str) -> int:
    files = sorted(glob.glob(os.path.join(directory, '*.bin')))
    if not files:
        print('no .bin files found')
        return 1
    bad = 0
    for f in files:
        blob = open(f, 'rb').read()
        info = parse(blob)
        rebuilt = pack(info['body'], info['version'], info['pid'], info['boot_pid'])
        same = rebuilt == blob
        bad += not same
        print(f"{'OK ' if same else 'BAD'} {os.path.basename(f)}: v{info['version']:04x} "
              f"{info['length']} bytes, trailer {'ok' if info['trailer_ok'] else 'BAD'}, "
              f"repack {'identical' if same else 'DIFFERS'}; image checks: "
              f"{check_app_image(info['body']) or 'clean'}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--selftest', metavar='DIR')
    ap.add_argument('input', nargs='?')
    ap.add_argument('output', nargs='?')
    ap.add_argument('--version', default='0x0F01', help='header version field (default 0x0F01)')
    a = ap.parse_args()
    if a.selftest:
        return selftest(a.selftest)
    if not (a.input and a.output):
        ap.error('input and output are required')
    body = open(a.input, 'rb').read()
    body += b'\xff' * (-len(body) % 4)
    problems = check_app_image(body)
    if problems:
        for p in problems:
            print('REFUSING:', p)
        return 1
    blob = pack(body, int(a.version, 0))
    info = parse(blob)
    assert info['trailer_ok'] and info['size_ok'] and info['body'] == body
    open(a.output, 'wb').write(blob)
    print(f'wrote {a.output}: {len(blob)} bytes (body {len(body)}), header '
          f'{blob[:HEADER].hex(" ")}, trailer {blob[-4:].hex()}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
