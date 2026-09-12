# innovv-k7-autodump

Automated INNOVV K7 dashcam footage backup, triggered by **dual-sensor charger detection** (Victron BLE, with a battery-voltage fallback) when the motorcycle goes on charge.

A controller in the garage closes a relay on the machine, which tells the camera
to wake. The camera joins its own WiFi, a Raspberry Pi pulls the new footage to a
NAS and verifies every file, and the relay opens again.

> **Rebuilt on 2026-09-12.** The controller used to live on the motorcycle and
> drive a MOSFET; it now sits in the garage on mains and drives a signal relay
> down a two-core lead. See [Switching circuit](#switching-circuit). The chief
> gain is that the machine carries no standing load at all.

## What It Does

When you plug in your motorcycle battery charger:

1. **Victron BLE daemon** (on the Pi) detects the charger is actively charging via BLE GATT — with the GPS tracker's battery voltage as fallback
2. **openHAB state machine** confirms the charger, and that the garage lead is plugged in, with 60 s stabilisation
3. **Relay closes** → the signal relay on the machine drives the K7's ignition line
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
  Tracker battery ────────>│  │ State Machine (14 JSRules)        │ │
  (Vehicle10_Power)        │  │                                   │ │
  Victron BLE ────────────>│  │ Charger detection:                │ │
  (MC_Charger_BLE_Online)  │  │   ONLY:     BLE charger state     │ │
  (MC_Charger_State)       │  │   FALLBACK: tracker voltage       │ │
  Garage lead ────────────>│  │   GATE:     lead must be in       │ │
  (MC_K7_Lead_Connected)   │  │                                   │ │
                           │  │                                   │ │
                           │  │ PARKED→CHARGING→TRANSFERRING      │ │
                           │  │ →COOLDOWN→DUMP_DONE→PARKED        │ │
  K7_Dump_Status ─────────>│  └───────────┬───────────────────────┘ │
  (from Pi REST API)       │              │ sendCommand(ON/OFF)     │
                           └──────────────┼─────────────────────────┘
                                          │
                                          ▼ Shelly Binding
                              ┌───────────────────────────┐
                              │  Shelly Plus Uni (garage)  │
                              │  ADC: coil sense current   │
                              │  Relay: feeds the coil     │
                              │  Script: heartbeat failsafe│
                              └───────────┬────────────────┘
                                          │
                                          ▼ two-core lead, ~12 mA
                              ┌───────────────────────────┐
                              │  G6S-2 relay (on machine)  │
                              │  Contact on K7 YELLOW      │
                              │  Coil release = fail-safe  │
                              └───────────┬────────────────┘
                                          │ Ignition signal
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

## Clamp-on-Bike Detection (drain prevention)

The charger sits permanently on 230V mains, so "BLE online" says nothing about whether the DC clamps are actually on the bike. Powering the K7 with the clamps hanging in open air would drain the bike battery. `isSecondaryConnected()` proves the physical connection, in priority order:

| Proof | Signal | Why it's trusted |
|-------|--------|------------------|
| **D** (primary) | Charge-current registers seen in the daemon's recent BLE cycles (`MC_Charger_Secondary_Proof`) | Current can only flow through a **closed circuit** — a hard physical fact. Holds even at a full battery, where the charger keeps a small maintenance current. |
| **B** (fallback) | Instantaneous current ≥ 0.10 A | Used only in the brief window before the daemon has posted Proof D. |
| **A** (last resort) | Self-calibrated `|ChargerV − BatteryV|` delta vs a learned baseline | Only when Proof D is unknown/settling. Note: the delta method false-reads "connected" at a full battery, which is exactly why Proof D leads. |

Gated by `isHome()` (bike present via BLE beacon or GPS geofence) and a generator-rejection guard (battery driven far above the charger setpoint = engine alternator, not the charger). The deciding proof is surfaced in the UI as `MC_Secondary_Reason` (e.g. "Proof D — current seen", "Off — no current (Proof D)").

> **Proof D background:** the charger exposes no "secondary connected" characteristic (Victron-confirmed; the `0xEDD5`/`0xEDD7` registers proved to be plain mirrors of the main voltage/current in a live clamp on/off A/B test). The reliable discriminator is simply *whether current is flowing*, which the daemon derives from the BLE register stream.

## Hardware Required

| Component | Model | Role |
|-----------|-------|------|
| Shelly Plus Uni | SNSN-0043X (Gen 2) | Relay control + ADC sensing. **In the garage, on mains** — not on the machine |
| Signal relay | Omron `G6S-2 DC12` (DPDT, 1 kΩ coil) | On the machine. Drives the camera's ignition line; the coil is fed from the garage |
| 100 Ω resistor | 1/4 W | Sense resistor in the coil's return leg — this is what makes the lead detectable |
| Two-core lead | 2 × 0.5 mm², numbered cores | Garage to machine. Carries the coil current and reports that it is connected |
| Connector pair | Deutsch DTM, 2-way | The interlock: unplugged means the dump cannot run |
| Blocking diode | 1N4007 (1A/1000V) | **INSTALLED** — blocks back-feed from the switched ignition line into the ignition circuit |
| INNOVV K7 | Dual-channel dashcam | Records front + rear video |
| Raspberry Pi 3 | Any RAM variant | Runs dump service + BLE monitor |
| Victron Blue Smart IP65 12/10 | BLE-enabled battery charger | Primary charger detection via BLE |
| ALFA AWUS036ACM | MT7612U, AC1200, USB 3.0 | 5GHz WiFi to K7 AP |
| GPS tracker | e.g. Teltonika FMM920 (optional) | Ignition state for state machine |

## Switching circuit

> **Rebuilt on 2026-09-12.** The controller moved off the motorcycle and into the
> garage. What it switches, and how, changed with it. The arrangement this
> replaced is kept at the end of this section — it ran for six months and someone
> may still be looking at one.

### What is actually switched

The camera has three wires, and only one of them is a control input:

| K7 wire | What it is | Fed from |
|---|---|---|
| **RED** | Permanent 12 V | Battery, direct. **Always live, never switched.** |
| **BLACK** | Ground | Battery ground |
| **YELLOW** | Ignition — a **sense input**, telling the camera when to run | The switching element |

**Nothing in this project has ever switched the camera's power.** It switches the
ignition signal, and the camera's own permanent feed stays connected throughout.
That distinction is easy to lose and expensive to get wrong: route the camera's
main supply through a small switching element and you have both under-rated it
and removed the permanent feed the camera needs to shut down cleanly.

### The arrangement now

A signal relay on the machine, with its coil fed from the garage down a two-core
lead. The controller never touches the motorcycle's wiring directly.

```
  GARAGE                                        |  MOTORCYCLE
                                                |
  12 V PSU (+) ---- relay COM                   |
                    relay NO  ------- core 1 ---+--- coil +  \
                                                |             ) signal relay
  12 V PSU (-) ---- 100 ohm ------- core 2 -----+--- coil -  /   (DPDT, 12 V)
                        |                       |
       controller ADC --+                       |   contact COM <-- battery +12 V (fused)
       (reads the coil current)                 |   contact NO  --> K7 YELLOW
                                                |                   (via the existing
                                                |                    1N4007 from ignition)
```

- The controller switches the **high** side; the return runs through the sense
  resistor, so the same two wires carry the coil current and report that they are
  connected.
- Coil energised: about **12 mA** through a **1 kOhm** coil, developing roughly
  **0.86 V** across the 100 Ohm as the ADC reads it.
- Lead unplugged: no circuit, and the ADC reads **0.000 V**.

### Why the lead can be sensed but not polled

The sense resistor sits in the coil's return leg, so it only carries current
while the coil is energised. With the relay open the ADC reads zero whether the
lead is plugged in or lying on the bench — *no circuit* and *no connection* are
the same measurement.

So the lead is tested by **asking**: close the relay, wait, look, and open it
again if the answer was no. It cannot be done passively, and the arithmetic says
why. The ADC needs about 0.27 V across the 100 Ohm to read anything at all, which
is 2.7 mA, which is 2.7 V across a 1 kOhm coil — and a signal relay of this type
is only guaranteed to **release** below 1.2 V. Any sense current large enough to
see is large enough to hold the contact closed. There is no window.

The probe is therefore tied to the moment the answer matters — the charger
arriving, or someone asking — and never to a timer. The relay contact sits on the
camera's ignition line, so a fifteen-minute poll would wake the camera ninety-six
times a day to answer a question nobody had asked.

### Fail-safe behaviour

| Event | What happens | Why |
|---|---|---|
| Garage loses power | Controller drops, coil releases, contact opens, camera sleeps | Free. No code involved |
| Lead unplugged | No coil current, contact opens | The connector is the interlock |
| Controller hangs with the relay closed | On-board script opens it when the heartbeat stops | See the failsafe script |
| Dump running when the lead is pulled | Detected within ten seconds, sequence aborted | Added 2026-09-12 |

The first of those did not exist in the previous arrangement, where the
controller ran from the motorcycle's own battery. Mains power made it free.

### Parasitic draw on the motorcycle

**None at rest.** The only thing left on the machine is the relay, and its coil
draws ~12 mA solely while energised. The previous arrangement left a controller
on the machine drawing 80–110 mA continuously, which was the single largest item
in the power budget.

---

<details>
<summary><strong>Historical: the MOSFET arrangement, until 2026-09-12</strong></summary>

The controller lived on the motorcycle and drove a P-channel MOSFET as a
high-side switch on the ignition-sense line. Its ADC read the machine's battery
voltage directly, which is what made voltage-threshold charger detection possible
at the time.

```
  Battery +12V (always-on, fused 3A)
     |
     +-------- Controller POWER
     +-------- Controller ADC input
     |
     +-- IRFP9140N Source (pin 3)
     |       |
     |     10K resistor (pull-up: fail-safe MOSFET OFF)
     |       |
     |   IRFP9140N Gate (pin 1) -- 100 ohm -- Relay COM
     |                                            |
     |                             Relay NO ------+
     |                                            |
     |                                       Battery GND
     |
     +-- IRFP9140N Drain (pin 2) ----------+
                                           +--> K7 YELLOW (ignition sense)
  Motorcycle ignition 12V --->|-- 1N4007 --+
                            (anode=ignition, cathode=splice)
```

| Relay | Vgs | MOSFET | K7 ignition line |
|---|---|---|---|
| **OPEN** | 0 V | OFF | Not driven — camera sleeps |
| **CLOSED** | −12 V | ON | Driven to 12 V — camera runs |

**An earlier version of this README drew the MOSFET drain going to "K7 DC power
input (+)" and labelled the table "K7 Power: No power / Powered".** That was
wrong for the whole life of the circuit. The drain went to the YELLOW ignition
sense wire; the camera's DC supply was the RED wire, permanently connected. The
error is recorded rather than quietly deleted because anyone who built from that
drawing has the camera's main supply running through a TO-247 that was never
meant to carry it.

Removed on 2026-09-12: the controller, the IRFP9140N, its 10K pull-up and the
100 Ohm gate resistor. The 1N4007 and the camera's own three wires stayed.

</details>

## Repository Structure

```
innovv-k7-autodump/
├── README.md                              ← You are here
├── pi-software/
│   ├── innovv_k7_dump.py                  ← Main dump service (~1100 lines)
│   ├── wifi_manager.py                    ← WiFi connection management
│   ├── k7_api.py                          ← K7 HTTP API client (heartbeat, listing, download, delete, free space, card status)
│   ├── openhab_client.py                  ← openHAB REST API reporter
│   ├── k7_liveproxy.py                    ← On-demand MJPEG live-view proxy (k7-liveproxy.service)
│   ├── k7_diskfree.py                     ← One-shot SD free-space probe (cmd=3017)
│   ├── k7_disktest.py                     ← One-shot disk-info probe (compares 3017 vs 4003)
│   ├── k7_captest.py                      ← Read-only capability probe (cmd 3022/3024/3030/3007/3014)
│   ├── k7_format_sd.py                    ← SD format helper (cmd=3010) — use with care
│   ├── k7_format_watch.py                 ← Watches for SD-full and auto-formats (cmd=3010)
│   ├── config.example.json                ← Configuration template
│   ├── install.sh                         ← Pi setup script (run as root)
│   ├── backup-sd.sh                       ← Monthly Pi SD backup to NAS
│   ├── innovv-k7-dump.service             ← systemd service unit
│   └── README.md                          ← Pi software documentation
├── shelly/
│   └── shelly-failsafe-script.js          ← On-device mJS failsafe script
├── openhab/
│   ├── items/
│   │   ├── motorcycle_k7_power.items      ← Relay, lead, BLE charger, session tracking (42 items)
│   │   └── innovv_k7.items                ← Pi dump service status + Storage Health items (SD free/used/card status)
│   ├── things/
│   │   └── shelly.things                  ← Controller thing definition
│   ├── rules/
│   │   ├── vehicle-motorcycle-k7-power.js ← State machine, 14 rules
│   │   ├── vehicle-motorcycle-k7-lead.js  ← Garage lead detection, 5 rules
│   │   ├── vehicle-motorcycle-ignition.js ← Ignition notifications
│   │   ├── vehicle-motorcycle-k7-notify.js ← Dump result notifications
│   │   └── vehicle-motorcycle-k7-charge-history.js ← Per-session charge logging
│   ├── icons/                             ← Sitemap icons (k7-*, victron-*)
│   └── transform/
│       ├── k7_onoff.map                   ← Relay ON/OFF display mapping
│       ├── k7_connected.map               ← Charger clamps display mapping
│       └── k7_lead.map                    ← Garage lead display mapping
├── shelly-scripts/
│   └── k7-failsafe.js                     ← On-device failsafe. Heartbeat watchdog, no voltage thresholds
└── docs/
    ├── K7_AUTO_POWER_README.md            ← Detailed auto-power documentation (BLE, lead gate, all 19 rules)
    └── FIRMWARE_ANALYSIS.md               ← K7 firmware reverse engineering
```

## Quick Start

### 1. Wire the switching circuit

See the circuit diagram above. Two halves:

**On the machine.** Mount the signal relay. Coil **+** and coil **−** go to the
two cores of the lead; the contact switches battery +12 V onto the camera's
**YELLOW** ignition wire. The camera's RED and BLACK stay exactly as they are —
its supply is permanent and is not part of this circuit.

**In the garage.** Controller relay COM to the 12 V PSU **+**, relay NO to core 1.
Core 2 returns through the **100 Ω** to PSU **−**, and the ADC input taps the
**coil side** of that resistor.

> The ADC must sit between the coil and the resistor, not between the resistor
> and PSU −. On the wrong side it shares a node with the supply return and can
> only ever read zero — a mistake that survived one drawing and got built.

**Install the 1N4007 diode** in the ignition wire before the splice point so the
switched line cannot back-feed the ignition circuit or the GPS tracker. (Anode on
the ignition side, cathode on the splice.)

**Settle the coil polarity on the bench**, before anything is soldered: 12 V
across the coil one way, listen; reverse it, listen again. Thirty seconds, and
worth more than a datasheet citation.

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

### Rules

**19 in two files**, counted from the source on 2026-09-12.

`vehicle-motorcycle-k7-power.js` — 14:

| # | Rule | Trigger |
|---|------|---------|
| 1 | System Init | Startup — relay OFF, state recovery |
| 2 | Ignition Handler | Ignition changed — 5 s debounce (1N4007 blocks back-feed) |
| 3 | Voltage Monitor | Tracker battery changed — low battery only; charger detection is BLE's job now |
| 4 | Dump Complete | Dump status changed — 30 s cooldown with the relay held ON, then OFF |
| 5 | Manual Override | Relay command — manual dump trigger |
| 6 | Shelly Status Poll | Cron 30 s — full status, and the heartbeat the on-device failsafe watches |
| 7 | Relay Tracker | Relay changed — timestamp, counter-punch on unexpected ON |
| 8 | BLE Online | BLE online changed — charger presence |
| 9 | BLE Charge State | Charge state changed — starts the sequence from PARKED |
| 10 | Connection Status | BLE + voltage/current — computes clamps-on-bike |
| 11 | **Lead Connected** | Lead OFF→ON — resumes a sequence the missing lead had parked |
| 12 | **Lead Disconnected** | Lead ON→OFF — aborts an in-flight dump, relay OFF |
| 13 | Relay Safety Watchdog | Cron 2 min — four guards |
| 14 | Baseline Learner | Charger current changed — self-calibrates the clamps delta |

`vehicle-motorcycle-k7-lead.js` — 5:

| # | Rule | Trigger |
|---|------|---------|
| 1 | Passive Sense | Cron 10 s — reads only while the relay is already closed |
| 2 | Probe When Charger Connects | Charger arrival — one probe, 60 s guard |
| 3 | Probe On Demand | REFRESH command |
| 4 | Automatic While Charging | Cron :07 and :37 — **only while the lead is believed out** |
| 5 | Test Button | Sitemap button — the only way to check at rest |

> **Rule 4 will not probe when the lead is already in.** A probe closes the
> relay, and with the lead connected that is also the signal that wakes the
> camera. With the lead out there is no circuit, so a probe is completely
> silent. It is free exactly when it has something to tell us.

### Watchdog guards

| Guard | Condition |
|---|---|
| 1 | Relay ON in PARKED, DUMP_DONE or LOW_BATTERY → force OFF |
| 2 | Relay ON past the absolute ceiling → force OFF. State-independent |
| 3 | Relay ON while the charger clamps are not proven on the bike → force OFF |
| 4 | Relay ON while the garage lead is not connected → force OFF |

> **COOLDOWN is deliberately absent from guard 1.** It is a state where the relay
> is held ON for 30 s so the camera can close its files. Listing it there made
> the watchdog fight that timer: 8 of 15 dumps were cut short, one to 7 seconds.
> Fixed 2026-09-12 and verified on hardware at 30.003 s.

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
| Coil-release fail-safe | Physical | Lose garage power, or unplug the lead, and the contact opens by itself |
| Lead interlock | Precondition | The dump cannot start unless the lead is proven connected, and aborts within 10 s if it is pulled |
| Local failsafe script | On the controller | Opens the relay if openHAB stops writing its heartbeat |
| Absolute ceiling | 45 min | Enforced on the device, independent of openHAB |

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
| `safety.min_nas_free_space_gb` | `10` | Abort dump if NAS free space drops below this |
| `safety.sd_card_total_gb` | `512` | Camera SD nominal capacity — drives the fill-% calc (change if you swap cards) |
| `safety.sd_card_low_warn_gb` | `8` | Warn when the camera SD free space drops below this |

## Documentation

- [Pi Software README](pi-software/README.md) — Detailed Pi setup, K7 API details, NAS structure, monitoring
- [Auto-Power Documentation](docs/K7_AUTO_POWER_README.md) — switching circuit, dual-sensor BLE integration, state machine, all rules
- [K7 Firmware Analysis](docs/FIRMWARE_ANALYSIS.md) — Reverse engineering of the K7 firmware (Novatek NA51055, RTL8821CS WiFi, CarDV HTTP API)
- [Novatek NT9666x WiFi Command User Guide](docs/NT9666x-WiFi-Command-User-Guide.pdf) — Official Novatek CarDV HTTP API reference (the `cmd=NNNN` commands used by this project, e.g. 3017 free space, 3024 card status)
- [Victron BLE Monitor](https://github.com/Prinsessen/victron-ble-openhab) — Standalone BLE daemon for the Victron charger

## Related Projects

- **[victron-ble-openhab](https://github.com/Prinsessen/victron-ble-openhab)** — BLE GATT monitor for Victron Blue Smart IP65 charger. Primary charger detection sensor for this project.

## License

MIT License — see [LICENSE](LICENSE).

## Credits

**Author:** Nanna Agesen ([@Prinsessen](https://github.com/Prinsessen)) — Nanna@agesen.dk

**BLE Protocol:** Based on reverse engineering by [Olen](https://github.com/Olen) (VictronConnect / phoenix.py).

Built for a motorcycle with an INNOVV K7 dashcam, automated with openHAB, Shelly, and a Raspberry Pi.
