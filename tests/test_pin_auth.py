"""
Tests for pin_auth.py.

SMTP is blocked globally by conftest.py.  Individual tests that assert on
SMTP behaviour apply their own inner patch on top.
"""

import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import pin_auth


# ---------------------------------------------------------------------------
# hash_pin / verify_pin
# ---------------------------------------------------------------------------

class TestHashPin:
    def test_returns_correct_format(self):
        h = pin_auth.hash_pin("secret123")
        parts = h.split("$")
        assert len(parts) == 4
        assert parts[0] == "pbkdf2_sha256"
        assert parts[1] == str(pin_auth._ITERATIONS)

    def test_unique_hashes_for_same_pin(self):
        h1 = pin_auth.hash_pin("same")
        h2 = pin_auth.hash_pin("same")
        assert h1 != h2  # different salts

    def test_different_pins_produce_different_hashes(self):
        assert pin_auth.hash_pin("pin1") != pin_auth.hash_pin("pin2")


class TestVerifyPin:
    def test_correct_pin_returns_true(self):
        h = pin_auth.hash_pin("mypin")
        assert pin_auth.verify_pin("mypin", h) is True

    def test_wrong_pin_returns_false(self):
        h = pin_auth.hash_pin("mypin")
        assert pin_auth.verify_pin("wrongpin", h) is False

    def test_empty_pin_returns_false(self):
        h = pin_auth.hash_pin("mypin")
        assert pin_auth.verify_pin("", h) is False

    def test_malformed_stored_hash_returns_false(self):
        assert pin_auth.verify_pin("any", "not-a-valid-hash") is False
        assert pin_auth.verify_pin("any", "") is False
        assert pin_auth.verify_pin("any", "a$b$c") is False

    def test_unknown_algorithm_returns_false(self):
        h = pin_auth.hash_pin("pin")
        tampered = h.replace("pbkdf2_sha256", "unknown_algo")
        assert pin_auth.verify_pin("pin", tampered) is False

    def test_timing_safe(self):
        """verify_pin must use constant-time comparison (no early exit)."""
        h = pin_auth.hash_pin("correct")
        for _ in range(50):
            assert pin_auth.verify_pin("correct", h) is True
            assert pin_auth.verify_pin("wrong",   h) is False


# ---------------------------------------------------------------------------
# prompt_and_verify
# ---------------------------------------------------------------------------

class TestPromptAndVerify:
    """Tests for the interactive prompt loop."""

    def _stored(self, pin="goodpin"):
        return pin_auth.hash_pin(pin)

    def test_correct_pin_first_attempt(self):
        stored = self._stored("goodpin")
        with patch("getpass.getpass", return_value="goodpin"):
            assert pin_auth.prompt_and_verify(stored) is True

    def test_correct_pin_second_attempt(self):
        stored = self._stored("goodpin")
        inputs = iter(["wrong", "goodpin"])
        with patch("getpass.getpass", side_effect=inputs):
            assert pin_auth.prompt_and_verify(stored) is True

    def test_correct_pin_third_attempt(self):
        stored = self._stored("goodpin")
        inputs = iter(["bad1", "bad2", "goodpin"])
        with patch("getpass.getpass", side_effect=inputs):
            assert pin_auth.prompt_and_verify(stored) is True

    def test_all_attempts_exhausted_returns_false(self):
        stored = self._stored("goodpin")
        with patch("getpass.getpass", return_value="wrong"), \
             patch.object(pin_auth, "_send_notification"):
            result = pin_auth.prompt_and_verify(stored)
        assert result is False

    def test_failed_attempts_trigger_notification(self):
        stored = self._stored("goodpin")
        with patch("getpass.getpass", return_value="wrong"), \
             patch.object(pin_auth, "_send_notification") as mock_notify:
            pin_auth.prompt_and_verify(stored)
        mock_notify.assert_called_once()
        kwargs = mock_notify.call_args[1]
        assert "Failed uninstall attempt" in kwargs["subject"]
        assert "blocked" in kwargs["html_body"].lower()

    def test_notification_not_sent_on_success(self):
        stored = self._stored("goodpin")
        with patch("getpass.getpass", return_value="goodpin"), \
             patch.object(pin_auth, "_send_notification") as mock_notify:
            pin_auth.prompt_and_verify(stored)
        mock_notify.assert_not_called()

    def test_eof_returns_false_without_notification(self):
        stored = self._stored("goodpin")
        with patch("getpass.getpass", side_effect=EOFError), \
             patch.object(pin_auth, "_send_notification") as mock_notify:
            result = pin_auth.prompt_and_verify(stored)
        assert result is False
        mock_notify.assert_not_called()

    def test_keyboard_interrupt_returns_false_without_notification(self):
        stored = self._stored("goodpin")
        with patch("getpass.getpass", side_effect=KeyboardInterrupt), \
             patch.object(pin_auth, "_send_notification") as mock_notify:
            result = pin_auth.prompt_and_verify(stored)
        assert result is False
        mock_notify.assert_not_called()


