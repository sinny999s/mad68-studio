"""Find which checksum the 2-byte field at header[12:14] holds."""
import glob, struct, sys, zlib
import crcmod.predefined as P
files = sorted(glob.glob(sys.argv[1] + '/*.bin'))
data = [open(f, 'rb').read() for f in files]
want = [d[12:14] for d in data]
cands = {}
def add(name, fn):
    cands[name] = fn
for c in P._crc_definitions_table:
    nm = c[0]
    if c[2] == 16 or nm.startswith('crc-16') or nm in ('xmodem', 'modbus', 'kermit', 'x-25'):
        try:
            f = P.mkPredefinedCrcFun(nm)
            add(nm, f)
        except Exception:
            pass
add('sum16', lambda b: sum(b) & 0xFFFF)
add('sum16-words', lambda b: sum(struct.unpack_from('<%dH' % (len(b)//2), b)) & 0xFFFF)
add('neg-sum16', lambda b: (-sum(b)) & 0xFFFF)
add('xor16', lambda b: __import__('functools').reduce(lambda a, w: a ^ w, struct.unpack_from('<%dH' % (len(b)//2), b), 0))
add('crc32-lo', lambda b: zlib.crc32(b) & 0xFFFF)
add('crc32-hi', lambda b: zlib.crc32(b) >> 16)
regions = {'image': lambda d: d[14:], 'hdr12+image': lambda d: d[:12] + d[14:],
           'image-pad4': lambda d: d[14:] + b'\xff' * (-(len(d) - 14) % 4),
           'image-pad48': lambda d: d[14:] + b'\x00' * (-(len(d) - 14) % 48),
           'image-padff48': lambda d: d[14:] + b'\xff' * (-(len(d) - 14) % 48)}
hits = 0
for rn, rf in regions.items():
    for cn, cf in cands.items():
        vals = [cf(rf(d)) for d in data]
        for endian in ('big', 'little'):
            if all(v.to_bytes(2, endian) == w for v, w in zip(vals, want)):
                print('MATCH', rn, cn, endian); hits += 1
print('header fields:', [w.hex() for w in want], 'hits:', hits)
