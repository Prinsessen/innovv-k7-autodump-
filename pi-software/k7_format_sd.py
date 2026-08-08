#!/usr/bin/env python3
"""One-shot INNOVV K7 SD-card format over WiFi (Novatek cmd=3010&par=1).

Run ON THE PI, with the dump service STOPPED (it owns wlan1 otherwise) and the
K7 already powered (Shelly relay ON). Sequence:
  radio on -> scan/associate to INNOVV_K7 -> heartbeat -> cmd=3010&par=1
  -> parse <Status>0</Status> -> disconnect -> radio off.

Exit codes: 0 = format confirmed (Status 0); 1 = no AP / not reachable;
            2 = command sent but Status not 0 / unparseable.
"""
import sys
import time
import urllib.request

sys.path.insert(0, "/opt/innovv-k7")
from wifi_manager import WiFiManager

CAM = "192.168.1.254"


def cmd(path, timeout=30):
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
        print("NO_AP - could not associate to INNOVV_K7 (camera AP down)", flush=True)
        try:
            wm.disconnect(); wm.radio_off()
        except Exception:
            pass
        sys.exit(1)

    print("connected: %s (signal %s dBm)" % (True, wm.get_signal_dbm()), flush=True)
    time.sleep(2)

    # Prove the HTTP API is alive before formatting.
    try:
        hb = cmd("/?custom=1&cmd=3016", timeout=8)
        print("heartbeat OK: %s" % hb.strip()[:160], flush=True)
    except Exception as e:
        print("heartbeat FAILED (API not up): %s" % e, flush=True)
        wm.disconnect(); wm.radio_off()
        sys.exit(1)

    # Format the SD card (par=1 = format card, per NT9666x cmd reference 5.3.10).
    print(">>> sending FORMAT: cmd=3010&par=1", flush=True)
    rc = 2
    try:
        res = cmd("/?custom=1&cmd=3010&par=1", timeout=45)
        print("=== FORMAT RESPONSE ===", flush=True)
        print(res, flush=True)
        compact = res.replace(" ", "").replace("\n", "").replace("\r", "")
        if "<Status>0</Status>" in compact:
            print("FORMAT_OK — <Status>0</Status> (SD card formatted)", flush=True)
            rc = 0
        else:
            print("FORMAT_UNKNOWN_STATUS — command returned but Status != 0", flush=True)
            rc = 2
    except Exception as e:
        print("FORMAT request FAILED: %s" % e, flush=True)
        rc = 2

    # Re-query free space so we can see the card is now empty.
    try:
        fs = cmd("/?custom=1&cmd=3017", timeout=8)  # 3017 = free space / capacity query
        print("post-format capacity query: %s" % fs.strip()[:200], flush=True)
    except Exception as e:
        print("capacity query skipped: %s" % e, flush=True)

    time.sleep(1)
    wm.disconnect()
    time.sleep(1)
    wm.radio_off()
    print("done (rc=%d)" % rc, flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
