# Station mode for the INNOV K7 (research branch)

This branch explores running the K7 as a **WiFi client on the home network** ("station
mode") instead of only as its own access point with a Raspberry Pi bridging to it.

`main` is the stable Pi solution and is **not touched** by this branch. If the decision is to
stay on the Pi, this branch is simply left as a record.

## Status (2026-11-21)

- ✅ **Station mode works**, using a self-built `wpa_supplicant` with the **nl80211** driver
  interface. The camera joined the home SSID within ~25 s and got a DHCP lease.
- ✅ **Throughput** from a wired client through the router, using the Pi's own method
  (HTTP GET + `cmd=3012` heartbeat, recording stopped with `cmd=2001&par=0`):
  **1.52 MB/s**, a complete 106 MB clip in 66.5 s, no crash.
- ⚠️ **Sessions end on their own** after roughly 4–12 minutes: cardv performs an orderly
  shutdown and the camera comes back as its AP. No hardware watchdog is involved and the
  ignition stayed on. The cause is being investigated by capturing cardv's console
  output with `tools/consnoop.c` (FINDINGS §16–§17).
- ⬜ **Not persistent yet.** After a power cycle the camera is back in AP mode. The rootfs is
  read-only and 100 % full, so a boot hook is still the open problem (FINDINGS §8).

## Key findings

| Topic | Finding |
|---|---|
| Why the app has no "join network" option | The firmware ships **without `wpa_supplicant`**. `up.sh sta` calls a binary that does not exist. |
| wext vs nl80211 | The rtl8821cs v5.x driver rejects the wext `SIOCSIWAP` ioctl and never emits the wext association event, so a wext-only supplicant stops at ASSOCIATING. **nl80211 is required**, the same interface the camera's own `hostapd` uses. |
| Transfer method | HTTP GET from `http://<camera>/INNOVVK7/Movie_E/<file>` with a heartbeat every ~10 s (as `pi-software/k7_api.py` does). The busybox FTP server on port 21 is slow and unreliable for bulk data. |
| Slow tests from a Mac on the camera AP | ~13–36 KB/s. That was the Mac's WiFi link to the camera, not the camera: through the router the camera delivers ~1.5 MB/s. |
| A wrong turn | A DHCP lease named `k7-bridge` was first taken as proof that station mode worked. It was the Pi. Corrected in FINDINGS §13. |

The full, dated log of everything tried, including the dead ends, is in
[`FINDINGS.md`](FINDINGS.md).

## Layout

```
station-mode/
  README.md            this file
  FINDINGS.md          full technical log (firmware, API, filesystems, all STA attempts)
  PROCEDURE.md         first install/backup/rollback procedure (historical; see scripts/)
  build/
    build-wpa_supplicant-nl80211.sh   static wpa_supplicant 2.10 + libnl 3.9 for ARMv7 (use this)
    build-wpa_supplicant.sh           first wext-only build (does NOT work on this driver)
    wpa_supplicant_nl80211.config     the .config used
  config/
    wpa_supplicant.conf.example       5 GHz-preferred network block (fill in SSID/PSK)
    wpa_supplicant_any.conf.example   any-band variant
  scripts/
    wifi_sta_stay.sh   v4: switch to STA via nl80211 and stay; COMPLETED-only check,
                       90 s give-up + 180 s no-IP watchdog that reboots back to the AP
    sta_diag.sh        wrapper: consnoop console capture + 5 s monitor, then wifi_sta_stay.sh
    sta_test.sh        early self-reverting test (logs, then reboots)
    wifi_sta.sh, S99_wifi_sta   early manual/boot helpers
  tools/
    consnoop.c         capture user-space /dev/console output (cardv's logs) via TIOCCONS
    oneshot.py         run one command over telnet (CAMHOST env, default 192.168.1.254)
    session.py, cam.py, push_robust.py   other telnet helpers used along the way
    http_speedtest.py  HTTP + heartbeat download benchmark (CAM env)
    ubifs_extract.py   pure-Python UBI/UBIFS extractor used to unpack the firmware
```

Not included on purpose: the vendor firmware, the unpacked rootfs (both copyrighted), the
compiled binaries (rebuild them with `build/`), and the raw logs, which contain home-network
details.

## Reproduce

1. Build on macOS: `brew install messense/macos-cross-toolchains/armv7-unknown-linux-musleabihf`,
   then run `build/build-wpa_supplicant-nl80211.sh`. Compile `tools/consnoop.c` with
   `armv7-unknown-linux-musleabihf-gcc -Os -static -o consnoop consnoop.c`.
2. Copy `config/wpa_supplicant.conf.example` to `wpa_supplicant.conf` and fill in the SSID and PSK.
3. With a client on the camera AP (`INNOVV_K7`, camera at 192.168.1.254), upload
   `wpa_supplicant_nl80211`, `wpa_supplicant.conf`, `scripts/wifi_sta_stay.sh` (and for diagnosis
   `consnoop` + `scripts/sta_diag.sh`) to the SD card over FTP (root, empty password).
4. Start it over telnet: `nohup sh /mnt/sd/wifi_sta_stay.sh >/dev/null 2>&1 &`
5. Find the camera in the router's DHCP list. API: `http://<camera>/?custom=1&cmd=3012`.

## Safety

Nothing here changes the firmware or the read-only rootfs. Files live on the SD card and in
`/mnt/app/wifi`. A power cycle always brings the camera back as the stock AP with recording on.
The scripts reboot the camera back to its AP if it cannot associate or get an IP.
