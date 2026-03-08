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
            f"Add it to your .env file or launchd plist EnvironmentVariables."
        )
    return value

OPENAI_API_KEY: str = _require("OPENAI_API_KEY")
MAILERSEND_API_KEY: str = _require("MAILERSEND_API_KEY")
MAILERSEND_FROM: str = _require("MAILERSEND_FROM")   # e.g. "tracker@yourdomain.com"
# Comma-separated list of recipient addresses, e.g. "a@example.com,b@example.com"
MAILERSEND_TO: List[str] = [e.strip() for e in _require("MAILERSEND_TO").split(",") if e.strip()]

OPENAI_MODEL: str = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

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
