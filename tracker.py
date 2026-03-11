import hashlib
import logging
import re
import signal
import sys
import threading
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Optional

import psutil

import alerter
import config
from platform_common import acquire_instance_lock, get_app_dirs

# ---------------------------------------------------------------------------
# Platform delegation — all platform-specific logic lives in dedicated modules
# so that Windows changes never touch macOS code and vice versa.
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    from platforms.windows import tracker_windows as _platform
else:
    from platforms.macos import tracker_macos as _platform

get_idle_seconds = _platform.get_idle_seconds
get_active_label = _platform.get_active_label

# Trigger TCC Automation dialogs for all installed browsers at startup so that
# macOS attributes the grants to python3.12 (the daemon process) rather than
# to whichever terminal happened to run an earlier install/test command.
if hasattr(_platform, "request_automation_permissions"):
    _platform.request_automation_permissions()


APP_SUPPORT_DIR, LOG_DIR = get_app_dirs()
APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

ACTIVITY_LOG = APP_SUPPORT_DIR / "detailed_activity_log.txt"
INTEGRITY_FILE = APP_SUPPORT_DIR / "detailed_activity_log.txt.sha256"
ALERT_CURSOR_FILE = APP_SUPPORT_DIR / "alerter_cursor.txt"
DAEMON_LOG = LOG_DIR / "tracker_daemon.log"
PID_FILE = APP_SUPPORT_DIR / "tracker.pid"

_running = True
_current_session: Optional[dict] = None  # accessible by SIGTERM handler

# ---------------------------------------------------------------------------
# Incremental SHA-256 state — avoids re-reading the full log on every write
# ---------------------------------------------------------------------------
_integrity_hasher = None   # hashlib.sha256 object kept alive in memory
_integrity_file_offset: int = 0  # byte offset up to which we've hashed

# Rotating file logger — 5 MB per file, keep 3 backups
_handler = RotatingFileHandler(DAEMON_LOG, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
_handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_logger = logging.getLogger("tracker")
_logger.setLevel(logging.DEBUG)
_logger.addHandler(_handler)


# ---------------------------------------------------------------------------
# Idle detection thresholds
# ---------------------------------------------------------------------------

IDLE_THRESHOLD      = 120  # seconds of no input before pausing the session timer
MIN_SESSION_DURATION =  5  # sessions shorter than this (seconds) are discarded

# ---------------------------------------------------------------------------
# Alert scan interval
# ---------------------------------------------------------------------------

_ALERT_SCAN_INTERVAL_SECS: float = config.ALERT_SCAN_INTERVAL_MINUTES * 60

# Lock prevents a slow scan (e.g. SMTP retry) from overlapping with the next.
_alert_scan_lock = threading.Lock()


def _run_scan() -> None:
    """Run scan_and_alert; skip silently if the previous scan is still in flight."""
    if not _alert_scan_lock.acquire(blocking=False):
        return
    try:
        alerter.scan_and_alert(ACTIVITY_LOG, ALERT_CURSOR_FILE)
    finally:
        _alert_scan_lock.release()


# ---------------------------------------------------------------------------
# Signal handling — finalise open session before exit
# ---------------------------------------------------------------------------

def _handle_signal(signum, frame):
    """Stop the main loop on SIGTERM/SIGINT.

    Session finalisation is intentionally deferred to the post-loop block in
    main() so it always runs in the main thread, avoiding a race condition
    where the signal could arrive while the loop is mid-way through updating
    _current_session's idle fields.
    """
    global _running
    _running = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT,  _handle_signal)


# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------

