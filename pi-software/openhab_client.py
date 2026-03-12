"""
OpenHAB REST API client for the INNOVV K7 dump service.

Reports dump status, progress, and results to OpenHAB items
via the REST API over the Pi's Ethernet connection.

OpenHAB items should be created separately in the OpenHAB config
(see README for required items).
"""

import logging
from urllib.request import urlopen, Request
from urllib.error import URLError
from datetime import datetime
from typing import Optional

log = logging.getLogger("innovv-k7.openhab")


class OpenHABClient:
    """Simple OpenHAB REST API client for state updates."""

    def __init__(
        self,
        base_url: str = "http://10.0.5.21:8080",
        item_prefix: str = "K7_",
    ):
        self.base_url = base_url.rstrip("/")
        self.item_prefix = item_prefix

    def _update_item(self, item_name: str, value: str) -> bool:
        """Update an OpenHAB item state via REST API (PUT)."""
        url = f"{self.base_url}/rest/items/{item_name}/state"
        try:
            data = str(value).encode("utf-8")
            req = Request(url, data=data, method="PUT")
            req.add_header("Content-Type", "text/plain")
            with urlopen(req, timeout=10) as resp:
                log.debug(f"Updated {item_name} = {value} (HTTP {resp.status})")
                return True
        except URLError as e:
            log.warning(f"Failed to update {item_name}: {e}")
            return False
        except Exception as e:
            log.warning(f"Error updating {item_name}: {e}")
            return False

    def _item(self, suffix: str) -> str:
        """Build full item name from prefix + suffix."""
        return f"{self.item_prefix}{suffix}"

    # --- Status updates ---

    def update_status(self, status: str):
        """
        Update the dump service status.
        Values: IDLE, SCANNING, CONNECTING, DUMPING, COMPLETE, ERROR
        """
        self._update_item(self._item("Dump_Status"), status)

    def update_last_dump(self, timestamp: Optional[datetime] = None):
        """Update the last successful dump timestamp."""
        if timestamp is None:
            timestamp = datetime.now()
        # OpenHAB DateTime format
        formatted = timestamp.strftime("%Y-%m-%dT%H:%M:%S")
        self._update_item(self._item("Last_Dump"), formatted)

    def update_files_downloaded(self, count: int):
        """Update the number of files downloaded in current/last dump."""
        self._update_item(self._item("Files_Downloaded"), str(count))

    def update_bytes_downloaded(self, total_bytes: int):
        """Update total bytes downloaded in current/last dump."""
        mb = total_bytes / (1024 * 1024)
        self._update_item(self._item("MB_Downloaded"), f"{mb:.1f}")

    def update_files_on_camera(self, count: int):
        """Update total files found on camera."""
        self._update_item(self._item("Files_On_Camera"), str(count))

    def update_wifi_signal(self, dbm: int | None):
        """Update K7 WiFi signal strength.

        Sends a human-readable string like 'Excellent (-31 dBm)' or 'Disconnected'.
        """
        if dbm is None:
            text = "Disconnected"
        elif dbm >= -50:
            text = f"Excellent ({dbm} dBm)"
        elif dbm >= -60:
            text = f"Good ({dbm} dBm)"
        elif dbm >= -70:
            text = f"Fair ({dbm} dBm)"
        else:
            text = f"Weak ({dbm} dBm)"
        self._update_item(self._item("WiFi_Signal"), text)

    def update_camera_online(self, online: bool):
        """Update K7 camera reachable state."""
        self._update_item(self._item("Camera_Online"), "ON" if online else "OFF")

    def update_wifi_band(self, freq_mhz: str | None):
        """Update K7 WiFi band/channel display.

        Converts frequency in MHz to human-readable string like '5 GHz ch 36 (5180 MHz)'.
        """
        if not freq_mhz:
            self._update_item(self._item("WiFi_Band"), "Unknown")
            return
        try:
            freq = int(float(freq_mhz))
        except (ValueError, TypeError):
            self._update_item(self._item("WiFi_Band"), f"{freq_mhz} MHz")
            return

        # Determine band and channel number
        if 2400 <= freq <= 2500:
            band = "2.4 GHz"
            ch = (freq - 2407) // 5 if freq <= 2472 else 14
        elif 5000 <= freq <= 5900:
            band = "5 GHz"
            ch = (freq - 5000) // 5
        else:
            band = "?"
            ch = 0
        text = f"{band} ch {ch} ({freq} MHz)"
        self._update_item(self._item("WiFi_Band"), text)

    def update_files_verified(self, count: int):
        """Update total verified files on NAS (lifetime)."""
        self._update_item(self._item("Files_Verified"), str(count))

    def update_files_deleted(self, count: int):
        """Update total files deleted from K7 (lifetime)."""
        self._update_item(self._item("Files_Deleted"), str(count))

    def update_pending_deletes(self, count: int):
        """Update count of files verified on NAS but not yet deleted from K7."""
        self._update_item(self._item("Pending_Deletes"), str(count))

    def update_pi_disk_free(self, free_mb: int):
        """Update Pi SD card free space in MB."""
        self._update_item(self._item("Pi_Disk_Free_MB"), str(free_mb))

    def update_error(self, message: str):
        """Update last error message."""
        self._update_item(self._item("Last_Error"), message[:200])

    def is_movie_e_enabled(self) -> bool:
        """Check if Movie_E (loop video) dumping is enabled in OpenHAB.

        Returns True if the switch is ON or unset (default: dump everything).
        """
        state = self.get_item_state(self._item("Dump_Movie_E"))
        if state == "OFF":
            return False
        return True  # ON, NULL, UNDEF -> dump by default

    # --- Read item state ---

    def get_item_state(self, item_name: str) -> Optional[str]:
        """Read an OpenHAB item's current state."""
        url = f"{self.base_url}/rest/items/{item_name}/state"
        try:
            req = Request(url)
            with urlopen(req, timeout=10) as resp:
                state = resp.read().decode("utf-8").strip()
                if state == "NULL" or state == "UNDEF":
                    return None
                return state
        except Exception as e:
            log.debug(f"Could not read {item_name}: {e}")
            return None
