"""
alerter.py — adult/porn site detection and periodic log-scan alerting.

Detection is entirely offline:
  1. Domain blocklist  — exact match against data/domains.txt
  2. Keyword matching  — adult-related substrings found in the domain name

Detection (check_url) is called by tracker.py on every URL change and returns
a bool — no I/O occurs at detection time.

When a flagged session is finalised, tracker.py appends a [FLAGGED_CONTENT] tag
to the activity log entry.  scan_and_alert() is called by the tracker on a
configurable interval (ALERT_SCAN_INTERVAL_MINUTES) and sends one consolidated
email for all new [FLAGGED_CONTENT] entries found since the last scan.

A cursor file (alerter_cursor.txt) persists the last-scan timestamp across
restarts so no entries are double-processed and no entries are missed.
"""

import logging
import re
import smtplib
import socket
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Optional, Tuple

import config
from platform_common import get_app_dirs

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

_, _log_dir = get_app_dirs()
_log_dir.mkdir(parents=True, exist_ok=True)
_handler = RotatingFileHandler(
    _log_dir / "alerter.log",
    maxBytes=2 * 1024 * 1024,
    backupCount=2,
    encoding="utf-8",
)
_handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_logger = logging.getLogger("alerter")
_logger.setLevel(logging.INFO)
if not _logger.handlers:
    _logger.addHandler(_handler)

# Friendly device name shown in alert emails — resolved once at import time.
_DEVICE_NAME: str = socket.gethostname()

# Pre-compiled regex: extracts the hostname from a full URL that includes the protocol.
_HOSTNAME_RE = re.compile(r'https?://(?:[^@/]+@)?([^/:?\s]+)')

# Chrome strips https:// from the address bar value it exposes via UIA, so the
# active label becomes "[BrowserName] host/path" with no protocol prefix.
_BROWSER_BARE_RE = re.compile(r'^\[.+?\]\s+([^/?#:\s]+)')

# Pre-compiled pattern — splits a domain on '.' and '-' for keyword matching.
_DOMAIN_SPLIT = re.compile(r'[.-]')

# ---------------------------------------------------------------------------
# Flagged content tag
# ---------------------------------------------------------------------------

FLAGGED_TAG = "[FLAGGED_CONTENT]"

# Matches a complete log line that carries the flagged tag.
# Captures group 1 = timestamp string, group 2 = label (domain or browser label).
_FLAGGED_LINE_RE = re.compile(
    r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (.+?) \[duration: \d+s\] \[FLAGGED_CONTENT\]'
)

# ---------------------------------------------------------------------------
# Blocklist — loaded once at import time into a set for O(1) lookup
# ---------------------------------------------------------------------------

_BLOCKLIST_PATH = Path(__file__).parent / "data" / "domains.txt"


def _load_blocklist() -> frozenset:
    """Read data/domains.txt and return a frozenset of bare domains."""
    try:
        domains = set()
        for line in _BLOCKLIST_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip().lower()
            if line and not line.startswith("#"):
                domains.add(line)
        return frozenset(domains)
    except Exception:
        return frozenset()


_BLOCKLIST: frozenset = _load_blocklist()

# ---------------------------------------------------------------------------
# Keyword matching
# ---------------------------------------------------------------------------

_ADULT_KEYWORDS: frozenset = frozenset({
    "porn",
    "xxx",
    "nude",
    "naked",
    "hentai",
    "nsfw",
    "escort",
    "fetish",
    "erotic",
    "camgirl",
    "onlyfans",
    "stripper",
    "sexcam",
    "livesex",
    "adultcam",
    "hotlive",
    "dirtychat",
})

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def is_adult_domain(domain: str) -> bool:
    """Return True if *domain* matches the blocklist or an adult keyword."""
    if domain in _BLOCKLIST:
        return True
    parts = _DOMAIN_SPLIT.split(domain)
    return bool(_ADULT_KEYWORDS.intersection(parts))


def check_url(label: str) -> bool:
    """Check *label* for adult content and return True if detected.

    Extracts the hostname from the raw URL or '[Browser] URL' format and
    runs it through the blocklist and keyword matcher.  Never raises.
    No I/O or email is performed here — detection only.
    """
    if not config.ALERT_ENABLED:
        return False

    try:
        m = _HOSTNAME_RE.search(label)
        if m:
            raw = m.group(1)
        else:
            bm = _BROWSER_BARE_RE.match(label)
            if not bm:
                return False
            raw = bm.group(1)

        domain = raw.lower()
        if domain.startswith("www."):
            domain = domain[4:]

        return is_adult_domain(domain)

    except Exception as exc:
        _logger.error("Unexpected error in check_url(%r): %s: %s", label, type(exc).__name__, exc)
        return False


# ---------------------------------------------------------------------------
# Cursor — persists last-scan timestamp to avoid double-processing
# ---------------------------------------------------------------------------

