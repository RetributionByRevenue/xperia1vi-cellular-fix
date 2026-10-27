# Runs on the phone as root (su). Drives Qualcomm's diag_mdlog.
#   sh diagrun.sh once  <mask.cfg>   send the commands, wait 5 s, stop -> /data/local/tmp/xc/out.qmdl
#   sh diagrun.sh start <mask.cfg>   start logging in the background
#   sh diagrun.sh stop               stop logging -> /data/local/tmp/xc/out.qmdl
W=/data/local/tmp/xc
collect() {
  /vendor/bin/diag_mdlog -k >/dev/null 2>&1; sleep 2
  cat $W/log/*.qmdl > $W/out.qmdl 2>/dev/null; chmod 644 $W/out.qmdl
  ls -l $W/out.qmdl
}
case "$1" in
  once|start)
    rm -rf $W/log; mkdir -p $W/log
    setsid /vendor/bin/diag_mdlog -f "$2" -o $W/log -c > /dev/null 2>&1 < /dev/null &
    sleep 5
    if [ "$1" = once ]; then collect; else echo "logging (pid $(pidof diag_mdlog))"; fi ;;
  stop) collect ;;
  *) echo "usage: diagrun.sh once|start <cfg> | stop" ;;
esac
