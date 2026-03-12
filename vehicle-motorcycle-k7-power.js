// =============================================================================
// K7 Auto-Power State Machine - Shelly Plus Uni Relay Control  (v2 BLE)
// =============================================================================
// Charger detection: DUAL-SENSOR (BLE primary, voltage fallback)
//
//   PRIMARY:  Victron BLE charger state (Bulk/Absorption/Float/Storage)
//             Polled every 30s by Pi daemon → MC_Charger_BLE_Online, MC_Charger_State
//             100% accurate — no threshold guessing
//
//   FALLBACK: Shelly ADC voltage thresholds (CHARGER_ON_V / CHARGER_OFF_V)
//             Only used when BLE daemon is unavailable
//
// Victron Blue Smart IP65 12/10 charge profile (BLE-verified 2026-03-12):
//   Bulk:        voltage rising toward 14.4V, max current
//   Absorption:  14.4V held, current tapering
//   Float:       13.8V held, maintenance current
//   Storage:     13.2V held, minimal current
// =============================================================================
const { rules, triggers, items, actions, time } = require('openhab');

const LOG = 'k7_power';
// Voltage thresholds — FALLBACK only (used when BLE is unavailable)
const CHARGER_ON_V  = 13.0;  // Voltage to DETECT charger (fallback)
const CHARGER_OFF_V = 12.7;  // Voltage to CONFIRM charger removed (fallback)
// When BLE is online, all charger decisions use BLE state as authority.
// Voltage-only decisions are blocked if BLE contradicts them.
const LOW_BATT_V  = 12.0;
const STAB_SEC    = 60;
const MAX_ON_MIN  = 30;
const COOLDOWN_S  = 30;
const IGN_DEBOUNCE_S = 5;

const STATES = {
  PARKED: 'PARKED', RIDING: 'RIDING', CHARGING: 'CHARGING',
  TRANSFERRING: 'TRANSFERRING', COOLDOWN: 'COOLDOWN', DUMP_DONE: 'DUMP_DONE',
  LOW_BATTERY: 'LOW_BATTERY'
};

function getState() {
  const st = items.getItem('MC_K7_Power_State');
  return (st.isUninitialized || st.state === 'NULL') ? STATES.PARKED : st.state;
}

function setState(newState, reason) {
  items.getItem('MC_K7_Power_State').postUpdate(newState);
  if (reason) items.getItem('MC_K7_Power_Reason').postUpdate(reason);
  console.info(LOG + ': State -> ' + newState + (reason ? ' (' + reason + ')' : ''));
}

function relayOn(reason) {
  items.getItem('MC_K7_Relay').sendCommand('ON');
  console.info(LOG + ': Relay ON - ' + reason);
}

function relayOff(reason) {
  items.getItem('MC_K7_Relay').sendCommand('OFF');
  console.info(LOG + ': Relay OFF - ' + reason);
}

function cancelTimer(timerName) {
  var t = cache.private.get(timerName);
  if (t !== null && t !== undefined) {
    try { t.cancel(); } catch(e) {}
    cache.private.put(timerName, null);
  }
}

// Returns BLE charger info string for enriched logging
function getBLEInfo() {
  try {
    var online = items.getItem('MC_Charger_BLE_Online').state;
    var st = items.getItem('MC_Charger_State').state;
    var v = items.getItem('MC_Charger_Voltage').state;
    var a = items.getItem('MC_Charger_Current').state;
    return 'BLE=' + online + ' ChgState=' + st + ' ChgV=' + v + ' ChgA=' + a;
  } catch (e) { return 'BLE=unavailable'; }
}

// Returns true if Victron BLE charger is online
function isBLEOnline() {
  try {
    return items.getItem('MC_Charger_BLE_Online').state === 'ON' && isBLEFresh();
  } catch (e) { return false; }
}

// Returns true if BLE reports an active charging state
function isBLECharging() {
  try {
    var st = items.getItem('MC_Charger_State').state;
    return ['Bulk', 'Absorption', 'Float', 'Recondition'].indexOf(st) >= 0 && isBLEFresh();
  } catch (e) { return false; }
}

// Returns true if BLE reports ANY charger-connected state (including Storage/Idle)
// Used to prevent false "charger removed" when charger is in maintenance mode
function isBLEConnected() {
  try {
    var st = items.getItem('MC_Charger_State').state;
    return ['Bulk', 'Absorption', 'Float', 'Storage', 'Recondition', 'Idle'].indexOf(st) >= 0 && isBLEFresh();
  } catch (e) { return false; }
}

