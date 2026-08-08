#!/usr/bin/env python3
"""One-shot INNOVV K7 free-space query over WiFi (Novatek cmd=3017).

Run ON THE PI with the dump service STOPPED (it owns wlan1) and the K7 already
powered (Shelly relay ON). Sequence:
  radio on -> scan/associate to INNOVV_K7 -> heartbeat 3016 -> cmd=3017
  -> parse <Value> (bytes) -> print human-readable -> disconnect -> radio off.

Exit codes: 0 = value parsed; 1 = no AP / not reachable; 2 = command sent but
            unparseable.
"""
import re
import sys
import time
import urllib.request

sys.path.insert(0, "/opt/innovv-k7")
from wifi_manager import WiFiManager

CAM = "192.168.1.254"


def cmd(path, timeout=15):
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

    try:
        hb = cmd("/?custom=1&cmd=3016", timeout=8)
        print("heartbeat: %s" % hb.strip()[:120], flush=True)
    except Exception as e:
        print("heartbeat FAILED: %s" % e, flush=True)

    rc = 2
    try:
        res = cmd("/?custom=1&cmd=3017", timeout=15)
        print("=== 3017 RESPONSE ===", flush=True)
        print(res.strip(), flush=True)
        m = re.search(r"<Value>\s*(\d+)\s*</Value>", res)
        if m:
            b = int(m.group(1))
            print("FREE_BYTES=%d" % b, flush=True)
            print("FREE_HUMAN=%.2f GB  (%.1f MB)" % (b / 1024**3, b / 1024**2), flush=True)
            rc = 0
        else:
            print("UNPARSEABLE - no <Value> element", flush=True)
    except Exception as e:
        print("3017 request FAILED: %s" % e, flush=True)

    time.sleep(1)
    wm.disconnect()
    time.sleep(1)
    wm.radio_off()
    print("done (rc=%d)" % rc, flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
