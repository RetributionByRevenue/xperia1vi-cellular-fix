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
    ./xperia.py fallback status         network mode, 2G setting, current voice RAT  (no root)
    ./xperia.py fallback on             automatic mode + 2G allowed: incoming calls ring via CSFB
    ./xperia.py fallback off            block 2G again (Android's "2G network protection")

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
# LTE band preference per modem subscription (sub 0 = physical SIM slot, sub 1 = the eSIM here)
BANDPREFS = {
    'SIM slot 1': '/nv/item_files/modem/mmode/lte_bandpref',
    'SIM slot 2': '/nv/item_files/modem/mmode/lte_bandpref_Subscription01',
}
SLOT = '1'                                                             # telephony slot of the eSIM
SUBID = os.environ.get('XPERIA_SUBID', '7')                            # subscription id of the eSIM

# Network-type bitmasks for `cmd phone set-allowed-network-types-for-users` (binary string,
# bit n = TelephonyManager.NETWORK_TYPE_BITMASK_* for network type n+1).
AUTO_MASK = '10011111101111111111'     # 654335: everything (2G/3G/4G/5G), "automatic"
LTE_ONLY_MASK = '1000000000000'        # 4096: LTE only
NET_TYPES = ['GPRS', 'EDGE', 'UMTS', 'CDMA', 'EVDO_0', 'EVDO_A', '1xRTT', 'HSDPA', 'HSUPA', 'HSPA',
             'IDEN', 'EVDO_B', 'LTE', 'EHRPD', 'HSPA+', 'GSM', 'TD_SCDMA', 'IWLAN', 'LTE_CA', 'NR']
GEN = {'GPRS': '2G', 'EDGE': '2G', 'GSM': '2G', 'CDMA': '2G', '1xRTT': '2G',
       'UMTS': '3G', 'HSDPA': '3G', 'HSUPA': '3G', 'HSPA': '3G', 'HSPA+': '3G', 'TD_SCDMA': '3G',
       'LTE': '4G', 'LTE_CA': '4G', 'NR': '5G'}
# The per-SIM switch in Settings → Network → SIM. Android 16 calls it "2G network protection"
# (on = 2G blocked); Android 12-15 call it "Allow 2G" (on = 2G allowed).
SWITCH_2G = {'2G network protection': False, 'Allow 2G': True}   # text -> meaning of checked=true
UI_XML = '/data/local/tmp/xc_ui.xml'


# ---------------------------------------------------------------- adb helpers
def adb(*args, check=True):
    r = subprocess.run(['adb', *args], capture_output=True, text=True)
    if check and r.returncode:
        sys.exit('adb %s failed: %s' % (' '.join(args), r.stderr.strip()))
    return r.stdout


def sh(cmd):
    r = subprocess.run(['adb', 'shell', cmd], capture_output=True, text=True)
    return r.stdout + r.stderr


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


def bandlock_read(path):
    for r in efs.replies(diag_once(efs.get(path, 1, 8))):
        if r['op'] == 'get':
            return r
    sys.exit('no reply from the modem')


def bandlock_write(path, bands):
    opened = [r for r in efs.replies(diag_once(efs.fs_open(path))) if r['op'] == 'open']
    if not opened or opened[0]['errno']:
        sys.exit('%s: open failed: %s' % (path, opened))
    fd = opened[0]['fd']
    res = efs.replies(diag_once(efs.fs_write(fd, efs.lte_mask(bands)) + efs.fs_close(fd)
                                + efs.get(path, 2, 8)))
    got = [r for r in res if r['op'] == 'get']
    if not got or efs.mask_bands(got[0]['data']) != sorted(bands):
        sys.exit('%s: verification FAILED: %s' % (path, res))
    print('%-58s LTE %s  (verified)' % (path, sorted(bands)))


def cmd_bandlock(args):
    action = args[0] if args else 'status'
    if action == 'status':
        for name, path in BANDPREFS.items():
            r = bandlock_read(path)
            if r['errno'] == 2:
                print('%-12s no band lock (all bands allowed)' % name)
            else:
                print('%-12s LTE bands %s' % (name, efs.mask_bands(r['data'])))
    elif action == 'apply':
        bands = [int(b) for b in args[1:]]
        if not bands or any(not 1 <= b <= 64 for b in bands):
            sys.exit('usage: bandlock apply BAND [BAND...]   (LTE bands 1-64)')
        for path in BANDPREFS.values():
            bandlock_write(path, bands)
        print('\nReboot the phone for it to take effect.')
    elif action == 'undo':
        for name, path in BANDPREFS.items():
            res = efs.replies(diag_once(efs.fs_unlink(path) + efs.get(path, 3, 8)))
            gone = any(r['op'] == 'get' and r['errno'] == 2 for r in res)
            print('%-12s %s' % (name, 'removed' if gone else 'FAILED: %s' % res))
        print('\nReboot the phone for it to take effect.')
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


def mask_summary(value):
    """Decode a network-type bitmask (int) into '2G 3G 4G 5G (GPRS|EDGE|...)'."""
    names = [n for i, n in enumerate(NET_TYPES) if value >> i & 1]
    gens = sorted({GEN[n] for n in names if n in GEN})
    return '%s (%s)' % (' '.join(gens) or 'nothing', '|'.join(names) or '-')


def radio_log_masks():
    """Latest (user, enable_2g) masks the framework logged for our subscription, or None."""
    log = sh('logcat -b radio -d 2>/dev/null | grep -E "SubId%s,setAllowedNetworkTypes user=" | tail -1' % SUBID)
    m = re.search(r'user=(\d+),enable_2g=(\d+)', log)
    return (int(m.group(1)), int(m.group(2))) if m else None


def ui_nodes():
    sh('uiautomator dump %s >/dev/null 2>&1' % UI_XML)
    return re.findall(r'<node [^>]*>', sh('cat ' + UI_XML))


def node_attr(node, name):
    m = re.search(r'\b%s="([^"]*)"' % name, node)
    return m.group(1) if m else ''


def node_bounds(node):
    return [int(x) for x in re.findall(r'\d+', node_attr(node, 'bounds'))]   # x1 y1 x2 y2


def find_2g_switch():
    """Scroll the SIM's network settings page until the 2G row is visible.
    Returns (switch_node, checked_means_allowed) or exits."""
    for _ in range(6):
        nodes = ui_nodes()
        rows = [n for n in nodes if node_attr(n, 'text') in SWITCH_2G]
        if rows:
            allowed_when_checked = SWITCH_2G[node_attr(rows[0], 'text')]
            y1, y2 = node_bounds(rows[0])[1], node_bounds(rows[0])[3]
            switches = [n for n in nodes if node_attr(n, 'class') == 'android.widget.Switch']
            near = [n for n in switches if y1 - 150 < (node_bounds(n)[1] + node_bounds(n)[3]) // 2 < y2 + 150]
            if len(near) == 1 or (not near and len(switches) == 1):
                return (near or switches)[0], allowed_when_checked
            sys.exit('found the 2G row but not its switch: %s' % (switches or 'no switches on screen'))
        if not any('Preferred network type' in node_attr(n, 'text') or 'Network' == node_attr(n, 'text')
                   for n in nodes):
            sys.exit('the network settings page did not open (is the screen unlocked?)')
        sh('input swipe 540 1900 540 500 300; sleep 2')
    sys.exit('no "2G network protection" / "Allow 2G" row on the network settings page')


def set_2g(allow):
    """Flip the per-SIM 2G switch in Settings through the UI (there is no shell command for it)."""
    sh('input keyevent KEYCODE_WAKEUP; input keyevent KEYCODE_HOME; sleep 1; '
       'am start -n com.android.settings/.Settings\\$MobileNetworkActivity --ei android.provider.extra.SUB_ID %s '
       '>/dev/null 2>&1; sleep 4' % SUBID)
    node, checked_means_allowed = find_2g_switch()
    allowed = (node_attr(node, 'checked') == 'true') == checked_means_allowed
    if allowed != allow:
        b = node_bounds(node)
        sh('input tap %d %d; sleep 3' % ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2))
        node, _ = find_2g_switch()
        allowed = (node_attr(node, 'checked') == 'true') == checked_means_allowed
    sh('input keyevent KEYCODE_HOME')
    if allowed != allow:
        sys.exit('2G switch did not change (now: 2G %s)' % ('allowed' if allowed else 'blocked'))
    print('2G %s (Settings switch)' % ('allowed' if allowed else 'blocked'))


def fallback_status():
    user = sh('cmd phone get-allowed-network-types-for-users -s %s' % SLOT).strip().splitlines()
    print('network mode (user):  %s' % (user[-1] if user else '?'))
    masks = radio_log_masks()
    if masks:
        print('   user reason:       %s' % mask_summary(masks[0]))
        print('   2G setting:        %s' % ('2G allowed' if masks[1] & 1 else '2G BLOCKED (2G network protection on)'))
        print('   effective:         %s' % mask_summary(masks[0] & masks[1]))
    else:
        print('   2G setting:        unknown (nothing in the radio log yet; `fallback on` sets and shows it)')
    reg = registry()
    voice, data, ch, _ = serving(reg)
    rat = re.findall(r'getRilVoiceRadioTechnology=([^,]*)', reg)
    print('voice: %s on %s   data: %s' % (voice, rat[-1] if rat else '?', data))
    if ch.isdigit():
        gsm = 'GSM' in (rat[-1] if rat else '')
        print('serving: %s' % ('GSM arfcn %s' % ch if gsm else '%s (earfcn %s)' % (rrc.band(int(ch)), ch)))


def cmd_fallback(args):
    """2G fallback for incoming calls. Tune Talk delivers incoming calls over the circuit-switched
    side (no VoLTE paging), so the phone must be able to fall back to Celcom's 2G: automatic
    network mode AND the 2G switch allowed. Either one alone sends calls to voicemail."""
    action = args[0] if args else 'status'
    if action == 'status':
        fallback_status()
    elif action == 'on':
        print(sh('cmd phone set-allowed-network-types-for-users -s %s %s' % (SLOT, AUTO_MASK)).strip())
        set_2g(True)
        print(); fallback_status()
        print('\nIncoming calls now ring through CSFB (the phone drops to 2G for the call and returns to LTE after).')
    elif action == 'off':
        set_2g(False)
        print(); fallback_status()
        print('\n2G is blocked again: incoming calls will go to voicemail on this network.')
    else:
        sys.exit('usage: fallback status | on | off')


COMMANDS = {'status': cmd_status, 'watch': cmd_watch, 'recover': cmd_recover, 'test': cmd_test,
            'capture': cmd_capture, 'analyze': cmd_analyze, 'efs-get': cmd_efs_get,
            'bandlock': cmd_bandlock, 'mcfg-bands': cmd_mcfg_bands, 'fallback': cmd_fallback}

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    COMMANDS[sys.argv[1]](sys.argv[2:])
