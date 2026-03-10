# INNOVV K7 Auto-Power — Shelly Plus Uni Integration

Automated K7 dashcam power control via Shelly Plus Uni and IRFP9140N P-channel MOSFET, triggered by battery charger detection. When the Vitronic charger is connected, the system powers the K7 on, waits for the Pi dump service to finish downloading footage, then powers the K7 off.

## Architecture

```
                          ┌─────────────────────────────────────────┐
                          │          openHAB 5.1.3 (192.168.1.10)     │
                          │                                         │
  Traccar FMM920 ────────>│  vehicle-motorcycle-k7-power.js         │
  (Vehicle10_Ignition)     │  ┌───────────────────────────────────┐ │
  (Vehicle10_Power)        │  │ State Machine (5 JSRules)         │ │
                           │  │ RIDING->CHARGING->DUMPING->COOLDOWN │
                           │  │ <-PARKED<-LOW_BATTERY             │ │
  K7_Dump_Status ─────────>│  └───────────┬───────────────────────┘ │
  (from Pi 4 REST API)     │              │ sendCommand(ON/OFF)     │
                           │              │ HTTP poll (SSID/RSSI)   │
                           └──────────────┼─────────────────────────┘
                                          │
                                          v Shelly Binding (native)
                              ┌───────────────────────────┐
                              │  Shelly Plus Uni           │
                              │  shellyplusuni-xxxxxxxxxxxx│
                              │  IP: 192.168.1.62  FW: 1.7.4 │
                              │                            │
                              │  ADC (Voltmeter:100)       │
                              │    <- battery voltage      │
                              │  Relay1 output             │
                              │    -> IRFP9140N gate       │
                              │  WiFi: STA0=YourHomeSSID (home) │
                              │        STA1=YourMobileHotspot (mob) │
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
                              ┌───────────────────────────┐
                              │  Pi 4 (192.168.1.60)         │
                              │  innovv-k7-dump.service    │
                              │  Detects -> Downloads      │
                              │  -> Verifies -> Deletes    │
                              │  -> Reports to openHAB     │
                              └────────────────────────────┘
```

## Problem Solved

The INNOVV K7 has no remote power control — it only powers on via the ignition switch. This means footage can only be dumped while riding (engine running). With the Shelly Plus Uni:

1. **Charger connected** → Shelly detects high voltage (>13.2V) with ignition OFF
2. **Relay turns ON** → K7 powers up and broadcasts its WiFi AP
3. **Pi detects K7** → Downloads all new footage to NAS, verifies SHA-256, deletes from K7
4. **Dump complete** → Shelly relay turns OFF, K7 shuts down

No manual intervention needed. Footage is automatically backed up whenever the charger is plugged in.

## Components

### Hardware

| Component | Model | Role | Location |
|-----------|-------|------|----------|
| Shelly Plus Uni | SNSN-0043X (Gen 2) | Relay + ADC voltage (Voltmeter:100) + WiFi | Mounted on motorcycle |
| IRFP9140N | P-channel MOSFET, -100V/-23A, TO-247 | High-side power switch for K7 | Inline in K7 wiring harness |
| 10K resistor | 1/4W, any tolerance | Gate pull-up (fail-safe OFF) | Soldered to MOSFET |
| INNOVV K7 | Dual-channel dashcam | Records front + rear video | Mounted on motorcycle |
| Teltonika FMM920 | GPS tracker (000000000000000) | Ignition state + battery voltage | Mounted on motorcycle |
| Vitronic charger | Battery charger (~14.4V output) | Charges battery, triggers dump | Garage |
| Pi 4 | Raspberry Pi 4 | K7 footage dump service | Garage (192.168.1.60) |

### Software

