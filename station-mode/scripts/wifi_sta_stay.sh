#!/bin/sh
# v4 — Switch INNOV K7 wlan0 to STATION mode via nl80211, join "HOME_SSID", and STAY.
# - uses wpa_supplicant_nl80211 (static, libnl) with -Dnl80211,wext (wext alone can't complete assoc on rtl8821cs)
# - night-1 recipe: 5 GHz-preferred wpa_supplicant.conf + Realtek chan_plan 0x76
# - wpa_supplicant debug log -> /mnt/sd/wpa_debug.log ; scan_results logged at ~12 s
# - associated ONLY when wpa_cli says COMPLETED ; DHCP retried
# - log sync'ed after every step so a crash still leaves a trail
# - not associated in ~90 s -> reboot ; watchdog: no real IP after 180 s -> reboot (back to AP)
LOG=/mnt/sd/sta_stay.log
log() { echo "$(date +%T 2>/dev/null) $*" >> "$LOG"; sync; }
: > "$LOG"
log "=== station-and-stay v4 (nl80211) ==="

# Global safety net: if wlan0 has no real IP after 180 s, reboot back to the AP.
(
  sleep 180
  IPCHK=$(ifconfig wlan0 2>/dev/null | grep -o 'inet addr:[0-9.]*' | cut -d: -f2)
  case "$IPCHK" in
    ""|192.168.0.3|192.168.1.254) echo "$(date +%T) WATCHDOG: no real IP ($IPCHK) after 180s -> reboot" >> "$LOG"; sync; sleep 1; reboot ;;
    *) echo "$(date +%T) WATCHDOG: ok, IP=$IPCHK -> staying" >> "$LOG"; sync ;;
  esac
) &

mkdir -p /mnt/app/wifi
cp /mnt/sd/wpa_supplicant_nl80211 /mnt/app/wifi/wpa_supplicant 2>/dev/null
# Use the 5 GHz-preferred config that WORKED on night 1 (got <pi-ip-on-iot-net>).
# (The any-band config picked a BSSID it could not complete association with.)
cp /mnt/sd/wpa_supplicant.conf     /mnt/app/wifi/wpa.conf       2>/dev/null
chmod 755 /mnt/app/wifi/wpa_supplicant
log "files: $(ls -l /mnt/app/wifi | tr '\n' ' ')"

log "stop AP services"
killall -q hostapd 2>/dev/null
killall -q udhcpd 2>/dev/null
killall -q wpa_supplicant 2>/dev/null
killall -q udhcpc 2>/dev/null
sleep 2
ifconfig wlan0 0.0.0.0 2>/dev/null
ifconfig wlan0 up
CP=/proc/net/rtl8821cs/wlan0/chan_plan
[ -e "$CP" ] && { echo 0x76 > "$CP"; log "chan_plan set 0x76 (as on night 1)"; sleep 1; }
log "wlan0 up: $(iwconfig wlan0 2>&1 | head -2 | tr '\n' ' ')"

log "start wpa_supplicant (debug -> /mnt/sd/wpa_debug.log)"
/mnt/app/wifi/wpa_supplicant -Dnl80211,wext -i wlan0 -c /mnt/app/wifi/wpa.conf -d > /mnt/sd/wpa_debug.log 2>&1 &
sync
( sleep 12; echo "$(date +%T) scan_results:" >> "$LOG"; wpa_cli -p /var/run/wpa_supplicant -i wlan0 scan_results >> "$LOG" 2>&1; sync ) &
sleep 2
log "wpa_cli ping: $(wpa_cli -p /var/run/wpa_supplicant -i wlan0 ping 2>&1 | tr '\n' ' ')"

ASSOC=0
i=0
while [ $i -lt 45 ]; do
  ST=$(wpa_cli -p /var/run/wpa_supplicant -i wlan0 status 2>/dev/null | grep '^wpa_state=' | cut -d= -f2)
  APMAC=$(iwconfig wlan0 2>/dev/null | grep -o 'Access Point: [0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f:]*')
  log "[$i] wpa_state=$ST $APMAC"
  if [ "$ST" = "COMPLETED" ]; then ASSOC=1; break; fi   # only trust wpa_cli; iwconfig shows a BSSID while still trying
  i=$((i+1)); sleep 2
done

if [ $ASSOC -ne 1 ]; then
  log "NOT associated after ~90s -> scan results:"
  wpa_cli -p /var/run/wpa_supplicant -i wlan0 scan_results >> "$LOG" 2>&1
  log "rebooting to restore AP"
  sync; sleep 2; reboot
  exit 0
fi

log "associated; DHCP"
ifconfig wlan0 0.0.0.0 2>/dev/null
n=0
while [ $n -lt 6 ]; do
  udhcpc -i wlan0 -n -t 6 -T 3 >> "$LOG" 2>&1 && break   # forks + keeps renewing after lease
  log "dhcp retry $n"; n=$((n+1)); sleep 3
done
IP=$(ifconfig wlan0 2>/dev/null | grep -o 'inet addr:[0-9.]*' | cut -d: -f2)
log "IP=$IP"
log "$(wpa_cli -p /var/run/wpa_supplicant -i wlan0 status 2>&1 | tr '\n' ' ')"
log "$(route -n 2>&1 | tr '\n' ' ')"
log "=== staying in station mode ==="
