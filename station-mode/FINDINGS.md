# INNOV K7 — WiFi station-mode project — FINDINGS (2026-11-18)

Goal: make the INNOV K7 dash cam **join an existing WiFi SSID ("HOME_SSID")** itself,
instead of only creating its own access point. The manufacturer app has no option for it.

**Status at end of night 1 (later CORRECTED, see §13 — station mode was NOT achieved):** Station mode works (camera joined
"HOME_SSID", got IP <pi-ip-on-iot-net> from the router, shown as "k7-bridge"). The one missing
piece — a `wpa_supplicant` binary — is built and confirmed running on the camera.
Remaining work: **persistence** (auto-join at every boot).

---

## 1. The device

- Product: **INNOV K7** (strings: `A:\INNOVVK7\...`, wlan AP SSID `INNOVV_K7`, hostname `NVTEVM`, router name `k7-bridge`). A car DVR / dashcam, likely a main unit for ethernet sub-cameras (strings: `EthcamTxFW1.bin`, `EthRestart`, multi-cam `[0]..[4]`).
- SoC: **Novatek NA51055** (NT96660-class SDK: `SDK_VER="NVT_NT96660_Linux_V0.4.8"`), ARM **Cortex-A9** (ARMv7, 32-bit).
- OS: **Linux 4.19.91**, BusyBox **1.24.1**, uClibc. Built 2025-03-15.
- Main app: **`/usr/bin/cardv`** (the whole camera application; manages WiFi, recording, RTSP, HTTP API).

## 2. The firmware file `in-k7-1.bin` (28,695,292 bytes)

- **NOT encrypted.** Plain, readable strings; entropy ~6.7–7.3 (encrypted would be ~8.0).
- Header @0x00: 16 high-entropy bytes (likely a hash/signature — NOT a plain MD5/SHA; **not reversed**). `0x1c` = total size (0x1b5dafc). Partition table at 0x80 (5 entries: offset,size,type).
- 5 logical partitions inside the .bin:
  1. DTB/fdt (`d00dfeed`), 2. u-boot, 3. kernel uImage (`Linux-4.19.91`), 4. rootfs (UBI/UBIFS), 5. app (UBI/UBIFS).