// Returns true if BLE data was updated within the last 3 minutes
// (daemon polls every 30s — 3min = 6 missed polls = definitely stale)
function isBLEFresh() {
  try {
    var lastUpdate = items.getItem('MC_Charger_Last_Update');
    if (lastUpdate.isUninitialized || lastUpdate.state === 'NULL') return false;
    var stateStr = lastUpdate.state.toString();
    // Daemon sends +0100 format — ZonedDateTime needs +01:00 (with colon)
    stateStr = stateStr.replace(/([+-]\d{2})(\d{2})$/, '$1:$2');
    var then = time.ZonedDateTime.parse(stateStr);
    var age = time.Duration.between(then, time.ZonedDateTime.now()).toMinutes();
    return age < 3;
  } catch (e) {
    console.warn(LOG + ': isBLEFresh() parse error: ' + e.message);
    return false;
  }
}

// Returns the BLE charger state string, or null if not available
function getBLEState() {
  try {
    var st = items.getItem('MC_Charger_State').state;
    return (st && st !== 'NULL' && st !== 'UNDEF') ? st : null;
  } catch (e) { return null; }
}

// Compute human-readable charger connection status from BLE signals.
// Three states: Offline (no mains), Standby (mains but cable off bike),
// Charging — <stage> (actively charging battery).
function updateChargerConnection() {
  try {
    var bleOnline = items.getItem('MC_Charger_BLE_Online').state === 'ON';
    var bleState = getBLEState();
    var status;
    if (!bleOnline) {
      status = 'Offline';
    } else {
      var chargingStates = ['Bulk', 'Absorption', 'Float', 'Storage', 'Recondition'];
      if (chargingStates.indexOf(bleState) >= 0) {
        status = 'Charging \u2014 ' + bleState;
      } else {
        status = 'Standby (idle)';
      }
    }
    items.getItem('MC_Charger_Connection').postUpdate(status);
  } catch (e) {
    console.warn(LOG + ': updateChargerConnection error: ' + e.message);
  }
}

// Re-arm to PARKED from a charger-active state.
// Sets 5-min grace period to suppress voltage-only false retrigger
// while battery voltage settles from charger levels (>13V → ~12.5V).
function rearmToParked(reason) {
  setState(STATES.PARKED, reason);
  items.getItem('MC_Charger_Detected').postUpdate('OFF');
  cache.private.put('rearmTime', time.ZonedDateTime.now());
}

function startChargerSequence(voltage, source) {
  var currentState = getState();
  if (currentState !== STATES.PARKED) return;

  // Accept if BLE confirms charging/connected OR voltage above threshold (fallback)
  var bleCharging = isBLECharging();
  var bleConnected = isBLEConnected();
  if (!bleCharging && !bleConnected && voltage <= CHARGER_ON_V) return;

  // Grace period: after re-arm from charger-active state, battery voltage
  // lingers above CHARGER_ON_V for minutes. Suppress voltage-only detection
  // for 5 min. BLE charging/connected confirmation overrides immediately.
  if (!bleCharging && !bleConnected) {
    var rearmTime = cache.private.get('rearmTime');
    if (rearmTime !== null && rearmTime !== undefined) {
      var sinceRearm = time.Duration.between(rearmTime, time.ZonedDateTime.now()).toMinutes();
      if (sinceRearm < 5) {
        console.info(LOG + ': Suppressed voltage-only charger detect \u2014 ' + sinceRearm + 'min since re-arm (grace 5min). V=' + voltage.toFixed(1) + 'V');
        return;
      }
    }
  }

  items.getItem('MC_Charger_Detected').postUpdate('ON');
  var reason = source + (bleCharging ? ' BLE=' + items.getItem('MC_Charger_State').state : '') + ' V=' + voltage.toFixed(1) + 'V';
  setState(STATES.CHARGING, reason);
  console.info(LOG + ': Charger detected (' + reason + '), ' + STAB_SEC + 's stabilisation...');

  cancelTimer('stabTimer');
  var stabTimer = actions.ScriptExecution.createTimer(
    time.ZonedDateTime.now().plusSeconds(STAB_SEC),
    function () {
      var recheck = parseFloat(items.getItem('MC_K7_Shelly_Voltage').state);
      var bleOnline = isBLEOnline();
      var bleChg = isBLECharging();
      var bleCon = isBLEConnected();

      // PRIMARY: BLE is authoritative when online — trust completely
      //   Accept charging OR connected (Storage/Idle = charger still present)
      // FALLBACK: No BLE available, use voltage threshold
      var confirmed;
      if (bleOnline) {
        confirmed = bleChg || bleCon;  // BLE online: confirm if charger connected in any state
      } else {
        confirmed = recheck > CHARGER_ON_V;  // No BLE: voltage fallback
      }

      if (confirmed) {
        var method = (bleChg || bleCon) ? 'BLE ' + items.getItem('MC_Charger_State').state : 'Voltage ' + recheck.toFixed(1) + 'V (no BLE)';
        console.info(LOG + ': Stabilisation complete [' + method + '] [' + getBLEInfo() + '] V=' + recheck.toFixed(1) + 'V - relay ON');
        setState(STATES.TRANSFERRING, 'Charger confirmed: ' + method);
        relayOn('Charger confirmed: ' + method);

        cancelTimer('maxOnTimer');
        var maxOnTimer = actions.ScriptExecution.createTimer(
          time.ZonedDateTime.now().plusMinutes(MAX_ON_MIN),
          function () {
            console.warn(LOG + ': SAFETY TIMEOUT - relay ON for ' + MAX_ON_MIN + 'min - forcing OFF');
            relayOff('Safety timeout ' + MAX_ON_MIN + 'min');
            rearmToParked('Safety timeout');
          }
        );
        cache.private.put('maxOnTimer', maxOnTimer);
      } else {
        console.info(LOG + ': Stabilisation failed [' + getBLEInfo() + '] V=' + recheck.toFixed(1) + 'V - back to PARKED');
        rearmToParked('Stabilisation failed \u2014 no BLE charging, V=' + recheck.toFixed(1));
      }
    }
  );
  cache.private.put('stabTimer', stabTimer);
}

