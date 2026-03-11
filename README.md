# innovv-k7-autodump

Automated INNOVV K7 dashcam footage backup via Shelly Plus Uni, IRFP9140N MOSFET power switching, and openHAB — triggered by battery charger detection on motorcycle.

## What It Does

When you plug in your motorcycle battery charger:

1. **Shelly Plus Uni** detects high voltage (>13.2V) on the ADC input
2. **Relay turns ON** → IRFP9140N MOSFET switches 12V to the K7 dashcam
3. **K7 boots up** and broadcasts its WiFi access point
4. **Raspberry Pi 4** detects the K7 WiFi, connects, and downloads all new footage to NAS
5. Every file is **SHA-256 verified** (download hash + NAS read-back) before deletion from K7
6. **Dump complete** → Relay turns OFF → K7 shuts down

No manual intervention. Footage is automatically backed up whenever you charge.

## Architecture

```
                          ┌─────────────────────────────────────────┐
                          │       openHAB (home automation)         │
                          │                                         │
  GPS Tracker ───────────>│  vehicle-motorcycle-k7-power.js         │
  (Ignition / Voltage)    │  ┌───────────────────────────────────┐  │
                          │  │ State Machine (5 JSRules)         │  │
  K7_Dump_Status ────────>│  │ PARKED→CHARGING→DUMPING→COOLDOWN │  │
  (from Pi REST API)      │  └───────────┬───────────────────────┘  │
                          │              │ sendCommand(ON/OFF)       │
                          └──────────────┼──────────────────────────┘
                                         │
                                         ▼ Shelly Binding
                             ┌───────────────────────────┐
                             │  Shelly Plus Uni           │
                             │  ADC: battery voltage      │
                             │  Relay: MOSFET gate drive  │
                             │  Script: local failsafe    │
                             └───────────┬────────────────┘
                                         │
                                         ▼ Relay drives MOSFET gate
                             ┌───────────────────────────┐
                             │  IRFP9140N P-ch MOSFET    │
                             │  High-side switch          │
                             │  10K pull-up (fail-safe)   │
                             └───────────┬────────────────┘
                                         │ Switched 12V
                                         ▼
                             ┌───────────────────────────┐
                             │  INNOVV K7 Dashcam        │
                             │  Dual-channel (front+rear) │
                             │  WiFi AP (5 GHz)           │
                             └───────────┬────────────────┘
                                         │ WiFi
                                         ▼
                             ┌───────────────────────────┐
                             │  Raspberry Pi 4            │
                             │  Auto-dump service         │
                             │  Downloads → Verifies      │
                             │  → Deletes → Reports       │
                             └────────────────────────────┘
```

## Hardware Required

| Component | Model | Role |
|-----------|-------|------|
| Shelly Plus Uni | SNSN-0043X (Gen 2) | Relay control + ADC voltage sensing |
| IRFP9140N | P-channel MOSFET, TO-247 | High-side power switch for K7 |
| 10K resistor | 1/4W | Gate pull-up (fail-safe OFF) |
| 100Ω resistor | 1/4W (optional) | Gate inrush limiter |
| Schottky diode | SB540 (5A/40V) or 1N5822 (3A/40V) | **REQUIRED** — Blocks MOSFET back-feed into ignition circuit |
| INNOVV K7 | Dual-channel dashcam | Records front + rear video |
| Raspberry Pi 4 | Any RAM variant | Runs the auto-dump service |
| Battery charger | Any float/maintenance charger | Triggers the dump cycle |
| GPS tracker | e.g. Teltonika FMM920 (optional) | Ignition state for state machine |

## MOSFET Circuit

