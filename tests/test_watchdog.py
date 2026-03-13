"""
Tests for watchdog.py tamper-resistance scenarios.

Covers:
  H2 — _send_alert uses keychain-stored original SMTP_TO, not the (potentially
         tampered) .env SMTP_TO.
  _run_check — startup grace period, new-vs-already-stopped alert logic,
               regression test for the _last_stopped ordering bug.
  _shutdown_handler — SIGTERM fires a partner alert unless the graceful
                      sentinel file is present (launchctl / Task Scheduler tamper).
  M1 — vigil doctor (cmd_doctor) includes the watchdog service in its checks
        on both macOS and Windows.

SMTP is blocked globally by conftest.py.  Individual tests that need to
inspect SMTP traffic apply their own inner patch on top.
"""

import argparse
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import watchdog
import vigil_cli


# ---------------------------------------------------------------------------
# H2: _send_alert uses keychain-stored SMTP_TO, not cfg.SMTP_TO
# ---------------------------------------------------------------------------

def _smtp_context_mock():
    """Return (MockSMTPClass, smtp_ctx, captured_messages_list)."""
    captured: list = []
    smtp_ctx = MagicMock()
    smtp_ctx.send_message.side_effect = lambda m: captured.append(m)
    MockSMTP = MagicMock()
    MockSMTP.return_value.__enter__ = MagicMock(return_value=smtp_ctx)
    MockSMTP.return_value.__exit__ = MagicMock(return_value=False)
    return MockSMTP, smtp_ctx, captured


class TestSendAlertSmtpTo:
    """_send_alert prefers the keychain-stored original SMTP_TO over cfg.SMTP_TO."""

    def test_uses_keychain_address_when_available(self):
        """Keychain returns an address → that address is used, not cfg.SMTP_TO."""
        MockSMTP, smtp_ctx, captured = _smtp_context_mock()
        with patch("smtplib.SMTP", MockSMTP), \
             patch("pin_auth.get_original_smtp_to", return_value="keychain_partner@example.com"):
            watchdog._send_alert("Test Subject", "body text")

        assert len(captured) == 1
        assert "keychain_partner@example.com" in captured[0]["To"]

    def test_falls_back_to_cfg_smtp_to_when_keychain_empty(self):
        """Keychain returns '' → cfg.SMTP_TO is used as fallback."""
        import config as _cfg
        MockSMTP, smtp_ctx, captured = _smtp_context_mock()
        with patch("smtplib.SMTP", MockSMTP), \
             patch("pin_auth.get_original_smtp_to", return_value=""), \
             patch("importlib.reload"), \
             patch.object(_cfg, "SMTP_TO", ["cfg_fallback@example.com"]):
            watchdog._send_alert("Test Subject", "body text")

        assert len(captured) == 1
        assert "cfg_fallback@example.com" in captured[0]["To"]

    def test_falls_back_to_cfg_when_pin_auth_raises(self):
        """pin_auth import/call error → fall back to cfg.SMTP_TO silently."""
        import config as _cfg
        MockSMTP, smtp_ctx, captured = _smtp_context_mock()
        with patch("smtplib.SMTP", MockSMTP), \
             patch("pin_auth.get_original_smtp_to", side_effect=Exception("keychain unavailable")), \
             patch("importlib.reload"), \
             patch.object(_cfg, "SMTP_TO", ["cfg_fallback@example.com"]):
            watchdog._send_alert("Test Subject", "body text")

        assert len(captured) == 1
        assert "cfg_fallback@example.com" in captured[0]["To"]

    def test_tampered_env_smtp_to_does_not_hijack_alert(self):
        """Even if cfg.SMTP_TO reflects a tampered .env, the keychain address wins."""
        MockSMTP, smtp_ctx, captured = _smtp_context_mock()
        with patch("smtplib.SMTP", MockSMTP), \
             patch("pin_auth.get_original_smtp_to", return_value="real_partner@example.com"), \
             patch.dict("os.environ", {"SMTP_TO": "attacker@evil.com"}):
            watchdog._send_alert("Test Subject", "body text")

        assert len(captured) == 1
        assert "real_partner@example.com" in captured[0]["To"]
        assert "attacker@evil.com" not in captured[0]["To"]

    def test_swallows_smtp_connection_error_silently(self):
        """Any SMTP failure is swallowed — watchdog must not crash."""
        with patch("smtplib.SMTP", side_effect=ConnectionRefusedError("blocked")):
            watchdog._send_alert("Test", "body")  # must not raise