// Rule 1: System Init (voltage-aware)
// Checks current voltage to determine correct initial state:
//   > CHARGER_ON_V  -> charger active -> start charger sequence
//   > CHARGER_OFF_V -> hysteresis zone (Storage/Float) -> DUMP_DONE
//   <= CHARGER_OFF_V -> no charger -> PARKED
rules.JSRule({
  name: 'K7 Power - System Init',
  description: 'Initialize K7 power state machine on system start',
  triggers: [triggers.SystemStartlevelTrigger(100)],
  execute: function () {
    console.info(LOG + ': System start - initializing');
    relayOff('System start');

    // Preserve DUMP_DONE across script reloads \u2014 dump already done,
    // no need to re-trigger. Will re-arm when charger truly disconnects.
    var existingState = getState();
    if (existingState === STATES.DUMP_DONE) {
      console.info(LOG + ': Init: Preserving DUMP_DONE \u2014 dump already completed, ready for next ride');
      updateChargerConnection();
      return;
    }

    var powerItem = items.getItem('MC_K7_Shelly_Voltage');
    var voltage = (!powerItem.isUninitialized && powerItem.state !== 'NULL')
      ? parseFloat(powerItem.state) : 0;

    console.info(LOG + ': Init: Shelly V=' + voltage.toFixed(1) + 'V, ' + getBLEInfo());

    var bleCharging = isBLECharging();
    var bleOnline = isBLEOnline();

    if (bleCharging) {
      console.info(LOG + ': Init: BLE says ' + getBLEState() + ' \u2014 charger active, starting sequence');
      items.getItem('MC_Charger_Detected').postUpdate('ON');
      startChargerSequence(voltage, 'System init BLE=' + getBLEState());
    } else if (bleOnline && !bleCharging) {
      // BLE online but Idle/Off \u2014 charger powered but not charging battery
      console.info(LOG + ': Init: BLE online but ' + getBLEState() + ' \u2014 charger powered, not charging');
      // Preserve DUMP_DONE across script reloads during Float\u2194Idle oscillation
      var prevState = items.getItem('MC_K7_Power_State').state;
      if (prevState === STATES.DUMP_DONE) {
        console.info(LOG + ': Init: preserving DUMP_DONE (previous state was DUMP_DONE, charger still connected)');
        setState(STATES.DUMP_DONE, 'System start - preserved DUMP_DONE (BLE online, ' + getBLEState() + ')');
        items.getItem('MC_Charger_Detected').postUpdate('ON');
      } else {
        setState(STATES.PARKED, 'System start - charger powered but ' + getBLEState());
        items.getItem('MC_Charger_Detected').postUpdate('OFF');
      }
    } else if (voltage > CHARGER_ON_V) {
      console.info(LOG + ': Init: no BLE, voltage ' + voltage.toFixed(1) + 'V > ' + CHARGER_ON_V + 'V \u2014 fallback charger detect');
      items.getItem('MC_Charger_Detected').postUpdate('ON');
      startChargerSequence(voltage, 'System init (voltage fallback)');
    } else if (voltage > CHARGER_OFF_V) {
      console.info(LOG + ': Init: no BLE, hysteresis zone (' + voltage.toFixed(1) + 'V) \u2014 assuming charger connected');
      setState(STATES.DUMP_DONE, 'System init - voltage fallback (' + voltage.toFixed(1) + 'V)');
      items.getItem('MC_Charger_Detected').postUpdate('ON');
    } else {
      console.info(LOG + ': Init: no charger (V=' + voltage.toFixed(1) + ', BLE offline) \u2014 PARKED');
      setState(STATES.PARKED, 'System start');
      items.getItem('MC_Charger_Detected').postUpdate('OFF');
    }
    updateChargerConnection();
  }
});

