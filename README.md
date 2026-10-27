# Xperia 1 VI (KDDI SOG13): cellular fix and toolkit

The investigation and fix from 2026-10-27 for poor and dropping mobile data on a
KDDI Xperia 1 VI used in Malaysia, with the tools used to diagnose it.

## The problem

On a Tune Talk eSIM (it runs on Celcom's network, 502-19), the Xperia had stalls,
packet loss, multi-second latency spikes, and frequent complete loss of data. Only
toggling airplane mode brought data back. A Moto on the same plan, sitting right
next to it, worked fine, so the fault is in the phone.

## Diagnosis

The Qualcomm modem log (captured with the phone's own `diag_mdlog`) showed:

| Band | Connection requests answered | Unanswered |
|------|------------------------------|------------|
| B1 2100 MHz | 0 | 3 |
| B3 1800 MHz | 0 | 12 |
| B8 900 MHz  | 10 | 0 |
| B28 700 MHz | 7 | 0 |

**On the mid bands (B1/B3) the network never hears the Xperia's uplink.** Its
downlink is fine, so the phone keeps choosing those cells and then can't transmit.
That causes:
- stalls, packet loss, and band ping-pong (B28↔B8 every few seconds),
- failed handovers (`ReestablishmentRequest cause=handoverFailure`),
- eventually, after unanswered TAU/attach attempts, the modem moves to another
  network, gets rejected (TAU reject #9, attach reject #15), and puts Celcom on its
  "backoff forbidden PLMN" list. It never retries Celcom until airplane mode is toggled.

This is a mid-band transmit problem, either hardware (antenna/PA path) or a
KDDI-config power limit. It is not a missing-band problem: the KDDI hardware profile
(`PDX245_K4`, rule `rf_bands_roam` for MCC 502) already enables every band Celcom
uses (LTE B1/3/5/7/8/28…, NR NSA n78). Turning 5G on or off made no difference.

## What was changed on the phone

| Change | Where | Survives reboot / no root | Undo |
|---|---|---|---|
| **LTE band lock B8 + B28** | modem EFS `/nv/item_files/modem/mmode/lte_bandpref_Subscription01` = `0x0000000008000080` | yes (lives in the modem, survives factory reset) | `./xperia.py bandlock undo`, then reboot (needs root) |
| Network mode LTE-only | Android, slot 1 | yes | Settings → Network → Preferred type, or `cmd phone set-allowed-network-types-for-users -s 1 10011111101111111111` |
| APN `TuneTalk-celcom3g` (`celcom3g`, types `default,ia,supl`, IPv4v6), preferred | Android APN db | yes | Settings → APN |
| APN `tunetalk` (disabled, don't use: Celcom rejects it and the phone loses service) | Android APN db | yes | delete in Settings → APN |
| Mobile data on for the eSIM | Android | yes | Settings |
| Play Store Termux replaced with GitHub Termux 0.118.3 (to match Termux:Boot's signing key) | apps | yes | - |

The band lock only takes effect after a **full reboot**. Airplane mode and the
"mobile radio power" toggle don't reload it. After the reboot only B8/B28 cells
appeared, and pings over cellular had 0% loss at about 28 ms.

A Sony firmware update that reloads the modem config might reset the band lock.
If the B1/B3 problems come back, check with `./xperia.py bandlock status`.

**If data gets stuck:** `./xperia.py recover` (or toggle airplane mode by hand).

## Toolkit

Requires `adb` and Python 3 on the PC. Uses the only connected adb device, or the one
named by `ANDROID_SERIAL`. Commands marked *root* need the phone re-rooted first.

```
./xperia.py status                  serving cell/band, signal, neighbour bands, data state
./xperia.py watch [SECONDS]         sample serving/neighbour bands every 5 s
./xperia.py recover                 airplane-mode toggle to re-attach            (root)
./xperia.py test [LABEL]            60 pings + 20 MB download over cellular only  (root)
./xperia.py capture start [nas|rrc] start modem log capture                       (root)
./xperia.py capture stop            stop, save to captures/, and analyze          (root)
./xperia.py analyze FILE.qmdl       decode a capture: link events + per-cell uplink table
./xperia.py efs-get PATH SIZE       read a modem EFS item                         (root)
./xperia.py bandlock status|apply 8 28|undo                                       (root)
./xperia.py mcfg-bands [K4|A4|J4…]  band sets in a hardware modem profile         (root)
```

Captures are saved to `captures/`, which is git-ignored because modem logs contain the
SIM's IMSI. Don't publish `.qmdl` files.

### Layout

```
xperia.py          PC-side command-line tool (adb)
lib/diag.py        DIAG HDLC framing, log-packet extraction, log-mask builder
lib/nas.py         LTE NAS (EMM/ESM) message decoder
lib/rrc.py         LTE RRC decoder (conn request/setup/release, re-establishment, handover)
                   + per-cell "uplink not heard" analysis
lib/efs.py         modem EFS over DIAG: GET, OPEN/WRITE/CLOSE, UNLINK; band-mask helpers
lib/mcfg.py        MCFG (.mbn) parser and policyman band_set decoder
phone/celltest.sh  on-phone test: ping + download via rmnet, band samples (Termux curl or /system/bin/curl)
phone/diagrun.sh   on-phone wrapper around /vendor/bin/diag_mdlog
termux/            Termux:Boot scripts (start-hello → ~/.termux/boot/, hello-unlock.sh → ~/)
captures/          saved .qmdl modem logs
```

### How the modem access works

`/vendor/bin/diag_mdlog -f mask.cfg` replays HDLC-framed DIAG requests from a "mask
file" to the modem and records every reply into a `.qmdl`. The tool builds request
files (log masks, or EFS commands), runs them as root, and parses the replies.

Verified on this modem (MPSS.DE.5.0, Snapdragon 8 Gen 3):
- EFS `GET` is subsystem `0x13` cmd `0x27`: `{i32 size, i32 path_len, u16 seq, path}`.
  `size` must equal the item size, or you get `ERANGE`.
- `OPEN 0x02 {i32 oflag, i32 mode, path}` → `{fd, errno}`, `WRITE 0x05 {fd, offset, data}`,
  `CLOSE 0x03 {fd}` and `UNLINK 0x08 {path}` all work. The handle from OPEN stays valid
  across separate diag_mdlog runs.
- **Never send cmd `0x1A`/`0x19`** (deprecated PUT/GET). `PUT 0x26`'s layout never worked here.
- Log packets arrive wrapped in `0x98` multi-SIM frames. RRC OTA log version `0x1e`:
  header PCI at byte 6, EARFCN at 8, PDU type at 14, length at 19, message at 24.
  PDU types: 7 PCCH, 8 DL-CCCH, 9 DL-DCCH, 10 UL-CCCH, 11 UL-DCCH.

## Phone facts

- Sony Xperia 1 VI (pdx245), KDDI SOG13, Android 16 system / vendor 69.2.A.4.110,
  `ro.vendor.radio.variant=KDDI-K4`
- Root: ReSukiSU (KernelSU fork), not persistent. Apps only see `su` once toggled on
  in ReSukiSU → Superuser; there's no prompt.
- Active line: eSIM "Tunetalk call only" (subId 7, telephony slot 1, modem Subscription01),
  MCC/MNC 502-150 on Celcom 502-19. The eSIM has a junk HPLMN (999999), so Android
  never auto-provisions an APN for it.
- Termux:Boot needs the **GitHub** Termux (key `CN=APK Signer, OU=Earth`), not the
  Play Store build. The boot script starts a background session (notification only,
  no popup window).
