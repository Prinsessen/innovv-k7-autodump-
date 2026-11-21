#!/bin/sh
# INNOV K7 - bring wlan0 up as a WiFi STATION (join an existing SSID).
# Requires: a wpa_supplicant binary at WPA_BIN and a config at WPA_CONF.
# Run manually over telnet first to confirm it connects, then persist (see README).

WPA_BIN=/etc/wifi/wpa_supplicant
WPA_CONF=/etc/wifi/wpa_supplicant.conf
IFACE=wlan0

echo "[sta] loading Realtek 8821cs driver (if needed)"
if ! lsmod | grep -q 8821cs; then
	# init.sh does: modprobe 8821cs and waits for wlan0 to appear
	/usr/share/wifiscripts/init.sh
else
	echo "[sta] 8821cs already loaded"
fi

echo "[sta] stopping any AP/hostapd/wpa_supplicant/udhcp that owns wlan0"
killall -q hostapd 2>/dev/null
killall -q wpa_supplicant 2>/dev/null
killall -q udhcpc 2>/dev/null
killall -q udhcpd 2>/dev/null
sleep 1

echo "[sta] bringing $IFACE up"
ifconfig $IFACE up
sleep 1

# Enable the driver's 5 GHz channel plan so it scans 5 GHz (same code the
# manufacturer's AP path uses). Harmless if the node doesn't exist.
CHANPLAN=/proc/net/rtl8821cs/$IFACE/chan_plan
if [ -e "$CHANPLAN" ]; then
	echo "[sta] enabling 5 GHz channel plan (0x76)"
	echo 0x76 > "$CHANPLAN"
	sleep 1
fi

echo "[sta] starting wpa_supplicant (wext) against $WPA_CONF"
# -B = background, -Dwext = manufacturer's driver interface for this chip.
# If wext fails to associate, try: -Dnl80211
"$WPA_BIN" -B -Dwext -i "$IFACE" -c "$WPA_CONF"
sleep 4

echo "[sta] requesting an IP via DHCP"
udhcpc -i "$IFACE" -t 6 -T 3 &

echo "[sta] done. Check association + IP with:  iwconfig $IFACE ; ifconfig $IFACE"