def _read_cursor(cursor_file: Path) -> Optional[datetime]:
    """Return the datetime stored in *cursor_file*, or None if absent/unreadable."""
    try:
        return datetime.fromisoformat(cursor_file.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _write_cursor(cursor_file: Path, dt: datetime) -> None:
    """Write *dt* as an ISO-format string to *cursor_file*."""
    try:
        cursor_file.write_text(dt.isoformat(), encoding="utf-8")
    except Exception as exc:
        _logger.error("Failed to write cursor file %s: %s", cursor_file, exc)


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def _do_send_smtp(subject: str, html_body: str, plain_text: str) -> None:
    """Low-level SMTP send."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Vigil <{config.SMTP_FROM}>"
    msg["To"] = ", ".join(config.SMTP_TO)
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    if config.SMTP_PORT == 465:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=ctx, timeout=30) as smtp:
            smtp.login(config.SMTP_USER, config.SMTP_PASS)
            smtp.sendmail(config.SMTP_FROM, config.SMTP_TO, msg.as_string())
    else:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(config.SMTP_USER, config.SMTP_PASS)
            smtp.sendmail(config.SMTP_FROM, config.SMTP_TO, msg.as_string())


def _send_flagged_email(visits: List[Tuple[str, str]]) -> None:
    """Send a consolidated alert email for one or more flagged visits.

    *visits* is a list of (label, timestamp) tuples from the activity log.
    Called from a daemon thread by scan_and_alert so SMTP latency never
    blocks the main tracking loop.
    """
    count = len(visits)
    subject = f"⚠️ Vigil Alert — {count} flagged site{'s' if count > 1 else ''} visited on {_DEVICE_NAME}"

    rows_html = "".join(
        f"<tr>"
        f"<td style='padding:4px 12px 4px 0;color:#555;'>{ts}</td>"
        f"<td style='padding:4px 0;'><strong>{label}</strong></td>"
        f"</tr>"
        for label, ts in visits
    )
    html_body = f"""
    <p style="font-family:sans-serif;font-size:15px;">
        {'An adult or pornographic website was' if count == 1 else 'Adult or pornographic websites were'}
        detected in the active browser tab on <strong>{_DEVICE_NAME}</strong>.
    </p>
    <table style="font-family:monospace;font-size:14px;border-collapse:collapse;">
        <tr>
            <th style="padding:4px 12px 4px 0;color:#555;text-align:left;">Time</th>
            <th style="padding:4px 0;text-align:left;">Site</th>
        </tr>
        {rows_html}
    </table>
    <p style="font-family:sans-serif;font-size:13px;color:#888;margin-top:20px;">
        Sent by Vigil at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.
    </p>
    """
    plain_rows = "\n".join(f"  {ts}  {label}" for label, ts in visits)
    plain_text = (
        f"Vigil Alert — flagged content detected on {_DEVICE_NAME}\n\n"
        f"Time                 Site\n"
        f"{plain_rows}\n"
    )

    try:
        _do_send_smtp(subject, html_body, plain_text)
        _logger.info("Alert email sent for %d flagged visit(s).", count)
    except Exception as exc:
        _logger.error("Alert email failed: %s: %s", type(exc).__name__, exc)


# ---------------------------------------------------------------------------
# Public scan entry point
# ---------------------------------------------------------------------------

def scan_and_alert(activity_log: Path, cursor_file: Path) -> None:
    """Scan *activity_log* for new [FLAGGED_CONTENT] entries and send an alert.

    Reads the last-scan timestamp from *cursor_file* and processes only log
    lines whose timestamp is strictly after that value.  Always updates the
    cursor to datetime.now() after running, so the next scan sees only newer
    entries.  Sends at most one consolidated email per call.  Never raises.
    """
    if not config.ALERT_ENABLED:
        return

    try:
        cursor = _read_cursor(cursor_file)
        now = datetime.now()

        visits: List[Tuple[str, str]] = []

        if activity_log.exists():
            for line in activity_log.read_text(encoding="utf-8", errors="replace").splitlines():
                m = _FLAGGED_LINE_RE.match(line)
                if not m:
                    continue
                entry_time = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                if cursor is not None and entry_time <= cursor:
                    continue
                visits.append((m.group(2), m.group(1)))

        if visits and config.ALERT_EMAIL:
            try:
                _send_flagged_email(visits)
            except Exception as exc:
                _logger.error("Unexpected error in _send_flagged_email: %s: %s", type(exc).__name__, exc)
        elif visits:
            _logger.info("scan_and_alert: %d flagged visit(s) found but ALERT_EMAIL is disabled.", len(visits))

        _write_cursor(cursor_file, now)

    except Exception as exc:
        _logger.error("Unexpected error in scan_and_alert: %s: %s", type(exc).__name__, exc)

