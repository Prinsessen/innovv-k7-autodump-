#!/usr/bin/env python3
"""INNOVV K7 opportunistic SD-format watcher.

The K7's SD card is hung, so its WiFi AP only flickers up for a few seconds
during a fresh boot before firmware hangs on storage-init. This watcher keeps
hammering that window: it power-cycles the camera on a fixed cadence (via the
Shelly relay over HTTP) and scans continuously; the instant INNOVV_K7 is visible
it associates and fires cmd=3010&par=1 (format SD card), confirming <Status>0</Status>.

Run on the Pi with the dump service STOPPED. Logs to /tmp/k7_format_watch.log.
Exits 0 the moment a format is confirmed; keeps trying until DEADLINE otherwise.
"""
import sys
import time
import urllib.request

sys.path.insert(0, "/opt/innovv-k7")
from wifi_manager import WiFiManager

CAM        = "192.168.1.254"
SHELLY     = "192.168.1.62"   # <-- set to your Shelly relay's LAN IP
LOG        = "/tmp/k7_format_watch.log"
DEADLINE_S = 30 * 60      # give up after 30 min
CYCLE_S    = 120          # power-cycle the K7 every 2 min to make a fresh boot window
OFF_S      = 18           # relay-off duration for a clean discharge


def log(msg):
    line = time.strftime("%H:%M:%S") + "  " + msg
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def shelly(on):
    try:
        urllib.request.urlopen(
            "http://%s/rpc/Switch.Set?id=0&on=%s" % (SHELLY, "true" if on else "false"),
            timeout=6).read(100)
        return True
    except Exception as e:
        log("shelly set %s failed: %s" % (on, e))
        return False


def cam(path, timeout=30):
    return urllib.request.urlopen("http://%s%s" % (CAM, path),
                                  timeout=timeout).read().decode("utf-8", "replace")


def try_format(wm):
    """AP is visible — associate and fire the format. Returns True on Status 0."""
    if not wm.connect():
        log("  associate failed (AP vanished mid-connect)")
        return False
    log("  ASSOCIATED (signal %s dBm) — sending heartbeat" % wm.get_signal_dbm())
    try:
        cam("/?custom=1&cmd=3016", timeout=6)
    except Exception as e:
        log("  heartbeat failed, API not up yet: %s" % e)
        return False
    log("  >>> FORMAT cmd=3010&par=1")
    try:
        res = cam("/?custom=1&cmd=3010&par=1", timeout=45)
        log("  RESPONSE: " + res.replace("\n", " ").strip()[:200])
        if "<Status>0</Status>" in res.replace(" ", "").replace("\n", "").replace("\r", ""):
            log("  ✅ FORMAT_OK — <Status>0</Status>")
            return True
        log("  Status != 0 (unexpected)")
    except Exception as e:
        log("  format request failed: %s" % e)
    return False


def main():
    open(LOG, "w").close()
    log("=== K7 format watcher start (deadline %d min) ===" % (DEADLINE_S // 60))
    wm = WiFiManager(interface="wlan1", ssid="INNOVV_K7", password="12345678")
    wm.radio_on()
    time.sleep(2)

    t0 = time.time()
    last_cycle = 0.0
    powered = False

    while time.time() - t0 < DEADLINE_S:
        # Periodic power-cycle to force a fresh boot window.
        if time.time() - last_cycle >= CYCLE_S:
            log("power-cycle: relay OFF %ds" % OFF_S)
            shelly(False)
            powered = False
            time.sleep(OFF_S)
            log("power-cycle: relay ON (fresh boot)")
            shelly(True)
            powered = True
            last_cycle = time.time()

        # Fast scan loop — catch the brief AP window right after boot.
        try:
            if wm.is_ssid_visible():
                log("INNOVV_K7 VISIBLE — attempting format")
                if try_format(wm):
                    try:
                        wm.disconnect()
                    except Exception:
                        pass
                    log("=== SUCCESS — SD formatted, watcher exiting ===")
                    wm.radio_on()  # leave radio up so dump can resume immediately
                    sys.exit(0)
                # failed this window; disconnect and keep hammering
                try:
                    wm.disconnect()
                except Exception:
                    pass
        except Exception as e:
            log("scan error: %s" % e)
        time.sleep(3)

    log("=== DEADLINE reached — no successful format (AP never stabilised) ===")
    sys.exit(1)


if __name__ == "__main__":
    main()
