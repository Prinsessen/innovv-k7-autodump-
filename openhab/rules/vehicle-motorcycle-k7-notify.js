// =============================================================================
// INNOVV K7 Fault Notifications
// =============================================================================
// Pushes an alert whenever the K7 auto-dump service reports a fault. The Pi
// bridge writes every fault condition to the K7_Last_Error item:
//   - SD card low / full / removed / unformatted (cmd=3017 / cmd=3024)
//   - NAS free space low
//   - K7 httpd unresponsive after deletes
//   - dump-cycle errors
// so watching that single item covers all of them.
//
// Why NOT alert on K7_Camera_Online=OFF:
//   The K7 is only powered while the motorcycle charger is connected / during a
//   dump. OFF is the NORMAL resting state, so a plain "offline" alert would spam
//   constantly. Genuine problems while the camera IS online surface via
//   K7_Last_Error instead, which is what we notify on.
//
// De-dup / anti-spam:
//   - Identical error text is not re-sent within THROTTLE_MS (30 min).
//   - Baseline is seeded on load/startup so a script reload never re-fires an
//     error that was already standing.
//   - Empty / cleared error resets the de-dup so the next real fault alerts.
//
// Notification channel:
//   This public version uses the standard openHAB Cloud broadcast notification
//   (the free myopenHAB app) via actions.NotificationAction, which requires no
//   secrets. Swap sendAlert() for your own channel (email, Telegram, Pushover,
//   ntfy, etc.) if you prefer.
// =============================================================================

const { rules, triggers, items, actions, time } = require('openhab');

const LOG          = 'k7_notify';
const ERR_ITEM     = 'K7_Last_Error';
const THROTTLE_MS  = 30 * 60 * 1000;   // don't repeat the same error within 30 min
const SEED_DELAY_S = 20;               // let restored states settle on cold start

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------
function safeState(name) {
  try {
    const it = items.getItem(name);
    if (!it || it.isUninitialized) return null;
    const s = String(it.state);
    if (s === 'NULL' || s === 'UNDEF' || s === '') return null;
    return s;
  } catch (e) {
    return null;
  }
}

function nowMs() {
  return time.ZonedDateTime.now().toInstant().toEpochMilli();
}

// Pick a short severity label from the error text.
function classify(err) {
  const e = err.toLowerCase();
  if (e.indexOf('removed') >= 0 || e.indexOf('full') >= 0 ||
      e.indexOf('httpd') >= 0   || e.indexOf('unformat') >= 0) {
    return 'CRITICAL';
  }
  if (e.indexOf('low') >= 0) return 'WARNING';
  return 'ERROR';
}

// Send the alert. Uses openHAB Cloud broadcast (myopenHAB app). Replace with
// your own channel as needed.
function sendAlert(title, message) {
  try {
    actions.NotificationAction.sendBroadcastNotification(title + ' — ' + message);
    return true;
  } catch (e) {
    console.warn(LOG + ': NotificationAction unavailable (' + e.message +
      ') — logging instead: ' + title + ' — ' + message);
    return false;
  }
}

// ---------------------------------------------------------------------------
// Seed baseline on load/startup (no alert) so reloads don't re-fire
// ---------------------------------------------------------------------------
function seedBaseline(tag) {
  actions.ScriptExecution.createTimer('k7NotifySeed' + tag,
    time.ZonedDateTime.now().plusSeconds(SEED_DELAY_S), function () {
      const cur = safeState(ERR_ITEM);
      cache.private.put('lastErrText', cur || '');
      cache.private.put('lastErrAtMs', cur ? nowMs() : 0);
      cache.private.put('seeded', true);
      console.info(LOG + ': baseline seeded (' + tag + ') - current error=' +
        (cur ? '"' + cur + '"' : 'none'));
    });
}

rules.JSRule({
  name: 'K7 Notify - Seed Baseline',
  description: 'Seeds the fault baseline after startup (no alert) to avoid reload spam',
  triggers: [triggers.SystemStartlevelTrigger(100)],
  execute: function () { seedBaseline('Start'); }
});

// IIFE: seed on script reload too
(function () { seedBaseline('Reload'); })();

// ---------------------------------------------------------------------------
// Fault notifier — fires when K7_Last_Error changes
// ---------------------------------------------------------------------------
rules.JSRule({
  name: 'K7 Notify - Fault Alert',
  description: 'Alerts when the K7 auto-dump service reports a fault (de-duped)',
  triggers: [triggers.ItemStateChangeTrigger(ERR_ITEM)],
  execute: function (event) {
    try {
      if (!cache.private.get('seeded')) return;   // ignore churn during seeding

      const err = safeState(ERR_ITEM);

      // Error cleared -> reset de-dup so the next real fault alerts again.
      if (!err) {
        cache.private.put('lastErrText', '');
        cache.private.put('lastErrAtMs', 0);
        return;
      }

      // De-dup: same text within throttle window -> skip.
      const lastText = cache.private.get('lastErrText') || '';
      const lastAt   = cache.private.get('lastErrAtMs') || 0;
      if (err === lastText && (nowMs() - lastAt) < THROTTLE_MS) {
        console.info(LOG + ': duplicate error within throttle window - skipping');
        return;
      }

      cache.private.put('lastErrText', err);
      cache.private.put('lastErrAtMs', nowMs());

      const sev = classify(err);

      // Context snapshot for the alert message
      const sdFree  = safeState('K7_SD_Free_GB');
      const sdUsed  = safeState('K7_SD_Used_Pct');
      const sdCard  = safeState('K7_SD_Card_Status');
      const nasFree = safeState('K7_NAS_Free_GB');

      const msg = err +
        '. SD ' + (sdFree || '?') + 'GB free' + (sdUsed ? ' (' + sdUsed + '% used)' : '') +
        (sdCard ? ', card=' + sdCard : '') +
        '. NAS ' + (nasFree || '?') + 'GB.';

      const ok = sendAlert('K7 ' + sev, msg);
      console.info(LOG + ': fault alert (' + sev + ') sent=' + ok);

    } catch (e) {
      console.error(LOG + ': Error in fault notifier: ' + e.message);
    }
  }
});