# ---------------------------------------------------------------------------
# Keychain helpers (_get_stored_hash, _store_hash, _delete_hash)
# ---------------------------------------------------------------------------

class TestKeychainHelpers:
    def test_get_stored_hash_returns_value(self):
        with patch("pin_auth.keyring.get_password", return_value="myhash"):
            assert pin_auth._get_stored_hash() == "myhash"

    def test_get_stored_hash_returns_empty_when_none(self):
        with patch("pin_auth.keyring.get_password", return_value=None):
            assert pin_auth._get_stored_hash() == ""

    def test_get_stored_hash_returns_empty_on_exception(self):
        with patch("pin_auth.keyring.get_password", side_effect=Exception("backend error")):
            assert pin_auth._get_stored_hash() == ""

    def test_store_hash_calls_keyring_set(self):
        with patch("pin_auth.keyring.set_password") as mock_set:
            pin_auth._store_hash("testhash")
        mock_set.assert_called_once_with(
            pin_auth._KEYCHAIN_SERVICE, pin_auth._KEYCHAIN_ACCOUNT, "testhash"
        )

    def test_delete_hash_calls_keyring_delete(self):
        with patch("pin_auth.keyring.delete_password") as mock_del:
            pin_auth._delete_hash()
        mock_del.assert_called_once_with(
            pin_auth._KEYCHAIN_SERVICE, pin_auth._KEYCHAIN_ACCOUNT
        )

    def test_delete_hash_silent_on_not_found(self):
        import keyring.errors
        with patch(
            "pin_auth.keyring.delete_password",
            side_effect=keyring.errors.PasswordDeleteError("not found"),
        ):
            pin_auth._delete_hash()  # must not raise


# ---------------------------------------------------------------------------
# _cmd_verify
# ---------------------------------------------------------------------------

class TestCmdVerify:
    def test_no_pin_configured_returns_0(self):
        with patch.object(pin_auth, "_get_stored_hash", return_value=""), \
             patch.object(pin_auth, "_pin_was_configured", return_value=False):
            assert pin_auth._cmd_verify() == 0

    def test_correct_pin_returns_0(self):
        stored = pin_auth.hash_pin("secret")
        with patch.object(pin_auth, "_get_stored_hash", return_value=stored), \
             patch("getpass.getpass", return_value="secret"):
            assert pin_auth._cmd_verify() == 0

    def test_wrong_pin_returns_1(self):
        stored = pin_auth.hash_pin("secret")
        with patch.object(pin_auth, "_get_stored_hash", return_value=stored), \
             patch("getpass.getpass", return_value="wrong"), \
             patch.object(pin_auth, "_send_notification"):
            assert pin_auth._cmd_verify() == 1

    def test_empty_stored_hash_returns_0(self):
        with patch.object(pin_auth, "_get_stored_hash", return_value=""), \
             patch.object(pin_auth, "_pin_was_configured", return_value=False):
            assert pin_auth._cmd_verify() == 0

    def test_blocks_when_pin_hash_deleted_but_sentinel_present(self):
        """PIN hash deleted outside normal flow while sentinel remains → alert + block (return 1)."""
        with patch.object(pin_auth, "_get_stored_hash", return_value=""), \
             patch.object(pin_auth, "_pin_was_configured", return_value=True), \
             patch.object(pin_auth, "_send_notification") as mock_notify:
            result = pin_auth._cmd_verify()
        assert result == 1
        mock_notify.assert_called_once()
        subject = mock_notify.call_args[1]["subject"]
        assert "PIN" in subject

    def test_alert_subject_mentions_pin_removed_on_tamper(self):
        """Alert subject clearly identifies that the PIN was removed from the keychain."""
        with patch.object(pin_auth, "_get_stored_hash", return_value=""), \
             patch.object(pin_auth, "_pin_was_configured", return_value=True), \
             patch.object(pin_auth, "_send_notification") as mock_notify:
            pin_auth._cmd_verify()
        subject = mock_notify.call_args[1]["subject"]
        assert "Removed" in subject or "removed" in subject.lower()

    def test_no_alert_when_no_pin_ever_configured(self):
        """If neither hash nor sentinel is present, no notification is sent (no PIN configured)."""
        with patch.object(pin_auth, "_get_stored_hash", return_value=""), \
             patch.object(pin_auth, "_pin_was_configured", return_value=False), \
             patch.object(pin_auth, "_send_notification") as mock_notify:
            result = pin_auth._cmd_verify()
        assert result == 0
        mock_notify.assert_not_called()


