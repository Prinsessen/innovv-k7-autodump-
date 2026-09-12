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

echo
echo "Now run:  ./tools/check-public.sh --all"
