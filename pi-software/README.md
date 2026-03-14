# INNOVV K7 Auto-Dump — Pi 4 Software

Automated footage download from INNOVV K7 dual-channel dashcam to NAS via WiFi.

## Architecture

```
[K7 Dashcam]  ←WiFi 5GHz→  [Pi 4 wlan0]  ←Ethernet→  [OpenHAB + NAS]
   (bike)                   (garage)           (house)
```

The Pi 4 uses **dual networking**:
- **wlan0** — Connects to K7's WiFi hotspot (5 GHz, ch 36) for HTTP downloads
- **eth0** — Home LAN (Ethernet) for NAS writes and OpenHAB API calls

## How It Works

1. K7 powers on when bike ignition starts (always recording)
2. Pi service detects K7 SSID `INNOVV_K7` via `iw scan`
3. Pi connects wlan0, sends heartbeat, recursively lists all folders
4. New files downloaded via HTTP to NAS mount (over eth0)
5. Each file: SHA-256 during download → fsync → NAS read-back verification
6. Only after 100% verification: file deleted from K7 SD card
7. Pi reports progress to OpenHAB via REST API (live counters)
8. On completion or K7 power-off: disconnects WiFi, resumes on next detection

## K7 Camera Details

| Property | Value |
|----------|-------|
| Chipset | Novatek NA51055 |
| WiFi | RTL8821CS, 5 GHz ch 36, WPA2-PSK CCMP |
| SSID | `INNOVV_K7` |
| Password | `12345678` |
| BSSID | `64:82:14:4C:BB:D8` |
| IP | `192.168.1.254` |
| HTTP port | 80 |
| API | Novatek CarDV (`/?custom=1&cmd=<CMD>`) |
| FTP | **NOT available** — HTTP only |
| Channels | Dual: Front (_F) + Rear (_R) |

### K7 Folder Structure

The K7 creates folders dynamically — empty folders are not visible via HTTP.

| K7 Folder | Content | NAS Subfolder | File Extensions |
|-----------|---------|---------------|-----------------|
| `Movie_E` | Continuous recordings | `Movie_E/` | MP4 (_F, _R) |
| `Photo_E` | Manual photo captures | `Photo_E/` | JPG (_F, _R) |
| `EMR_E` | Emergency/protected clips | `EMR_E/` | MP4 (_FK, _RK) |

Filename format: `YYYYMMDDHHMMSS_NNNNNN_<CAM>.<EXT>`
- `_F` = Front camera, `_R` = Rear camera
- `_FK` = Front emergency, `_RK` = Rear emergency

### K7 Clock & Time Sync

The K7 maintains its clock across power cycles (likely synced via the INNOVV
phone app or retained by a small internal capacitor). Filenames reflect the
K7's internal clock at time of recording. For best accuracy, periodically
open the INNOVV phone app which syncs the phone's clock to the K7.

The dump service extracts dates from filenames for NAS folder organization
(e.g., `2026-03-09/Movie_E/`).

### Loop Recording (Movie_E Auto-Delete)

**IMPORTANT:** The K7 uses loop recording for `Movie_E/`. When the SD card
fills up, the K7 automatically deletes the **oldest** Movie_E files to make
room for new recordings. This means Movie_E only contains recent footage.

`Photo_E/` and `EMR_E/` are **never auto-deleted** by the K7 — they persist
until the user removes them manually (or our dump service deletes them after
verified transfer to NAS).

### K7 API Commands

| Command | Purpose | Status |
|---------|---------|--------|
| 3012 | Heartbeat | Working |
| 3015 | File listing (XML) | Returns -21 (empty), fall back to HTML |
| 3016 | Firmware version | Working |
| 3019 | SD card status | Untested |
| 4003 | Disk free | Untested |

The XML file listing API (cmd=3015) returns `Status=-21` and no file elements.
The service uses HTML directory listing as primary method — recursive parsing
of the K7's built-in web server directory pages.

## Files

| File | Purpose |
|------|---------|
| `innovv_k7_dump.py` | Main service — orchestrates the dump cycle |
| `wifi_manager.py` | WiFi connection management (wpa_supplicant) |
| `k7_api.py` | K7 HTTP API + HTTP directory listing + download + delete |
| `openhab_client.py` | OpenHAB REST API client for status reporting |
| `config.json` | All configuration (WiFi, paths, OpenHAB URL) |
| `backup-sd.sh` | Monthly Pi SD card backup to NAS (cron) |
| `innovv-k7-dump.service` | systemd service unit |
| `install.sh` | Automated setup script |

