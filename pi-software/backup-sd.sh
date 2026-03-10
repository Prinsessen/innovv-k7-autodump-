#!/bin/bash
# ============================================================
# INNOVV K7 Pi - Monthly SD Card Backup
# ============================================================
# Creates a compressed image of the Pi's SD card on the NAS.
# Keeps the 3 most recent backups, deleting older ones.
#
# IMPORTANT: Stops the K7 dump service during backup to avoid
# I/O and CPU contention (gzip + dd saturates the Pi 4).
# The dump service is restarted automatically after completion.
#
# Nothing is written to the Pi's SD card — all output goes
# directly to NAS via pipe (dd | gzip > NAS).
#
# Install: copy to /opt/innovv-k7/backup-sd.sh on the Pi
# Cron:    0 3 1 * *  /opt/innovv-k7/backup-sd.sh >> /var/log/innovv-k7-backup.log 2>&1
#          (Runs 1st of every month at 03:00)
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/config.json"
HOSTNAME="$(hostname)"
DATE="$(date +%Y-%m-%d)"
DEVICE="/dev/mmcblk0"
KEEP_VERSIONS=3
LOG_TAG="pi-backup"
DUMP_SERVICE="innovv-k7-dump.service"

# Read settings from config.json (same dir as this script)
NAS_MOUNT=$(python3 -c "import json; print(json.load(open('${CONFIG_FILE}'))['download']['nas_mount_path'])" 2>/dev/null || echo "/mnt/nas/dashcam")
OPENHAB_URL=$(python3 -c "import json; print(json.load(open('${CONFIG_FILE}'))['openhab']['url'])" 2>/dev/null || echo "http://localhost:8080")
BACKUP_DIR="${NAS_MOUNT}/pi-backups"
BACKUP_FILE="${BACKUP_DIR}/${HOSTNAME}-sd-${DATE}.img.gz"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [$LOG_TAG] $*"; }

oh_update() {
    # Update OpenHAB item (best-effort, don't fail backup if OH is down)
    local item="$1" value="$2"
    curl -s -o /dev/null -X PUT \
        -H "Content-Type: text/plain" \
        -d "$value" \
        "${OPENHAB_URL}/rest/items/${item}/state" 2>/dev/null || true
}

cleanup() {
    # Always restart dump service, even if backup fails
    log "Restarting $DUMP_SERVICE..."
    systemctl start "$DUMP_SERVICE" 2>/dev/null || true
    oh_update "K7_Dump_Status" "idle"
    # Remove incomplete temp file if backup failed
    rm -f "${BACKUP_FILE}.tmp" 2>/dev/null || true
}

# --- Pre-checks ---
if [ ! -b "$DEVICE" ]; then
    log "ERROR: Block device $DEVICE not found"
    exit 1
fi

if ! mountpoint -q "$(dirname "$BACKUP_DIR")" 2>/dev/null && \
   ! [ -d "$BACKUP_DIR" ]; then
    log "ERROR: NAS not mounted at $(dirname "$BACKUP_DIR")"
    exit 1
fi

mkdir -p "$BACKUP_DIR"

# Check NAS free space (need at least 3GB for safety)
FREE_GB=$(df --output=avail "$BACKUP_DIR" | tail -1 | awk '{printf "%.0f", $1/1024/1024}')
if [ "$FREE_GB" -lt 3 ]; then
    log "ERROR: Only ${FREE_GB}GB free on NAS — need at least 3GB"
    exit 1
fi

# --- Check Pi SD free space and report to OpenHAB ---
PI_FREE_MB=$(df --output=avail / | tail -1 | awk '{printf "%.0f", $1/1024}')
PI_USED_PCT=$(df --output=pcent / | tail -1 | tr -d ' %')
log "Pi SD card: ${PI_FREE_MB}MB free, ${PI_USED_PCT}% used"
oh_update "K7_Pi_Disk_Free_MB" "$PI_FREE_MB"

# --- Skip if today's backup already exists ---
if [ -f "$BACKUP_FILE" ]; then
    log "Backup already exists: $BACKUP_FILE — skipping"
    exit 0
fi

# --- Stop dump service to free CPU/IO ---
log "Stopping $DUMP_SERVICE for backup..."
oh_update "K7_Dump_Status" "pi_backup"
systemctl stop "$DUMP_SERVICE" 2>/dev/null || true
sleep 5  # Let pending I/O drain

# Ensure cleanup runs on exit (restart service + remove temp)
trap cleanup EXIT

# --- Create compressed SD image ---
log "Starting full SD backup: $DEVICE -> $BACKUP_FILE"
log "SD card size: $(lsblk -b -d -n -o SIZE $DEVICE | awk '{printf "%.1f GB", $1/1024/1024/1024}')"
log "NAS free space: ${FREE_GB}GB"

START=$(date +%s)

# Use dd with gzip compression, piped directly to NAS.
# Nothing is stored on Pi. ionice/nice to reduce system impact.
ionice -c3 nice -n 19 dd if="$DEVICE" bs=4M status=none | gzip -1 > "${BACKUP_FILE}.tmp"

# Rename on completion (atomic — prevents half-written backups)
mv "${BACKUP_FILE}.tmp" "$BACKUP_FILE"

END=$(date +%s)
ELAPSED=$((END - START))
SIZE_MB=$(du -m "$BACKUP_FILE" | cut -f1)

log "Backup complete: ${SIZE_MB}MB in ${ELAPSED}s"

# --- Rotate: keep only N newest backups ---
BACKUP_COUNT=$(ls -1 "${BACKUP_DIR}/${HOSTNAME}-sd-"*.img.gz 2>/dev/null | wc -l)

if [ "$BACKUP_COUNT" -gt "$KEEP_VERSIONS" ]; then
    DELETE_COUNT=$((BACKUP_COUNT - KEEP_VERSIONS))
    log "Rotating: keeping $KEEP_VERSIONS, deleting $DELETE_COUNT old backup(s)"
    ls -1t "${BACKUP_DIR}/${HOSTNAME}-sd-"*.img.gz | tail -n "$DELETE_COUNT" | while read -r old; do
        log "  Deleting: $(basename "$old")"
        rm -f "$old"
    done
fi

# List current backups
log "Current backups:"
ls -lh "${BACKUP_DIR}/${HOSTNAME}-sd-"*.img.gz 2>/dev/null | while read -r line; do
    log "  $line"
done

# Report final Pi disk space
PI_FREE_MB=$(df --output=avail / | tail -1 | awk '{printf "%.0f", $1/1024}')
oh_update "K7_Pi_Disk_Free_MB" "$PI_FREE_MB"

log "Done. Dump service will be restarted by cleanup trap."