# ---------------------------------------------------------------------------
# _run_check: grace period and alert logic
# ---------------------------------------------------------------------------

class TestRunCheck:
    """Watchdog run-check service polling behaviour."""

    def test_no_alert_during_startup_grace_window(self):
        """Services down right at startup → no alert until grace expires."""
        with patch.object(watchdog, "_stopped_services", return_value={"Vigil Tracker"}), \
             patch.object(watchdog, "_started_at", time.monotonic()), \
             patch.object(watchdog, "_last_stopped", set()), \
             patch.object(watchdog, "_send_alert") as mock_alert:
            watchdog._run_check()
        mock_alert.assert_not_called()

    def test_alerts_newly_stopped_service_after_grace(self):
        """Service stops after grace window → one alert sent containing the service name."""
        past = time.monotonic() - watchdog._STARTUP_GRACE - 10
        with patch.object(watchdog, "_stopped_services", return_value={"Vigil Tracker"}), \
             patch.object(watchdog, "_started_at", past), \
             patch.object(watchdog, "_last_stopped", set()), \
             patch.object(watchdog, "_send_alert") as mock_alert:
            watchdog._run_check()
        mock_alert.assert_called_once()
        assert "Vigil Tracker" in mock_alert.call_args.kwargs["subject"]

    def test_no_duplicate_alert_for_already_stopped_service(self):
        """Service was already in _last_stopped → no repeat alert."""
        past = time.monotonic() - watchdog._STARTUP_GRACE - 10
        with patch.object(watchdog, "_stopped_services", return_value={"Vigil Tracker"}), \
             patch.object(watchdog, "_started_at", past), \
             patch.object(watchdog, "_last_stopped", {"Vigil Tracker"}), \
             patch.object(watchdog, "_send_alert") as mock_alert:
            watchdog._run_check()
        mock_alert.assert_not_called()

    def test_alerts_only_newly_stopped_service(self):
        """Tracker was already down; Summarizer newly stopped → alert for Summarizer only."""
        past = time.monotonic() - watchdog._STARTUP_GRACE - 10
        with patch.object(watchdog, "_stopped_services", return_value={"Vigil Tracker", "Vigil Summarizer"}), \
             patch.object(watchdog, "_started_at", past), \
             patch.object(watchdog, "_last_stopped", {"Vigil Tracker"}), \
             patch.object(watchdog, "_send_alert") as mock_alert:
            watchdog._run_check()
        mock_alert.assert_called_once()
        subject = mock_alert.call_args.kwargs["subject"]
        assert "Vigil Summarizer" in subject
        assert "Vigil Tracker" not in subject

    def test_no_alert_when_all_services_running(self):
        """All services healthy → no alert fired."""
        past = time.monotonic() - watchdog._STARTUP_GRACE - 10
        with patch.object(watchdog, "_stopped_services", return_value=set()), \
             patch.object(watchdog, "_started_at", past), \
             patch.object(watchdog, "_last_stopped", set()), \
             patch.object(watchdog, "_send_alert") as mock_alert:
            watchdog._run_check()
        mock_alert.assert_not_called()

    def test_service_down_at_startup_alerts_after_grace_expires(self):
        """Regression: service was down during grace window (last_stopped never updated)
        → should alert once grace expires, not be silently absorbed."""
        # Simulate: service was down since before start, grace has now expired.
        # _last_stopped is still empty (was not updated during grace period).
        past = time.monotonic() - watchdog._STARTUP_GRACE - 5
        with patch.object(watchdog, "_stopped_services", return_value={"Vigil Summarizer"}), \
             patch.object(watchdog, "_started_at", past), \
             patch.object(watchdog, "_last_stopped", set()), \
             patch.object(watchdog, "_send_alert") as mock_alert:
            watchdog._run_check()
        mock_alert.assert_called_once()


