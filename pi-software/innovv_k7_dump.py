#!/usr/bin/env python3
"""
INNOVV K7 Dashcam Auto-Dump Service

Main orchestrator that runs on a Raspberry Pi 4 in the garage.
- Ethernet: always connected to home LAN (NAS, OpenHAB)
- WiFi (wlan0): dedicated to connecting to K7 hotspot

Flow:
  1. Wait for K7 WiFi SSID to appear (scan periodically)
  2. Connect wlan0 to K7 hotspot
  3. Send heartbeat, get file listing via HTTP API
  4. Download new files via HTTP directly to NAS mount
  5. Report status to OpenHAB REST API
  6. Disconnect WiFi when done
"""

import json
import logging
import logging.handlers
import os
import shutil
import signal
import sqlite3
import sys
import time
from datetime import datetime

from wifi_manager import WiFiManager
from k7_api import K7ApiClient, DownloadResult
from openhab_client import OpenHABClient


class InnovvK7Dump:
    """Main service class for K7 footage auto-dump."""

    def __init__(self, config_path: str):
        self.config = self._load_config(config_path)
        self._setup_logging()
        self.running = True

        self.wifi = WiFiManager(
            interface=self.config["k7_wifi"]["interface"],
            ssid=self.config["k7_wifi"]["ssid"],
            password=self.config["k7_wifi"]["password"],
            connect_timeout=self.config["k7_wifi"]["connect_timeout_sec"],
            country=self.config["k7_wifi"].get("country", "DK"),
            static_ip=self.config["k7_wifi"].get("static_ip", "192.168.1.100/24"),
            wpa_conf_dir=os.path.dirname(self.config["database"]["path"]),
        )
        self.k7 = K7ApiClient(
            host=self.config["k7_wifi"]["camera_ip"],
            http_port=self.config["k7_api"]["http_port"],
            heartbeat_interval=self.config["k7_api"]["heartbeat_interval_sec"],
        )
        self.openhab = OpenHABClient(
            base_url=self.config["openhab"]["url"],
            item_prefix="K7_",
        )
        self.db_path = self.config["database"]["path"]
        self._init_database()

        # Graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _load_config(self, path: str) -> dict:
        with open(path) as f:
            return json.load(f)

    def _setup_logging(self):
        cfg = self.config["logging"]
        logger = logging.getLogger()
        logger.setLevel(getattr(logging, cfg["level"]))

        # Rotating file handler
        os.makedirs(os.path.dirname(cfg["file"]), exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            cfg["file"],
            maxBytes=cfg["max_bytes"],
            backupCount=cfg["backup_count"],
        )
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        logger.addHandler(fh)

        # Console handler
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s"
        ))
        logger.addHandler(ch)

        self.log = logging.getLogger("innovv-k7")

    def _init_database(self):
        """Create SQLite database for tracking downloaded files."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS downloaded_files (
                    remote_path TEXT PRIMARY KEY,
                    file_size INTEGER,
                    downloaded_at TEXT,
                    local_path TEXT,
                    verified INTEGER DEFAULT 0,
                    sha256 TEXT DEFAULT '',
                    deleted_from_k7 INTEGER DEFAULT 0
                )
            """)
            # Migrate: add columns if missing (existing DBs from earlier versions)
            for col, coldef in [("sha256", "TEXT DEFAULT ''"),
                                ("deleted_from_k7", "INTEGER DEFAULT 0")]:
                try:
                    conn.execute(f"ALTER TABLE downloaded_files ADD COLUMN {col} {coldef}")
                except sqlite3.OperationalError:
                    pass  # column already exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dump_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT,
                    completed_at TEXT,
                    files_downloaded INTEGER DEFAULT 0,
                    bytes_downloaded INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'started'
                )
            """)
            conn.commit()

    def _signal_handler(self, signum, frame):
        self.log.info(f"Received signal {signum}, shutting down...")
        self.running = False

    def _is_already_downloaded(self, remote_path: str, file_size: int) -> bool:
        """Check if file was previously downloaded AND verified.

        If file_size is 0 (K7 HTML listing doesn't provide sizes), we consider
        any verified DB entry for this path as "already downloaded". If
        file_size > 0 (from Novatek XML API), we also verify the size matches.

        Unverified entries are NOT considered "downloaded" — they will be
        re-downloaded and re-verified.
        """
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT file_size, verified FROM downloaded_files WHERE remote_path = ?",
                (remote_path,),
            ).fetchone()
            if row is None:
                return False
            db_size, db_verified = row
            # Only trust verified entries
            if not db_verified:
                return False
            # Size 0 from listing = unknown size, trust the DB entry exists
            if file_size == 0 or db_size == file_size:
                return True
        return False

    def _get_pending_deletes(self) -> list[dict]:
        """Get files that are verified on NAS but not yet deleted from K7."""
        if not self.config["download"].get("delete_after_verified_download", False):
            return []
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT remote_path, local_path FROM downloaded_files
                   WHERE verified = 1 AND deleted_from_k7 = 0""",
            ).fetchall()
            return [{"remote_path": r[0], "local_path": r[1]} for r in rows]

    def _mark_deleted_from_k7(self, remote_path: str):
        """Mark a file as deleted from K7 SD card."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE downloaded_files SET deleted_from_k7 = 1 WHERE remote_path = ?",
                (remote_path,),
            )
            conn.commit()

    def _record_download(self, remote_path: str, file_size: int, local_path: str,
                         sha256: str = "", verified: bool = False):
        """Record a successfully downloaded file."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO downloaded_files
                   (remote_path, file_size, downloaded_at, local_path, verified, sha256)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (remote_path, file_size, datetime.now().isoformat(),
                 local_path, 1 if verified else 0, sha256),
            )
            conn.commit()

    def _is_nas_mounted(self) -> bool:
        """Check if NAS is mounted by reading /proc/mounts (no NFS/CIFS I/O).

        This avoids touching the CIFS mount at all — a stale mount can hang
        os.path.ismount() for 30+ seconds.  Reading /proc/mounts is instant.
        """
        nas_path = self.config["download"]["nas_mount_path"]
        try:
            with open("/proc/mounts") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] == nas_path:
                        return True
            return False
        except OSError:
            # Fallback: stat-based check (may hang on stale CIFS)
            return os.path.ismount(nas_path)

    def _check_nas_space(self) -> bool:
        """Verify NAS mount has enough free space."""
        nas_path = self.config["download"]["nas_mount_path"]
        if not self._is_nas_mounted():
            self.log.error(f"NAS not mounted at {nas_path} — refusing to download")
            return False

        stat = os.statvfs(nas_path)
        free_gb = (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
        min_free = self.config["safety"]["min_nas_free_space_gb"]

        # Report NAS free space to OpenHAB
        self.openhab.update_nas_free_gb(free_gb)

        if free_gb < min_free:
            self.log.error(f"NAS free space {free_gb:.1f}GB < minimum {min_free}GB")
            return False

        self.log.info(f"NAS free space: {free_gb:.1f}GB")
        return True

    def _report_db_stats(self):
        """Push lifetime verified/deleted/pending counts to OpenHAB."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    """SELECT
                        SUM(CASE WHEN verified = 1 THEN 1 ELSE 0 END),
                        SUM(CASE WHEN deleted_from_k7 = 1 THEN 1 ELSE 0 END),
                        SUM(CASE WHEN verified = 1 AND deleted_from_k7 = 0 THEN 1 ELSE 0 END)
                    FROM downloaded_files""",
                ).fetchone()
                verified = row[0] or 0
                deleted = row[1] or 0
                pending = row[2] or 0
            self.openhab.update_files_verified(verified)
            self.openhab.update_files_deleted(deleted)
            self.openhab.update_pending_deletes(pending)
        except Exception as e:
            self.log.warning(f"Failed to report DB stats: {e}")

    def _report_pi_disk_space(self):
        """Report Pi SD card free space to OpenHAB."""
        try:
            usage = shutil.disk_usage("/")
            free_mb = usage.free // (1024 * 1024)
            self.openhab.update_pi_disk_free(free_mb)
            self.log.info(f"Pi SD card free space: {free_mb}MB")
            if free_mb < 500:
                self.log.warning(f"Pi SD card low: only {free_mb}MB free!")
                self.openhab.update_error(f"Pi SD low: {free_mb}MB free")
        except Exception as e:
            self.log.warning(f"Could not check Pi disk space: {e}")

    def _clean_stale_partials(self):
        """Remove stale .partial files from NAS left by interrupted downloads.

        A .partial file means either:
          - A download was interrupted (WiFi drop, service restart)
          - A download is in progress (shouldn't happen — we're single-threaded)

        Safe to remove: the file will be re-downloaded from K7 next cycle.
        """
        nas_path = self.config["download"]["nas_mount_path"]
        count = 0
        total_bytes = 0
        for root, _dirs, filenames in os.walk(nas_path):
            for fn in filenames:
                if fn.endswith(".partial"):
                    fp = os.path.join(root, fn)
                    try:
                        sz = os.path.getsize(fp)
                        os.remove(fp)
                        count += 1
                        total_bytes += sz
                        self.log.info(f"Removed stale partial: {fp} ({sz / 1024 / 1024:.1f}MB)")
                    except OSError as e:
                        self.log.warning(f"Could not remove {fp}: {e}")
        if count:
            self.log.info(
                f"Cleaned {count} stale .partial files "
                f"({total_bytes / 1024 / 1024:.1f}MB recovered)"
            )

    def _verify_nas_integrity(self):
        """Check that verified files still exist on NAS.

        If a user manually deletes footage from the NAS, the DB still
        marks it as verified.  This method detects the mismatch and
        resets the DB entry so the file will be re-downloaded from K7
        on the next cycle (if it still exists on the camera).

        Files already deleted from K7 (deleted_from_k7=1) are skipped —
        the source is gone and the file is unrecoverable.
        """
        if not self._is_nas_mounted():
            self.log.warning("NAS not mounted — skipping integrity check")
            return

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT remote_path, local_path FROM downloaded_files
                   WHERE verified = 1 AND deleted_from_k7 = 0""",
            ).fetchall()
            reset_count = 0
            for remote_path, local_path in rows:
                if local_path and not os.path.exists(local_path):
                    conn.execute(
                        "DELETE FROM downloaded_files WHERE remote_path = ?",
                        (remote_path,),
                    )
                    reset_count += 1
                    self.log.info(
                        f"NAS file missing, will re-download: {remote_path}"
                    )
            if reset_count:
                conn.commit()
                self.log.info(
                    f"Reset {reset_count} DB entries for files "
                    f"missing from NAS (will re-download)"
                )

    def _remove_unverified_downloads(self):
        """Remove files from NAS that were downloaded but failed verification.

        These are corrupt/incomplete files from previous cycles.
        They will be re-downloaded fresh on the next attempt.
        """
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT remote_path, local_path FROM downloaded_files
                   WHERE verified = 0""",
            ).fetchall()
            for remote_path, local_path in rows:
                if local_path and os.path.exists(local_path):
                    try:
                        os.remove(local_path)
                        self.log.info(
                            f"Removed unverified file for re-download: {local_path}"
                        )
                    except OSError as e:
                        self.log.warning(f"Could not remove {local_path}: {e}")
                # Remove DB entry so it will be re-downloaded
                conn.execute(
                    "DELETE FROM downloaded_files WHERE remote_path = ?",
                    (remote_path,),
                )
            if rows:
                conn.commit()
                self.log.info(
                    f"Cleared {len(rows)} unverified DB entries for re-download"
                )

    def _retry_pending_deletes(self):
        """Retry deleting files from K7 that were verified but not yet deleted.

        This handles the case where a previous cycle verified and saved a file
        but the K7 delete failed (e.g. WiFi dropped right after verification).
        """
        pending = self._get_pending_deletes()
        if not pending:
            return

        self.log.info(f"Retrying {len(pending)} pending K7 deletions...")
        deleted_count = 0
        failed_count = 0
        for item in pending:
            remote_path = item["remote_path"]
            local_path = item["local_path"]

            # Double-check the NAS file still exists and is intact
            if not local_path or not os.path.exists(local_path):
                self.log.warning(
                    f"NAS file missing for pending delete, skipping: {remote_path}"
                )
                continue

            # Throttle: K7 Novatek httpd crashes after ~8 rapid deletes
            if deleted_count > 0:
                time.sleep(0.5)

            if self.k7.delete_file(remote_path):
                self._mark_deleted_from_k7(remote_path)
                deleted_count += 1
                self.log.info(f"  Pending delete OK: {remote_path}")
            else:
                failed_count += 1
                self.log.warning(f"  Pending delete failed: {remote_path}")
                if failed_count >= 3:
                    self.log.warning("3 delete failures — stopping pending deletes")
                    break

        if deleted_count:
            self.log.info(
                f"Pending deletes complete: {deleted_count}/{len(pending)} "
                f"deleted from K7"
            )
        self._report_db_stats()

    def _make_local_path(self, remote_path: str) -> str:
        """Generate local NAS path preserving K7 folder structure under date dirs.

        K7 remote_path examples:
          /INNOVVK7/Movie_E/20260309221222_000290_R.MP4
          /INNOVVK7/Photo_E/20260310000324_000319_R.JPG
          /INNOVVK7/EMR_E/20260310000312_000311_RK.MP4

        Result on NAS (organize_by_date=True):
          /mnt/nas/dashcam/2026-03-09/Movie_E/20260309221222_000290_R.MP4
          /mnt/nas/dashcam/2026-03-10/Photo_E/20260310000324_000319_R.JPG
          /mnt/nas/dashcam/2026-03-10/EMR_E/20260310000312_000311_RK.MP4

        The /INNOVVK7/ root prefix is stripped; the rest of the K7 folder
        hierarchy (Movie_E, Photo_E, EMR_E, etc.) is preserved.
        """
        nas_base = self.config["download"]["nas_mount_path"]
        filename = os.path.basename(remote_path)

        # Strip the /INNOVVK7/ prefix to get the K7-relative path
        # e.g. /INNOVVK7/Movie_E/file.MP4 -> Movie_E/file.MP4
        # e.g. /INNOVVK7/Photo_E/file.JPG -> Photo_E/file.JPG
        k7_root = "/INNOVVK7"
        if remote_path.startswith(k7_root):
            rel_path = remote_path[len(k7_root):].lstrip("/")
        else:
            rel_path = remote_path.lstrip("/")

        if self.config["download"]["organize_by_date"]:
            # Extract date from filename: YYYYMMDDHHMMSS_NNNNNN_[F|R].MP4
            try:
                date_part = filename[:8]  # YYYYMMDD
                if len(date_part) == 8 and date_part.isdigit():
                    date_dir = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:8]}"
                else:
                    date_dir = datetime.now().strftime("%Y-%m-%d")
            except (ValueError, IndexError):
                date_dir = datetime.now().strftime("%Y-%m-%d")

            return os.path.join(nas_base, date_dir, rel_path)
        else:
            return os.path.join(nas_base, rel_path)

    @staticmethod
    def _set_file_timestamp(local_path: str, remote_path: str):
        """Set file mtime/atime from the K7 filename timestamp.

        K7 filename format: YYYYMMDDHHMMSS_NNNNNN_[F|R].MP4
        Example: 20260309221222_000290_R.MP4 -> 2026-03-09 22:12:22

        Preserves the original recording timestamp so files sort correctly
        by date on the NAS regardless of when they were downloaded.
        """
        try:
            ts_str = os.path.basename(remote_path)[:14]  # YYYYMMDDHHMMSS
            if len(ts_str) == 14 and ts_str.isdigit():
                dt = datetime(
                    int(ts_str[0:4]), int(ts_str[4:6]), int(ts_str[6:8]),
                    int(ts_str[8:10]), int(ts_str[10:12]), int(ts_str[12:14]),
                )
                epoch = dt.timestamp()
                os.utime(local_path, (epoch, epoch))
                logging.getLogger("innovv-k7").debug(
                    f"Set timestamp {dt.isoformat()} on {local_path}"
                )
        except (ValueError, OSError) as e:
            logging.getLogger("innovv-k7").warning(
                f"Could not set timestamp on {local_path}: {e}"
            )

    @staticmethod
    def _find_active_recording_files(files: list[dict]) -> set[str]:
        """Find the newest file per recording directory — likely being written to.

        The K7 records in segments (~3 min each). While powered on, the newest
        file in each directory (Movie_E, Movie_F, EMR_E, etc.) is actively being
        written to by the Novatek firmware. Downloading it mid-write causes HTTP
        errors or partial/corrupt files.

        Files are identified by their timestamp prefix (e.g. 20260309221222).
        The newest file per directory per camera (F/R suffix) is skipped.

        Returns a set of remote paths to skip.
        """
        # Group files by directory: e.g. "/INNOVVK7/Movie_E" -> [file1, file2, ...]
        from collections import defaultdict
        dir_files = defaultdict(list)
        for f in files:
            path = f["path"]
            directory = path.rsplit("/", 1)[0] if "/" in path else ""
            if directory:
                dir_files[directory].append(path)

        active = set()
        for directory, paths in dir_files.items():
            # Sort by filename (timestamp-based) — newest last
            paths.sort()
            if paths:
                active.add(paths[-1])
        return active

    def run_dump_cycle(self) -> bool:
        """
        Execute one complete dump cycle.
        Returns True if dump completed successfully.
        """
        session_start = datetime.now()
        session_id = None

        # Record session start
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO dump_sessions (started_at, status) VALUES (?, 'started')",
                (session_start.isoformat(),),
            )
            session_id = cur.lastrowid
            conn.commit()

        try:
            self.openhab.update_status("connected")

            # Step 0: Report Pi disk space
            self._report_pi_disk_space()

            # Step 0a: Clean up stale .partial files from interrupted downloads
            self._clean_stale_partials()

            # Step 0b: Check NAS integrity (detect manually deleted files)
            self._verify_nas_integrity()

            # Step 0c: Remove unverified files (will be re-downloaded fresh)
            self._remove_unverified_downloads()

            # Step 1: Heartbeat
            self.log.info("Sending heartbeat to K7...")
            if self.k7.heartbeat():
                self.openhab.update_camera_online(True)
            else:
                self.openhab.update_camera_online(False)
                self.log.warning("Heartbeat failed, K7 may not respond to API")

            # Step 1b: Retry any pending K7 deletions from previous cycles
            self._retry_pending_deletes()

            # Step 1c: Make sure httpd is alive before proceeding.
            # Deleting files causes the K7's Novatek httpd to crash
            # after a few seconds.  The deletes themselves succeed
            # (rapid burst), but the httpd needs up to 60s to recover.
            if not self.k7.wait_ready(max_wait=60):
                self.log.error("K7 httpd not responding after pending deletes — aborting cycle")
                self.openhab.update_status("error: K7 httpd down")
                self.openhab.update_error("K7 httpd unresponsive after deletes")
                self._update_session(session_id, 0, 0, "error_httpd")
                return False

            # Step 2: Get file listing
            self.log.info("Getting file listing from K7...")
            self.openhab.update_status("scanning")
            files = self.k7.get_file_listing()

            if files is None:
                self.log.info("HTTP API file listing failed, falling back to HTML directory listing")
                files = self.k7.http_list_files(self.config["download"]["remote_path"])

            if not files:
                self.log.info("No files found on K7")
                self.openhab.update_status("complete (no new files)")
                return True

            self.log.info(f"Found {len(files)} files on K7")
            self.openhab.update_files_on_camera(len(files))

            # Step 2b: Skip Movie_E if disabled in OpenHAB
            if not self.openhab.is_movie_e_enabled():
                before = len(files)
                files = [f for f in files if "/Movie_E/" not in f["path"]]
                skipped = before - len(files)
                if skipped:
                    self.log.info(f"Movie_E disabled — skipped {skipped} loop video files")

            # Step 2c: Skip actively-recording files
            # The K7 records continuously while powered — the newest file in each
            # recording directory (Movie_E, Movie_F, EMR_E, etc.) is being actively
            # written to and cannot be reliably downloaded mid-write. Skip it now;
            # it will be picked up in the next dump cycle once the K7 has moved on
            # to a new segment.
            active_files = self._find_active_recording_files(files)
            if active_files:
                files = [f for f in files if f["path"] not in active_files]
                self.log.info(
                    f"Skipped {len(active_files)} actively-recording file(s): "
                    + ", ".join(os.path.basename(p) for p in active_files)
                )

            # Step 3: Filter out already-downloaded files
            new_files = [
                f for f in files
                if not self._is_already_downloaded(f["path"], f.get("size", 0))
            ]
            self.log.info(f"{len(new_files)} new files to download (of {len(files)} total)")

            if not new_files:
                self.openhab.update_status("complete (no new files)")
                self._update_session(session_id, 0, 0, "complete")
                return True

            # Step 4: Check NAS space
            if not self._check_nas_space():
                self.openhab.update_status("error: NAS space low")
                self.openhab.update_error("NAS free space below minimum")
                self._update_session(session_id, 0, 0, "error_space")
                return False

            # Step 5: Download new files
            total_files = len(new_files)
            downloaded_count = 0
            downloaded_bytes = 0
            consecutive_failures = 0
            max_consecutive_failures = 3  # Abort if K7 drops off
            max_bytes = self.config["safety"]["max_total_download_gb"] * (1024 ** 3)
            max_dump_sec = self.config["safety"]["max_dump_duration_min"] * 60
            loop_aborted_error = False

            movie_e_was_enabled = self.openhab.is_movie_e_enabled()
            verified_for_deletion = []  # Batch-delete after all downloads

            for i, file_info in enumerate(new_files, 1):
                if not self.running:
                    self.log.info("Shutdown requested, stopping download")
                    break

                if downloaded_bytes >= max_bytes:
                    self.log.warning(f"Reached max download limit ({self.config['safety']['max_total_download_gb']}GB)")
                    break

                elapsed_sec = (datetime.now() - session_start).total_seconds()
                if elapsed_sec > max_dump_sec:
                    self.log.warning(
                        f"Dump duration limit reached "
                        f"({self.config['safety']['max_dump_duration_min']} min) — stopping"
                    )
                    break

                if consecutive_failures >= max_consecutive_failures:
                    self.log.error(
                        f"Aborting: {consecutive_failures} consecutive download "
                        f"failures — K7 likely offline or WiFi dropped"
                    )
                    self.openhab.update_status("error: K7 connection lost")
                    self.openhab.update_error(f"{consecutive_failures} consecutive download failures")
                    loop_aborted_error = True
                    break

                remote_path = file_info["path"]

                # Re-check Movie_E toggle mid-cycle so the user can
                # disable loop-video downloads without waiting for the
                # entire cycle to finish.
                if "/Movie_E/" in remote_path and movie_e_was_enabled:
                    if not self.openhab.is_movie_e_enabled():
                        movie_e_was_enabled = False
                        remaining = sum(
                            1 for f in new_files[i - 1:]
                            if "/Movie_E/" in f["path"]
                        )
                        self.log.info(
                            f"Movie_E disabled mid-cycle — "
                            f"skipping {remaining} remaining loop video files"
                        )
                if "/Movie_E/" in remote_path and not movie_e_was_enabled:
                    continue
                file_size = file_info.get("size", 0)
                local_path = self._make_local_path(remote_path)

                self.openhab.update_status(f"dumping ({i}/{total_files})")
                self.log.info(f"[{i}/{total_files}] Downloading: {remote_path} ({file_size / 1024 / 1024:.1f}MB)")

                # Send heartbeat before each download to keep connection alive
                self.k7.heartbeat()

                # Progress callback for near-realtime speed reporting.
                # Reports to OpenHAB every ~5 seconds during download.
                _dl_start_time = time.time()
                _last_speed_report = [0.0]  # mutable for closure

                def _progress_cb(downloaded_now, total_size):
                    now = time.time()
                    if now - _last_speed_report[0] < 5.0:
                        return  # throttle to every 5s
                    _last_speed_report[0] = now
                    elapsed = now - _dl_start_time
                    if elapsed > 0.5:
                        speed_mbps = (downloaded_now / (1024 * 1024)) / elapsed
                        pct = int(downloaded_now * 100 / total_size) if total_size else 0
                        speed_text = f"{speed_mbps:.1f} MB/s ({pct}%)"
                        self.openhab.update_transfer_speed(speed_text)

                # Download via HTTP (returns DownloadResult with SHA-256)
                result = self.k7.download_file(
                    remote_path, local_path,
                    progress_callback=_progress_cb,
                    cancel_check=lambda: not self.running,
                )

                if result.error == "cancelled":
                    self.log.info("Download aborted by shutdown")
                    break

                if result.success:
                    actual_size = result.bytes_downloaded

                    # --- Read-back verification from NAS ---
                    # Re-reads the entire file and re-computes SHA-256.
                    # This catches: CIFS write corruption, truncated NAS writes,
                    # flipped bits, and partial files from dropped WiFi.
                    verified = False
                    ok, reason = K7ApiClient.verify_local_file(
                        local_path, actual_size, result.sha256
                    )
                    if ok:
                        verified = True
                        self.log.info(
                            f"  -> VERIFIED: {local_path} "
                            f"({actual_size / 1024 / 1024:.1f}MB, "
                            f"sha256={result.sha256[:16]}...)"
                        )
                        # Preserve K7 recording timestamp on the NAS file
                        self._set_file_timestamp(local_path, remote_path)
                    else:
                        self.log.error(
                            f"  -> VERIFICATION FAILED for {remote_path}: {reason}"
                        )
                        self.openhab.update_error(f"Verify failed: {os.path.basename(remote_path)}: {reason[:80]}")
                        # Keep the file for manual inspection but don't trust it
                        self.log.error(
                            f"  -> File kept at {local_path} for inspection "
                            f"(NOT deleting from K7)"
                        )

                    effective_size = actual_size if actual_size > 0 else file_size
                    self._record_download(
                        remote_path, effective_size, local_path,
                        sha256=result.sha256, verified=verified,
                    )
                    downloaded_count += 1
                    downloaded_bytes += effective_size
                    consecutive_failures = 0  # Reset on success

                    # Live-update OpenHAB with running totals
                    self.openhab.update_files_downloaded(downloaded_count)
                    self.openhab.update_bytes_downloaded(downloaded_bytes)

                    # Only delete from K7 if VERIFIED and configured to delete.
                    # Deletions are deferred to a batch phase after all downloads
                    # complete — interleaving deletes with downloads crashes the
                    # K7's Novatek httpd mid-transfer.
                    if (verified
                            and self.config["download"].get(
                                "delete_after_verified_download", False)):
                        verified_for_deletion.append(remote_path)

                    # Live-update verified/deleted/pending counts
                    self._report_db_stats()

                    # Check NAS space every 10 files to catch filling up
                    if downloaded_count % 10 == 0 and downloaded_count > 0:
                        self.openhab.update_wifi_signal(self.wifi.get_signal_dbm())
                        if not self._check_nas_space():
                            self.log.error("NAS space low — stopping downloads")
                            self.openhab.update_status("error: NAS full")
                            self.openhab.update_error("NAS full during download")
                            loop_aborted_error = True
                            break
                else:
                    consecutive_failures += 1
                    self.log.error(
                        f"  -> FAILED to download {remote_path}"
                        f"{': ' + result.error if result.error else ''}"
                    )

            # Step 6: Report download results
            elapsed = (datetime.now() - session_start).total_seconds()
            speed_mbps = (downloaded_bytes / 1024 / 1024) / elapsed if elapsed > 0 else 0
            bytes_str = self._format_bytes(downloaded_bytes)

            self.log.info(
                f"Dump {'aborted' if loop_aborted_error else 'complete'}: "
                f"{downloaded_count}/{total_files} files, "
                f"{bytes_str} in {elapsed:.0f}s ({speed_mbps:.1f} MB/s)"
            )

            # Step 7: Batch-delete verified files from K7
            # Deletions are done AFTER all downloads to avoid crashing the
            # K7's Novatek httpd — interleaving delete+download causes
            # truncated transfers and "Connection refused" errors.
            if verified_for_deletion and self.running:
                self.log.info(
                    f"Batch-deleting {len(verified_for_deletion)} verified files from K7..."
                )
                self.openhab.update_status("deleting from K7")
                del_ok = 0
                del_fail = 0
                # Throttled deletes — the K7 Novatek httpd crashes
                # after ~8 rapid-fire deletes.  A 500ms delay between
                # requests keeps the server stable.
                for remote_path in verified_for_deletion:
                    if not self.running:
                        self.log.info("Shutdown requested, stopping batch delete")
                        break
                    # Throttle: avoid crashing K7 httpd
                    if del_ok > 0:
                        time.sleep(0.5)
                    if self.k7.delete_file(remote_path):
                        self._mark_deleted_from_k7(remote_path)
                        del_ok += 1
                    else:
                        del_fail += 1
                        self.log.warning(
                            f"  Delete failed: {remote_path} (will retry next cycle)"
                        )
                        if del_fail >= 3:
                            self.log.warning(
                                "3 delete failures — stopping batch delete, "
                                "remaining files deferred to next cycle"
                            )
                            break

                self.log.info(
                    f"Batch delete: {del_ok} deleted, {del_fail} failed, "
                    f"{len(verified_for_deletion) - del_ok - del_fail} deferred"
                )

            # Step 8: Final status update
            self.openhab.update_transfer_speed("")  # Clear speed display

            # Only clear errors and mark complete if loop finished without error.
            # Error-break paths already set their own status/error messages.
            if not loop_aborted_error:
                self.openhab.update_status("complete")
                self.openhab.update_error("")  # Clear stale errors on success

            self.openhab.update_last_dump()
            self.openhab.update_files_downloaded(downloaded_count)
            self.openhab.update_bytes_downloaded(downloaded_bytes)
            self._report_db_stats()

            status = "error" if loop_aborted_error else "complete"
            self._update_session(session_id, downloaded_count, downloaded_bytes, status)
            return not loop_aborted_error

        except Exception as e:
            self.log.exception(f"Dump cycle failed: {e}")
            self.openhab.update_status(f"error: {str(e)[:50]}")
            self.openhab.update_error(f"Dump exception: {str(e)[:150]}")
            self.openhab.update_transfer_speed("")  # Clear speed on error
            if session_id:
                self._update_session(session_id, 0, 0, f"error: {str(e)[:100]}")
            return False

    def _update_session(self, session_id: int, files: int, bytes_dl: int, status: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE dump_sessions
                   SET completed_at = ?, files_downloaded = ?,
                       bytes_downloaded = ?, status = ?
                   WHERE id = ?""",
                (datetime.now().isoformat(), files, bytes_dl, status, session_id),
            )
            conn.commit()

    @staticmethod
    def _format_bytes(num_bytes: int) -> str:
        if num_bytes < 1024:
            return f"{num_bytes}B"
        elif num_bytes < 1024 ** 2:
            return f"{num_bytes / 1024:.1f}KB"
        elif num_bytes < 1024 ** 3:
            return f"{num_bytes / 1024 ** 2:.1f}MB"
        else:
            return f"{num_bytes / 1024 ** 3:.2f}GB"

    def run(self):
        """Main service loop: scan for K7, dump when found, repeat."""
        self.log.info("INNOVV K7 Auto-Dump Service starting...")

        # Clear stale dashboard state from a previous crash/SIGKILL.
        # Without this, the UI can show "online" even though the K7 is off.
        self.openhab.update_status("idle")
        self.openhab.update_camera_online(False)
        self.openhab.update_wifi_signal(None)

        scan_interval = self.config["k7_wifi"]["scan_interval_sec"]
        max_dump_sec = self.config["safety"]["max_dump_duration_min"] * 60

        while self.running:
            try:
                # Scan for K7 WiFi
                self.log.debug(f"Scanning for SSID: {self.config['k7_wifi']['ssid']}")

                if self.wifi.is_ssid_visible():
                    self.log.info(f"K7 WiFi detected! SSID: {self.config['k7_wifi']['ssid']}")
                    self.openhab.update_status("k7_detected")

                    # Connect to K7 WiFi
                    if self.wifi.connect():
                        self.log.info("Connected to K7 WiFi")
                        self.openhab.update_wifi_signal(self.wifi.get_signal_dbm())
                        self.openhab.update_wifi_band(self.wifi._detected_freq)
                        dump_start = time.time()

                        # Run dump with safety timeout
                        dump_ok = False
                        try:
                            dump_ok = self.run_dump_cycle()
                        except Exception as e:
                            self.log.exception(f"Dump cycle error: {e}")
                            self.openhab.update_status(f"error: {str(e)[:50]}")

                        elapsed = time.time() - dump_start
                        if elapsed > max_dump_sec:
                            self.log.warning(
                                f"Dump took {elapsed:.0f}s (limit: {max_dump_sec}s)"
                            )

                        # Disconnect from K7 WiFi
                        self.wifi.disconnect()
                        self.log.info("Disconnected from K7 WiFi")
                        self.openhab.update_wifi_signal(None)
                        self.openhab.update_camera_online(False)
                        # Only set idle if dump succeeded; keep error status visible
                        if dump_ok:
                            self.openhab.update_status("idle")

                        # Wait a bit before scanning again (K7 might still be on)
                        self._sleep(60)
                    else:
                        self.log.warning("Failed to connect to K7 WiFi")
                        self.openhab.update_status("wifi_connect_failed")
                        self.openhab.update_error("WiFi connect failed")
                        self._sleep(scan_interval)
                else:
                    self._sleep(scan_interval)

            except Exception as e:
                self.log.exception(f"Main loop error: {e}")
                self.openhab.update_error(f"Main loop: {str(e)[:150]}")
                self._sleep(scan_interval)

        self.log.info("Service stopped")
        self.openhab.update_status("offline")

    def _sleep(self, seconds: int):
        """Interruptible sleep."""
        for _ in range(seconds):
            if not self.running:
                break
            time.sleep(1)


def main():
    config_path = os.environ.get(
        "INNOVV_CONFIG",
        "/opt/innovv-k7/config.json",
    )

    if not os.path.exists(config_path):
        print(f"Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    service = InnovvK7Dump(config_path)
    service.run()


if __name__ == "__main__":
    main()
