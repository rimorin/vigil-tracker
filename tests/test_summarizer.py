"""
Tests for summarizer.py — pure logic only.

All tests that touch the filesystem use pytest's tmp_path fixture so the
real activity log is never modified.  OpenAI, SMTP, and APScheduler are
never invoked.
"""

import hashlib
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

import summarizer
import config


# ---------------------------------------------------------------------------
# Shared fixture — redirects module-level file paths to a tmp directory
# ---------------------------------------------------------------------------

@pytest.fixture
def log_env(tmp_path, monkeypatch):
    """Patch ACTIVITY_LOG and INTEGRITY_FILE to point at temp files."""
    log       = tmp_path / "activity_log.txt"
    integrity = tmp_path / "activity_log.txt.sha256"
    monkeypatch.setattr(summarizer, "ACTIVITY_LOG",  log)
    monkeypatch.setattr(summarizer, "INTEGRITY_FILE", integrity)
    return log, integrity


@pytest.fixture
def sentinel_env(tmp_path, monkeypatch):
    """Patch SENTINEL_FILE to a temp file."""
    sentinel = tmp_path / "last_summarized_date.txt"
    monkeypatch.setattr(summarizer, "SENTINEL_FILE", sentinel)
    return sentinel


# ---------------------------------------------------------------------------
# _cleanup_old_entries
# ---------------------------------------------------------------------------

