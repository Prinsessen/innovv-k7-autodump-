"""
WiFi Manager for INNOVV K7 connection.

Manages the USB WiFi dongle (wlan1) on the Pi 4 to scan for and connect to the K7's WiFi hotspot.
Uses wpa_supplicant for connection management.

The Pi's Ethernet (eth0) remains connected to the home LAN at all times.
The USB WiFi interface is dedicated exclusively to the K7 connection.
"""

import ipaddress
import logging
import os
import re
import subprocess
import time

log = logging.getLogger("innovv-k7.wifi")

# Full paths required — systemd ProtectSystem=strict doesn't include /usr/sbin in PATH
_IP = "/usr/bin/ip"
_IW = "/usr/sbin/iw"
_IWLIST = "/usr/sbin/iwlist"
_WPA_SUPPLICANT = "/usr/sbin/wpa_supplicant"
_WPA_CLI = "/usr/sbin/wpa_cli"
_KILLALL = "/usr/bin/killall"
_IWCONFIG = "/usr/sbin/iwconfig"


class WiFiManager:
    """Manage WiFi connection to K7 hotspot via wpa_supplicant."""

    def __init__(
        self,
        interface: str,
        ssid: str,
        password: str,
        connect_timeout: int = 30,
        country: str = "DK",
        static_ip: str = "192.168.1.100/24",
        wpa_conf_dir: str = "/opt/innovv-k7",
    ):
        self.interface = interface
        self.ssid = ssid
        self.password = password
        self.connect_timeout = connect_timeout
        self.country = country
        self.static_ip = static_ip
        self._subnet = str(ipaddress.ip_interface(static_ip).network)
        self.wpa_conf_dir = wpa_conf_dir
        # Auto-detected from scan results (updated by is_ssid_visible)
        self._detected_freq: str | None = None   # e.g. "5180" or "2437"
        self._detected_bssid: str | None = None   # e.g. "64:82:14:4c:bb:d8"

    def _run(self, cmd: list, timeout: int = 15) -> subprocess.CompletedProcess:
        """Run a shell command and return result."""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result
        except subprocess.TimeoutExpired:
            log.warning(f"Command timed out: {' '.join(cmd)}")
            return subprocess.CompletedProcess(cmd, 1, "", "timeout")

    def is_ssid_visible(self) -> bool:
        """Scan for the K7 SSID. Returns True if visible.

        Also extracts the K7's frequency and BSSID from the scan output
        so connect() can target the correct band/channel automatically.
        """
        # Bring interface up (may already be up)
        self._run([_IP, "link", "set", self.interface, "up"])
        time.sleep(0.5)

        scan_output = None

        # Fast scan: if we previously found the K7 on a specific freq, try that first
        if self._detected_freq:
            result = self._run(
                [_IW, self.interface, "scan", "freq", self._detected_freq],
                timeout=20,
            )
            if result.returncode == 0 and self.ssid in result.stdout:
                scan_output = result.stdout

        # If fast scan didn't work, try common 5 GHz channels (36-48)
        if scan_output is None:
            result = self._run(
                [_IW, self.interface, "scan", "freq", "5180", "5200", "5220", "5240"],
                timeout=20,
            )
            if result.returncode == 0 and self.ssid in result.stdout:
                scan_output = result.stdout

        # Still not found — full spectrum scan (2.4 + 5 GHz, slower)
        # Note: "ap-force" removed — hangs on mt76x2u (USB dongle) driver
        if scan_output is None:
            result = self._run(
                [_IW, self.interface, "scan"],
                timeout=25,
            )
            if result.returncode == 0 and self.ssid in result.stdout:
                scan_output = result.stdout

        # Last resort: iwlist
        if scan_output is None:
            result = self._run(
                [_IWLIST, self.interface, "scan"],
                timeout=25,
            )
            if result.returncode == 0 and self.ssid in result.stdout:
                scan_output = result.stdout

        if scan_output is None:
            log.debug(f"SSID '{self.ssid}' not found in scan")
            return False

        # Parse iw scan output to extract frequency and BSSID for our SSID
        self._parse_scan_results(scan_output)
        log.debug(f"SSID '{self.ssid}' found — freq={self._detected_freq}, bssid={self._detected_bssid}")
        return True

    def _parse_scan_results(self, output: str) -> None:
        """Parse iw scan output to extract freq and BSSID for our SSID.

        iw scan output format:
            BSS aa:bb:cc:dd:ee:ff(on wlan1)
                ...
                freq: 5180
                ...
                SSID: INNOVV_K7
        """
        current_bssid = None
        current_freq = None

        for line in output.split("\n"):
            # New BSS block
            bss_match = re.match(r"BSS ([0-9a-f:]{17})", line, re.IGNORECASE)
            if bss_match:
                current_bssid = bss_match.group(1).lower()
                current_freq = None
                continue

            stripped = line.strip()

            if stripped.startswith("freq:"):
                current_freq = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("SSID:"):
                ssid = stripped.split(":", 1)[1].strip()
                if ssid == self.ssid:
                    self._detected_bssid = current_bssid
                    self._detected_freq = current_freq
                    return

    def connect(self) -> bool:
        """
        Connect to the K7 WiFi hotspot via the configured interface.

        Uses wpa_supplicant with a temporary config, then assigns a static IP
        (192.168.1.100/24). Static IP avoids DHCP issues with systemd sandboxing.

        Returns True if connected and got an IP address.
        """
        log.info(f"Connecting {self.interface} to '{self.ssid}'...")

        # Kill any existing wpa_supplicant on this interface
        self._run([_KILLALL, "-9", "wpa_supplicant"])
        time.sleep(2)

        # Remove stale ctrl_iface socket (left by killed wpa_supplicant)
        sock_path = f"/var/run/wpa_supplicant/{self.interface}"
        if os.path.exists(sock_path):
            try:
                os.remove(sock_path)
                log.debug(f"Removed stale socket: {sock_path}")
            except OSError:
                pass

        # Build wpa_supplicant config
        # country code enables 5 GHz channels (set via config.json k7_wifi.country)
        # scan_freq auto-detected from scan results (band-agnostic)
        # bssid auto-detected from scan results
        # scan_ssid=1 for hidden/directed connection
        # ieee80211w=0 disables management frame protection (K7 doesn't support it)

        # Build optional scan_freq and bssid lines from auto-detected values
        extra_network_opts = ""
        if self._detected_freq:
            extra_network_opts += f"    scan_freq={self._detected_freq}\n"
            log.info(f"Using detected frequency: {self._detected_freq} MHz")
        if self._detected_bssid:
            extra_network_opts += f"    bssid={self._detected_bssid}\n"
            log.info(f"Using detected BSSID: {self._detected_bssid}")

        wpa_conf = f"""
ctrl_interface=/var/run/wpa_supplicant
ctrl_interface_group=0
update_config=0
country={self.country}
p2p_disabled=1

network={{
    ssid="{self.ssid}"
    psk="{self.password}"
    key_mgmt=WPA-PSK
    proto=RSN
    pairwise=CCMP
    group=CCMP
    scan_ssid=1
{extra_network_opts}    ieee80211w=0
}}
"""
        conf_path = f"{self.wpa_conf_dir}/wpa_k7_{self.interface}.conf"
        with open(conf_path, "w") as f:
            f.write(wpa_conf)

        # Ensure interface is up and has no old IP
        self._run([_IP, "addr", "flush", "dev", self.interface])
        self._run([_IP, "link", "set", self.interface, "up"])

        # Start wpa_supplicant (service runs as root, no sudo needed)
        result = self._run([
            _WPA_SUPPLICANT,
            "-B",                          # Background
            "-i", self.interface,
            "-c", conf_path,
            "-D", "nl80211",               # Driver
        ])

        if result.returncode != 0:
            log.error(f"wpa_supplicant failed to start: {result.stderr.strip()}")
            return False

        # Wait for association
        associated = False
        for i in range(self.connect_timeout):
            time.sleep(1)
            status = self._get_wpa_status()
            if status.get("wpa_state") == "COMPLETED":
                associated = True
                log.info(f"WiFi associated after {i + 1}s")
                break

        if not associated:
            log.error(f"WiFi association timed out after {self.connect_timeout}s")
            self.disconnect()
            return False

        # Assign a static IP — the K7 AP runs a simple network.
        # Using static avoids DHCP issues with systemd sandboxing
        # (ProtectSystem=strict blocks /var/lib/dhcp).
        log.info(f"Assigning static IP {self.static_ip}...")
        self._run([
            _IP, "addr", "add",
            self.static_ip, "dev", self.interface,
        ])
        time.sleep(1)

        # Remove any default route that might appear via WiFi
        # (eth0 must keep the default route for LAN/SSH access)
        self._run([
            _IP, "route", "del", "default",
            "dev", self.interface,
        ])

        # Verify we got an IP
        ip = self._get_interface_ip()
        if ip:
            log.info(f"Connected! IP: {ip}")

            # Add route for K7 subnet via WiFi (keep default route on eth0)
            self._run([
                _IP, "route", "add",
                self._subnet, "dev", self.interface,
            ])

            return True
        else:
            log.error("Failed to get IP address after static assignment")
            self.disconnect()
            return False

    def disconnect(self):
        """Disconnect from K7 WiFi."""
        log.info(f"Disconnecting {self.interface}...")

        # Stop wpa_supplicant
        self._run([_KILLALL, "-q", "wpa_supplicant"])

        # Remove K7 route
        self._run([
            _IP, "route", "del",
            self._subnet, "dev", self.interface,
        ])

        # Flush IP
        self._run([_IP, "addr", "flush", "dev", self.interface])

        log.info(f"{self.interface} disconnected")

    def is_connected(self) -> bool:
        """Check if currently connected to K7 WiFi."""
        status = self._get_wpa_status()
        return (
            status.get("wpa_state") == "COMPLETED"
            and status.get("ssid") == self.ssid
        )

    def _get_wpa_status(self) -> dict:
        """Get wpa_supplicant status as dict."""
        result = self._run([
            _WPA_CLI, "-i", self.interface, "status",
        ])
        status = {}
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if "=" in line:
                    key, _, val = line.partition("=")
                    status[key] = val
        return status

    def _get_interface_ip(self) -> str | None:
        """Get the current IP address of the WiFi interface."""
        result = self._run([
            _IP, "-4", "-o", "addr", "show", self.interface,
        ])
        if result.returncode == 0 and result.stdout.strip():
            # Parse: "3: wlan1    inet 192.168.1.20/24 ..."
            parts = result.stdout.strip().split()
            for i, part in enumerate(parts):
                if part == "inet" and i + 1 < len(parts):
                    return parts[i + 1].split("/")[0]
        return None

    def get_signal_dbm(self) -> int | None:
        """Read current WiFi signal strength in dBm from iwconfig.

        Returns e.g. -40 for excellent, -70 for weak, None if unavailable.
        """
        result = self._run([_IWCONFIG, self.interface])
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if "Signal level=" in line:
                    # "Signal level=-40 dBm"
                    try:
                        part = line.split("Signal level=")[1]
                        dbm = int(part.split()[0])
                        return dbm
                    except (IndexError, ValueError):
                        pass
        return None
