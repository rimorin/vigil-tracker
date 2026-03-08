import os
from pathlib import Path
from typing import List
from dotenv import load_dotenv

# Load .env from the project directory (same folder as this file).
# This works both in local dev and when run as a launchd service, regardless
# of the current working directory.
load_dotenv(Path(__file__).parent / ".env")

def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{name}' is not set. "
            f"Add it to your .env file or re-run install.sh."
        )
    return value

OPENAI_API_KEY: str = _require("OPENAI_API_KEY")
OPENAI_MODEL: str = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# ---------------------------------------------------------------------------
# SMTP email configuration
# ---------------------------------------------------------------------------
SMTP_HOST: str = _require("SMTP_HOST")
SMTP_PORT: int = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER: str = _require("SMTP_USER")
SMTP_PASS: str = _require("SMTP_PASS")
# Sender address — defaults to SMTP_USER if not explicitly set
SMTP_FROM: str = os.environ.get("SMTP_FROM", "") or _require("SMTP_USER")
# Comma-separated list of recipient addresses
SMTP_TO: List[str] = [e.strip() for e in _require("SMTP_TO").split(",") if e.strip()]

# ---------------------------------------------------------------------------
# Schedule configuration
# ---------------------------------------------------------------------------
# SUMMARY_SCHEDULE controls when the digest is sent.
# Valid values: hourly | daily | weekly | monthly
SUMMARY_SCHEDULE: str = os.environ.get("SUMMARY_SCHEDULE", "daily").lower()

# Hour (0-23) and minute (0-59) for the trigger time.
SUMMARY_SCHEDULE_HOUR: int = int(os.environ.get("SUMMARY_SCHEDULE_HOUR", "21"))
SUMMARY_SCHEDULE_MINUTE: int = int(os.environ.get("SUMMARY_SCHEDULE_MINUTE", "0"))

# Day of week for weekly schedule: mon | tue | wed | thu | fri | sat | sun
SUMMARY_SCHEDULE_WEEKDAY: str = os.environ.get("SUMMARY_SCHEDULE_WEEKDAY", "mon").lower()

# Day of month (1-28) for monthly schedule.
SUMMARY_SCHEDULE_DAY: int = int(os.environ.get("SUMMARY_SCHEDULE_DAY", "1"))

_VALID_SCHEDULES = {"hourly", "daily", "weekly", "monthly", "interval"}
if SUMMARY_SCHEDULE not in _VALID_SCHEDULES:
    raise EnvironmentError(
        f"SUMMARY_SCHEDULE must be one of {_VALID_SCHEDULES}. Got: '{SUMMARY_SCHEDULE}'"
    )

# Number of minutes between digests — used when SUMMARY_SCHEDULE=interval
SUMMARY_SCHEDULE_INTERVAL_MINUTES: int = int(os.environ.get("SUMMARY_SCHEDULE_INTERVAL_MINUTES", "60"))
