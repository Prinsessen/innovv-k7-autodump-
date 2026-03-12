# INNOVV K7 — Firmware Analysis & Auto-Dump Project

**Firmware:** IK7.20250317.V05 (`IN-K7-1.bin`, 28MB)  
**Analyzed:** 2026-03-09  
**Purpose:** Map WiFi/network interfaces for automatic footage dump to NAS

---

## Table of Contents

1. [Hardware / SoC](#hardware--soc)
2. [Dual Camera Architecture](#dual-camera-architecture)
3. [WiFi Access Point Configuration](#wifi-access-point-configuration)
4. [Network Services](#network-services)
5. [HTTP API (Novatek CarDV Protocol)](#http-api-novatek-cardv-protocol)
6. [RTSP Live Streaming](#rtsp-live-streaming)
7. [FTP Access](#ftp-access)
8. [SD Card File Structure](#sd-card-file-structure)
9. [Connectivity Challenge](#connectivity-challenge)
10. [Power Solution — Victron Charger + Shelly Plus Uni Relay](#power-solution--victron-charger--shelly-plus-uni-relay)
11. [System Architecture](#system-architecture)
12. [Download Strategy](#download-strategy)
13. [Full Automation Flow](#full-automation-flow)
14. [Hardware Shopping List](#hardware-shopping-list)
15. [Power Analysis & Battery Safety](#power-analysis--battery-safety)
16. [Known Pitfalls & Caveats](#known-pitfalls--caveats)
17. [Implementation Steps](#implementation-steps)
18. [Pi Software](#pi-software)
19. [References](#references)

---

## Hardware / SoC

| Component | Detail |
|-----------|--------|
| **SoC** | Novatek NA51055 (ARM, Linux 4.19.91) |
| **WiFi chip** | Realtek RTL8821CS (5GHz capable, driver v5.14.2) |
| **SDK** | `na51055_linux_sdk` (Novatek CarDV platform) |
| **BusyBox** | v1.24.1 |
| **Build source** | `/home/zhengxingjie/2023/IK7/580_p` |

## Dual Camera Architecture

The K7 is a **dual-channel** system with front+rear cameras:
- **Front camera** (wired to DVR via ethernet cable)
- **Rear camera** via `ETHCAM` (Ethernet Camera protocol)
- Firmware contains `EthCamTxFW1.bin` for camera firmware updates over the link
- Only the DVR unit exposes WiFi AP — cameras connect via cable

## WiFi Access Point Configuration

| Setting | Value |
|---------|-------|
| **Daemon** | hostapd v2.7 (Realtek fork `devel_rtw-17-g894b400ab`) |
| **Interface** | `wlan0` |
| **Config file** | `/etc/wifiap_wpa2.conf` |
| **Camera IP** | `192.168.1.254` (acts as gateway) |
| **DHCP server** | `udhcpd` (assigns IPs to connected clients) |
| **Client IP range** | `192.168.1.20` – `192.168.1.x` |
| **WPA2** | Yes (WPA2-PSK) |
| **Template SSID** | `MYSSID` / `680apwpa2` (configured via INNOVV app) |
| **Template PSK** | `myssidpwd` (template — real password is set by user) |
| **Channel** | Default `3` (2.4GHz, configurable) |

**Important:** The actual SSID and password are configured by the user through the INNOVV mobile app (`cn.rxt.case.innovv`). Check the app or look at the WiFi network broadcast when the K7 is powered on.

### WiFi Band & Transfer Speed

The RTL8821CS is a **dual-band** chip (2.4 GHz + 5 GHz 802.11ac). The firmware
template uses `channel=3` (2.4 GHz), but the band may be configurable via the
INOVV app or hidden settings.

| Band | Standard | RTL8821CS 1×1 Link | Realistic FTP | 256 GB full dump |
|------|----------|-------------------|---------------|------------------|
| 2.4 GHz | 802.11n | 150 Mbps | ~30–50 Mbps | 10–15 min |
| 5 GHz | 802.11ac | 433 Mbps | ~100–200 Mbps | 2–5 min |

The Pi 4's BCM43455 also supports both bands — both sides can do 5 GHz.

At garage range (1–3 meters), 5 GHz has zero disadvantage (shorter range doesn't
matter). **If configurable, use 5 GHz for fastest dumps.**

**To check when powered on:**
1. Connect phone, open a WiFi analyzer app — shows exact channel/band instantly
2. Check the INNOVV app for a band or channel setting
3. If stuck on 2.4 GHz with no option, it's still fine — 15 min for a full dump
   is acceptable for an automated overnight process

## Network Services

| Service | Port | Notes |
|---------|------|-------|
| **httpd** | 80 (standard) | Novatek CarDV HTTP API server |
| **nvtrtspd** | 554 (standard) | RTSP live streaming daemon |
| **ftpd** | 21 (standard) | FTP file access to SD card |
| **tftpd** | 69 (UDP) | `udpsvd -vE 0 69 tftpd /mnt/sd/` |
| **telnetd** | 23 (standard) | Debug/shell access (may be disabled) |
| **lviewd** | Unknown | LiveView daemon (video preview) |
| **msdcnvt** | N/A | Mass Storage Device Controller (USB mode) |

## HTTP API (Novatek CarDV Protocol)

The K7 uses the **standard Novatek CarDV WiFi HTTP API**:

```
http://192.168.1.254/?custom=1&cmd=<CMD_ID>[&par=<PARAM>]
```

Responses are XML:
```xml
<?xml version="1.0" encoding="UTF-8" ?>
<Function>
  <Cmd>CMD_ID</Cmd>
  <Status>0</Status>
  <!-- additional data -->
</Function>
```

### Known Novatek CarDV Commands

These are the standard SDK commands (NA51055). The K7 may support a subset:

#### Recording Control
| CMD | Description | Parameters |
|-----|-------------|------------|
| 2001 | Start/stop video recording | `par=1` start, `par=0` stop |
| 1001 | Capture photo | |
| 3001 | Get recording state | |
| 3004 | Get current mode | Returns: 0=video, 1=photo |

#### File Operations (Most Important for Footage Dump)
| CMD | Description | Parameters |
|-----|-------------|------------|
| 3015 | **Get file listing** | Returns XML with file paths, sizes |
| 3014 | Get file thumbnail | `par=<filepath>` |
| 4001 | **Get all files info** | Alternative file listing |
| 4003 | Get disk free space | |

#### System
| CMD | Description | Parameters |
|-----|-------------|------------|
| 3012 | **Heartbeat / keep-alive** | Must send periodically! |
| 3016 | Get firmware version | |
| 3019 | Get SD card status | |
| 3024 | Format SD card | ⚠️ Destructive |

#### WiFi Configuration (via API)
| CMD | Description | Parameters |
|-----|-------------|------------|
| 2010 | Set WiFi SSID | `str=<new_ssid>` |
| 2011 | Get WiFi SSID | |
| 2012 | Set WiFi password | `str=<new_password>` |

### File Download

Files on the SD card can be downloaded via **direct HTTP GET**:
```
http://192.168.1.254/<filepath_from_listing>
```

Example:
```
http://192.168.1.254/DCIM/MOVIE/2026_0607_120000_001.MP4
```

The file listing command (3015) returns the exact paths to use for download.

## RTSP Live Streaming

```
rtsp://192.168.1.254/live
```

- **Codecs:** H.264 (primary), H.265 (alternative)
- **Transport:** MPEG-2 Transport Stream
- **Daemon:** `nvtrtspd`
- **LiveView:** Separate daemon `lviewd` for app preview

This can be used for:
- Live viewing in VLC/ffmpeg
- NVR integration (e.g., Synology Surveillance Station, Blue Iris)
- Snapshot capture

## FTP Access

Standard FTP access to SD card contents:
```
ftp://192.168.1.254/
```

The SD card is mounted at `/mnt/sd/` with standard DCIM structure:
```
/mnt/sd/
├── DCIM/
│   ├── MOVIE/      (video recordings)
│   └── PHOTO/      (photo captures)
└── ...
```

FTP may be the **most reliable method** for bulk file download as it:
- Doesn't require the Novatek API
- Supports resume on interruption
- Can list directories natively
- Works with standard tools (`lftp`, `wget`, `curl`)

## SD Card File Structure

| Path | Content |
|------|---------|
| `A:\DCIM\` | Main media storage (DCIM standard) |
| `A:\MSDCHP` | Mass Storage Device Controller config |
| `A:\test.htm` | Test/diagnostic page |
| `A:\WIFI_TEST.txt` | WiFi test mode flag file |
| `A:\EthcamTxFW1.bin` | Ethernet camera firmware for rear cam |
| `A:\query.*` | Query/config files |

---

## Connectivity Challenge

The OpenHAB server (`OpenHab5`, 10.0.5.21) is a **Hyper-V VM** with only a virtual
`eth0` interface — no WiFi hardware, no USB passthrough. It **cannot** connect
directly to the K7's WiFi hotspot.

The K7 runs in **AP mode only** (hardcoded hostapd). It creates its own isolated
WiFi network (192.168.1.x). It cannot join the home Fortinet network — it is
always the hotspot, never a client.

Additionally, the K7 is **12V hardwired** to the bike's ignition circuit and
shuts down **1-2 minutes after ignition off**. This is not enough time to dump
footage (a single 3-minute 1080p dual-channel clip can be 500MB–1GB).

These three constraints shape the entire solution design.

---

## Power Solution — Victron Charger + Shelly Plus Uni Relay

The bike already has a **Victron Blue Smart IP65 12V/10A** battery maintainer
attached when parked at home. This keeps the battery fully charged indefinitely.

The K7 is 12V hardwired to the **ignition circuit** (only live when key is ON).
To keep the K7 alive for footage dump, a **Shelly Plus Uni** drives an
**automotive relay** that bypasses the ignition switch, feeding the K7 directly
from the battery.

### Why Shelly Plus Uni (not Shelly 1 Gen3)

The Shelly 1 Gen3 is rated 12 VDC ±10% (10.8–13.2V) — but a running motorcycle
puts 13.5–14.8V at the battery terminals during charging. **Out of spec.**

The **Shelly Plus Uni** accepts **9–28 VDC** — covers the full motorcycle range
natively with no buck converter needed:

| State | Battery Voltage | Shelly Plus Uni (9–28V) | Shelly 1 Gen3 (10.8–13.2V) |
|-------|----------------|------------------------|----------------------------|
| Engine off, resting | 12.4 – 12.8V | ✅ OK | ✅ OK |
| Engine running, charging | 13.5 – 14.8V | ✅ OK | ❌ Over-voltage |
| Cold cranking dip | 9.5 – 10.5V | ✅ OK (≥9V) | ❌ Under-voltage |

Additional advantages:

- **Pre-wired colored leads** — no screw terminals, vibration-proof on a motorcycle
- **Built-in voltmeter** (0–30V) — can report battery voltage to OpenHAB
- **PCB coating film** — some weather resistance built in
- **Tiny** — 40×21×7mm, 8.5g
- **Shelly binding** — native HTTP integration with OpenHAB (no MQTT)

### Automotive Relay (Required)

The Plus Uni's solid-state outputs are rated **300 mA max** — the K7 draws
~500–800 mA when recording. Solution: the 300 mA output drives an automotive
relay coil (~150 mA), which switches the K7's full current:

| Component | Current | Rating |
|-----------|---------|--------|
| Shelly Plus Uni OUT1 | ~150 mA (relay coil) | 300 mA max ✅ |
| Automotive relay contact | ~500–800 mA (K7) | 10A max ✅ |

Recommended relay: **SRA-12VDC-CL** (SPDT, 12V coil, 20A contacts, ~10 DKK).
Designed for automotive vibration. Add a flyback diode (1N4007) across the coil
if the relay module doesn't include one.

### Wiring Diagram

```
                   Ignition switch
Battery + ─────────────┤ON├──────────── K7 power (normal riding)
   │
   ├── Shelly Plus Uni (VAC1=+12V, VAC2=GND, power supply)
   │     │
   │     OUT1 ──→ Automotive relay coil (+)
   │                    │
   │              Relay coil (-) ──→ GND
   │
   └── Automotive relay COM ──→ NO ──→ K7 power (garage dump mode)
                                       (same wire, downstream of
                                        ignition switch)

Victron IP65 ═══ Battery (always maintaining)
```

### How It Works

- **Riding:** Ignition ON powers K7 normally. Shelly Plus Uni OUT1 is OFF (irrelevant)
- **Parked at home:** Ignition OFF → K7 dies in 1-2 min → but then:
  - Traccar detects bike at home → OpenHAB turns ON Shelly Plus Uni OUT1
  - OUT1 energizes automotive relay → K7 connects directly to battery
  - K7 powers back up, WiFi AP comes alive
  - Victron charger keeps battery topped up → unlimited dump time
- **Dump complete:** OpenHAB turns OFF OUT1 → relay drops → K7 shuts down

No buck converter, no voltage regulation, no screw terminals. The Plus Uni's
pre-wired leads are soldered/crimped for permanent vibration-proof connections.

### Physical Installation

1. Identify the K7 power wire between ignition switch and K7 DVR unit
2. Mount Shelly Plus Uni + automotive relay near the battery/DVR
3. Wire: battery → fuse → Shelly Plus Uni power (VAC1/VAC2) + relay COM
4. Wire: Shelly OUT1 → relay coil; relay NO → K7 power wire (downstream of ignition)
5. Shelly connects to home WiFi for OpenHAB control (Shelly binding, HTTP)
6. Add an inline fuse (3A) on the battery tap for safety
7. Conformal coat the Plus Uni PCB + heat-shrink or IP65 project box

The user already attaches the Victron charger manually when parking — no change
to that routine needed.

---

## System Architecture

Since the OpenHAB VM has no WiFi, a **Raspberry Pi 4** in the garage acts
as the WiFi bridge and runs the dump script locally.

The Pi 4 has both **Gigabit Ethernet and built-in WiFi**, which is ideal:
- **Ethernet** → plugged into garage network switch → always on home LAN
- **WiFi (`wlan0`)** → dedicated exclusively for K7 hotspot connection

Both networks are available simultaneously — no WiFi juggling needed.

### Raspberry Pi 4 — OS & Setup

**OS:** Raspberry Pi OS Lite (64-bit) — headless, no desktop (Debian 12 Bookworm).

| Requirement | Status |
|---|---|
| Python 3.11+ | ✅ Included |
| NetworkManager (dual eth0+wlan0) | ✅ Default since Bookworm |
| systemd (dump service) | ✅ Included |
| FTP client (`ftplib` in Python stdlib) | ✅ Included |
| wpa_supplicant (K7 WiFi via NetworkManager) | ✅ Included |
| NFS/SMB client (NAS mount) | `apt install nfs-common` or `cifs-utils` |

**Why Lite:** No desktop GUI = ~1.5 GB RAM saved, smaller attack surface for a
headless appliance that only dumps files.

**Flash with Raspberry Pi Imager:**
- Image: Raspberry Pi OS Lite (64-bit)
- Pre-configure in Imager settings:
  - Hostname: `k7dump`
  - Enable SSH + set your public key
  - WiFi: **skip** (wlan0 is configured separately for K7)
  - Locale: `da_DK` / `Europe/Copenhagen`

After first boot, SSH in via eth0 and run `install.sh` from the `pi-software/`
package — it sets up the venv, systemd service, NAS mount, and NetworkManager
connection profile for the K7 hotspot.

```
                           Home LAN (10.0.5.x)
                    ┌──────────┬──────────────────┐
                    │          │                   │
              ┌─────┴──┐  ┌───┴────┐         ┌────┴─────┐
              │OpenHAB │  │Synology│         │ Fortinet │
              │  VM    │  │  NAS   │         │   AP     │
              │10.0.5.21│ │(storage)│         │ (garage) │
              └────────┘  └────────┘         └────┬─────┘
                                                   │ Ethernet
                                              ┌────┴─────┐
                                              │  Pi 4    │
                                              │ (garage) │
                                              │10.0.5.x  │
                                              └────┬─────┘
                                                   │ WiFi (wlan0)
                                                   │ dedicated to K7
                                         ┌────────┴────────┐
               K7 WiFi AP                │  INNOVV K7 DVR  │
               192.168.1.x               │  192.168.1.254  │
                                         │  httpd / ftpd   │
                                         └─────────────────┘
```

### Pi Zero W Role

### Pi 4 Role

The Pi connects to the **home LAN via Ethernet** (always connected to NAS and
OpenHAB). Its WiFi is dedicated to the K7:

1. WiFi scans for K7 SSID (every 30s when Shelly Plus Uni relay is ON)
2. K7 SSID detected → `wlan0` connects to K7 hotspot (192.168.1.x)
3. Downloads new footage via FTP/HTTP (K7 on WiFi, NAS on Ethernet — simultaneous)
4. Files go directly to NAS mount — no local buffering needed
5. Disconnects WiFi when done
6. Reports status to OpenHAB via REST API (over Ethernet — always available)

**Advantage of Pi 4 over Pi Zero:** Ethernet + WiFi means the Pi downloads
from K7 over WiFi and writes directly to NAS over Ethernet at the same time.
No buffering to local SD card needed. The Gigabit Ethernet is faster than
the WiFi link to K7, so the K7's WiFi speed is the only bottleneck.

### Communication

| Channel | Purpose |
|---------|---------|
| **Pi → OpenHAB REST API** | Pi reports status via Ethernet (always connected) |
| **OpenHAB → Shelly binding** | Controls Shelly Plus Uni relay (K7 power on/off) via HTTP |
| **Traccar binding → OpenHAB** | Bike arrival/departure triggers power sequence |
| **HTTP/FTP → K7** | Pi downloads footage from K7 over WiFi (wlan0) |
| **NFS/SMB → NAS** | Pi stores footage on Synology NAS over Ethernet (simultaneous) |

No MQTT broker needed — the Pi uses simple HTTP calls to the OpenHAB REST API
(`http://10.0.5.21:8080/rest/items/K7_Status`) to report dump status.

---

## Download Strategy

### Actual Implementation: HTTP-Only Download

> **Note:** The FTP strategy below was the original plan. In practice, FTP is
> not accessible on the K7 (port 21 refused). The production service uses
> **HTTP-only** downloads — see `pi-software/README.md` for the verified pipeline.

#### Production Step-by-step

1. **Heartbeat** — `GET http://192.168.1.254/?custom=1&cmd=3012`
2. **File listing** — HTML directory listing at `http://192.168.1.254/INNOVVK7/`
   (XML API cmd=3015 returns empty; HTML parsing used as fallback)
3. **Compare** — check against SQLite DB of already-downloaded/verified files
4. **Download new files** — HTTP GET with `.partial` temp file + SHA-256
5. **Verify** — SHA-256 during download + NAS read-back re-hash + media header check
6. **Delete from K7** — only after full verification
7. **Report to OpenHAB** — live status, counters, WiFi signal, errors

#### Original FTP Plan (Not Working)

The original plan was to use FTP for bulk download via `lftp`. FTP was expected
to be available based on firmware analysis (ftpd present in init scripts), but
in practice port 21 is not accessible. Kept here for reference:

```bash
lftp -c "open ftp://192.168.1.254; mirror --newer-than=<last_dump> /DCIM/ /mnt/nas/dashcam/"
```

---

## Full Automation Flow

```
1. Traccar: bike GPS enters home geofence
   └─► OpenHAB rule: "bike arrived home"
       └─► Turn ON Shelly Plus Uni OUT1 (automotive relay → K7 powered from battery)
       └─► Start 30-minute safety timer

2. K7 stays powered after ignition off (Shelly Plus Uni relay bypasses ignition, Victron maintains battery)

3. Pi 4: wlan0 scanning for K7 WiFi SSID (every 30s)
   └─► K7 SSID detected!
       └─► Connect wlan0 to K7 WiFi
       └─► REST API: postUpdate K7_Status = "connected"

4. Pi 4: run dump script
   └─► HTTP API: heartbeat (cmd=3012)
   └─► HTML directory listing: get file list (recursive)
   └─► Compare with SQLite download database
   └─► HTTP: download new files → verify SHA-256 → write to NAS (via Ethernet)
   └─► REST API: postUpdate K7_Status = "dumping (3/12)"

5. Pi 4: dump complete
   └─► Disconnect wlan0 from K7 WiFi
   └─► REST API: postUpdate K7_Status = "complete"
   └─► REST API: postUpdate K7_LastDump = "2026-03-09T18:30:00"
   └─► REST API: postUpdate K7_FilesDownloaded = 12
   └─► REST API: postUpdate K7_BytesDownloaded = "4.2GB"

6. OpenHAB rule: K7_Status changed to "complete"
   └─► Turn OFF Shelly Plus Uni OUT1 (relay drops → K7 powered down)
   └─► Log event / send notification
```

### Timeout Protection

If dump doesn't complete within **30 minutes**:
- OpenHAB turns OFF Shelly Plus Uni OUT1 anyway (safety timer)
- Prevents K7 draining the battery indefinitely
- Pi detects K7 WiFi gone, reports error via REST API (Ethernet always up)

### When Bike Leaves Home

```
Traccar: bike GPS exits home geofence
  └─► OpenHAB: turn OFF Shelly Plus Uni OUT1 (if still on)
  └─► Cancel safety timer
  └─► Pi: K7 WiFi disappears on wlan0, Ethernet stays up
```

---

## Hardware Shopping List

| Item | Purpose | Est. Cost |
|------|---------|-----------|
| **Raspberry Pi 4** (spare) | Ethernet + WiFi bridge, dump script | Already have |
| **Ethernet cable** | Pi 4 to garage switch | ~$5 |
| **Pi 4 power supply** (5V 3A USB-C) | Power the Pi in garage | ~$10 (or reuse) |
| **microSD card** (32GB) | Pi OS (footage goes straight to NAS) | ~$8 |
| **Shelly Plus Uni** | Smart relay controller, 9–28V DC, pre-wired | ~140 DKK / €19 |
| **Automotive relay** (SRA-12VDC) | Switches K7 current (10A contacts) | ~10 DKK |
| **1N4007 flyback diode** | Protects Shelly output from relay coil back-EMF | ~2 DKK |
| **3A inline fuse** | Safety fuse on battery tap | ~$2 |
| **Wire + connectors** | Battery tap, relay wiring, K7 power | ~$5 |
| **Conformal coat spray** | Weatherproofing for Plus Uni PCB | ~40 DKK |
| **Heat-shrink / IP65 box** | Physical protection | ~20 DKK |
| **SAE quick-disconnect** | Physical kill switch for long trips | ~30 DKK |
| **Pi 4 case** (optional) | Garage mounting | ~$5 |
| | **Total** | **~350–400 DKK / ~$45-50** |

**Already have:** Raspberry Pi 4, Victron Blue Smart IP65 12V/10A charger

### Shelly Plus Uni — Specifications

| Spec | Value | Motorcycle Fit |
|------|-------|----------------|
| **Power supply** | 9–28 VDC (also 8–24 VAC) | ✅ Covers 9.5V cranking through 14.8V charging |
| **Outputs** | 2× 300 mA solid-state (potential-free) | Drives automotive relay coil (~150 mA) |
| **Inputs** | 2× digital, 1× analog, 1× pulse counter | Future expansion |
| **Voltmeter** | 0–15V / 0–30V (two-range) | ✅ Monitor motorcycle battery voltage |
| **Sensors** | 1-Wire: DHT22 or up to 5× DS18B20 | Future: engine/ambient temperature |
| **Connections** | Pre-wired colored leads | ✅ No screw terminals — vibration-proof |
| **Size / Weight** | 40×21×7mm / 8.5g | ✅ Tiny, easy to hide on bike |
| **Protection** | PCB coating film | ✅ Basic weather resistance |
| **WiFi** | 802.11 b/g/n, ESP32 | ✅ Connects to home WiFi |
| **OpenHAB** | Shelly binding (HTTP, no MQTT needed) | ✅ Native integration |
| **Price** | ~18.63 € / ~140 DKK | |

### Wiring (with automotive relay)

```
Battery +12V ──┬── Victron charger (always connected)
               │
               ├── Shelly Plus Uni VAC1 (red, 9–28V power in)
               │
               └── Automotive relay COM
                         │
                    Relay NO ──→ K7 power wire (bypasses ignition)

Battery GND ───┬── Shelly Plus Uni VAC2 (black, power ground)
               ├── Automotive relay coil (-)
               └── K7 ground

Shelly Plus Uni OUT1 (black) ──→ Automotive relay coil (+)
                                 │
                           [1N4007 flyback diode across coil]
```

---

## Power Analysis & Battery Safety

### Parasitic Drain

The Shelly Plus Uni stays powered from the motorcycle battery 24/7 (even when OUT1 is OFF):

| State | Current Draw | Daily Drain |
|-------|-------------|-------------|
| Plus Uni standby (OUT1 OFF, WiFi on) | ~35–80 mA (<1W) | ~0.8–1.9 Ah/day |
| Plus Uni + relay coil (OUT1 ON, no K7 yet) | ~185–230 mA | N/A (brief) |
| Plus Uni + relay + K7 active (dump running) | ~700–1100 mA | N/A (30 min max) |
| Typical motorcycle battery capacity | — | 8–12 Ah |

### Scenarios

| Scenario | Risk | Time to Flat |
|----------|------|-------------|
| **Parked at home, Victron attached** | None | Victron maintains indefinitely |
| **Parked away, no Victron** | Battery drain | **5–7 days** |
| **Weekend trip (2–3 days)** | Low | ~3–6 Ah drained, battery OK |
| **Week+ holiday, no Victron** | **High** | Battery may die |
| **Relay stuck ON (software bug)** | **Critical** | **10–20 hours** |

### Safest Wiring — SAE Quick-Disconnect

Add an **SAE quick-disconnect** between the battery and the Shelly Plus Uni.
This uses the same connector type as the Victron charger — familiar and tool-free.

Pull the SAE plug before any trip longer than a weekend. When parked at home with the Victron, leave it connected.

```
                          SAE quick-disconnect
                          (pull before long trips)
                                 │
Battery +12V ──→ 3A fuse ──→ [SAE plug ══ SAE socket] ──┬── Plus Uni VAC1 (red)
                                                         │
                                                         └── Automotive relay COM
                                                                  │
                                                             Relay NO ──→ K7 +12V
                                                                          (bypass ignition)

Battery GND ─────────────────────────────────────────────┬── Plus Uni VAC2 (black)
                                                         ├── Relay coil (-)
                                                         └── K7 GND

Plus Uni OUT1 ──→ Relay coil (+) ──→ [1N4007 diode across coil]

Victron charger ──→ [SAE plug ══ SAE socket] ──→ Battery
(separate SAE, always connected when parked at home)
```

**Safety layers (defense in depth):**

| Layer | Protection | Scope |
|-------|-----------|-------|
| **1. SAE disconnect** | Physical kill switch for long trips | Hardware |
| **2. Shelly Plus Uni auto-off timer** | Set 30 min in Shelly firmware (Settings → Timers) | Shelly firmware |
| **3. Pi dump timeout** | 30 min max dump time, then signals completion | Software |
| **4. OpenHAB watchdog** | Turn off relay if dump doesn't complete in 45 min | Rule |
| **5. Inline fuse (3A)** | Protects wiring from shorts | Hardware |

### Shelly Plus Uni Auto-Off Timer (Critical)

Configure this **directly in the Shelly Plus Uni firmware** — works independently of OpenHAB/WiFi:

1. Open Shelly web UI (or app)
2. Go to **Settings → Timers** (for Switch 0 / OUT1)
3. Set **Auto-off after: 30 minutes**
4. This means: even if OpenHAB crashes, OUT1 turns off after 30 min max
   → automotive relay drops → K7 powers down

This is the single most important safety measure against the "relay stuck ON" scenario.

---

## Known Pitfalls & Caveats

Things to watch out for during commissioning and daily operation:

### 1. K7 WiFi Credentials Unknown
The K7's SSID and WiFi password are set via the INNOVV mobile app. Until you
power on the K7 and check, the credentials are unknown. The firmware template
shows `MYSSID` / `myssidpwd` — these are placeholders.

**Action:** Power on K7, connect phone, note SSID. Set a known password via the
app (or via API: `cmd=2010` for SSID, `cmd=2012` for password).

### 2. K7 May Record When Powered On
The K7 starts recording automatically when it powers up. During a dump session
(Shelly relay ON), the K7 will be recording to SD while you're downloading from
it. This is normally fine — but if the SD card is nearly full, **loop-recording
may overwrite the oldest files** before you download them.

**Mitigation:** The dump script downloads newest-first (most valuable footage)
and the 30-minute dump window should be enough to grab everything. Consider
sending `cmd=2001&par=0` (stop recording) at the start of the dump if this
becomes an issue.

### 3. wlan0 Default Route Hijack
When the Pi connects wlan0 to the K7 hotspot (192.168.1.x), NetworkManager may
add a default route via wlan0 — breaking the Pi's internet/LAN access via eth0.

**Mitigation:** The `wifi_manager.py` script configures the K7 connection profile
with `never-default=yes` (no default route via wlan0). Only a `/24` route for
`192.168.1.0/24` is added. Verify with `ip route` after first connection.

### 4. FTP Speed vs Safety Timeout
A full 256 GB SD dump over 2.4 GHz WiFi takes ~10–15 minutes. Over 5 GHz, ~2–5
minutes. The 30-minute safety timer is generous, but if the K7 has a very slow
FTP server or the WiFi link is poor, the timer may cut the dump short.

**Mitigation:** The Pi reports progress to OpenHAB. If dumps are consistently
cutting close to 30 min, increase the safety timer or switch to 5 GHz.

### 5. K7 Boot Time Unknown
After the Shelly relay powers the K7, it needs time to boot Linux, start hostapd,
and bring up the WiFi AP. This could be 10–30 seconds or more.

**Mitigation:** The Pi scans for the K7 SSID every 30 seconds. The first scan
may miss the AP if the K7 hasn't booted yet — the next scan will catch it. No
action needed, but be aware of this ~30–60 second delay after power-on.

### 6. Shelly Plus Uni WiFi Range
The Shelly Plus Uni connects to your **home WiFi** (not the K7). It needs to
reach the Fortinet AP from the motorcycle's parking spot. The ESP32's PCB antenna
is small — if the parking spot is far from the AP, consider:
- A Fortinet AP closer to the garage/parking
- Shelly in AP mode with the Pi as intermediary (adds complexity)

**Test:** Before permanent mounting, power the Shelly at the parking spot and
check WiFi signal strength in the Shelly web UI.

### 7. Loop-Recording Overwrites
The K7 uses loop recording — when the SD card is full, oldest files are deleted
automatically. If you don't dump frequently enough (e.g., bike sits unused for
weeks then does long rides), older footage may be lost before the next dump.

**Mitigation:** With a 256 GB card at 1080p dual-channel, you get ~20–30 hours
of footage. Dumps happen every time you arrive home. This is only a risk if:
- Very long rides with no home return
- SD card is smaller than 256 GB

### 8. Abrupt Power Cut on Relay Off
When OpenHAB turns OFF the Shelly relay, the K7 loses power instantly — no
graceful shutdown. The K7 firmware is designed for this (ignition-off is normal
for dashcams), but the **last file being recorded may be corrupt**.

**Mitigation:** This is by design — dashcams handle abrupt power loss. Each
recording segment is typically 1–3 minutes, so at most one short clip is affected.
The dump script should skip files that are currently being written (size = 0 or
still growing).

---

## Implementation Steps

### Phase 1: Hardware Setup
1. **Discover actual SSID/password** — power on K7, check WiFi networks or INNOVV app
2. **Order hardware** — Shelly Plus Uni, SRA-12VDC automotive relay, 1N4007 diode, inline fuse, SAE connectors, conformal coat, wire, Ethernet cable
3. **Wire Shelly Plus Uni + automotive relay** — battery → fuse → SAE → Plus Uni + relay → K7 power wire (bypassing ignition)
4. **Set up Pi 4** — Raspberry Pi OS Lite (64-bit), Ethernet to garage switch, configure SSH, mount NAS

### Phase 2: Manual Testing
5. **Power K7 from battery** — ignition off, Victron attached, Shelly Plus Uni OUT1 ON → verify K7 stays alive
6. **Connect Pi to K7** — manually connect wlan0 to K7 WiFi, verify connectivity to 192.168.1.254
7. **Probe the API** — test heartbeat (3012), file listing (3015), firmware version (3016)
8. **Test FTP** — connect via FTP, browse SD card, download a test file
9. **Test RTSP** — try `ffplay rtsp://192.168.1.254/live` (optional, for NVR later)

### Phase 3: Software
10. **Build Python dump script** — runs on Pi 4, handles WiFi scan/connect/dump/report
11. **OpenHAB items + rules** — K7 status items, Shelly Plus Uni relay control, timeout safety
12. **Pi → OpenHAB REST API** — status updates via HTTP over Ethernet
13. **Download database** — SQLite on Pi tracking which files have been dumped
14. **NAS mount** — configure NFS/SMB mount on Pi for footage storage

### Phase 4: Full Automation
15. **Traccar integration** — bike arrives → trigger dump sequence
16. **Shelly Plus Uni automation** — automatic OUT1 on/off based on dump state
17. **Timeout protection** — 30-minute safety cutoff
18. **Notifications** — pushover/signal alert on dump complete or errors
19. **Dashboard** — OpenHAB sitemap showing K7 status, last dump, storage used

---

## Pi Software

The complete Python software package for the Pi 4 is in [`pi-software/`](pi-software/).

### Files

| File | Purpose |
|------|---------|
| `innovv_k7_dump.py` | Main service — scan/connect/dump/disconnect loop |
| `wifi_manager.py` | wlan0 management via wpa_supplicant (band-agnostic) |
| `k7_api.py` | K7 HTTP API + HTML directory listing + download + delete |
| `openhab_client.py` | REST API client for OpenHAB status updates |
| `config.json` | All configuration (WiFi, NAS, OpenHAB, safety limits) |
| `backup-sd.sh` | Monthly Pi SD card backup to NAS (cron) |
| `innovv-k7-dump.service` | systemd service unit for the Pi |
| `install.sh` | Automated setup script (run on Pi as root) |

### Quick Deploy

```bash
scp -r pi-software/ pi@raspberrypi:/tmp/innovv-k7/
ssh pi@raspberrypi "sudo bash /tmp/innovv-k7/install.sh"
```

Then edit `/opt/innovv-k7/config.json` with the K7 WiFi credentials and NAS mount path. See `pi-software/README.md` for full instructions.

### OpenHAB Items

See `pi-software/README.md` for the full production items list. Key items:

```openhab
Group       gK7                     "INNOVV K7 Dashcam"                         <k7-cam-blue>

// Status & connection
String      K7_Dump_Status          "Dump Status [%s]"                          <k7-sync-blue>      (gK7)
Switch      K7_Camera_Online        "K7 Online [MAP(k7_onoff.map):%s]"         <k7-online-green>   (gK7)
DateTime    K7_Last_Dump            "Last Dump [%1$td.%1$tm.%1$tY %1$tH:%1$tM]" <k7-time-blue>   (gK7)
String      K7_Last_Error           "Last Error [%s]"                           <k7-alert-red>      (gK7)
String      K7_WiFi_Signal          "K7 WiFi Signal [%s]"                       <k7-wifi-cyan>      (gK7)
String      K7_WiFi_Band            "K7 WiFi Band [%s]"                         <k7-band-teal>      (gK7)

// Download progress & verification
Number      K7_Files_On_Camera      "Files on Camera [%d]"                      <k7-sdcard-orange>  (gK7)
Number      K7_Files_Downloaded     "Files Downloaded [%d]"                     <k7-download-green> (gK7)
Number      K7_MB_Downloaded        "MB Downloaded [%.1f MB]"                   <k7-data-blue>      (gK7)
Number      K7_Files_Verified       "Files Verified on NAS [%d]"                <k7-verified-green> (gK7)
Number      K7_Files_Deleted        "Files Deleted from K7 [%d]"                <k7-delete-red>     (gK7)
Number      K7_Pending_Deletes      "Pending K7 Deletes [%d]"                   <k7-pending-orange> (gK7)

// Settings & health
Switch      K7_Dump_Movie_E         "Dump Movie_E (Loop Video) [MAP(k7_onoff.map):%s]" <k7-movie-orange> (gK7)
Number      K7_Pi_Disk_Free_MB      "Pi SD Free [%d MB]"                        <k7-disk-cyan>      (gK7)
```

---

## OpenHAB Integration

The production OpenHAB items and sitemap are in `/etc/openhab/items/innovv_k7.items`
and `/etc/openhab/sitemaps/`. See `pi-software/README.md` for the full items list.

**Future items** (pending Sonoff/Shelly relay hardware delivery):
```openhab
Switch  K7_Power_Relay      "K7 Power (Relay)"          <switch>   (gK7) { channel="shelly:shellyuni:bike-k7:relay1#output" }
Number  K7_Battery_Voltage  "Battery [%.1f V]"           <energy>   (gK7) { channel="shelly:shellyuni:bike-k7:meter1#voltage" }
```

---

## Real-World Findings (2026-03 Deployment)

> **This section documents what we discovered during actual Pi 4 deployment.
> Several assumptions from the firmware analysis above turned out to be wrong.**

### FTP Does NOT Work

Despite ftpd being present in the firmware, **FTP is not accessible** from the
K7 in practice. Connection refused on port 21. The K7 only serves HTTP (port 80).
All downloads use HTTP GET with HTML directory listing parsing.

### XML File Listing API Returns Empty

The Novatek `cmd=3015` file listing returns `Status=-21` with zero file elements.
The service uses **HTML directory listing** at `http://192.168.1.254/INNOVVK7/`
instead, recursively parsing `<a href>` tags.

### SD Card Structure Is NOT DCIM

The actual K7 folder structure under `/INNOVVK7/` is:

| Folder | Content | Appears When |
|--------|---------|--------------|
| `Movie_E/` | Continuous recordings (MP4) | Always (if any recordings) |
| `Photo_E/` | Manual photo captures (JPG) | Only when photos exist |
| `EMR_E/` | Emergency/protected clips (MP4) | Only when emergency clips exist |

There is **no DCIM folder**. Folders appear dynamically.

### K7 Clock & Loop Recording

The K7 maintains its clock across power cycles — timestamps in filenames are
accurate. The dates `20260309`/`20260310` in our initial dump were correct.

**Loop recording:** The K7 automatically deletes the oldest `Movie_E/` files
when the SD card fills up, so Movie_E only contains recent footage. `Photo_E/`
and `EMR_E/` files are **never auto-deleted** and persist until the user
removes them manually.

For best accuracy, periodically sync time via the INNOVV phone app.

### BCM43455 WiFi Firmware Bug

The Pi 4's Broadcom BCM43455 WiFi chip **cannot associate** with the K7's
RTL8821CS access point using the standard Cypress firmware. It repeatedly
fails with `ASSOC_REJECT` (status code 1).

**Fix:** Use the minimal firmware (7.45.241) via update-alternatives:
```bash
sudo update-alternatives --set cyfmac43455-sdio.bin \
  /lib/firmware/cypress/cyfmac43455-sdio-minimal.bin
```

This is stable and survives reboot. Signal: -31 dBm, throughput: 2.9-4.5 MB/s.

### Photo Button Requires Pi Disconnection

The K7's photo button does not work while the Pi is connected to its WiFi.
Disconnect the Pi first, press the button, then reconnect. The `Photo_E` folder
only appears after photos are taken.

### WiFi Band Auto-Detection

The service auto-detects the K7's WiFi band (2.4 GHz vs 5 GHz) from `iw scan`
results and reports it to OpenHAB via `K7_WiFi_Band`. The `wifi_manager.py`
is fully band-agnostic — no hardcoded frequency or channel settings.

### Movie_E Toggle (Live Mid-Cycle Control)

The `K7_Dump_Movie_E` OpenHAB switch controls whether Movie_E loop recordings
are downloaded. This toggle is **re-checked before each Movie_E file** during
the download loop, so toggling it OFF mid-cycle immediately skips remaining
Movie_E files without waiting for the full cycle to finish.

### Graceful Shutdown & Download Cancellation

SIGTERM triggers a graceful shutdown that cancels in-progress HTTP downloads
within milliseconds (checked every 64KB chunk). Cancelled downloads stay as
`.partial` files and will be resumed on the next cycle.

### Incomplete Download Protection

The completeness check (byte count vs Content-Length) runs **before** renaming
`.partial` to the final filename. This prevents truncated files from being
treated as valid downloads.

### Actual OpenHAB Items (Production)

The items from earlier sections of this document are **outdated**. See
`/etc/openhab/items/innovv_k7.items` for the current production items, or
`pi-software/README.md` for the full list including:
- `K7_WiFi_Signal` (String — "Excellent (-31 dBm)")
- `K7_WiFi_Band` (String — "5 GHz" or "2.4 GHz")
- `K7_Dump_Movie_E` (Switch — ON/OFF, re-checked mid-cycle)
- `K7_Files_Verified`, `K7_Files_Deleted`, `K7_Pending_Deletes`
- `K7_Last_Error`, `K7_Pi_Disk_Free_MB`

### Verified Transfer Pipeline

Every file is SHA-256 verified during download AND re-verified by reading it
back from NAS. Only after both hashes match + media header check is the file
marked verified in SQLite and eligible for deletion from K7.

### Monthly Pi SD Backup

A cron job on the 1st of each month at 03:00 creates a compressed full SD card
image on the NAS (`dd | gzip`), keeping 3 versions. The dump service is stopped
during backup to prevent I/O starvation. The backup script reads its OpenHAB
URL and NAS path from `config.json`.

### Fully Configurable (No Hardcoded Values)

All site-specific values are in `config.json`:
- WiFi SSID, password, country code, static IP (CIDR notation)
- Camera IP, NAS path, OpenHAB URL
- Safety limits, database path, log settings

The `install.sh` script handles BCM43455 firmware detection, NAS credentials
template, rfkill persistence, and prints a configuration checklist.

---

## References

- INNOVV K7 support: https://www.innovv.com/pages/innovv-k7-support
- INNOVV App (Android): `cn.rxt.case.innovv` on Google Play
- INNOVV App (iOS): https://apps.apple.com/us/app/innovv/id1235353801
- Novatek NA51055 CarDV SDK documentation (vendor-internal)
- K7 Manual: https://drive.google.com/file/d/1p9SigjXJ8GfYPslu9Xc0cdqsC-V1-lLz/view
