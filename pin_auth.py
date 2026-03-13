"""
pin_auth.py — Partner PIN protection for Vigil.

Protects uninstallation from impulsive removal by requiring a partner-set PIN.
The PIN is hashed with PBKDF2-HMAC-SHA256 and stored in the OS-native secret
store (macOS Keychain on macOS, Windows Credential Locker on Windows) via the
`keyring` library — not in a plain-text file the user can easily edit.

CLI usage (called by install / uninstall scripts):
    python pin_auth.py hash       — prompt for a new PIN, store hash in OS keychain
    python pin_auth.py verify     — prompt for PIN, verify against keychain, exit 0/1
    python pin_auth.py delete     — remove stored hash from OS keychain
    python pin_auth.py status     — exit 0 if a PIN is stored, 1 if not
    python pin_auth.py env_store  — snapshot critical .env fields into keychain
    python pin_auth.py env_verify — exit 0 if .env matches snapshot, 1 if tampered
"""

import getpass
import hashlib
import hmac
import keyring
import keyring.errors
import secrets
import smtplib
import socket
import ssl
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_ALGORITHM        = "pbkdf2_sha256"
_ITERATIONS       = 260_000
_MAX_ATTEMPTS     = 3
_KEYCHAIN_SERVICE = "com.vigil"
_KEYCHAIN_ACCOUNT = "partner_pin_hash"

# ---------------------------------------------------------------------------
# .env integrity — constants and helpers
# ---------------------------------------------------------------------------

# Keychain accounts used by the env-integrity subsystem.
_KEYCHAIN_ENV_HASH_ACCOUNT  = "env_critical_hash"
_KEYCHAIN_ENV_SMTP_TO_ACCOUNT = "env_original_smtp_to"

# The fields that the observed person must NOT be able to silently change.
_ENV_CRITICAL_KEYS = [
    "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS",
    "SMTP_TO", "ALERT_ENABLED",
]

# Resolved at import time — callers can override if needed.
_ENV_FILE = Path(__file__).parent / ".env"


def _parse_env_file(env_path: Path) -> dict:
    """Return a key→value dict parsed from *env_path* (no shell expansion)."""
    result: dict = {}
    if not env_path.exists():
        return result
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def compute_env_hash(env_path: Path | None = None) -> str:
    """Return a SHA-256 hex digest of the critical .env fields.

    Uses a deterministic canonical form so the hash is independent of field
    order in the file.
    """
    path = env_path or _ENV_FILE
    values = _parse_env_file(path)
    canonical = "|".join(
        f"{k}={values.get(k, '')}"
        for k in sorted(_ENV_CRITICAL_KEYS)
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def store_env_hash(env_path: Path | None = None) -> None:
    """Snapshot the current critical .env fields into the OS keychain.

    Also stores SMTP_TO separately so the summariser can alert the *original*
    partner even if that address is later tampered with.
    """
    path = env_path or _ENV_FILE
    h = compute_env_hash(path)
    keyring.set_password(_KEYCHAIN_SERVICE, _KEYCHAIN_ENV_HASH_ACCOUNT, h)
    values = _parse_env_file(path)
    smtp_to = values.get("SMTP_TO", "")
    if smtp_to:
        keyring.set_password(_KEYCHAIN_SERVICE, _KEYCHAIN_ENV_SMTP_TO_ACCOUNT, smtp_to)


def verify_env_hash(env_path: Path | None = None) -> bool:
    """Return True if the current .env critical fields match the stored snapshot.

    Returns True (not a tamper failure) when no snapshot has been stored yet.
    """
    try:
        stored = keyring.get_password(_KEYCHAIN_SERVICE, _KEYCHAIN_ENV_HASH_ACCOUNT) or ""
    except Exception:
        return True  # keychain unavailable — fail open
    if not stored:
        return True  # no baseline yet
    return compute_env_hash(env_path or _ENV_FILE) == stored


def get_original_smtp_to() -> str:
    """Return the original SMTP_TO stored at install/update time.

    Falls back to '' if the keychain entry is missing.
    """
    try:
        return keyring.get_password(_KEYCHAIN_SERVICE, _KEYCHAIN_ENV_SMTP_TO_ACCOUNT) or ""
    except Exception:
        return ""

# ---------------------------------------------------------------------------
# Hashing / verification
# ---------------------------------------------------------------------------

def hash_pin(pin: str) -> str:
    """Hash *pin* with PBKDF2-HMAC-SHA256 and return a storable string."""
    salt   = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), _ITERATIONS)
    return f"{_ALGORITHM}${_ITERATIONS}${salt}${digest.hex()}"


def verify_pin(pin: str, stored: str) -> bool:
    """Return True if *pin* matches the *stored* hash from :func:`hash_pin`."""
    try:
        algo, iters_str, salt, expected_hex = stored.split("$")
        if algo != _ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", pin.encode(), salt.encode(), int(iters_str)
        )
        return hmac.compare_digest(digest.hex(), expected_hex)
    except Exception:
        return False

# ---------------------------------------------------------------------------
# Keychain helpers
# ---------------------------------------------------------------------------

def _get_stored_hash() -> str:
    """Retrieve the stored hash from the OS keychain, or '' if not set."""
    try:
        return keyring.get_password(_KEYCHAIN_SERVICE, _KEYCHAIN_ACCOUNT) or ""
    except Exception:
        return ""


def _store_hash(hash_value: str) -> None:
    """Persist *hash_value* in the OS keychain."""
    keyring.set_password(_KEYCHAIN_SERVICE, _KEYCHAIN_ACCOUNT, hash_value)


