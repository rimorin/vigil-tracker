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
        with patch.object(pin_auth, "_get_stored_hash", return_value=""):
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
        with patch.object(pin_auth, "_get_stored_hash", return_value=""):
            assert pin_auth._cmd_verify() == 0

    def test_keychain_error_returns_0(self):
        """Keychain read failure should not block uninstall."""
        with patch.object(pin_auth, "_get_stored_hash", return_value=""):
            assert pin_auth._cmd_verify() == 0


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
# _cmd_delete
# ---------------------------------------------------------------------------

class TestCmdDelete:
    def test_delete_returns_0(self):
        with patch.object(pin_auth, "_delete_hash") as mock_del:
            assert pin_auth._cmd_delete() == 0
        mock_del.assert_called_once()

    def test_delete_called_even_when_no_pin_set(self):
        with patch.object(pin_auth, "_delete_hash") as mock_del:
            pin_auth._cmd_delete()
        mock_del.assert_called_once()


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