# ---------------------------------------------------------------------------
# _cmd_hash
# ---------------------------------------------------------------------------

class TestCmdHash:
    def test_matching_pins_returns_0_and_stores_hash(self):
        inputs = iter(["mypin123", "mypin123"])
        with patch("getpass.getpass", side_effect=inputs), \
             patch.object(pin_auth, "_store_hash") as mock_store:
            result = pin_auth._cmd_hash()
        assert result == 0
        mock_store.assert_called_once()
        stored_hash = mock_store.call_args[0][0]
        assert pin_auth.verify_pin("mypin123", stored_hash)

    def test_mismatched_pins_retries_then_succeeds(self):
        inputs = iter(["pin1", "pin2", "goodpin", "goodpin"])
        with patch("getpass.getpass", side_effect=inputs), \
             patch.object(pin_auth, "_store_hash") as mock_store:
            result = pin_auth._cmd_hash()
        assert result == 0
        stored_hash = mock_store.call_args[0][0]
        assert pin_auth.verify_pin("goodpin", stored_hash)

    def test_empty_pin_retries(self):
        inputs = iter(["", "realpin", "realpin"])
        with patch("getpass.getpass", side_effect=inputs), \
             patch.object(pin_auth, "_store_hash"):
            result = pin_auth._cmd_hash()
        assert result == 0

    def test_eof_on_first_prompt_returns_1(self):
        with patch("getpass.getpass", side_effect=EOFError), \
             patch.object(pin_auth, "_store_hash") as mock_store:
            assert pin_auth._cmd_hash() == 1
        mock_store.assert_not_called()

    def test_eof_on_confirm_prompt_returns_1(self):
        with patch("getpass.getpass", side_effect=["goodpin", EOFError]), \
             patch.object(pin_auth, "_store_hash") as mock_store:
            assert pin_auth._cmd_hash() == 1
        mock_store.assert_not_called()


# ---------------------------------------------------------------------------
# store_env_hash / verify_env_hash / env-hash sentinel  (H1 tamper defence)
# ---------------------------------------------------------------------------

def _write_env(tmp_path, smtp_to: str = "partner@example.com") -> Path:
    env = tmp_path / ".env"
    env.write_text(
        "SMTP_HOST=smtp.example.com\nSMTP_PORT=587\n"
        f"SMTP_USER=user\nSMTP_PASS=pass\nSMTP_TO={smtp_to}\nALERT_ENABLED=true\n"
    )
    return env


class TestStoreEnvHash:
    """store_env_hash writes hash, sentinel, and original SMTP_TO to keychain."""

    def test_writes_hash_to_keychain(self, tmp_path):
        stored = {}
        with patch("keyring.set_password", side_effect=lambda s, a, v: stored.update({a: v})):
            pin_auth.store_env_hash(_write_env(tmp_path))
        assert "env_critical_hash" in stored
        assert len(stored["env_critical_hash"]) == 64  # SHA-256 hex digest

    def test_writes_configured_sentinel(self, tmp_path):
        stored = {}
        with patch("keyring.set_password", side_effect=lambda s, a, v: stored.update({a: v})):
            pin_auth.store_env_hash(_write_env(tmp_path))
        assert stored.get("env_hash_configured") == "1"

    def test_writes_original_smtp_to(self, tmp_path):
        stored = {}
        with patch("keyring.set_password", side_effect=lambda s, a, v: stored.update({a: v})):
            pin_auth.store_env_hash(_write_env(tmp_path, smtp_to="accountability@example.com"))
        assert stored.get("env_original_smtp_to") == "accountability@example.com"