## Pi Hardware & WiFi

### Critical WiFi Firmware Requirement

The Pi 4's BCM43455 WiFi chip **MUST use minimal firmware** (7.45.241) to
connect to the K7's RTL8821CS access point. The standard Cypress firmware
causes `ASSOC_REJECT` errors.

```bash
# Set minimal firmware (ALREADY CONFIGURED — survives reboot)
sudo update-alternatives --set cyfmac43455-sdio.bin \
  /lib/firmware/cypress/cyfmac43455-sdio-minimal.bin
```

**Verified:** Signal strength -31 dBm (Excellent), throughput ~2.9-4.5 MB/s.

### Pi Network Setup

| Interface | Purpose | IP | Config |
|-----------|---------|-----|--------|
| eth0 | Home LAN (NAS, OpenHAB) | DHCP (10.0.5.60) | Default route |
| wlan0 | K7 WiFi (downloads) | Static 192.168.1.100/24 | No default route |

wlan0 is isolated from dhcpcd via `denyinterfaces wlan0` to prevent
routing conflicts. The dump service manages wlan0 directly via wpa_supplicant.

## Verified Transfer Pipeline

Every file goes through this pipeline before deletion from K7:

```
1. HTTP download → stream to .partial temp file on NAS
2. SHA-256 computed during download (in-memory)
3. fsync() flushes data to NAS disk
4. Atomic rename: .partial → final filename
5. NAS read-back: re-read entire file, re-compute SHA-256
6. SHA-256 comparison: download hash == read-back hash
7. Media header check: MP4 ftyp magic / JPEG FF D8 FF magic
8. Only if ALL checks pass → mark verified in SQLite DB
9. Only verified files are deleted from K7
10. Deletion recorded in DB (deleted_from_k7 = 1)
```

**Safety guarantees:**
- Unverified files are NEVER deleted from K7
- Failed verifications trigger re-download on next cycle
- Pending deletes retry automatically on reconnect
- Manual NAS deletions detected and re-downloaded (if still on K7)
- 3 consecutive failures → abort cycle (K7 likely offline)

## NAS Folder Structure

```
/mnt/nas/dashcam/
├── 2026-03-09/
│   └── Movie_E/
│       ├── 20260309221222_000290_R.MP4
│       └── 20260309221222_000289_F.MP4
├── 2026-03-10/
│   ├── Movie_E/
│   │   └── ...MP4 files...
│   ├── Photo_E/
│   │   ├── 20260310000324_000319_R.JPG
│   │   └── 20260310000324_000318_F.JPG
│   └── EMR_E/
│       ├── 20260310000312_000311_RK.MP4
│       └── 20260310000312_000310_FK.MP4
└── pi-backups/
    └── k7-bridge-sd-2026-03-01.img.gz
```

## OpenHAB Integration

### Items (items/innovv_k7.items)

```openhab
Group       gK7                     "INNOVV K7 Dashcam"                         <k7-cam-blue>

// --- Status ---
String      K7_Dump_Status          "Dump Status [%s]"                          <k7-sync-blue>      (gK7)
Switch      K7_Camera_Online        "K7 Online [MAP(k7_onoff.map):%s]"         <k7-online-green>   (gK7)
DateTime    K7_Last_Dump            "Last Dump [%1$td.%1$tm.%1$tY %1$tH:%1$tM]" <k7-time-blue>    (gK7)
String      K7_Last_Error           "Last Error [%s]"                           <k7-alert-red>      (gK7)

// --- WiFi connection ---
String      K7_WiFi_Signal          "K7 WiFi Signal [%s]"                       <k7-wifi-cyan>      (gK7)
String      K7_WiFi_Band            "K7 WiFi Band [%s]"                         <k7-band-teal>      (gK7)

// --- Download progress ---
Number      K7_Files_On_Camera      "Files on Camera [%d]"                      <k7-sdcard-orange>  (gK7)
Number      K7_Files_Downloaded     "Files Downloaded [%d]"                     <k7-download-green> (gK7)
Number      K7_MB_Downloaded        "MB Downloaded [%.1f MB]"                   <k7-data-blue>      (gK7)

// --- Verified transfers ---
Number      K7_Files_Verified       "Files Verified on NAS [%d]"                <k7-verified-green> (gK7)
Number      K7_Files_Deleted        "Files Deleted from K7 [%d]"                <k7-delete-red>     (gK7)
Number      K7_Pending_Deletes      "Pending K7 Deletes [%d]"                   <k7-pending-orange> (gK7)

// --- Settings ---
Switch      K7_Dump_Movie_E         "Dump Movie_E (Loop Video) [MAP(k7_onoff.map):%s]" <k7-movie-orange> (gK7)

// --- Pi health ---
Number      K7_Pi_Disk_Free_MB      "Pi SD Free [%d MB]"                        <k7-disk-cyan>      (gK7)
```

