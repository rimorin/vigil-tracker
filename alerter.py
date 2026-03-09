"""
alerter.py — real-time adult/porn site detection and alerting.

Called by tracker.py on every URL change.  Detection is entirely offline:
  1. Domain blocklist  — exact match against data/domains.txt
  2. Keyword matching  — adult-related substrings found in the domain name

When a match is found, one or both alert channels fire (configured via .env):
  • macOS banner notification  (ALERT_NOTIFICATION)
  • Alert email via SMTP        (ALERT_EMAIL)

A per-domain cooldown (ALERT_COOLDOWN_MINUTES) prevents alert spam.
All public functions are wrapped in broad try/except so alerter failures
never propagate to or crash the tracker daemon.
"""

import concurrent.futures
import re
import smtplib
import socket
import ssl
import subprocess
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict, FrozenSet, Optional

import config

# Friendly device name shown in alert emails — resolved once at import time.
def _get_device_name() -> str:
    try:
        result = subprocess.run(
            ["scutil", "--get", "ComputerName"],
            capture_output=True, text=True, timeout=5,
        )
        name = result.stdout.strip()
        if name:
            return name
    except Exception:
        pass
    return socket.gethostname()

# Pre-compiled regex: extracts the hostname from either "[Browser] https://host/path"
# or a raw "https://host/path" label in a single pass — avoids urllib.parse overhead.
# Handles user:pass@ credentials, strips path/query/port automatically.
_HOSTNAME_RE = re.compile(r'https?://(?:[^@/]+@)?([^/:?\s]+)')

# Pre-compiled pattern — splits a domain on '.' and '-' for keyword matching.
_DOMAIN_SPLIT = re.compile(r'[.\-]')

# Pre-computed cooldown window in seconds (avoids timedelta object construction
# on every call; read once at module import so config changes require restart).
_COOLDOWN_SECS: float = config.ALERT_COOLDOWN_MINUTES * 60

# Resolved once — used in alert emails so the recipient knows which machine fired.
_DEVICE_NAME: str = _get_device_name()

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
# Keyword matching — substrings that strongly indicate adult content
# ---------------------------------------------------------------------------

# frozenset enables O(1) set.intersection() instead of O(k) any() over tuple.
_ADULT_KEYWORDS: FrozenSet[str] = frozenset({
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
# Cooldown — in-memory per-domain deduplication
# ---------------------------------------------------------------------------

# Stores time.monotonic() floats — float subtraction is ~5x faster than
# datetime arithmetic + timedelta construction on every cooldown check.
_cooldown: Dict[str, float] = {}


def _is_on_cooldown(domain: str) -> bool:
    """Return True if this domain was alerted within the cooldown window."""
    last = _cooldown.get(domain)
    return last is not None and (time.monotonic() - last) < _COOLDOWN_SECS


def _record_alert(domain: str) -> None:
    _cooldown[domain] = time.monotonic()


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def is_adult_domain(domain: str) -> bool:
    """Return True if *domain* matches the blocklist or an adult keyword.

    Expects *domain* to already be lowercased and www-stripped (as provided
    by check_url).  When called directly, pass a normalised bare hostname.

    Blocklist lookup is O(1) via frozenset.  Keyword matching splits on '.'
    and '-' then uses set.intersection() — O(min(parts, keywords)).
    """
    if domain in _BLOCKLIST:
        return True
    parts = _DOMAIN_SPLIT.split(domain)
    return bool(_ADULT_KEYWORDS.intersection(parts))


# ---------------------------------------------------------------------------
# Alert channels
# ---------------------------------------------------------------------------

def _do_send_smtp(subject: str, html_body: str, plain_text: str) -> None:
    """Low-level SMTP send (mirrors summarizer.py pattern)."""
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


def _send_alert_email(domain: str, timestamp: str) -> None:
    """Send an adult-site alert email with a 60-second hard timeout."""
    subject = f"⚠️ Vigil Alert — Adult site visited: {domain}"
    html_body = f"""
    <p style="font-family:sans-serif;font-size:15px;">
        An adult or pornographic website was detected in your active browser tab.
    </p>
    <table style="font-family:monospace;font-size:14px;border-collapse:collapse;">
        <tr><td style="padding:4px 12px 4px 0;color:#555;">Site</td>
            <td style="padding:4px 0;"><strong>{domain}</strong></td></tr>
        <tr><td style="padding:4px 12px 4px 0;color:#555;">Device</td>
            <td style="padding:4px 0;">{_DEVICE_NAME}</td></tr>
        <tr><td style="padding:4px 12px 4px 0;color:#555;">Time</td>
            <td style="padding:4px 0;">{timestamp}</td></tr>
    </table>
    <p style="font-family:sans-serif;font-size:13px;color:#888;margin-top:20px;">
        Sent by Vigil on {timestamp}.
    </p>
    """
    plain_text = (
        f"Vigil Alert\n"
        f"Adult site visited: {domain}\n"
        f"Device: {_DEVICE_NAME}\n"
        f"Time: {timestamp}\n"
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_do_send_smtp, subject, html_body, plain_text)
        try:
            future.result(timeout=60)
        except concurrent.futures.TimeoutError:
            pass  # Don't block or crash the tracker if SMTP hangs


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def check_url(label: str) -> None:
    """Check *label* (raw URL or '[Browser] URL' format) for adult content.

    Uses a pre-compiled regex to extract the hostname in a single pass,
    avoiding urllib.parse overhead on every tracker poll.  Never raises.
    """
    if not config.ALERT_ENABLED:
        return

    try:
        m = _HOSTNAME_RE.search(label)
        if not m:
            return
        domain = m.group(1).lower()
        if domain.startswith("www."):
            domain = domain[4:]

        if not is_adult_domain(domain):
            return

        if _is_on_cooldown(domain):
            return

        _record_alert(domain)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if config.ALERT_EMAIL:
            try:
                _send_alert_email(domain, timestamp)
            except Exception:
                pass

    except Exception:
        pass
