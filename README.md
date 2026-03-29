# innovv-k7-autodump

Automated INNOVV K7 dashcam footage backup via Shelly Plus Uni, IRFP9140N MOSFET power switching, and openHAB — triggered by **dual-sensor charger detection** (Victron BLE + voltage fallback) on motorcycle.

## What It Does

When you plug in your motorcycle battery charger:

1. **Victron BLE daemon** (on Pi 3) detects charger is actively charging via BLE GATT — or **Shelly ADC** detects high voltage (>13.0V) as fallback
2. **openHAB state machine** (10 JSRules) confirms charger presence with 60s stabilisation
3. **Relay turns ON** → IRFP9140N MOSFET switches 12V to the K7 dashcam
4. **K7 boots up** and broadcasts its WiFi access point
5. **Raspberry Pi 3** detects the K7 WiFi, connects, and downloads all new footage to NAS
6. Every file is **SHA-256 verified** (download hash + NAS read-back) before deletion from K7
7. **Dump complete** → Relay turns OFF → K7 shuts down
8. **Charger disconnected** → BLE detects Idle state → System re-arms to PARKED

No manual intervention. Footage is automatically backed up whenever you charge.

## Architecture

```
                          ┌─────────────────────────────────────────┐
                          │          openHAB 5.1.3                  │
                          │                                         │
  Traccar FMM920 ────────>│  vehicle-motorcycle-k7-power.js         │
  (Vehicle10_Ignition)     │  ┌───────────────────────────────────┐ │
  Shelly ADC ─────────────>│  │ State Machine (10 JSRules)        │ │
  (MC_K7_Shelly_Voltage)   │  │                                   │ │
  Victron BLE ────────────>│  │ DUAL-SENSOR charger detection:    │ │
  (MC_Charger_BLE_Online)  │  │   PRIMARY:  BLE charger state     │ │
  (MC_Charger_State)       │  │   FALLBACK: Shelly ADC voltage    │ │
                           │  │                                   │ │
                           │  │ PARKED→CHARGING→TRANSFERRING      │ │
                           │  │ →COOLDOWN→DUMP_DONE→PARKED        │ │
  K7_Dump_Status ─────────>│  └───────────┬───────────────────────┘ │
  (from Pi REST API)       │              │ sendCommand(ON/OFF)     │
                           └──────────────┼─────────────────────────┘
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
                              │  INNOVV K7 Dashcam         │
                              │  Dual-channel (front+rear) │
                              │  WiFi AP (5 GHz)           │
                              └───────────┬────────────────┘
                                          │ WiFi 5GHz
                              ┌───────────┴────────────────┐
                              │  Raspberry Pi 3             │
                              │  ALFA AWUS036ACM (USB3)     │
                              │  innovv-k7-dump.service     │
                              │  victron-ble-monitor.service│
                              │  Downloads → Verifies       │
                              │  → Deletes → Reports        │
                              └────────────────────────────┘
```

## Dual-Sensor Charger Detection

The system uses **two independent sensors** to detect charger presence, eliminating false triggers:

| Sensor | Role | How |
|--------|------|-----|
| **Victron BLE** (primary) | Reads actual charger state | Pi daemon connects via BLE GATT every 30s |
| **Shelly ADC** (fallback) | Voltage threshold detection | >13.0V = charger, <12.7V = removed |

**Three detection tiers:**
1. `isBLECharging()` — Active charging (Bulk/Absorption/Float/Recondition)
2. `isBLEConnected()` — Charger connected (above + Storage/Idle — full battery goes to Storage)
3. Voltage > 13.0V — Fallback when BLE offline

**Why dual-sensor?** Voltage alone is unreliable — the charger's Storage stage (~13.0V) sits right at the detection threshold, and battery voltage lingers above 13.0V for minutes after charger removal. BLE provides the ground truth.