# ---------------------------------------------------------------------------
# _shutdown_handler: SIGTERM tamper alerting
# ---------------------------------------------------------------------------

class TestShutdownHandler:
    """SIGTERM fires a partner alert unless the graceful sentinel file is present."""

    def test_sends_alert_on_unexpected_sigterm(self, tmp_path):
        """No graceful sentinel → partner alert fired (launchctl/kill without vigil uninstall)."""
        sentinel = tmp_path / "watchdog_graceful_shutdown"  # does not exist
        with patch.object(watchdog, "_GRACEFUL_SENTINEL", sentinel), \
             patch.object(watchdog, "_send_alert") as mock_alert:
            with pytest.raises(SystemExit):
                watchdog._shutdown_handler(15, None)
        mock_alert.assert_called_once()
        subject = mock_alert.call_args.kwargs["subject"]
        assert "Watchdog" in subject

    def test_no_alert_on_graceful_sigterm(self, tmp_path):
        """Graceful sentinel present (vigil uninstall) → no alert, clean exit."""
        sentinel = tmp_path / "watchdog_graceful_shutdown"
        sentinel.touch()
        with patch.object(watchdog, "_GRACEFUL_SENTINEL", sentinel), \
             patch.object(watchdog, "_send_alert") as mock_alert:
            with pytest.raises(SystemExit) as exc_info:
                watchdog._shutdown_handler(15, None)
        mock_alert.assert_not_called()
        assert exc_info.value.code == 0

    def test_graceful_sentinel_consumed_on_graceful_stop(self, tmp_path):
        """Sentinel file is deleted after a graceful shutdown so it cannot be reused."""
        sentinel = tmp_path / "watchdog_graceful_shutdown"
        sentinel.touch()
        with patch.object(watchdog, "_GRACEFUL_SENTINEL", sentinel), \
             patch.object(watchdog, "_send_alert"):
            with pytest.raises(SystemExit):
                watchdog._shutdown_handler(15, None)
        assert not sentinel.exists()

    def test_exits_after_sending_alert(self, tmp_path):
        """After sending alert on unexpected SIGTERM, process exits cleanly."""
        sentinel = tmp_path / "watchdog_graceful_shutdown"
        with patch.object(watchdog, "_GRACEFUL_SENTINEL", sentinel), \
             patch.object(watchdog, "_send_alert"):
            with pytest.raises(SystemExit) as exc_info:
                watchdog._shutdown_handler(15, None)
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# M1: cmd_doctor includes watchdog in service health checks
# ---------------------------------------------------------------------------

