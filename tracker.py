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

BASE_DIR = Path(__file__).parent.resolve()
ACTIVITY_LOG = BASE_DIR / "detailed_activity_log.txt"
INTEGRITY_FILE = BASE_DIR / "detailed_activity_log.txt.sha256"
DAEMON_LOG = BASE_DIR / "tracker_daemon.log"

_running = True

# Rotating file logger — 5 MB per file, keep 3 backups
_handler = RotatingFileHandler(DAEMON_LOG, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
_handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_logger = logging.getLogger("tracker")
_logger.setLevel(logging.INFO)
_logger.addHandler(_handler)


def _log(message: str):
    _logger.info(message)


def _handle_signal(signum, frame):
    """Gracefully stop the tracker when launchd sends SIGTERM."""
    global _running
    _running = False

# Browsers that support exact URL extraction
SUPPORTED_BROWSERS = ["Safari", "Google Chrome", "Microsoft Edge", "Brave Browser", "Arc", "Vivaldi"]

# Browsers that require the Window Title fallback method
FALLBACK_BROWSERS = ["Firefox", "Tor Browser", "Opera"]

def get_tagged_applescript():
    """Generates an AppleScript that prepends the browser name to the data."""
    script = "set tracked_data to {}\n"
    
    # 1. EXACT URL TRACKING (Safari)
    script += """
    if application "Safari" is running then
        tell application "Safari"
            repeat with w in windows
                repeat with t in tabs of w
                    try
                        -- Prepend [Safari] to the URL
                        set end of tracked_data to ("[Safari] " & URL of t)
                    end try
                end repeat
            end repeat
        end tell
    end if
    """
    
    # 2. EXACT URL TRACKING (Chromium Browsers)
    for browser in SUPPORTED_BROWSERS[1:]:
        script += f"""
        if application "{browser}" is running then
            tell application "{browser}"
                repeat with w in windows
                    repeat with t in tabs of w
                        try
                            -- Prepend the specific browser name to the URL
                            set end of tracked_data to ("[{browser}] " & URL of t)
                        end try
                    end repeat
                end repeat
            end tell
        end if
        """
        
    # 3. UNIVERSAL FALLBACK: Active Window Title Tracking
    script += """
    tell application "System Events"
        try
            set activeApp to name of first application process whose frontmost is true
            if activeApp contains "Firefox" or activeApp contains "Tor" or activeApp contains "Opera" then
                set windowTitle to name of front window of application process activeApp
                -- Tag it with the fallback browser name
                set end of tracked_data to ("[" & activeApp & " - Active Tab] " & windowTitle)
            end if
        end try
    end tell
    """
    
    script += "return tracked_data"
    return script

def _update_integrity_hash():
    """Recompute and store the SHA-256 of the activity log."""
    try:
        digest = hashlib.sha256(ACTIVITY_LOG.read_bytes()).hexdigest()
        INTEGRITY_FILE.write_text(digest)
    except Exception:
        pass


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
        event = f"[{timestamp}] [SYSTEM EVENT] Shutdown or restart detected (last activity: {last_log_time.strftime('%Y-%m-%d %H:%M:%S')}, boot time: {boot_time.strftime('%Y-%m-%d %H:%M:%S')})"
        with open(ACTIVITY_LOG, "a") as f:
            f.write(event + "\n")
        _log(f"Shutdown/restart detected. Last log: {last_log_time}, Boot: {boot_time}")


def main():
    _log("Web Activity Tracker started.")
    check_for_shutdown_event()
    recorded_data = set()
    script_source = get_tagged_applescript()

    while _running:
        proc = subprocess.Popen(
            ['osascript', '-e', script_source],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        out, _ = proc.communicate()

        if out:
            current_data = [item.strip() for item in out.split(", ") if item.strip()]

            for item in current_data:
                if item not in recorded_data and "missing value" not in item:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    log_entry = f"[{timestamp}] {item}"
                    recorded_data.add(item)

                    with open(ACTIVITY_LOG, "a") as f:
                        f.write(log_entry + "\n")
                    _update_integrity_hash()

        time.sleep(5)

    _log("Web Activity Tracker stopped.")

if __name__ == "__main__":
    main()
