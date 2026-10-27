"""Modem EFS access over DIAG (subsystem 0x13), as verified on this phone's modem.

Working commands:  GET 0x27 (stateless read), OPEN 0x02, WRITE 0x05, CLOSE 0x03, UNLINK 0x08.
Do NOT use 0x1A/0x19 (deprecated PUT/GET variants) and PUT 0x26 (layout never worked here).
"""
import struct

from diag import hdlc, frames

FS = 0x13
O_WRONLY, O_CREAT, O_TRUNC = 0o1, 0o100, 0o1000


def get(path, seq, size):
    """Read an item file. size must match the item's size (else ERANGE)."""
    p = path.encode() + b'\0'
    return hdlc(struct.pack('<BBHiiH', 0x4B, FS, 0x27, size, len(p), seq) + p)


def fs_open(path, oflag=O_WRONLY | O_CREAT | O_TRUNC, mode=0o644):
    return hdlc(struct.pack('<BBHii', 0x4B, FS, 0x02, oflag, mode) + path.encode() + b'\0')


def fs_write(fd, data, offset=0):
    return hdlc(struct.pack('<BBHiI', 0x4B, FS, 0x05, fd, offset) + data)


def fs_close(fd):
    return hdlc(struct.pack('<BBHi', 0x4B, FS, 0x03, fd))


def fs_unlink(path):
    return hdlc(struct.pack('<BBH', 0x4B, FS, 0x08) + path.encode() + b'\0')


def replies(qmdl_bytes):
    """Decode EFS replies in a capture -> list of dicts."""
    out = []
    for f in frames(qmdl_bytes):
        if len(f) < 4:
            continue
        if f[0] == 0x4B and f[1] == FS:
            cmd, = struct.unpack_from('<H', f, 2)
            b = f[4:]
            if cmd == 0x27 and len(b) >= 10:
                n, err, seq = struct.unpack_from('<iiH', b)
                out.append(dict(op='get', seq=seq, errno=err, data=b[10:10 + max(n, 0)]))
            elif cmd == 0x02 and len(b) >= 8:
                fd, err = struct.unpack_from('<ii', b)
                out.append(dict(op='open', fd=fd, errno=err))
            elif cmd == 0x05 and len(b) >= 16:
                fd, off, n, err = struct.unpack_from('<iIii', b)
                out.append(dict(op='write', fd=fd, written=n, errno=err))
            elif cmd == 0x03 and len(b) >= 4:
                out.append(dict(op='close', errno=struct.unpack_from('<i', b)[0]))
            elif cmd == 0x08 and len(b) >= 4:
                out.append(dict(op='unlink', errno=struct.unpack_from('<i', b)[0]))
        elif f[0] in (0x13, 0x14, 0x15) and f[1:3] == bytes([0x4B, FS]):
            out.append(dict(op='error', code=hex(f[0]), errno=None))   # 0x15 = bad length
    return out


def lte_mask(bands):
    """LTE band preference bitmask for bands 1..64 (bit n-1 = band n), 8 bytes LE."""
    m = 0
    for b in bands:
        m |= 1 << (b - 1)
    return struct.pack('<Q', m)


def mask_bands(data):
    if len(data) != 8:
        return None
    m, = struct.unpack('<Q', data)
    return [i + 1 for i in range(64) if m >> i & 1]