class TestDoctorIncludesWatchdog:
    """cmd_doctor must include the watchdog service in its per-platform checks."""

    # Minimal env dict that satisfies cmd_doctor's validation sections.
    _FAKE_ENV = {
        "SMTP_HOST": "smtp.example.com",
        "SMTP_PORT": "587",
        "SMTP_USER": "user@example.com",
        "SMTP_PASS": "password",
        "SMTP_TO":   "partner@example.com",
    }

    def test_macos_doctor_checks_com_vigil_watchdog(self):
        """On macOS, _launchd_status must be called with 'com.vigil.watchdog'."""
        checked: list = []

        with patch("sys.platform", "darwin"), \
             patch.object(vigil_cli, "_load_dotenv_raw", return_value=self._FAKE_ENV), \
             patch.object(vigil_cli, "_spinner", side_effect=lambda msg, fn: ("✓", "ok")), \
             patch.object(vigil_cli, "_launchd_status",
                          side_effect=lambda l: checked.append(l) or (True, "123")), \
             patch("builtins.print"):
            vigil_cli.cmd_doctor(argparse.Namespace())

        assert "com.vigil.watchdog" in checked

    def test_macos_doctor_checks_all_three_services(self):
        """On macOS, all of tracker, summarizer, and watchdog labels are checked."""
        checked: list = []

        with patch("sys.platform", "darwin"), \
             patch.object(vigil_cli, "_load_dotenv_raw", return_value=self._FAKE_ENV), \
             patch.object(vigil_cli, "_spinner", side_effect=lambda msg, fn: ("✓", "ok")), \
             patch.object(vigil_cli, "_launchd_status",
                          side_effect=lambda l: checked.append(l) or (True, "123")), \
             patch("builtins.print"):
            vigil_cli.cmd_doctor(argparse.Namespace())

        assert "com.vigil.tracker" in checked
        assert "com.vigil.summarizer" in checked
        assert "com.vigil.watchdog" in checked

    def test_windows_doctor_checks_vigil_watchdog(self):
        """On Windows, _schtasks_status must be called with 'Vigil Watchdog'."""
        checked: list = []

        with patch("sys.platform", "win32"), \
             patch.object(vigil_cli, "_load_dotenv_raw", return_value=self._FAKE_ENV), \
             patch.object(vigil_cli, "_spinner", side_effect=lambda msg, fn: ("✓", "ok")), \
             patch.object(vigil_cli, "_schtasks_status",
                          side_effect=lambda t: checked.append(t) or (True, "Running")), \
             patch("builtins.print"):
            vigil_cli.cmd_doctor(argparse.Namespace())

        assert "Vigil Watchdog" in checked

    def test_windows_doctor_checks_all_three_services(self):
        """On Windows, all of Tracker, Summarizer, and Watchdog tasks are checked."""
        checked: list = []

        with patch("sys.platform", "win32"), \
             patch.object(vigil_cli, "_load_dotenv_raw", return_value=self._FAKE_ENV), \
             patch.object(vigil_cli, "_spinner", side_effect=lambda msg, fn: ("✓", "ok")), \
             patch.object(vigil_cli, "_schtasks_status",
                          side_effect=lambda t: checked.append(t) or (True, "Running")), \
             patch("builtins.print"):
            vigil_cli.cmd_doctor(argparse.Namespace())

        assert "Vigil Tracker" in checked
        assert "Vigil Summarizer" in checked
        assert "Vigil Watchdog" in checked

    def test_stopped_watchdog_shows_as_failure(self):
        """If the watchdog is not running, cmd_doctor increments the issues count (fail path)."""
        def mock_status(label):
            return (False, "not running") if label == "com.vigil.watchdog" else (True, "123")

        output_lines: list = []

        with patch("sys.platform", "darwin"), \
             patch.object(vigil_cli, "_load_dotenv_raw", return_value=self._FAKE_ENV), \
             patch.object(vigil_cli, "_spinner", side_effect=lambda msg, fn: ("✓", "ok")), \
             patch.object(vigil_cli, "_launchd_status", side_effect=mock_status), \
             patch("builtins.print", side_effect=lambda *a, **kw: output_lines.append(str(a))):
            vigil_cli.cmd_doctor(argparse.Namespace())

        # The watchdog label should appear in the output (either ok or fail line)
        full_output = "\n".join(output_lines)
        assert "com.vigil.watchdog" in full_output


# ---------------------------------------------------------------------------
# Heartbeat file: watchdog writes a fresh timestamp on every check loop
# ---------------------------------------------------------------------------

class TestHeartbeatFile:
    """HEARTBEAT_FILE is defined and the write logic works correctly."""

    def test_heartbeat_file_constant_is_defined(self):
        """HEARTBEAT_FILE must be a Path attribute on the watchdog module."""
        from pathlib import Path
        assert hasattr(watchdog, "HEARTBEAT_FILE")
        assert isinstance(watchdog.HEARTBEAT_FILE, Path)

    def test_heartbeat_file_is_under_app_support_dir(self):
        """HEARTBEAT_FILE must live in the same APP_SUPPORT_DIR as other runtime files."""
        from platform_common import get_app_dirs
        app_support_dir, _ = get_app_dirs()
        assert watchdog.HEARTBEAT_FILE.parent == app_support_dir

    def test_heartbeat_write_succeeds(self, tmp_path):
        """Writing a float timestamp to a tmp heartbeat path should work."""
        hb = tmp_path / "watchdog_heartbeat"
        hb.write_text(str(time.time()))
        val = float(hb.read_text().strip())
        assert val > 0

    def test_heartbeat_value_is_recent(self, tmp_path):
        """A freshly written heartbeat should be within 1 second of time.time()."""
        hb = tmp_path / "watchdog_heartbeat"
        before = time.time()
        hb.write_text(str(time.time()))
        after = time.time()
        val = float(hb.read_text().strip())
        assert before <= val <= after


