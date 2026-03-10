"""
Tests for tracker.py — pure logic only.

All tests that touch the filesystem use pytest's tmp_path fixture so the
real activity log is never modified.  macOS-specific calls (AppleScript,
CoreGraphics, sysctl) are either tested at a structural level or mocked.
"""

import hashlib
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import tracker
from platforms.macos import tracker_macos


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_log(path: Path, lines: list) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# _update_integrity_hash
# ---------------------------------------------------------------------------

class TestUpdateIntegrityHash:
    def test_writes_correct_sha256(self, tmp_path, monkeypatch):
        log = tmp_path / "activity_log.txt"
        integrity = tmp_path / "activity_log.txt.sha256"
        log.write_text("hello world", encoding="utf-8")

        monkeypatch.setattr(tracker, "ACTIVITY_LOG",  log)
        monkeypatch.setattr(tracker, "INTEGRITY_FILE", integrity)

        tracker._update_integrity_hash()

        expected = hashlib.sha256(b"hello world").hexdigest()
        assert integrity.read_text().strip() == expected

    def test_is_noop_when_log_missing(self, tmp_path, monkeypatch):
        log = tmp_path / "missing.txt"
        integrity = tmp_path / "missing.txt.sha256"

        monkeypatch.setattr(tracker, "ACTIVITY_LOG",  log)
        monkeypatch.setattr(tracker, "INTEGRITY_FILE", integrity)

        # Should not raise
        tracker._update_integrity_hash()
        assert not integrity.exists()


# ---------------------------------------------------------------------------
# _log_duration_entry
# ---------------------------------------------------------------------------

class TestLogDurationEntry:
    def test_appends_correct_format(self, tmp_path, monkeypatch):
        log = tmp_path / "activity_log.txt"
        integrity = tmp_path / "activity_log.txt.sha256"
        log.write_text("", encoding="utf-8")

        monkeypatch.setattr(tracker, "ACTIVITY_LOG",  log)
        monkeypatch.setattr(tracker, "INTEGRITY_FILE", integrity)

        tracker._log_duration_entry("[Safari] github.com", 42)

        content = log.read_text()
        assert "[Safari] github.com [duration: 42s]" in content
        # Timestamp format: [YYYY-MM-DD HH:MM:SS]
        import re
        assert re.search(r'\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]', content)

    def test_no_flagged_tag_by_default(self, tmp_path, monkeypatch):
        log = tmp_path / "activity_log.txt"
        log.write_text("", encoding="utf-8")
        monkeypatch.setattr(tracker, "ACTIVITY_LOG",  log)
        monkeypatch.setattr(tracker, "INTEGRITY_FILE", tmp_path / "hash")

        tracker._log_duration_entry("[Safari] github.com", 10)

        assert "[FLAGGED_CONTENT]" not in log.read_text()

    def test_flagged_tag_appended_when_is_adult_true(self, tmp_path, monkeypatch):
        log = tmp_path / "activity_log.txt"
        log.write_text("", encoding="utf-8")
        monkeypatch.setattr(tracker, "ACTIVITY_LOG",  log)
        monkeypatch.setattr(tracker, "INTEGRITY_FILE", tmp_path / "hash")

        tracker._log_duration_entry("[Chrome] badsite.com", 15, is_adult=True)

        content = log.read_text()
        assert "[FLAGGED_CONTENT]" in content
        assert "[Chrome] badsite.com [duration: 15s] [FLAGGED_CONTENT]" in content

    def test_updates_integrity_hash(self, tmp_path, monkeypatch):
        log = tmp_path / "activity_log.txt"
        integrity = tmp_path / "activity_log.txt.sha256"
        log.write_text("", encoding="utf-8")

        monkeypatch.setattr(tracker, "ACTIVITY_LOG",  log)
        monkeypatch.setattr(tracker, "INTEGRITY_FILE", integrity)

        tracker._log_duration_entry("[Safari] example.com", 10)

        expected = hashlib.sha256(log.read_bytes()).hexdigest()
        assert integrity.read_text().strip() == expected


# ---------------------------------------------------------------------------
# _finalize_session
# ---------------------------------------------------------------------------

