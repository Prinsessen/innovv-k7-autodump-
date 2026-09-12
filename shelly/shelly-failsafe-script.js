// =============================================================================
// Shelly Plus Uni -- K7 relay failsafe (garage edition)
// =============================================================================
// Runs ON the Shelly itself, mJS engine. Script ID 1, "K7 Failsafe".
// Device: shellyplusuni-a1b2c3d4e5f6, 192.168.1.62, FW 1.7.4
//
// WHAT THIS REPLACED, AND WHY IT HAD TO GO
// -----------------------------------------------------------------------------
// The previous version watched the ADC and made decisions from it: charger
// detected above 14.0 V, charger gone below 13.0 V, emergency cut-off below
// 12.0 V. That worked while the Shelly lived on the motorcycle with its ADC
// across the battery.
//
// It moved to the garage on 2026-09-12 and the ADC went with it. It now reads
// the voltage across a 100 ohm resistor in the relay coil's return leg: 0.000 V
// with the coil open, about 0.86 V with it energised. Both are below 12, so the
// old emergency cut-off fired on every single check, every 30 seconds, for ever.
//
// Measured on the bench that day: the relay was commanded on, held for about
// twenty seconds, and was then switched off by this script -- with openHAB's own
// rule removed, so there was nothing else it could have been. It would have made
// the new design impossible to operate.
//
// WHAT THE JOB ACTUALLY IS
// -----------------------------------------------------------------------------
// Open the relay if openHAB stops talking. That is all, and it is still needed:
// the camera draws from the machine's battery down its own permanent feed, and
// the relay only tells it when to wake. A controller that hangs with the relay
// closed still flattens the bike.
//
// So this is a dead-man's handle. openHAB pets it on every poll. If the petting
// stops for HEARTBEAT_TIMEOUT_S, the relay opens. No thresholds, no calibration,
// no reading of anything -- which is the point: the previous version broke
// because it depended on the meaning of a measurement, and meanings change when
// hardware moves. A timer cannot be wrong about what it is measuring.
//
// A SECOND FAILSAFE ARRIVED FOR FREE
// -----------------------------------------------------------------------------
// The Shelly is on mains now. A power cut in the garage drops the Shelly, which
// releases the coil, which opens the contact and tells the camera to sleep. That
// protection did not exist while the Shelly ran from the machine's own battery,
// and it needs no code at all.
//
// UPLOAD: via RPC Script.PutCode. Strip non-ASCII first or the device returns
// 500. Keep this file as the source of truth -- the copy on the device is a
// deployment, not a master.
// =============================================================================

let CONFIG = {
  relayId: 0,                 // the terminal labelled 1; the API counts from zero
  heartbeatTimeoutS: 900,     // 15 min without openHAB -> open the relay
  checkIntervalMs: 60000,     // how often to look at the clock
  maxOnMinutes: 45            // absolute ceiling, even with a healthy heartbeat
};

let state = {
  lastPetMs: 0,               // when openHAB last confirmed it is in charge
  relayOnSinceMs: 0,
  relayIsOn: false,
  everPetted: false           // no heartbeat yet is not the same as a lost one
};

function log(msg) { print("[K7-failsafe] " + msg); }

function openRelay(why) {
  Shelly.call("Switch.Set", { id: CONFIG.relayId, on: false }, function (res, err) {
    if (err) { log("could not open relay: " + JSON.stringify(err)); return; }
    log("relay opened -- " + why);
    state.relayIsOn = false;
    state.relayOnSinceMs = 0;
  });
}

// --- The heartbeat -----------------------------------------------------------
// openHAB pets this by writing any value to the virtual component below, or in
// practice by simply commanding the relay: any command from outside counts as a
// sign of life. That is deliberate -- a heartbeat that needs its own separate
// call is a heartbeat that gets forgotten in the next refactor.
Shelly.addStatusHandler(function (event) {
  if (event.component === "switch:" + JSON.stringify(CONFIG.relayId)) {
    state.lastPetMs = Date.now();
    state.everPetted = true;
    if (typeof event.delta.output !== "undefined") {
      if (event.delta.output === true && !state.relayIsOn) {
        state.relayIsOn = true;
        state.relayOnSinceMs = Date.now();
        log("relay closed by openHAB -- watching");
      } else if (event.delta.output === false && state.relayIsOn) {
        state.relayIsOn = false;
        state.relayOnSinceMs = 0;
        log("relay opened by openHAB");
      }
    }
  }
});

function check() {
  if (!state.relayIsOn) return;          // nothing to protect against

  // Absolute ceiling. Even a perfectly healthy openHAB does not get to hold the
  // camera awake indefinitely: a stuck state machine pets the heartbeat happily
  // while doing nothing useful.
  if (state.relayOnSinceMs > 0) {
    let onMin = (Date.now() - state.relayOnSinceMs) / 60000;
    if (onMin >= CONFIG.maxOnMinutes) {
      openRelay("absolute ceiling " + CONFIG.maxOnMinutes + " min reached");
      return;
    }
  }

  // The dead-man's handle itself.
  if (!state.everPetted) return;         // never heard from it; not a loss yet
  let quietS = (Date.now() - state.lastPetMs) / 1000;
  if (quietS >= CONFIG.heartbeatTimeoutS) {
    openRelay("no word from openHAB for " + Math.round(quietS) + " s");
  }
}

Timer.set(CONFIG.checkIntervalMs, true, check);
log("loaded -- dead-man's handle, " + CONFIG.heartbeatTimeoutS + " s, ceiling "
    + CONFIG.maxOnMinutes + " min. No voltage thresholds.");
