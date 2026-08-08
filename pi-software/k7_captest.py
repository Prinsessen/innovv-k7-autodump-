#!/usr/bin/env python3
"""One-shot INNOVV K7 capability probe (READ-ONLY).

Dumps raw XML for a set of Novatek query commands so we can see what this K7
firmware actually exposes:
  cmd=3022  Get hardware capacity   (5.3.22)
  cmd=3024  Get card status         (5.3.24)
  cmd=3030  Get movie size capacity (5.3.30)
  cmd=3007  Power-off / auto-off timer (5.3.7) -- READ attempts only, NO set
  cmd=3014  Query current status    (5.3.14, for context)

For 3007 we deliberately try read-style variants (type=get / no par) and do NOT
send a par value, so the camera's auto-off setting is never changed.

Run ON THE PI with the dump service STOPPED (owns wlan1) and the K7 powered.
"""
import sys
import time
import urllib.request

sys.path.insert(0, "/opt/innovv-k7")
from wifi_manager import WiFiManager

CAM = "192.168.1.254"

# (label, path, is_readonly)  -- everything here is read-only by design.
PROBES = [
    ("3022 hardware capacity", "/?custom=1&cmd=3022"),
    ("3024 card status", "/?custom=1&cmd=3024"),
    ("3030 movie size capacity", "/?custom=1&cmd=3030"),
    ("3014 query current status", "/?custom=1&cmd=3014"),
    # --- auto-off READ attempts (no par sent -> should not change the setting) ---
    ("3007 auto-off (type=get)", "/?custom=1&cmd=3007&type=get"),
    ("3007 auto-off (str=get)", "/?custom=1&cmd=3007&str=get"),
]


def cmd(path, timeout=12):
    return urllib.request.urlopen("http://%s%s" % (CAM, path),
                                  timeout=timeout).read().decode("utf-8", "replace")


def main():
    wm = WiFiManager(interface="wlan1", ssid="INNOVV_K7", password="12345678")
    print("radio_on", flush=True)
    wm.radio_on()
    time.sleep(3)

    connected = False
    for attempt in range(15):  # ~90s window for the AP to appear
        try:
            if wm.is_ssid_visible():
                print("AP visible -> connecting (attempt %d)" % (attempt + 1), flush=True)
                if wm.connect():
                    connected = True
                    break
            else:
                print("attempt %d: INNOVV_K7 not visible yet" % (attempt + 1), flush=True)
        except Exception as e:
            print("scan/connect error: %s" % e, flush=True)
        time.sleep(6)

    if not connected:
        print("NO_AP - could not associate to INNOVV_K7", flush=True)
        try:
            wm.disconnect(); wm.radio_off()
        except Exception:
            pass
        sys.exit(1)

    print("connected (signal %s dBm)" % wm.get_signal_dbm(), flush=True)
    time.sleep(2)

    # heartbeat first to confirm httpd is alive
    try:
        hb = cmd("/?custom=1&cmd=3016", timeout=8)
        print("heartbeat: %s" % hb.strip().replace("\n", " ")[:120], flush=True)
    except Exception as e:
        print("heartbeat FAILED: %s" % e, flush=True)

    for label, path in PROBES:
        print("\n=== %s  ->  %s ===" % (label, path), flush=True)
        try:
            res = cmd(path)
            print(res.strip()[:900], flush=True)
        except Exception as e:
            print("REQUEST FAILED: %s" % e, flush=True)
        time.sleep(1.5)  # be gentle on the Novatek httpd

    time.sleep(1)
    wm.disconnect()
    time.sleep(1)
    wm.radio_off()
    print("\ndone", flush=True)


if __name__ == "__main__":
    main()
