"""watchdog.py — independent watchdog daemon for Vigil.

Monitors the tracker and summarizer services and alerts the accountability
partner when either goes offline unexpectedly.  Also fires on SIGTERM so that
disabling Vigil via System Settings → Login Items (macOS) or Task Scheduler
(Windows) is immediately reported to the partner.
"""

import csv
import importlib
import io
import signal
import smtplib
import ssl
import subprocess
import sys
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from socket import gethostname

sys.path.insert(0, str(Path(__file__).parent))
from platform_common import get_app_dirs  # noqa: E402

_APP_SUPPORT_DIR, _ = get_app_dirs()

# .env lives alongside the source files — cache its path for existence checks.
_ENV_FILE = Path(__file__).parent / ".env"

# Sentinel written by install/uninstall scripts before legitimately stopping
# the watchdog — tells the SIGTERM handler to exit silently (no alert).
_GRACEFUL_SENTINEL = _APP_SUPPORT_DIR / "watchdog_graceful_shutdown"

# Written on every check loop so summarizer.py can detect a stale (killed)
# watchdog even when SIGKILL prevents the SIGTERM handler from firing.
HEARTBEAT_FILE = _APP_SUPPORT_DIR / "watchdog_heartbeat"

# Written by summarizer.py on a ~60 s interval.  Watchdog reads this to detect
# a forcibly killed summarizer independently of SIGTERM / launchd service status.
SUMMARIZER_HEARTBEAT_FILE = _APP_SUPPORT_DIR / "summarizer_heartbeat"
_SUMMARIZER_HEARTBEAT_STALE_SECS = 150

_CHECK_INTERVAL = 60   # seconds between service health checks
_STARTUP_GRACE  = 120  # seconds to wait at startup before alerting

_started_at: float = time.monotonic()
_last_stopped: set = set()

# SMTP config cached at startup so alerts can still be sent if .env is deleted.
_cached_smtp: dict | None = None

# Presence flags — set True the first time each file is confirmed to exist.
# If a file disappears after being seen, that is treated as a tamper attempt.
_summarizer_heartbeat_ever_seen: bool = False
_env_ever_seen: bool = False


# ---------------------------------------------------------------------------
# Email helper
# ---------------------------------------------------------------------------

def _load_smtp_cache() -> None:
    """Cache SMTP credentials on startup so alerts survive .env deletion."""
    global _cached_smtp
    try:
        if "config" in sys.modules:
            import config as _cfg
            importlib.reload(_cfg)
        else:
            import config as _cfg  # type: ignore[no-redef]
        _cached_smtp = {
            "host": _cfg.SMTP_HOST,
            "port": _cfg.SMTP_PORT,
            "user": _cfg.SMTP_USER,
            "pass": _cfg.SMTP_PASS,
            "from": _cfg.SMTP_FROM,
        }
    except Exception:
        pass


def _send_alert(subject: str, body_text: str) -> None:
    """Best-effort alert email; silently swallows all errors.

    Uses live .env config when available; falls back to the in-memory SMTP
    cache populated at startup so that alerts survive .env deletion.
    """
    global _cached_smtp
    try:
        # Attempt to reload live config; fall back to cached copy on failure.
        cfg_host = cfg_port = cfg_user = cfg_pass = cfg_from = None
        try:
            if "config" in sys.modules:
                import config as _cfg
                importlib.reload(_cfg)
            else:
                import config as _cfg  # type: ignore[no-redef]
            cfg_host  = _cfg.SMTP_HOST
            cfg_port  = _cfg.SMTP_PORT
            cfg_user  = _cfg.SMTP_USER
            cfg_pass  = _cfg.SMTP_PASS
            cfg_from  = _cfg.SMTP_FROM
            # Keep cache current while .env is healthy.
            _cached_smtp = {
                "host": cfg_host, "port": cfg_port,
                "user": cfg_user, "pass": cfg_pass, "from": cfg_from,
            }
        except Exception:
            if _cached_smtp:
                cfg_host  = _cached_smtp["host"]
                cfg_port  = _cached_smtp["port"]
                cfg_user  = _cached_smtp["user"]
                cfg_pass  = _cached_smtp["pass"]
                cfg_from  = _cached_smtp["from"]
            else:
                return  # no credentials at all; cannot send

        # Use the original SMTP_TO stored in the keychain at install time so that
        # a redirected .env cannot silently redirect watchdog alerts.
        try:
            from pin_auth import get_original_smtp_to as _get_orig
            _orig = _get_orig()
            recipients = [_orig] if _orig else None
        except Exception:
            recipients = None

        if recipients is None:
            # Fall back to live cfg or cached copy of SMTP_TO.
            try:
                recipients = _cfg.SMTP_TO  # type: ignore[possibly-undefined]
            except Exception:
                recipients = [cfg_from] if cfg_from else []

        if not recipients:
            return

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = cfg_from
        msg["To"]      = ", ".join(recipients)
        msg.attach(MIMEText(body_text, "plain", "utf-8"))

        ctx = ssl.create_default_context()
        if cfg_port == 465:
            with smtplib.SMTP_SSL(cfg_host, cfg_port, context=ctx, timeout=15) as s:
                s.login(cfg_user, cfg_pass)
                s.send_message(msg)
        else:
            with smtplib.SMTP(cfg_host, cfg_port, timeout=15) as s:
                s.ehlo()
                s.starttls(context=ctx)
                s.ehlo()
                s.login(cfg_user, cfg_pass)
                s.send_message(msg)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Service health checks
