"""
INNOVV K7 API Client.

Communicates with the K7 dashcam via HTTP only:
  - Novatek CarDV API for heartbeat and camera status
  - HTTP directory listing for file discovery
  - HTTP GET for file downloads

The K7 runs on 192.168.1.254 with httpd on port 80:
  - API: /?custom=1&cmd=<CMD> (Novatek XML responses)
  - Files: /INNOVVK7/<TYPE>/<TIMESTAMP>_<SEQ>_<CAM>.<EXT>
    - Movie_E: Continuous recordings (MP4, F=front R=rear)
    - Photo_E: Photos (JPG, F=front R=rear)
    - EMR_E:   Emergency/protected clips (MP4, FK=front RK=rear)
  - Delete: /INNOVVK7/<TYPE>/<filename>?del=1
  - FTP is NOT available on this device.
"""

import hashlib
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

log = logging.getLogger("innovv-k7.api")

# Novatek CarDV HTTP API command numbers
CMD_HEARTBEAT = 3012
CMD_FILE_LISTING = 3015
CMD_FIRMWARE_VERSION = 3016
CMD_DISK_FREE = 4003

# MP4 file starts with an "ftyp" box (byte offset 4-7)
_MP4_FTYP_MAGIC = b'ftyp'
# JPEG starts with FF D8 FF
_JPEG_MAGIC = b'\xff\xd8\xff'


@dataclass
class DownloadResult:
    """Result of a file download with integrity metadata."""
    success: bool = False
    bytes_downloaded: int = 0
    sha256: str = ""
    local_path: str = ""
    error: str = ""


# Regex to parse K7 HTML directory listing rows
# Matches: <a href="/INNOVVK7/Movie_E/20260309221222_000290_R.MP4">
_FILE_HREF_RE = re.compile(
    r'href="([^"]+\.(?:MP4|MOV|AVI|JPG|THM))"', re.IGNORECASE
)
# Matches folder links: <a href="/INNOVVK7"><b>INNOVVK7</b></a>...<i>folder</i>
_FOLDER_HREF_RE = re.compile(
    r'href="([^"]+)"[^<]*<b>[^<]+</b>.*?<i>folder</i>', re.IGNORECASE | re.DOTALL
)


