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
// Rule 1: Ignition ON Notification
// ---------------------------------------------------------------------------
rules.JSRule({
  name: 'Springfield Ignition ON Notification',
  description: 'Sends email+SMS when ignition turns ON with vehicle telemetry',
  triggers: [triggers.ItemStateChangeTrigger('Vehicle10_Ignition', 'OFF', 'ON')],
  execute: function () {
    try {
      // Debounce: suppress if <30 s since last ON notification
      const now = Date.now();
      const lastOn = cache.private.get('lastIgnitionOnTime') || 0;
      if (now - lastOn < 30000) {
        console.info('springfield_ignition: Ignition ON suppressed (debounce)');
        return;
      }
      cache.private.put('lastIgnitionOnTime', now);

      // Suppress false ignition from K7 auto-power MOSFET relay
      const k7State = items.getItem('MC_K7_Power_State').state;
      if (['CHARGING', 'TRANSFERRING', 'COOLDOWN'].indexOf(k7State) !== -1) {
        console.info('springfield_ignition: Ignition ON suppressed (K7 auto-power state: ' + k7State + ')');
        return;
      }

      const v = getVehicleData('dd/MM HH:mm');

      console.info('springfield_ignition: Ignition ON - Odometer: ' + v.odometer);
      console.info('springfield_ignition: Ignition ON - sending notification');

      const mail = actions.Things.getActions('mail', notify.mailThing);

      const body = buildEmail({
        headerColor: '#667eea',
        headerTitle: '&#127949; SPRINGFIELD STARTED',
        headerSubtitle: 'Springfield - Indian Springfield 1811ccm',
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

    } catch (e) {
      console.error('springfield_ignition: Error sending ignition ON notification: ' + e.message);
    }
  }
});

// ---------------------------------------------------------------------------
// Rule 2: Ignition OFF Notification
// ---------------------------------------------------------------------------
rules.JSRule({
  name: 'Springfield Ignition OFF Notification',
  description: 'Sends email+SMS when ignition turns OFF with parked location',
  triggers: [triggers.ItemStateChangeTrigger('Vehicle10_Ignition', 'ON', 'OFF')],
  execute: function () {
    try {
      // Debounce: suppress if <30 s since last OFF notification
      const now = Date.now();
      const lastOff = cache.private.get('lastIgnitionOffTime') || 0;
      if (now - lastOff < 30000) {
        console.info('springfield_ignition: Ignition OFF suppressed (debounce)');
        return;
      }
      cache.private.put('lastIgnitionOffTime', now);

      // Suppress false ignition from K7 auto-power MOSFET relay
      const k7off = items.getItem('MC_K7_Power_State').state;
      if (['CHARGING', 'TRANSFERRING', 'COOLDOWN'].indexOf(k7off) !== -1) {
        console.info('springfield_ignition: Ignition OFF suppressed (K7 auto-power state: ' + k7off + ')');
        return;
      }

      const v = getVehicleData('dd/MM HH:mm');

      console.info('springfield_ignition: Ignition OFF - Odometer: ' + v.odometer);
      console.info('springfield_ignition: Ignition OFF - sending notification');

      const mail = actions.Things.getActions('mail', notify.mailThing);

      const body = buildEmail({
        headerColor: '#f5576c',
        headerTitle: '&#127359; SPRINGFIELD PARKED',
        headerSubtitle: 'Springfield - Indian Springfield 1811ccm',
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

    } catch (e) {
      console.error('springfield_ignition: Error sending ignition OFF notification: ' + e.message);
    }
  }
});