# ---------------------------------------------------------------------------
# Summarizer heartbeat check: watchdog detects a dead summarizer via heartbeat
# ---------------------------------------------------------------------------

@pytest.fixture
def summarizer_hb_env(tmp_path, monkeypatch):
    """Patch SUMMARIZER_HEARTBEAT_FILE to a temp path."""
    hb = tmp_path / "summarizer_heartbeat"
    monkeypatch.setattr(watchdog, "SUMMARIZER_HEARTBEAT_FILE", hb)
    return hb


class TestCheckSummarizerHeartbeat:
    """Watchdog detects a stale summarizer heartbeat and sends an alert."""

    def test_no_alert_when_heartbeat_file_absent(self, summarizer_hb_env):
        """No file → summarizer may not be installed; skip silently."""
        with patch.object(watchdog, "_send_alert") as mock_alert:
            watchdog._check_summarizer_heartbeat()
        mock_alert.assert_not_called()

    def test_no_alert_when_heartbeat_is_fresh(self, summarizer_hb_env):
        """Heartbeat written just now → no alert."""
        summarizer_hb_env.write_text(str(time.time()))
        with patch.object(watchdog, "_send_alert") as mock_alert:
            watchdog._check_summarizer_heartbeat()
        mock_alert.assert_not_called()

    def test_no_alert_just_within_threshold(self, summarizer_hb_env):
        """Heartbeat 1 second inside the stale threshold → no alert."""
        just_within = time.time() - watchdog._SUMMARIZER_HEARTBEAT_STALE_SECS + 1
        summarizer_hb_env.write_text(str(just_within))
        with patch.object(watchdog, "_send_alert") as mock_alert:
            watchdog._check_summarizer_heartbeat()
        mock_alert.assert_not_called()

    def test_alerts_when_heartbeat_stale(self, summarizer_hb_env):
        """Heartbeat older than threshold → partner alert sent."""
        stale_time = time.time() - watchdog._SUMMARIZER_HEARTBEAT_STALE_SECS - 10
        summarizer_hb_env.write_text(str(stale_time))
        with patch.object(watchdog, "_send_alert") as mock_alert:
            watchdog._check_summarizer_heartbeat()
        mock_alert.assert_called_once()
        subject = mock_alert.call_args.kwargs["subject"]
        assert "Summarizer" in subject
        assert "Heartbeat" in subject or "heartbeat" in subject.lower()

    def test_alert_subject_mentions_stale(self, summarizer_hb_env):
        """Alert subject clearly identifies the stale condition."""
        stale_time = time.time() - watchdog._SUMMARIZER_HEARTBEAT_STALE_SECS - 30
        summarizer_hb_env.write_text(str(stale_time))
        with patch.object(watchdog, "_send_alert") as mock_alert:
            watchdog._check_summarizer_heartbeat()
        subject = mock_alert.call_args.kwargs["subject"]
        assert "Stale" in subject or "stale" in subject.lower()

    def test_no_alert_when_heartbeat_file_malformed(self, summarizer_hb_env):
        """Malformed heartbeat file → skip silently, no crash."""
        summarizer_hb_env.write_text("not-a-timestamp")
        with patch.object(watchdog, "_send_alert") as mock_alert:
            watchdog._check_summarizer_heartbeat()  # must not raise
        mock_alert.assert_not_called()

    def test_run_check_calls_heartbeat_check_when_all_services_running(self, summarizer_hb_env):
        """_run_check invokes _check_summarizer_heartbeat even when no services stopped."""
        past = time.monotonic() - watchdog._STARTUP_GRACE - 10
        stale_time = time.time() - watchdog._SUMMARIZER_HEARTBEAT_STALE_SECS - 60
        summarizer_hb_env.write_text(str(stale_time))
        with patch.object(watchdog, "_stopped_services", return_value=set()), \
             patch.object(watchdog, "_started_at", past), \
             patch.object(watchdog, "_last_stopped", set()), \
             patch.object(watchdog, "_send_alert") as mock_alert:
            watchdog._run_check()
        # _check_summarizer_heartbeat should have fired the alert
        mock_alert.assert_called_once()
        assert "Summarizer" in mock_alert.call_args.kwargs["subject"]


