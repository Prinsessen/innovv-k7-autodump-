#!/bin/bash
# ============================================================
# INNOVV K7 Dump Service — Pi 4 Setup Script
# ============================================================
# Run on the Raspberry Pi 4 as root.
#
# This script:
#   1. Installs system dependencies
#   2. Creates the installation directory
#   3. Creates a Python virtual environment
#   4. Copies service files into place
#   5. Sets up NAS mount point
#   6. Configures wlan0 for manual management
#   7. Installs and enables the systemd service
# ============================================================

set -euo pipefail

INSTALL_DIR="/opt/innovv-k7"
NAS_MOUNT="/mnt/nas/dashcam"
SERVICE_NAME="innovv-k7-dump"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# --- Pre-flight checks ---
if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root"
    exit 1
fi

info "INNOVV K7 Dump Service — Installation"
echo "======================================"
echo ""

# --- Step 1: System dependencies ---
info "Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-venv python3-pip \
    wpasupplicant iw wireless-tools \
    nfs-common cifs-utils \
    > /dev/null 2>&1

info "System dependencies installed"

# --- Step 2: Create installation directory ---
info "Creating install directory: ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"

# Copy Python files and scripts
for f in innovv_k7_dump.py wifi_manager.py k7_api.py openhab_client.py config.json backup-sd.sh; do
    if [[ -f "${SCRIPT_DIR}/${f}" ]]; then
        cp "${SCRIPT_DIR}/${f}" "${INSTALL_DIR}/${f}"
        info "  Copied ${f}"
    else
        warn "  Missing: ${f} — skipping"
    fi
done
chmod +x "${INSTALL_DIR}/backup-sd.sh" 2>/dev/null || true

# --- Step 3: Python virtual environment ---
info "Creating Python virtual environment..."
if [[ ! -d "${INSTALL_DIR}/.venv" ]]; then
    python3 -m venv "${INSTALL_DIR}/.venv"
fi
# No pip packages needed — all stdlib
info "Virtual environment ready"

# --- Step 4: NAS mount point ---
info "Setting up NAS mount point: ${NAS_MOUNT}"
mkdir -p "${NAS_MOUNT}"

# Check if NAS is already in fstab
if ! grep -q "${NAS_MOUNT}" /etc/fstab 2>/dev/null; then
    warn "NAS mount not found in /etc/fstab"
    echo ""
    echo "  You need to add a mount entry for your NAS. Examples:"
    echo ""
    echo "  # NFS mount:"
    echo "  nas-server:/volume/dashcam  ${NAS_MOUNT}  nfs  defaults,noatime  0  0"
    echo ""
    echo "  # SMB/CIFS mount:"
    echo "  //nas-server/dashcam  ${NAS_MOUNT}  cifs  credentials=/root/.nas-creds,uid=0  0  0"
    echo ""
fi

# --- Step 5: Configure wlan0 for manual management ---
info "Configuring wlan0 for manual management..."

# Prevent NetworkManager/dhcpcd from managing wlan0 automatically
if [[ -d /etc/NetworkManager ]]; then
    NM_CONF="/etc/NetworkManager/conf.d/99-innovv-k7.conf"
    if [[ ! -f "${NM_CONF}" ]]; then
        cat > "${NM_CONF}" <<EOF
# Do not auto-manage wlan0 — used by INNOVV K7 dump service
[keyfile]
unmanaged-devices=interface-name:wlan0
EOF
        info "  NetworkManager: wlan0 set to unmanaged"
        systemctl restart NetworkManager 2>/dev/null || true
    fi
fi

if [[ -f /etc/dhcpcd.conf ]]; then
    if ! grep -q "denyinterfaces wlan0" /etc/dhcpcd.conf; then
        echo "" >> /etc/dhcpcd.conf
        echo "# Do not auto-manage wlan0 — used by INNOVV K7 dump service" >> /etc/dhcpcd.conf
        echo "denyinterfaces wlan0" >> /etc/dhcpcd.conf
        info "  dhcpcd: wlan0 denied"
    fi
fi

# Ensure wlan0 is not blocked (and persist across reboots)
rfkill unblock wifi 2>/dev/null || true
if [[ -d /etc/systemd/system ]]; then
    # systemd-rfkill should handle persistence; verify it's enabled
    systemctl enable systemd-rfkill.service 2>/dev/null || true
fi