def _make_log(log_path: Path, days_ago_list: list, extra_lines: list = None):
    """Write log entries with dates relative to today."""
    lines = []
    for days_ago in days_ago_list:
        d = (date.today() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        lines.append(f"[{d} 10:00:00] [Safari] https://example.com [duration: 60s]")
    if extra_lines:
        lines.extend(extra_lines)
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestCleanupOldEntries:
    def test_removes_entries_older_than_retention(self, log_env, monkeypatch):
        log, _ = log_env
        # 3 old entries (40 days ago) + 1 recent entry (1 day ago)
        _make_log(log, days_ago_list=[40, 45, 50, 1])
        monkeypatch.setattr(config, "LOG_RETENTION_DAYS", 30)

        summarizer._cleanup_old_entries()

        content = log.read_text()
        lines = [l for l in content.splitlines() if l.strip()]
        assert len(lines) == 1
        assert (date.today() - timedelta(days=1)).strftime("%Y-%m-%d") in lines[0]

    def test_keeps_all_entries_within_retention(self, log_env, monkeypatch):
        log, _ = log_env
        _make_log(log, days_ago_list=[1, 5, 10, 29])
        monkeypatch.setattr(config, "LOG_RETENTION_DAYS", 30)

        summarizer._cleanup_old_entries()

        lines = [l for l in log.read_text().splitlines() if l.strip()]
        assert len(lines) == 4

    def test_keeps_undated_lines(self, log_env, monkeypatch):
        """Lines without a parseable date prefix (e.g. SYSTEM EVENT) must be kept."""
        log, _ = log_env
        _make_log(log, days_ago_list=[50],
                  extra_lines=["[SYSTEM EVENT] Shutdown or restart detected"])
        monkeypatch.setattr(config, "LOG_RETENTION_DAYS", 30)

        summarizer._cleanup_old_entries()

        content = log.read_text()
        assert "[SYSTEM EVENT]" in content

    def test_noop_when_all_entries_are_recent(self, log_env, monkeypatch):
        log, _ = log_env
        _make_log(log, days_ago_list=[1, 2, 3])
        original = log.read_text()
        monkeypatch.setattr(config, "LOG_RETENTION_DAYS", 30)

        summarizer._cleanup_old_entries()

        assert log.read_text() == original

    def test_noop_when_retention_days_is_zero(self, log_env, monkeypatch):
        log, _ = log_env
        _make_log(log, days_ago_list=[100, 200])
        original = log.read_text()
        monkeypatch.setattr(config, "LOG_RETENTION_DAYS", 0)

        summarizer._cleanup_old_entries()

        assert log.read_text() == original

    def test_noop_when_log_does_not_exist(self, log_env, monkeypatch):
        log, _ = log_env
        # Do not create the log file
        monkeypatch.setattr(config, "LOG_RETENTION_DAYS", 30)
        # Should not raise
        summarizer._cleanup_old_entries()

    def test_updates_integrity_hash_after_prune(self, log_env, monkeypatch):
        log, integrity = log_env
        _make_log(log, days_ago_list=[40, 1])
        monkeypatch.setattr(config, "LOG_RETENTION_DAYS", 30)

        summarizer._cleanup_old_entries()

        expected = hashlib.sha256(log.read_bytes()).hexdigest()
        assert integrity.read_text().strip() == expected

    def test_no_temp_file_left_behind(self, log_env, monkeypatch):
        log, _ = log_env
        _make_log(log, days_ago_list=[40, 1])
        monkeypatch.setattr(config, "LOG_RETENTION_DAYS", 30)

        summarizer._cleanup_old_entries()

        assert not log.with_suffix(".tmp").exists()

    def test_removes_correct_count(self, log_env, monkeypatch):
        log, _ = log_env
        # 5 old, 2 recent
        _make_log(log, days_ago_list=[31, 35, 40, 50, 60, 1, 2])
        monkeypatch.setattr(config, "LOG_RETENTION_DAYS", 30)

        summarizer._cleanup_old_entries()

        lines = [l for l in log.read_text().splitlines() if l.strip()]
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# _strip_to_domain
# ---------------------------------------------------------------------------

class TestStripToDomain:
    def test_strips_path_and_query(self):
        entries = ["[2026-03-08 10:00:00] [Safari] https://github.com/user/repo?tab=stars [duration: 30s]"]
        result = summarizer._strip_to_domain(entries)
        assert "github.com" in result[0]
        assert "/user/repo" not in result[0]
        assert "tab=stars" not in result[0]

    def test_preserves_timestamp_and_duration(self):
        entries = ["[2026-03-08 10:00:00] [Chrome] https://youtube.com/watch?v=abc [duration: 120s]"]
        result = summarizer._strip_to_domain(entries)
        assert "[2026-03-08 10:00:00]" in result[0]
        assert "[duration: 120s]" in result[0]

    def test_handles_multiple_entries(self):
        entries = [
            "https://news.ycombinator.com/item?id=123",
            "https://stackoverflow.com/questions/456/answer",
        ]
        result = summarizer._strip_to_domain(entries)
        assert "news.ycombinator.com" in result[0]
        assert "stackoverflow.com" in result[1]


# ---------------------------------------------------------------------------
# parse_duration_entries
# ---------------------------------------------------------------------------

class TestParseDurationEntries:
    def test_accumulates_time_per_domain(self):
        entries = [
            "[2026-03-08 09:00:00] [Safari] https://github.com/repo [duration: 60s]",
            "[2026-03-08 10:00:00] [Safari] https://github.com/other [duration: 90s]",
            "[2026-03-08 11:00:00] [Chrome] https://youtube.com/watch [duration: 120s]",
        ]
        result = summarizer.parse_duration_entries(entries)
        assert result["github.com"] == 150
        assert result["youtube.com"] == 120

    def test_returns_empty_dict_for_no_entries(self):
        assert summarizer.parse_duration_entries([]) == {}

    def test_ignores_lines_without_duration(self):
        entries = [
            "[SYSTEM EVENT] Shutdown detected",
            "[2026-03-08 09:00:00] [Safari] https://example.com [duration: 45s]",
        ]
        result = summarizer.parse_duration_entries(entries)
        assert list(result.keys()) == ["example.com"]
        assert result["example.com"] == 45


# ---------------------------------------------------------------------------
# _format_duration
# ---------------------------------------------------------------------------

class TestFormatDuration:
    @pytest.mark.parametrize("seconds,expected", [
        (0,    "0s"),
        (30,   "30s"),
        (59,   "59s"),
        (60,   "1m"),
        (90,   "1m"),
        (3600, "1h"),
        (3660, "1h 1m"),
        (7200, "2h"),
        (7380, "2h 3m"),
    ])
    def test_formats_correctly(self, seconds, expected):
        assert summarizer._format_duration(seconds) == expected


# ---------------------------------------------------------------------------
# _build_time_per_domain_html
# ---------------------------------------------------------------------------

class TestBuildTimePerDomainHtml:
    def test_returns_empty_string_for_empty_input(self):
        assert summarizer._build_time_per_domain_html({}) == ""

    def test_includes_top_5_domains_only(self):
        domain_times = {f"site{i}.com": (10 - i) * 60 for i in range(8)}
        html = summarizer._build_time_per_domain_html(domain_times)
        # Top 5: site0 through site4
        for i in range(5):
            assert f"site{i}.com" in html
        # site5 and beyond should be excluded
        for i in range(5, 8):
            assert f"site{i}.com" not in html

    def test_includes_total_time(self):
        html = summarizer._build_time_per_domain_html({"github.com": 3600})
        assert "1h" in html

    def test_most_visited_domain_appears_in_output(self):
        html = summarizer._build_time_per_domain_html(
            {"top.com": 3600, "second.com": 1800}
        )
        assert "top.com" in html
        assert "second.com" in html


# ---------------------------------------------------------------------------
# _html_to_text
# ---------------------------------------------------------------------------

class TestHtmlToText:
    def test_strips_tags(self):
        html = "<h1>Title</h1><p>Some <strong>text</strong>.</p>"
        result = summarizer._html_to_text(html)
        assert "<" not in result
        assert ">" not in result
        assert "Title" in result
        assert "text" in result

    def test_collapses_extra_whitespace(self):
        html = "<p>  lots   of   spaces  </p>"
        result = summarizer._html_to_text(html)
        assert "  " not in result


# ---------------------------------------------------------------------------
# _already_sent_today / _mark_sent_today
# ---------------------------------------------------------------------------

class TestSentinelFile:
    def test_already_sent_today_returns_true_for_today(self, sentinel_env):
        sentinel_env.write_text(str(date.today()))
        assert summarizer._already_sent_today() is True

    def test_already_sent_today_returns_false_for_yesterday(self, sentinel_env):
        yesterday = str(date.today() - timedelta(days=1))
        sentinel_env.write_text(yesterday)
        assert summarizer._already_sent_today() is False

    def test_already_sent_today_returns_false_when_no_file(self, sentinel_env):
        assert not sentinel_env.exists()
        assert summarizer._already_sent_today() is False

    def test_mark_sent_today_writes_todays_date(self, sentinel_env):
        summarizer._mark_sent_today()
        assert sentinel_env.read_text().strip() == str(date.today())

    def test_mark_then_check_returns_true(self, sentinel_env):
        summarizer._mark_sent_today()
        assert summarizer._already_sent_today() is True


# ---------------------------------------------------------------------------
# _missed_todays_schedule
# ---------------------------------------------------------------------------

class TestMissedTodaysSchedule:
    """_missed_todays_schedule returns True only for daily schedule when the
    scheduled time has passed and no digest has been sent today."""

    def _now_at(self, hour, minute=0):
        return datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)

    def test_returns_true_when_past_schedule_and_not_sent(self, sentinel_env, monkeypatch):
        monkeypatch.setattr(config, "SUMMARY_SCHEDULE", "daily")
        monkeypatch.setattr(config, "SUMMARY_SCHEDULE_HOUR", 6)
        monkeypatch.setattr(config, "SUMMARY_SCHEDULE_MINUTE", 0)
        with patch("summarizer.datetime") as mock_dt:
            mock_dt.now.return_value = self._now_at(9)
            assert summarizer._missed_todays_schedule() is True

    def test_returns_false_when_before_schedule(self, sentinel_env, monkeypatch):
        monkeypatch.setattr(config, "SUMMARY_SCHEDULE", "daily")
        monkeypatch.setattr(config, "SUMMARY_SCHEDULE_HOUR", 6)
        monkeypatch.setattr(config, "SUMMARY_SCHEDULE_MINUTE", 0)
        with patch("summarizer.datetime") as mock_dt:
            mock_dt.now.return_value = self._now_at(5)
            assert summarizer._missed_todays_schedule() is False

    def test_returns_false_when_already_sent_today(self, sentinel_env, monkeypatch):
        monkeypatch.setattr(config, "SUMMARY_SCHEDULE", "daily")
        monkeypatch.setattr(config, "SUMMARY_SCHEDULE_HOUR", 6)
        monkeypatch.setattr(config, "SUMMARY_SCHEDULE_MINUTE", 0)
        sentinel_env.write_text(str(date.today()))
        with patch("summarizer.datetime") as mock_dt:
            mock_dt.now.return_value = self._now_at(9)
            assert summarizer._missed_todays_schedule() is False

    def test_returns_false_for_non_daily_schedule(self, sentinel_env, monkeypatch):
        for schedule in ("hourly", "weekly", "monthly", "interval"):
            monkeypatch.setattr(config, "SUMMARY_SCHEDULE", schedule)
            assert summarizer._missed_todays_schedule() is False