| File | Purpose |
|------|---------|
| `items/motorcycle_k7_power.items` | 14 items: Shelly channels (relay, voltage, WiFi, uptime, heartbeat) + API-polled (SSID, RSSI) + virtual state items |
| `things/shelly.things` | Shelly Plus Uni thing definition (IP: 192.168.1.62) |
| `automation/js/vehicle-motorcycle-k7-power.js` | State machine (5 JSRules): init, ignition, voltage, dump complete, WiFi poll |
| `innovv-k7/shelly-failsafe-script.js` | Local failsafe for Shelly (mJS, Script ID 1) — runs on-device when openHAB unavailable |
| `innovv-k7/pi-software/innovv_k7_dump.py` | Pi dump service (**NOT modified**) |
| `sitemaps/myhouse.sitemap` | K7 Auto-Power + Shelly Device Status frames in INNOVV K7 section |

## State Machine

### States

| State | Relay | Description |
|-------|-------|-------------|
| **RIDING** | OFF | Ignition ON — K7 powered by ignition circuit |
| **CHARGING** | OFF | Charger detected, 60s stabilisation in progress |
| **DUMPING** | ON | K7 powered, waiting for Pi dump to complete |
| **COOLDOWN** | OFF→ | Dump complete, 30s cooldown before relay OFF |
| **PARKED** | OFF | Normal parked state |
| **LOW_BATTERY** | OFF | Battery < 12.0V — relay forced off |

### Transitions

```
         Ignition ON
    ┌────────────────────┐
    │                    ▼
 PARKED ──(V>13.2V)──► CHARGING ──(60s)──► DUMPING
    ▲                    │                    │
    │              V<13.2V│              "complete"
    │                    ▼                    ▼
    │                 PARKED              COOLDOWN ──(30s)──► PARKED
    │                                         
    │   V<12.0V                               
    └── LOW_BATTERY ◄──── (any state)         
```

### Safety Features

| Feature | Value | Purpose |
|---------|-------|---------|
| Stabilisation delay | 60 seconds | Avoids false triggers from voltage spikes |
| Safety timeout | 30 minutes | Prevents indefinite relay ON (battery drain) |
| Low battery cutoff | 12.0V | Protects battery from deep discharge |
| Ignition override | Immediate | Relay OFF when ignition turns ON (riding) |
| Post-dump cooldown | 30 seconds | Clean K7 shutdown after dump |
| Charger removal | Immediate | Relay OFF if voltage drops during dump |

## Installation

### 1. Physical Wiring

The Shelly relay drives an IRFP9140N P-channel MOSFET as a high-side switch. This replaces a direct relay connection for minimal size, zero mechanical wear, and near-zero power loss.

#### MOSFET Circuit (IRFP9140N — P-channel, TO-247)

```
  Battery +12V (always-on, fused 3A)
     │
     ├────────────────> Shelly Plus Uni POWER (DC input)
     │
     ├────────────────> Shelly ADC input (Voltmeter:100, voltage sensing)
     │
     ├────────────────> FMM920 power (existing tracker)
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
     └── IRFP9140N Drain (pin 2) ───────> K7 DC power input (+)
  
  Battery GND ──────────────────────────> K7 DC power input (-)
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
| STA0 (primary) | YourHomeSSID | Home WiFi — openHAB control |
| STA1 (failover) | YourMobileHotspot | Phone hotspot — remote relay control when away from home |

**BLE** is disabled to reduce parasitic current draw (~5-10mA saved). Use STA1 (phone hotspot) for remote control when away from home.

To reconfigure WiFi:
1. Power on the Shelly (connect battery or charger)
2. If no WiFi available, Shelly creates AP: `ShellyPlusUni-XXXXXXXXXXXX`
3. Connect phone to Shelly AP, open `192.168.33.1`
4. Configure WiFi credentials

### 3. openHAB Thing

The Shelly Plus Uni thing is defined in `things/shelly.things`:

```openhab
Thing shelly:shellyplusuni:xxxxxxxxxxxx "Shelly Plus Uni (Motorcycle K7)" @ "Garage" [
    deviceIp="192.168.1.62"
]
```

**Thing UID**: `shelly:shellyplusuni:xxxxxxxxxxxx`
**Status**: ONLINE (Shelly binding, native — not MQTT)

### 4. Shelly Voltmeter Peripheral

The ADC voltage reading uses Shelly's Voltmeter peripheral, added via RPC:

```
Uni.AddPeripheral { type: "voltmeter" }   -> voltmeter:100
Voltmeter.GetStatus { id: 100 }           -> { voltage: 12.18 }
```

This is exposed to openHAB via the `sensors#voltage` channel.

