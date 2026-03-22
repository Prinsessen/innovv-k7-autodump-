// =============================================================================
// Shelly Plus Uni -- K7 Auto-Power Local Failsafe Script
// =============================================================================
// Runs ON the Shelly Plus Uni itself (mJS scripting engine).
// Script ID: 1 ("K7 Failsafe"), enabled, running.
// Device: shellyplusuni-e08cfe8b1c3c  IP: 10.0.5.62  FW: 1.7.4
//
// Provides basic K7 power control when openHAB is unavailable.
// This is a FAILSAFE only. The primary state machine is in openHAB:
//   automation/js/vehicle-motorcycle-k7-power.js (11 JSRules)
//
// Hardware:
//   Shelly Relay1 -> 100 ohm -> IRFP9140N gate (P-channel MOSFET)
//   10K pull-up resistor: gate to source (+12V) = fail-safe OFF
//   MOSFET Source: battery +12V, Drain: K7 DC+ input
//   Voltmeter:100 peripheral (added via Uni.AddPeripheral)
//
// Logic:
//   1. Every 30s: read Voltmeter:100 voltage
//   2. If voltage > 14.0V for 90s continuously -> charger detected -> relay ON
//      Charger removal detected when voltage < 13.0V (hysteresis prevents bounce)
//      14.0V threshold = Bulk/Absorption only (ignores Float 13.8V / Storage 13.2V)
//      This is HIGHER than openHAB's 13.0V because the Shelly has no BLE cross-
//      validation or state machine awareness. The higher threshold prevents false
//      triggers during Storage/Float voltage where openHAB is in DUMP_DONE state.
//   3. Relay ON drives MOSFET gate to GND -> MOSFET ON -> K7 powered
//   4. Auto mode: relay stays ON for max 25 minutes (dump window)
//   5. After 25 min or voltage drops below 13.0V -> relay OFF
//   6. If voltage < 12.0V -> relay forced OFF immediately (protect battery)
//   7. Relay OFF -> 10K pull-up holds gate high -> MOSFET OFF -> K7 off
//   8. After relay OFF: 2-hour cooldown prevents re-triggering during same
//      charging session (avoids fighting with openHAB counter-punch)
//
// Manual Mode (external relay toggle from Shelly app/cloud/API):
//   - Detected via Shelly.addStatusHandler() on switch:0 output change
//   - Relay stays ON for max 60 minutes (browse footage on K7 app)
//   - Voltage-based charger removal does NOT shut off (no charger present)
//   - Low battery cutoff (< 12.0V) still applies as emergency protection
//   - If user forgets to turn off, 60-min timeout protects battery
//
// Upload: Via Shelly RPC (Script.PutCode). All non-ASCII characters MUST
//         be stripped before upload or Shelly returns 500 error.
//
// NOTE: This script yields to openHAB when cloud/LAN control is available.
//       openHAB sendCommand() overrides local relay state.
// =============================================================================

// --- Configuration ---
let CONFIG = {
  chargerOnVoltage: 14.0,   // V — detect charger connecting (Bulk/Absorption only)
                             //     Higher than openHAB's 13.0V because this script has no BLE
                             //     cross-validation. Prevents false trigger on Storage (13.2V)
                             //     or Float (13.8V) that caused relay fighting with openHAB.
  chargerOffVoltage: 13.0,  // V — confirm charger truly removed (1.0V hysteresis)
  lowBattVoltage: 12.0,     // V — emergency cutoff (match openHAB LOW_BATT_V)
  checkIntervalMs: 30000,   // 30s — ADC polling interval
  stabChecks: 3,            // 3 checks (90s) required to confirm charger
  maxOnMinutes: 25,         // Max relay-ON time (local limit, shorter than OH)
  manualMaxOnMinutes: 60,   // Max relay-ON time for manual/external ON (browse footage)
  cooldownMs: 7200000,      // 2 hours — after relay OFF, ignore charger detection
  relayId: 0                // Relay channel (0 = relay1)
};

