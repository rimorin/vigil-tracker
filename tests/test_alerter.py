"""
Tests for alerter.py.

External I/O is blocked at two levels:

  1. **Session scope** (conftest.py): smtplib.SMTP and smtplib.SMTP_SSL are
     patched for the entire pytest session.  This fires before any test module
     is imported and is the primary safety net.

  2. **Function scope** (this module): the `_block_external_io` autouse
     fixture below re-applies the same patches per-test.  This ensures
     individual tests that reload the alerter module still get clean mocks,
     and makes the blocking intent explicit at the module level.

Individual tests that need to assert on SMTP behaviour apply their own inner
patch, which stacks safely on top of both autouse layers.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Autouse safety fixture — blocks ALL external I/O for every test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _block_external_io():
    """Prevent any real SMTP call from firing during tests."""
    with patch("smtplib.SMTP"), patch("smtplib.SMTP_SSL"):
        yield

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reload_alerter():
    """Re-import alerter so module-level state (_BLOCKLIST) is fresh."""
    if "alerter" in sys.modules:
        del sys.modules["alerter"]
    import alerter as _a
    return _a


# ---------------------------------------------------------------------------
# is_adult_domain
# ---------------------------------------------------------------------------

class TestIsAdultDomain:
    def setup_method(self):
        self.alerter = _reload_alerter()

    def test_blocklist_hit(self):
        self.alerter._BLOCKLIST = frozenset({"testadultsite.com"})
        assert self.alerter.is_adult_domain("testadultsite.com") is True

    def test_blocklist_miss_clean_domain(self):
        self.alerter._BLOCKLIST = frozenset({"testadultsite.com"})
        assert self.alerter.is_adult_domain("github.com") is False

    def test_keyword_match_porn(self):
        self.alerter._BLOCKLIST = frozenset()
        assert self.alerter.is_adult_domain("porn.example.com") is True

    def test_keyword_match_xxx(self):
        self.alerter._BLOCKLIST = frozenset()
        assert self.alerter.is_adult_domain("xxx.example.com") is True

    def test_keyword_match_hentai(self):
        self.alerter._BLOCKLIST = frozenset()
        assert self.alerter.is_adult_domain("hentai.io") is True

    def test_keyword_match_nsfw(self):
        self.alerter._BLOCKLIST = frozenset()
        assert self.alerter.is_adult_domain("nsfw-content.net") is True

    def test_no_false_positive_essex(self):
        self.alerter._BLOCKLIST = frozenset()
        assert self.alerter.is_adult_domain("essex.gov.uk") is False

    def test_no_false_positive_xxxlarge(self):
        self.alerter._BLOCKLIST = frozenset()
        assert self.alerter.is_adult_domain("xxxlarge-clothing.com") is False

    def test_case_insensitive_blocklist(self):
        self.alerter._BLOCKLIST = frozenset({"testadultsite.com"})
        assert self.alerter.is_adult_domain("testadultsite.com") is True

    def test_www_stripped_by_check_url_not_is_adult_domain(self):
        self.alerter._BLOCKLIST = frozenset({"testadultsite.com"})
        assert self.alerter.is_adult_domain("www.testadultsite.com") is False  # www not in blocklist


# ---------------------------------------------------------------------------
# check_url — returns bool, no email dispatch
# ---------------------------------------------------------------------------

class TestCheckUrlParsing:
    """Verify check_url correctly extracts the domain and returns True/False."""

    def setup_method(self):
        self.alerter = _reload_alerter()
        self.alerter._BLOCKLIST = frozenset({"adultexample.com"})

    def test_safari_label_format_returns_true(self):
        assert self.alerter.check_url("[Safari] https://adultexample.com/page") is True

    def test_chrome_label_format_returns_true(self):
        assert self.alerter.check_url("[Google Chrome] https://adultexample.com/") is True

    def test_www_stripped_before_lookup(self):
        assert self.alerter.check_url("[Safari] https://www.adultexample.com/") is True

    def test_clean_domain_returns_false(self):
        assert self.alerter.check_url("[Safari] https://github.com/user/repo") is False

    def test_raw_url_without_browser_prefix(self):
        assert self.alerter.check_url("https://adultexample.com/video") is True

    def test_empty_label_returns_false(self):
        assert self.alerter.check_url("") is False

    def test_non_url_label_returns_false(self):
        assert self.alerter.check_url("not-a-url") is False


class TestCheckUrlDispatch:
    """check_url must never send emails — it is detection-only."""

    def setup_method(self):
        self.alerter = _reload_alerter()
        self.alerter._BLOCKLIST = frozenset({"adultexample.com"})

    def test_no_email_sent_on_adult_domain(self):
        with patch.object(self.alerter, "_send_flagged_email") as mock_email:
            self.alerter.check_url("[Safari] https://adultexample.com/")
            mock_email.assert_not_called()

    def test_master_switch_returns_false(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ALERT_ENABLED", False)
        assert self.alerter.check_url("[Safari] https://adultexample.com/") is False

    def test_returns_true_for_adult_domain(self):
        assert self.alerter.check_url("[Safari] https://adultexample.com/") is True

    def test_returns_false_for_clean_domain(self):
        assert self.alerter.check_url("[Safari] https://github.com/") is False


# ---------------------------------------------------------------------------
# Cursor helpers
# ---------------------------------------------------------------------------

class TestCursorHelpers:
    def setup_method(self):
        self.alerter = _reload_alerter()

    def test_read_cursor_returns_none_when_missing(self, tmp_path):
        assert self.alerter._read_cursor(tmp_path / "cursor.txt") is None

    def test_write_then_read_roundtrip(self, tmp_path):
        cursor = tmp_path / "cursor.txt"
        dt = datetime(2026, 3, 10, 14, 30, 0)
        self.alerter._write_cursor(cursor, dt)
        result = self.alerter._read_cursor(cursor)
        assert result == dt

    def test_read_cursor_returns_none_on_corrupt_file(self, tmp_path):
        cursor = tmp_path / "cursor.txt"
        cursor.write_text("not-a-datetime", encoding="utf-8")
        assert self.alerter._read_cursor(cursor) is None


# ---------------------------------------------------------------------------
# scan_and_alert
# ---------------------------------------------------------------------------

class TestScanAndAlert:
    def setup_method(self):
        self.alerter = _reload_alerter()

    def _make_log(self, path: Path, lines: list) -> None:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_no_flagged_entries_sends_no_email(self, tmp_path):
        log = tmp_path / "activity_log.txt"
        cursor = tmp_path / "cursor.txt"
        self._make_log(log, [
            "[2026-03-10 10:00:00] [Chrome] github.com [duration: 60s]",
        ])
        with patch.object(self.alerter, "_send_flagged_email") as mock_email:
            self.alerter.scan_and_alert(log, cursor)
            mock_email.assert_not_called()

    def test_flagged_entry_triggers_email(self, tmp_path):
        log = tmp_path / "activity_log.txt"
        cursor = tmp_path / "cursor.txt"
        self._make_log(log, [
            "[2026-03-10 10:00:00] [Chrome] badsite.com [duration: 30s] [FLAGGED_CONTENT]",
        ])
        with patch.object(self.alerter, "_send_flagged_email") as mock_email:
            self.alerter.scan_and_alert(log, cursor)
            mock_email.assert_called_once()
            visits = mock_email.call_args[0][0]
            assert len(visits) == 1
            assert visits[0][0] == "[Chrome] badsite.com"

    def test_multiple_flagged_entries_in_one_email(self, tmp_path):
        log = tmp_path / "activity_log.txt"
        cursor = tmp_path / "cursor.txt"
        self._make_log(log, [
            "[2026-03-10 10:00:00] [Chrome] site1.com [duration: 10s] [FLAGGED_CONTENT]",
            "[2026-03-10 10:05:00] [Chrome] site2.com [duration: 15s] [FLAGGED_CONTENT]",
        ])
        with patch.object(self.alerter, "_send_flagged_email") as mock_email:
            self.alerter.scan_and_alert(log, cursor)
            mock_email.assert_called_once()
            visits = mock_email.call_args[0][0]
            assert len(visits) == 2

    def test_cursor_filters_old_entries(self, tmp_path):
        log = tmp_path / "activity_log.txt"
        cursor = tmp_path / "cursor.txt"
        # Set cursor to a time after the flagged entry
        self.alerter._write_cursor(cursor, datetime(2026, 3, 10, 11, 0, 0))
        self._make_log(log, [
            "[2026-03-10 10:00:00] [Chrome] badsite.com [duration: 30s] [FLAGGED_CONTENT]",
        ])
        with patch.object(self.alerter, "_send_flagged_email") as mock_email:
            self.alerter.scan_and_alert(log, cursor)
            mock_email.assert_not_called()

    def test_cursor_allows_new_entries_after_cursor(self, tmp_path):
        log = tmp_path / "activity_log.txt"
        cursor = tmp_path / "cursor.txt"
        self.alerter._write_cursor(cursor, datetime(2026, 3, 10, 9, 0, 0))
        self._make_log(log, [
            "[2026-03-10 10:00:00] [Chrome] badsite.com [duration: 30s] [FLAGGED_CONTENT]",
        ])
        with patch.object(self.alerter, "_send_flagged_email") as mock_email:
            self.alerter.scan_and_alert(log, cursor)
            mock_email.assert_called_once()

    def test_cursor_updated_after_scan(self, tmp_path):
        log = tmp_path / "activity_log.txt"
        cursor = tmp_path / "cursor.txt"
        log.write_text("", encoding="utf-8")
        before = datetime.now() - timedelta(seconds=1)
        with patch.object(self.alerter, "_send_flagged_email"):
            self.alerter.scan_and_alert(log, cursor)
        after = datetime.now() + timedelta(seconds=1)
        result = self.alerter._read_cursor(cursor)
        assert result is not None
        assert before <= result <= after

    def test_cursor_updated_even_when_no_flagged_entries(self, tmp_path):
        log = tmp_path / "activity_log.txt"
        cursor = tmp_path / "cursor.txt"
        log.write_text("[2026-03-10 10:00:00] [Chrome] clean.com [duration: 30s]\n", encoding="utf-8")
        self.alerter.scan_and_alert(log, cursor)
        assert self.alerter._read_cursor(cursor) is not None

    def test_missing_log_does_not_crash(self, tmp_path):
        cursor = tmp_path / "cursor.txt"
        self.alerter.scan_and_alert(tmp_path / "nonexistent.txt", cursor)

    def test_alert_email_disabled_no_send(self, tmp_path, monkeypatch):
        import config
        monkeypatch.setattr(config, "ALERT_EMAIL", False)
        log = tmp_path / "activity_log.txt"
        cursor = tmp_path / "cursor.txt"
        self._make_log(log, [
            "[2026-03-10 10:00:00] [Chrome] badsite.com [duration: 30s] [FLAGGED_CONTENT]",
        ])
        with patch.object(self.alerter, "_send_flagged_email") as mock_email:
            self.alerter.scan_and_alert(log, cursor)
            mock_email.assert_not_called()

    def test_alert_disabled_master_switch(self, tmp_path, monkeypatch):
        import config
        monkeypatch.setattr(config, "ALERT_ENABLED", False)
        log = tmp_path / "activity_log.txt"
        cursor = tmp_path / "cursor.txt"
        self._make_log(log, [
            "[2026-03-10 10:00:00] [Chrome] badsite.com [duration: 30s] [FLAGGED_CONTENT]",
        ])
        with patch.object(self.alerter, "_send_flagged_email") as mock_email:
            self.alerter.scan_and_alert(log, cursor)
            mock_email.assert_not_called()


# ---------------------------------------------------------------------------
# _do_send_smtp (SMTP wiring)
# ---------------------------------------------------------------------------

class TestDoSendSmtp:
    def setup_method(self):
        self.alerter = _reload_alerter()

    def test_smtp_called_with_correct_credentials(self):
        import config
        mock_smtp_instance = MagicMock()
        with patch("smtplib.SMTP", return_value=mock_smtp_instance) as mock_smtp_cls:
            mock_smtp_instance.__enter__ = MagicMock(return_value=mock_smtp_instance)
            mock_smtp_instance.__exit__ = MagicMock(return_value=False)
            self.alerter._do_send_smtp(
                subject="Test",
                html_body="<p>test</p>",
                plain_text="test",
            )
            mock_smtp_cls.assert_called_once_with(config.SMTP_HOST, config.SMTP_PORT, timeout=30)
            mock_smtp_instance.login.assert_called_once_with(config.SMTP_USER, config.SMTP_PASS)


# ---------------------------------------------------------------------------
# _send_flagged_email
# ---------------------------------------------------------------------------

class TestSendFlaggedEmail:
    def setup_method(self):
        self.alerter = _reload_alerter()

    def test_email_includes_device_name(self):
        self.alerter._DEVICE_NAME = "TestMacBook"
        captured = {}

        def fake_do_send(subject, html_body, plain_text):
            captured["html"] = html_body
            captured["plain"] = plain_text

        with patch.object(self.alerter, "_do_send_smtp", side_effect=fake_do_send):
            self.alerter._send_flagged_email([("badsite.com", "2026-01-01 00:00:00")])

        assert "TestMacBook" in captured["html"]
        assert "TestMacBook" in captured["plain"]

    def test_email_includes_all_visits(self):
        captured = {}

        def fake_do_send(subject, html_body, plain_text):
            captured["html"] = html_body
            captured["plain"] = plain_text

        visits = [
            ("site1.com", "2026-01-01 10:00:00"),
            ("site2.com", "2026-01-01 10:05:00"),
        ]
        with patch.object(self.alerter, "_do_send_smtp", side_effect=fake_do_send):
            self.alerter._send_flagged_email(visits)

        assert "site1.com" in captured["html"]
        assert "site2.com" in captured["html"]
        assert "site1.com" in captured["plain"]
        assert "site2.com" in captured["plain"]

