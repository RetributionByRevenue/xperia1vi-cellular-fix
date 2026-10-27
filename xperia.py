#!/usr/bin/env python3
"""Xperia 1 VI (KDDI SOG13) cellular toolkit, driven from the PC over adb.

    ./xperia.py status                  serving cell, neighbour bands, data state   (no root)
    ./xperia.py watch [SECONDS]         sample serving/neighbour bands every 5 s     (no root)
    ./xperia.py recover                 toggle airplane mode to re-attach            (root)
    ./xperia.py test [LABEL]            60 pings + download over cellular only       (root)
    ./xperia.py capture start [nas|rrc] start modem log capture                      (root)
    ./xperia.py capture stop            stop it, save to captures/, analyze          (root)
    ./xperia.py analyze FILE.qmdl       decode a capture (RRC/NAS events, per-cell uplink)
    ./xperia.py efs-get PATH SIZE       read a modem EFS item file                   (root)
    ./xperia.py bandlock status         show the LTE band lock                       (root)
    ./xperia.py bandlock apply 8 28     lock LTE to these bands (needs reboot)       (root)
    ./xperia.py bandlock undo           remove the band lock (needs reboot)          (root)
    ./xperia.py mcfg-bands [VARIANT]    show band sets from a hardware MCFG (K4, A4…) (root)

Uses the only connected adb device, or the one named by ANDROID_SERIAL.
"""
import datetime
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'lib'))
import diag  # noqa: E402
import efs   # noqa: E402
import rrc   # noqa: E402

REMOTE = '/data/local/tmp/xc'
BANDPREF = '/nv/item_files/modem/mmode/lte_bandpref_Subscription01'   # eSIM = subscription 1
SLOT = '1'                                                             # telephony slot of the eSIM


# ---------------------------------------------------------------- adb helpers
def adb(*args, check=True):
    r = subprocess.run(['adb', *args], capture_output=True, text=True)
    if check and r.returncode:
        sys.exit('adb %s failed: %s' % (' '.join(args), r.stderr.strip()))
    return r.stdout


def sh(cmd):
    return adb('shell', cmd, check=False)


def su(cmd):
    """Run as root on the phone. Exits with a hint if root isn't currently available."""
    if 'uid=0' not in sh('su -c id 2>/dev/null'):
        sys.exit('Root is not active on the phone. Re-root it first (your PC command), then retry.')
    return sh("su -c '%s'" % cmd.replace("'", "'\\''"))


def push_phone_scripts():
    sh('mkdir -p %s' % REMOTE)
    for f in ('diagrun.sh', 'celltest.sh'):
        adb('push', os.path.join(HERE, 'phone', f), '%s/%s' % (REMOTE, f))


def diag_once(cfg_bytes):
    """Send DIAG requests via diag_mdlog and return the raw capture bytes."""
    push_phone_scripts()
    with tempfile.NamedTemporaryFile(suffix='.cfg', delete=False) as t:
        t.write(cfg_bytes)
    adb('push', t.name, REMOTE + '/q.cfg')
    os.unlink(t.name)
    su('sh %s/diagrun.sh once %s/q.cfg' % (REMOTE, REMOTE))
    with tempfile.NamedTemporaryFile(suffix='.qmdl', delete=False) as t:
        local = t.name
    adb('pull', REMOTE + '/out.qmdl', local)
    data = open(local, 'rb').read()
    os.unlink(local)
    return data


# ---------------------------------------------------------------- commands
def registry():
    return sh('dumpsys telephony.registry')


def serving(reg):
    ss = [l for l in reg.splitlines() if 'mServiceState=' in l]
    ss = ss[-1] if ss else ''
    pick = lambda k: (re.search(k + r'=([^,]*)', ss) or [None, '?'])[1]
    return pick('mVoiceRegState'), pick('mDataRegState'), pick('mChannelNumber'), pick('mCellBandwidths')


def neighbour_bands(reg):
    ci = [l for l in reg.splitlines() if 'mCellInfo=' in l]
    return sorted(re.findall(r'mBands=\[(\d*)\]', ci[-1] if ci else ''))


def cmd_status(_):
    reg = registry()
    voice, data, ch, bw = serving(reg)
    conn = re.findall(r'mDataConnectionState=(-?\d+)', reg)
    sig = re.findall(r'mLte=CellSignalStrengthLte: rssi=\S+ rsrp=(\S+) rsrq=(\S+) rssnr=(\S+)', reg)
    print('voice: %s\ndata reg: %s\ndata connection: %s (2 = connected)' % (voice, data, conn[-1] if conn else '?'))
    if ch.isdigit():
        print('serving: %s (earfcn %s) bandwidth %s' % (rrc.band(int(ch)), ch, bw))
    if sig:
        print('signal: rsrp %s  rsrq %s  sinr %s' % sig[-1])
    nb = neighbour_bands(reg)
    print('neighbour bands:', ', '.join('B%s x%d' % (b, nb.count(b)) for b in sorted(set(nb))) or '-')
    print('allowed types:', sh('cmd phone get-allowed-network-types-for-users -s %s' % SLOT).strip().splitlines()[-1])


def cmd_watch(args):
    import time
    secs = int(args[0]) if args else 60
    for i in range(0, secs, 5):
        reg = registry()
        _, data, ch, _ = serving(reg)
        nb = neighbour_bands(reg)
        print('t+%3ds serving %-4s data %-18s neighbours %s' % (
            i, rrc.band(int(ch)) if ch.isdigit() else '?', data,
            ' '.join('B%s' % b for b in sorted(set(nb)))))
        time.sleep(5)