// Rule 2: Ignition Handler (debounced)
// With 1N4007 diode installed (2026-03-12), MOSFET back-feed to FMM920 is
// physically blocked. A short 5s debounce remains for generic ignition noise.
// Real riding keeps ignition ON for minutes.
rules.JSRule({
  name: 'K7 Power - Ignition Handler',
  description: 'Handles ignition ON/OFF with 5s debounce (diode blocks MOSFET back-feed)',
  triggers: [triggers.ItemStateChangeTrigger('Vehicle10_Ignition')],
  execute: function () {
    try {
      var ignition = items.getItem('Vehicle10_Ignition').state;

      if (ignition === 'ON') {
        console.info(LOG + ': Ignition ON detected - debouncing ' + IGN_DEBOUNCE_S + 's');
        cancelTimer('ignDebounceTimer');
        var debounceTimer = actions.ScriptExecution.createTimer(
          time.ZonedDateTime.now().plusSeconds(IGN_DEBOUNCE_S),
          function () {
            cache.private.put('ignDebounceTimer', null);  // Clear so OFF handler works
            var recheck = items.getItem('Vehicle10_Ignition').state;
            if (recheck !== 'ON') {
              console.info(LOG + ': Ignition debounce expired but ignition now OFF - ignoring');
              return;
            }
            var currentState = getState();
            // DUMP_DONE: dump is complete, allow riding. Post-ignition check
            // will re-detect charger and trigger another dump if needed.
            if (currentState === STATES.DUMP_DONE) {
              console.info(LOG + ': Ignition ON in DUMP_DONE \u2014 dump already complete, allowing ride');
            }
            // MOSFET back-feed check removed 2026-03-12: 1N4007 diode physically blocks it.
            // If ignition is ON during TRANSFERRING/COOLDOWN with relay ON, it's real ignition.
            if (currentState === STATES.CHARGING) {
              console.info(LOG + ': Real ignition during CHARGING - cancelling charger sequence');
              cancelTimer('stabTimer');
              items.getItem('MC_Charger_Detected').postUpdate('OFF');
            }
            console.info(LOG + ': Ignition confirmed ON after ' + IGN_DEBOUNCE_S + 's debounce');
            cancelTimer('stabTimer');
            cancelTimer('maxOnTimer');
            cancelTimer('cooldownTimer');
            setState(STATES.RIDING, 'Ignition ON');
            items.getItem('MC_Charger_Detected').postUpdate('OFF');
          }
        );
        cache.private.put('ignDebounceTimer', debounceTimer);
      } else if (ignition === 'OFF') {
        var pending = cache.private.get('ignDebounceTimer');
        if (pending !== null && pending !== undefined) {
          console.info(LOG + ': Ignition OFF before debounce expired - false trigger filtered');
          cancelTimer('ignDebounceTimer');
          return;
        }
        var currentState = getState();
        if (currentState !== STATES.RIDING) {
          console.info(LOG + ': Ignition OFF but not RIDING (' + currentState + ') - ignoring');
          return;
        }
        console.info(LOG + ': Ignition OFF - checking charger state');
        setState(STATES.PARKED, 'Ignition OFF');
        var checkTimer = actions.ScriptExecution.createTimer(
          time.ZonedDateTime.now().plusSeconds(10),
          function () {
            var v = parseFloat(items.getItem('MC_K7_Shelly_Voltage').state);
            var bleChg = isBLECharging();
            var bleCon = isBLEConnected();
            console.info(LOG + ': Post-ignition check: V=' + v.toFixed(1) + 'V [' + getBLEInfo() + ']');
            if (bleChg) {
              console.info(LOG + ': Post-ignition: BLE confirms charging \u2014 starting charger sequence');
              startChargerSequence(v, 'Post-ignition BLE=' + getBLEState());
            } else if (bleCon) {
              console.info(LOG + ': Post-ignition: BLE charger connected (' + getBLEState() + ') \u2014 starting charger sequence');
              startChargerSequence(v, 'Post-ignition BLE-connected=' + getBLEState());
            } else if (v > CHARGER_ON_V) {
              console.info(LOG + ': Post-ignition: voltage ' + v.toFixed(1) + 'V \u2014 fallback charger detect');
              startChargerSequence(v, 'Post-ignition voltage');
            }
          }
        );
        cache.private.put('postIgnitionTimer', checkTimer);
      }
    } catch (e) {
      console.error(LOG + ': Ignition handler error: ' + e.message);
    }
  }
});

