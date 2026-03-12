"""vigil_cli.py — unified cross-platform CLI entry point for Vigil.

After installing the package, a `vigil` command is added to your PATH:

    pip install -e .     # editable install — keeps .env in the project directory
    pipx install -e .    # same, in an isolated environment

Subcommands
-----------
  vigil setup       Interactive first-time setup wizard
  vigil status      Service health + config summary
  vigil update      Edit settings and reload services
  vigil blocklist   Download the latest domain blocklist
  vigil reinstall   Re-register services (e.g. after moving the folder)
  vigil uninstall   Remove Vigil from this machine
  vigil doctor      Diagnose configuration and service issues
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import smtplib
import subprocess
import sys
import threading
import time
from pathlib import Path

__version__ = "0.1.0"

# ── ANSI colours (disabled when stdout is not a TTY) ─────────────────────────
_USE_COLOR = sys.stdout.isatty()
if _USE_COLOR and sys.platform == "win32":
    # Enable VT processing on Windows 10+ so ANSI escape codes render correctly.
    # os.system("") is the lightest way to trigger this — no output, no side effects.
    os.system("")
_GREEN  = "\033[32m" if _USE_COLOR else ""
_YELLOW = "\033[33m" if _USE_COLOR else ""
_RED    = "\033[31m" if _USE_COLOR else ""
_CYAN   = "\033[36m" if _USE_COLOR else ""
_DIM    = "\033[2m"  if _USE_COLOR else ""
_BOLD   = "\033[1m"  if _USE_COLOR else ""
_NC     = "\033[0m"  if _USE_COLOR else ""

_OK   = f"{_GREEN}✓{_NC}"
_WARN = f"{_YELLOW}⚠{_NC}"
_FAIL = f"{_RED}✗{_NC}"

# ── visual helpers ────────────────────────────────────────────────────────────

_LOGO = """\
  ██╗   ██╗██╗ ██████╗ ██╗██╗
  ██║   ██║██║██╔════╝ ██║██║
  ██║   ██║██║██║  ███╗██║██║
  ╚██╗ ██╔╝██║██║   ██║██║██║
   ╚████╔╝ ██║╚██████╔╝██║███████╗
    ╚═══╝  ╚═╝ ╚═════╝ ╚═╝╚══════╝"""

def _print_banner() -> None:
    """Print the Vigil logo banner. Only called when stdout is a TTY."""
    if not _USE_COLOR:
        return
    print(f"\n{_CYAN}{_BOLD}{_LOGO}{_NC}")
    print(f"  {_DIM}◉  Always Watching  ·  v{__version__}{_NC}\n")


_SECTION_WIDTH = 52  # total width of the section rule line

def _section(title: str) -> None:
    """Print a bold section header with a trailing dim rule."""
    rule = "─" * max(0, _SECTION_WIDTH - len(title) - 2)
    print(f"\n  {_BOLD}{title}{_NC}  {_DIM}{rule}{_NC}")


def _spinner(msg: str, fn: "callable") -> "any":
    """Run *fn* in a background thread while showing an animated spinner.

    The spinner line is erased before returning so subsequent prints are clean.
    Falls back to a plain status line when output is not a TTY.
    """
    if not _USE_COLOR:
        print(f"  … {msg}", flush=True)
        return fn()

    frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    stop = threading.Event()
    result_holder: list = [None]

    def _work() -> None:
        result_holder[0] = fn()
        stop.set()

    def _spin() -> None:
        i = 0
        while not stop.is_set():
            frame = frames[i % len(frames)]
            print(f"\r  {_CYAN}{frame}{_NC}  {msg}", end="", flush=True)
            stop.wait(0.1)
            i += 1
        print(f"\r\033[2K", end="", flush=True)  # erase spinner line

    worker = threading.Thread(target=_work,  daemon=True)
    spinner = threading.Thread(target=_spin, daemon=True)
    spinner.start()
    worker.start()
    worker.join()
    spinner.join()
    return result_holder[0]

REPO_ROOT = Path(__file__).parent

_MACOS_INSTALL   = REPO_ROOT / "platforms" / "macos" / "install.sh"
_MACOS_UNINSTALL = REPO_ROOT / "platforms" / "macos" / "uninstall.sh"
_WIN_INSTALL     = REPO_ROOT / "platforms" / "windows" / "install.ps1"
_WIN_INSTALL_BAT = REPO_ROOT / "platforms" / "windows" / "install.bat"
_WIN_UNINSTALL   = REPO_ROOT / "platforms" / "windows" / "uninstall.ps1"

# ── low-level helpers ─────────────────────────────────────────────────────────

def _run(cmd: list[str]) -> None:
    """Execute *cmd*, forwarding its exit code to the caller."""
    sys.exit(subprocess.run(cmd).returncode)


def _macos(flag: str | None = None) -> None:
    args = ["bash", str(_MACOS_INSTALL)]
    if flag:
        args.append(flag)
    _run(args)


def _windows(script: Path, flag: str | None = None) -> None:
    args = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    if flag:
        args.append(flag)
    _run(args)


def _unsupported() -> None:
    print(
        f"Vigil supports macOS and Windows only (detected: {sys.platform}).",
        file=sys.stderr,
    )
    sys.exit(1)


# ── subcommands ───────────────────────────────────────────────────────────────

def cmd_setup(_args: argparse.Namespace) -> None:
    if sys.platform == "darwin":
        _macos()
    elif sys.platform == "win32":
        # .bat wrapper bypasses PowerShell execution policy for users who haven't relaxed it
        _run(["cmd", "/c", str(_WIN_INSTALL_BAT)])
    else:
        _unsupported()


def cmd_status(_args: argparse.Namespace) -> None:
    if sys.platform == "darwin":
        _macos("--status")
    elif sys.platform == "win32":
        _windows(_WIN_INSTALL, "-Status")
    else:
        _unsupported()


def cmd_update(_args: argparse.Namespace) -> None:
    if sys.platform == "darwin":
        _macos("--update")
    elif sys.platform == "win32":
        _windows(_WIN_INSTALL, "-Update")
    else:
        _unsupported()


def cmd_blocklist(_args: argparse.Namespace) -> None:
    if sys.platform == "darwin":
        _macos("--blocklist")
    elif sys.platform == "win32":
        _windows(_WIN_INSTALL, "-Blocklist")
    else:
        _unsupported()


def cmd_reinstall(_args: argparse.Namespace) -> None:
    if sys.platform == "darwin":
        _macos("--reinstall")
    elif sys.platform == "win32":
        _windows(_WIN_INSTALL, "-Reinstall")
    else:
        _unsupported()


def cmd_uninstall(_args: argparse.Namespace) -> None:
    if sys.platform == "darwin":
        _run(["bash", str(_MACOS_UNINSTALL)])
    elif sys.platform == "win32":
        _windows(_WIN_UNINSTALL)
    else:
        _unsupported()


# ── vigil doctor ──────────────────────────────────────────────────────────────

def _load_dotenv_raw() -> dict[str, str]:
    """Parse the project .env file directly without importing config.

    Importing config raises EnvironmentError when required vars are missing,
    which is exactly the situation doctor is designed to diagnose.
    """
    env: dict[str, str] = {}
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return env
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _test_smtp(env: dict[str, str]) -> tuple[str, str]:
    """Attempt to connect and authenticate to the configured SMTP server.

    Returns (status_icon, human-readable message).
    """
    host     = env.get("SMTP_HOST", "").strip()
    port     = int(env.get("SMTP_PORT", "587") or "587")
    user     = env.get("SMTP_USER", "").strip()
    password = env.get("SMTP_PASS", "").strip()

    if not all([host, user, password]):
        return _WARN, "Skipped — SMTP_HOST / SMTP_USER / SMTP_PASS not all set"

    try:
        if port == 465:
            ctx = __import__("ssl").create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=10, context=ctx) as srv:
                srv.login(user, password)
        else:
            with smtplib.SMTP(host, port, timeout=10) as srv:
                srv.ehlo()
                srv.starttls()
                srv.ehlo()
                srv.login(user, password)
        return _OK, f"Authenticated to {host}:{port}"
    except smtplib.SMTPAuthenticationError:
        return _FAIL, f"Authentication failed on {host}:{port} — check SMTP_USER / SMTP_PASS"
    except OSError as exc:
        # SMTPException is a subclass of OSError (since Python 3.4), so this
        # catches all smtplib errors, connection errors, and timeouts.
        return _FAIL, f"Could not reach {host}:{port} — {exc}"


def _launchd_status(label: str) -> tuple[bool, str]:
    """Return (running, detail) for a macOS launchd service.

    Uses ``launchctl list`` (no label arg) whose output is a tab-separated
    table: PID \\t Status \\t Label.  The single-label form returns a plist
    dictionary, so ``parts[0]`` would be ``{`` — not the PID.
    """
    result = subprocess.run(
        ["launchctl", "list"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return False, "not loaded"
    for line in result.stdout.splitlines():
        cols = line.split("\t")
        if len(cols) >= 3 and cols[2].strip() == label:
            pid = cols[0].strip()
            if pid == "-":
                return False, "loaded but not running"
            return True, f"running (PID {pid})"
    return False, "not loaded"


def _schtasks_status(task_name: str) -> tuple[bool, str]:
    """Return (running, detail) for a Windows Task Scheduler task.

    Uses CSV output for locale-independent parsing.

    Healthy states: "Running" (currently executing) and "Ready" (scheduled,
    waiting for next trigger).  "Disabled" or "Could not start" are failures.
    Note: schtasks does not expose PIDs, so the detail is the status string.
    """
    result = subprocess.run(
        ["schtasks", "/query", "/tn", task_name, "/fo", "CSV", "/nh"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        return False, "task not registered"
    # CSV format: "TaskName","Next Run Time","Status"
    import csv, io
    rows = list(csv.reader(io.StringIO(result.stdout.strip())))
    if rows and len(rows[0]) >= 3:
        status = rows[0][2].strip()
        healthy = status.lower() in ("running", "ready")
        return healthy, status
    return False, "unknown"


def cmd_doctor(_args: argparse.Namespace) -> None:
    """Run a series of diagnostic checks and print a colour-coded summary."""
    _platform = {"darwin": "macOS", "win32": "Windows"}.get(sys.platform, sys.platform)
    _pyver    = ".".join(str(v) for v in sys.version_info[:3])
    print(
        f"\n  {_BOLD}Vigil Doctor{_NC}  "
        f"{_DIM}v{__version__}  ·  {_platform}  ·  Python {_pyver}{_NC}\n"
    )

    issues = 0
    t_start = time.monotonic()

    def ok(label: str, detail: str = "") -> None:
        suffix = f" — {_CYAN}{detail}{_NC}" if detail else ""
        print(f"  {_OK}  {label}{suffix}")

    def warn(label: str, detail: str = "") -> None:
        suffix = f" — {_YELLOW}{detail}{_NC}" if detail else ""
        print(f"  {_WARN}  {label}{suffix}")

    def fail(label: str, detail: str = "") -> None:
        nonlocal issues
        issues += 1
        # Highlight any "vigil <cmd>" action hint in bold cyan so it stands out.
        if detail and " — run: " in detail:
            info_part, _, hint = detail.partition(" — run: ")
            suffix = f" — {_DIM}{info_part}{_NC}  {_BOLD}{_CYAN}→ {hint}{_NC}"
        elif detail:
            suffix = f" — {_DIM}{detail}{_NC}"
        else:
            suffix = ""
        print(f"  {_FAIL}  {label}{suffix}")

    # ── 1. .env file ──────────────────────────────────────────────────────────
    _section("Configuration (.env)")
    env_file = REPO_ROOT / ".env"
    env = _load_dotenv_raw()
    if env_file.exists():
        ok(".env file found", str(env_file))
    else:
        fail(".env not found", f"expected at {env_file} — run: vigil setup")

    # ── 2. Required SMTP variables ────────────────────────────────────────────
    required = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_TO"]
    for var in required:
        val = env.get(var, "")
        if var == "SMTP_PASS":
            display = ("*" * min(len(val), 8)) if val else ""
        else:
            display = val
        if val:
            ok(var, display)
        else:
            fail(f"{var} not set", "run: vigil update")

    # ── 3. SMTP_TO format validation ──────────────────────────────────────────
    smtp_to_raw = env.get("SMTP_TO", "")
    if smtp_to_raw:
        email_re = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
        bad = [
            addr.strip()
            for addr in smtp_to_raw.split(",")
            if addr.strip() and not email_re.match(addr.strip())
        ]
        if bad:
            fail("SMTP_TO has invalid address(es)", ", ".join(bad))
        else:
            ok("SMTP_TO addresses valid")

    # ── 4. Live SMTP connection + auth ────────────────────────────────────────
    _section("Connectivity")
    smtp_icon, smtp_msg = _spinner("Testing SMTP connection…", lambda: _test_smtp(env))
    if smtp_icon == _FAIL:
        issues += 1
    # Print directly to avoid double-counting issues via fail().
    detail_color = _DIM if smtp_icon == _FAIL else _CYAN
    suffix = f" — {detail_color}{smtp_msg}{_NC}" if smtp_msg else ""
    print(f"  {smtp_icon}  SMTP login test{suffix}")

    # ── 5. Services ───────────────────────────────────────────────────────────
    _section("Services")
    if sys.platform == "darwin":
        for label in ("com.vigil.tracker", "com.vigil.summarizer"):
            running, detail = _launchd_status(label)
            if running:
                ok(label, detail)
            else:
                fail(label, f"{detail} — run: vigil reinstall")
    elif sys.platform == "win32":
        for task in ("Vigil Tracker", "Vigil Summarizer"):
            running, detail = _schtasks_status(task)
            if running:
                ok(task, detail)
            else:
                fail(task, f"{detail} — run: vigil reinstall")
    else:
        warn("Service checks not supported on this platform")

    # ── 6. Blocklist staleness ────────────────────────────────────────────────
    _section("Data")
    blocklist = REPO_ROOT / "data" / "domains.txt"
    if blocklist.exists():
        age_days = (time.time() - blocklist.stat().st_mtime) / 86_400
        size_kb  = blocklist.stat().st_size // 1024
        detail   = f"{age_days:.0f} days old, {size_kb:,} KB"
        if age_days > 30:
            warn("Blocklist may be stale", detail + " — run: vigil blocklist")
        else:
            ok("Blocklist up to date", detail)
    else:
        fail("Blocklist file missing", "run: vigil blocklist")

    # ── 7. Python dependencies ────────────────────────────────────────────────
    _section("Python dependencies")
    deps = {
        "dotenv":       "python-dotenv",
        "apscheduler":  "apscheduler",
        "psutil":       "psutil",
        "keyring":      "keyring",
        "tzlocal":      "tzlocal",
    }
    for module, package in deps.items():
        if importlib.util.find_spec(module) is not None:
            ok(package)
        else:
            fail(package, f"not installed — run: pip install {package}")

    # ── 8. OpenAI (optional) ─────────────────────────────────────────────────
    openai_key = env.get("OPENAI_API_KEY", "").strip()
    if openai_key:
        if importlib.util.find_spec("openai") is not None:
            ok("openai (optional)", "package installed, key configured")
        else:
            warn("openai (optional)", "key set but 'openai' package not installed — run: pip install openai")
    else:
        warn("openai (optional)", "not configured — AI summaries disabled")

    # ── summary ───────────────────────────────────────────────────────────────
    elapsed = time.monotonic() - t_start
    rule = _DIM + "─" * _SECTION_WIDTH + _NC
    print(f"\n  {rule}")
    if issues == 0:
        print(f"  {_GREEN}{_BOLD}All checks passed.{_NC}  {_DIM}{elapsed:.1f}s{_NC}\n")
    else:
        plural = "issue" if issues == 1 else "issues"
        print(
            f"  {_RED}{_BOLD}{issues} {plural} found.{_NC}"
            f"  Follow the suggestions above to fix them."
            f"  {_DIM}{elapsed:.1f}s{_NC}\n"
        )


# ── argument parser ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="vigil",
        description="Vigil accountability tracker — manage your installation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  vigil setup       # first-time guided installation\n"
            "  vigil status      # check if services are running\n"
            "  vigil doctor      # diagnose any configuration issues\n"
            "  vigil update      # change email or schedule settings\n"
            "  vigil blocklist   # refresh the adult-site domain list\n"
        ),
    )
    parser.add_argument(
        "--version", action="version",
        version=f"%(prog)s {__version__}",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    sub.add_parser("setup",     help="Interactive first-time setup wizard")
    sub.add_parser("status",    help="Service health and config summary")
    sub.add_parser("update",    help="Edit settings and reload services")
    sub.add_parser("blocklist", help="Download the latest domain blocklist")
    sub.add_parser("reinstall", help="Re-register services without re-prompting")
    sub.add_parser("uninstall", help="Stop services and remove Vigil from this machine")
    sub.add_parser("doctor",    help="Diagnose configuration and service issues")

    _print_banner()
    args = parser.parse_args()

    {
        "setup":     cmd_setup,
        "status":    cmd_status,
        "update":    cmd_update,
        "blocklist": cmd_blocklist,
        "reinstall": cmd_reinstall,
        "uninstall": cmd_uninstall,
        "doctor":    cmd_doctor,
    }[args.command](args)


if __name__ == "__main__":
    main()