// --- State ---
let state = {
  highVoltageCount: 0,      // Consecutive checks with voltage > threshold
  relayOnTime: 0,           // Timestamp when relay was turned ON (ms)
  relayIsOn: false,         // Current relay state
  isManualOn: false,        // True if relay was turned ON externally (app/API)
  lastVoltage: 0,           // Last ADC reading
  cooldownUntil: 0          // Timestamp until charger detection is suppressed
};

// --- Helpers ---
function log(msg) {
  print("[K7-failsafe] " + msg);
}

function relayControl(on) {
  Shelly.call("Switch.Set", { id: CONFIG.relayId, on: on }, function (res, err) {
    if (err) {
      log("Relay " + (on ? "ON" : "OFF") + " error: " + JSON.stringify(err));
    } else {
      state.relayIsOn = on;
      if (on) {
        state.relayOnTime = Date.now();
        state.isManualOn = false;  // Script-driven ON, not manual
        log("Relay ON -- charger confirmed");
      } else {
        state.relayOnTime = 0;
        state.highVoltageCount = 0;
        state.isManualOn = false;
        state.cooldownUntil = Date.now() + CONFIG.cooldownMs;
        log("Relay OFF -- cooldown " + (CONFIG.cooldownMs / 3600000) + "h");
      }
    }
  });
}

// --- External relay change handler ---
// Catches relay ON/OFF from ANY source: Shelly app, cloud, API, openHAB.
// If relay was turned ON externally (not by this script), start safety timer
// with the longer manual timeout (manualMaxOnMinutes) so user has time to
// browse footage on K7 app before auto-shutoff protects the battery.
Shelly.addStatusHandler(function (event) {
  if (event.component === "switch:" + JSON.stringify(CONFIG.relayId)) {
    if (typeof event.delta.output !== "undefined") {
      if (event.delta.output === true && !state.relayIsOn) {
        // Relay turned ON externally
        state.relayIsOn = true;
        state.relayOnTime = Date.now();
        state.isManualOn = true;
        log("External relay ON detected -- safety timer " + CONFIG.manualMaxOnMinutes + "min started");
      } else if (event.delta.output === false && state.relayIsOn) {
        // Relay turned OFF externally
        state.relayIsOn = false;
        state.relayOnTime = 0;
        state.isManualOn = false;
        state.highVoltageCount = 0;
        state.cooldownUntil = Date.now() + CONFIG.cooldownMs;
        log("External relay OFF detected -- cooldown " + (CONFIG.cooldownMs / 3600000) + "h");
      }
    }
  }
});

