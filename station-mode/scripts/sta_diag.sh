#!/bin/sh
# Diagnostic wrapper: capture cardv's console + a 5 s system monitor to the SD card,
# then run the v4 station switch. Everything is sync'ed so it survives a reboot.
rm -f /mnt/sd/console_sta.log /mnt/sd/sta_monitor.log
cp /mnt/sd/consnoop /tmp/consnoop 2>/dev/null; chmod 755 /tmp/consnoop
/tmp/consnoop /mnt/sd/console_sta.log &
(
  while true; do
    {
      echo "=== $(date +%T) $(uptime)"
      echo "cardv=$(pidof cardv) wpa=$(pidof wpa_supplicant) udhcpc=$(pidof udhcpc) hostapd=$(pidof hostapd) udhcpd=$(pidof udhcpd)"
      free | sed -n 2p
      ifconfig wlan0 2>/dev/null | grep -o 'inet addr:[0-9.]*'
      iwconfig wlan0 2>/dev/null | grep -oE 'Mode:[A-Za-z]+|ESSID:"[^"]*"|Access Point: [0-9A-Fa-f:]+'
      dmesg -c 2>/dev/null
    } >> /mnt/sd/sta_monitor.log 2>&1
    sync
    sleep 5
  done
) &
sleep 2
sh /mnt/sd/wifi_sta_stay.sh