See the [victron-ble-openhab](https://github.com/Prinsessen/victron-ble-openhab) repository for the BLE daemon.

## Hardware Required

| Component | Model | Role |
|-----------|-------|------|
| Shelly Plus Uni | SNSN-0043X (Gen 2) | Relay control + ADC voltage sensing |
| IRFP9140N | P-channel MOSFET, TO-247 | High-side power switch for K7 |
| 10K resistor | 1/4W | Gate pull-up (fail-safe OFF) |
| 100Ω resistor | 1/4W (optional) | Gate inrush limiter |
| Blocking diode | 1N4007 (1A/1000V) | **INSTALLED** — Blocks MOSFET back-feed into ignition circuit |
| INNOVV K7 | Dual-channel dashcam | Records front + rear video |
| Raspberry Pi 3 | Any RAM variant | Runs dump service + BLE monitor |
| Victron Blue Smart IP65 12/10 | BLE-enabled battery charger | Primary charger detection via BLE |
| ALFA AWUS036ACM | MT7612U, AC1200, USB 3.0 | 5GHz WiFi to K7 AP |
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
  Motorcycle ignition 12V ──►|── 1N4007 ──┘
                            (1N4007: anode=ignition, cathode=splice)
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
│   │   ├── motorcycle_k7_power.items      ← Shelly + BLE charger + session tracking + virtual state items (30 items)
│   │   └── innovv_k7.items                ← Pi dump service status items
│   ├── things/
│   │   └── shelly.things                  ← Shelly Plus Uni thing definition
│   ├── rules/
│   │   └── vehicle-motorcycle-k7-power.js ← State machine (10 JSRules, dual-sensor BLE + voltage)
│   └── transform/
│       └── k7_onoff.map                   ← ON/OFF display mapping
└── docs/
    ├── K7_AUTO_POWER_README.md            ← Detailed auto-power documentation (BLE integration, all 10 rules)
    └── FIRMWARE_ANALYSIS.md               ← K7 firmware reverse engineering
```

## Quick Start

### 1. Wire the MOSFET Circuit

See the circuit diagram above. Solder the IRFP9140N + 10K pull-up + optional 100Ω gate resistor. Heat-shrink the assembly.

**Install the 1N4007 diode** in the ignition wire before the splice point to prevent MOSFET back-feed to the GPS tracker. (Anode on ignition side, cathode on K7/MOSFET splice.)

### 2. Set Up the Victron BLE Monitor

The BLE daemon runs on the same Pi as the dump service. See [victron-ble-openhab](https://github.com/Prinsessen/victron-ble-openhab) for setup.

### 3. Configure the Shelly Plus Uni

- Connect Shelly to your home WiFi via the Shelly app
- Add the ADC peripheral: `Uni.AddPeripheral { type: "voltmeter" }` → creates `voltmeter:100`
- Upload the failsafe script from `shelly/shelly-failsafe-script.js` via RPC (`Script.PutCode`)
- **Important:** Strip all non-ASCII characters before upload (Shelly mJS engine rejects them)

### 4. Set Up the Raspberry Pi

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

### 5. Configure openHAB

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

### 6. Test

1. Connect the battery charger
2. Watch the state machine: `tail -f /var/log/openhab/openhab.log | grep k7_power`
3. States should progress: `PARKED → CHARGING → TRANSFERRING → COOLDOWN → DUMP_DONE`
4. Disconnect battery cable → BLE reports Idle → system re-arms to `PARKED`
5. Ignition ON from `DUMP_DONE` → transitions to `RIDING` (charger cable removal undetectable by BLE)

## State Machine

| State | Relay | Description |
|-------|-------|-------------|
| **PARKED** | OFF | Normal parked state (no charger) |
| **RIDING** | OFF | Ignition ON — K7 powered by ignition circuit |
| **CHARGING** | OFF | Charger detected (BLE or voltage), 60s stabilisation |
| **TRANSFERRING** | ON | K7 powered, Pi downloading footage |
| **COOLDOWN** | OFF→ | Dump complete, 30s cooldown |
| **DUMP_DONE** | OFF | Cycle complete, ready for next ride — ignition ON allowed (→ RIDING) |
| **LOW_BATTERY** | OFF | Battery < 12.0V — relay forced off |

### Rules (10 JSRules)

| # | Rule | Trigger |
|---|------|---------|
| 1 | System Init | Startup — relay OFF, state recovery |
| 2 | Ignition Handler | Ignition changed — 5s debounce (1N4007 diode), DUMP_DONE → RIDING |
| 3 | Voltage Monitor | ADC voltage changed — charger/low battery detect |
| 4 | Dump Complete | Dump status changed — cooldown & relay OFF |
| 5 | WiFi Poll | Cron (1 min) — Shelly SSID/RSSI |
| 6 | Relay Tracker | Relay changed — timestamp tracking |
| 7 | Manual Override | Relay command — manual dump trigger |
| 8 | BLE Online | BLE Online changed — charger presence |
| 9 | BLE Charge State | Charge state changed — Off→re-arm, Charging/Storage/Idle→start |
| 10 | Connection Status | BLE items changed — MC_Charger_Connection string |

## Safety Features

| Feature | Value | Purpose |
|---------|-------|---------|
| Stabilisation delay | 60 seconds | Avoids false triggers from voltage spikes |
| Safety timeout | 30 minutes | Prevents indefinite relay ON (battery drain) |
| Low battery cutoff | 12.0V (openHAB) / 11.5V (Shelly) | Protects battery from deep discharge |
| Ignition override | Immediate | Relay OFF when ignition turns ON |
| Re-arm grace period | 5 minutes | Suppresses voltage-only false retrigger after charger disconnect |
| BLE dual-sensor | Primary authority | Eliminates voltage threshold guessing |
| SHA-256 verification | Every file | Download hash + NAS read-back hash must match |
| Media header validation | Every file | MP4 ftyp / JPEG magic bytes checked |
| NAS space check | Every 10 files | Stops if < 10 GB free |
| 3-failure abort | Consecutive | Stops if K7 goes offline mid-cycle |
| MOSFET fail-safe | Physical | 10K pull-up ensures OFF when Shelly is unpowered |
| Local failsafe script | On Shelly | Basic power control when openHAB is unreachable |
| Manual mode safety | 60 min timeout | Shelly detects external relay toggle, 60-min safety timer |

## Verified Transfer Pipeline

Every file goes through this pipeline before deletion from K7:

```
HTTP download → .partial temp → SHA-256 during download → fsync
→ atomic rename → NAS read-back → SHA-256 comparison
→ media header check → SQLite record → K7 deletion
```

Files are **never** deleted from the K7 unless 100% verified on the NAS.

## Pi WiFi Note

An **ALFA AWUS036ACM** USB WiFi dongle (MediaTek MT7612U, `mt76x2u` driver) is used on `wlan1` for 5 GHz connectivity to the K7. The onboard BCM43455 (wlan0) had `ASSOC_REJECT` issues with the K7's RTL8821CS access point and is no longer used.

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
- [Auto-Power Documentation](docs/K7_AUTO_POWER_README.md) — MOSFET circuit, dual-sensor BLE integration, state machine, all 10 rules
- [K7 Firmware Analysis](docs/FIRMWARE_ANALYSIS.md) — Reverse engineering of the K7 firmware (Novatek NA51055, RTL8821CS WiFi, CarDV HTTP API)
- [Victron BLE Monitor](https://github.com/Prinsessen/victron-ble-openhab) — Standalone BLE daemon for the Victron charger

## Related Projects

- **[victron-ble-openhab](https://github.com/Prinsessen/victron-ble-openhab)** — BLE GATT monitor for Victron Blue Smart IP65 charger. Primary charger detection sensor for this project.

## License

MIT License — see [LICENSE](LICENSE).

## Credits

**Author:** Nanna Agesen ([@Prinsessen](https://github.com/Prinsessen)) — Nanna@agesen.dk

**BLE Protocol:** Based on reverse engineering by [Olen](https://github.com/Olen) (VictronConnect / phoenix.py).

Built for a motorcycle with an INNOVV K7 dashcam, automated with openHAB, Shelly, and a Raspberry Pi.