// Rule 3: Voltage Monitor
rules.JSRule({
  name: 'K7 Power - Voltage Monitor',
  description: 'Monitors battery voltage for charger detection and low battery',
  triggers: [triggers.ItemStateChangeTrigger('MC_K7_Shelly_Voltage')],
  execute: function () {
    try {
      var powerItem = items.getItem('MC_K7_Shelly_Voltage');
      if (powerItem.isUninitialized) return;
      var voltage = parseFloat(powerItem.state);
      var currentState = getState();

      if (voltage < LOW_BATT_V) {
        if (currentState !== STATES.LOW_BATTERY && currentState !== STATES.PARKED) {
          console.warn(LOG + ': LOW BATTERY ' + voltage.toFixed(1) + 'V - forcing OFF');
          cancelTimer('stabTimer');
          cancelTimer('maxOnTimer');
          cancelTimer('cooldownTimer');
          relayOff('Low battery ' + voltage.toFixed(1) + 'V');
          setState(STATES.LOW_BATTERY, 'Battery ' + voltage.toFixed(1) + 'V < ' + LOW_BATT_V + 'V');
          items.getItem('MC_Charger_Detected').postUpdate('OFF');
        }
        return;
      }

      if (currentState === STATES.LOW_BATTERY && voltage >= LOW_BATT_V) {
        setState(STATES.PARKED, 'Battery recovered to ' + voltage.toFixed(1) + 'V');
      }
      if (currentState === STATES.DUMP_DONE && voltage <= CHARGER_OFF_V) {
        if (isBLECharging()) {
          console.info(LOG + ': Voltage dropped (' + voltage.toFixed(1) + 'V) but BLE actively charging \u2014 stage transition dip, staying DUMP_DONE [' + getBLEInfo() + ']');
        } else if (isBLEConnected()) {
          console.info(LOG + ': Voltage dropped (' + voltage.toFixed(1) + 'V) but BLE charger still connected (maintenance mode) \u2014 staying DUMP_DONE [' + getBLEInfo() + ']');
        } else {
          console.info(LOG + ': Charger removed after dump (' + voltage.toFixed(1) + 'V, BLE not connected) - re-armed [' + getBLEInfo() + ']');
          rearmToParked('Charger removed (V=' + voltage.toFixed(1) + ', BLE not connected) - re-armed');
        }
      }
      if (currentState === STATES.PARKED && voltage > CHARGER_ON_V) {
        startChargerSequence(voltage, 'Voltage');
      }
      if (currentState === STATES.CHARGING && voltage <= CHARGER_OFF_V) {
        if (isBLECharging()) {
          console.info(LOG + ': Voltage dip during stabilisation (' + voltage.toFixed(1) + 'V) but BLE actively charging \u2014 keeping CHARGING [' + getBLEInfo() + ']');
        } else if (isBLEConnected()) {
          console.info(LOG + ': Voltage dip during stabilisation (' + voltage.toFixed(1) + 'V) but BLE charger still connected \u2014 keeping CHARGING [' + getBLEInfo() + ']');
        } else {
          console.info(LOG + ': Charger removed during stabilisation (' + voltage.toFixed(1) + 'V, BLE not connected) [' + getBLEInfo() + ']');
          cancelTimer('stabTimer');
          rearmToParked('Charger removed during stabilisation (V=' + voltage.toFixed(1) + ', BLE not connected)');
        }
      }
    } catch (e) {
      console.error(LOG + ': Voltage monitor error: ' + e.message);
    }
  }
});