### 5. Failsafe Script (on-device)

The failsafe script runs directly on the Shelly (mJS engine), providing basic K7 power control when openHAB is unreachable:

- **Script ID**: 1 ("K7 Failsafe")
- **Status**: enabled, running
- **Upload**: Via Shelly RPC (`Script.PutCode`) — all non-ASCII characters must be stripped before upload

The failsafe yields to openHAB `sendCommand()` when LAN control is available.

### 6. Verify

1. Connect charger and watch `MC_K7_Power_State`: `PARKED -> CHARGING -> DUMPING`
2. Check logs: `tail -f /var/log/openhab/openhab.log | grep k7_power`
3. Pi should detect K7 WiFi and start dumping
4. After dump: `DUMPING -> COOLDOWN -> PARKED`

## Items Reference

### Shelly Binding Channels

| Item | Type | Channel | Purpose |
|------|------|---------|---------|
| `MC_K7_Relay` | Switch | `relay1#output` | Shelly relay (drives MOSFET gate) |
| `MC_K7_Shelly_Voltage` | Number:ElectricPotential | `sensors#voltage` | ADC battery voltage (Voltmeter:100) |
| `MC_K7_Shelly_WiFi_Signal` | Number | `device#wifiSignal` | WiFi signal strength (0-4 bars) |
| `MC_K7_Shelly_Uptime` | Number:Time | `device#uptime` | Seconds since Shelly power-on |
| `MC_K7_Shelly_LastUpdate` | DateTime | `sensors#lastUpdate` | Last state change timestamp |
| `MC_K7_Shelly_Heartbeat` | DateTime | `device#heartBeat` | Last API response (confirms device reachable) |

### API-Polled Items (via Rule 5, every 60s)

| Item | Type | Source | Purpose |
|------|------|--------|---------|
| `MC_K7_Shelly_SSID` | String | `Wifi.GetStatus` | Connected WiFi name (Devices / YourMobileHotspot) |
| `MC_K7_Shelly_RSSI` | Number | `Wifi.GetStatus` | WiFi signal in dBm (e.g. -73) |

### Virtual State Items (no channel binding)

| Item | Type | Purpose |
|------|------|---------|
| `MC_K7_Power_State` | String | State machine current state |
| `MC_Charger_Detected` | Switch | Charger detected flag |
| `MC_K7_Power_Reason` | String | Human-readable reason for state |
| `MC_K7_Relay_Since` | DateTime | When relay was last turned ON |

## Rules Reference

| # | Rule | Trigger | Action |
|---|------|---------|--------|
| 1 | K7 Power - System Init | System startup (level 100) | Forces relay OFF, resets to PARKED |
| 2 | K7 Power - Ignition Handler | `Vehicle10_Ignition` changed | ON: cancel all, RIDING. OFF: check voltage after 10s |
| 3 | K7 Power - Voltage Monitor | `Vehicle10_Power` changed | Charger detect (>13.2V), disconnect, low battery (<12.0V) |
| 4 | K7 Power - Dump Complete | `K7_Dump_Status` changed | "complete": cooldown (30s) then relay OFF |
| 5 | K7 Power - Shelly WiFi Poll | Cron `0 * * * * ?` (every minute) | HTTP GET to Shelly, updates SSID + RSSI items |

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
| Charger threshold | 13.2V | Same as openHAB rule |
| Stabilisation | 3 checks (90s) | Confirm charger is stable |
| Max ON time | 25 min | Shorter than openHAB's 30 min |
| Low battery cutoff | 11.5V | Emergency protection |
| Voltmeter ID | 100 | Peripheral added via `Uni.AddPeripheral` |