class TestFinalizeSession:
    def _make_session(self, label, start_offset_secs, idle_accumulated=0, idle_start=None):
        start = datetime.now() - timedelta(seconds=start_offset_secs)
        return {
            "label":            label,
            "start_time":       start,
            "idle_accumulated": idle_accumulated,
            "idle_start":       idle_start,
        }

    def test_basic_duration_logged(self, tmp_path, monkeypatch):
        log = tmp_path / "activity_log.txt"
        integrity = tmp_path / "activity_log.txt.sha256"
        log.write_text("", encoding="utf-8")
        monkeypatch.setattr(tracker, "ACTIVITY_LOG",  log)
        monkeypatch.setattr(tracker, "INTEGRITY_FILE", integrity)

        session = self._make_session("[Safari] github.com", start_offset_secs=30)
        tracker._finalize_session(session, datetime.now())

        content = log.read_text()
        assert "[Safari] github.com [duration:" in content
        assert "[FLAGGED_CONTENT]" not in content

    def test_adult_session_writes_flagged_tag(self, tmp_path, monkeypatch):
        log = tmp_path / "activity_log.txt"
        integrity = tmp_path / "activity_log.txt.sha256"
        log.write_text("", encoding="utf-8")
        monkeypatch.setattr(tracker, "ACTIVITY_LOG",  log)
        monkeypatch.setattr(tracker, "INTEGRITY_FILE", integrity)

        session = self._make_session("[Chrome] badsite.com", start_offset_secs=30)
        session["is_adult"] = True
        tracker._finalize_session(session, datetime.now())

        content = log.read_text()
        assert "[Chrome] badsite.com [duration:" in content
        assert "[FLAGGED_CONTENT]" in content

    def test_short_session_discarded(self, tmp_path, monkeypatch):
        log = tmp_path / "activity_log.txt"
        log.write_text("", encoding="utf-8")
        monkeypatch.setattr(tracker, "ACTIVITY_LOG",  log)
        monkeypatch.setattr(tracker, "INTEGRITY_FILE", tmp_path / "hash")

        # 3-second session is below MIN_SESSION_DURATION (5)
        session = self._make_session("[Chrome] example.com", start_offset_secs=3)
        tracker._finalize_session(session, datetime.now())

        assert log.read_text() == ""

    def test_idle_gap_excluded_from_duration(self, tmp_path, monkeypatch):
        log = tmp_path / "activity_log.txt"
        integrity = tmp_path / "activity_log.txt.sha256"
        log.write_text("", encoding="utf-8")
        monkeypatch.setattr(tracker, "ACTIVITY_LOG",  log)
        monkeypatch.setattr(tracker, "INTEGRITY_FILE", integrity)

        # 60s total, 30s idle already accumulated → net 30s
        session = self._make_session("[Safari] github.com", start_offset_secs=60,
                                     idle_accumulated=30)
        tracker._finalize_session(session, datetime.now())

        content = log.read_text()
        import re
        m = re.search(r'\[duration: (\d+)s\]', content)
        assert m is not None
        duration = int(m.group(1))
        # net duration should be approximately 30s (allow ±2s for test timing)
        assert 28 <= duration <= 32

    def test_open_idle_gap_counted(self, tmp_path, monkeypatch):
        """An idle period still in progress at finalize time should be counted."""
        log = tmp_path / "activity_log.txt"
        integrity = tmp_path / "activity_log.txt.sha256"
        log.write_text("", encoding="utf-8")
        monkeypatch.setattr(tracker, "ACTIVITY_LOG",  log)
        monkeypatch.setattr(tracker, "INTEGRITY_FILE", integrity)

        now = datetime.now()
        idle_start = now - timedelta(seconds=20)
        # 60s total, idle started 20s ago (still open), no prior accumulated idle
        session = {
            "label":            "[Safari] news.ycombinator.com",
            "start_time":       now - timedelta(seconds=60),
            "idle_accumulated": 0,
            "idle_start":       idle_start,
        }
        tracker._finalize_session(session, now)

        content = log.read_text()
        import re
        m = re.search(r'\[duration: (\d+)s\]', content)
        assert m is not None
        duration = int(m.group(1))
        # net ~40s (60 total - 20 open idle)
        assert 38 <= duration <= 42


# ---------------------------------------------------------------------------
# get_last_log_time
# ---------------------------------------------------------------------------

