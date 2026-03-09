"""
Tests for alerter.py.

External I/O is blocked at two levels:

  1. **Session scope** (conftest.py): smtplib.SMTP, smtplib.SMTP_SSL, and
     subprocess.run are patched for the entire pytest session.  This fires
     before any test module is imported and is the primary safety net.

  2. **Function scope** (this module): the `_block_external_io` autouse
     fixture below re-applies the same patches per-test.  This ensures
     individual tests that reload the alerter module still get clean mocks,
     and makes the blocking intent explicit at the module level.

Individual tests that need to assert on SMTP or subprocess behaviour apply
their own inner patch, which stacks safely on top of both autouse layers.
"""

import importlib
import smtplib
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Autouse safety fixture — blocks ALL external I/O for every test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _block_external_io():
    """Prevent any real SMTP or subprocess call from firing during tests."""
    with patch("smtplib.SMTP"), patch("smtplib.SMTP_SSL"), patch("subprocess.run"):
        yield

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reload_alerter():
    """Re-import alerter so module-level state (_BLOCKLIST, _cooldown) is fresh."""
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
        # Inject a fake blocklist entry to avoid relying on the file contents.
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
        # is_adult_domain receives already-lowercased input from check_url;
        # test that it handles lowercase correctly.
        self.alerter._BLOCKLIST = frozenset({"testadultsite.com"})
        assert self.alerter.is_adult_domain("testadultsite.com") is True

    def test_www_stripped_by_check_url_not_is_adult_domain(self):
        # www-stripping happens in check_url; is_adult_domain receives a bare domain.
        self.alerter._BLOCKLIST = frozenset({"testadultsite.com"})
        # Direct call with www. — must still match (www. is just another part after split)
        assert self.alerter.is_adult_domain("www.testadultsite.com") is False  # www not in blocklist


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------

class TestCooldown:
    def setup_method(self):
        self.alerter = _reload_alerter()
        self.alerter._cooldown.clear()

    def test_first_visit_not_on_cooldown(self):
        assert self.alerter._is_on_cooldown("example.com") is False

    def test_after_alert_on_cooldown(self):
        self.alerter._record_alert("example.com")
        assert self.alerter._is_on_cooldown("example.com") is True

    def test_cooldown_expires(self):
        self.alerter._record_alert("example.com")
        # Backdate the cooldown entry beyond the window
        self.alerter._cooldown["example.com"] = self.alerter._cooldown["example.com"] - (self.alerter._COOLDOWN_SECS + 1)
        assert self.alerter._is_on_cooldown("example.com") is False

    def test_different_domains_independent(self):
        self.alerter._record_alert("site-a.com")
        assert self.alerter._is_on_cooldown("site-a.com") is True
        assert self.alerter._is_on_cooldown("site-b.com") is False


# ---------------------------------------------------------------------------
# check_url — URL parsing
# ---------------------------------------------------------------------------

class TestCheckUrlParsing:
    """Verify check_url correctly extracts the domain from various label formats."""

    def setup_method(self):
        self.alerter = _reload_alerter()
        self.alerter._BLOCKLIST = frozenset({"adultexample.com"})
        self.alerter._cooldown.clear()

    def _check_with_mocks(self, label):
        with patch.object(self.alerter, "_send_macos_notification") as mock_notif, \
             patch.object(self.alerter, "_send_alert_email") as mock_email:
            self.alerter.check_url(label)
            return mock_notif, mock_email

    def test_safari_label_format(self):
        mock_notif, mock_email = self._check_with_mocks("[Safari] https://adultexample.com/page")
        mock_notif.assert_called_once_with("adultexample.com")

    def test_chrome_label_format(self):
        mock_notif, _ = self._check_with_mocks("[Google Chrome] https://adultexample.com/")
        mock_notif.assert_called_once_with("adultexample.com")

    def test_www_stripped_before_lookup(self):
        mock_notif, _ = self._check_with_mocks("[Safari] https://www.adultexample.com/")
        mock_notif.assert_called_once_with("adultexample.com")

    def test_clean_domain_no_alert(self):
        mock_notif, mock_email = self._check_with_mocks("[Safari] https://github.com/user/repo")
        mock_notif.assert_not_called()
        mock_email.assert_not_called()

    def test_raw_url_without_browser_prefix(self):
        mock_notif, _ = self._check_with_mocks("https://adultexample.com/video")
        mock_notif.assert_called_once_with("adultexample.com")

    def test_empty_label_no_crash(self):
        # Should not raise even for empty/garbage input
        self.alerter.check_url("")
        self.alerter.check_url("not-a-url")


