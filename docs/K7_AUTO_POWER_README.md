# INNOVV K7 Auto-Power — Shelly Plus Uni Integration

Automated K7 dashcam power control via Shelly Plus Uni and IRFP9140N P-channel MOSFET, triggered by battery charger detection. When the Victron charger is connected, the system powers the K7 on, waits for the Pi dump service to finish downloading footage, then powers the K7 off.

## Architecture

```
                          ┌─────────────────────────────────────────┐
                          │          openHAB 5.1.3 (10.0.5.21)     │
                          │                                         │
  Traccar FMM920 ────────>│  vehicle-motorcycle-k7-power.js         │
  (Vehicle10_Ignition)     │  ┌───────────────────────────────────┐ │
  Shelly ADC ─────────────>│  │ State Machine (10 JSRules)        │ │
  (MC_K7_Shelly_Voltage)   │  │                                   │ │
  Victron BLE ────────────>│  │ DUAL-SENSOR charger detection:    │ │
  (MC_Charger_BLE_Online)  │  │   PRIMARY:  BLE charger state     │ │
  (MC_Charger_State)       │  │   FALLBACK: Shelly ADC voltage    │ │
                           │  │                                   │ │
                           │  │ RIDING->CHARGING->TRANSFERRING    │ │
                           │  │ ->COOLDOWN->DUMP_DONE->PARKED     │ │
                           │  │ LOW_BATTERY (emergency)           │ │
  K7_Dump_Status ─────────>│  └───────────┬───────────────────────┘ │
  (from Pi 4 REST API)     │              │ sendCommand(ON/OFF)     │
                           │              │ HTTP poll (SSID/RSSI)   │
                           └──────────────┼─────────────────────────┘
                                          │
                                          v Shelly Binding (native)
                              ┌───────────────────────────┐
                              │  Shelly Plus Uni           │
                              │  shellyplusuni-e08cfe8b1c3c│
                              │  IP: 10.0.5.62  FW: 1.7.4 │
                              │                            │
                              │  ADC (Voltmeter:100)       │
                              │    <- battery voltage      │
                              │  Relay1 output             │
                              │    -> IRFP9140N gate       │
                              │  WiFi: STA0=Devices (home) │
                              │        STA1=AgesenAP (mob) │
                              │  BLE: disabled             │
                              │  Script: K7 Failsafe (mJS) │
                              └───────────┬────────────────┘
                                          │ Relay drives MOSFET gate
                                          v
                              ┌───────────────────────────┐
                              │  IRFP9140N P-ch MOSFET    │
                              │  High-side switch          │
                              │  10K pull-up (fail-safe)   │
                              │  Source: Battery +12V      │
                              │  Drain: K7 DC+ input       │
                              └───────────┬────────────────┘
                                          │ Switched 12V
                                          v
                              ┌───────────────────────────┐
                              │  INNOVV K7 Dashcam         │
                              │  Powers on -> WiFi AP      │
                              └───────────┬────────────────┘
                                          │ WiFi 5GHz
                                          v
                              ┌──── 5dBi RP-SMA antenna ───┐
                              │   (ceiling above motorcycle)│
                              └───────────┬────────────────┘
                                          │ RP-SMA extension
                                          │ cable RG174 (2m)
                                          │ through PG7 gland
                              ┌───────────┴────────────────┐
                              │  IP65 Enclosure (wall)      │
                              │  ┌────────────────────────┐ │
                              │  │ Pi 4 (10.0.5.60)       │ │
                              │  │ eth0: home LAN         │ │
                              │  │ ALFA AWUS036ACM (USB3) │ │
                              │  │ innovv-k7-dump.service │ │
                              │  │ victron-ble-monitor    │ │
                              │  │ Detects -> Downloads   │ │
                              │  │ -> Verifies -> Deletes │ │
                              │  │ -> Reports to openHAB  │ │
                              │  └────────────────────────┘ │
                              └────────────────────────────┘
```

## Problem Solved

The INNOVV K7 has no remote power control — it only powers on via the ignition switch. This means footage can only be dumped while riding (engine running). With the Shelly Plus Uni:

1. **Charger connected** → BLE confirms charging state (or Shelly ADC detects high voltage as fallback)
2. **Relay turns ON** → K7 powers up and broadcasts its WiFi AP
3. **Pi detects K7** → Downloads all new footage to NAS, verifies SHA-256, deletes from K7
4. **Dump complete** → Shelly relay turns OFF, K7 shuts down

No manual intervention needed. Footage is automatically backed up whenever the charger is plugged in.

## Dual-Sensor Charger Detection (v2 — 2026-03-12)

The system uses **two independent sensors** for charger detection, with BLE as the primary authority:

### PRIMARY: Victron BLE Charger State

A Pi 4 daemon (`victron-ble-monitor`) connects to the Victron Blue Smart IP65 12/10 charger via BLE GATT every 30 seconds, reading the actual charge state (Bulk/Absorption/Float/Storage/Idle/Off). This is 100% accurate — no voltage threshold guessing.

- **BLE Online + Charging** (Bulk/Absorption/Float/Recondition) = charger actively charging battery
- **BLE Online + Connected** (Storage/Idle) = charger has mains power, battery may be connected but fully charged
- **BLE Online + Off** = charger has mains power but battery cable not connected to bike
- **BLE Offline** = charger unplugged from mains (BLE radio shuts off)

**Three detection tiers** (used by post-ignition check, `startChargerSequence`, and stabilisation):
1. `isBLECharging()` — Active charging: Bulk/Absorption/Float/Recondition (highest confidence)
2. `isBLEConnected()` — Charger connected: all of above + Storage/Idle (charger sees a battery or is on mains)
3. Voltage > CHARGER_ON_V — Fallback when BLE is offline

