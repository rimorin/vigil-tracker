"""
summarizer.py — long-running daemon that reads daily web activity logs,
summarises them with OpenAI, and sends an HTML digest email via MailerSend.

Schedule is controlled entirely via environment variables (see config.py):
  SUMMARY_SCHEDULE        = hourly | daily | weekly | monthly
  SUMMARY_SCHEDULE_HOUR   = 0-23   (used by daily / weekly / monthly)
  SUMMARY_SCHEDULE_MINUTE = 0-59   (used by all)
  SUMMARY_SCHEDULE_WEEKDAY= mon-sun (used by weekly)
  SUMMARY_SCHEDULE_DAY    = 1-28   (used by monthly)

Run directly or managed by launchd (KeepAlive=true).
"""

import hashlib
import logging
import re
import signal
import smtplib
import ssl
import subprocess
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from logging.handlers import RotatingFileHandler
from datetime import date, datetime
from pathlib import Path
from socket import gethostname
from typing import List, Optional, Tuple

from collections import defaultdict
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from tzlocal import get_localzone_name
from openai import OpenAI

import config

BASE_DIR = Path(__file__).parent.resolve()
ACTIVITY_LOG = BASE_DIR / "detailed_activity_log.txt"
INTEGRITY_FILE = BASE_DIR / "detailed_activity_log.txt.sha256"
SENTINEL_FILE = BASE_DIR / "last_summarized_date.txt"
SUMMARIZER_LOG = BASE_DIR / "summarizer_daemon.log"

# gpt-4o-mini context window: 128k tokens. Each log line ≈ 20 tokens.
# Cap at 3000 lines (~60k tokens) to leave room for prompt + response.
MAX_LOG_LINES = 3000
MAX_RESPONSE_TOKENS = 1500

_scheduler: Optional[BlockingScheduler] = None
_openai_client: Optional[OpenAI] = None