// Rule 4: Dump Complete
rules.JSRule({
  name: 'K7 Power - Dump Complete',
  description: 'Handles K7 dump completion - cooldown then relay OFF',
  triggers: [triggers.ItemStateChangeTrigger('K7_Dump_Status')],
  execute: function () {
    try {
      var status = items.getItem('K7_Dump_Status').state;
      var currentState = getState();
      if (currentState !== STATES.TRANSFERRING) return;

      var doneStatuses = ['complete', 'complete (no new files)'];
      if (doneStatuses.indexOf(status) >= 0) {
        console.info(LOG + ': Dump complete (' + status + ') - ' + COOLDOWN_S + 's cooldown');
        setState(STATES.COOLDOWN, 'Dump: ' + status);
        cancelTimer('maxOnTimer');

        cancelTimer('cooldownTimer');
        var cooldownTimer = actions.ScriptExecution.createTimer(
          time.ZonedDateTime.now().plusSeconds(COOLDOWN_S),
          function () {
            console.info(LOG + ': Cooldown complete - relay OFF, staying in DUMP_DONE');
            relayOff('Dump complete + cooldown');
            setState(STATES.DUMP_DONE, 'Dump cycle complete - ready for next ride');
          }
        );
        cache.private.put('cooldownTimer', cooldownTimer);
      }

      if (status && status.toString().indexOf('error') === 0) {
        console.warn(LOG + ': Dump error (' + status + ') - relay stays ON, max timer will handle shutdown');
      }
    } catch (e) {
      console.error(LOG + ': Dump handler error: ' + e.message);
    }
  }
});

// =============================================================================
// Rule 7: Manual Relay Override
// Allows manual relay ON (e.g. from sitemap) to trigger a dump from DUMP_DONE
// or PARKED state. Useful when charger is still connected and you want to
// force another dump without removing/reconnecting the charger.
// =============================================================================
rules.JSRule({
  name: 'K7 Power - Manual Relay Override',
  description: 'Manual relay ON triggers dump from DUMP_DONE or PARKED',
  triggers: [triggers.ItemCommandTrigger('MC_K7_Relay', 'ON')],
  execute: function () {
    try {
      var currentState = getState();
      if (currentState === STATES.DUMP_DONE || currentState === STATES.PARKED) {
        console.info(LOG + ': Manual relay ON - starting dump from ' + currentState);
        setState(STATES.TRANSFERRING, 'Manual relay ON');
        cancelTimer('maxOnTimer');
        var maxOnTimer = actions.ScriptExecution.createTimer(
          time.ZonedDateTime.now().plusMinutes(MAX_ON_MIN),
          function () {
            console.warn(LOG + ': SAFETY TIMEOUT - relay ON for ' + MAX_ON_MIN + 'min - forcing OFF');
            relayOff('Safety timeout ' + MAX_ON_MIN + 'min');
            setState(STATES.DUMP_DONE, 'Safety timeout');
          }
        );
        cache.private.put('maxOnTimer', maxOnTimer);
      }
    } catch (e) {
      console.error(LOG + ': Manual relay error: ' + e.message);
    }
  }
});

// =============================================================================
// Rule 5: Shelly WiFi Status Poller (SSID + RSSI dBm)
// Polls Shelly API every 60s for WiFi details not exposed by binding channels.
// =============================================================================
rules.JSRule({
  name: 'K7 Power - Shelly WiFi Poll',
  description: 'Poll Shelly Plus Uni for SSID and RSSI dBm',
  triggers: [triggers.GenericCronTrigger('0 * * * * ?')],
  execute: function () {
    try {
      var http = actions.HTTP;
      var json = http.sendHttpGetRequest('http://10.0.5.62/rpc/Wifi.GetStatus', 5000);
      if (json === null) {
        console.debug(LOG + ': Shelly WiFi poll - no response (device offline?)');
        return;
      }
      var data = JSON.parse(json);
      var ssid = data.ssid || '';
      var rssi = data.rssi;
      if (ssid) {
        items.getItem('MC_K7_Shelly_SSID').postUpdate(ssid);
      }
      if (typeof rssi === 'number') {
        items.getItem('MC_K7_Shelly_RSSI').postUpdate(rssi);
      }
    } catch (e) {
      console.debug(LOG + ': Shelly WiFi poll error: ' + e.message);
    }
  }
});

