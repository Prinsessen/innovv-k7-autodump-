#!/usr/bin/env python3
# One persistent telnet session to the INNOV K7. Runs a sequence of commands
# (passed as a file, blank-line separated) and prints each one's output.
# Usage: python3 session.py <commands_file>
import socket, sys, time

HOST, PORT = "192.168.1.254", 23
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

def recv_more(t=3):
    global buf
    s.settimeout(t)
    try: d = s.recv(8192)
    except Exception: return False
    if d: buf += feed(d); return True
    return False

def send_raw(b):
    s.settimeout(None)
    for i in range(0, len(b), 1024):
        s.sendall(b[i:i+1024])
        if len(b) > 4096: time.sleep(0.003)

ctr = 0
def run(cmd, t=60):
    global buf, ctr
    ctr += 1; mk = f"__D{ctr}__"
    buf = b""
    send_raw((cmd + f"; echo {mk}$?\r\n").encode())
    end = time.time() + t
    while time.time() < end:
        recv_more(2)
        if mk.encode() in buf:
            recv_more(1)            # flush trailing bytes
            break
    txt = buf.decode(errors="replace")
    parts = txt.split(mk)
    if len(parts) >= 3:             # echo ON: output is between the two markers
        out = parts[1]
        out = out.split("\n", 1)[1] if "\n" in out else ""
    else:                           # echo OFF: output is before the single marker
        out = parts[0]
    return out.strip("\r\n")

# login
s.settimeout(10); t0 = time.time()
while time.time() - t0 < 12:
    recv_more(3)
    if b"login:" in buf: break
send_raw(b"root\r\n"); time.sleep(0.6); recv_more(3)
if b"Password:" in buf:
    send_raw(b"\r\n"); time.sleep(0.4); recv_more(3)
buf = b""; run("echo READY"); run("stty -echo 2>/dev/null")

cmds = open(sys.argv[1]).read().split("\n@@@\n")
for c in cmds:
    c = c.strip()
    if not c: continue
    print(f"\n$ {c.splitlines()[0]}", flush=True)
    print(run(c, 120), flush=True)
try: send_raw(b"exit\r\n"); s.close()
except Exception: pass
