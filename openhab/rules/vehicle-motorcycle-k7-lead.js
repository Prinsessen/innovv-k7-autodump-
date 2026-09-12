/**
 * Is the garage-to-machine lead connected?
 *
 * THE QUESTION CANNOT BE ASKED PASSIVELY
 * ----------------------------------------------------------------------------
 * The sense resistor sits in the relay coil's return leg, so it only carries
 * current while the coil is energised. With the relay open the ADC reads zero
 * whether the lead is plugged in or lying on the bench — "no circuit" and "no
 * connection" are the same measurement.
 *
 * So this asks. Close the relay, wait, look, and open it again if the answer was
 * no. A probe, not a poll.
 *
 * MEASURED ON THE BENCH, 2026-09-12, before any of this was written
 * ----------------------------------------------------------------------------
 *   relay open, either way        0.000 V
 *   relay closed, lead connected  0.850 - 0.870 V
 *   relay closed, lead pulled     0.000 V, within one sample
 *
 * Pulling the connector mid-probe dropped it to zero immediately and it stayed
 * there; plugging back in brought it straight back. No intermediate state, no
 * noise, nothing to debounce. LEAD_THRESHOLD_V sits between the two with room on
 * both sides for a slack battery or a warm coil.
 *
 * The numbers behind it: 1 kΩ coil, 11.2 mA at 11.22 V across the coil, 25 %
 * above the G6S-2's guaranteed pull-in. The Shelly's ADC reads 0.27 V low, so
 * the true drop is about 1.12 V and everything under 0.27 V clamps to zero.
 *
 * WHAT THIS FILE DOES NOT DO
 * ----------------------------------------------------------------------------
 * It does not command the camera, decide anything about charging, or raise an
 * alarm when the lead is out. Not plugging in is a normal decision — there may be
 * no footage worth moving — and an alert that fires on ordinary behaviour teaches
 * people to ignore it. This publishes a fact. The state machine uses it as a
 * gate, and the sitemap shows it to whoever cares.
 */
const { rules, triggers, items, actions, time, cache } = require('openhab');
const { safeExecute } = require('openhab-helpers');

const LOG = 'k7_lead';
const SHELLY_IP = '192.168.1.62';
const RELAY_ID = 0;               // the Shelly terminal labelled 1; API counts from zero
const SENSE_ITEM = 'MC_K7_Sense_Voltage';
const LEGACY_ITEM = 'MC_K7_Shelly_Voltage';   // fed in parallel until consumers move
// Halfway between the two measured states, and not a tuned value: anything from
// 0.2 to 0.6 separates 0.000 from 0.860 with room to spare.
const LEAD_THRESHOLD_V = 0.30;
// The coil pulls in within milliseconds; this is the Shelly's own reporting
// latency, measured at up to two seconds on a 0.1 V report threshold.
const SETTLE_MS = 3000;
const HTTP_MS = 5000;

function rpc(path) {
  try {
    const r = actions.HTTP.sendHttpGetRequest('http://' + SHELLY_IP + '/rpc/' + path, HTTP_MS);
    return r === null ? null : JSON.parse(r);
  } catch (e) {
    console.warn(LOG + ': RPC ' + path + ' failed — ' + (e.message || e));
    return null;
  }
}

function senseVolts() {
  const r = rpc('Voltmeter.GetStatus?id=100');
  return (r && typeof r.voltage === 'number') ? r.voltage : null;
}

function setRelay(on) {
  return rpc('Switch.Set?id=' + RELAY_ID + '&on=' + (on ? 'true' : 'false')) !== null;
}

function relayIsOn() {
  const r = rpc('Switch.GetStatus?id=' + RELAY_ID);
  return (r && typeof r.output === 'boolean') ? r.output : null;
}

function publish(connected, volts) {
  if (volts !== null) {
    items.getItem(SENSE_ITEM).postUpdate(volts);
    items.getItem(LEGACY_ITEM).postUpdate(volts);
  }
  const prev = items.getItem('MC_K7_Lead_Connected').state;
  const now = connected ? 'ON' : 'OFF';
  items.getItem('MC_K7_Lead_Connected').postUpdate(now);
  items.getItem('MC_K7_Lead_Checked').postUpdate(time.ZonedDateTime.now().toString());
  if (prev === null || prev.toString() !== now) {
    console.info(LOG + ': lead ' + (connected ? 'CONNECTED' : 'not connected')
      + ' (' + (volts === null ? 'no reading' : volts.toFixed(3) + ' V') + ')');
  }
}

