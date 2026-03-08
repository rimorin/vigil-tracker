import ctypes
import hashlib
import logging
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "Vigil"
LOG_DIR = Path.home() / "Library" / "Logs" / "Vigil"
APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

ACTIVITY_LOG = APP_SUPPORT_DIR / "detailed_activity_log.txt"
INTEGRITY_FILE = APP_SUPPORT_DIR / "detailed_activity_log.txt.sha256"
DAEMON_LOG = LOG_DIR / "tracker_daemon.log"

_running = True
_current_session: Optional[dict] = None  # accessible by SIGTERM handler

# Rotating file logger — 5 MB per file, keep 3 backups
_handler = RotatingFileHandler(DAEMON_LOG, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
_handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_logger = logging.getLogger("tracker")
_logger.setLevel(logging.INFO)
_logger.addHandler(_handler)


def _log(message: str):
    _logger.info(message)


# ---------------------------------------------------------------------------
# Idle detection — Apple CoreGraphics API (macOS 10.4+)
# Ref: developer.apple.com/documentation/coregraphics/cgeventsource/
#      secondssincelasteventtype(_:eventtype:)
# ---------------------------------------------------------------------------

_cg = ctypes.CDLL(
    "/System/Library/Frameworks/ApplicationServices.framework"
    "/Frameworks/CoreGraphics.framework/CoreGraphics"
)
_cg.CGEventSourceSecondsSinceLastEventType.restype  = ctypes.c_double
_cg.CGEventSourceSecondsSinceLastEventType.argtypes = [ctypes.c_uint32, ctypes.c_uint32]

_kCGEventSourceStateCombinedSessionState = 0        # Apple: combinedSessionState = 0
_kCGAnyInputEventType                    = 0xFFFFFFFF  # Apple: kCGAnyInputEventType macro

IDLE_THRESHOLD      = 120  # seconds of no input before pausing the session timer
MIN_SESSION_DURATION =  5  # sessions shorter than this (seconds) are discarded


def get_idle_seconds() -> float:
    """Return seconds since the last keyboard/mouse/tablet input event."""
    try:
        return _cg.CGEventSourceSecondsSinceLastEventType(
            _kCGEventSourceStateCombinedSessionState,
            _kCGAnyInputEventType,
        )
    except Exception:
        return 0.0


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

# Chromium-based browsers that expose "active tab of front window" via AppleScript.
# Arc's support is limited — wrapped in try/end try for silent fallback.
# Vivaldi is excluded (known AppleScript gap — does not expose active tab URL).
CHROMIUM_ACTIVE_TAB_BROWSERS = ["Google Chrome", "Microsoft Edge", "Brave Browser", "Arc"]


def _installed_chromium_browsers() -> list:
    """Return only the CHROMIUM_ACTIVE_TAB_BROWSERS that are installed on this machine.

    AppleScript validates 'tell application X' blocks against the app's scripting
    dictionary at COMPILE time. Including a block for an app that isn't installed
    causes a parse error (-2741). Only installed browsers are added to the script.
    """
    search_roots = ["/Applications", str(Path.home() / "Applications")]
    installed = []
    for browser in CHROMIUM_ACTIVE_TAB_BROWSERS:
        if any((Path(root) / f"{browser}.app").is_dir() for root in search_roots):
            installed.append(browser)
    return installed


def get_active_tab_applescript() -> str:
    """Build AppleScript that returns '[Browser] URL' for the frontmost browser only.

    Uses inline 'and application X is running' guards to avoid nested if/end if
    blocks before else-if chains (which are invalid in AppleScript).
    Safari uses 'current tab' (confirmed via sdef, property cTab).
    Chromium browsers use 'active tab' (confirmed via sdef, property acTa).
    Arc is wrapped in try/end try — limited sdef, silent skip on failure.
    Note: 'result' is a reserved AppleScript keyword; variable named 'activeTab'.
    """
    chromium_blocks = ""
    for browser in _installed_chromium_browsers():
        chromium_blocks += f"""
else if frontApp is "{browser}" and application "{browser}" is running then
    tell application "{browser}"
        try
            set activeURL to URL of active tab of front window
            if activeURL is not missing value then
                set activeTab to "[{browser}] " & activeURL
            end if
        end try
    end tell"""

    return f"""
set activeTab to ""
set frontApp to ""
tell application "System Events"
    try
        set frontApp to name of first application process whose frontmost is true
    end try
end tell
if frontApp is "Safari" and application "Safari" is running then
    tell application "Safari"
        try
            set activeURL to URL of current tab of front window
            if activeURL is not missing value then
                set activeTab to "[Safari] " & activeURL
            end if
        end try
    end tell{chromium_blocks}
end if
return activeTab"""


# ---------------------------------------------------------------------------
# Integrity hash
# ---------------------------------------------------------------------------

def _update_integrity_hash():
    """Recompute and store the SHA-256 of the activity log."""
    try:
        digest = hashlib.sha256(ACTIVITY_LOG.read_bytes()).hexdigest()
        INTEGRITY_FILE.write_text(digest)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def _log_duration_entry(label: str, duration_seconds: int):
    """Append '[timestamp] label [duration: Xs]' and refresh the integrity hash."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(ACTIVITY_LOG, "a") as f:
        f.write(f"[{timestamp}] {label} [duration: {duration_seconds}s]\n")
    _update_integrity_hash()


def _finalize_session(session: dict, now: datetime):
    """Compute net active duration (excluding idle gaps) and log if ≥ MIN_SESSION_DURATION."""
    idle_accumulated = session["idle_accumulated"]
    if session["idle_start"] is not None:
        idle_accumulated += int((now - session["idle_start"]).total_seconds())
    duration = int((now - session["start_time"]).total_seconds()) - idle_accumulated
    if duration >= MIN_SESSION_DURATION:
        _log_duration_entry(session["label"], duration)


# ---------------------------------------------------------------------------
# Boot/restart detection
# ---------------------------------------------------------------------------

def get_boot_time() -> Optional[datetime]:
    """Return the system boot time by parsing sysctl kern.boottime."""
    try:
        result = subprocess.run(
            ['sysctl', '-n', 'kern.boottime'],
            capture_output=True, text=True, timeout=5
        )
        # Output: { sec = 1741234567, usec = 123456 } Sun Mar  8 ...
        match = re.search(r'sec\s*=\s*(\d+)', result.stdout)
        if match:
            return datetime.fromtimestamp(int(match.group(1)))
    except Exception:
        pass
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
        _log(f"Shutdown/restart detected. Last log: {last_log_time}, Boot: {boot_time}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    global _current_session
    _log("Vigil started.")
    # Reconcile the integrity hash on every startup.  If the process was killed
    # between writing a log entry and updating the hash (e.g. power loss), the
    # sidecar would be stale and trigger a false tamper alert.  Recomputing here
    # ensures the summariser always sees a consistent state.
    _update_integrity_hash()
    check_for_shutdown_event()
    script_source = get_active_tab_applescript()

    while _running:
        proc = subprocess.Popen(
            ['osascript', '-e', script_source],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        out, _ = proc.communicate()
        active_label = out.strip() if out else ""

        if "missing value" in active_label:
            active_label = ""

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
            _current_session = (
                {
                    "label":            active_label,
                    "start_time":       now,
                    "idle_accumulated": 0,
                    "idle_start":       now if idle_secs >= IDLE_THRESHOLD else None,
                }
                if active_label else None
            )

        time.sleep(5)

    # Finalise any session still open on clean exit (not already handled by SIGTERM)
    if _current_session is not None:
        _finalize_session(_current_session, datetime.now())

    _log("Vigil stopped.")


if __name__ == "__main__":
    main()