- Non-kernel partitions are wrapped in a **"CKSM" header** (64 bytes: magic `CKSM`, ver `0x16040719`, a 16-bit checksum, data offset 0x40, data length). The checksum algorithm is **NOT reversed** (doesn't match standard sum/xor/crc/md5). This + the 16-byte global header are what make building a *modified* flashable `.bin` hard.
- Extracted the two UBIFS volumes with a custom pure-Python reader (`tools/ubifs_extract.py`, handles UBI + UBIFS nodes + LZO/zlib). Output is in `extracted-rootfs/`.

## 3. Live device — filesystems & access (from running camera)

MTD layout (`/proc/mtd`): mtd0 loader, mtd1 fdt, mtd2 fdt.restore, mtd3 uboot, **mtd4 uenv**,
mtd5 kernel, **mtd6 rootfs** (0x51c0000 ~82MB), mtd7 pstore, **mtd8 app** (0x21a0000 ~34MB),
mtd9 **all** (0x8000000 = 128MB whole flash).

Mounts (`mount` / `df`):
- `/` = `ubi0:rootfs`, 70MB, **100% FULL**, rw but mounted `assert=read-only`. => cannot add files to `/etc` or `/etc/init.d`.
- `/mnt/app` = `ubi1_0`, 24MB, **24MB free, rw, persistent (NAND)**. <-- where we put the binary.
- `/tmp`, `/var/run` = tmpfs (RAM, lost on reboot).
- `/mnt/sd` = exfat, 476GB (~7GB free), persistent but removable; files show as 0755.
- `/mnt/pstore` = yaffs2, 4MB.
- **Overlay (`/mnt/overlay_rw0`) is NOT mounted** — the overlay mechanism in `S05_FS_Overlay`
  needs a `rootfs1` MTD partition that doesn't exist here. So `/etc` is the read-only rootfs.

Access (all confirmed):
- **root with EMPTY password** (`/etc/passwd`: `root::0:0:...`).
- **telnetd** on port 23 (standalone, started by `/etc/init.d/S25_Net` last line — NOT via inetd).
- **FTP server** on port 21: `inetd.conf` → `21 stream tcp nowait root ftpd ftpd -w /mnt/sd`.
  Login root / empty password. **Write access, rooted at /mnt/sd.** THIS is the reliable transfer channel.
- TFTP on port 69 (`tftpd -l -c /home`). RTSP via `/bin/nvtrtspd`. HTTP API via cardv.

## 4. WiFi

- Chip: **Realtek RTL8821CS** (SDIO), **dual-band 2.4 + 5 GHz, 802.11ac**. Driver `8821cs.ko`
  (Realtek SDK v5.14.2). Config: `NVT_SDIO_WIFI=NVT_SDIO_WIFI_RTK`, `WIFI_RTK_MDL_8821CS`.
- AP mode (default) works: `hostapd` + `udhcpd`. Camera AP = SSID `INNOVV_K7`, runs on **5 GHz ch36**
  (its own AP proves 5 GHz works), wlan0 = 192.168.1.254, DHCP pool 192.168.1.20-254, **no gateway handed out**.
- **ROOT CAUSE of "no station option":** the **`wpa_supplicant` binary is MISSING** from the firmware.
  Only `hostapd`, `hostapd_cli`, `wpa_cli` are present. The script `up.sh sta` calls `wpa_supplicant`,
  which doesn't exist → station mode is non-functional out of the box. cardv DOES have STA code
  (`OnWiFiMode`, "Neither Wi-Fi AP nor Station mode!") but relies on the missing binary.

## 5. What we built & proved

- Cross-compiled a **static `wpa_supplicant` 2.10** for ARMv7 on the Mac:
  - Toolchain: Homebrew `messense/macos-cross-toolchains` → `armv7-unknown-linux-musleabihf` (musl, hardfloat).
  - Config: `CONFIG_DRIVER_WEXT=y` only (no libnl needed), internal TLS/crypto, fully static (`-static`).
  - Result: `deliverables/wpa_supplicant` — ELF 32-bit ARM, statically linked, 427,508 bytes.
  - Recipe: `deliverables/build-wpa_supplicant.sh`.
- Transferred it to the camera (`/mnt/app/wifi/wpa_supplicant`) **via FTP** and confirmed:
  `wpa_supplicant -v` → `wpa_supplicant v2.10 ... RC=0` → **runs natively** (see `results/vercheck.txt`).
- Ran a self-reverting **station test** (`deliverables/sta_test.sh`): switch wlan0 to STA, join "HOME_SSID",
  log to SD, reboot. **Router showed k7-bridge @ <pi-ip-on-iot-net>** → association + DHCP succeeded.
  (The on-device log said "unassociated" only because it checked at 12s, too early — see `results/sta_test.log`.)

## 6. WiFi config in use

`deliverables/wpa_supplicant.conf`: SSID **HOME_SSID**, WPA2-PSK, **prefers 5 GHz**
(high-priority network with a 5 GHz `freq_list`) with a 2.4 GHz fallback network.
`sta_test.sh` also sets the Realtek 5 GHz channel plan: `echo 0x76 > /proc/net/rtl8821cs/wlan0/chan_plan`.
Home "HOME_SSID" network is on subnet **<iot-subnet>.x** (separate from the Mac's <lan-subnet>.x — likely an IoT VLAN).

## 7. What works for driving the camera (important for tomorrow)

- **FTP (port 21) is the reliable channel** for moving files to/from `/mnt/sd` (curl `-u root:`).
- **Telnet is flaky** — connections drop, and the camera occasionally reboots under load.
  Best pattern discovered: use telnet to *run* a command that **writes its output to /mnt/sd**,
  then **read the result back over FTP**. Avoid many rapid telnet sessions.
- Transfer tools written (in `tools/`): `oneshot.py` (one telnet command, raw output — most reliable),
  `session.py`, `cam.py`, `push_robust.py` (uudecode chunking — uudecode on-device turned out flaky; FTP is better).
  Camera has **no `nc`** and **no `base64`**; it DOES have `wget`, `ftpget/ftpput`, `tftp`, `uudecode`.

## 8. OPEN PROBLEM — persistence (tomorrow)

Need the camera to join "HOME_SSID" automatically at every boot. `/etc/init.d` is read-only+full, no overlay.
Ideas to investigate, roughly in order of promise:
1. **Does `cardv` read a WiFi config from `/mnt/app`?** If we can set STA mode + SSID there, use the
   manufacturer's own path (but it needs `wpa_supplicant` reachable — maybe symlink/copy into a PATH dir,
   or patch where up.sh looks). `/mnt/app` is writable+persistent.
2. **U-Boot environment (mtd4 "uenv").** Check for `fw_setenv`/`fw_printenv` on device or in uboot; a
   `bootcmd`/preboot hook could run a script from `/mnt/app` at boot. Edit with care.
3. **crond** (started in `S15_NvtAppInit`) — does it read a crontab from a writable location? `@reboot` hook?
4. **Free a little space on the rootfs** to drop a tiny `/etc/init.d/S99_wifi_sta` (risky: fs is
   `assert=read-only` and 100% full; could corrupt — last resort).
5. **Bake into firmware `.bin`** — would need reversing the CKSM 16-bit checksum + the 16-byte global
   header + UBIFS repack (needs Linux mtd-utils). Highest effort / brick risk.

Also refine `sta_test.sh`: wait ~25-30s and loop until `iwconfig wlan0` shows an Access Point MAC
before running `udhcpc`; retry udhcpc a few times.

## 9. Reconnect checklist for tomorrow

1. Power on camera; connect Mac WiFi to `INNOVV_K7` (camera AP). Camera = **192.168.1.254**.
   Keep internet on the Mac's **Ethernet** so this chat/session stays alive, and turn OFF auto-join on the
   home WiFi so the Mac doesn't roam off the camera AP.
2. Verify: `nc -z 192.168.1.254 21` (FTP) and `:23` (telnet).
3. Files already on camera survive in `/mnt/app/wifi/` IF the camera wasn't fully reset; otherwise
   re-upload from `deliverables/` via FTP: `curl -u root: -T wpa_supplicant ftp://192.168.1.254/...`
   then copy into `/mnt/app/wifi/`.
4. Credentials: root / (empty password). SSID **HOME_SSID** (password is in `wpa_supplicant.conf`).

---

## 10. HTTP API + video-transfer speed (night 2 — 2026-11-21)

Goal of the session: benchmark how fast video can be pulled off the camera, to judge
whether a direct pull beats the "Pi bridge" idea. Firmware version (cmd=3012): **IK7.20250317.V05**.

### Open ports on the camera (AP, 192.168.1.254)
- **21** FTP (busybox ftpd, root/empty pw, rooted at /mnt/sd) — file transfer
- **23** telnet (root/empty pw) — flaky
- **80** HTTP control API (Novatek "custom" API, returns XML)
- **554** RTSP (only while streaming) · **3333**, **8192** open (other NTK services)

### Novatek HTTP control API (port 80)
Form: `http://192.168.1.254/?custom=1&cmd=<N>&par=<M>` → returns XML
(`<Function><Cmd>N</Cmd><Status>S</Status>...`). Confirmed commands:
- `cmd=3012` → firmware version string (`IK7.20250317.V05`)
- `cmd=3014` → full status dump (current value/Status for ~40 cmds; e.g. `2001`=record state)
- `cmd=3029` → returns AP `<SSID>INNOV_K7</SSID><PASSPHRASE>12345678</PASSPHRASE>`
- **`cmd=3001&par=<mode>` = change working mode.** `par=2` → **PLAYBACK mode, which STOPS
  recording** (verified: `cmd=2001` status went 1→0). `par=0` should be Movie/record mode (resume).
- File ops need playback mode (error strings: `3015 not PLAYBACK mode`, `4001 not PLAYBACK mode`),
  i.e. `cmd=3015` = file list, `cmd=4001` = download-related — both require playback. This is
  exactly what the phone app does: switch to playback (stop rec) → browse/download → back to movie.

### KEY RESULT — recording contention kills transfer speed
The camera records **two 1080p streams (front "F" + rear "R")** to the SD continuously,
~**106 MB per 1-minute clip**, in `/mnt/sd/INNOVVK7/Movie_E/` (4971 files ≈ 504 GB on the card).
- **While recording:** FTP download of a 106 MB clip ran at **~17 KB/s** (3.5 MB in 200 s) —
  effectively unusable. The bottleneck is the camera's CPU/SD I/O being saturated by dual-stream
  recording, NOT the 5 GHz WiFi link.
- **With recording stopped (playback mode):** still only **~36 KB/s** (6.6 MB in 180 s). So stopping
  recording roughly doubled it but it is still unusable (a single 106 MB clip would take ~50 min).
- **The WiFi air link is NOT the bottleneck:** measured Mac↔camera link = **5 GHz ch36, 80 MHz,
  signal -58 dBm, 1170 Mbps PHY, MCS 9** — excellent. The ceiling is the camera's own
  file-serving path (busybox ftpd + exfat read + the RTL8821CS hanging off a slow SDIO bus + a weak SoC).
- **The camera CRASHES/REBOOTS under sustained transfer load.** During the 180 s download the
  camera's services died (FTP refused, HTTP API timed out), the AP dropped, and the Mac roamed back
  to the home WiFi. Same instability we saw night 1 under load.

### CONCLUSION (answers the Pi-vs-direct question) — ⚠️ RETRACTED, see §11
**Bulk video offload over WiFi from this camera is not viable** — ~20–40 KB/s AND it crashes under
load — regardless of whether a Pi or the Mac pulls it, because the camera's single radio + SoC is the
bottleneck in both. Neither approach will be "fast". The only fast path for bulk footage is
**physically removing the SD card** (476 GB exfat, read at USB-reader speed). WiFi is fine for the
live RTSP preview and for the occasional small/single clip, not for moving hundreds of GB.

**HTTP direct file serving also tested (different code path from busybox ftpd):** the camera's HTTP
server (port 80) serves the SD card directly at **`http://192.168.1.254/INNOVVK7/Movie_E/<file>.MP4`**
(browser-friendly, supports HTTP Range / 206). But it did NOT help: ~25 KB/s on FTP and the camera
**rebooted again** during a *bounded* 15 MB request (got only 3 MB). So the instability is not
FTP-specific — **any sustained WiFi transfer reboots this camera within seconds / a few MB.**

### FINAL VERDICT — ⚠️ RETRACTED, see §11 (the Pi gets 2.9–4.5 MB/s; the slowness was on the Mac side)
- Speed: **~20–40 KB/s** over WiFi, regardless of protocol (FTP or HTTP) or recording on/off.
- Stability: **the camera reboots under essentially any transfer load** (seen repeatedly, even <15 MB).
- Therefore **WiFi bulk offload is not viable on this camera** — Pi vs direct makes no difference,
  the camera itself is the hard limit. Don't build an auto-offload around WiFi file pull.
- **Fast/reliable bulk offload = pull the microSD card** and read it in a USB card reader.
- Worth trying another day (not WiFi): the camera's **Ethernet** port (eth0, 192.168.0.3, telnetd/ftpd
  also run there) — a wired link avoids the SDIO-WiFi path and might be faster/stable. Unknown if the
  connector is a standard RJ45 reachable from a PC.
