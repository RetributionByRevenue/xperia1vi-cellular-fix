# usage: sh celltest.sh <label>   (run as root on the phone)
# 60 pings + a 20 MB download over the cellular interface only (bypasses Wi-Fi),
# sampling the serving EARFCN and SINR every 2 s.
IF=$(ip -o -4 addr | grep rmnet_data | head -1 | awk '{print $2}')
if [ -z "$IF" ]; then echo "[$1] no cellular data interface (data not connected)"; exit 1; fi
TMP=/data/local/tmp/ct_ping.txt
logcat -c -b all 2>/dev/null
ping -c 60 -i 0.5 -W 2 -I $IF 1.1.1.1 > $TMP 2>&1 &
P=$!
bands=""
for i in $(seq 1 15); do
  ch=$(dumpsys telephony.registry | grep -E mServiceState | tail -1 | grep -oE "mChannelNumber=[0-9]+" | cut -d= -f2)
  sn=$(dumpsys telephony.registry | grep -oE "rssnr=-?[0-9]+" | tail -1 | cut -d= -f2)
  bands="$bands $ch/$sn"; sleep 2
done
wait $P
# Prefer Termux's curl if installed, else Android's own.
T=/data/data/com.termux/files/usr
if [ -x $T/bin/curl ]; then CURL="env LD_LIBRARY_PATH=$T/lib $T/bin/curl"; else CURL=/system/bin/curl; fi
spd=$($CURL --interface $IF -o /dev/null -s -w "%{speed_download}" --max-time 15 \
      "https://speed.cloudflare.com/__down?bytes=20000000" 2>/dev/null)
spd=${spd%.*}; case "$spd" in ''|*[!0-9]*) spd=0 ;; esac
rel=$(logcat -d -b all | grep -c "LTE_RRC_CONN_REL_IND Cause = 2")
echo "[$1] $(grep -oE '[0-9]+% packet loss' $TMP) | rtt min/avg/max $(tail -1 $TMP | cut -d= -f2) | down $((spd * 8 / 1000)) kbps | RRC cause2 drops: $rel"
echo "    earfcn/sinr every 2s:$bands"
rm -f $TMP