def _update_integrity_hash() -> None:
    """Incrementally update the SHA-256 sidecar — only reads newly appended bytes.

    Keeps a hashlib object alive in memory so each call only hashes the delta
    since the last write.  On cold start (or after an external file rewrite such
    as log cleanup) it falls back to a full chunked read.  Uses .copy() to
    snapshot the running state for writing so the hasher is never mutated.
    """
    global _integrity_hasher, _integrity_file_offset
    try:
        if not ACTIVITY_LOG.exists():
            return
        current_size = ACTIVITY_LOG.stat().st_size

        # File was truncated/replaced externally (e.g. log-retention cleanup) —
        # reset so the next branch does a full rehash from the new content.
        if _integrity_hasher is not None and current_size < _integrity_file_offset:
            _integrity_hasher = None
            _integrity_file_offset = 0

        if _integrity_hasher is None:
            # Cold start: hash the entire file in 64 KB chunks to cap RAM use.
            h = hashlib.sha256()
            with open(ACTIVITY_LOG, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            _integrity_hasher = h
            _integrity_file_offset = current_size
        elif current_size > _integrity_file_offset:
            # Incremental path: only hash the bytes appended since last call.
            with open(ACTIVITY_LOG, "rb") as f:
                f.seek(_integrity_file_offset)
                new_bytes = f.read(current_size - _integrity_file_offset)
            _integrity_hasher.update(new_bytes)
            _integrity_file_offset = current_size

        # .copy() snapshots internal state without mutating the running hasher.
        INTEGRITY_FILE.write_text(_integrity_hasher.copy().hexdigest())
    except Exception:
        # Reset on any error; next call will cold-start cleanly.
        _integrity_hasher = None
        _integrity_file_offset = 0


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def _log_duration_entry(label: str, duration_seconds: int, is_adult: bool = False):
    """Append '[timestamp] label [duration: Xs]' and refresh the integrity hash.

    When *is_adult* is True the line is tagged with [FLAGGED_CONTENT] so the
    periodic scan in main() can find and report it without re-running detection.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tag = f" {alerter.FLAGGED_TAG}" if is_adult else ""
    with open(ACTIVITY_LOG, "a") as f:
        f.write(f"[{timestamp}] {label} [duration: {duration_seconds}s]{tag}\n")
    _update_integrity_hash()


def _finalize_session(session: dict, now: datetime):
    """Compute net active duration (excluding idle gaps) and log if ≥ MIN_SESSION_DURATION."""
    idle_accumulated = session["idle_accumulated"]
    if session["idle_start"] is not None:
        idle_accumulated += int((now - session["idle_start"]).total_seconds())
    duration = int((now - session["start_time"]).total_seconds()) - idle_accumulated
    if duration >= MIN_SESSION_DURATION:
        _log_duration_entry(session["label"], duration, session.get("is_adult", False))


# ---------------------------------------------------------------------------
# Boot/restart detection
# ---------------------------------------------------------------------------

def get_boot_time() -> Optional[datetime]:
    """Return the system boot time (cross-platform via psutil)."""
    try:
        return datetime.fromtimestamp(psutil.boot_time())
    except Exception:
        return None


def get_last_log_time() -> Optional[datetime]:
    """Return the timestamp of the last entry in the activity log."""
    if not ACTIVITY_LOG.exists():
        return None
    try:
        with open(ACTIVITY_LOG, 'rb') as f:
            # Efficiently seek to the last non-empty line
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return None
            pos = size - 1
            while pos > 0:
                f.seek(pos)
                char = f.read(1)
                if char == b'\n' and pos != size - 1:
                    break
                pos -= 1
            last_line = f.read().decode('utf-8', errors='ignore').strip()
        match = re.match(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]', last_line)
        if match:
            return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    return None


def check_for_shutdown_event():
    """Log a system event if the machine was shut down or restarted since last run."""
    boot_time = get_boot_time()
    last_log_time = get_last_log_time()

    if boot_time and last_log_time and last_log_time < boot_time:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        event = (
            f"[{timestamp}] [SYSTEM EVENT] Shutdown or restart detected "
            f"(last activity: {last_log_time.strftime('%Y-%m-%d %H:%M:%S')}, "
            f"boot time: {boot_time.strftime('%Y-%m-%d %H:%M:%S')})"
        )
        with open(ACTIVITY_LOG, "a") as f:
            f.write(event + "\n")
        _logger.info("Shutdown/restart detected. Last log: %s, Boot: %s", last_log_time, boot_time)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    global _current_session
    acquire_instance_lock(PID_FILE, _logger)
    _logger.info("Vigil started.")
    # Reconcile the integrity hash on every startup.  If the process was killed
    # between writing a log entry and updating the hash (e.g. power loss), the
    # sidecar would be stale and trigger a false tamper alert.  Recomputing here
    # ensures the summariser always sees a consistent state.
    _update_integrity_hash()
    check_for_shutdown_event()

    # Register a platform-appropriate exit handler.  On Windows, Task Scheduler
    # uses TerminateProcess() so SIGTERM is never delivered; tracker_windows
    # registers an atexit flush instead.  On macOS this is a no-op.
    _platform.register_exit_handler(lambda: _current_session, _finalize_session)

    _last_alert_scan: float = 0.0  # time.monotonic() timestamp of last scan

    while _running:
        active_label = get_active_label()
        _logger.debug("poll: label=%r", active_label)  # TODO: remove after debug

        idle_secs = get_idle_seconds()
        now = datetime.now()

        # Track idle gaps to exclude them from the session duration
        if _current_session is not None:
            if idle_secs >= IDLE_THRESHOLD:
                if _current_session["idle_start"] is None:
                    _current_session["idle_start"] = now
            else:
                if _current_session["idle_start"] is not None:
                    idle_gap = int((now - _current_session["idle_start"]).total_seconds())
                    _current_session["idle_accumulated"] += idle_gap
                    _current_session["idle_start"] = None

        prev_label = _current_session["label"] if _current_session else ""
        if active_label != prev_label:
            if _current_session is not None:
                _finalize_session(_current_session, now)
            if active_label:
                is_adult = False
                try:
                    is_adult = alerter.check_url(active_label)
                except Exception:
                    pass
                _current_session = {
                    "label":            active_label,
                    "start_time":       now,
                    "idle_accumulated": 0,
                    "idle_start":       now if idle_secs >= IDLE_THRESHOLD else None,
                    "is_adult":         is_adult,
                }
            else:
                _current_session = None

        # Periodic alert scan — daemon thread so SMTP latency never stalls the
        # main tracking loop.  The lock in _run_scan prevents overlapping scans.
        now_mono = time.monotonic()
        if now_mono - _last_alert_scan >= _ALERT_SCAN_INTERVAL_SECS:
            threading.Thread(target=_run_scan, daemon=True).start()
            _last_alert_scan = now_mono

        time.sleep(5)

    # Finalise any session still open on clean exit (not already handled by SIGTERM)
    if _current_session is not None:
        _finalize_session(_current_session, datetime.now())

    _logger.info("Vigil stopped.")


if __name__ == "__main__":
    main()