The failsafe has a **shorter timeout** (25 min vs 30 min) so openHAB's safety timer takes priority if both are running.

**Upload notes:** The Shelly mJS engine cannot parse non-ASCII characters in script source. All em dashes, arrows, and special characters must be stripped before uploading via `Script.PutCode` RPC. The Shelly returns HTTP 500 "Missing or bad argument 'code'" otherwise.

## Current Draw & Battery Drain Analysis

### Always-on parasitic draw

The Shelly Plus Uni is powered directly from the motorcycle battery on an always-on (non-ignition-switched) circuit. This means it draws current 24/7, even when parked.

| Component | State | Current Draw | Power @ 12V |
|-----------|-------|-------------|-------------|
| Shelly Plus Uni | Idle (WiFi connected) | ~70-90 mA | ~0.85-1.1 W |
| Shelly Plus Uni | Idle (WiFi searching/not connected) | ~100-120 mA | ~1.2-1.4 W |
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

*Estimated from a typical motorcycle battery (12V, 8-12 Ah). Varies significantly by battery capacity and age. Calculations assume discharge to 11.5V (the failsafe emergency cutoff).

### Mitigation strategies

1. **Safety timeouts prevent runaway drain:**
   - openHAB: 30-minute max relay ON
   - Failsafe script: 25-minute max ON
   - Low battery cutoff at 12.0V (openHAB) / 11.5V (failsafe)

2. **Keep charger connected when parked at home:** The Vitronic charger maintains the battery and simultaneously triggers the dump cycle. Best practice: plug in charger whenever parking in the garage.

3. **Extended storage (>1 week without charger):**
   - The Shelly draws ~75-95 mA continuously. On a 10Ah battery, this drains ~2.2 Ah/day.
   - After 4-5 days, battery may drop below 12V.
   - **Options:** Install a battery disconnect switch upstream of the Shelly, or add a small solar trickle charger (1-2W panel).
   - Alternative: Use the Shelly's built-in auto-OFF timer to power down the relay after extended parking, though the Shelly itself still draws power.

4. **FMM920 contribution:** The Teltonika tracker also draws from the same battery. In deep sleep it's minimal (~5mA), but if it wakes for GPS/GPRS reporting, combined draw increases.

5. **Voltage monitoring provides early warning:** Both `Vehicle10_Power` (from FMM920) and `MC_K7_Shelly_Voltage` (from Shelly ADC) provide independent battery voltage readings. A rule could be added to send alerts when voltage drops below a warning threshold (e.g. 12.2V).

## Troubleshooting

### K7 doesn't power on
- Check Shelly relay output with multimeter
- Verify `MC_K7_Relay` shows ON in openHAB
- Check Shelly thing status (ONLINE?)
- Verify wiring: relay output → K7 DC input

### Charger not detected
- Check `Vehicle10_Power` value — should be >13.2V when charger connected
- Verify `Vehicle10_Ignition` is OFF
- Check FMM920 is reporting (not in deep sleep)
- Check `MC_K7_Shelly_Voltage` for independent voltage reading

### Dump doesn't start
- Pi dump service is independent — check `systemctl status innovv-k7-dump` on Pi
- Verify K7 WiFi AP is broadcasting (`iw dev wlan0 scan` on Pi)
- The K7 may need 30-60s to fully boot and start WiFi

### Relay stays ON too long
- Safety timeout (30 min) will force OFF
- Check `K7_Dump_Status` — if stuck on "dumping", Pi may have lost K7 WiFi
- Manual override: send OFF command to `MC_K7_Relay`

## Monitoring

Watch the state machine in real-time:

```bash
# openHAB log
tail -f /var/log/openhab/openhab.log | grep k7_power

# All motorcycle K7 items
curl -s http://192.168.1.10:8080/rest/items?tags=Status | python3 -c "
import sys, json
items = json.load(sys.stdin)
for i in items:
    if i['name'].startswith('MC_K7') or i['name'].startswith('K7_'):
        print(f'{i[\"name\"]}: {i.get(\"state\",\"?\")}')
"
```