# ---------------------------------------------------------------------------
# _verify_log_integrity
# ---------------------------------------------------------------------------

class TestVerifyLogIntegrity:
    def test_returns_true_when_hash_matches(self, log_env):
        log, integrity = log_env
        content = b"some log content\n"
        log.write_bytes(content)
        integrity.write_text(hashlib.sha256(content).hexdigest())

        assert summarizer._verify_log_integrity() is True

    def test_returns_false_when_log_tampered(self, log_env):
        log, integrity = log_env
        log.write_bytes(b"original content\n")
        integrity.write_text(hashlib.sha256(b"original content\n").hexdigest())
        # Tamper with the log
        log.write_bytes(b"tampered content\n")

        assert summarizer._verify_log_integrity() is False

    def test_returns_true_when_no_integrity_file(self, log_env):
        log, integrity = log_env
        log.write_bytes(b"some content\n")
        # integrity file not created → nothing to compare

        assert summarizer._verify_log_integrity() is True

    def test_returns_true_when_neither_file_exists(self, log_env):
        # Both files absent → no comparison possible → considered valid
        assert summarizer._verify_log_integrity() is True

    def test_returns_false_when_log_deleted_but_hash_present(self, log_env):
        """Hash sidecar exists (baseline established) but log was deleted → tampered."""
        log, integrity = log_env
        integrity.write_text(hashlib.sha256(b"original content\n").hexdigest())
        # log file is intentionally NOT created — simulates attacker deleting the log

        assert summarizer._verify_log_integrity() is False