class TestGetLastLogTime:
    def test_parses_last_line_timestamp(self, tmp_path, monkeypatch):
        log = tmp_path / "activity_log.txt"
        _write_log(log, [
            "[2026-01-01 08:00:00] [Safari] github.com [duration: 60s]",
            "[2026-03-07 18:30:45] [Chrome] youtube.com [duration: 120s]",
        ])
        monkeypatch.setattr(tracker, "ACTIVITY_LOG", log)

        result = tracker.get_last_log_time()
        assert result is not None
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 7
        assert result.hour == 18
        assert result.minute == 30
        assert result.second == 45

    def test_returns_none_for_empty_file(self, tmp_path, monkeypatch):
        log = tmp_path / "activity_log.txt"
        log.write_text("", encoding="utf-8")
        monkeypatch.setattr(tracker, "ACTIVITY_LOG", log)

        assert tracker.get_last_log_time() is None

    def test_returns_none_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tracker, "ACTIVITY_LOG", tmp_path / "nonexistent.txt")
        assert tracker.get_last_log_time() is None

    def test_returns_none_for_unparseable_last_line(self, tmp_path, monkeypatch):
        log = tmp_path / "activity_log.txt"
        log.write_text("no timestamp here\n", encoding="utf-8")
        monkeypatch.setattr(tracker, "ACTIVITY_LOG", log)

        assert tracker.get_last_log_time() is None


# ---------------------------------------------------------------------------
# check_for_shutdown_event
# ---------------------------------------------------------------------------

class TestCheckForShutdownEvent:
    def test_logs_shutdown_when_last_activity_before_boot(self, tmp_path, monkeypatch):
        log = tmp_path / "activity_log.txt"
        _write_log(log, ["[2026-03-07 10:00:00] [Safari] github.com [duration: 30s]"])
        monkeypatch.setattr(tracker, "ACTIVITY_LOG", log)

        # Boot time is after last log time — shutdown must have occurred
        boot = datetime(2026, 3, 8, 9, 0, 0)
        monkeypatch.setattr(tracker, "get_boot_time",      lambda: boot)
        monkeypatch.setattr(tracker, "get_last_log_time",  lambda: datetime(2026, 3, 7, 10, 0, 0))

        tracker.check_for_shutdown_event()

        content = log.read_text()
        assert "[SYSTEM EVENT]" in content
        assert "Shutdown or restart detected" in content

    def test_no_log_when_last_activity_after_boot(self, tmp_path, monkeypatch):
        log = tmp_path / "activity_log.txt"
        log.write_text("", encoding="utf-8")
        monkeypatch.setattr(tracker, "ACTIVITY_LOG", log)

        # Last log is AFTER boot — no shutdown event
        monkeypatch.setattr(tracker, "get_boot_time",     lambda: datetime(2026, 3, 8, 7, 0, 0))
        monkeypatch.setattr(tracker, "get_last_log_time", lambda: datetime(2026, 3, 8, 9, 0, 0))

        tracker.check_for_shutdown_event()
        assert log.read_text() == ""

    def test_no_log_when_boot_time_unavailable(self, tmp_path, monkeypatch):
        log = tmp_path / "activity_log.txt"
        log.write_text("", encoding="utf-8")
        monkeypatch.setattr(tracker, "ACTIVITY_LOG", log)
        monkeypatch.setattr(tracker, "get_boot_time",     lambda: None)
        monkeypatch.setattr(tracker, "get_last_log_time", lambda: datetime(2026, 3, 8, 9, 0, 0))

        tracker.check_for_shutdown_event()
        assert log.read_text() == ""


# ---------------------------------------------------------------------------
# get_active_tab_applescript (structural tests — macOS AppleScript building)
# ---------------------------------------------------------------------------

class TestGetActiveTabAppleScript:
    def test_returns_non_empty_string(self):
        script = tracker_macos.get_active_tab_applescript()
        assert isinstance(script, str)
        assert len(script) > 0

    def test_contains_safari_block(self):
        script = tracker_macos.get_active_tab_applescript()
        assert 'application "Safari"' in script
        assert "current tab" in script

    def test_contains_system_events_block(self):
        script = tracker_macos.get_active_tab_applescript()
        assert "System Events" in script
        assert "frontmost" in script
