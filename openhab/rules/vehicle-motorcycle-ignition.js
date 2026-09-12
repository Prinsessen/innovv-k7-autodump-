// =============================================================================
// Springfield Ignition Notifications
// Migrated from: Springfield_Ignition.rules
// @original-file  springfield_ignition.js
// =============================================================================
// Sends email + SMS when motorcycle ignition turns ON or OFF.
// Includes location (Google Maps + OpenStreetMap), speed, odometer, hours,
// GPS satellite count. 30-second debounce via cache.private timestamps.
//
// Rules:
//   1. Ignition ON  - Engine started notification with full telemetry
//   2. Ignition OFF - Parked notification with location
//
// Items:
//   - Vehicle10_Ignition     : Switch (ON/OFF)
//   - Vehicle10_Position     : Location "lat,lon"
//   - Vehicle10_Address      : String address
//   - Vehicle10_Speed        : Number km/h
//   - Vehicle10_TotalDistance : Number km (odometer)
//   - Vehicle10_Hours        : Number hours
//   - Vehicle10_GpsSatellites: Number
//
// Notifications:
//   - HTML email (recipient from notification secrets)
//   - SMS (recipient from notification secrets)
//   - Recipients loaded from /etc/openhab/secrets/notification-recipients.json
// =============================================================================

const { rules, triggers, items, actions, time } = require('openhab');
const notify = require('notification-config');
const { buildEmail } = require('email-builder');
const { getVehicleData } = require('motorcycle-helpers');

// ---------------------------------------------------------------------------
// Ignition ON / OFF notification (anti-flap settle timer)
// ---------------------------------------------------------------------------
// The Teltonika ignition line can flap OFF<->ON several times within a few
// seconds (voltage transients / telemetry glitch). With one rule per direction
// that produced BOTH a "Started" and a "Parked" email/SMS at the same time.
// Fix: on any ignition change, (re)start a short settle timer and only notify
// once the state has been STABLE, and only if it differs from the last state
// we actually notified about. Rapid flaps collapse to a single final message.
const IGN_SETTLE_S = 8;

function springfieldIgnitionNotify(state) {
  // Suppress false ignition raised by the K7 auto-power circuit driving the
  // ignition sense line (G6S-2 contact since 2026-09-12, MOSFET before that)
  const k7State = items.getItem('MC_K7_Power_State').state;
  if (['CHARGING', 'TRANSFERRING', 'COOLDOWN'].indexOf(k7State) !== -1) {
    console.info('springfield_ignition: Ignition ' + state + ' suppressed (K7 auto-power state: ' + k7State + ')');
    return;
  }

  const v = getVehicleData('dd/MM HH:mm');
  const mail = actions.Things.getActions('mail', notify.mailThing);

  if (state === 'ON') {
    console.info('springfield_ignition: Ignition ON - Odometer: ' + v.odometer + ' - sending notification');
    const body = buildEmail({
      headerColor: '#667eea',
      headerTitle: '&#127949; SPRINGFIELD STARTED',
      headerSubtitle: 'Springfield - Indian Springfield Thunderstroke 111cci',
      timestamp: v.ts,
      location: {
        address: v.address,
        lat: v.lat,
        lon: v.lon,
        linkColor: '#667eea'
      },
      dataRows: [
        { icon: '&#128293;', label: 'Ignition', value: 'ON', bgColor: '#e8f5e9' },
        { icon: '&#9889;', label: 'Speed', value: v.speed, bgColor: '#fff3e0' },
        { icon: '&#128207;', label: 'Odometer', value: v.odometer, bgColor: '#e3f2fd' },
        { icon: '&#9201;', label: 'Hours', value: v.hours, bgColor: '#f3e5f5' },
        { icon: '&#128225;', label: 'GPS', value: v.satellites + ' sats', bgColor: '#e0f2f1' }
      ],
      footerText: '<strong>Device:</strong> Springfield FMM920 (ID: 10)'
    });
    const s1 = mail.sendHtmlMail(notify.nanna.email, 'Springfield Ignition ON', body);
    console.info('springfield_ignition: Ignition ON email: ' + s1);
    const s2 = mail.sendMail(notify.nanna.sms, 'Springfield Ignition ON',
      'Springfield ignition started at ' + v.ts + '. Location: ' + v.address);
    console.info('springfield_ignition: Ignition ON SMS: ' + s2);
  } else {
    console.info('springfield_ignition: Ignition OFF - Odometer: ' + v.odometer + ' - sending notification');
    const body = buildEmail({
      headerColor: '#f5576c',
      headerTitle: '&#127359; SPRINGFIELD PARKED',
      headerSubtitle: 'Springfield - Indian Springfield Thunderstroke 111cci',
      timestamp: v.ts,
      location: {
        address: v.address,
        lat: v.lat,
        lon: v.lon,
        linkColor: '#f5576c',
        title: '&#128205; Parked Location'
      },
      dataRows: [
        { icon: '&#128268;', label: 'Ignition', value: 'OFF', bgColor: '#ffebee' },
        { icon: '&#128207;', label: 'Odometer', value: v.odometer, bgColor: '#e3f2fd' },
        { icon: '&#9201;', label: 'Hours', value: v.hours, bgColor: '#f3e5f5' },
        { icon: '&#128225;', label: 'GPS', value: v.satellites + ' sats', bgColor: '#e0f2f1' }
      ],
      footerText: '<strong>Device:</strong> Springfield FMM920 (ID: 10)'
    });
    const s1 = mail.sendHtmlMail(notify.nanna.email, 'Springfield Ignition OFF - Parked', body);
    console.info('springfield_ignition: Ignition OFF email: ' + s1);
    const s2 = mail.sendMail(notify.nanna.sms, 'Springfield Parked',
      'Springfield parked at ' + v.ts + '. Location: ' + v.address);
    console.info('springfield_ignition: Ignition OFF SMS: ' + s2);
  }
}

rules.JSRule({
  name: 'Springfield Ignition Change Notification',
  description: 'Email+SMS on STABLE ignition ON/OFF (anti-flap settle timer)',
  triggers: [triggers.ItemStateChangeTrigger('Vehicle10_Ignition')],
  execute: function () {
    try {
      // (Re)start the settle timer on every change; only the last one survives.
      const existing = cache.private.get('sfIgnSettleTimer');
      if (existing) { try { existing.cancel(); } catch (e0) {} }

      const t = actions.ScriptExecution.createTimer('sfIgnSettle',
        time.ZonedDateTime.now().plusSeconds(IGN_SETTLE_S), function () {
          cache.private.put('sfIgnSettleTimer', null);
          try {
            const stable = String(items.getItem('Vehicle10_Ignition').state);
            if (stable !== 'ON' && stable !== 'OFF') return;

            const lastNotified = cache.private.get('sfIgnLastNotified');
            if (stable === lastNotified) {
              console.info('springfield_ignition: Ignition settled ' + stable + ' — same as last notified, skip');
              return;
            }
            cache.private.put('sfIgnLastNotified', stable);

            springfieldIgnitionNotify(stable);
          } catch (e2) {
            console.error('springfield_ignition: Error ignition settle: ' + e2.message);
          }
        });
      cache.private.put('sfIgnSettleTimer', t);
    } catch (e) {
      console.error('springfield_ignition: Error ignition change: ' + e.message);
    }
  }
});