See the [victron-ble-openhab](https://github.com/Prinsessen/victron-ble-openhab) repository for full BLE daemon documentation.

### FALLBACK: Shelly ADC Voltage Thresholds

When the BLE daemon is unavailable, the system falls back to Shelly ADC voltage:

- **V > 13.0V** (CHARGER_ON_V) → Charger connecting
- **V < 12.7V** (CHARGER_OFF_V) → Charger removed
- **12.7V – 13.0V** → Hysteresis zone, no state change

### Sensor Priority Logic

| Scenario | BLE | Voltage | Decision |
|----------|-----|---------|----------|
| BLE confirms charging | Bulk/Absorption/Float/Recondition | Any | **Start charger sequence** (BLE authoritative) |
| BLE confirms connected | Storage/Idle | Any | **Start charger sequence** (BLE connected — full battery or just clipped) |
| BLE online, not charging/connected | Off | > 13.0V | **Suppress** — charger has mains but state unclear |
| BLE offline | N/A | > 13.0V | **Fallback** — use voltage threshold |
| BLE offline | N/A | < 12.7V | **No charger** |
| Stabilisation check | BLE online + charging OR connected | Any | **Confirm** (BLE authority) |
| Stabilisation check | BLE online + NOT charging/connected | Any | **Reject** — false positive |
| Stabilisation check | BLE offline | > 13.0V | **Confirm** (voltage fallback) |

### Grace Period (Re-arm Suppression)

After a charger-active state re-arms to PARKED (battery disconnect, charger removal), battery voltage lingers above CHARGER_ON_V (13.0V) for several minutes. Without protection, this triggers a false charger detection loop.

**Solution:** A 5-minute grace period suppresses voltage-only charger detection after re-arm. BLE charging confirmation always overrides the grace period immediately.

```
Re-arm to PARKED at T=0 (voltage = 13.3V, dropping slowly)
T+0s:  V=13.3V > 13.0V → Suppressed (0min since re-arm, grace 5min)
T+15s: V=13.2V > 13.0V → Suppressed (0min since re-arm)
T+60s: V=13.1V > 13.0V → Suppressed (1min since re-arm)
T+120s: V=13.0V         → Below threshold, no trigger
...
T+300s: Grace period expires. Voltage-only detection re-enabled.
```

> **GraalJS Duration API note:** On openHAB 5.1.3 GraalJS, `java.time.Duration` only exposes `toMinutes()`. Neither `toSeconds()` (Java 9+) nor `getSeconds()` (Java 8) work. All duration checks use minutes granularity.

## Components

### Hardware

| Component | Model | Role | Location |
|-----------|-------|------|----------|
| Shelly Plus Uni | SNSN-0043X (Gen 2) | Relay + ADC voltage (Voltmeter:100) + WiFi | Mounted on motorcycle |
| IRFP9140N | P-channel MOSFET, -100V/-23A, TO-247 | High-side power switch for K7 | Inline in K7 wiring harness |
| 10K resistor | 1/4W, any tolerance | Gate pull-up (fail-safe OFF) | Soldered to MOSFET |
| 1N4007 diode | Silicon rectifier, 1A/1000V, 0.7V Vf | **INSTALLED** — Blocks MOSFET back-feed into ignition circuit | Inline on ignition wire before K7/MOSFET splice |
| INNOVV K7 | Dual-channel dashcam | Records front + rear video | Mounted on motorcycle |
| Teltonika FMM920 | GPS tracker | Ignition state + battery voltage | Mounted on motorcycle |
| Victron Blue Smart IP65 12/10 | BLE-enabled battery charger | Charges battery, PRIMARY charger detection via BLE | Garage |
| Pi 4 | Raspberry Pi 4 | K7 dump service + Victron BLE monitor | Garage IP65 enclosure (10.0.5.60) |
| ALFA AWUS036ACM | MT7612U, AC1200, USB 3.0 | 5GHz WiFi to K7 AP (mt76 in-kernel driver) | Inside IP65 enclosure with Pi |
| RP-SMA extension cable | RG174 coax, 2m, RP-SMA M→F | Antenna feed through IP65 enclosure | PG7 cable gland pass-through |
| 5dBi dual-band antenna | RP-SMA, included with ALFA | 5GHz reception from K7 | Ceiling-mounted above motorcycle |

### Software

| File | Purpose |
|------|---------|
| `items/motorcycle_k7_power.items` | 22 items: Shelly channels + BLE charger items + virtual state items |
| `things/shelly.things` | Shelly Plus Uni thing definition (IP: 10.0.5.62) |
| `automation/js/vehicle-motorcycle-k7-power.js` | State machine (10 JSRules): dual-sensor charger detection with BLE + voltage fallback |
| `innovv-k7/shelly-failsafe-script.js` | Local failsafe for Shelly (mJS, Script ID 1) — runs on-device when openHAB unavailable |
| `innovv-k7/pi-software/innovv_k7_dump.py` | Pi dump service (**NOT modified**) |
| `victron-ble/victron_ble_monitor.py` | Pi BLE daemon — reads Victron charger via GATT, posts to openHAB REST API |
| `sitemaps/k7-sitemap-extract.sitemap` | K7 Auto-Power + Shelly Status + Victron Charger frames (extract — K7 sections only) |

## State Machine

### States

| State | Relay | Description |
|-------|-------|-------------|
| **RIDING** | OFF | Ignition ON — K7 powered by ignition circuit |
| **CHARGING** | OFF | Charger detected (BLE or voltage), 60s stabilisation in progress |
| **TRANSFERRING** | ON | K7 powered, waiting for Pi file transfer to complete |
| **COOLDOWN** | OFF→ | Dump complete, 30s cooldown before relay OFF |
| **DUMP_DONE** | OFF | Dump cycle completed, ready for next ride — ignition ON allowed (→ RIDING) |
| **PARKED** | OFF | Normal parked state (no charger connected) |
| **LOW_BATTERY** | OFF | Battery < 12.0V — relay forced off |

### Transitions

```
         Ignition ON
    ┌────────────────────┐
    │                    ▼
 PARKED ──(charger)───► CHARGING ──(60s)──► TRANSFERRING
    ▲                    │                    │
    │            not confirmed                │
    │                    ▼              "complete"
    │                 PARKED                  ▼
    │                                    COOLDOWN ──(30s)──► DUMP_DONE
    │                                                     │         │
    │                                         BLE Idle/Off│         │Ignition ON
    │                                         or V<12.7V  │         │
    │                                                     ▼         ▼
    │◄──────────────────────────────────────────────── PARKED    RIDING
    │                                              (5min grace)    │
    │                                                              │
    │◄─────────────────────────────────(Ignition OFF)──────────────┘
    │   V<12.0V
    └── LOW_BATTERY ◄──── (any state)
```

**Charger detection** uses three tiers:
- **BLE charging** (Bulk/Absorption/Float/Recondition) → immediate start
- **BLE connected** (Storage/Idle) → immediate start (full battery goes straight to Storage)
- **Voltage > 13.0V** (fallback when no BLE) → start with grace period check

**Stabilisation** (60s) rechecks using the same three-tier priority:
- BLE online + charging OR connected → **confirmed**
- BLE online + NOT charging/connected → **rejected** (false positive)
- BLE offline + voltage > 13.0V → **confirmed** (fallback)

**DUMP_DONE → RIDING** when ignition turns ON:
- Charger cable removal is undetectable by BLE (charger stays on mains, reports Storage with no load)
- User unclips cable → ignition ON → rides. System allows DUMP_DONE → RIDING transition.

**DUMP_DONE re-arm** to PARKED triggers when:
- BLE state changes to Off (charger reports no battery) — **immediate, authoritative**
- BLE goes Offline (charger mains unplugged) — **immediate**
- Voltage drops < 12.7V AND BLE not actively charging — **fallback**

**Grace period** (5 minutes after any re-arm from charger-active state):
- Voltage-only detection suppressed (battery voltage lingers > 13.0V)
- BLE charging confirmation overrides grace period immediately

### Safety Features

| Feature | Value | Purpose |
|---------|-------|---------|
| Stabilisation delay | 60 seconds | Avoids false triggers from voltage spikes |
| Safety timeout | 30 minutes | Prevents indefinite relay ON (battery drain) |
| Low battery cutoff | 12.0V | Protects battery from deep discharge |
| Charger ON threshold | 13.0V (raw ADC) | Detect charger connecting (fallback) |
| Charger OFF threshold | 12.7V (raw ADC) | Confirm charger truly removed (fallback) |
| Ignition debounce | 5 seconds | Filters MOSFET back-feed false triggers to FMM920 (1N4007 diode installed 2026-03-12, debounce reduced from 30s to 5s) |
| Post-dump cooldown | 30 seconds | Clean K7 shutdown after dump |
| Re-arm grace period | 5 minutes | Suppresses voltage-only false retrigger after charger disconnect |
| BLE offline threshold | ~90 seconds | 3 consecutive BLE failures = charger mains removed |

## Installation

### 1. Physical Wiring

The Shelly relay drives an IRFP9140N P-channel MOSFET as a high-side switch. This replaces a direct relay connection for minimal size, zero mechanical wear, and near-zero power loss.

#### MOSFET Circuit (IRFP9140N — P-channel, TO-247)

```
  Battery +12V ── RED wire (3A inline fuse at battery)
     │
     ├────────────────> Shelly Plus Uni POWER (DC input)
     │
     ├────────────────> Shelly ADC input (Voltmeter:100, voltage sensing)
     │
     ├────────────────> FMM920 power (existing tracker)
     │
     ├────────────────> K7 permanent 12V (K7 RED wire) — direct, always on
     │
     ├──── IRFP9140N Source (pin 3)
     │         │
     │       10K resistor (pull-up: ensures MOSFET OFF when gate floats)
     │         │
     │     IRFP9140N Gate (pin 1) ──── 100 ohm ──── Shelly Relay1 COM
     │                                                    │
     │                                  Shelly Relay1 NO ─┤
     │                                                    │
     │                                               Battery GND
     │
     └── IRFP9140N Drain (pin 2) ── ORANGE wire ──┐
                                                   ├──> K7 ignition (K7 YELLOW wire)
  Motorcycle ignition 12V ──►|── 1N4007 ──────────┘
                            (1N4007: anode=ignition, cathode=K7/MOSFET splice)
                            (Blocks MOSFET back-feed to ignition circuit/FMM920)
                            (Installed 2026-03-12, verified working)
  
  Battery GND ── BLACK wire ────────────> K7 ground (K7 BLACK wire)
```

**Wiring Harness — Shelly/MOSFET Pack (3 wires to battery):**
| Wire | Color | From | To |
|------|-------|------|----|
| +12V supply | **RED** | Battery +12V (3A fuse) | Shelly power + MOSFET Source + Shelly ADC |
| Ground | **BLACK** | Battery GND | Shelly GND + Relay NO |
| Switched output | **ORANGE** | MOSFET Drain | K7 ignition (spliced to K7 YELLOW) |

**K7 Wiring Harness (3 wires — original from K7):**
| Wire | Color | Connection |
|------|-------|------------|
| Permanent 12V | **RED** | Direct to battery +12V (always on) |
| Ignition 12V | **YELLOW** | Spliced to **ORANGE** from MOSFET Drain |
| Ground | **BLACK** | Battery GND |

#### Quick Wiring Reference

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │                    IRFP9140N (TO-247)                               │
 │                   ┌──────────────┐                                  │
 │                   │  IRFP9140N   │  (face label towards you)        │
 │                   │              │                                  │
 │                   │  1    2    3 │                                  │
 │                   └──┬────┬────┬─┘                                  │
 │                      │    │    │                                    │
 │                    GATE DRAIN SOURCE                                │
 │                      │    │    │                                    │
 │                      │    │    │                                    │
 │  BATTERY (+12V) ═══════════════╪═══════════╗  (always-on, fused 3A)│
 │       ║              │    │    ║            ║                       │
 │       ║              │    │    ╠═══[10K]════╣                       │
 │       ║              │    │    ║  pull-up   ║                       │
 │       ║              │    │    ║  resistor  ║                       │
 │       ║              │    │  SOURCE         ║                       │
 │       ║              │    │         to Shelly Plus Uni:             │
 │       ║              │    │           ║──> DC Power (+)             │
 │       ║              │    │           ║──> ADC Input (voltage sense)│
 │       ║              │   DRAIN                                     │
 │       ║              │    │                                        │
 │       ║              │    └──────> ORANGE wire ──> K7 YELLOW (ign)  │
 │       ║              │                                             │
 │       ║            GATE                                            │
 │       ║              │                                             │
 │       ║           [100Ω]  (optional gate resistor)                 │
 │       ║              │                                             │
 │       ║              └──────────> Shelly Relay1 COM                │
 │       ║                                                            │
 │       ║                           Shelly Relay1 NO ──┐             │
 │       ║                                              │             │
 │  BATTERY GND ════════════════════════════════════════╧═══════════╗ │
 │       ║                                                          ║ │
 │       ║              to Shelly Plus Uni:                         ║ │
 │       ║                ║──> DC Power (-)                         ║ │
 │       ║                                                          ║ │
 │       ╠══════════════════════════> K7 Ground (K7 BLACK wire)     ║ │
 │       ║                                                          ║ │
 │       ║              to K7:                                      ║ │
 │       ║                ══════════> K7 Permanent (K7 RED wire)    ║ │
 │       ║                           (direct from Battery +12V)    ║ │
 │                                                                  ║ │
 └──────────────────────────────────────────────────────────────────║─┘

 STEP-BY-STEP WIRING ORDER:
 ══════════════════════════
 1. MOSFET Source (pin 3) ──── Battery +12V (RED wire, 3A fuse at battery)
 2. 10K resistor ──────────── between Source (pin 3) and Gate (pin 1)
 3. 100Ω resistor ─────────── from Gate (pin 1) to Shelly Relay1 COM
 4. Shelly Relay1 NO ──────── Battery GND (BLACK wire)
 5. MOSFET Drain (pin 2) ──── ORANGE wire ──> splice to K7 YELLOW (ignition)
 6. Battery +12V ──────────── K7 Permanent (K7 RED wire) — direct, no MOSFET!
 7. Battery GND ───────────── K7 Ground (K7 BLACK wire)
 8. Battery +12V ──────────── Shelly DC power (+) (from RED wire)
 9. Battery GND ───────────── Shelly DC power (-) (from BLACK wire)
 10. Battery +12V ─────────── Shelly ADC input (voltage sensing)

 WIRE COLORS FROM SHELLY/MOSFET PACK:
   RED    = +12V from battery (fused 3A)   — to Shelly + MOSFET Source
   BLACK  = GND from battery               — to Shelly + Relay NO
   ORANGE = MOSFET Drain output            — to K7 ignition (K7 YELLOW)

 K7 ORIGINAL WIRES:
   RED    = permanent +12V                 — direct to battery (unchanged)
   YELLOW = ignition +12V                  — spliced to ORANGE from MOSFET
   BLACK  = ground                         — direct to battery (unchanged)

 RELAY OFF  = Gate at +12V (via 10K) = MOSFET OFF = no ignition = K7 off
 RELAY ON   = Gate at GND            = MOSFET ON  = ignition on = K7 on
```

#### How it works

| Relay State | Gate Voltage | Vgs | MOSFET | K7 Power |
|-------------|-------------|-----|--------|----------|
| **OPEN** (OFF) | Pulled to +12V via 10K | 0V | **OFF** | No power |
| **CLOSED** (ON) | Pulled to GND via relay | -12V | Fully **ON** | Powered |

- **Relay OPEN**: 10K pull-up ties gate to Source (+12V). Vgs = 0V. MOSFET off. K7 has no power.
- **Relay CLOSED**: Gate pulled to GND through relay contact. Vgs = -12V. MOSFET fully on (Rdson = 0.2 ohm). K7 powered.
- **Fail-safe**: If Shelly loses power or crashes, relay opens, 10K pull-up holds gate high, MOSFET stays OFF. K7 stays off. Battery is safe.

#### Component Notes

| Component | Spec | Note |
|-----------|------|------|
| **IRFP9140N** | P-channel, -100V, -23A, Rdson 0.2 ohm, TO-247 | Massively over-rated for K7's ~0.5A. No heatsink needed. |
| **10K pull-up** | 1/4W, any tolerance | Gate-to-Source. Critical for fail-safe OFF. Without it, gate floats and MOSFET can randomly turn on from motorcycle electrical noise. |
| **100 ohm gate resistor** | 1/4W, optional | Limits inrush to gate capacitance (~3000pF). Good practice but not strictly required at these current levels. |

#### MOSFET Pinout (IRFP9140N, TO-247, facing label)

```
        ┌──────────┐
        │ IRFP9140N│
        │  TO-247  │
        │          │
        │ 1  2  3  │
        └──┬──┬──┬─┘
           │  │  │
         Gate │ Source
             Drain
```

#### Wiring Tips

- **Heat-shrink** the MOSFET + resistors assembly and zip-tie to the wiring harness
- Use **always-on** battery circuit (not ignition-switched) for Shelly power
- **Fuse** the Shelly power line at 3A
- The Shelly ADC input is safe for 12V motorcycles (max 30V, no voltage divider needed)
- Route MOSFET inline between Shelly and K7, as close to K7 connector as practical
- Keep gate wiring short to minimize noise pickup

### 2. Shelly WiFi Configuration

The Shelly has dual WiFi for home and mobile connectivity:

| Network | SSID | Purpose |
|---------|------|---------|
| STA0 (primary) | Devices | Home WiFi — openHAB control |
| STA1 (failover) | AgesenAP | Phone hotspot — remote relay control when away from home |

**BLE** is disabled to reduce parasitic current draw (~5-10mA saved). Use STA1 (phone hotspot) for remote control when away from home.

To reconfigure WiFi:
1. Power on the Shelly (connect battery or charger)
2. If no WiFi available, Shelly creates AP: `ShellyPlusUni-E08CFE8B1C3C`
3. Connect phone to Shelly AP, open `192.168.33.1`
4. Configure WiFi credentials

### 3. openHAB Thing

The Shelly Plus Uni thing is defined in `things/shelly.things`:

```openhab
Thing shelly:shellyplusuni:e08cfe8b1c3c "Shelly Plus Uni (Motorcycle K7)" @ "Garage" [
    deviceIp="10.0.5.62"
]
```

**Thing UID**: `shelly:shellyplusuni:e08cfe8b1c3c`
**Status**: ONLINE (Shelly binding, native — not MQTT)

### 4. Shelly Voltmeter Peripheral

The ADC voltage reading uses Shelly's Voltmeter peripheral, added via RPC:

```
Uni.AddPeripheral { type: "voltmeter" }   -> voltmeter:100
Voltmeter.GetStatus { id: 100 }           -> { voltage: 12.18 }
```

This is exposed to openHAB via the `sensors#voltage` channel.

### 5. ADC Voltage Calibration

The Shelly Plus Uni ADC (Voltmeter:100) reads ~2-3% low compared to a Fluke reference,
but the offset is **non-linear** — it varies with voltage level.

**Calibration performed 2026-03-11 (on bike with Fluke 175 reference):**

| Condition | Fluke (real) | Shelly raw ADC | Offset |
|-----------|-------------|----------------|--------|
| Battery only (charger off) | 12.77 V | 12.37 V | +0.40 V |
| Charger connected (Victron BSC IP65) | 13.70 V | 13.55 V | +0.15 V |

The offset changes from +0.40V at ~12.4V to +0.15V at ~13.5V — a linear `system:offset`
profile cannot correct both ends accurately.

**Decision: No offset correction.** All thresholds use **raw ADC values** directly:

| Threshold | Raw ADC value | Purpose |
|-----------|--------------|---------|
| `CHARGER_ON_V` (JS rule) | **13.0 V** | Charger detection — voltage must rise above this to start CHARGING |
| `CHARGER_OFF_V` (JS rule) | **12.7 V** | Charger removal — voltage must drop below this to confirm charger disconnected |
| `chargerOnVoltage` (Shelly failsafe) | **13.0 V** | Same ON threshold on-device |
| `chargerOffVoltage` (Shelly failsafe) | **12.7 V** | Same OFF threshold on-device |
| `LOW_BATT_V` (JS rule) | **12.0 V** | Emergency low battery cutoff |
| `lowBattVoltage` (Shelly failsafe) | **11.5 V** | Emergency cutoff (on-device) |

> **Note:** Voltage thresholds are FALLBACK only. When BLE is online, charger detection
> uses BLE charge state as the primary authority. Voltage-only detection is suppressed
> by the 5-minute grace period after re-arm to prevent false triggers.

### 5a. Victron Charger Stages & Impact on K7 Auto-Dump

The Victron Blue Smart IP65 12/10 battery charger follows a multi-stage charging profile:

#### Charging Stages

| Stage | Voltage (real) | Voltage (Shelly ADC) | BLE State | Duration |
|-------|---------------|---------------------|-----------|----------|
| **Bulk** | 14.4–14.7 V | ~14.2–14.5 V | Bulk | Hours |
| **Absorption** | ~14.4 V | ~14.2 V | Absorption | 1–3 hours |
| **Float** | ~13.8 V | ~13.6 V | Float | Hours |
| **Storage** | ~13.2 V | ~13.0 V | Storage | Indefinite |
| **Idle** | — | — | Idle | Cable off bike |
| **Off** | — | — | Off | Mains unplugged |

#### Impact on K7 Auto-Dump

With BLE integration, charger stage transitions no longer cause false triggers. The BLE
daemon reports the exact charge state, and the state machine trusts BLE over voltage.

**Without BLE (voltage-only fallback):** Storage stage (~13.0V ADC) sits right at the
CHARGER_ON_V threshold. Without hysteresis, this caused endless dump cycling.

**With BLE (primary):** BLE confirms the charger is in Storage — state machine stays in
DUMP_DONE regardless of voltage fluctuations. Re-arm only when BLE reports Idle/Off
(battery disconnected) or BLE goes offline (mains removed).

### 5b. Ignition Back-Feed Problem (MOSFET → FMM920)

**Problem discovered 2026-03-11:** The MOSFET drain (ORANGE wire) is spliced to the
K7 YELLOW ignition wire, which connects back through the bike's ignition circuit
to other devices — including the Teltonika FMM920 DIN1 (ignition sense input).

When the Shelly relay turns ON the MOSFET for auto-power, 12V back-feeds from the
MOSFET drain through the ignition wire to the FMM920. The FMM920 reports ignition=ON
to Traccar, which triggers false "Springfield Started" / "Springfield Parked" email
notifications every time the K7 auto-power cycles.

**Hardware fix: 1N4007 blocking diode** — Installed 2026-03-12 in the ignition wire
BEFORE the splice point. Blocks MOSFET back-feed to FMM920. Anode on ignition side, cathode on K7/MOSFET splice.
Debounce reduced from 30s to 5s after diode installation — verified working, no false triggers.

### 6. Failsafe Script (on-device)

The failsafe script runs directly on the Shelly (mJS engine), providing basic K7 power control when openHAB is unreachable:

- **Script ID**: 1 ("K7 Failsafe")
- **Status**: enabled, running
- **Upload**: Via Shelly RPC (`Script.PutCode`) — all non-ASCII characters must be stripped before upload

The failsafe yields to openHAB `sendCommand()` when LAN control is available.

### 7. Verify

1. Connect charger and watch `MC_K7_Power_State`: `PARKED -> CHARGING -> TRANSFERRING`
2. Check logs: `tail -f /var/log/openhab/openhab.log | grep k7_power`
3. Pi should detect K7 WiFi and start transferring footage
4. After transfer: `TRANSFERRING -> COOLDOWN -> DUMP_DONE`

## Items Reference

### Shelly Binding Channels (6 items)

| Item | Type | Channel | Purpose |
|------|------|---------|---------|
| `MC_K7_Relay` | Switch | `relay1#output` | Shelly relay (drives MOSFET gate) |
| `MC_K7_Shelly_Voltage` | Number:ElectricPotential | `sensors#voltage` | ADC battery voltage (Voltmeter:100, raw — no offset) |
| `MC_K7_Shelly_WiFi_Signal` | Number | `device#wifiSignal` | WiFi signal strength (0-4 bars) |
| `MC_K7_Shelly_Uptime` | Number:Time | `device#uptime` | Seconds since Shelly power-on |
| `MC_K7_Shelly_LastUpdate` | DateTime | `sensors#lastUpdate` | Last state change timestamp |
| `MC_K7_Shelly_Heartbeat` | DateTime | `device#heartBeat` | Last API response |

### API-Polled Items (via Rule 5, every 60s) (2 items)

| Item | Type | Source | Purpose |
|------|------|--------|---------|
| `MC_K7_Shelly_SSID` | String | `Wifi.GetStatus` | Connected WiFi name |
| `MC_K7_Shelly_RSSI` | Number | `Wifi.GetStatus` | WiFi signal in dBm |

### Virtual State Items (no channel binding) (4 items)

| Item | Type | Purpose |
|------|------|---------|
| `MC_K7_Power_State` | String | State machine current state |
| `MC_Charger_Detected` | Switch | Charger detected flag |
| `MC_K7_Power_Reason` | String | Human-readable reason for state |
| `MC_K7_Relay_Since` | DateTime | When relay was last turned ON |

### Victron BLE Charger Items (via Pi daemon REST API) (8 items)

| Item | Type | Purpose |
|------|------|---------|
| `MC_Charger_BLE_Online` | Switch | BLE connection status (ON/OFF) |
| `MC_Charger_Connection` | String | Human-readable: "Offline" / "Standby (cable detached)" / "Charging — \<stage\>" |
| `MC_Charger_Voltage` | Number | Battery voltage from charger (V) |
| `MC_Charger_Current` | Number | Charge current (A) |
| `MC_Charger_Current_mA` | Number | Charge current (mA) |
| `MC_Charger_Yield` | Number | Charged energy today (kWh) |
| `MC_Charger_State` | String | Charge state: Off/Idle/Bulk/Absorption/Float/Storage/Recondition |
| `MC_Charger_Last_Update` | DateTime | Last successful BLE data update |

### Charge Session Items (via Pi BLE daemon REST API) (9 items)

| Item | Type | Purpose |
|------|------|---------|
| `MC_Charge_Session_Start` | DateTime | Session start time |
| `MC_Charge_Session_End` | DateTime | Session end time |
| `MC_Charge_Session_Minutes` | Number | Session duration (minutes) |
| `MC_Charge_Session_V_Start` | Number | Battery voltage at session start (V) |
| `MC_Charge_Session_V_End` | Number | Battery voltage at session end (V) |
| `MC_Charge_Session_V_Delta` | Number | Voltage change during session (V) |
| `MC_Charge_Session_Wh` | Number | Energy charged during session (Wh) |
| `MC_Charge_Session_Peak` | String | Peak charge stage reached |
| `MC_Charge_Session_Count` | Number | Total charge sessions count |

**Total: 30 items** (1 group + 29 items) in `items/motorcycle_k7_power.items`

## Rules Reference (10 JSRules)

| # | Rule | Trigger | Action |
|---|------|---------|--------|
| 1 | System Init | System startup (level 100) | Relay OFF, voltage-aware + BLE-aware state selection, `updateChargerConnection()` |
| 2 | Ignition Handler | `Vehicle10_Ignition` changed | 5s debounce (1N4007 diode installed), MOSFET back-feed suppression during TRANSFERRING/COOLDOWN, DUMP_DONE → RIDING allowed |
| 3 | Voltage Monitor | `MC_K7_Shelly_Voltage` changed | Charger detect (>13.0V), charger removed (<12.7V, only when BLE not charging), low battery (<12.0V) |
| 4 | Dump Complete | `K7_Dump_Status` changed | Cooldown (30s) then relay OFF → DUMP_DONE |
| 5 | Shelly WiFi Poll | Cron every minute | HTTP GET to Shelly API, updates SSID + RSSI |
| 6 | Relay State Tracker | `MC_K7_Relay` changed | Updates `MC_K7_Relay_Since` on ON, clears on OFF |
| 7 | Manual Relay Override | `MC_K7_Relay` command ON | Manual relay ON triggers dump from DUMP_DONE or PARKED |
| 8 | BLE Charger Online | `MC_Charger_BLE_Online` changed | BLE ON: start charger sequence if PARKED + charging. BLE OFF: re-arm if DUMP_DONE, cancel if CHARGING |
| 9 | BLE Charge State | `MC_Charger_State` changed | Off → re-arm from DUMP_DONE. Bulk/Absorption/Float/Storage/Idle → start sequence from PARKED |
| 10 | Charger Connection Status | `MC_Charger_BLE_Online` or `MC_Charger_State` changed | Computes `MC_Charger_Connection` string |

### Key Helper Functions

| Function | Purpose |
|----------|---------|
| `isBLEOnline()` | Charger has mains power (BLE radio active + fresh data < 3min) |
| `isBLECharging()` | Charger actively charging battery (Bulk/Absorption/Float/Recondition + fresh) |
| `isBLEConnected()` | Charger connected to battery or on mains (Bulk/Absorption/Float/Storage/Recondition/Idle + fresh) |
| `isBLEFresh()` | BLE data updated within last 3 minutes (uses `toMinutes()`) |
| `getBLEState()` | Returns BLE charge state string or null |
| `getBLEInfo()` | Returns formatted BLE info string for log enrichment |
| `updateChargerConnection()` | Computes "Offline" / "Standby (cable detached)" / "Charging — \<stage\>" |
| `rearmToParked(reason)` | Sets PARKED + stores grace period timestamp |
| `startChargerSequence(voltage, source)` | Grace period check → CHARGING → stabilisation timer |
| `cancelTimer(name)` | Safe timer cancel from private cache |

## K7 Dump Status Values

These are reported by the Pi dump service via REST API (not modified):

| Status | Meaning |
|--------|---------|
| `idle` | Pi waiting for K7 WiFi |
| `k7_detected` | K7 SSID found, connecting |
| `connected` | WiFi connected to K7 |
| `scanning` | Listing files on K7 |
| `dumping (N/M)` | Downloading file N of M |
| `deleting from K7` | Removing verified files |
| `complete` | All files transferred and verified |
| `complete (no new files)` | K7 found but nothing new |
| `error: ...` | Various error conditions |
| `offline` | Service stopped |

## Shelly Failsafe Script

The local failsafe (`innovv-k7/shelly-failsafe-script.js`) runs on the Shelly's mJS engine:

| Parameter | Value | Purpose |
|-----------|-------|---------|
| Check interval | 30s | ADC polling frequency |
| Charger ON threshold | 13.0V (raw) | Detect charger connecting (matches JS rule) |
| Charger OFF threshold | 12.7V (raw) | Confirm charger removed |
| Stabilisation | 3 checks (90s) | Confirm charger is stable |
| Max ON time (auto) | 25 min | Shorter than openHAB's 30 min — so openHAB timer takes priority |
| Max ON time (manual) | 60 min | External relay toggle (Shelly app/cloud/physical) |
| Low battery cutoff | 11.5V | Emergency protection — applies in ALL modes |
| Voltmeter ID | 100 | Peripheral added via `Uni.AddPeripheral` |

The failsafe has a **shorter auto timeout** (25 min vs 30 min) so openHAB's safety timer takes priority if both are running.

### Manual Mode Detection (v4 — 2026-03-15)

The failsafe detects external relay toggles (Shelly app, cloud, API, physical button) via a `Shelly.addStatusHandler()` callback. When relay ON is detected from a source other than the script's own `relayControl()`:

- Sets `isManualOn = true`
- Starts 60-minute safety timer (generous for browsing K7 footage via WiFi)
- **Skips charger-removal voltage shutoff** (no charger present when riding/stopped away from home — battery ~12.5V would trigger the 12.7V threshold within 30s)
- Low battery cutoff (< 11.5V) still applies regardless

This covers the edge case where the user toggles the relay via the Shelly app through their phone hotspot (STA1) when openHAB is unreachable — e.g., stopped on a ride wanting to browse footage. Without this, the relay would stay ON indefinitely since openHAB Rule 7 (Manual Override) never fires.

## Test Results (2026-03-12)

All scenarios tested live with real hardware:

| Test | Expected | Result | Details |
|------|----------|--------|---------|
| Battery + charger plug-in | BLE Absorption → CHARGING → dump cycle | **PASS** | 14.4V/0.8A, full cycle to DUMP_DONE |
| Battery reconnect (charger on mains) | BLE Float → CHARGING → dump cycle | **PASS** | 13.83V/0.6A, ignition back-feed filtered |
| Battery detach (charger on mains) | BLE Idle → PARKED + grace suppression | **PASS** | 2 voltage-only triggers suppressed (13.3V, 13.2V) |
| Mains unplug (battery detached) | BLE Offline → "Offline" | **PASS** | ~90s detection time (3 missed polls) |
| Full reconnect (mains + battery from cold) | Offline → CHARGING → dump cycle | **PASS** | Voltage trigger first, BLE confirmed Absorption |
| Grace period suppression | V > 13.0V after re-arm suppressed | **PASS** | 0-2 min since re-arm, all suppressed |
| `MC_Charger_Connection` status | Correct for all 3 states | **PASS** | "Offline", "Standby (cable detached)", "Charging — Float/Absorption" |
| Ignition back-feed during TRANSFERRING | Suppressed (MOSFET relay ON) | **PASS** | 5s debounce + state check (1N4007 diode installed) |
| Manual relay override | Starts dump from PARKED/DUMP_DONE | **PASS** | Accidental toggle handled correctly |
| Shelly manual mode detection | External relay ON detected, 60-min timer | **PASS** | `Shelly.addStatusHandler()` fires, `isManualOn=true` |
| Manual mode charger-removal skip | Relay stays ON on battery-only (~12.5V) | **PASS** | No false shutoff at 12.7V threshold |
| GraalJS Duration API | `toMinutes()` works, `toSeconds()`/`getSeconds()` fail | **VERIFIED** | Both isBLEFresh and grace period use toMinutes |

## Current Draw & Battery Drain Analysis

### Always-on parasitic draw

| Component | State | Current Draw | Power @ 12V |
|-----------|-------|-------------|-------------|
| Shelly Plus Uni | Idle (WiFi connected) | ~70-90 mA | ~0.85-1.1 W |
| Shelly Plus Uni | Idle (WiFi searching) | ~100-120 mA | ~1.2-1.4 W |
| Shelly Plus Uni | Relay ON + script running | ~100-130 mA | ~1.2-1.6 W |
| IRFP9140N MOSFET | Gate off (leakage) | <1 uA | Negligible |
| IRFP9140N MOSFET | Gate on, 0.5A load | Rdson loss only | ~0.05 W |
| 10K pull-up resistor | Always (12V/10K) | 1.2 mA | 0.014 W |
| Teltonika FMM920 | Deep sleep | ~3-5 mA | ~0.04-0.06 W |
| Teltonika FMM920 | Active GPS/GPRS | ~100-150 mA | ~1.2-1.8 W |

### Drain scenarios

| Scenario | Total Draw | Time to Drain* |
|----------|-----------|---------------|
| Parked at home (Shelly on WiFi, FMM920 sleep) | ~75-95 mA | ~8-10 days |
| Parked away (Shelly searching WiFi, FMM920 sleep) | ~105-125 mA | ~6-7 days |
| Dump in progress (relay ON, K7 + Shelly + Pi active) | ~600-800 mA | ~1-1.3 days |

*Estimated from a typical motorcycle battery (12V, 8-12 Ah).

## Troubleshooting

### K7 doesn't power on
- Check Shelly relay output with multimeter
- Verify `MC_K7_Relay` shows ON in openHAB
- Check Shelly thing status (ONLINE?)
- Verify wiring: relay output → K7 DC input

### Charger not detected
- **BLE:** Check `MC_Charger_BLE_Online` — should be ON when charger has mains
- **BLE daemon:** `ssh pi@10.0.5.60 'journalctl -u victron-ble-monitor -f --no-pager'`
- **Voltage fallback:** Check `MC_K7_Shelly_Voltage` — should be >13.0V when charger connected
- **Grace period:** If within 5 min of last re-arm, voltage-only triggers are suppressed (check logs for "Suppressed" messages)
- Check raw Shelly ADC via `http://10.0.5.62/rpc/Voltmeter.GetStatus?id=100`

### Dump doesn't start
- Pi dump service is independent — check `systemctl status innovv-k7-dump` on Pi
- Verify K7 WiFi AP is broadcasting (`iw dev wlan0 scan` on Pi)
- The K7 may need 30-60s to fully boot and start WiFi

### Relay stays ON too long
- Safety timeout (30 min) will force OFF
- Check `K7_Dump_Status` — if stuck on "dumping", Pi may have lost K7 WiFi
- Manual override: send OFF command to `MC_K7_Relay`

### BLE shows "Offline" but charger is plugged in
- Check BLE pairing: `bluetoothctl info EB:A8:21:DD:9C:A0` on Pi
- Restart daemon: `sudo systemctl restart victron-ble-monitor`
- Check Pi connectivity: `ping 10.0.5.60`

## Monitoring

Watch the state machine in real-time:

```bash
# openHAB log (filtered to K7 rules)
tail -f /var/log/openhab/openhab.log | grep k7_power

# BLE daemon log on Pi
ssh pi@10.0.5.60 'journalctl -u victron-ble-monitor -f --no-pager'

# All K7 items
curl -s http://10.0.5.21:8080/rest/items?tags=Status | python3 -c "
import sys, json
items = json.load(sys.stdin)
for i in items:
    if i['name'].startswith('MC_K7') or i['name'].startswith('MC_Charger') or i['name'].startswith('K7_'):
        print(f'{i[\"name\"]}: {i.get(\"state\",\"?\")}')
"
```

## Changelog

### v4 — 2026-03-15: Manual Mode Safety + Diode Debounce Reduction
- **Shelly failsafe manual mode**: `Shelly.addStatusHandler()` detects external relay toggles from any source (app, cloud, API)
- **60-minute manual timeout**: Generous limit for browsing K7 footage via WiFi when stopped on a ride
- **Charger-removal skip in manual mode**: Battery ~12.5V would trigger the 12.7V threshold within 30s — now only low battery cutoff (11.5V) applies in manual mode
- **1N4007 diode installed**: Blocks MOSFET back-feed into ignition circuit/FMM920 (was causing false ignition=ON reports)
- **Ignition debounce reduced**: 30s → 5s (diode eliminates back-feed, minimal debounce sufficient for electrical noise)
- **Charge session tracking**: 9 new items for session start/end times, voltage delta, energy charged, peak stage
- **Total items**: 20 → 30 (1 group + 29 items)
- **Sitemap**: Full house sitemap replaced with K7-only extract (sensitive data removed from public repo)
- **IMEI removed**: FMM920 tracker IMEI scrubbed from documentation

### v3 — 2026-03-12: Storage/Idle Detection + DUMP_DONE Ride Allowance
- **`isBLEConnected()` function**: New middle tier — includes Storage/Idle (charger on mains, battery may be full)
- **Three-tier charger detection**: `isBLECharging()` → `isBLEConnected()` → voltage fallback
- **Post-ignition 3-tier check**: After ignition OFF, detects Storage (full battery on charger) not just active charging
- **DUMP_DONE → RIDING**: Ignition ON now allowed from DUMP_DONE (charger cable removal undetectable by BLE)
- **Rule 9 expanded**: Storage/Idle in PARKED now triggers dump sequence (full battery goes straight to Storage)
- **`startChargerSequence` gate**: Accepts `isBLEConnected()` — no longer rejects Storage/Idle
- **Stabilisation confirmation**: Accepts BLE connected states (Storage/Idle), not just active charging
- **Reason text**: "waiting for charger removal" → "ready for next ride" (matches real-world workflow)

### v2 — 2026-03-12: Dual-Sensor BLE Integration
- **Dual-sensor charger detection**: BLE primary, voltage fallback
- **10 JSRules** (was 7): added BLE Online (R8), BLE Charge State (R9), Charger Connection Status (R10)
- **`isBLEOnline()` vs `isBLECharging()`**: Distinguishes "mains power" from "actively charging"
- **`MC_Charger_Connection` item**: Human-readable "Offline" / "Standby (cable detached)" / "Charging — \<stage\>"
- **Grace period**: 5-min voltage-only suppression after re-arm (prevents false retrigger)
- **GraalJS Duration fix**: `toMinutes()` only (`.toSeconds()` and `.getSeconds()` broken in GraalJS)
- **Stabilisation BLE authority**: Stabilisation check uses BLE when online, rejects if BLE online + not charging
- **DUMP_DONE re-arm**: BLE Idle/Off triggers immediate re-arm to PARKED

### v1 — 2026-03-11: Initial Release
- State machine with 7 JSRules
- Voltage-only charger detection with hysteresis
- MOSFET circuit, Shelly failsafe, ADC calibration
- Ignition debounce for MOSFET back-feed
