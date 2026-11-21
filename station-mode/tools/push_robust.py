#!/usr/bin/env python3
# Robust chunked upload to INNOV K7 over telnet (busybox + uudecode).
# Each 12KB chunk is uudecoded to a part file and size-verified; retries on
# mismatch; parts are concatenated at the end. No server / nc needed.
# Usage: python3 push_robust.py <localfile> <camera_dir> <camera_basename>
import socket, sys, time, binascii, re

HOST, PORT = "192.168.1.254", 23
SRC, CDIR, CNAME = sys.argv[1], sys.argv[2], sys.argv[3]
CHUNK = 12000

s = socket.create_connection((HOST, PORT), timeout=10)
pending = b""
buf = b""

def feed(data):
    global pending
    out = bytearray(); i = 0
    d = pending + data; pending = b""
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
    except socket.timeout: return False
    except Exception: return False
    if d: buf += feed(d); return True
    return False

def send_raw(b):
    s.settimeout(None)
    for i in range(0, len(b), 1024):
        s.sendall(b[i:i+1024])
        if len(b) > 4096: time.sleep(0.003)

ctr = 0
def run(cmd, t=90):
    global buf, ctr
    ctr += 1; mk = f"__D{ctr}__"
    buf = b""
    send_raw((cmd + f"\r\necho {mk}$?\r\n").encode())
    end = time.time() + t
    while time.time() < end:
        if not recv_more(3): continue
        if buf.count(mk.encode()) >= 2: break
    txt = buf.decode(errors="replace")
    parts = txt.split(mk)
    mid = parts[1] if len(parts) > 1 else ""
    mid = mid.split("\n", 1)[1] if "\n" in mid else ""
    return mid.strip("\r\n")

# --- login ---
s.settimeout(10)
t0 = time.time()
while time.time() - t0 < 12:
    recv_more(3)
    if b"login:" in buf: break
send_raw(b"root\r\n")
time.sleep(0.6); recv_more(3)
if b"Password:" in buf:
    send_raw(b"\r\n"); time.sleep(0.4); recv_more(3)
buf = b""
run("echo READY")

data = open(SRC, "rb").read()
total = len(data)
nch = (total + CHUNK - 1) // CHUNK
print(f"total {total} bytes -> {nch} chunks")

def uu_of(chunk, name):
    out = [f"begin 644 {name}\n".encode()]
    for j in range(0, len(chunk), 45):
        out.append(binascii.b2a_uu(chunk[j:j+45]))
    out.append(b"`\nend\n")
    return b"".join(out).decode()

run(f"mkdir -p {CDIR}; rm -f {CDIR}/p??????")
ok = True
for i in range(nch):
    chunk = data[i*CHUNK:(i+1)*CHUNK]
    pname = f"p{i:06d}"
    uud = uu_of(chunk, pname)
    good = False
    for attempt in range(4):
        out = run(f"cd {CDIR} && uudecode <<'__U__'\n{uud}__U__\nwc -c {pname}", 120)
        got = None
        for tok in out.split():
            if tok.isdigit(): got = int(tok); break
        if got == len(chunk):
            good = True; break
        print(f"  chunk {i} attempt {attempt+1}: got {got} want {len(chunk)} -- retry")
    if not good:
        print(f"FAILED at chunk {i}"); ok = False; break
    if (i+1) % 5 == 0 or i == nch-1:
        print(f"  {i+1}/{nch} chunks ok")

if ok:
    out = run(f"cd {CDIR} && cat p?????? > {CNAME} && rm -f p?????? && chmod 755 {CNAME} && wc -c {CNAME} && (md5sum {CNAME} 2>/dev/null || echo no-md5sum)")
    print("ASSEMBLE:", out)
try: send_raw(b"exit\r\n"); s.close()
except Exception: pass