# ---------------------------------------------------------------------------
# _check_watchdog_heartbeat  (SIGKILL heartbeat defence)
# ---------------------------------------------------------------------------

@pytest.fixture
def heartbeat_env(tmp_path, monkeypatch):
    """Patch WATCHDOG_HEARTBEAT_FILE to a temp path."""
    hb = tmp_path / "watchdog_heartbeat"
    monkeypatch.setattr(summarizer, "WATCHDOG_HEARTBEAT_FILE", hb)
    return hb


class TestCheckWatchdogHeartbeat:
    """_check_watchdog_heartbeat alerts when the watchdog heartbeat goes stale."""

    def test_no_alert_when_heartbeat_file_absent(self, heartbeat_env):
        """No heartbeat file → watchdog may not be installed; skip silently."""
        with patch.object(summarizer, "_send_alert_email") as mock_alert:
            summarizer._check_watchdog_heartbeat()
        mock_alert.assert_not_called()

    def test_no_alert_when_heartbeat_is_fresh(self, heartbeat_env):
        """Heartbeat written just now → no alert."""
        heartbeat_env.write_text(str(time.time()))
        with patch.object(summarizer, "_send_alert_email") as mock_alert:
            summarizer._check_watchdog_heartbeat()
        mock_alert.assert_not_called()

    def test_no_alert_just_within_threshold(self, heartbeat_env):
        """Heartbeat at exactly the threshold boundary is still considered fresh."""
        just_within = time.time() - summarizer._WATCHDOG_HEARTBEAT_STALE_SECS + 1
        heartbeat_env.write_text(str(just_within))
        with patch.object(summarizer, "_send_alert_email") as mock_alert:
            summarizer._check_watchdog_heartbeat()
        mock_alert.assert_not_called()

    def test_alerts_when_heartbeat_stale(self, heartbeat_env):
        """Heartbeat older than threshold → partner alert sent."""
        stale_time = time.time() - summarizer._WATCHDOG_HEARTBEAT_STALE_SECS - 10
        heartbeat_env.write_text(str(stale_time))
        with patch.object(summarizer, "_send_alert_email") as mock_alert:
            summarizer._check_watchdog_heartbeat()
        mock_alert.assert_called_once()
        subject = mock_alert.call_args.kwargs["subject"]
        assert "Watchdog" in subject
        assert "Heartbeat" in subject or "heartbeat" in subject.lower()

    def test_alert_subject_mentions_stale_heartbeat(self, heartbeat_env):
        """Alert subject clearly identifies the stale heartbeat condition."""
        stale_time = time.time() - summarizer._WATCHDOG_HEARTBEAT_STALE_SECS - 30
        heartbeat_env.write_text(str(stale_time))
        with patch.object(summarizer, "_send_alert_email") as mock_alert:
            summarizer._check_watchdog_heartbeat()
        subject = mock_alert.call_args.kwargs["subject"]
        assert "Stale" in subject or "stale" in subject.lower()

    def test_no_alert_when_heartbeat_file_malformed(self, heartbeat_env):
        """Malformed heartbeat file (not a float) → skip silently, no crash."""
        heartbeat_env.write_text("not-a-timestamp")
        with patch.object(summarizer, "_send_alert_email") as mock_alert:
            summarizer._check_watchdog_heartbeat()  # must not raise
        mock_alert.assert_not_called()


