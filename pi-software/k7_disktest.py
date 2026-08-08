#!/usr/bin/env python3
"""One-shot INNOVV K7 disk-capacity probe.

Tries several Novatek "disk info" style commands and dumps the RAW XML for each,
so we can see whether the K7 firmware exposes Total / Used capacity (not just the
free-space <Value> that cmd=3017 already gives us).

Run ON THE PI with the dump service STOPPED (it owns wlan1) and the K7 already
powered (Shelly relay ON).
"""
import sys
import time
import urllib.request

sys.path.insert(0, "/opt/innovv-k7")
from wifi_manager import WiFiManager

CAM = "192.168.1.254"

# Candidate commands to probe. 3017 is our verified free-space cmd; the rest are
# Novatek platform "disk info / total capacity" candidates seen across firmwares.
PROBES = [
    ("3017 free-space (verified)", "/?custom=1&cmd=3017"),
    ("4003 disk-info", "/?custom=1&cmd=4003"),
    ("3016 heartbeat", "/?custom=1&cmd=3016"),
    ("3018 (total? candidate)", "/?custom=1&cmd=3018"),
    ("3019 (used? candidate)", "/?custom=1&cmd=3019"),
    ("3020 (capacity? candidate)", "/?custom=1&cmd=3020"),
    ("2010 sd-info candidate", "/?custom=1&cmd=2010"),
    ("get diskinfo (str)", "/?custom=1&str=diskinfo"),
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

    for label, path in PROBES:
        print("\n=== %s  ->  %s ===" % (label, path), flush=True)
        try:
            res = cmd(path)
            print(res.strip()[:600], flush=True)
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