# --- Step 5b: BCM43455 WiFi firmware (Pi 4 only) ---
# The Pi 4's BCM43455 MUST use the minimal firmware (7.45.241) for
# 5 GHz association with the K7's RTL8821CS AP. The standard firmware
# (7.45.265) has a bug causing ASSOC_REJECT on 5 GHz.
MINIMAL_FW="/lib/firmware/cypress/cyfmac43455-sdio-minimal.bin"
if [[ -f "${MINIMAL_FW}" ]]; then
    CURRENT_FW=$(readlink -f /lib/firmware/cypress/cyfmac43455-sdio.bin 2>/dev/null || echo "")
    if [[ "${CURRENT_FW}" != "${MINIMAL_FW}" ]]; then
        info "Selecting BCM43455 minimal firmware for 5 GHz K7 compatibility..."
        if command -v update-alternatives &>/dev/null; then
            update-alternatives --set cyfmac43455-sdio.bin "${MINIMAL_FW}" 2>/dev/null && \
                info "  Minimal firmware selected (reboot required)" || \
                warn "  Could not set firmware alternative — set manually"
        else
            warn "  update-alternatives not available — manually symlink:"
            echo "    ln -sf ${MINIMAL_FW} /lib/firmware/cypress/cyfmac43455-sdio.bin"
        fi
    else
        info "  BCM43455 minimal firmware already active"
    fi
else
    warn "BCM43455 minimal firmware not found at ${MINIMAL_FW}"
    echo "  If using Pi 4 with 5 GHz K7 WiFi, install the minimal firmware:"
    echo "    apt-get install firmware-brcm80211"
    echo "    update-alternatives --set cyfmac43455-sdio.bin ${MINIMAL_FW}"
fi

# --- Step 5c: NAS credentials template ---
if [[ ! -f /root/.nas-creds ]]; then
    info "Creating NAS credentials template: /root/.nas-creds"
    cat > /root/.nas-creds <<EOF
# NAS credentials for CIFS mount (chmod 600 this file)
username=your_nas_user
password=your_nas_password
EOF
    chmod 600 /root/.nas-creds
    warn "  Edit /root/.nas-creds with your NAS credentials before mounting"
else
    info "  NAS credentials file already exists: /root/.nas-creds"
fi

# --- Step 6: Create database directory ---
info "Creating SQLite database directory..."
mkdir -p "${INSTALL_DIR}"
touch "${INSTALL_DIR}/downloaded_files.db"
chmod 644 "${INSTALL_DIR}/downloaded_files.db"

# --- Step 7: Create log directory ---
info "Creating log directory..."
mkdir -p /var/log
# Log file will be created by the service

# --- Step 8: Install systemd service ---
info "Installing systemd service..."
cp "${SCRIPT_DIR}/innovv-k7-dump.service" "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"
info "Service installed and enabled"

# --- Step 9: Configuration reminder ---
echo ""
echo "======================================"
info "Installation complete!"
echo ""
echo "Before starting the service, update the configuration:"
echo ""
echo "  1. Edit ${INSTALL_DIR}/config.json:"
echo "     - k7_wifi.ssid         : K7 WiFi SSID (default: INNOVV_K7)"
echo "     - k7_wifi.password      : K7 WiFi password (default: 12345678)"
echo "     - k7_wifi.country       : Your 2-letter country code (default: DK)"
echo "     - k7_wifi.static_ip     : Pi's IP on K7 subnet (default: 192.168.1.100/24)"
echo "     - k7_wifi.camera_ip     : K7 AP IP (default: 192.168.1.254)"
echo "     - download.nas_mount_path : Your NAS mount point"
echo "     - openhab.url           : Your OpenHAB URL (e.g. http://192.168.1.10:8080)"
echo ""
echo "  2. Edit /root/.nas-creds with your NAS username/password"
echo "     Configure NAS mount in /etc/fstab (see examples above)"
echo "     Then run: mount ${NAS_MOUNT}"
echo ""
echo "  3. Verify wlan0 exists:"
echo "     ip link show wlan0"
echo ""
echo "  4. If using 5 GHz K7 WiFi on Pi 4, verify minimal firmware is active:"
echo "     readlink -f /lib/firmware/cypress/cyfmac43455-sdio.bin"
echo "     (should end in -minimal.bin — reboot after switching)"
echo ""
echo "  5. Start the service:"
echo "     systemctl start ${SERVICE_NAME}"
echo ""
echo "  6. Check status:"
echo "     systemctl status ${SERVICE_NAME}"
echo "     journalctl -u ${SERVICE_NAME} -f"
echo ""
echo "  Optional: Enable monthly SD card backup:"
echo "     crontab -e"
echo "     0 3 1 * *  ${INSTALL_DIR}/backup-sd.sh >> /var/log/innovv-k7-backup.log 2>&1"
echo ""
