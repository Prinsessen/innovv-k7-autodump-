#!/usr/bin/env python3
"""INNOVV K7 Live-View Proxy (on-demand).

Exposes the K7's Novatek MJPEG live stream (http://192.168.1.254:8192,
Content-Type multipart/x-mixed-replace) on the Pi's LAN address so a browser or
VLC on the home LAN / over the VPN can watch live at http://<pi-lan-ip>:8192/.

Design (on-demand coordination):
  * This service is mutually exclusive with innovv-k7-dump.service. The systemd
    unit stops the dump service on start (ExecStartPre) and restarts it on stop
    (ExecStopPost) so only one owner ever holds wlan1 + the camera mode.
  * On start: wlan1 radio on -> associate to INNOVV_K7 -> movie mode
    (cmd=3001&par=1) -> stop recording (cmd=2001&par=0) -> start live view
    (cmd=2015&par=1) -> TCP-proxy the MJPEG stream. A heartbeat keeps the AP
    and live state warm.
  * On SIGTERM/SIGINT (systemctl stop): stop live view (cmd=2015&par=0),
    disconnect, radio off — then ExecStopPost brings the dump service back.

The K7 must already be powered (Shelly relay ON via the openHAB state machine /
manual override) before starting this service. If the camera cannot be reached
the service exits non-zero and the dump service is restored by ExecStopPost.
"""
import sys
import time
import socket
import signal
import threading
import urllib.request

sys.path.insert(0, "/opt/innovv-k7")
from wifi_manager import WiFiManager

CAM = "192.168.1.254"
CAMPORT = 8192
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8192

_wm = None
_stop = threading.Event()


def cmd(path, timeout=6):
    try:
        urllib.request.urlopen("http://%s%s" % (CAM, path), timeout=timeout).read(200)
        return True
    except Exception as e:
        print("cmd err %s: %s" % (path, e), flush=True)
        return False


def relay(client, addr):
    """Bridge one client <-> a fresh camera MJPEG GET."""
    up = None
    try:
        up = socket.create_connection((CAM, CAMPORT), timeout=8)
        up.sendall(b"GET / HTTP/1.0\r\n\r\n")
        while not _stop.is_set():
            data = up.recv(32768)
            if not data:
                break
            client.sendall(data)
    except Exception:
        pass
    finally:
        for s in (client, up):
            try:
                if s:
                    s.close()
            except Exception:
                pass


def keepalive():
    while not _stop.is_set():
        if _stop.wait(15):
            break
        cmd("/?custom=1&cmd=3016")  # heartbeat keeps AP + live state warm


def start_live():
    """Connect wlan1 and put the camera into live-view state."""
    global _wm
    _wm = WiFiManager(interface="wlan1", ssid="INNOVV_K7", password="12345678")
    print("radio_on", flush=True)
    _wm.radio_on()
    time.sleep(3)
    ok = _wm.connect()
    if not ok:
        for _ in range(8):
            time.sleep(3)
            if _wm.is_connected():
                ok = True
                break
    print("connected: %s" % ok, flush=True)
    if not ok:
        return False
    time.sleep(2)
    print("movie 3001&par=1: %s" % cmd("/?custom=1&cmd=3001&par=1"), flush=True)
    time.sleep(1)
    print("stoprec 2001&par=0: %s" % cmd("/?custom=1&cmd=2001&par=0"), flush=True)
    time.sleep(1)
    print("live 2015&par=1: %s" % cmd("/?custom=1&cmd=2015&par=1"), flush=True)
    time.sleep(1)
    return True


def cleanup(*_a):
    if _stop.is_set():
        return
    _stop.set()
    print("cleanup: stop live view + disconnect + radio off", flush=True)
    try:
        cmd("/?custom=1&cmd=2015&par=0")  # stop live view
        cmd("/?custom=1&cmd=2001&par=1")  # resume recording
    except Exception:
        pass
    try:
        if _wm:
            _wm.disconnect()
            time.sleep(1)
            _wm.radio_off()
    except Exception:
        pass
    print("cleanup done", flush=True)


def main():
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    if not start_live():
        print("NO_CONN - camera unreachable (is the K7 powered?)", flush=True)
        cleanup()
        sys.exit(1)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LISTEN_HOST, LISTEN_PORT))
    srv.listen(8)
    srv.settimeout(1.0)
    print("PROXY LISTENING %s:%d -> %s:%d" % (LISTEN_HOST, LISTEN_PORT, CAM, CAMPORT), flush=True)

    threading.Thread(target=keepalive, daemon=True).start()

    try:
        while not _stop.is_set():
            try:
                c, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            print("client: %s" % (addr,), flush=True)
            threading.Thread(target=relay, args=(c, addr), daemon=True).start()
    finally:
        try:
            srv.close()
        except Exception:
            pass
        cleanup()


if __name__ == "__main__":
    main()