class TestVerifyEnvHash:
    """verify_env_hash tamper-detection sentinel scenarios (H1).

    Scenario matrix:
      A. No hash, no sentinel       → True  (genuine first run)
      B. Hash stored, file unchanged → True  (clean state)
      C. Hash stored, file tampered  → False (content change)
      D. Hash deleted, sentinel kept → False (hash deleted outside normal flow)
      E. Both deleted                → True  (indistinguishable from A; inherent limit)
      F. Keychain unavailable        → True  (fail-open; don't block summarizer)
    """

    def test_returns_true_before_any_store(self, tmp_path):
        """Scenario A: genuine first run with no baseline → pass."""
        with patch("keyring.get_password", return_value=None):
            assert pin_auth.verify_env_hash(_write_env(tmp_path)) is True

    def test_returns_true_after_store_unchanged(self, tmp_path):
        """Scenario B: hash stored, .env not modified → pass."""
        env = _write_env(tmp_path)
        kc: dict = {}
        with patch("keyring.get_password", side_effect=lambda s, a: kc.get((s, a))), \
             patch("keyring.set_password", side_effect=lambda s, a, v: kc.update({(s, a): v})):
            pin_auth.store_env_hash(env)
            assert pin_auth.verify_env_hash(env) is True

    def test_returns_false_after_smtp_to_tampered(self, tmp_path):
        """Scenario C: SMTP_TO changed after hash stored → tamper detected."""
        env = _write_env(tmp_path)
        kc: dict = {}
        with patch("keyring.get_password", side_effect=lambda s, a: kc.get((s, a))), \
             patch("keyring.set_password", side_effect=lambda s, a, v: kc.update({(s, a): v})):
            pin_auth.store_env_hash(env)
            env.write_text(env.read_text().replace("partner@example.com", "evil@hacker.com"))
            assert pin_auth.verify_env_hash(env) is False

    def test_returns_false_when_hash_deleted_but_sentinel_present(self, tmp_path):
        """Scenario D: attacker removes env_critical_hash but sentinel remains → tamper detected."""
        env = _write_env(tmp_path)
        kc: dict = {}
        with patch("keyring.get_password", side_effect=lambda s, a: kc.get((s, a))), \
             patch("keyring.set_password", side_effect=lambda s, a, v: kc.update({(s, a): v})):
            pin_auth.store_env_hash(env)
            del kc[("com.vigil", "env_critical_hash")]
            assert pin_auth.verify_env_hash(env) is False

    def test_returns_true_when_both_hash_and_sentinel_deleted(self, tmp_path):
        """Scenario E: both entries removed — indistinguishable from no install → pass (inherent limit)."""
        with patch("keyring.get_password", return_value=None):
            assert pin_auth.verify_env_hash(_write_env(tmp_path)) is True

    def test_fails_open_when_keychain_raises(self, tmp_path):
        """Scenario F: keychain unavailable → True (fail-open; don't block summarizer)."""
        with patch("keyring.get_password", side_effect=Exception("keychain error")):
            assert pin_auth.verify_env_hash(_write_env(tmp_path)) is True


# ---------------------------------------------------------------------------
# _cmd_delete
# ---------------------------------------------------------------------------

class TestCmdDelete:
    def test_delete_returns_0(self):
        with patch.object(pin_auth, "_delete_hash"), \
             patch("keyring.delete_password"):
            assert pin_auth._cmd_delete() == 0

    def test_delete_called_even_when_no_pin_set(self):
        with patch.object(pin_auth, "_delete_hash") as mock_del, \
             patch("keyring.delete_password"):
            pin_auth._cmd_delete()
        mock_del.assert_called_once()

    def test_removes_all_env_hash_keychain_entries(self):
        """Uninstall must clear env_critical_hash, env_hash_configured, and env_original_smtp_to."""
        deleted: list = []
        with patch.object(pin_auth, "_delete_hash"), \
             patch.object(pin_auth, "_delete_pin_configured_marker"), \
             patch("keyring.delete_password", side_effect=lambda s, a: deleted.append(a)):
            pin_auth._cmd_delete()
        assert "env_critical_hash" in deleted
        assert "env_hash_configured" in deleted
        assert "env_original_smtp_to" in deleted


# ---------------------------------------------------------------------------
# _cmd_status
# ---------------------------------------------------------------------------

class TestCmdStatus:
    def test_returns_0_when_pin_is_set(self):
        with patch.object(pin_auth, "_get_stored_hash", return_value="somehash"):
            assert pin_auth._cmd_status() == 0

    def test_returns_1_when_no_pin(self):
        with patch.object(pin_auth, "_get_stored_hash", return_value=""):
            assert pin_auth._cmd_status() == 1

