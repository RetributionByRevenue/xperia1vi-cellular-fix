# usage: sh celltest.sh <label>   (run as root on the phone)
IF=$(ip -o -4 addr | grep rmnet_data | head -1 | awk '{print $2}')
logcat -c -b all 2>/dev/null
ping -c 60 -i 0.5 -W 2 -I $IF 1.1.1.1 > /data/local/tmp/ct_ping.txt 2>&1 &
P=$!
bands=""
for i in $(seq 1 15); do
  ch=$(dumpsys telephony.registry | grep -E mServiceState | tail -1 | grep -oE "mChannelNumber=[0-9]+" | cut -d= -f2)
  sn=$(dumpsys telephony.registry | grep -oE "rssnr=-?[0-9]+" | tail -1 | cut -d= -f2)
  bands="$bands $ch/$sn"; sleep 2
done
wait $P
T=/data/data/com.termux/files/usr
spd=$(LD_LIBRARY_PATH=$T/lib $T/bin/curl --interface $IF -o /dev/null -s -w "%{speed_download}" --max-time 15 "https://speed.cloudflare.com/__down?bytes=20000000")
rel=$(logcat -d -b all | grep -c "LTE_RRC_CONN_REL_IND Cause = 2")
echo "[$1] $(grep -oE '[0-9]+% packet loss' /data/local/tmp/ct_ping.txt) | $(tail -1 /data/local/tmp/ct_ping.txt | cut -d= -f2) | down $((${spd%.*}*8/1000)) kbps | RRC cause2 drops: $rel"
echo "    earfcn/sinr every 2s:$bands"
rm -f /data/local/tmp/ct_ping.txt