# Rotating file logger — 5 MB per file, keep 3 backups
_handler = RotatingFileHandler(SUMMARIZER_LOG, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
_handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_logger = logging.getLogger("summarizer")
_logger.setLevel(logging.INFO)
_logger.addHandler(_handler)


def _get_openai_client() -> OpenAI:
    """Return a cached OpenAI client (created once, reused across jobs)."""
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _openai_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log(message: str):
    _logger.info(message)


def _handle_signal(signum, frame):
    _log("Received shutdown signal — stopping scheduler.")
    if _scheduler:
        _scheduler.shutdown(wait=False)
    sys.exit(0)


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def _already_sent_today() -> bool:
    today = str(date.today())
    if SENTINEL_FILE.exists():
        return SENTINEL_FILE.read_text().strip() == today
    return False


def _mark_sent_today():
    SENTINEL_FILE.write_text(str(date.today()))


def _read_today_entries() -> List[str]:
    today_prefix = f"[{date.today()}"
    entries = []
    if not ACTIVITY_LOG.exists():
        return entries
    with open(ACTIVITY_LOG) as f:
        for line in f:
            line = line.strip()
            if line.startswith(today_prefix):
                entries.append(line)
    return entries


# ---------------------------------------------------------------------------
# AI summarisation
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a personal productivity assistant.
Analyse the user's web browsing log for today and produce a concise HTML summary.
Structure your response as valid HTML (no markdown, no code fences) with these sections:

<h2>📊 Overview</h2>
<h2>🌐 Top Domains</h2>
<h2>🏷️ Categories</h2>
<h2>⏱️ Timeline Highlights</h2>
<h2>🚨 Concern Flags</h2>

Guidelines:
- Each log line has the format: [HH:MM:SS] [Browser] domain.com [duration: Xs]
- Overview: total visits, total active browsing time (sum all [duration: Xs] fields), main themes.
- Top Domains: list the top 5 domains by TOTAL TIME SPENT (sum [duration: Xs] per domain),
  most time first; include the formatted duration next to each domain.
- Categories: group sites into buckets like Productivity, Social Media, News, Entertainment, Research, Other.
- Timeline Highlights: pick 3-5 interesting or notable moments from the day.
- Concern Flags: explicitly check for visits to adult content, pornography, gambling, self-harm, or
  substance abuse sites. If any are found, list each domain with total time spent
  using inline style color:#c0392b (red). If none found, write a single line: "✅ No flagged content detected."
  Do not soften, omit, or euphemise findings — be direct and factual.
- Keep the tone neutral and factual for all other sections.
- Use <ul>/<li> for lists and keep the HTML clean and email-safe (inline styles if needed).
"""


def _strip_to_domain(entries: List[str]) -> List[str]:
    """Replace full URLs with domain names only before sending to OpenAI.

    Keeps timestamps, browser tags, and [duration: Xs] suffixes intact so the
    AI can still produce timeline, frequency, and duration analysis — but paths,
    query strings, and page slugs never leave the machine.
    """
    return [re.sub(r'https?://([^/\s]+)[^\s]*', r'\1', entry) for entry in entries]


# Matches duration log entries:  [timestamp] [Browser] https://... [duration: Xs]
_DURATION_ENTRY_RE = re.compile(
    r'\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \[.+?\] https?://[^\s]+ \[duration: (\d+)s\]'
)


def parse_duration_entries(entries: List[str]) -> dict:
    """Return {domain: total_seconds} accumulated from [duration: Xs] log lines."""
    domain_times: dict = defaultdict(int)
    for entry in entries:
        m = _DURATION_ENTRY_RE.search(entry)
        if not m:
            continue
        seconds = int(m.group(1))
        url_match = re.search(r'https?://([^/\s]+)', entry)
        if url_match:
            domain_times[url_match.group(1)] += seconds
    return dict(domain_times)


def _format_duration(seconds: int) -> str:
    """Format a number of seconds as a human-readable string (e.g. '2h 15m', '45m', '30s')."""
    if seconds >= 3600:
        h, rem = divmod(seconds, 3600)
        m = rem // 60
        return f"{h}h {m}m" if m else f"{h}h"
    if seconds >= 60:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _build_time_per_domain_html(domain_times: dict) -> str:
    """Return an HTML section listing the top 5 domains by time spent."""
    if not domain_times:
        return ""
    top = sorted(domain_times.items(), key=lambda x: x[1], reverse=True)[:5]
    total = sum(domain_times.values())
    rows = "".join(
        f"<li><strong>{domain}</strong> — {_format_duration(secs)}</li>"
        for domain, secs in top
    )
    return (
        f'<h2>⏱️ Time Per Domain</h2>'
        f'<p style="color:#777; font-size:0.9em;">Total active browsing time: {_format_duration(total)}</p>'
        f"<ul>{rows}</ul>"
    )


def _summarise_with_openai(entries: List[str], domain_times: dict) -> str:
    # Truncate if the log is unusually large to stay within context limits
    if len(entries) > MAX_LOG_LINES:
        _log(f"Log has {len(entries)} entries — truncating to last {MAX_LOG_LINES} for summarisation.")
        entries = entries[-MAX_LOG_LINES:]

    client = _get_openai_client()
    log_text = "\n".join(_strip_to_domain(entries))

    # Provide pre-computed domain totals so the AI doesn't have to aggregate manually
    if domain_times:
        top = sorted(domain_times.items(), key=lambda x: x[1], reverse=True)[:10]
        time_context = "Pre-computed time per domain: " + ", ".join(
            f"{d} ({_format_duration(s)})" for d, s in top
        )
        user_content = f"Here is today's browsing log:\n\n{log_text}\n\n{time_context}"
    else:
        user_content = f"Here is today's browsing log:\n\n{log_text}"

    response = client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_content},
        ],
        temperature=0.3,
        max_tokens=MAX_RESPONSE_TOKENS,
    )
    return response.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------

def _wrap_email_html(heading: str, body: str, footer: str) -> str:
    """Return a complete HTML email document with a consistent layout."""
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: Arial, sans-serif; max-width: 700px; margin: auto; color: #333; padding: 16px;">
  <h1 style="color: #2c3e50;">{heading}</h1>
  {body}
  <hr/>
  <p style="color: #aaa; font-size: 0.8em;">{footer}</p>
</body>
</html>"""


def _html_to_text(html: str) -> str:
    """Strip HTML tags to produce a plain-text fallback."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _send_smtp(subject: str, html_body: str, plain_text: str) -> None:
    """Send an email via SMTP using credentials from config."""
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
            smtp.starttls()
            smtp.ehlo()
            smtp.login(config.SMTP_USER, config.SMTP_PASS)
            smtp.sendmail(config.SMTP_FROM, config.SMTP_TO, msg.as_string())


# ---------------------------------------------------------------------------
# Integrity check & watchdog
# ---------------------------------------------------------------------------

def _send_alert_email(subject: str, html_body: str, plain_text: str):
    """Send a plain alert email (no digest heading, no AI involved)."""
    _send_smtp(
        subject=subject,
        html_body=_wrap_email_html(
            heading="🌐 Vigil",
            body=html_body,
            footer="Sent by Vigil.",
        ),
        plain_text=plain_text,
    )


def _verify_log_integrity() -> bool:
    """Return False if the activity log SHA-256 doesn't match the stored sidecar hash."""
    if not INTEGRITY_FILE.exists() or not ACTIVITY_LOG.exists():
        return True  # nothing to compare yet
    expected = INTEGRITY_FILE.read_text().strip()
    actual = hashlib.sha256(ACTIVITY_LOG.read_bytes()).hexdigest()
    return actual == expected


_tracker_was_running: bool = True  # module-level state for edge-transition alerting


def _check_tracker_alive():
    """Alert once when the tracker service transitions from running to stopped."""
    global _tracker_was_running
    result = subprocess.run(
        ['launchctl', 'list', 'com.vigil.tracker'],
        capture_output=True,
    )
    running = result.returncode == 0

    if not running and _tracker_was_running:
        _log("⚠️ Tracker service not running — sending alert.")
        try:
            _send_alert_email(
                subject="⚠️ Vigil — Monitoring Stopped",
                html_body="""
                <h2>⚠️ Monitoring Service Stopped</h2>
                <p>The browser tracking service has stopped. No browsing activity is being recorded.</p>
                <p>This may mean it was manually disabled.</p>
                <p style="color:#888; font-size:0.9em;">
                  To restart: run <code>bash install.sh</code> in the project folder.
                </p>
                """,
                plain_text=(
                    "⚠️ Monitoring Service Stopped\n\n"
                    "The browser tracking service has stopped. No browsing activity is being recorded.\n"
                    "This may mean it was manually disabled.\n\n"
                    "To restart: run bash install.sh in the project folder."
                ),
            )
        except Exception as exc:
            _log(f"Failed to send watchdog alert: {exc}")

    if running and not _tracker_was_running:
        _log("Tracker service resumed.")

    _tracker_was_running = running


# ---------------------------------------------------------------------------
# Email via MailerSend REST API
# ---------------------------------------------------------------------------

def _send_email(subject: str, html_body: str):
    today_str = date.today().strftime("%B %d, %Y")
    heading = "🔦 Vigil Digest"
    preamble = f'<p style="color: #777; font-size: 0.9em;">Your activity digest for {today_str}</p><hr/>'
    _send_smtp(
        subject=subject,
        html_body=_wrap_email_html(
            heading=heading,
            body=preamble + html_body,
            footer="Sent by Vigil.",
        ),
        plain_text=f"{heading}\nYour activity digest for {today_str}\n\n{_html_to_text(html_body)}",
    )


# ---------------------------------------------------------------------------
# Scheduled job
# ---------------------------------------------------------------------------

def run_summary():
    """Called by APScheduler on every trigger. Sends digest if not already sent."""
    _log(f"Summary job triggered (schedule: {config.SUMMARY_SCHEDULE}).")

    if not _verify_log_integrity():
        _log("⚠️ Log integrity check failed — possible tampering detected.")
        try:
            _send_alert_email(
                subject="⚠️ Vigil — Log May Have Been Tampered With",
                html_body="""
                <h2>⚠️ Log Integrity Check Failed</h2>
                <p>The activity log file does not match its stored checksum.</p>
                <p>This may indicate that log entries were edited or deleted.</p>
                <p style="color:#888; font-size:0.9em;">The digest for today has been skipped.</p>
                """,
                plain_text=(
                    "⚠️ Log Integrity Check Failed\n\n"
                    "The activity log file does not match its stored checksum.\n"
                    "This may indicate that log entries were edited or deleted.\n\n"
                    "The digest for today has been skipped."
                ),
            )
        except Exception as exc:
            _log(f"Failed to send tamper alert: {exc}")
        return

    if config.SUMMARY_SCHEDULE == "daily" and _already_sent_today():
        _log("Digest already sent today — skipping.")
        return

    entries = _read_today_entries()
    if not entries:
        _log("No activity logged — skipping email.")
        return

    domain_times = parse_duration_entries(entries)
    _log(f"Found {len(entries)} entries. Calling OpenAI ({config.OPENAI_MODEL})...")
    today_str = date.today().strftime("%B %d, %Y")
    try:
        summary_html = _summarise_with_openai(entries, domain_times)
    except Exception as exc:
        _log(f"OpenAI error: {exc}")
        try:
            _send_alert_email(
                subject=f"⚠️ Vigil Digest Failed — {today_str}",
                html_body=f"""
                <h2>⚠️ Digest Could Not Be Generated</h2>
                <p>The AI summarisation step failed for <strong>{today_str}</strong>.</p>
                <p><strong>Error:</strong> <code>{exc}</code></p>
                <p style="color:#888; font-size:0.9em;">
                  Your browsing activity was still logged normally.
                  The digest will be attempted again on the next scheduled run.
                </p>
                """,
                plain_text=(
                    f"⚠️ Digest Could Not Be Generated\n\n"
                    f"The AI summarisation step failed for {today_str}.\n"
                    f"Error: {exc}\n\n"
                    f"Your browsing activity was still logged normally.\n"
                    f"The digest will be attempted again on the next scheduled run."
                ),
            )
        except Exception as alert_exc:
            _log(f"Failed to send OpenAI failure alert: {alert_exc}")
        return

    subject = f"Your Vigil Digest — {today_str}"

    # Prepend a factual, pre-computed time section before the AI narrative
    time_section = _build_time_per_domain_html(domain_times)
    full_body = time_section + summary_html if time_section else summary_html

    _log("Sending email via MailerSend...")
    try:
        _send_email(subject, full_body)
    except Exception as exc:
        _log(f"Email send error: {exc}")
        return

    _mark_sent_today()
    _log("Digest sent successfully.")


# ---------------------------------------------------------------------------
# Schedule builder
# ---------------------------------------------------------------------------

def _build_trigger() -> Tuple[object, str]:
    """
    Translate SUMMARY_SCHEDULE + supporting env vars into an APScheduler
    CronTrigger.

    hourly  → every hour at :MM
    daily   → every day at HH:MM
    weekly  → every WEEKDAY at HH:MM
    monthly → day DAY of each month at HH:MM
    """
    h = config.SUMMARY_SCHEDULE_HOUR
    m = config.SUMMARY_SCHEDULE_MINUTE

    if config.SUMMARY_SCHEDULE == "interval":
        mins = config.SUMMARY_SCHEDULE_INTERVAL_MINUTES
        if mins < 1:
            raise ValueError("SUMMARY_SCHEDULE_INTERVAL_MINUTES must be >= 1.")
        trigger = IntervalTrigger(minutes=mins)
        desc = f"every {mins} minute(s)"

    elif config.SUMMARY_SCHEDULE == "hourly":
        trigger = CronTrigger(minute=m)
        desc = f"every hour at :{m:02d}"

    elif config.SUMMARY_SCHEDULE == "daily":
        trigger = CronTrigger(hour=h, minute=m)
        desc = f"daily at {h:02d}:{m:02d}"

    elif config.SUMMARY_SCHEDULE == "weekly":
        wd = config.SUMMARY_SCHEDULE_WEEKDAY
        trigger = CronTrigger(day_of_week=wd, hour=h, minute=m)
        desc = f"weekly on {wd} at {h:02d}:{m:02d}"

    elif config.SUMMARY_SCHEDULE == "monthly":
        d = config.SUMMARY_SCHEDULE_DAY
        trigger = CronTrigger(day=d, hour=h, minute=m)
        desc = f"monthly on day {d} at {h:02d}:{m:02d}"

    else:
        raise ValueError(f"Unknown schedule: {config.SUMMARY_SCHEDULE}")

    return trigger, desc


# ---------------------------------------------------------------------------
# Confirmation email (sent once on first install)
# ---------------------------------------------------------------------------

def send_confirmation_email():
    """Send a one-time confirmation email after successful installation."""
    h = config.SUMMARY_SCHEDULE_HOUR
    m = config.SUMMARY_SCHEDULE_MINUTE
    schedule = config.SUMMARY_SCHEDULE

    schedule_descriptions = {
        "interval": f"every {config.SUMMARY_SCHEDULE_INTERVAL_MINUTES} minute(s)",
        "hourly":  f"every hour at :{m:02d}",
        "daily":   f"daily at {h:02d}:{m:02d}",
        "weekly":  f"every {config.SUMMARY_SCHEDULE_WEEKDAY.capitalize()} at {h:02d}:{m:02d}",
        "monthly": f"on day {config.SUMMARY_SCHEDULE_DAY} of each month at {h:02d}:{m:02d}",
    }
    schedule_desc = schedule_descriptions.get(schedule, schedule)
    installed_at = datetime.now().strftime("%B %d, %Y at %H:%M")
    hostname = gethostname()

    td = "padding:8px 12px; border:1px solid #e0e0e0;"
    th = f"{td} font-weight:bold; background:#f5f5f5;"

    html_body = f"""
    <h2>✅ Installation Successful</h2>
    <p>Your <strong>Vigil</strong> is now active on <strong>{hostname}</strong>.</p>

    <h2>⚙️ Configuration</h2>
    <table style="border-collapse: collapse; width: 100%;">
      <tr>
        <td style="{th}">Installed at</td>
        <td style="{td}">{installed_at}</td>
      </tr>
      <tr>
        <td style="{th}">Digest schedule</td>
        <td style="{td}">{schedule_desc}</td>
      </tr>
      <tr>
        <td style="{th}">AI engine</td>
        <td style="{td}">{config.OPENAI_MODEL}</td>
      </tr>
      <tr>
        <td style="{th}">Sending to</td>
        <td style="{td}">{"<br>".join(config.SMTP_TO)}</td>
      </tr>
    </table>

    <h2>🚦 What happens next?</h2>
    <ul>
      <li>The tracker is running in the background and logging your browser activity.</li>
      <li>You will receive your first digest <strong>{schedule_desc}</strong>.</li>
      <li>To uninstall at any time, run <code>bash uninstall.sh</code> in the project folder.</li>
    </ul>

    <p style="color:#888; font-size:0.9em;">
      Both background services will restart automatically after a reboot or crash.
    </p>
    """

    plain_text = (
        f"✅ Installation Successful\n"
        f"Vigil is now active on {hostname}.\n\n"
        f"Installed at:     {installed_at}\n"
        f"Digest schedule:  {schedule_desc}\n"
        f"AI engine:        {config.OPENAI_MODEL}\n"
        f"Sending to:       {', '.join(config.SMTP_TO)}\n\n"
        f"What happens next?\n"
        f"- The tracker is running in the background and logging your browser activity.\n"
        f"- You will receive your first digest {schedule_desc}.\n"
        f"- To uninstall at any time, run: bash uninstall.sh\n\n"
        f"Both background services will restart automatically after a reboot or crash."
    )

    _send_smtp(
        subject="✅ Vigil — Installation Confirmed",
        html_body=_wrap_email_html(
            heading="🌐 Vigil",
            body=html_body,
            footer="Sent by Vigil.",
        ),
        plain_text=plain_text,
    )


def send_uninstall_email():
    """Send a one-time notification email when the tracker is being uninstalled."""
    uninstalled_at = datetime.now().strftime("%B %d, %Y at %H:%M")
    hostname = gethostname()

    html_body = f"""
    <h2>🛑 Tracker Uninstalled</h2>
    <p>
      The <strong>Vigil</strong> has been removed from
      <strong>{hostname}</strong> on {uninstalled_at}.
    </p>

    <h2>ℹ️ What was removed</h2>
    <ul>
      <li>The browser monitoring service and email digest service have been stopped.</li>
      <li>Browser activity is no longer being monitored.</li>
      <li>No further digest emails will be sent.</li>
    </ul>

    <p style="color:#888; font-size:0.9em;">
      To reinstall at any time, run <code>bash install.sh</code> in the project folder.
    </p>
    """

    plain_text = (
        f"🛑 Tracker Uninstalled\n"
        f"Vigil has been removed from {hostname} on {uninstalled_at}.\n\n"
        f"What was removed:\n"
        f"- The browser monitoring service and email digest service have been stopped.\n"
        f"- Browser activity is no longer being monitored.\n"
        f"- No further digest emails will be sent.\n\n"
        f"To reinstall at any time, run: bash install.sh"
    )

    _send_smtp(
        subject="🛑 Vigil — Uninstalled",
        html_body=_wrap_email_html(
            heading="🌐 Vigil",
            body=html_body,
            footer="This is the final email from your Vigil.",
        ),
        plain_text=plain_text,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    # `--confirm` mode: send installation confirmation email then exit
    if "--confirm" in sys.argv:
        try:
            send_confirmation_email()
            print("Confirmation email sent successfully.")
        except Exception as exc:
            print(f"Failed to send confirmation email: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    # `--uninstall-notify` mode: send uninstall notification email then exit
    if "--uninstall-notify" in sys.argv:
        try:
            send_uninstall_email()
            print("Uninstall notification email sent successfully.")
        except Exception as exc:
            print(f"Failed to send uninstall notification email: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    global _scheduler
    trigger, desc = _build_trigger()
    _log(f"Summarizer daemon started — schedule: {desc}")

    _scheduler = BlockingScheduler(timezone=get_localzone_name())
    _scheduler.add_job(run_summary, trigger, misfire_grace_time=3600)
    _scheduler.add_job(_check_tracker_alive, IntervalTrigger(minutes=5), id='watchdog')

    try:
        _scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        _log("Summarizer daemon stopped.")


if __name__ == "__main__":
    main()