class K7ApiClient:
    """Client for the INNOVV K7 Novatek CarDV HTTP API."""

    def __init__(
        self,
        host: str = "192.168.1.254",
        http_port: int = 80,
        heartbeat_interval: int = 10,
    ):
        self.host = host
        self.http_port = http_port
        self.heartbeat_interval = heartbeat_interval
        self._last_heartbeat = 0

    @property
    def _api_base(self) -> str:
        return f"http://{self.host}:{self.http_port}"

    def _api_request(self, cmd: int, par: str = None, timeout: int = 10) -> Optional[str]:
        """
        Send a command to the Novatek CarDV HTTP API.

        URL format: http://192.168.1.254/?custom=1&cmd=<CMD>[&par=<PAR>]
        Returns: raw response text, or None on failure.
        """
        url = f"{self._api_base}/?custom=1&cmd={cmd}"
        if par:
            url += f"&par={par}"

        try:
            log.debug(f"API request: {url}")
            req = Request(url)
            with urlopen(req, timeout=timeout) as resp:
                data = resp.read().decode("utf-8", errors="replace")
                log.debug(f"API response ({len(data)} bytes): {data[:200]}")
                return data
        except URLError as e:
            log.warning(f"API request failed (cmd={cmd}): {e}")
            return None
        except Exception as e:
            log.warning(f"API request error (cmd={cmd}): {e}")
            return None

    def _parse_xml_status(self, xml_text: str) -> int:
        """Extract <Status> value from Novatek XML response. 0 = success."""
        try:
            root = ET.fromstring(xml_text)
            status_elem = root.find(".//Status")
            if status_elem is not None:
                return int(status_elem.text)
        except ET.ParseError:
            log.debug(f"XML parse error: {xml_text[:100]}")
        return -1

    def heartbeat(self) -> bool:
        """
        Send heartbeat to keep the K7 WiFi connection alive.
        The K7 may disconnect idle clients after a timeout.
        Returns True if heartbeat succeeded.
        """
        now = time.time()
        if now - self._last_heartbeat < self.heartbeat_interval:
            return True  # Too soon, skip

        resp = self._api_request(CMD_HEARTBEAT)
        if resp is not None:
            self._last_heartbeat = now
            status = self._parse_xml_status(resp)
            if status == 0:
                log.debug("Heartbeat OK")
                return True
            else:
                log.warning(f"Heartbeat returned status: {status}")
                return True  # Got a response, probably fine

        log.warning("Heartbeat failed — no response")
        return False

    def wait_ready(self, max_wait: int = 30) -> bool:
        """
        Wait until the K7 httpd is responsive after a disruptive operation
        (e.g. file deletion).  Polls heartbeat every second, bypassing the
        normal heartbeat interval throttle.

        Returns True once the K7 responds, False if timed out.
        """
        for attempt in range(max_wait):
            resp = self._api_request(CMD_HEARTBEAT, timeout=3)
            if resp is not None:
                self._last_heartbeat = time.time()
                if attempt > 0:
                    log.info(f"K7 httpd ready after {attempt + 1}s wait")
                return True
            time.sleep(1)
        log.warning(f"K7 httpd not ready after {max_wait}s")
        return False

    def get_firmware_version(self) -> Optional[str]:
        """Get the K7 firmware version string."""
        resp = self._api_request(CMD_FIRMWARE_VERSION)
        if resp:
            try:
                root = ET.fromstring(resp)
                ver = root.find(".//String")
                if ver is not None:
                    return ver.text
            except ET.ParseError:
                pass
        return None

    def get_file_listing(self) -> Optional[list[dict]]:
        """
        Get file listing via HTTP API (cmd=3015).

        Returns list of dicts: [{"path": "/INNOVVK7/Movie_E/file.MP4", "size": 123456}, ...]
        Returns None if API call fails (caller should fall back to HTML listing).
        """
        resp = self._api_request(CMD_FILE_LISTING, timeout=30)
        if resp is None:
            return None

        files = []
        try:
            root = ET.fromstring(resp)

            for file_elem in root.iter("File"):
                path = None
                size = 0

                fpath = file_elem.find("FPATH")
                if fpath is not None and fpath.text:
                    path = fpath.text.strip()
                else:
                    name = file_elem.find("NAME")
                    if name is not None and name.text:
                        path = name.text.strip()

                size_elem = file_elem.find("SIZE")
                if size_elem is not None and size_elem.text:
                    try:
                        size = int(size_elem.text.strip())
                    except ValueError:
                        pass

                if path:
                    path = path.replace("A:\\", "/").replace("\\", "/")
                    if not path.startswith("/"):
                        path = "/" + path
                    files.append({"path": path, "size": size})

            if files:
                log.info(f"HTTP API returned {len(files)} files")
                return files

            log.warning("HTTP API returned XML but no <File> elements found")
            log.debug(f"XML content: {resp[:500]}")
            return None

        except ET.ParseError as e:
            log.warning(f"Failed to parse file listing XML: {e}")
            log.debug(f"Raw response: {resp[:300]}")
            return None

    def http_list_files(self, remote_path: str = "/INNOVVK7/Movie_E") -> list[dict]:
        """
        List files by parsing the K7's HTML directory listing.

        The K7 web server returns HTML with <a href="..."> links for each file.
        This is the primary method since FTP is not available on the K7.

        Returns list of dicts: [{"path": "/INNOVVK7/Movie_E/file.MP4", "size": 0}, ...]
        Size is 0 (K7 HTML listing doesn't reliably include file sizes).
        """
        files = []
        try:
            self._http_list_recursive(remote_path, files)
            log.info(f"HTTP listing: {len(files)} files found under {remote_path}")
            return files
        except Exception as e:
            log.error(f"HTTP listing failed: {e}")
            return []

    def _http_list_recursive(self, path: str, files: list):
        """Recursively list files from the K7 HTTP directory listing."""
        url = f"{self._api_base}{path}/"
        try:
            log.debug(f"Listing directory: {url}")
            req = Request(url)
            with urlopen(req, timeout=30) as resp:
                html_content = resp.read().decode("utf-8", errors="replace")

            # Find folders and recurse
            for match in _FOLDER_HREF_RE.finditer(html_content):
                folder_href = match.group(1)
                # Skip parent directory links and delete links
                if "del=1" in folder_href or folder_href == path:
                    continue
                # Only recurse into subfolders under our path
                if folder_href.startswith(path) and folder_href != path:
                    self._http_list_recursive(folder_href, files)

            # Find video/image files
            for match in _FILE_HREF_RE.finditer(html_content):
                file_href = match.group(1)
                # Skip delete links
                if "del=1" in file_href:
                    continue
                files.append({
                    "path": file_href,
                    "size": 0,  # Size not reliably available from HTML listing
                })

        except URLError as e:
            log.warning(f"HTTP listing failed for {path}: {e}")
        except Exception as e:
            log.warning(f"HTTP listing error for {path}: {e}")

    def download_file(self, remote_path: str, local_path: str,
                      progress_callback=None,
                      cancel_check=None) -> DownloadResult:
        """
        Download a file from K7 via HTTP GET.

        cancel_check: optional callable returning True to abort download.

        URL: http://192.168.1.254/INNOVVK7/Movie_E/file.MP4
        Uses a .partial temp file and renames on completion to prevent
        corrupt/incomplete files from appearing on the NAS.

        Computes SHA-256 during download and calls fsync() before rename
        to guarantee the data is flushed to the NAS disk.

        Returns a DownloadResult with success flag, byte count, and SHA-256.
        The SHA-256 is computed over the FULL file content (even for resumed
        downloads — if resume is used, the existing partial is re-hashed).
        """
        url = f"{self._api_base}{remote_path}"
        partial_path = local_path + ".partial"
        result = DownloadResult(local_path=local_path)

        try:
            os.makedirs(os.path.dirname(local_path), exist_ok=True)

            # If final file already exists, return it and clean up any stale .partial
            if os.path.exists(local_path):
                if os.path.exists(partial_path):
                    try:
                        os.remove(partial_path)
                        log.debug(f"Removed stale .partial alongside existing file: {partial_path}")
                    except OSError:
                        pass
                log.info(f"File already exists: {local_path}")
                result.bytes_downloaded = os.path.getsize(local_path)
                result.sha256 = self._hash_file(local_path)
                result.success = True
                return result

            # Check for partial download (resume support)
            rest_pos = 0
            if os.path.exists(partial_path):
                rest_pos = os.path.getsize(partial_path)

            req = Request(url)
            if rest_pos > 0:
                req.add_header("Range", f"bytes={rest_pos}-")
                log.info(f"Resuming download from {rest_pos} bytes")

            with urlopen(req, timeout=30) as resp:
                # Check if server supports range requests
                if rest_pos > 0 and resp.status != 206:
                    # Server doesn't support resume — start over
                    log.debug("Server doesn't support resume, starting from beginning")
                    rest_pos = 0

                # Get content length for progress tracking
                content_length = resp.headers.get("Content-Length")
                total_size = int(content_length) + rest_pos if content_length else None

                mode = "ab" if rest_pos > 0 and resp.status == 206 else "wb"
                downloaded = rest_pos if mode == "ab" else 0

                cancelled = False
                with open(partial_path, mode) as f:
                    while True:
                        if cancel_check and cancel_check():
                            log.info("Download cancelled by shutdown request")
                            cancelled = True
                            break
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total_size:
                            progress_callback(downloaded, total_size)

                    # Flush Python buffers -> OS -> NAS disk
                    f.flush()
                    os.fsync(f.fileno())

                if cancelled:
                    result.error = "cancelled"
                    return result

            # Compute SHA-256 of the COMPLETE .partial file before rename
            dl_hash = self._hash_file(partial_path)

            result.bytes_downloaded = downloaded
            result.sha256 = dl_hash

            # Check completeness BEFORE renaming to final path.
            # An incomplete file must stay as .partial so the next cycle
            # doesn't mistake it for a valid download.
            if total_size:
                log.info(f"Downloaded {downloaded}/{total_size} bytes  sha256={dl_hash[:16]}...")
                if downloaded < total_size:
                    log.warning(f"Incomplete download: {downloaded}/{total_size}")
                    result.success = False
                    return result
            else:
                log.info(f"Downloaded {downloaded} bytes  sha256={dl_hash[:16]}...")
                if downloaded == 0:
                    result.success = False
                    return result

            # Only rename .partial -> final when download is complete
            os.rename(partial_path, local_path)
            result.success = True

            return result

        except URLError as e:
            log.error(f"HTTP download failed for {remote_path}: {e}")
            result.error = str(e)
            return result
        except IOError as e:
            log.error(f"File write error for {local_path}: {e}")
            result.error = str(e)
            return result
        except Exception as e:
            log.error(f"Download error for {remote_path}: {e}")
            result.error = str(e)
            return result

    @staticmethod
    def _hash_file(path: str, algorithm: str = "sha256",
                   chunk_size: int = 131072) -> str:
        """Compute hash of a file by reading it in chunks."""
        h = hashlib.new(algorithm)
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def verify_local_file(local_path: str, expected_size: int,
                          expected_sha256: str) -> tuple[bool, str]:
        """
        Read-back verify a file on the NAS after download.

        Checks:
          1. File exists
          2. File size matches bytes_downloaded
          3. SHA-256 read back from disk matches the download hash
          4. File starts with valid MP4 (ftyp) or JPEG (FF D8 FF) header

        Returns (ok, reason) — ok=True means the file is 100% safe to trust.
        """
        if not os.path.exists(local_path):
            return False, "file does not exist on NAS"

        actual_size = os.path.getsize(local_path)
        if expected_size > 0 and actual_size != expected_size:
            return False, (f"size mismatch: expected {expected_size}, "
                           f"got {actual_size}")

        # Re-read and re-hash from NAS
        h = hashlib.sha256()
        header_bytes = b""
        with open(local_path, "rb") as f:
            first_chunk = True
            while True:
                chunk = f.read(131072)
                if not chunk:
                    break
                h.update(chunk)
                if first_chunk:
                    header_bytes = chunk[:16]
                    first_chunk = False

        actual_hash = h.hexdigest()
        if actual_hash != expected_sha256:
            return False, (f"SHA-256 mismatch: expected {expected_sha256[:16]}..., "
                           f"got {actual_hash[:16]}...")

        # Validate media header
        ext = os.path.splitext(local_path)[1].upper()
        if ext in (".MP4", ".MOV", ".AVI"):
            if len(header_bytes) >= 8 and header_bytes[4:8] != _MP4_FTYP_MAGIC:
                return False, (f"invalid MP4 header: {header_bytes[:8].hex()} "
                               f"(expected xx xx xx xx 66 74 79 70)")
        elif ext in (".JPG", ".JPEG"):
            if len(header_bytes) >= 3 and header_bytes[:3] != _JPEG_MAGIC:
                return False, f"invalid JPEG header: {header_bytes[:3].hex()}"

        return True, "OK"

    def delete_file(self, remote_path: str) -> bool:
        """
        Delete a file on the K7 SD card via HTTP.

        The K7 supports deletion via: GET /path/to/file?del=1
        Returns True on success.
        """
        url = f"{self._api_base}{remote_path}?del=1"
        try:
            req = Request(url)
            with urlopen(req, timeout=10) as resp:
                log.info(f"Deleted remote file: {remote_path}")
                return True
        except Exception as e:
            log.error(f"Failed to delete {remote_path}: {e}")
            return False

    def get_disk_info(self) -> Optional[dict]:
        """Get SD card space info via API."""
        resp = self._api_request(CMD_DISK_FREE)
        if resp:
            try:
                root = ET.fromstring(resp)
                info = {}
                for field in ("Free", "Total", "Used"):
                    elem = root.find(f".//{field}")
                    if elem is not None and elem.text:
                        try:
                            info[field.lower()] = int(elem.text)
                        except ValueError:
                            pass
                return info if info else None
            except ET.ParseError:
                pass
        return None