// =============================================================================
// Rule 6: Relay State Tracker (updates Relay_Since on any ON, clears on OFF)
// Fires for both rule-driven and manual sendCommand.
//
// Counter-punch: If relay goes ON while state is DUMP_DONE or COOLDOWN,
//   it was turned on externally (Shelly failsafe script). Immediately turn
//   it back OFF. The Shelly failsafe detects the external OFF and enters a
//   30-minute suppression period, preventing relay cycling.
// =============================================================================
rules.JSRule({
  name: 'K7 Power - Relay State Tracker',
  description: 'Tracks MC_K7_Relay ON/OFF from any source and updates Relay_Since',
  triggers: [triggers.ItemStateChangeTrigger('MC_K7_Relay')],
  execute: function () {
    var relayState = items.getItem('MC_K7_Relay').state;
    if (relayState === 'ON') {
      // Counter-punch: relay ON while DUMP_DONE or COOLDOWN = external (Shelly failsafe)
      var currentState = getState();
      if (currentState === STATES.DUMP_DONE || currentState === STATES.COOLDOWN || currentState === STATES.PARKED || currentState === STATES.CHARGING) {
        console.info(LOG + ': Relay ON detected in ' + currentState + ' - unexpected, counter-punching OFF');
        items.getItem('MC_K7_Relay').sendCommand('OFF');
        return;  // Don't update Relay_Since for the brief blip
      }
      items.getItem('MC_K7_Relay_Since').postUpdate(
        time.ZonedDateTime.now().format(time.DateTimeFormatter.ISO_OFFSET_DATE_TIME)
      );
      console.info(LOG + ': Relay ON detected - Relay_Since updated');
    } else {
      items.getItem('MC_K7_Relay_Since').postUpdate('NULL');
      console.info(LOG + ': Relay OFF detected - Relay_Since cleared');
    }
  }
});

// =============================================================================
// Rule 8: BLE Charger Online/Offline
// Victron BLE monitor posts MC_Charger_BLE_Online ON/OFF from the Pi.
//
//   BLE ON  + PARKED  -> check voltage, maybe start charger sequence
//   BLE OFF + DUMP_DONE -> definitive charger removal -> re-arm to PARKED
//   BLE OFF + CHARGING -> cancel stabilisation, back to PARKED
// =============================================================================
rules.JSRule({
  name: 'K7 Power - BLE Charger Online',
  description: 'Victron BLE online/offline triggers charger detection and re-arm',
  triggers: [triggers.ItemStateChangeTrigger('MC_Charger_BLE_Online')],
  execute: function () {
    try {
      var bleState = items.getItem('MC_Charger_BLE_Online').state;
      var currentState = getState();
      var voltage = parseFloat(items.getItem('MC_K7_Shelly_Voltage').state) || 0;

      if (bleState === 'ON') {
        console.info(LOG + ': BLE Online - V=' + voltage.toFixed(1) + 'V, state=' + currentState + ' [' + getBLEInfo() + ']');

        if (currentState === STATES.PARKED) {
          if (isBLECharging()) {
            console.info(LOG + ': BLE Online + actively charging \u2014 starting charger sequence');
            startChargerSequence(voltage, 'BLE Online');
          } else if (voltage > CHARGER_ON_V) {
            console.info(LOG + ': BLE Online + voltage ' + voltage.toFixed(1) + 'V > ' + CHARGER_ON_V + 'V \u2014 starting charger sequence');
            startChargerSequence(voltage, 'BLE Online');
          } else {
            console.info(LOG + ': BLE Online but not charging, V=' + voltage.toFixed(1) + 'V \u2014 charger powered, idle');
          }
        }
      } else if (bleState === 'OFF') {
        console.info(LOG + ': BLE Offline - V=' + voltage.toFixed(1) + 'V, state=' + currentState);

        if (currentState === STATES.DUMP_DONE) {
          cancelTimer('disconnectTimer');
          console.info(LOG + ': BLE Offline in DUMP_DONE \u2014 definitive charger removal, re-arming to PARKED');
          rearmToParked('BLE Offline \u2014 charger mains removed');
        } else if (currentState === STATES.CHARGING) {
          console.info(LOG + ': BLE Offline during CHARGING \u2014 cancelling stabilisation');
          cancelTimer('stabTimer');
          rearmToParked('BLE Offline during stabilisation');
        } else if (currentState === STATES.TRANSFERRING || currentState === STATES.COOLDOWN) {
          console.warn(LOG + ': BLE Offline during ' + currentState + ' \u2014 dump in progress, safety timer will handle relay');
        }
      }
    } catch (e) {
      console.error(LOG + ': BLE charger handler error: ' + e.message);
    }
  }
});