# ---------------------------------------------------------------------------
# Gap 1a: watchdog heartbeat file deletion detected by summarizer
# ---------------------------------------------------------------------------

class TestCheckWatchdogHeartbeatDeletion:
    """Summarizer alerts when the watchdog heartbeat file is deleted after first-seen."""

    def test_no_alert_when_file_absent_and_never_seen(self, heartbeat_env, monkeypatch):
        """File absent before first-seen → silent (not yet installed)."""
        monkeypatch.setattr(summarizer, "_watchdog_heartbeat_ever_seen", False)
        with patch.object(summarizer, "_send_alert_email") as mock_alert:
            summarizer._check_watchdog_heartbeat()
        mock_alert.assert_not_called()

    def test_alerts_when_file_deleted_after_being_seen(self, heartbeat_env, monkeypatch):
        """File was present, then deleted → alert for tamper attempt."""
        heartbeat_env.write_text(str(time.time()))
        monkeypatch.setattr(summarizer, "_watchdog_heartbeat_ever_seen", False)
        with patch.object(summarizer, "_send_alert_email"):
            summarizer._check_watchdog_heartbeat()  # primes _ever_seen = True
        heartbeat_env.unlink()
        with patch.object(summarizer, "_send_alert_email") as mock_alert:
            summarizer._check_watchdog_heartbeat()
        mock_alert.assert_called_once()
        subject = mock_alert.call_args.kwargs["subject"]
        assert "Deleted" in subject or "deleted" in subject.lower()
        assert "Watchdog" in subject or "Heartbeat" in subject

    def test_deletion_alert_body_mentions_tamper(self, heartbeat_env, monkeypatch):
        """Alert body should mention tamper/deliberate deletion."""
        heartbeat_env.write_text(str(time.time()))
        monkeypatch.setattr(summarizer, "_watchdog_heartbeat_ever_seen", False)
        with patch.object(summarizer, "_send_alert_email"):
            summarizer._check_watchdog_heartbeat()
        heartbeat_env.unlink()
        with patch.object(summarizer, "_send_alert_email") as mock_alert:
            summarizer._check_watchdog_heartbeat()
        body = mock_alert.call_args.kwargs["plain_text"]
        assert "tamper" in body.lower() or "deliberate" in body.lower()


# ---------------------------------------------------------------------------
# _write_summarizer_heartbeat: summarizer writes its own heartbeat
# ---------------------------------------------------------------------------

@pytest.fixture
def summarizer_hb_env(tmp_path, monkeypatch):
    """Patch SUMMARIZER_HEARTBEAT_FILE to a temp path."""
    hb = tmp_path / "summarizer_heartbeat"
    monkeypatch.setattr(summarizer, "SUMMARIZER_HEARTBEAT_FILE", hb)
    return hb


class TestWriteSummarizerHeartbeat:
    """_write_summarizer_heartbeat writes a fresh timestamp to SUMMARIZER_HEARTBEAT_FILE."""

    def test_creates_heartbeat_file(self, summarizer_hb_env):
        """File is created when it does not exist."""
        summarizer._write_summarizer_heartbeat()
        assert summarizer_hb_env.exists()

    def test_written_value_is_a_float(self, summarizer_hb_env):
        """Written content is a valid floating-point Unix timestamp."""
        summarizer._write_summarizer_heartbeat()
        val = float(summarizer_hb_env.read_text().strip())
        assert val > 0

    def test_written_value_is_recent(self, summarizer_hb_env):
        """Timestamp is within 1 second of time.time()."""
        before = time.time()
        summarizer._write_summarizer_heartbeat()
        after = time.time()
        val = float(summarizer_hb_env.read_text().strip())
        assert before <= val <= after

    def test_overwrites_stale_value(self, summarizer_hb_env):
        """Calling the function twice updates the file with a newer timestamp."""
        summarizer_hb_env.write_text(str(time.time() - 120))
        first = float(summarizer_hb_env.read_text())
        summarizer._write_summarizer_heartbeat()
        second = float(summarizer_hb_env.read_text())
        assert second > first

    def test_no_exception_on_write_failure(self, tmp_path, monkeypatch):
        """A write failure (e.g., read-only dir) is swallowed — must not crash."""
        import summarizer as _s
        monkeypatch.setattr(_s, "SUMMARIZER_HEARTBEAT_FILE", Path("/nonexistent_dir/hb"))
        _s._write_summarizer_heartbeat()  # must not raise