# ---------------------------------------------------------------------------
# check_url — alert dispatch
# ---------------------------------------------------------------------------

class TestCheckUrlDispatch:
    def setup_method(self):
        self.alerter = _reload_alerter()
        self.alerter._BLOCKLIST = frozenset({"adultexample.com"})
        self.alerter._cooldown.clear()

    def test_both_channels_fire_by_default(self):
        with patch.object(self.alerter, "_send_macos_notification") as mock_notif, \
             patch.object(self.alerter, "_send_alert_email") as mock_email:
            self.alerter.check_url("[Safari] https://adultexample.com/")
            mock_notif.assert_called_once()
            mock_email.assert_called_once()

    def test_notification_disabled(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ADULT_ALERT_NOTIFICATION", False)
        with patch.object(self.alerter, "_send_macos_notification") as mock_notif, \
             patch.object(self.alerter, "_send_alert_email") as mock_email:
            self.alerter.check_url("[Safari] https://adultexample.com/")
            mock_notif.assert_not_called()
            mock_email.assert_called_once()

    def test_email_disabled(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ADULT_ALERT_EMAIL", False)
        with patch.object(self.alerter, "_send_macos_notification") as mock_notif, \
             patch.object(self.alerter, "_send_alert_email") as mock_email:
            self.alerter.check_url("[Safari] https://adultexample.com/")
            mock_notif.assert_called_once()
            mock_email.assert_not_called()

    def test_master_switch_disables_all(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ADULT_ALERT_ENABLED", False)
        with patch.object(self.alerter, "_send_macos_notification") as mock_notif, \
             patch.object(self.alerter, "_send_alert_email") as mock_email:
            self.alerter.check_url("[Safari] https://adultexample.com/")
            mock_notif.assert_not_called()
            mock_email.assert_not_called()

    def test_cooldown_suppresses_second_alert(self):
        with patch.object(self.alerter, "_send_macos_notification") as mock_notif, \
             patch.object(self.alerter, "_send_alert_email"):
            self.alerter.check_url("[Safari] https://adultexample.com/")
            self.alerter.check_url("[Safari] https://adultexample.com/other-page")
        mock_notif.assert_called_once()  # only the first call fires

    def test_alert_fires_again_after_cooldown_expires(self, monkeypatch):
        with patch.object(self.alerter, "_send_macos_notification") as mock_notif, \
             patch.object(self.alerter, "_send_alert_email"):
            self.alerter.check_url("[Safari] https://adultexample.com/")
            # Expire the cooldown by backdating the monotonic timestamp
            self.alerter._cooldown["adultexample.com"] -= (self.alerter._COOLDOWN_SECS + 1)
            self.alerter.check_url("[Safari] https://adultexample.com/")
        assert mock_notif.call_count == 2


# ---------------------------------------------------------------------------
# _send_macos_notification
# ---------------------------------------------------------------------------

class TestSendMacosNotification:
    def setup_method(self):
        self.alerter = _reload_alerter()

    def test_calls_osascript(self):
        with patch("subprocess.run") as mock_run:
            self.alerter._send_macos_notification("badsite.com")
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == "osascript"
            assert "badsite.com" in args[2]


# ---------------------------------------------------------------------------
# _send_alert_email (SMTP)
# ---------------------------------------------------------------------------

class TestSendAlertEmail:
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