/**
 * Probe, and put the relay back where it was.
 *
 * The restore matters. A probe that leaves the coil energised has told the
 * camera to wake up as a side effect of asking a question, and a probe that
 * opens a relay the state machine deliberately closed would cut a dump in
 * progress. So the relay's prior state is read first and returned to afterwards.
 */
function probe(why) {
  const was = relayIsOn();
  if (was === null) {
    console.warn(LOG + ': Shelly unreachable — lead state left unchanged');
    return null;
  }
  if (!was && !setRelay(true)) {
    console.warn(LOG + ': could not close the relay to probe');
    return null;
  }
  actions.ScriptExecution.createTimer(
    time.ZonedDateTime.now().plusNanos(SETTLE_MS * 1000000),
    function () {
      safeExecute(LOG + ' probe-read', function () {
        const v = senseVolts();
        const connected = v !== null && v >= LEAD_THRESHOLD_V;
        publish(connected, v);
        if (!was) setRelay(false);      // put it back as we found it
        console.info(LOG + ': probe (' + why + ') → '
          + (v === null ? 'no reading' : v.toFixed(3) + ' V')
          + (was ? ' [relay was already closed, left closed]' : ' [relay restored open]'));
      });
    });
  return true;
}

// =============================================================================
// 1. Passive read — while the relay is closed, the sense voltage is live
// =============================================================================
// Every ten seconds, not every minute, and the reason is which failure this
// catches. While the coil is energised the camera has been told to wake and a
// transfer may be running — losing the lead there is the case that actually
// hurts, because the camera simply stops being told anything and the state
// machine carries on believing it is transferring.
//
// It costs nothing. There is no coil to pull and no camera to disturb: the
// current is already flowing and this only reads a number that is already there.
// A minute was the first value written, chosen for no reason at all.
rules.JSRule({
  name: 'K7 Lead - Passive Sense',
  description: 'While the coil is energised the sense voltage is a free live reading',
  triggers: [triggers.GenericCronTrigger('0/10 * * * * ?')],
  execute: function () {
    safeExecute(LOG + ' passive', function () {
      if (relayIsOn() !== true) return;   // nothing to read with the coil open
      const v = senseVolts();
      if (v === null) return;
      publish(v >= LEAD_THRESHOLD_V, v);
    });
  }
});

// =============================================================================
// 2. Probe when the charger arrives — the moment the answer starts to matter
// =============================================================================
// NOT on a timer, and the reason is the whole shape of this feature. The relay
// contact sits on the camera's YELLOW ignition-sense line, so every probe tells
// the K7 to wake for as long as the coil is held. A fifteen-minute schedule
// would do that ninety-six times a day, on a camera that is meant to be asleep,
// drawing from a battery that may not be on charge. The first draft of this file
// did exactly that, and it took the owner asking about keeping the two
// connectors independent to notice.
//
// So the probe is tied to the event that makes the answer useful: the charger
// becoming connected. That is the moment a dump becomes possible, and the only
// moment before it that anyone needs to know whether the lead is in.
//
// Charger connected with no lead is a perfectly normal choice — charge the
// machine, leave the camera alone — and it produces exactly one probe and then
// silence. No retry, no reminder, no error state.
rules.JSRule({
  name: 'K7 Lead - Probe When Charger Connects',
  description: 'Test for the lead once, when the charger arrives and a dump becomes possible',
  triggers: [
    triggers.ItemStateChangeTrigger('MC_Secondary_Connected', 'OFF', 'ON'),
    triggers.ItemStateChangeTrigger('MC_Charger_BLE_Online', 'OFF', 'ON')
  ],
  execute: function () {
    safeExecute(LOG + ' charger-arrived', function () {
      // Both triggers can fire within seconds of each other on the same event.
      const last = cache.private.get('lastProbeMs');
      if (last && (Date.now() - last) < 60000) return;
      cache.private.put('lastProbeMs', Date.now());
      probe('charger connected');
    });
  }
});

