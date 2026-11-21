#!/bin/sh
# Self-reverting STATION-mode test for INNOV K7.
# Binary + config live in /mnt/app/wifi. Logs result to SD, then reboots
# (which restores the camera's normal AP). Nothing is made permanent.
LOG=/mnt/sd/sta_test.log
exec >"$LOG" 2>&1
echo "=== INNOV K7 station-mode test ==="
date 2>/dev/null
cp /mnt/sd/wpa_supplicant.conf /mnt/app/wifi/wpa_supplicant.conf 2>/dev/null
chmod 755 /mnt/app/wifi/wpa_supplicant 2>/dev/null

echo "--- stopping AP services ---"
killall -q hostapd 2>/dev/null
killall -q wpa_supplicant 2>/dev/null
killall -q udhcpc 2>/dev/null
killall -q udhcpd 2>/dev/null
sleep 2

echo "--- wlan0 up ---"
ifconfig wlan0 up
CP=/proc/net/rtl8821cs/wlan0/chan_plan
[ -e "$CP" ] && { echo "set chan_plan 0x76"; echo 0x76 > "$CP"; sleep 1; }

echo "--- start wpa_supplicant (wext) ---"
/mnt/app/wifi/wpa_supplicant -B -Dwext -i wlan0 -c /mnt/app/wifi/wpa_supplicant.conf
sleep 12
echo "--- iwconfig ---"; iwconfig wlan0 2>&1
echo "--- DHCP ---"; udhcpc -i wlan0 -t 8 -T 2 -n 2>&1
sleep 2
echo "--- ifconfig wlan0 ---"; ifconfig wlan0 2>&1
echo "--- routes ---"; route -n 2>&1
GW=$(route -n 2>/dev/null | awk '/^0.0.0.0/{print $2; exit}')
echo "--- ping gateway ($GW) ---"
[ -n "$GW" ] && ping -c 3 "$GW" 2>&1 || echo "no default gateway"
echo "--- ping 8.8.8.8 ---"; ping -c 3 8.8.8.8 2>&1
echo "=== test done; rebooting in 3s to restore AP ==="
sync
sleep 3
reboot
