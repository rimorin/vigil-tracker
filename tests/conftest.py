"""
Shared pytest configuration.

Adds the project root to sys.path so tests can import tracker, summarizer,
and config without installing the package.  Also sets the minimum required
environment variables so config.py's module-level _require() calls succeed
without a real .env file being present.
"""

import os
import sys
from pathlib import Path

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
