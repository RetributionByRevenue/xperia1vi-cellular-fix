"""Decode LTE RRC over-the-air log packets (0xB0C0, log version 0x1e on this modem).

Only the handful of messages needed to diagnose link problems are decoded:
connection request/setup/reject/release, re-establishment (with cause) and
handover commands. Serving cell (EARFCN, PCI) comes from the packet header.
"""
import collections
import struct

from diag import logs
from nas import ts, decode as nas_decode, NAMES as NAS_NAMES

# PDU types seen in log version 0x1e
PCCH, DL_CCCH, DL_DCCH, UL_CCCH, UL_DCCH = 7, 8, 9, 10, 11

REEST = {0: 'reconfigurationFailure', 1: 'handoverFailure', 2: 'otherFailure(RLF)'}
EST = {0: 'emergency', 1: 'highPrio', 2: 'mt-Access', 3: 'mo-Signalling', 4: 'mo-Data',
       5: 'delayTolerant', 6: 'mo-Voice'}


def bits(b, off, n):
    v = int.from_bytes(b, 'big')
    return (v >> (len(b) * 8 - off - n)) & ((1 << n) - 1)


def band(earfcn):
    for lo, hi, name in [(0, 599, 'B1'), (1200, 1949, 'B3'), (2750, 3449, 'B7'),
                         (3450, 3799, 'B8'), (9210, 9659, 'B28'), (37750, 38249, 'B38'),
                         (38650, 39649, 'B40'), (39650, 41589, 'B41')]:
        if lo <= earfcn <= hi:
            return name
    return '?%d' % earfcn


def rrc(p):
    """-> (pci, earfcn, event or None)"""
    pci, = struct.unpack_from('<H', p, 6)
    earfcn, = struct.unpack_from('<I', p, 8)
    pdu_type = p[14]
    ln, = struct.unpack_from('<H', p, 19)
    m = p[24:24 + ln]
    ev = None
    if not m:
        return pci, earfcn, None
    if pdu_type == UL_CCCH:
        if bits(m, 1, 1) == 0:
            ev = 'ReestablishmentRequest cause=' + REEST.get(bits(m, 44, 2), '?')
        else:
            ev = 'ConnectionRequest cause=' + EST.get(bits(m, 44, 3), '?')
    elif pdu_type == DL_CCCH:
        ev = ['Reestablishment', 'ReestablishmentReject', 'ConnectionReject',
              'ConnectionSetup'][bits(m, 1, 2)]
    elif pdu_type == DL_DCCH:
        c = bits(m, 1, 4)
        if c == 5:
            ev = 'ConnectionRelease'
        elif c == 4 and bits(m, 8, 3) == 0 and bits(m, 12, 1):
            ev = 'HANDOVER command'            # reconfiguration carrying mobilityControlInfo
    return pci, earfcn, ev


def analyze(data, show_cells=False):
    """Print a timeline of link events plus per-cell connection-request success."""
    last = None
    totals = collections.Counter()
    events = []                                    # (raw_ts, text_ts, earfcn, pci, event)
    for code, t, p in logs(data):
        if code == 0xB0C0:
            pci, earfcn, ev = rrc(p)
            if (pci, earfcn) != last:
                if show_cells:
                    print(ts(t), '   cell -> %s earfcn %d pci %d' % (band(earfcn), earfcn, pci))
                last = (pci, earfcn)
                totals['cell change'] += 1
            if ev:
                print(ts(t), 'RRC', '%-4s' % band(earfcn), ev)
                totals[ev.split(' cause')[0]] += 1
                events.append((t, ts(t), earfcn, pci, ev))
        elif code in NAS_NAMES:
            s = nas_decode(p)
            if any(k in s for k in ('Reject', 'Detach', 'Attach', 'cause')):
                print(ts(t), 'NAS', NAS_NAMES[code], s)
                totals['NAS ' + s.split()[0]] += 1

    # Did each ConnectionRequest get a ConnectionSetup within 2 s? Did handovers complete?
    per_cell = collections.defaultdict(lambda: [0, 0])
    for i, (t, _, earfcn, pci, ev) in enumerate(events):
        if ev.startswith('ConnectionRequest'):
            ok = any(x[4] == 'ConnectionSetup' and 0 <= (x[0] - t) / 65536 * 1.25e-3 < 2
                     for x in events[i + 1:i + 4])
            per_cell[(band(earfcn), earfcn, pci)][0 if ok else 1] += 1
        elif ev.startswith('HANDOVER'):
            failed = i + 1 < len(events) and events[i + 1][4].startswith('ReestablishmentRequest')
            totals['handover failed' if failed else 'handover ok'] += 1

    print('\nTotals:', dict(totals))
    if per_cell:
        print('\nConnectionRequest answered / unanswered, per serving cell:')
        for (b, e, pci), (ok, bad) in sorted(per_cell.items()):
            flag = '   <-- uplink not heard' if bad and not ok else ''
            print('  %-4s earfcn %-5d pci %-4d  %3d / %-3d%s' % (b, e, pci, ok, bad, flag))