- WiFi is still fine for the **live RTSP preview** and pulling the *odd single* clip (slowly).

### Extra confirmation (2026-11-21, later)
- **Small-file control test:** a 427 KB file in the SD *root* (tiny directory) downloaded **fully** but
  took **24.5 s = 17.4 KB/s**. So the slowness is **uniform and fundamental** — not the huge Movie_E
  directory, not exfat fragmentation, not file size. The camera simply serves files over WiFi at
  ~17–25 KB/s. (A 106 MB clip ≈ 1.5–2 hours. Impractical.)
- **Station-mode "stay" test failed today:** switched wlan0 to STA via `wifi_sta_stay.sh`, but the
  log (`/mnt/sd/sta_stay.log`) shows wpa_supplicant started, never associated (empty "Access Point:"),
  DHCP got no lease, and the log cuts off mid-"Sending discover" — i.e. **the camera crashed/rebooted
  during the switch** and came back as its own AP. The camera is extremely unstable today; it reboots
  under essentially any WiFi reconfiguration or transfer. (Night 1 it associated once and got
  <pi-ip-on-iot-net>, so the path CAN work, but it's unreliable and the throughput ceiling is the same.)
- **Bottom line unchanged and now firm:** this camera's WiFi offload is ~17–25 KB/s and it crashes
  under load. Not viable for bulk video by any WiFi method. Use the **physical SD card**; optionally
  try **wired Ethernet** (eth0) another day. (Known class of problem: cheap SDIO WiFi modules on these
  Novatek SoCs negotiate a high PHY rate but deliver a tiny fraction of it.)

### How to stop/restart recording (for file transfer)
```
curl "http://192.168.1.254/?custom=1&cmd=3001&par=2"   # playback mode = stop recording
# ... transfer files via FTP (fast) ...
curl "http://192.168.1.254/?custom=1&cmd=3001&par=0"   # back to movie mode = resume recording
```
Recordings live under FTP at `INNOVVK7/Movie_E/` (also `EMR_E` events, `Photo_E` photos).

---

## 11. CORRECTION — the camera is NOT the bottleneck (2026-11-21)

The user's existing, stable Pi project (github.com/Prinsessen/innovv-k7-autodump-) shows the
right way to pull footage, and its own measurement: **2.9–4.5 MB/s at -31 dBm**
(docs/FIRMWARE_ANALYSIS.md). So the §10 verdict "WiFi offload is not viable" was **wrong**.

How the Pi does it (pi-software/k7_api.py):
- Connects to the camera's **AP** (`INNOVV_K7`, 192.168.1.254) with an ALFA AWUS036ACM USB3 adapter.
- **HTTP GET** `http://192.168.1.254/INNOVVK7/Movie_E/<file>`, 256 KB chunks, Range/resume, inline SHA-256.
- **Heartbeat `cmd=3012` every ~10 s** — "The K7 may disconnect idle clients after a timeout."
  `wait_ready()` polls the heartbeat after disruptive ops (e.g. delete) until httpd answers again.
- File list: HTML directory listing of `/INNOVVK7/Movie_E/` (or `cmd=3015`). Delete: `<path>?del=1`.
- Other verified cmds: 3016 fw version, 3017 SD free bytes, 3024 card status, 3005/3006 set date/time.
- Recording control: **`cmd=2001&par=0` stop / `par=1` start** (verified here too: Status=0).
- MJPEG live stream on port **8192**.

Re-test from the Mac with the Pi's exact method (HTTP + heartbeat every 4 s + recording stopped
via 2001): **538 KB in 41.9 s = ~13 KB/s**, then the camera closed the stream. Same as FTP.
=> The slowness is **specific to the Mac ↔ camera link/client**, not the camera's capability.
Mac link at the time: 5 GHz ch36/80 MHz, -58 dBm, 1170 Mbps PHY, MCS 9.

