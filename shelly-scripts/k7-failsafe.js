// =============================================================================
// Shelly Plus Uni -- K7 relay failsafe (garage edition)
// =============================================================================
// Runs ON the Shelly itself, mJS engine. Script ID 1, "K7 Failsafe".
// Device: shellyplusuni-a1b2c3d4e5f6, 192.168.1.62, FW 1.7.4
//
// WHAT THIS REPLACED, AND WHY IT HAD TO GO
// -----------------------------------------------------------------------------
// The first version watched the ADC and made decisions from it: charger
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
// rule removed, so there was nothing else it could have been.
//
// AND WHY THE SECOND VERSION WAS NEVER SWITCHED ON
// -----------------------------------------------------------------------------
// The rewrite was a dead-man's handle, and its comment said openHAB pets it on
// every poll. It does not. The pet was a status handler on switch:0, and a
// status handler fires on CHANGE. openHAB's poll is Shelly.GetStatus -- a read,
// which changes nothing and raises no event.
//
// So the only pet was the relay closing. The sequence was therefore: relay
// closes, one event, silence, and fifteen minutes later the script declares
// openHAB dead and opens the relay -- in the middle of a perfectly healthy dump,
// with openHAB polling happily every thirty seconds throughout. Every dump
// longer than the heartbeat timeout would have been cut in half, and the fault
// would have looked exactly like a hardware problem.
//
// Caught by reading it before enabling it, 2026-09-12, never in production.
//
// THE HEARTBEAT IS NOW A REAL ONE
// -----------------------------------------------------------------------------
// openHAB writes a counter into the Shelly's key-value store on every poll, and
// this script watches that key for CHANGE. Petting is now a deliberate act that
// cannot happen by accident, and cannot silently stop happening because someone
// changed how the relay is commanded.
//
// The script never compares the two clocks. It stores the last value it saw and
// the time IT saw that value change, both by its own Date.now(). openHAB's clock
// and the Shelly's may disagree by minutes after a reboot, and a heartbeat that
// depends on them agreeing is a heartbeat that fails at 3 a.m.
//
// WHAT THE JOB ACTUALLY IS
// -----------------------------------------------------------------------------
// Open the relay if openHAB stops talking. That is all, and it is still needed:
// the camera draws from the machine's battery down its own permanent feed, and
// the relay only tells it when to wake. A controller that hangs with the relay
// closed still flattens the bike.
//
// No thresholds, no calibration, no reading of any measurement -- which is the
// point. The first version broke because it depended on the meaning of a
// measurement, and meanings change when hardware moves. A counter that stops
// advancing means one thing wherever the box is bolted.
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
  heartbeatKey: "oh_heartbeat",
  // openHAB writes every 30 s, so this is ten missed writes. Long enough that a
  // rules reload or a slow poll is not a fault; short enough that a hung
  // controller does not hold the camera awake for the full ceiling.
  heartbeatTimeoutS: 300,
  checkIntervalMs: 60000,     // how often to look at the clock
  // Absolute ceiling, above openHAB's own MAX_ON_MIN of 30 so it only ever
  // catches what openHAB failed to catch, and never pre-empts a normal dump.
  maxOnMinutes: 45
};

let state = {
  lastBeat: null,             // last heartbeat value seen in the KVS
  lastBeatChangeMs: 0,        // when THIS script saw it change (its own clock)
  relayOnSinceMs: 0,
  relayIsOn: false
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

// Track the relay so the script knows when there is something to protect.
// This is NOT the heartbeat -- that mistake is what the header above describes.
Shelly.addStatusHandler(function (event) {
  if (event.component === "switch:" + JSON.stringify(CONFIG.relayId)) {
    if (typeof event.delta.output !== "undefined") {
      if (event.delta.output === true && !state.relayIsOn) {
        state.relayIsOn = true;
        state.relayOnSinceMs = Date.now();
        log("relay closed -- watching");
      } else if (event.delta.output === false && state.relayIsOn) {
        state.relayIsOn = false;
        state.relayOnSinceMs = 0;
        log("relay opened");
      }
    }
  }
});

function judge() {
  // Absolute ceiling. Even a perfectly healthy openHAB does not get to hold the
  // camera awake indefinitely: a stuck state machine writes heartbeats happily
  // while doing nothing useful.
  if (state.relayOnSinceMs > 0) {
    let onMin = (Date.now() - state.relayOnSinceMs) / 60000;
    if (onMin >= CONFIG.maxOnMinutes) {
      openRelay("absolute ceiling " + CONFIG.maxOnMinutes + " min reached");
      return;
    }
  }

  // Never seen a heartbeat at all is not the same as having lost one. A Shelly
  // that boots before openHAB does must not open a relay openHAB has not yet
  // had the chance to ask for.
  if (state.lastBeatChangeMs === 0) return;

  let quietS = (Date.now() - state.lastBeatChangeMs) / 1000;
  if (quietS >= CONFIG.heartbeatTimeoutS) {
    openRelay("no heartbeat from openHAB for " + Math.round(quietS) + " s");
  }
}

function check() {
  if (!state.relayIsOn) return;          // nothing to protect against

  Shelly.call("KVS.Get", { key: CONFIG.heartbeatKey }, function (res, err) {
    if (!err && res && typeof res.value !== "undefined") {
      if (res.value !== state.lastBeat) {
        state.lastBeat = res.value;
        state.lastBeatChangeMs = Date.now();
      }
    }
    // A failed KVS read is not evidence that openHAB is gone -- it is evidence
    // that one call failed. Judge on the timestamp either way; a genuine outage
    // simply means the value stops changing and the timeout does its work.
    judge();
  });
}

// Learn the relay's state at boot rather than assuming it is open: a script
// restart while the relay is closed must not lose track of a live dump.
Shelly.call("Switch.GetStatus", { id: CONFIG.relayId }, function (res, err) {
  if (!err && res && res.output) {
    state.relayIsOn = true;
    state.relayOnSinceMs = Date.now();
    log("boot: relay already closed -- watching");
  }
});

Timer.set(CONFIG.checkIntervalMs, true, check);
log("loaded -- heartbeat '" + CONFIG.heartbeatKey + "', timeout "
    + CONFIG.heartbeatTimeoutS + " s, ceiling " + CONFIG.maxOnMinutes
    + " min. No voltage thresholds.");