### Live Updates

The dump service pushes updates to OpenHAB throughout the cycle:
- **Dump status**: idle → k7_detected → connected → scanning → dumping (n/total) → complete
- **WiFi signal**: Excellent/Good/Fair/Weak with dBm value (updated every 10 files)
- **WiFi band**: Auto-detected frequency (e.g. "5 GHz" or "2.4 GHz")
- **Files/MB downloaded**: Updated after every file
- **Verified/Deleted/Pending**: Updated after every file's delete operation
- **Pi disk free**: Reported at start of each dump cycle, warns if < 500MB

### Movie_E Toggle (K7_Dump_Movie_E)

The `K7_Dump_Movie_E` switch controls whether continuous loop recordings
(`Movie_E/`) are downloaded. When OFF, only `Photo_E/` and `EMR_E/` files
are transferred.

This toggle is **re-checked during the download loop** — you can turn it OFF
mid-cycle and it takes effect before the next Movie_E file. The log shows:
```
Movie_E disabled mid-cycle — skipping 237 remaining loop video files
```

Defaults to ON (download everything) if the OpenHAB item is uninitialized.

## Monthly Pi SD Backup

A cron job creates a compressed full SD card image on the NAS every month:

```
Schedule: 1st of month at 03:00 (cron: 0 3 1 * *)
Location: /mnt/nas/dashcam/pi-backups/k7-bridge-sd-YYYY-MM-DD.img.gz
Rotation: Keeps 3 most recent, deletes older
Size:     ~1 GB compressed (15 GB SD, 3 GB used)
```

**Important:** The backup script stops the dump service during backup to
avoid CPU/IO starvation (gzip + dd saturates the Pi 4). The service is
automatically restarted after backup completes (even on failure via trap).

Nothing is written to the Pi's SD card — `dd | gzip` pipes directly to NAS.

To restore: `gunzip -c backup.img.gz | dd of=/dev/sdX bs=4M`

## Persistence (Survives Reboot)

| Component | How | Location |
|-----------|-----|----------|
| Dump service | systemd enabled | `/etc/systemd/system/innovv-k7-dump.service` |
| NAS mount | fstab nofail | `//Rackstation2.agesen.dk/...` → `/mnt/nas/dashcam` |
| NAS credentials | file | `/root/.nas-creds` (mode 0600) |
| Download DB | SQLite | `/opt/innovv-k7/downloaded_files.db` |
| Config | JSON | `/opt/innovv-k7/config.json` |
| WiFi firmware | update-alternatives | `cyfmac43455-sdio-minimal.bin` |
| wlan0 isolation | dhcpcd.conf | `denyinterfaces wlan0` |
| Backup cron | root crontab | `0 3 1 * * /opt/innovv-k7/backup-sd.sh` |
| Python venv | on disk | `/opt/innovv-k7/.venv/` |

## Configuration (config.json)

```json
{
    "k7_wifi": {
        "ssid": "INNOVV_K7",
        "password": "12345678",
        "interface": "wlan0",
        "camera_ip": "192.168.1.254",
        "static_ip": "192.168.1.100/24",
        "country": "DK",
        "connect_timeout_sec": 30,
        "scan_interval_sec": 30
    },
    "k7_api": {
        "http_port": 80,
        "heartbeat_interval_sec": 10
    },
    "download": {
        "remote_path": "/INNOVVK7",
        "nas_mount_path": "/mnt/nas/dashcam",
        "organize_by_date": true,
        "delete_after_verified_download": true
    },
    "openhab": {
        "url": "http://10.0.5.21:8080"
    },
    "safety": {
        "max_dump_duration_min": 30,
        "max_total_download_gb": 50,
        "min_nas_free_space_gb": 10
    },
    "database": {
        "path": "/opt/innovv-k7/downloaded_files.db"
    },
    "logging": {
        "level": "INFO",
        "file": "/var/log/innovv-k7-dump.log",
        "max_bytes": 5242880,
        "backup_count": 3
    }
}
```