// --- Main check loop ---
function checkVoltage() {
  Shelly.call("Voltmeter.GetStatus", { id: 100 }, function (res, err) {
    if (err || !res) {
      log("ADC read error: " + JSON.stringify(err));
      return;
    }

    let voltage = res.voltage;
    state.lastVoltage = voltage;

    // --- Emergency low battery ---
    if (voltage < CONFIG.lowBattVoltage) {
      if (state.relayIsOn) {
        log("LOW BATTERY " + voltage.toFixed(2) + "V -- forcing relay OFF");
        relayControl(false);
      }
      state.highVoltageCount = 0;
      return;
    }

    // --- Safety timeout ---
    if (state.relayIsOn && state.relayOnTime > 0) {
      let onMinutes = (Date.now() - state.relayOnTime) / 60000;
      let limit = CONFIG.maxOnMinutes;
      let mode = "auto";
      if (state.isManualOn) {
        limit = CONFIG.manualMaxOnMinutes;
        mode = "manual";
      }
      if (onMinutes >= limit) {
        log("TIMEOUT -- relay ON for " + onMinutes.toFixed(0) + " min (limit " + limit + "min, " +
            mode + ") -- forcing OFF");
        relayControl(false);
        return;
      }
    }

    // --- Charger detection ---
    // Skip if in cooldown (dump was recently completed)
    if (state.cooldownUntil > 0 && Date.now() < state.cooldownUntil) {
      if (voltage > CONFIG.chargerOnVoltage && state.highVoltageCount === 0) {
        let remainMin = ((state.cooldownUntil - Date.now()) / 60000).toFixed(0);
        log("Charger detection suppressed -- cooldown " + remainMin + "min remaining");
      }
      state.highVoltageCount = 0;
      return;
    }
    // Clear expired cooldown
    if (state.cooldownUntil > 0 && Date.now() >= state.cooldownUntil) {
      log("Cooldown expired -- charger detection re-enabled");
      state.cooldownUntil = 0;
    }

    if (voltage > CONFIG.chargerOnVoltage) {
      state.highVoltageCount++;
      log("Voltage " + voltage.toFixed(2) + "V > " + CONFIG.chargerOnVoltage +
          "V (check " + state.highVoltageCount + "/" + CONFIG.stabChecks + ")");

      if (!state.relayIsOn && state.highVoltageCount >= CONFIG.stabChecks) {
        log("Charger confirmed after " + CONFIG.stabChecks + " checks — relay ON");
        relayControl(true);
      }
    } else if (voltage <= CONFIG.chargerOffVoltage) {
      // Voltage below OFF threshold — charger truly removed
      if (state.highVoltageCount > 0) {
        log("Voltage " + voltage.toFixed(2) + "V <= " + CONFIG.chargerOffVoltage + "V — charger removed, reset count");
      }
      state.highVoltageCount = 0;

      if (state.relayIsOn && !state.isManualOn) {
        log("Charger removed (" + voltage.toFixed(2) + "V) -- relay OFF");
        relayControl(false);
      } else if (state.relayIsOn && state.isManualOn) {
        // Manual mode: don't shut off on low voltage - user wants K7 powered
        // Safety timeout (manualMaxOnMinutes) and low battery cutoff still apply
        let onMin = "?";
        if (state.relayOnTime > 0) {
          onMin = ((Date.now() - state.relayOnTime) / 60000).toFixed(0);
        }
        log("Manual mode -- voltage " + voltage.toFixed(2) + "V (no charger) -- " + onMin + "min elapsed, timeout at " + CONFIG.manualMaxOnMinutes + "min");
      }
    } else {
      // Between OFF and ON thresholds (hysteresis zone) — no action
      // This covers Vitronic Float/Storage stages (~12.9V)
      if (state.highVoltageCount > 0) {
        log("Voltage " + voltage.toFixed(2) + "V in hysteresis zone (" +
            CONFIG.chargerOffVoltage + "-" + CONFIG.chargerOnVoltage + "V) — holding");
        state.highVoltageCount = 0;
      }
    }
  });
}

// --- Start timer ---
log("Starting -- check every " + (CONFIG.checkIntervalMs / 1000) + "s");
log("Charger ON: " + CONFIG.chargerOnVoltage + "V, OFF: " + CONFIG.chargerOffVoltage + "V, " +
    "low battery: " + CONFIG.lowBattVoltage + "V, " +
    "auto max: " + CONFIG.maxOnMinutes + "min, manual max: " + CONFIG.manualMaxOnMinutes + "min, " +
    "cooldown: " + (CONFIG.cooldownMs / 3600000) + "h");

// On startup: read actual relay state to sync internal tracking.
// Prevents desync if Shelly rebooted while relay was ON.
Shelly.call("Switch.GetStatus", { id: CONFIG.relayId }, function (res, err) {
  if (!err && res) {
    state.relayIsOn = res.output;
    if (res.output) {
      state.relayOnTime = Date.now();
      log("Init: relay is ON (boot state) - starting safety timer");
    } else {
      log("Init: relay is OFF - normal");
    }
  }
});

// Initial check after 5 seconds
Timer.set(5000, false, checkVoltage);

// Recurring check
Timer.set(CONFIG.checkIntervalMs, true, checkVoltage);
