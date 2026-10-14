#!/usr/bin/env bash
# Copy the live openHAB files into this published tree, redacting as they go.
#
# WHY THIS EXISTS
# A plain `cp` from the live tree was done on 2026-09-12 and check-public.sh
# immediately flagged the house IP range and the Shelly's MAC in four files. The
# copy step and the redaction step must not be two things a person remembers to
# do in order, because one of them will eventually be forgotten and the result is
# published.
#
# So: this is the only supported way to update the copies. Run it, then run
# ./tools/check-public.sh --all, then read the diff before committing.
#
# The placeholders match what is already committed, so re-running produces no
# spurious diff.
set -euo pipefail

LIVE="${LIVE:-/etc/openhab}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The real->placeholder map lives OUTSIDE this repository, for the same reason
# the scan patterns do: a mapping from real infrastructure to its placeholder
# contains the real infrastructure. Publishing the map publishes the thing.
REDACTIONS="${REDACTIONS:-$HOME/.config/innovv-k7/redactions.sed}"
if [ ! -r "$REDACTIONS" ]; then
  echo "REFUSING TO RUN: no redaction map at $REDACTIONS" >&2
  echo "Copying without it would publish the live addresses verbatim." >&2
  exit 2
fi

redact() { sed -f "$REDACTIONS"; }

copy() {  # copy <src> <dst>
  local src="$1" dst="$2"
  [ -f "$src" ] || { echo "  MISSING  $src" >&2; return 1; }
  mkdir -p "$(dirname "$dst")"
  redact < "$src" > "$dst"
  echo "  ok  $(basename "$dst")"
}

echo "Syncing from $LIVE"
copy "$LIVE/items/motorcycle_k7_power.items"                   "$HERE/openhab/items/motorcycle_k7_power.items"
copy "$LIVE/automation/js/vehicle-motorcycle-k7-power.js"       "$HERE/openhab/rules/vehicle-motorcycle-k7-power.js"
copy "$LIVE/automation/js/vehicle-motorcycle-k7-lead.js"        "$HERE/openhab/rules/vehicle-motorcycle-k7-lead.js"
copy "$LIVE/automation/js/vehicle-motorcycle-ignition.js"       "$HERE/openhab/rules/vehicle-motorcycle-ignition.js"
copy "$LIVE/automation/js/vehicle-motorcycle-k7-charge-history.js" "$HERE/openhab/rules/vehicle-motorcycle-k7-charge-history.js"
copy "$LIVE/shelly-scripts/k7-failsafe.js"                      "$HERE/shelly-scripts/k7-failsafe.js"
# The Victron BLE daemon the Pi runs (deployed from $LIVE/victron-ble to the Pi by hand,
# so this is the code on the Pi): added 2026-10-14 with the charge-preset prick.
copy "$LIVE/victron-ble/victron_ble_monitor.py"                 "$HERE/victron-ble/victron_ble_monitor.py"
copy "$LIVE/victron-ble/README.md"                              "$HERE/victron-ble/README.md"
copy "$LIVE/victron-ble/victron-ble-monitor.service"            "$HERE/victron-ble/victron-ble-monitor.service"
copy "$LIVE/victron-ble/requirements.txt"                       "$HERE/victron-ble/requirements.txt"
# Added 2026-10-14 after both had drifted: the dump service's items, and the icons
# this tree already carries (by name - a new icon is added here on purpose, not by glob).
copy "$LIVE/items/innovv_k7.items"                              "$HERE/openhab/items/innovv_k7.items"
for icon in "$HERE"/openhab/icons/*.svg; do
  copy "$LIVE/icons/classic/$(basename "$icon")"                "$icon"
done
# Deliberately NOT synced: openhab/things/shelly.things (the live file holds every Shelly in
# the house, with credentials - the copy here is a hand-made extract of the K7 one) and
# openhab/rules/vehicle-motorcycle-k7-notify.js (the public version notifies through
# myopenHAB instead of the private mail/SMS helpers and secrets).

echo
echo "Now run:  ./tools/check-public.sh --all"
