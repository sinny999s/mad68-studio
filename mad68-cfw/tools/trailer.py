"""Identify the 4-byte trailer at the end of each vendor image."""
import glob, struct, sys, zlib
import crcmod.predefined as P

def stm32_crc(b):  # CRC-32/MPEG-2 over little-endian words (STM32 CRC unit)
    f = P.mkPredefinedCrcFun('crc-32-mpeg')
    b = b + b'\x00' * (-len(b) % 4)
    swapped = b''.join(struct.pack('>I', w) for w in struct.unpack('<%dI' % (len(b) // 4), b))
    return f(swapped)

algos = {
    'crc32': zlib.crc32,
    'crc32-mpeg2': P.mkPredefinedCrcFun('crc-32-mpeg'),
    'stm32-crc-words': stm32_crc,
    'sum32-bytes': lambda b: sum(b) & 0xFFFFFFFF,
    'sum32-words': lambda b: sum(struct.unpack('<%dI' % (len(b) // 4), b[:len(b) // 4 * 4])) & 0xFFFFFFFF,
    'xor32-words': lambda b: __import__('functools').reduce(lambda a, w: a ^ w, struct.unpack('<%dI' % (len(b) // 4), b[:len(b) // 4 * 4]), 0),
}
for f in sorted(glob.glob(sys.argv[1] + '/*.bin')):
    d = open(f, 'rb').read(); img = d[14:]
    L = struct.unpack('>I', d[10:14])[0]
    body, tail = img[:L], img[L:]
    print(__import__('os').path.basename(f), 'len field', L, 'img', len(img), 'tail', tail.hex(), 'aligned4:', len(img) % 4 == 0)
    for n, fn in algos.items():
        for region_name, region in (('body', body), ('hdr+body', d[:14] + body), ('hdr10+body', d[:10] + body)):
            v = fn(region)
            for e in ('<I', '>I'):
                if struct.pack(e, v) == tail:
                    print('   MATCH', n, region_name, e)
