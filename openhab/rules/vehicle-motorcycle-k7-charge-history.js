// =============================================================================
// Motorcycle K7 — Charge Session History Logger
// =============================================================================
// Tracks each Victron charger session and logs a summary to InfluxDB via
// openHAB items. Each completed charge cycle (CHARGING → DUMP_DONE) produces
// one history row with: start/end time, duration, voltage delta, energy, stage.
//
// Trigger: MC_K7_Power_State changes
//   CHARGING  → record session start (timestamp + voltage + yield)
//   DUMP_DONE → record session end and compute summary
//
// Items updated (persisted to InfluxDB via everyChange):
//   MC_Charge_Session_Start    — session start timestamp
//   MC_Charge_Session_End      — session end timestamp
//   MC_Charge_Session_Minutes  — duration in minutes
//   MC_Charge_Session_V_Start  — battery voltage at start (V)
//   MC_Charge_Session_V_End    — battery voltage at end (V)
//   MC_Charge_Session_V_Delta  — voltage change (V)
//   MC_Charge_Session_Wh       — energy charged this session (Wh)
//   MC_Charge_Session_Peak     — highest Victron stage reached
//   MC_Charge_Session_Count    — lifetime session counter
// =============================================================================

var LOG = 'k7_charge_history';

// Stage priority (higher = deeper charge)
var STAGE_PRIORITY = {
  'Off': 0, 'Idle': 0,
  'Bulk': 1, 'Absorption': 2, 'Float': 3, 'Storage': 4, 'Recondition': 5
};

// ---- Rule 1: Track charge session lifecycle ----
rules.JSRule({
  name: 'K7 Charge History - Session Tracker',
  description: 'Logs charge sessions to InfluxDB when power state transitions happen',
  triggers: [triggers.ItemStateChangeTrigger('MC_K7_Power_State')],
  execute: function (event) {
    try {
      var newState = event.newState.toString();
      var oldState = event.oldState ? event.oldState.toString() : '';

      // --- Session START: entering CHARGING ---
      if (newState === 'CHARGING') {
        var voltage = parseFloat(items.getItem('MC_K7_Shelly_Voltage').state) || 0;
        var yield_kWh = parseFloat(items.getItem('MC_Charger_Yield').state) || 0;
        var now = time.ZonedDateTime.now();

        // Store session start data in cache
        cache.private.put('sessionStart', now.toString());
        cache.private.put('sessionStartV', voltage);
        cache.private.put('sessionStartYield', yield_kWh);
        cache.private.put('sessionPeakStage', 'Idle');

        // Update the start item immediately
        items.getItem('MC_Charge_Session_Start').postUpdate(now.toString());

        console.info(LOG + ': Session started — V=' + voltage.toFixed(2) + 'V, yield=' + yield_kWh.toFixed(3) + ' kWh');
      }

      // --- Session END: arriving at DUMP_DONE from a charge cycle ---
      if (newState === 'DUMP_DONE' && cache.private.get('sessionStart') !== null) {
        var endTime = time.ZonedDateTime.now();
        var startStr = cache.private.get('sessionStart');
        var startV = cache.private.get('sessionStartV') || 0;
        var startYield = cache.private.get('sessionStartYield') || 0;
        var peakStage = cache.private.get('sessionPeakStage') || 'Unknown';

        var endV = parseFloat(items.getItem('MC_K7_Shelly_Voltage').state) || 0;
        var endYield = parseFloat(items.getItem('MC_Charger_Yield').state) || 0;

        // Calculate duration
        var startTime = time.ZonedDateTime.parse(startStr);
        var durationMs = java.time.Duration.between(startTime, endTime).toMillis();
        var durationMin = durationMs / 60000.0;

        // Calculate energy (Wh)
        var energyWh = (endYield - startYield) * 1000;
        if (energyWh < 0) energyWh = 0; // Guard against yield reset

        // Calculate voltage delta
        var vDelta = endV - startV;

        // Update session count
        var countItem = items.getItem('MC_Charge_Session_Count');
        var count = (!countItem.isUninitialized && countItem.state !== 'NULL')
          ? parseFloat(countItem.state) : 0;
        count += 1;

        // Post all session items (triggers InfluxDB everyChange persistence)
        items.getItem('MC_Charge_Session_End').postUpdate(endTime.toString());
        items.getItem('MC_Charge_Session_Minutes').postUpdate(durationMin);
        items.getItem('MC_Charge_Session_V_Start').postUpdate(startV);
        items.getItem('MC_Charge_Session_V_End').postUpdate(endV);
        items.getItem('MC_Charge_Session_V_Delta').postUpdate(vDelta);
        items.getItem('MC_Charge_Session_Wh').postUpdate(energyWh);
        items.getItem('MC_Charge_Session_Peak').postUpdate(peakStage);
        items.getItem('MC_Charge_Session_Count').postUpdate(count);

        console.info(LOG + ': Session #' + count + ' complete — ' +
          durationMin.toFixed(1) + ' min, ' +
          startV.toFixed(2) + 'V → ' + endV.toFixed(2) + 'V (Δ' + vDelta.toFixed(2) + 'V), ' +
          energyWh.toFixed(1) + ' Wh, peak=' + peakStage);

        // Clear cache
        cache.private.put('sessionStart', null);
        cache.private.put('sessionStartV', null);
        cache.private.put('sessionStartYield', null);
        cache.private.put('sessionPeakStage', null);
      }

      // --- Session ABORTED: went back to PARKED without completing ---
      if (newState === 'PARKED' && oldState === 'CHARGING') {
        if (cache.private.get('sessionStart') !== null) {
          console.info(LOG + ': Session aborted (CHARGING → PARKED) — stab failed or charger removed');
          cache.private.put('sessionStart', null);
          cache.private.put('sessionStartV', null);
          cache.private.put('sessionStartYield', null);
          cache.private.put('sessionPeakStage', null);
        }
      }
    } catch (e) {
      console.error(LOG + ': Session tracker error: ' + e.message);
    }
  }
});

// ---- Rule 2: Track peak charge stage ----
rules.JSRule({
  name: 'K7 Charge History - Peak Stage Tracker',
  description: 'Tracks highest Victron charge stage during active session',
  triggers: [triggers.ItemStateChangeTrigger('MC_Charger_State')],
  execute: function (event) {
    try {
      // Only track during active sessions
      if (cache.private.get('sessionStart') === null) return;

      var newStage = event.newState.toString();
      var currentPeak = cache.private.get('sessionPeakStage') || 'Idle';
      var newPriority = STAGE_PRIORITY[newStage] || 0;
      var currentPriority = STAGE_PRIORITY[currentPeak] || 0;

      if (newPriority > currentPriority) {
        cache.private.put('sessionPeakStage', newStage);
        console.info(LOG + ': Peak stage updated: ' + currentPeak + ' → ' + newStage);
      }
    } catch (e) {
      console.error(LOG + ': Peak stage tracker error: ' + e.message);
    }
  }
});