```
  Battery +12V (always-on, fused 3A)
     │
     ├──────── Shelly Plus Uni POWER
     ├──────── Shelly ADC input (Voltmeter:100)
     │
     ├── IRFP9140N Source (pin 3)
     │       │
     │     10K resistor (pull-up: fail-safe MOSFET OFF)
     │       │
     │   IRFP9140N Gate (pin 1) ── 100Ω ── Shelly Relay COM
     │                                            │
     │                          Shelly Relay NO ──┤
     │                                            │
     │                                       Battery GND
     │
     └── IRFP9140N Drain (pin 2) ──────────┐
                                          ├──> K7 DC power input (+)
  Motorcycle ignition 12V ──►|── SB540 ──┘
                            (Schottky diode: anode=ignition, cathode=splice)
                            (Blocks MOSFET back-feed to ignition circuit)

  Battery GND ─────────────────────> K7 DC power input (-)
```

| Relay State | Vgs | MOSFET | K7 Power |
|-------------|-----|--------|----------|
| **OPEN** (OFF) | 0V | OFF | No power |
| **CLOSED** (ON) | -12V | Fully ON | Powered |

**Fail-safe:** If Shelly loses power, relay opens, 10K pull-up holds gate high, MOSFET stays OFF. K7 stays off. Battery is safe.

## Repository Structure

```
innovv-k7-autodump/
├── README.md                              ← You are here
├── pi-software/
│   ├── innovv_k7_dump.py                  ← Main dump service (~850 lines)
│   ├── wifi_manager.py                    ← WiFi connection management
│   ├── k7_api.py                          ← K7 HTTP API client
│   ├── openhab_client.py                  ← openHAB REST API reporter
│   ├── config.example.json                ← Configuration template
│   ├── install.sh                         ← Pi setup script (run as root)
│   ├── backup-sd.sh                       ← Monthly Pi SD backup to NAS
│   ├── innovv-k7-dump.service             ← systemd service unit
│   └── README.md                          ← Pi software documentation
├── shelly/
│   └── shelly-failsafe-script.js          ← On-device mJS failsafe script
├── openhab/
│   ├── items/
│   │   ├── motorcycle_k7_power.items      ← Shelly/MOSFET power control items
│   │   └── innovv_k7.items                ← Pi dump service status items
│   ├── things/
│   │   └── shelly.things                  ← Shelly Plus Uni thing definition
│   ├── rules/
│   │   └── vehicle-motorcycle-k7-power.js ← State machine (5 JSRules)
│   └── transform/
│       └── k7_onoff.map                   ← ON/OFF display mapping
└── docs/
    ├── K7_AUTO_POWER_README.md            ← Detailed auto-power documentation
    └── FIRMWARE_ANALYSIS.md               ← K7 firmware reverse engineering
```

## Quick Start

### 1. Wire the MOSFET Circuit

See the circuit diagram above. Solder the IRFP9140N + 10K pull-up + optional 100Ω gate resistor. Heat-shrink the assembly.

### 2. Configure the Shelly Plus Uni

- Connect Shelly to your home WiFi via the Shelly app
- Add the ADC peripheral: `Uni.AddPeripheral { type: "voltmeter" }` → creates `voltmeter:100`
- Upload the failsafe script from `shelly/shelly-failsafe-script.js` via RPC (`Script.PutCode`)
- **Important:** Strip all non-ASCII characters before upload (Shelly mJS engine rejects them)

### 3. Set Up the Raspberry Pi

```bash
# Clone this repo
git clone https://github.com/Prinsessen/innovv-k7-autodump-.git
cd innovv-k7-autodump/pi-software

# Copy config and edit with your settings
cp config.example.json config.json
nano config.json  # Set your openHAB URL, NAS path, etc.

# Run the installer (as root)
sudo bash install.sh

# Start the service
sudo systemctl start innovv-k7-dump
sudo systemctl status innovv-k7-dump
```

See [pi-software/README.md](pi-software/README.md) for detailed setup instructions.

### 4. Configure openHAB

Copy the openHAB configuration files to your openHAB instance:

```bash
# Adjust device IDs and IPs in these files first!
cp openhab/items/*.items    /etc/openhab/items/
cp openhab/things/*.things  /etc/openhab/things/
cp openhab/rules/*.js       /etc/openhab/automation/js/
cp openhab/transform/*.map  /etc/openhab/transform/
```

