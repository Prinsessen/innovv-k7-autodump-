// =============================================================================
// K7 Auto-Power State Machine - Shelly Plus Uni Relay Control
// =============================================================================
const { rules, triggers, items, actions, time } = require('openhab');

const LOG = 'k7_power';
const CHARGER_V   = 13.0;
const LOW_BATT_V  = 12.0;
const STAB_SEC    = 60;
const MAX_ON_MIN  = 30;
const COOLDOWN_S  = 30;
const IGN_DEBOUNCE_S = 30;

const STATES = {
  PARKED: 'PARKED', RIDING: 'RIDING', CHARGING: 'CHARGING',
  TRANSFERRING: 'TRANSFERRING', COOLDOWN: 'COOLDOWN', LOW_BATTERY: 'LOW_BATTERY'
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

function startChargerSequence(voltage, source) {
  var currentState = getState();
  if (currentState !== STATES.PARKED) return;
  if (voltage <= CHARGER_V) return;

  items.getItem('MC_Charger_Detected').postUpdate('ON');
  setState(STATES.CHARGING, source + ' V=' + voltage.toFixed(1) + 'V');
  console.info(LOG + ': Charger detected (' + voltage.toFixed(1) + 'V), ' + STAB_SEC + 's stabilisation...');

  cancelTimer('stabTimer');
  var stabTimer = actions.ScriptExecution.createTimer(
    time.ZonedDateTime.now().plusSeconds(STAB_SEC),
    function () {
      var recheck = parseFloat(items.getItem('MC_K7_Shelly_Voltage').state);
      if (recheck > CHARGER_V) {
        console.info(LOG + ': Stabilisation complete, V=' + recheck.toFixed(1) + 'V - relay ON');
        setState(STATES.TRANSFERRING, 'Charger confirmed after ' + STAB_SEC + 's');
        relayOn('Charger confirmed');

        cancelTimer('maxOnTimer');
        var maxOnTimer = actions.ScriptExecution.createTimer(
          time.ZonedDateTime.now().plusMinutes(MAX_ON_MIN),
          function () {
            console.warn(LOG + ': SAFETY TIMEOUT - relay ON for ' + MAX_ON_MIN + 'min - forcing OFF');
            relayOff('Safety timeout ' + MAX_ON_MIN + 'min');
            setState(STATES.PARKED, 'Safety timeout');
            items.getItem('MC_Charger_Detected').postUpdate('OFF');
          }
        );
        cache.private.put('maxOnTimer', maxOnTimer);
      } else {
        console.info(LOG + ': Stabilisation failed, V=' + recheck.toFixed(1) + 'V - back to PARKED');
        setState(STATES.PARKED, 'Voltage dropped during stabilisation');
        items.getItem('MC_Charger_Detected').postUpdate('OFF');
      }
    }
  );
  cache.private.put('stabTimer', stabTimer);
}

// Rule 1: System Init
rules.JSRule({
  name: 'K7 Power - System Init',
  description: 'Initialize K7 power state machine on system start',
  triggers: [triggers.SystemStartlevelTrigger(100)],
  execute: function () {
    console.info(LOG + ': System start - initializing');
    setState(STATES.PARKED, 'System start');
    items.getItem('MC_Charger_Detected').postUpdate('OFF');
    relayOff('System start');
  }
});

// Rule 2: Ignition Handler (debounced)
// The K7 MOSFET output shares the ignition-switched circuit. When the relay
// toggles, the FMM920 sees brief false ignition ON/OFF events (~5-10s).
// A 30s debounce filters these; real riding keeps ignition ON for minutes.
rules.JSRule({
  name: 'K7 Power - Ignition Handler',
  description: 'Handles ignition ON/OFF with debounce to filter MOSFET-induced false triggers',
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
            var recheck = items.getItem('Vehicle10_Ignition').state;
            if (recheck !== 'ON') {
              console.info(LOG + ': Ignition debounce expired but ignition now OFF - ignoring');
              return;
            }
            var currentState = getState();
            // If relay is ON, ignition signal during transfer/cooldown is MOSFET back-feed — ignore
            // During CHARGING relay is OFF (no back-feed possible) so real ignition is allowed through
            var relayIsOn = items.getItem('MC_K7_Relay').state === 'ON';
            if (relayIsOn && (currentState === STATES.TRANSFERRING || currentState === STATES.COOLDOWN)) {
              console.info(LOG + ': Ignition suppressed - back-feed from MOSFET (relay ON, state: ' + currentState + ')');
              return;
            }
            // During CHARGING (relay OFF), cancel stab timer — this is real ignition, not charger
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
        // If debounce timer is pending, cancel it - ignition was too brief
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
        console.info(LOG + ': Ignition OFF - checking voltage');
        setState(STATES.PARKED, 'Ignition OFF');
        var checkTimer = actions.ScriptExecution.createTimer(
          time.ZonedDateTime.now().plusSeconds(10),
          function () {
            var v = parseFloat(items.getItem('MC_K7_Shelly_Voltage').state);
            console.info(LOG + ': Post-ignition voltage check: ' + v.toFixed(1) + 'V');
            if (v > CHARGER_V) startChargerSequence(v, 'Post-ignition');
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
      if (currentState === STATES.PARKED && voltage > CHARGER_V) {
        startChargerSequence(voltage, 'Voltage');
      }
      if (currentState === STATES.CHARGING && voltage <= CHARGER_V) {
        console.info(LOG + ': Charger removed during stabilisation (' + voltage.toFixed(1) + 'V)');
        cancelTimer('stabTimer');
        setState(STATES.PARKED, 'Charger removed during stabilisation');
        items.getItem('MC_Charger_Detected').postUpdate('OFF');
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
            console.info(LOG + ': Cooldown complete - relay OFF');
            relayOff('Dump complete + cooldown');
            setState(STATES.PARKED, 'Dump cycle complete');
            items.getItem('MC_Charger_Detected').postUpdate('OFF');
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
      var json = http.sendHttpGetRequest('http://192.168.1.62/rpc/Wifi.GetStatus', 5000);
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
// =============================================================================
rules.JSRule({
  name: 'K7 Power - Relay State Tracker',
  description: 'Tracks MC_K7_Relay ON/OFF from any source and updates Relay_Since',
  triggers: [triggers.ItemStateChangeTrigger('MC_K7_Relay')],
  execute: function () {
    var state = items.getItem('MC_K7_Relay').state;
    if (state === 'ON') {
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