Key settings:
- `remote_path: "/INNOVVK7"` — Scans ALL K7 folders (Movie_E, Photo_E, EMR_E, etc.)
- `delete_after_verified_download: true` — Deletes from K7 only after SHA-256 verified
- `organize_by_date: true` — Creates `YYYY-MM-DD/<folder>/` on NAS
- `static_ip` — Pi IP on K7 subnet (CIDR notation — subnet auto-derived)
- `country` — 2-letter country code for wpa_supplicant regulatory domain

## Monitoring

```bash
# Service status
sudo systemctl status innovv-k7-dump

# Live logs
tail -f /var/log/innovv-k7-dump.log

# Database stats
sqlite3 /opt/innovv-k7/downloaded_files.db \
  "SELECT COUNT(*) as total, SUM(verified) as verified, SUM(deleted_from_k7) as deleted FROM downloaded_files;"

# Backup log
cat /var/log/innovv-k7-backup.log
```

## Safety Features

- **SHA-256 verified transfers** — download hash + NAS read-back hash must match
- **Media header validation** — MP4 ftyp / JPEG FF D8 FF magic bytes checked
- **fsync + atomic rename** — .partial temp files prevent corrupt NAS entries
- **NAS integrity check** — detects manually deleted NAS files, re-downloads from K7
- **Pending delete retry** — failed K7 deletions retried on next cycle
- **3-failure abort** — stops if K7 goes offline mid-cycle
- **30-minute timeout** — prevents runaway sessions
- **NAS space check** — stops if < 10 GB free (checked every 10 files)
- **Pi disk monitoring** — warns OpenHAB if Pi SD < 500 MB free
- **SQLite tracking** — never re-downloads verified files
- **WiFi isolation** — wlan0 has no default route, cannot disrupt eth0
- **Graceful shutdown** — SIGTERM cancels in-progress downloads within milliseconds
  (partial files remain as `.partial` for resume next cycle)
- **Incomplete download protection** — completeness check runs BEFORE rename;
  truncated files stay as `.partial` and are never mistaken for valid downloads

## TODO / Future Work

- [x] ~~**Shelly Plus Uni relay integration**~~ — **DONE** (v1 2026-03-11). Auto power-on via dual-sensor charger detection (BLE + voltage), auto power-off after dump complete. See [K7_AUTO_POWER_README.md](../docs/K7_AUTO_POWER_README.md).
- [ ] **K7 time sync** — Verify clock stays accurate; consider syncing from Pi if drift detected
- [ ] **Parking mode** — Handle parking/accident folder types when they appear
- [ ] **Retention policy** — Auto-delete old footage from NAS after N days
- [ ] **Notifications** — Push alert on dump errors or K7 offline for extended period

## Troubleshooting

**K7 WiFi not visible:**
- Is the K7 powered on? K7 broadcasts WiFi whenever powered
- K7 boots in ~20 seconds, WiFi may need 30s
- Run: `sudo iw wlan0 scan | grep -i innovv`
- Verify minimal firmware: `ls -la /etc/alternatives/cyfmac43455-sdio.bin`

**ASSOC_REJECT on WiFi connect:**
- BCM43455 needs minimal firmware (7.45.241)
- `sudo update-alternatives --set cyfmac43455-sdio.bin /lib/firmware/cypress/cyfmac43455-sdio-minimal.bin`
- Reboot Pi after firmware change

**Downloads fail with Connection refused:**
- K7 HTTP server may be busy (especially after bulk deletes)
- Service auto-retries after 30-second WiFi reconnect cycle
- 3 consecutive failures trigger abort (K7 likely powered off)

**NAS mount not accessible:**
- Check: `mount | grep dashcam`
- Remount: `sudo mount /mnt/nas/dashcam`
- Verify credentials: `cat /root/.nas-creds`

**Movie_E only has recent files:**
- This is normal — K7 loop recording auto-deletes oldest Movie_E files when SD is full
- Photo_E and EMR_E are never auto-deleted by K7
- To keep more Movie_E history, use a larger SD card

**Pi unresponsive during backup:**
- Monthly backup uses dd + gzip which saturates CPU/IO
- Backup runs at 03:00 on 1st of month, takes ~10-15 minutes
- Dump service is stopped during backup and auto-restarted after
