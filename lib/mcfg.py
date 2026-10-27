"""Parse Qualcomm MCFG (.mbn) modem config files and decode policyman band sets.

The hardware MCFG for this phone (PDX245_K4, KDDI) lives under
/data/vendor/modem_config/mcfg_hw/generic/common/Lanai/LA/<variant>/mcfg_hw.mbn
"""
import re
import struct


def parse(data):
    """-> {'efs:/path': bytes, 'nv:<id>': bytes}"""
    i = data.find(b'MCFG')
    if i < 0:
        raise ValueError('no MCFG magic')
    _, _, nitems = struct.unpack_from('<HHI', data, i + 4)
    p = i + 24                                     # 16-byte header + 8-byte version item
    items = {}
    for n in range(nitems):
        if p + 8 > len(data):
            break
        ln, typ = struct.unpack_from('<IB', data, p)
        body = data[p + 8:p + ln]
        if typ == 1 and len(body) >= 4:            # NV item
            nid, sz = struct.unpack_from('<HH', body)
            items['nv:%d' % nid] = body[4:4 + sz]
        elif len(body) >= 4:                       # EFS file: TLVs, 1 = path, 2 = data
            q, name, val = 0, None, b''
            while q + 4 <= len(body):
                tag, tl = struct.unpack_from('<HH', body, q)
                v = body[q + 4:q + 4 + tl]
                q += 4 + tl
                if tag == 1:
                    name = v.rstrip(b'\0').decode('latin1')
                elif tag == 2:
                    val = v
            items['efs:%s' % (name or '?%d' % n)] = val
        p += ln
    return items


def band_sets(xml_text):
    """Decode <rf_band_list> entries of a policyman band_set xml (bit n = band n+1)."""
    x = re.sub(r'<!--.*?-->', '', xml_text, flags=re.S)
    out = {}
    for m in re.finditer(r'<rf_band_list[^>]*name="([^"]+)">(.*?)</rf_band_list>', x, re.S):
        d = {}
        for t in ('gw', 'lte', 'nr5g_sa', 'nr5g_nsa'):
            mm = re.search(r'<%s_bands[^>]*?(?:/>|>(.*?)</%s_bands>)' % (t, t), m.group(2), re.S)
            inc = re.search(r'<include>(.*?)</include>', mm.group(1)) if mm and mm.group(1) else None
            d[t] = [int(b) + 1 for b in inc.group(1).split()] if inc else []
        out[m.group(1)] = d
    return out