// =============================================================================
// 3. On demand — the state machine asks before it commits to anything
// =============================================================================
rules.JSRule({
  name: 'K7 Lead - Probe On Demand',
  description: 'Probe when something is about to depend on the answer',
  triggers: [triggers.ItemCommandTrigger('MC_K7_Lead_Connected', 'REFRESH')],
  execute: function () { safeExecute(LOG + ' demand', function () { probe('on demand'); }); }
});

// =============================================================================
// 3b. Automatic while the charger is connected — and only then
// =============================================================================
// The lead cannot be sensed passively. To read anything the ADC needs 0.27 V
// across the 100 ohm shunt, which is 2.7 mA, which is 2.7 V across a 1 kOhm
// coil — and a G6S-2 is only guaranteed to RELEASE below 1.2 V. A permanent
// sense current large enough to see is large enough to hold the contact closed.
// There is no window, so there is no passive answer, and this probes instead.
//
// Gated on the charger because that is the only time the cost is nil and the
// answer is worth having. The machine is home and on charge, so three seconds of
// coil are free; and a dump is only possible while charging anyway, so the
// status is live exactly when it means something.
//
// Charger not connected: no probes at all. The answer does not matter, and the
// camera is left alone.
rules.JSRule({
  name: 'K7 Lead - Automatic While Charging',
  description: 'Refresh the lead status every 30 minutes, but only while the charger is connected',
  triggers: [triggers.GenericCronTrigger('0 7/30 * * * ?')],
  execute: function () {
    safeExecute(LOG + ' auto', function () {
      const c = items.getItem('MC_Secondary_Connected').state;
      if (c === null || c.toString() !== 'ON') return;   // charger clamps off: not our business

      // Already known connected? Then do not probe.
      //
      // A probe closes the relay, and with the lead IN that puts 12 V on the
      // camera's ignition line and wakes it for the three seconds we hold the
      // coil. With the lead OUT there is no circuit at all: no coil current, the
      // contact never moves, and the camera cannot tell the probe happened.
      //
      // So the probe is free exactly when it has something to tell us, and costs
      // a wake-up exactly when it does not. Gate on it.
      //
      // Found 2026-09-12, after the owner finished mounting the bike and watched
      // the camera come on. This rule fires at :07 and :37 — 48 wake-ups a day
      // on a camera meant to be asleep. The file's own comment two rules up
      // rejects a 15-minute timer for doing it 96 times a day; this was the same
      // mistake at half the rate, written directly underneath.
      //
      // Losing the lead while parked is still caught: the relay is open then, so
      // nothing can run anyway, and the charger-arrival probe re-reads it before
      // any dump is allowed to start. During a transfer the relay is closed, so
      // the passive sense sees it go within ten seconds.
      const known = items.getItem('MC_K7_Lead_Connected').state;
      if (known !== null && known.toString() === 'ON') return;

      probe('automatic, charger connected, lead believed out');
    });
  }
});

// =============================================================================
// 4. Test button — the only way to check from the screen
// =============================================================================
// Resting, the answer is unobtainable: with the relay open the sense resistor
// carries nothing, so lead in and lead out read the same zero. This is how a
// person asks. It costs one brief coil pull, which is the same cost the state
// machine pays when it asks, and it is a deliberate act rather than a timer.
rules.JSRule({
  name: 'K7 Lead - Test Button',
  description: 'Probe on demand from the sitemap, then reset the button',
  triggers: [triggers.ItemCommandTrigger('MC_K7_Lead_Test', 'ON')],
  execute: function () {
    safeExecute(LOG + ' test-button', function () {
      probe('test button');
      actions.ScriptExecution.createTimer(
        time.ZonedDateTime.now().plusSeconds(1),
        function () { items.getItem('MC_K7_Lead_Test').postUpdate('OFF'); });
    });
  }
});

console.info(LOG + ': loaded — threshold ' + LEAD_THRESHOLD_V + ' V, probes on charger arrival and on demand');
