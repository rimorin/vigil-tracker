"""
Shared pytest configuration.

Adds the project root to sys.path so tests can import tracker, summarizer,
and config without installing the package.  Also sets the minimum required
environment variables so config.py's module-level _require() calls succeed
without a real .env file being present.

External SMTP is permanently disabled for the entire test session via the
`_block_all_external_io` session-scoped autouse fixture below.  This runs
before any test module is imported, making it impossible for any real SMTP
connection to fire — even if a test accidentally bypasses the module-level
mocks in test_alerter.py.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import tracker
from platforms.macos import tracker_macos

# Make the project root importable
sys.path.insert(0, str(Path(__file__).parent.parent))

# Provide stub values for every required env var so config.py can be
# imported cleanly in tests without reading the real .env.
os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy")
os.environ.setdefault("SMTP_HOST",      "smtp.example.com")
os.environ.setdefault("SMTP_PORT",      "587")
os.environ.setdefault("SMTP_USER",      "test@example.com")
os.environ.setdefault("SMTP_PASS",      "dummy-password")
os.environ.setdefault("SMTP_TO",        "recipient@example.com")


@pytest.fixture(autouse=True, scope="session")
def _block_all_external_io():
    """Block ALL real SMTP calls for the entire test session.

    This session-scoped fixture starts the patches before any test runs and
    stops them only after the last test finishes.  It is the primary safety
    net; the function-scoped fixture in test_alerter.py is a secondary layer.
    """
    p_smtp = patch("smtplib.SMTP")
    p_ssl  = patch("smtplib.SMTP_SSL")
    p_smtp.start()
    p_ssl.start()
    yield
    p_smtp.stop()
    p_ssl.stop()


@pytest.fixture(autouse=True)
def _reset_module_globals():
    """Reset module-level globals that would otherwise leak between tests.

    * tracker._integrity_hasher / _integrity_file_offset  — incremental SHA-256
    * tracker_macos._last_frontmost_pid / _last_label     — PID-skip cache
    """
    tracker._integrity_hasher = None
    tracker._integrity_file_offset = 0
    tracker_macos._last_frontmost_pid = -1
    tracker_macos._last_label = ""
    yield
    tracker._integrity_hasher = None
    tracker._integrity_file_offset = 0
    tracker_macos._last_frontmost_pid = -1
    tracker_macos._last_label = ""