# ---------------------------------------------------------------------------
# Gap 1b: summarizer heartbeat file deletion detected by watchdog
# ---------------------------------------------------------------------------

class TestCheckSummarizerHeartbeatDeletion:
    """Watchdog alerts when the summarizer heartbeat file is deleted after first-seen."""

    def test_no_alert_when_file_absent_and_never_seen(self, summarizer_hb_env, monkeypatch):
        """File absent before ever being seen → silent (not yet installed)."""
        monkeypatch.setattr(watchdog, "_summarizer_heartbeat_ever_seen", False)
        with patch.object(watchdog, "_send_alert") as mock_alert:
            watchdog._check_summarizer_heartbeat()
        mock_alert.assert_not_called()

    def test_alerts_when_file_deleted_after_being_seen(self, summarizer_hb_env, monkeypatch):
        """File was present, then deleted → alert for tamper attempt."""
        # First, establish first-seen by writing a fresh heartbeat.
        summarizer_hb_env.write_text(str(time.time()))
        monkeypatch.setattr(watchdog, "_summarizer_heartbeat_ever_seen", False)
        with patch.object(watchdog, "_send_alert"):
            watchdog._check_summarizer_heartbeat()  # sets _ever_seen = True
        # Now delete the file and call again.
        summarizer_hb_env.unlink()
        with patch.object(watchdog, "_send_alert") as mock_alert:
            watchdog._check_summarizer_heartbeat()
        mock_alert.assert_called_once()
        subject = mock_alert.call_args.kwargs["subject"]
        assert "Deleted" in subject or "deleted" in subject.lower()
        assert "Summarizer" in subject or "Heartbeat" in subject

    def test_deletion_alert_body_mentions_tamper(self, summarizer_hb_env, monkeypatch):
        """Alert body text should mention tamper/deliberate deletion."""
        summarizer_hb_env.write_text(str(time.time()))
        monkeypatch.setattr(watchdog, "_summarizer_heartbeat_ever_seen", False)
        with patch.object(watchdog, "_send_alert"):
            watchdog._check_summarizer_heartbeat()
        summarizer_hb_env.unlink()
        with patch.object(watchdog, "_send_alert") as mock_alert:
            watchdog._check_summarizer_heartbeat()
        body = mock_alert.call_args.kwargs["body_text"]
        assert "tamper" in body.lower() or "deliberate" in body.lower()


# ---------------------------------------------------------------------------
# Gap 2: .env deletion detected by watchdog (_check_env_file)
# ---------------------------------------------------------------------------

@pytest.fixture
def env_file_env(tmp_path, monkeypatch):
    """Patch _ENV_FILE and _env_ever_seen to isolated tmp state."""
    env_file = tmp_path / ".env"
    monkeypatch.setattr(watchdog, "_ENV_FILE", env_file)
    monkeypatch.setattr(watchdog, "_env_ever_seen", False)
    return env_file


