"""Decode LTE NAS (EMM/ESM) log packets 0xB0E2/E3/EC/ED into readable lines."""
import datetime

EMM = {0x41: 'AttachRequest', 0x42: 'AttachAccept', 0x43: 'AttachComplete', 0x44: 'AttachReject',
       0x45: 'DetachRequest', 0x46: 'DetachAccept', 0x48: 'TAURequest', 0x49: 'TAUAccept',
       0x4a: 'TAUComplete', 0x4b: 'TAUReject', 0x4c: 'ExtServiceRequest', 0x4e: 'ServiceReject',
       0x50: 'GUTIRealloc', 0x52: 'AuthRequest', 0x53: 'AuthResponse', 0x54: 'AuthReject',
       0x5c: 'AuthFailure', 0x55: 'IdentityRequest', 0x56: 'IdentityResponse',
       0x5d: 'SecModeCommand', 0x5e: 'SecModeComplete', 0x5f: 'SecModeReject', 0x60: 'EMMStatus',
       0x61: 'EMMInformation', 0x62: 'DLNASTransport', 0x63: 'ULNASTransport'}
ESM = {0xc1: 'ActDefaultBearerReq', 0xc2: 'ActDefaultBearerAccept', 0xc3: 'ActDefaultBearerReject',
       0xc5: 'ActDedicatedBearerReq', 0xc6: 'ActDedicatedAccept', 0xc7: 'ActDedicatedReject',
       0xc9: 'ModifyBearerReq', 0xca: 'ModifyBearerAccept', 0xcb: 'ModifyBearerReject',
       0xcd: 'DeactBearerReq', 0xce: 'DeactBearerAccept', 0xd0: 'PDNConnReq', 0xd1: 'PDNConnReject',
       0xd2: 'PDNDisconnReq', 0xd3: 'PDNDisconnReject', 0xd9: 'ESMInfoReq', 0xda: 'ESMInfoResp',
       0xe8: 'ESMStatus'}
NAMES = {0xB0E2: 'ESM in ', 0xB0E3: 'ESM out', 0xB0EC: 'EMM in ', 0xB0ED: 'EMM out'}

# Common reject causes (3GPP TS 24.301 annex A)
EMM_CAUSES = {3: 'illegal UE', 7: 'EPS services not allowed', 9: 'UE identity cannot be derived',
              10: 'implicitly detached', 11: 'PLMN not allowed', 12: 'TA not allowed',
              13: 'roaming not allowed in TA', 15: 'no suitable cells in TA', 22: 'congestion'}


def ts(t, tz_hours=8):
    """DIAG timestamp (upper 48 bits = 1.25 ms ticks since 1980-01-06) -> local HH:MM:SS.mmm."""
    dt = datetime.datetime(1980, 1, 6) + datetime.timedelta(seconds=(t >> 16) * 1.25e-3,
                                                            hours=tz_hours)
    return dt.strftime('%H:%M:%S.%f')[:-3]


def decode(payload):
    pdu = payload[4:]
    if not pdu:
        return '?'
    pd, sec = pdu[0] & 0x0F, pdu[0] >> 4
    if pd == 7:                                   # EMM
        if sec:
            pdu = pdu[6:]
        if len(pdu) < 2:
            return 'EMM(short)'
        mt = pdu[1]
        name, extra = EMM.get(mt, hex(mt)), ''
        if mt in (0x44, 0x4b, 0x4e, 0x54, 0x60) and len(pdu) > 2:
            extra = ' cause=#%d (%s)' % (pdu[2], EMM_CAUSES.get(pdu[2], '?'))
        elif mt == 0x45 and len(pdu) > 2:
            extra = ' type=%d' % (pdu[2] & 7)
            j = pdu.find(b'\x53', 3)
            if 0 < j < len(pdu) - 1:
                extra += ' emm_cause=#%d' % pdu[j + 1]
        elif mt in (0x41, 0x48) and len(pdu) > 2:
            extra = ' type=%d' % (pdu[2] & 7)
        return name + extra
    if pd == 2 and len(pdu) > 2:                  # ESM
        mt = pdu[2]
        extra = ''
        if mt in (0xc3, 0xc7, 0xcb, 0xcd, 0xd1, 0xd3, 0xe8) and len(pdu) > 3:
            extra = ' esm_cause=#%d' % pdu[3]
        return 'ebi=%d %s%s' % (pdu[0] >> 4, ESM.get(mt, hex(mt)), extra)
    return 'pd=%d %s' % (pd, pdu[:8].hex())
