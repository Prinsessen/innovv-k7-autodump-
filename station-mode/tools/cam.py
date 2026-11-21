#!/usr/bin/env python3
# Minimal scripted telnet client for the INNOV K7 (busybox telnetd).
# Usage: python3 cam.py '<shell command>'
# Logs in as root (empty password), runs the command, prints output.
import socket, sys, time

HOST, PORT = "192.168.1.254", 23
cmd = sys.argv[1]
if cmd.startswith("@"):            # read command body from a file (for big payloads)
    with open(cmd[1:], "r") as f:
        cmd = f.read()

s = socket.create_connection((HOST, PORT), timeout=8)
s.settimeout(3)
pending = b""

def feed(data):
    """Strip/answer telnet IAC negotiation, return clean bytes."""
    global pending
    out = bytearray()
    i = 0
    d = pending + data
    pending = b""
    while i < len(d):
        b = d[i]
        if b == 0xff:  # IAC
            if i + 1 >= len(d):
                pending = d[i:]; break
            c = d[i+1]
            if c in (0xfb, 0xfc, 0xfd, 0xfe):  # WILL/WONT/DO/DONT
                if i + 2 >= len(d):
                    pending = d[i:]; break
                opt = d[i+2]
                # refuse everything: DO->WONT, WILL->DONT
                if c == 0xfd:   resp = bytes([0xff,0xfc,opt])
                elif c == 0xfb: resp = bytes([0xff,0xfe,opt])
                else:           resp = b""
                try: s.sendall(resp)
                except Exception: pass
                i += 3; continue
            elif c == 0xff:
                out.append(0xff); i += 2; continue
            else:
                i += 2; continue
        out.append(b); i += 1
    return bytes(out)

def recv_until(subs, t=10):
    buf = b""
    end = time.time() + t
    while time.time() < end:
        try:
            data = s.recv(4096)
        except socket.timeout:
            data = b""
        if data:
            buf += feed(data)
            for sub in subs:
                if sub in buf:
                    return buf, True
        else:
            time.sleep(0.15)
    return buf, False

# --- login ---
recv_until([b"login:"], 10)
s.sendall(b"root\r\n")
pre, _ = recv_until([b"Password:", b"# ", b"~ #", b"$ "], 6)
if b"Password:" in pre:
    s.sendall(b"\r\n")
    recv_until([b"# ", b"~ #", b"$ "], 6)

# --- run command with a done-marker; marker appears twice: once in the
#     echoed input line, once as real output. Wait for the 2nd. ---
marker = "___CAMDONE___"
payload = (cmd + f'\r\necho {marker}$?\r\n').encode()
s.settimeout(None)                 # block during send (large pastes)
CH = 1024
for i in range(0, len(payload), CH):
    s.sendall(payload[i:i+CH])
    if len(payload) > 4096:
        time.sleep(0.004)          # pace into the pty to avoid dropped bytes
s.settimeout(3)
buf = b""
end = time.time() + 240
while time.time() < end:
    try:
        data = s.recv(4096)
    except socket.timeout:
        data = b""
    if data:
        buf += feed(data)
        if buf.count(marker.encode()) >= 2:
            break
    else:
        time.sleep(0.1)
text = buf.decode(errors="replace")
parts = text.split(marker)
out = parts[1] if len(parts) > 1 else text   # between echo-marker and result-marker
out = out.split("\n", 1)[1] if "\n" in out else out  # drop "$?" remainder of echoed line
print(out.rstrip("\r\n "))
try: s.sendall(b"exit\r\n"); s.close()
except Exception: pass