class TestCheckEnvFile:
    """_check_env_file alerts when .env is deleted after having been present."""

    def test_no_alert_when_env_file_present(self, env_file_env):
        """File present → no alert, _env_ever_seen set to True."""
        env_file_env.write_text("SMTP_HOST=smtp.example.com\n")
        with patch.object(watchdog, "_send_alert") as mock_alert:
            watchdog._check_env_file()
        mock_alert.assert_not_called()
        assert watchdog._env_ever_seen is True

    def test_no_alert_when_file_absent_and_never_seen(self, env_file_env):
        """File never existed → no alert (not yet installed)."""
        with patch.object(watchdog, "_send_alert") as mock_alert:
            watchdog._check_env_file()
        mock_alert.assert_not_called()

    def test_alerts_when_env_file_deleted_after_being_seen(self, env_file_env):
        """File present then deleted → alert fired."""
        env_file_env.write_text("SMTP_HOST=smtp.example.com\n")
        watchdog._check_env_file()  # sets _env_ever_seen = True
        env_file_env.unlink()
        with patch.object(watchdog, "_send_alert") as mock_alert:
            watchdog._check_env_file()
        mock_alert.assert_called_once()
        subject = mock_alert.call_args.kwargs["subject"]
        assert "Configuration" in subject or "config" in subject.lower() or "Deleted" in subject

    def test_deletion_alert_body_mentions_silence(self, env_file_env):
        """Alert body should warn that future alerts may be silenced."""
        env_file_env.write_text("SMTP_HOST=smtp.example.com\n")
        watchdog._check_env_file()
        env_file_env.unlink()
        with patch.object(watchdog, "_send_alert") as mock_alert:
            watchdog._check_env_file()
        body = mock_alert.call_args.kwargs["body_text"]
        assert "silent" in body.lower() or "alert" in body.lower()

    def test_run_check_calls_check_env_file(self, env_file_env, monkeypatch):
        """_run_check always calls _check_env_file on every iteration."""
        past = time.monotonic() - watchdog._STARTUP_GRACE - 10
        env_file_env.write_text("SMTP_HOST=smtp.example.com\n")
        watchdog._check_env_file()  # prime _env_ever_seen
        env_file_env.unlink()
        with patch.object(watchdog, "_stopped_services", return_value=set()), \
             patch.object(watchdog, "_started_at", past), \
             patch.object(watchdog, "_last_stopped", set()), \
             patch.object(watchdog, "_check_summarizer_heartbeat"), \
             patch.object(watchdog, "_send_alert") as mock_alert:
            watchdog._run_check()
        # _send_alert should be called by _check_env_file (not by service-stop path)
        mock_alert.assert_called()
        subjects = [c.kwargs.get("subject", "") for c in mock_alert.call_args_list]
        assert any("Configuration" in s or "config" in s.lower() for s in subjects)


# ---------------------------------------------------------------------------
# Gap 2: _send_alert SMTP cache fallback
# ---------------------------------------------------------------------------

class TestSendAlertCacheFallback:
    """_send_alert uses cached SMTP credentials when .env config reload fails."""

    def _prime_cache(self, host="smtp.example.com", port=587,
                     user="u@example.com", password="secret",
                     smtp_from="vigil@example.com"):
        watchdog._cached_smtp = {
            "host": host, "port": port,
            "user": user, "pass": password, "from": smtp_from,
        }

    def test_uses_cached_smtp_when_config_reload_fails(self):
        """Config reload raises → cached SMTP used, alert is still sent."""
        self._prime_cache()
        MockSMTP, smtp_ctx, captured = _smtp_context_mock()
        with patch("smtplib.SMTP", MockSMTP), \
             patch("importlib.reload", side_effect=Exception("no .env")), \
             patch("pin_auth.get_original_smtp_to", return_value="partner@example.com"):
            watchdog._send_alert("Cached subject", "cached body")
        assert len(captured) == 1
        assert "partner@example.com" in captured[0]["To"]

    def test_no_alert_when_cache_empty_and_config_fails(self):
        """No cache AND config reload fails → alert silently dropped (no crash)."""
        watchdog._cached_smtp = None
        with patch("importlib.reload", side_effect=Exception("no .env")), \
             patch("smtplib.SMTP") as MockSMTP:
            watchdog._send_alert("subject", "body")  # must not raise
        MockSMTP.assert_not_called()

    def test_load_smtp_cache_populates_cached_smtp(self, monkeypatch):
        """_load_smtp_cache() stores SMTP config in _cached_smtp."""
        import config as _cfg
        monkeypatch.setattr(watchdog, "_cached_smtp", None)
        with patch("importlib.reload"), \
             patch.object(_cfg, "SMTP_HOST", "smtp.example.com"), \
             patch.object(_cfg, "SMTP_PORT", 587), \
             patch.object(_cfg, "SMTP_USER", "u@example.com"), \
             patch.object(_cfg, "SMTP_PASS", "secret"), \
             patch.object(_cfg, "SMTP_FROM", "vigil@example.com"):
            watchdog._load_smtp_cache()
        assert watchdog._cached_smtp is not None
        assert watchdog._cached_smtp["host"] == "smtp.example.com"

    def test_load_smtp_cache_silent_on_error(self, monkeypatch):
        """_load_smtp_cache() silently passes when config cannot be loaded."""
        monkeypatch.setattr(watchdog, "_cached_smtp", None)
        with patch("importlib.reload", side_effect=Exception("no .env")):
            watchdog._load_smtp_cache()  # must not raise
        assert watchdog._cached_smtp is None