**Important:** Replace placeholder values in the openHAB files:
- `xxxxxxxxxxxx` / `XXXXXXXXXXXX` → Your Shelly's device ID (MAC address without colons, lowercase)
- `192.168.1.62` → Your Shelly's IP address
- `192.168.1.10` → Your openHAB server's IP address

### 5. Test

1. Connect the battery charger
2. Watch the state machine: `tail -f /var/log/openhab/openhab.log | grep k7_power`
3. States should progress: `PARKED → CHARGING → DUMPING → COOLDOWN → PARKED`

## State Machine

| State | Relay | Description |
|-------|-------|-------------|
| **PARKED** | OFF | Normal parked state |
| **RIDING** | OFF | Ignition ON — K7 powered by ignition circuit directly |
| **CHARGING** | OFF | Charger detected, 60s stabilisation in progress |
| **DUMPING** | ON | K7 powered, Pi downloading footage |
| **COOLDOWN** | OFF→ | Dump complete, 30s cooldown before relay OFF |
| **LOW_BATTERY** | OFF | Battery < 12.0V — relay forced off |

## Safety Features

| Feature | Value | Purpose |
|---------|-------|---------|
| Stabilisation delay | 60 seconds | Avoids false triggers from voltage spikes |
| Safety timeout | 30 minutes | Prevents indefinite relay ON (battery drain) |
| Low battery cutoff | 12.0V (openHAB) / 11.5V (Shelly) | Protects battery from deep discharge |
| Ignition override | Immediate | Relay OFF when ignition turns ON |
| SHA-256 verification | Every file | Download hash + NAS read-back hash must match |
| Media header validation | Every file | MP4 ftyp / JPEG magic bytes checked |
| NAS space check | Every 10 files | Stops if < 10 GB free |
| 3-failure abort | Consecutive | Stops if K7 goes offline mid-cycle |
| MOSFET fail-safe | Physical | 10K pull-up ensures OFF when Shelly is unpowered |
| Local failsafe script | On Shelly | Basic power control when openHAB is unreachable |

## Verified Transfer Pipeline

Every file goes through this pipeline before deletion from K7:

```
HTTP download → .partial temp → SHA-256 during download → fsync
→ atomic rename → NAS read-back → SHA-256 comparison
→ media header check → SQLite record → K7 deletion
```

Files are **never** deleted from the K7 unless 100% verified on the NAS.

## Pi WiFi Firmware Note

The Pi 4's BCM43455 WiFi chip **requires minimal firmware** (7.45.241) to connect to the K7's RTL8821CS 5 GHz access point. The standard Cypress firmware causes `ASSOC_REJECT` errors. The install script handles this automatically.

## Configuration

Copy `pi-software/config.example.json` to `config.json` and edit:

| Setting | Default | Description |
|---------|---------|-------------|
| `k7_wifi.ssid` | `INNOVV_K7` | K7 WiFi SSID (factory default) |
| `k7_wifi.password` | `12345678` | K7 WiFi password (factory default) |
| `k7_wifi.country` | `DK` | 2-letter country code for regulatory domain |
| `download.nas_mount_path` | `/mnt/nas/dashcam` | NAS mount point for footage storage |
| `openhab.url` | `http://192.168.1.10:8080` | Your openHAB REST API URL |
| `safety.max_dump_duration_min` | `30` | Maximum dump time (battery protection) |

## Documentation

- [Pi Software README](pi-software/README.md) — Detailed Pi setup, K7 API details, NAS structure, monitoring
- [Auto-Power Documentation](docs/K7_AUTO_POWER_README.md) — MOSFET circuit, state machine, Shelly configuration, items/rules reference
- [K7 Firmware Analysis](docs/FIRMWARE_ANALYSIS.md) — Reverse engineering of the K7 firmware (Novatek NA51055, RTL8821CS WiFi, CarDV HTTP API)

## License

MIT License — see [LICENSE](LICENSE).

## Credits

**Author:** Nanna Agesen ([@Prinsessen](https://github.com/Prinsessen)) — Nanna@agesen.dk

Built for a motorcycle with an INNOVV K7 dashcam, automated with openHAB, Shelly, and a Raspberry Pi.
