"""Qualcomm DIAG framing: HDLC encode/decode, log packets, log-mask commands.

diag_mdlog on the phone replays a "mask file" (a series of HDLC-framed DIAG
requests) to the modem and records everything the modem sends back into a
.qmdl file. Both directions use the same framing, handled here.
"""
import struct


def crc16(data):
    """CRC-16/X.25, as used by DIAG HDLC framing."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def hdlc(pkt):
    """Frame one DIAG request: append CRC, escape 0x7E/0x7D, terminate with 0x7E."""
    raw = pkt + struct.pack('<H', crc16(pkt))
    out = bytearray()
    for b in raw:
        if b in (0x7E, 0x7D):
            out += bytes([0x7D, b ^ 0x20])
        else:
            out.append(b)
    return bytes(out) + b'\x7e'


def frames(data):
    """Split a .qmdl capture into unescaped DIAG frames (CRC stripped)."""
    for chunk in data.split(b'\x7e'):
        if not chunk:
            continue
        out = bytearray()
        esc = False
        for b in chunk:
            if esc:
                out.append(b ^ 0x20)
                esc = False
            elif b == 0x7D:
                esc = True
            else:
                out.append(b)
        if len(out) > 2:
            yield bytes(out[:-2])


def logs(data):
    """Yield (log_code, timestamp, payload) for every log packet in a capture.

    Plain log packets start with 0x10. On this multi-SIM modem most arrive
    wrapped in a 0x98 frame, with the 0x10 packet a few bytes in.
    """
    for f in frames(data):
        if f[0] == 0x10 and len(f) >= 16:
            _, code, ts = struct.unpack_from('<HHQ', f, 4)
            yield code, ts, f[16:]
        elif f[0] == 0x98 and len(f) > 20:
            i = f.find(b'\x10', 1, 12)
            if i > 0 and len(f) >= i + 16:
                _, code, ts = struct.unpack_from('<HHQ', f, i + 4)
                yield code, ts, f[i + 16:]


def log_mask(equip, codes, nitems=256):
    """DIAG 0x73 'set log mask' request enabling the given log codes."""
    mask = bytearray((nitems + 7) // 8)
    for c in codes:
        i = c & 0xFFF
        mask[i // 8] |= 1 << (i % 8)
    return hdlc(struct.pack('<B3xIII', 0x73, 3, equip, nitems) + bytes(mask))


LTE = 11                                    # DIAG equipment ID for LTE log codes
NAS_CODES = list(range(0xB0E0, 0xB0F0))     # LTE NAS ESM/EMM over-the-air
RRC_CODES = [0xB0C0, 0xB0C2]                # LTE RRC over-the-air, serving cell info

MASKS = {
    'nas': NAS_CODES,
    'rrc': NAS_CODES + RRC_CODES,
}