def cmd_recover(_):
    print(su('cmd connectivity airplane-mode enable; sleep 4; cmd connectivity airplane-mode disable; '
             'sleep 20; dumpsys telephony.registry | grep -oE "mDataConnectionState=[-0-9]+" | tail -1'))


def cmd_test(args):
    push_phone_scripts()
    print(su('sh %s/celltest.sh %s' % (REMOTE, args[0] if args else 'test')))


def cmd_capture(args):
    if not args or args[0] not in ('start', 'stop'):
        sys.exit('usage: capture start [nas|rrc] | capture stop')
    push_phone_scripts()
    if args[0] == 'start':
        kind = args[1] if len(args) > 1 else 'rrc'
        with tempfile.NamedTemporaryFile(suffix='.cfg', delete=False) as t:
            t.write(diag.log_mask(diag.LTE, diag.MASKS[kind]))
        adb('push', t.name, REMOTE + '/mask.cfg')
        os.unlink(t.name)
        print(su('sh %s/diagrun.sh start %s/mask.cfg' % (REMOTE, REMOTE)))
    else:
        print(su('sh %s/diagrun.sh stop' % REMOTE))
        out = os.path.join(HERE, 'captures', datetime.datetime.now().strftime('%Y%m%d-%H%M%S.qmdl'))
        adb('pull', REMOTE + '/out.qmdl', out)
        print('saved', out, '\n')
        rrc.analyze(open(out, 'rb').read())


def cmd_analyze(args):
    if not args:
        sys.exit('usage: analyze FILE.qmdl [--cells]')
    rrc.analyze(open(args[0], 'rb').read(), show_cells='--cells' in args)


def cmd_efs_get(args):
    if len(args) != 2:
        sys.exit('usage: efs-get PATH SIZE')
    for r in efs.replies(diag_once(efs.get(args[0], 1, int(args[1])))):
        print(r)


def bandlock_read():
    for r in efs.replies(diag_once(efs.get(BANDPREF, 1, 8))):
        if r['op'] == 'get':
            return r
    sys.exit('no reply from the modem')


def cmd_bandlock(args):
    action = args[0] if args else 'status'
    if action == 'status':
        r = bandlock_read()
        if r['errno'] == 2:
            print('no band lock (file absent: all bands allowed)')
        else:
            print('errno %d, data %s -> LTE bands %s' % (r['errno'], r['data'].hex(), efs.mask_bands(r['data'])))
    elif action == 'apply':
        bands = [int(b) for b in args[1:]]
        if not bands or any(not 1 <= b <= 64 for b in bands):
            sys.exit('usage: bandlock apply BAND [BAND...]   (LTE bands 1-64)')
        opened = [r for r in efs.replies(diag_once(efs.fs_open(BANDPREF))) if r['op'] == 'open']
        if not opened or opened[0]['errno']:
            sys.exit('open failed: %s' % opened)
        fd = opened[0]['fd']
        res = efs.replies(diag_once(efs.fs_write(fd, efs.lte_mask(bands)) + efs.fs_close(fd)
                                    + efs.get(BANDPREF, 2, 8)))
        for r in res:
            print(r)
        got = [r for r in res if r['op'] == 'get']
        if got and efs.mask_bands(got[0]['data']) == sorted(bands):
            print('\nband lock written: LTE %s. Reboot the phone for it to take effect.' % sorted(bands))
        else:
            sys.exit('\nverification FAILED - check the replies above')
    elif action == 'undo':
        res = efs.replies(diag_once(efs.fs_unlink(BANDPREF) + efs.get(BANDPREF, 3, 8)))
        for r in res:
            print(r)
        if any(r['op'] == 'get' and r['errno'] == 2 for r in res):
            print('\nband lock removed. Reboot the phone for it to take effect.')
    else:
        sys.exit('usage: bandlock status | apply BAND... | undo')


def cmd_mcfg_bands(args):
    import mcfg
    variant = args[0] if args else 'K4'
    path = '/data/vendor/modem_config/mcfg_hw/generic/common/Lanai/LA/PDX245_%s/mcfg_hw.mbn' % variant
    su('cp %s %s/hw.mbn; chmod 644 %s/hw.mbn' % (path, REMOTE, REMOTE))
    with tempfile.NamedTemporaryFile(suffix='.mbn', delete=False) as t:
        local = t.name
    adb('pull', REMOTE + '/hw.mbn', local)
    items = mcfg.parse(open(local, 'rb').read())
    os.unlink(local)
    xml = items.get('efs:/policyman/band_set_01.xml', b'').decode('latin1')
    for name, d in mcfg.band_sets(xml).items():
        print('%-28s LTE %s\n%-28s NR NSA %s | NR SA %s' % (
            name, ','.join('B%d' % b for b in d['lte']), '',
            ','.join('n%d' % b for b in d['nr5g_nsa']) or '-', ','.join('n%d' % b for b in d['nr5g_sa']) or '-'))


COMMANDS = {'status': cmd_status, 'watch': cmd_watch, 'recover': cmd_recover, 'test': cmd_test,
            'capture': cmd_capture, 'analyze': cmd_analyze, 'efs-get': cmd_efs_get,
            'bandlock': cmd_bandlock, 'mcfg-bands': cmd_mcfg_bands}

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    COMMANDS[sys.argv[1]](sys.argv[2:])