Open question to resolve next — why the Mac only gets ~13–36 KB/s when the Pi gets 3–4 MB/s:
1. Does the Pi *still* get 3–4 MB/s today (card is 99 % full: 504 GB / 4971 files)? Check Pi logs.
2. macOS AWDL (AirDrop/Continuity) channel hopping — known to wreck throughput; test with
   `sudo ifconfig awdl0 down`.
3. Distance/signal (-58 vs Pi's -31 dBm) — move the Mac right next to the camera.
4. Near-full SD / loop recording contention (Pi normally deletes after verify, keeping the card empty).

Implication for station-mode migration: the download method is the Pi's (HTTP + heartbeat),
unchanged; only the network path changes (camera joins "HOME_SSID" instead of the Pi joining the
camera AP). Speed in STA mode is still to be measured once STA is stable.

## 12. Station-mode attempts on 2026-11-21

- v1 `wifi_sta_stay.sh` (5 GHz conf, buggy assoc check): camera crashed/rebooted mid-switch.
- v2 (any-band `wpa_supplicant_any.conf`, no chan_plan): `wpa_cli` works with our wpa_supplicant.
  States: DISCONNECTED → SCANNING → **ASSOCIATING (BSSID xx:xx:xx:xx:xx:xx)** → never COMPLETED →
  back to SCANNING. v2 wrongly treated the BSSID shown by iwconfig as "associated", ran DHCP (no lease),
  and stayed in STA with no IP → camera unreachable until power-cycled.
- After that, the Mac reported "wrong password" on INNOVV_K7 — almost certainly our wpa_supplicant still
  running in RAM fighting the AP on the same radio. A full power-cycle fixed it. (Password unchanged: 12345678.)
- Lesson: association is only real at `wpa_state=COMPLETED`. Night 1 (which DID get <pi-ip-on-iot-net>) used the
  5 GHz-preferred config **plus chan_plan 0x76**; the any-band run without chan_plan picked an AP it could
  not complete with.
- v3.1: back to the night-1 recipe + `wpa_supplicant -d` debug log to `/mnt/sd/wpa_debug.log`, scan
  results logged, COMPLETED-only, and a 180 s watchdog that reboots to AP if there is no real IP.

## 13. ROOT CAUSE + MAJOR CORRECTION (2026-11-21)

### Correction: night 1 never succeeded
The "k7-bridge @ <pi-ip-on-iot-net>" lease that was taken as proof of station mode is the **user's Raspberry Pi**
(hostname `k7-bridge`, see the Pi repo: `openhab/items/innovv_k7.items` "Pi 4 (k7-bridge / …)" and
`k7-bridge-sd-*.img.gz` backups). The camera's busybox udhcpc does not even send a hostname, and its
wlan0 MAC is xx:xx:xx:xx:xx:xx. **The camera has never joined "HOME_SSID".** Earlier statements in this
file and README ("PROVEN POSSIBLE", "got <pi-ip-on-iot-net>") are wrong.

### What IS proven
- Our static `wpa_supplicant` 2.10 runs on the camera and talks to `wpa_cli` (PONG, states).
- The camera sees "HOME_SSID" on 5180 MHz (BSSID xx:xx:xx:xx:xx:xx, signal ~48) and 2437 MHz
  (xx:xx:xx:xx:xx:xx, ~26–36), WPA2-PSK-CCMP.
- The AP starts the 4-way handshake (17 EAPOL frames received over 6 attempts).

### Root cause (results/wpa_debug_2026-11-21.log)
```
Trying to associate with xx:xx:xx:xx:xx:xx (SSID='HOME_SSID' freq=5180 MHz)
ioctl[SIOCSIWAP]: Operation not permitted
Association request to the driver failed
RX EAPOL from xx:xx:xx:xx:xx:xx
Not associated - Delay processing of received EAPOL frame (bssid=xx:xx:xx:xx:xx:xx)
Authentication with xx:xx:xx:xx:xx:xx timed out.
```
The Realtek 8821cs v5.x driver rejects the wext set-BSSID ioctl (EPERM) and never emits the wext
association event (SIOCGIWAP, 0x8b15) — other wext events (0x8b06, 0x8b1a) do arrive. wpa_supplicant
therefore never learns it is associated, discards the AP's EAPOL frames, and times out. Same on both bands.
This is a **driver/interface mismatch, not password, signal or band**.

### Fix
Rebuild `wpa_supplicant` with **`CONFIG_DRIVER_NL80211=y`** (static, linked against libnl for
armv7/musl) and run with **`-Dnl80211`** — the same interface the camera's own `hostapd` uses
(`driver=nl80211` in up.sh). `cfg80211.ko` is loaded on the camera.

### Recovery note
v3.1 script + watchdog worked as designed: after 90 s without COMPLETED it rebooted the camera back to
its AP. No permanent change was made.

## 14. nl80211 build (2026-11-21)
- Built `deliverables/wpa_supplicant_nl80211`: wpa_supplicant 2.10, **nl80211 + wext**, static,
  ARMv7 musl, 651,396 bytes, linked against libnl 3.9.0 (libnl-3 + libnl-genl-3 only).
- Recipe: `deliverables/build-wpa_supplicant-nl80211.sh` (GCC 15 needs `-std=gnu11 -include time.h`
  for libnl; static link order `-lnl-genl-3 -lnl-3`). Config saved as `wpa_supplicant_nl80211.config`.
- `wifi_sta_stay.sh` v4 runs it with `-Dnl80211,wext` (5 GHz-preferred conf + chan_plan 0x76,
  COMPLETED-only, 90 s reboot + 180 s watchdog).

## 15. ✅ STATION MODE WORKS — nl80211 (2026-11-21 13:26)

- `wifi_sta_stay.sh` v4 + `wpa_supplicant_nl80211` (`-Dnl80211,wext`): launched 13:26:06, camera's AP gone
  13:26:08, camera found on the HOME_SSID network at **<camera-sta-ip>** by 13:26:33 (~25 s).
- API on <camera-sta-ip> works: `cmd=3012` → `IK7.20250317.V05`, `cmd=3016` OK, `/INNOVVK7/Movie_E/` listing OK.
- **Speed (Pi method: HTTP GET + heartbeat, recording stopped with cmd=2001&par=0), from the Mac via
  Ethernet → router → camera on HOME_SSID: 106,168,320 bytes in 66.5 s = 1.52 MB/s.** Full clip, no crash.
  (vs ~13 KB/s from the Mac's own WiFi on the camera AP; vs Pi's 2.9–4.5 MB/s at -31 dBm on the AP.)
  Camera's view of the HOME_SSID AP: 5180 MHz, signal ~48 (Realtek scale).
- Recording resumed afterwards with `cmd=2001&par=1` (same as the app does).
- **Not persistent yet:** the camera stays in STA only until it is power-cycled; then it is back to the
  `INNOVV_K7` AP. Making it auto-join at boot is the remaining work (§8).

Browser use while in STA mode:
- stop recording:   `http://<camera-sta-ip>/?custom=1&cmd=2001&par=0`
- file list:        `http://<camera-sta-ip>/INNOVVK7/Movie_E/`
- download a clip:  `http://<camera-sta-ip>/INNOVVK7/Movie_E/<file>.MP4`
- resume recording: `http://<camera-sta-ip>/?custom=1&cmd=2001&par=1`

## 16. STA session ended by a cardv-initiated reboot (2026-11-21 ~13:30)
- STA on <camera-sta-ip> was stable for ~4 min: watchdog logged `13:29:09 WATCHDOG: ok, IP=<camera-sta-ip> -> staying`,
  the 106 MB speed test ran, and the API answered at 13:29:42.
- 13:30:33: ping/telnet/FTP still answered but **port 80 (cardv's httpd) was closed** → cardv had stopped.
- 13:31:31: camera gone. It came back as the `INNOVV_K7` AP; at 13:34 `uptime` = 2 min (booted ~13:32),
  load average 39 right after boot, recording running again (`2001`=1).
- `/mnt/pstore` holds only `SYSP` (cardv's settings blob: fw version, AP SSID…) written **13:31** — no kernel
  oops/panic. So this looks like an **orderly reboot initiated by cardv** (it saves SYSP, then reboots),
  not a kernel crash. (`results/pstore_dump_2026-11-21.txt`)
- Hypothesis: cardv supervises its WiFi/AP; when its AP disappears because we took over the radio behind its
  back, it eventually restarts the system. 3014 dump earlier showed `3007` (auto power-off) = 0, so a plain
  auto-off timer is less likely. → **Persistence must cooperate with cardv** (e.g. drive cardv's own STA path,
  which calls `up.sh sta` → `wpa_supplicant`), not fight it. Next: find cardv's reboot trigger in the firmware.

## 17. Why did the camera reboot after ~4–5 min in STA? (investigation, 2026-11-21)
Facts from the live camera (AP mode, after the reboot):
- **No hardware watchdog**: no `/dev/watchdog*`, cardv has no watchdog fd open. cardv's stdout/stderr go
  to `/dev/console` (serial ttyS0 — not visible over the network). cardv holds `/dev/ttyS1` + `/dev/ttyS3`
  (most likely the power/ACC MCU: strings "ACC ON restart!!!", "MCU_PACKER_POWEROFF_STATUS").
- cardv is started by `/etc/init.d/S99_Sysctl` as a plain `cardv &` — **no supervisor/respawn**. A cardv
  crash alone would not reboot the box. So the reboot was most likely an **orderly shutdown flow run by
  cardv** (strings: "System Shutdown begin/end", "Shutdown flow + S/W Reset / H/W Reset / Power Off") —
  consistent with httpd (port 80) going away first and `SYSP` being written at 13:31.
- Kernel runs with `user_debug=0xff` (user faults would be printed to dmesg), but dmesg is lost on reboot.
- Avoid reading `/proc/nvt_info/memperf` / `drvdump` — they trigger actions (a blind read hung the shell).

**~~Leading hypothesis — the user's own garage automation~~ — RULED OUT by the user:** the relay logic is overridden locally (not on GitHub) and the camera was powered/awake throughout. Kept below for the record:
the openHAB rule `vehicle-motorcycle-k7-power.js` has `NO_BLE_MAX_ON_MIN = 5` ("shorter relay timeout when
BLE/Pi4 unavailable — dump can't complete"), `RELAY_ABS_MAX_MIN = 30` and a 5-min relay watchdog. If the camera
is woken through the garage relay (K7 ignition line) during these tests, openHAB opens the relay after its
timeout (the Pi can't see INNOVV_K7 while the camera is in STA, so no dump completes) → ignition low → cardv
runs its orderly ACC-off shutdown → later the relay closes again → camera boots as AP and starts recording.
Matches: ~4–5 min STA lifetime, orderly shutdown, no crash, no watchdog. It may also explain the other
"reboots" seen today, and if the Pi dumps whenever the AP is up it would compete with the Mac's AP-mode tests.

To confirm: Shelly relay history / openHAB log around 13:30–13:31 (relay OFF → ON), and whether the Pi
dump service touched the camera. If confirmed: for tests, keep the relay ON manually / pause the openHAB
rule; for the STA migration, the openHAB state machine must learn that the camera lives on <iot-subnet>.x.
