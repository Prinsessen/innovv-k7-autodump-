#!/usr/bin/env python3
# One short telnet interaction: login, send ONE command, dump raw output.
# Usage: python3 oneshot.py '<command>' [read_seconds]
import socket, sys, time
import os
HOST, PORT = os.environ.get("CAMHOST","192.168.1.254"), 23
cmd = sys.argv[1]
READ = int(sys.argv[2]) if len(sys.argv) > 2 else 35

s = socket.create_connection((HOST, PORT), timeout=10)
pending = b""; buf = b""
def feed(data):
    global pending
    out = bytearray(); i = 0; d = pending + data; pending = b""
    while i < len(d):
        b = d[i]
        if b == 0xff:
            if i+1 >= len(d): pending = d[i:]; break
            c = d[i+1]
            if c in (0xfb,0xfc,0xfd,0xfe):
                if i+2 >= len(d): pending = d[i:]; break
                opt = d[i+2]
                if c == 0xfd: s.sendall(bytes([0xff,0xfc,opt]))
                elif c == 0xfb: s.sendall(bytes([0xff,0xfe,opt]))
                i += 3; continue
            elif c == 0xff: out.append(0xff); i += 2; continue
            else: i += 2; continue
        out.append(b); i += 1
    return bytes(out)
def rx(t):
    global buf
    s.settimeout(t)
    try: d = s.recv(8192)
    except Exception: return False
    if d: buf += feed(d); return True
    return False

# login
t0 = time.time()
while time.time()-t0 < 12:
    rx(3)
    if b"login:" in buf: break
try:
    s.sendall(b"root\r\n"); time.sleep(0.6); rx(3)
    if b"Password:" in buf: s.sendall(b"\r\n"); time.sleep(0.4); rx(3)
    time.sleep(0.3)
    s.sendall((cmd + "\r\n").encode())
except Exception as e:
    print("SEND-ERR", e)
end = time.time() + READ
while time.time() < end:
    if not rx(2):
        if b"DONE123" in buf: break
print("=== RAW OUTPUT ===")
print(buf.decode(errors="replace"))
try: s.close()
except Exception: pass
