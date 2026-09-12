#!/bin/bash
# Refuse to publish house infrastructure. Run before every push.
#
# WHY THIS EXISTS HERE
# --------------------------------------------------------------------------
# This repository has been clean so far on care alone -- reading the diff before
# pushing. That worked, and it is exactly what the CAN bus project was doing
# right up until it carried a real OTA URL across twice in two days. Care is not
# a process; it is a thing you have less of when you are in a hurry.
#
# The specific risk here is known and dated. On 2026-09-12 the Shelly moved to
# the garage, and K7_AUTO_POWER_README.md in the private tree was rewritten with
# the new architecture -- including the Shelly's address, 10.0.5.62. There is no
# 10.0.x anywhere in this published tree today. The day that document is copied
# across, there will be.
#
#   tools/check-public.sh          scan the working tree
#   tools/check-public.sh --all    scan the whole history as well (slower)
#
# Exits non-zero on any hit. A hit is not automatically a leak -- read what it
# found. Some strings below are deliberately published and are excluded by name
# rather than by pattern, so that the exclusion is visible.
set -u
cd "$(dirname "$0")/.." || exit 2

PATTERNS=(
    '10\.0\.[0-9]+\.[0-9]+'    # the house LAN. 192.168.1.x is the CAMERA's own AP -- not this
    '10\.235\.'                # the tracker's cellular address
    'mqtt\.agesen\.dk'         # the broker. NOT bare agesen.dk: the author byline is deliberate
    # The Shelly's device name carries its MAC, so the MAC is the thing to look
    # for -- not the prefix. The tree already uses shellyplusuni-a1b2c3d4e5f6 as a
    # placeholder, and a pattern that flags the placeholder is a pattern that gets
    # waved through, which is the failure this file exists to prevent.
    'e08cfe8b1c3c|E08CFE8B1C3C'
    'adminops@'
    # The key's CONTENT, not its filename. victron-ble/README.md legitimately
    # documents "scp -i ~/.ssh/id_ed25519 ...", which is a standard default
    # filename in a worked example and not a disclosure.
    'BEGIN [A-Z ]*PRIVATE KEY'
    '56KTHAAAXH3343342'        # the VIN
    # A credential with a real-looking value. The K7's own AP password
    # ("12345678", the camera's factory default) is public knowledge and is not
    # what this is looking for.
    'MQTT_PASSWORD +"(?!YOUR_|CHANGE|xxx|)'
    'Jekboapj'                 # the broker password, seen in the private tree
)

fail=0
echo "Scanning the working tree..."
for p in "${PATTERNS[@]}"; do
    hits=$(grep -rIlP "$p" . --exclude-dir=.git --exclude=check-public.sh 2>/dev/null)
    if [ -n "$hits" ]; then
        echo "  HIT  $p"
        echo "$hits" | sed 's/^/         /'
        fail=1
    fi
done

if [ "${1:-}" = "--all" ]; then
    echo "Scanning history (this takes a moment)..."
    for p in "${PATTERNS[@]}"; do
        hits=$(git grep -I -l -P "$p" $(git rev-list --all) 2>/dev/null \
               | awk -F: '{print $2}' | sort -u | grep -v check-public.sh)
        if [ -n "$hits" ]; then
            echo "  HIT IN HISTORY  $p"
            echo "$hits" | sed 's/^/         /'
            fail=1
        fi
    done
fi

if [ "$fail" = "0" ]; then
    echo "Clean. Nothing found that should not be published."
else
    echo
    echo "Read each hit before deciding. Published history cannot be withdrawn."
fi
exit $fail