# ---------------------------------------------------------------------------

def _is_running_macos(label: str) -> bool:
    """Return True if launchd reports the given label has a running PID."""
    try:
        r = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, text=True, timeout=10,
        )
        for line in r.stdout.splitlines():
            cols = line.split("\t")
            if len(cols) >= 3 and cols[2].strip() == label:
                return cols[0].strip() != "-"
    except Exception:
        pass
    return False


def _is_running_windows(task_name: str) -> bool:
    """Return True if the Windows scheduled task is Running or Ready."""
    try:
        r = subprocess.run(
            ["schtasks", "/query", "/tn", task_name, "/fo", "CSV", "/nh"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
        if r.returncode != 0:
            return False
        rows = list(csv.reader(io.StringIO(r.stdout.strip())))
        if rows and len(rows[0]) >= 3:
            return rows[0][2].strip().lower() in ("running", "ready")
    except Exception:
        pass
    return False


def _stopped_services() -> set:
    """Return the set of service display-names that are currently not running."""
    stopped: set = set()
    if sys.platform == "darwin":
        if not _is_running_macos("com.vigil.tracker"):
            stopped.add("Vigil Tracker")
        if not _is_running_macos("com.vigil.summarizer"):
            stopped.add("Vigil Summarizer")
    elif sys.platform == "win32":
        if not _is_running_windows("Vigil Tracker"):
            stopped.add("Vigil Tracker")
        if not _is_running_windows("Vigil Summarizer"):
            stopped.add("Vigil Summarizer")
    return stopped


# ---------------------------------------------------------------------------
# Summarizer heartbeat check
# ---------------------------------------------------------------------------

def _check_summarizer_heartbeat() -> None:
    """Alert if the summarizer heartbeat goes stale or is deleted.

    The summarizer writes a Unix timestamp to SUMMARIZER_HEARTBEAT_FILE every
    ~60 s.  If that file is older than _SUMMARIZER_HEARTBEAT_STALE_SECS the
    summarizer was likely killed (including via SIGKILL) and did not restart.

    If the file is missing after having been previously seen, that is treated
    as deliberate deletion rather than "not yet installed".
    """
    global _summarizer_heartbeat_ever_seen
    if not SUMMARIZER_HEARTBEAT_FILE.exists():
        if _summarizer_heartbeat_ever_seen:
            _send_alert(
                subject=f"⚠️ Vigil — Summarizer Heartbeat File Deleted on {gethostname()}",
                body_text=(
                    f"The Vigil summarizer heartbeat file has been deleted on "
                    f"{gethostname()}. This file is written automatically every "
                    f"~60 seconds; its absence suggests a deliberate tamper attempt "
                    f"to disable SIGKILL detection.\n\n"
                    "If you did not authorise this, please follow up with the person "
                    "you are holding accountable immediately."
                ),
            )
        return  # file absent — either not installed yet, or just alerted above
    _summarizer_heartbeat_ever_seen = True

    try:
        last_beat = float(SUMMARIZER_HEARTBEAT_FILE.read_text().strip())
    except (ValueError, OSError):
        return  # malformed file; skip silently

    age = time.time() - last_beat
    if age <= _SUMMARIZER_HEARTBEAT_STALE_SECS:
        return  # heartbeat is fresh

    _send_alert(
        subject=f"⚠️ Vigil — Summarizer Heartbeat Stale on {gethostname()}",
        body_text=(
            f"The Vigil summarizer process on {gethostname()} has not updated "
            f"its heartbeat for over 2 minutes. It may have been forcibly killed "
            f"(e.g. via kill -9) and did not restart automatically.\n\n"
            "If you did not authorise this, please follow up with the person "
            "you are holding accountable immediately."
        ),
    )


# ---------------------------------------------------------------------------
# .env file existence check
# ---------------------------------------------------------------------------

def _check_env_file() -> None:
    """Alert if the .env configuration file is deleted after having been present.

    Without .env, SMTP credentials cannot be loaded and all future alert emails
    will silently fail.  Detecting deletion early (while the SMTP cache is still
    warm in memory) gives the partner the best chance of receiving a notification.
    """
    global _env_ever_seen
    if _ENV_FILE.exists():
        _env_ever_seen = True
        return
    if _env_ever_seen:
        _send_alert(
            subject=f"⚠️ Vigil — Configuration File Deleted on {gethostname()}",
            body_text=(
                f"The Vigil .env configuration file has been deleted on "
                f"{gethostname()}. Without this file, Vigil cannot send future "
                f"alert emails. This may be a deliberate attempt to silence "
                f"all monitoring notifications.\n\n"
                "If you did not authorise this, please follow up with the person "
                "you are holding accountable immediately and restore the .env file."
            ),
        )

# ---------------------------------------------------------------------------
# Check loop
# ---------------------------------------------------------------------------

def _run_check() -> None:
    global _last_stopped
    # Always check for .env deletion and summarizer heartbeat on every loop.
    _check_env_file()
    try:
        stopped = _stopped_services()
    except Exception:
        _check_summarizer_heartbeat()
        return

    newly_stopped = stopped - _last_stopped

    if not newly_stopped:
        _last_stopped = stopped  # record any services that have recovered
        _check_summarizer_heartbeat()
        return

    # During the startup grace window services may still be initialising.
    # Don't alert yet, but also don't update _last_stopped — so that services
    # still down when grace expires are still detected as "newly stopped".
    if time.monotonic() - _started_at < _STARTUP_GRACE:
        return

    _last_stopped = stopped
    names = " and ".join(sorted(newly_stopped))
    _send_alert(
        subject=f"⚠️ Vigil — Service Stopped: {names}",
        body_text=(
            f"The following Vigil monitoring service(s) are no longer running "
            f"on {gethostname()}: {names}.\n\n"
            "If you did not intentionally stop these services, please follow up "
            "with the person you are holding accountable."
        ),
    )
    _check_summarizer_heartbeat()


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

def _shutdown_handler(signum, frame) -> None:
    """Fires on SIGTERM/SIGINT.

    Triggered by launchctl bootout, the Login Items toggle, Stop-ScheduledTask,
    or a direct kill.  Sends a partner alert unless the shutdown was initiated
    by a PIN-verified vigil command (graceful sentinel file present).
    """
    if _GRACEFUL_SENTINEL.exists():
        _GRACEFUL_SENTINEL.unlink(missing_ok=True)
        sys.exit(0)

    _send_alert(
        subject=f"⚠️ Vigil — Monitoring Watchdog Disabled on {gethostname()}",
        body_text=(
            f"The Vigil monitoring watchdog on {gethostname()} has been stopped.\n\n"
            "This usually means someone used System Settings, Terminal, or Task "
            "Scheduler to disable Vigil services without going through the normal "
            "`vigil uninstall` command.\n\n"
            "If you did not authorise this, please follow up with the person you "
            "are holding accountable immediately."
        ),
    )
    sys.exit(0)


signal.signal(signal.SIGTERM, _shutdown_handler)
signal.signal(signal.SIGINT,  _shutdown_handler)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    # Remove any stale sentinel left by an interrupted install/uninstall run.
    _GRACEFUL_SENTINEL.unlink(missing_ok=True)
    # Warm the SMTP cache before the check loop starts so that alerts can still
    # be sent even if .env is deleted after this point.
    _load_smtp_cache()
    while True:
        _run_check()
        # Refresh heartbeat so summarizer.py can detect a stale/dead watchdog.
        try:
            HEARTBEAT_FILE.write_text(str(time.time()))
        except Exception:
            pass
        time.sleep(_CHECK_INTERVAL)