def _delete_hash() -> None:
    """Remove the stored hash from the OS keychain. Silent if not present."""
    try:
        keyring.delete_password(_KEYCHAIN_SERVICE, _KEYCHAIN_ACCOUNT)
    except keyring.errors.PasswordDeleteError:
        pass
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Email notification
# ---------------------------------------------------------------------------

def _send_notification(subject: str, html_body: str) -> None:
    """Best-effort email notification; silently swallows all errors."""
    try:
        # Import lazily so this module can be imported without a complete .env.
        sys.path.insert(0, str(Path(__file__).parent))
        import config  # noqa: PLC0415

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = config.SMTP_FROM
        msg["To"]      = ", ".join(config.SMTP_TO)
        msg.attach(MIMEText(html_body, "html"))

        ctx = ssl.create_default_context()
        if config.SMTP_PORT == 465:
            with smtplib.SMTP_SSL(
                config.SMTP_HOST, config.SMTP_PORT, context=ctx, timeout=15
            ) as s:
                s.login(config.SMTP_USER, config.SMTP_PASS)
                s.send_message(msg)
        else:
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as s:
                s.ehlo()
                s.starttls(context=ctx)
                s.ehlo()
                s.login(config.SMTP_USER, config.SMTP_PASS)
                s.send_message(msg)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Interactive helpers
# ---------------------------------------------------------------------------

def prompt_and_verify(stored_hash: str) -> bool:
    """
    Prompt for the partner PIN up to _MAX_ATTEMPTS times.

    Returns True on success.  After all attempts are exhausted a failed-attempt
    notification is sent to all configured recipients.
    """
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            pin = getpass.getpass(f"  Enter partner PIN ({attempt}/{_MAX_ATTEMPTS}): ")
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return False

        if verify_pin(pin, stored_hash):
            return True

        remaining = _MAX_ATTEMPTS - attempt
        if remaining > 0:
            print(f"  ❌  Incorrect PIN — {remaining} attempt(s) remaining.",
                  file=sys.stderr)

    print(file=sys.stderr)
    _send_notification(
        subject=f"⚠️ Vigil — Failed uninstall attempt on {socket.gethostname()}",
        html_body=(
            f"<p>Someone tried to uninstall Vigil on "
            f"<strong>{socket.gethostname()}</strong> "
            f"but entered the wrong partner PIN {_MAX_ATTEMPTS} times in a row.</p>"
            f"<p>The uninstall was <strong>blocked</strong>. No action is needed "
            f"unless you approved this attempt.</p>"
        ),
    )
    return False

# ---------------------------------------------------------------------------
# CLI entry points
# ---------------------------------------------------------------------------

def _cmd_verify() -> int:
    """Read stored hash from the OS keychain and interactively verify the PIN."""
    stored = _get_stored_hash()
    if not stored:
        return 0  # No PIN configured — allow uninstall without prompting.

    print(file=sys.stderr)
    print("  🔒  A partner PIN is required to continue.", file=sys.stderr)
    ok = prompt_and_verify(stored)
    if ok:
        print("  ✅  PIN accepted.", file=sys.stderr)
        return 0
    print("  🚫  Access denied. Your accountability partner has been notified.",
          file=sys.stderr)
    return 1


def _cmd_hash() -> int:
    """Prompt for a new PIN twice and store the hash in the OS keychain."""
    print(file=sys.stderr)
    print("  🔑  Set a partner PIN.", file=sys.stderr)
    print("  This PIN will be required to stop or uninstall Vigil.", file=sys.stderr)
    print("  Share it only with your accountability partner.", file=sys.stderr)
    print(file=sys.stderr)

    while True:
        try:
            pin = getpass.getpass("  Enter new PIN:   ")
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return 1

        if not pin:
            print("  PIN cannot be empty — try again.", file=sys.stderr)
            continue

        try:
            confirm = getpass.getpass("  Confirm PIN:     ")
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return 1

        if pin != confirm:
            print("  PINs do not match — try again.", file=sys.stderr)
            continue

        break

    _store_hash(hash_pin(pin))
    print("  🔐  Partner PIN stored in OS keychain.", file=sys.stderr)
    return 0


def _cmd_delete() -> int:
    """Remove the stored hash from the OS keychain."""
    _delete_hash()
    return 0


def _cmd_status() -> int:
    """Exit 0 if a PIN is stored in the OS keychain, 1 if not."""
    return 0 if _get_stored_hash() else 1


def _cmd_env_store() -> int:
    """Snapshot critical .env fields into the OS keychain."""
    try:
        store_env_hash()
        print("  🔐  .env integrity snapshot stored in OS keychain.", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"  ⚠️  Could not store .env snapshot: {exc}", file=sys.stderr)
        return 1


def _cmd_env_verify() -> int:
    """Exit 0 if .env critical fields match the stored snapshot, 1 if tampered."""
    if verify_env_hash():
        return 0
    print("  ⚠️  .env integrity check failed — configuration may have been tampered.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    if cmd == "verify":
        sys.exit(_cmd_verify())
    elif cmd == "hash":
        sys.exit(_cmd_hash())
    elif cmd == "delete":
        sys.exit(_cmd_delete())
    elif cmd == "status":
        sys.exit(_cmd_status())
    elif cmd == "env_store":
        sys.exit(_cmd_env_store())
    elif cmd == "env_verify":
        sys.exit(_cmd_env_verify())
    else:
        print(
            f"Usage: python {Path(__file__).name} verify | hash | delete | status | env_store | env_verify",
            file=sys.stderr,
        )
        sys.exit(2)


