import urllib.request, threading, time, re, os
CAM=os.environ.get("CAM","192.168.1.254")  # camera IP (AP default; set CAM=<camera-sta-ip> for STA)
def api(cmd,par=None,t=8):
    url=f"http://{CAM}/?custom=1&cmd={cmd}"+(f"&par={par}" if par is not None else "")
    try: return urllib.request.urlopen(url,timeout=t).read().decode("utf-8","replace")
    except Exception as e: return f"ERR {e}"
# stop recording (their verified method)
print("stop-rec cmd=2001&par=0:", " ".join(api(2001,0).split())[:120])
time.sleep(1)
# get a movie filename from the HTTP dir listing
html=""
try: html=urllib.request.urlopen(f"http://{CAM}/INNOVVK7/Movie_E/",timeout=15).read().decode("utf-8","replace")
except Exception as e: print("listing err",e)
m=re.findall(r'href="([^"]+\.MP4)"',html,re.I)
m=[x for x in m if "del=1" not in x]
if not m: print("no files found"); import sys; sys.exit(0)
F=m[len(m)//2]  # a middle (complete) file
if not F.startswith("/"): F="/INNOVVK7/Movie_E/"+F
print("test file:",F)
# heartbeat thread
stop=False
def hb():
    while not stop:
        api(3012,t=5); time.sleep(4)
threading.Thread(target=hb,daemon=True).start()
# download with heartbeats, cap at 80MB
url=f"http://{CAM}{F}"
t0=time.time(); n=0; CAP=80_000_000
try:
    with urllib.request.urlopen(url,timeout=30) as r:
        while True:
            c=r.read(262144)
            if not c: break
            n+=len(c)
            if n>=CAP: break
except Exception as e:
    print("download err:",e)
dt=time.time()-t0
stop=True
print(f"RESULT: {n} bytes in {dt:.1f}s = {n/dt/1024/1024:.2f} MB/s  ({n/dt/1024:.0f} KB/s)")
# resume recording
time.sleep(1); print("start-rec cmd=2001&par=1:", " ".join(api(2001,1).split())[:120])