// =============================================================================
// Rule 9: BLE Charger State Change
// Triggers directly on MC_Charger_State changes.
// Catches: Idle -> Bulk (battery connected), Off -> Bulk (charger reconnected)
// =============================================================================
rules.JSRule({
  name: 'K7 Power - BLE Charge State',
  description: 'Reacts to Victron BLE charger state transitions for charger/battery detection',
  triggers: [triggers.ItemStateChangeTrigger('MC_Charger_State')],
  execute: function () {
    try {
      var bleState = getBLEState();
      var currentState = getState();
      var voltage = parseFloat(items.getItem('MC_K7_Shelly_Voltage').state) || 0;

      console.info(LOG + ': BLE State changed to ' + bleState + ' [V=' + voltage.toFixed(1) + 'V, state=' + currentState + ']');

      var chargingStates = ['Bulk', 'Absorption', 'Float', 'Recondition'];
      var connectedStates = ['Bulk', 'Absorption', 'Float', 'Storage', 'Recondition', 'Idle'];
      var isCharging = chargingStates.indexOf(bleState) >= 0;
      var isConnected = connectedStates.indexOf(bleState) >= 0;

      if ((isCharging || isConnected) && currentState === STATES.PARKED) {
        console.info(LOG + ': BLE ' + bleState + ' while PARKED \u2014 charger detected, starting sequence');
        startChargerSequence(voltage, 'BLE ' + bleState);
      } else if (isCharging && currentState === STATES.DUMP_DONE) {
        // Cancel any pending disconnect timer \u2014 charger is clearly still connected
        cancelTimer('disconnectTimer');
        console.info(LOG + ': BLE ' + bleState + ' while DUMP_DONE \u2014 charger active, staying DUMP_DONE (disconnect timer cancelled)');
      } else if ((bleState === 'Off' || bleState === 'Idle') && currentState === STATES.DUMP_DONE) {
        // Victron oscillates Float\u2194Idle on a fully charged battery.
        // Don't immediately re-arm \u2014 use a 5-min timer to confirm true disconnect.
        // If Float returns within 5 min, cancel the timer (it was just oscillation).
        var existingTimer = cache.private.get('disconnectTimer');
        if (existingTimer === null || existingTimer === undefined) {
          console.info(LOG + ': BLE state ' + bleState + ' in DUMP_DONE \u2014 starting 5-min disconnect timer [' + getBLEInfo() + ']');
          function disconnectCheck() {
            cache.private.put('disconnectTimer', null);
            var st = getState();
            if (st !== STATES.DUMP_DONE) {
              console.info(LOG + ': Disconnect timer fired but state is ' + st + ' \u2014 ignoring');
              return;
            }
            // Check voltage: if still above charger threshold, mains is still connected
            // \u2014 the Victron is just sitting in Idle on a full battery. Stay DUMP_DONE.
            var v = parseFloat(items.getItem('MC_K7_Shelly_Voltage').state) || 0;
            if (v > CHARGER_ON_V) {
              console.info(LOG + ': Disconnect timer fired but V=' + v.toFixed(1) + 'V (>' + CHARGER_ON_V + ') \u2014 charger mains still connected, restarting timer');
              var newDt = actions.ScriptExecution.createTimer(time.ZonedDateTime.now().plusMinutes(5), disconnectCheck);
              cache.private.put('disconnectTimer', newDt);
              return;
            }
            console.info(LOG + ': Disconnect timer fired \u2014 V=' + v.toFixed(1) + 'V confirms battery disconnected, re-arming to PARKED');
            rearmToParked('BLE Idle 5min + V=' + v.toFixed(1) + ' \u2014 battery disconnected');
          }
          var dt = actions.ScriptExecution.createTimer(time.ZonedDateTime.now().plusMinutes(5), disconnectCheck);
          cache.private.put('disconnectTimer', dt);
        } else {
          console.info(LOG + ': BLE state ' + bleState + ' in DUMP_DONE \u2014 disconnect timer already running [' + getBLEInfo() + ']');
        }
      } else if ((bleState === 'Off' || bleState === 'Idle') && currentState === STATES.CHARGING) {
        // Victron oscillates Float\u2194Idle on a fully charged battery.
        // Don't cancel stab timer \u2014 let it run and check BLE state at expiry.
        // If BLE is charging at expiry \u2192 relay ON. If Idle/Off \u2192 back to PARKED.
        console.info(LOG + ': BLE state ' + bleState + ' during CHARGING \u2014 stab timer still running [' + getBLEInfo() + ']');
      }
    } catch (e) {
      console.error(LOG + ': BLE state handler error: ' + e.message);
    }
  }
});

// =============================================================================
// Rule 10: Charger Connection Status
// Computes a human-readable MC_Charger_Connection string from BLE signals.
// =============================================================================
rules.JSRule({
  name: 'K7 Power - Charger Connection Status',
  description: 'Computes human-readable charger connection status from BLE signals',
  triggers: [
    triggers.ItemStateChangeTrigger('MC_Charger_BLE_Online'),
    triggers.ItemStateChangeTrigger('MC_Charger_State')
  ],
  execute: function () {
    updateChargerConnection();
  }
});