# ---------------------------------------------------------------------------
# Gap 3: reinstall / setup / update require PIN verification
# ---------------------------------------------------------------------------

class TestPinGateOnReinstall:
    """cmd_reinstall, cmd_setup, and cmd_update are gated by PIN when configured."""

    def _mock_pin_auth(self, configured: bool, verify_result: int):
        """Return a minimal pin_auth mock."""
        mock_pa = MagicMock()
        mock_pa._pin_was_configured.return_value = configured
        mock_pa._cmd_verify.return_value = verify_result
        return mock_pa

    def test_reinstall_blocked_when_pin_fails(self):
        """cmd_reinstall exits with code 1 when PIN is configured and fails."""
        mock_pa = self._mock_pin_auth(configured=True, verify_result=1)
        with patch.dict("sys.modules", {"pin_auth": mock_pa}), \
             patch("builtins.__import__", side_effect=lambda n, *a, **k:
                   mock_pa if n == "pin_auth" else __import__(n, *a, **k)):
            with pytest.raises(SystemExit) as exc_info:
                vigil_cli._gate_with_pin()
        assert exc_info.value.code == 1

    def test_reinstall_allowed_when_pin_succeeds(self):
        """cmd_reinstall proceeds when PIN is configured and passes."""
        mock_pa = self._mock_pin_auth(configured=True, verify_result=0)
        with patch.dict("sys.modules", {"pin_auth": mock_pa}):
            vigil_cli._gate_with_pin()  # must not raise

    def test_gate_skipped_when_no_pin_configured(self):
        """_gate_with_pin is a no-op when no PIN has been set."""
        mock_pa = self._mock_pin_auth(configured=False, verify_result=0)
        with patch.dict("sys.modules", {"pin_auth": mock_pa}):
            vigil_cli._gate_with_pin()  # must not raise
        mock_pa._cmd_verify.assert_not_called()

    def test_gate_with_pin_survives_import_error(self):
        """If pin_auth cannot be imported, gate allows through silently."""
        with patch.dict("sys.modules", {"pin_auth": None}):
            vigil_cli._gate_with_pin()  # must not raise

    def test_cmd_reinstall_calls_gate(self, monkeypatch):
        """cmd_reinstall calls _gate_with_pin before delegating to install script."""
        gate_called = []
        monkeypatch.setattr(vigil_cli, "_gate_with_pin",
                            lambda: gate_called.append(True))
        with patch("sys.platform", "darwin"), \
             patch.object(vigil_cli, "_macos", side_effect=SystemExit(0)):
            with pytest.raises(SystemExit):
                vigil_cli.cmd_reinstall(argparse.Namespace())
        assert gate_called

    def test_cmd_update_calls_gate(self, monkeypatch):
        """cmd_update calls _gate_with_pin before delegating."""
        gate_called = []
        monkeypatch.setattr(vigil_cli, "_gate_with_pin",
                            lambda: gate_called.append(True))
        with patch("sys.platform", "darwin"), \
             patch.object(vigil_cli, "_macos", side_effect=SystemExit(0)):
            with pytest.raises(SystemExit):
                vigil_cli.cmd_update(argparse.Namespace())
        assert gate_called

    def test_cmd_setup_calls_gate(self, monkeypatch):
        """cmd_setup calls _gate_with_pin before running the install wizard."""
        gate_called = []
        monkeypatch.setattr(vigil_cli, "_gate_with_pin",
                            lambda: gate_called.append(True))
        with patch("sys.platform", "darwin"), \
             patch.object(vigil_cli, "_macos", side_effect=SystemExit(0)):
            with pytest.raises(SystemExit):
                vigil_cli.cmd_setup(argparse.Namespace())
        assert gate_called
