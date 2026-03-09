"""
platform_common.py — shared cross-platform utilities for Vigil.

Imported by both tracker.py and summarizer.py to avoid duplicating the
platform path-resolution logic.
"""

import atexit
import logging
import os
import sys
from pathlib import Path


def get_app_dirs():
    """Return (app_support_dir, log_dir) for the current platform.

    Windows:  APPDATA\\Vigil          and  LOCALAPPDATA\\Vigil\\Logs
    macOS:    ~/Library/Application Support/Vigil  and  ~/Library/Logs/Vigil
    """
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        localappdata = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return appdata / "Vigil", localappdata / "Vigil" / "Logs"
    home = Path.home()
    return (
        home / "Library" / "Application Support" / "Vigil",
        home / "Library" / "Logs" / "Vigil",
    )


def acquire_instance_lock(pid_file: Path, logger: logging.Logger) -> None:
    """Ensure only one instance of the calling daemon is running.

    Uses O_CREAT|O_EXCL for atomic file creation so two processes racing at
    startup cannot both claim the lock.  On Windows, Task Scheduler's
    pythonw.exe launcher exits immediately after spawning Python, so the task
    is considered 'finished' and re-triggered — without this guard every
    restart accumulates a duplicate daemon.

    Stale PID files (left after a crash) are detected, removed, and the lock
    is retried so normal restarts always succeed.

    Args:
        pid_file: Path where the PID file should be written.
        logger:   Logger used to record the duplicate-instance message before
                  exiting.  Passed in to avoid a circular import.
    """
    import psutil

    while True:
        # Attempt atomic creation — succeeds only if the file does not exist.
        try:
            fd = os.open(str(pid_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            atexit.register(_release_instance_lock, pid_file)
            return
        except FileExistsError:
            pass  # another process beat us — check whether it is still alive

        # PID file exists: live owner → exit; stale owner → delete and retry.
        try:
            existing_pid = int(pid_file.read_text().strip())
            proc = psutil.Process(existing_pid)
            if proc.is_running() and "python" in proc.name().lower():
                logger.info("Another instance is already running (PID %s). Exiting.", existing_pid)
                sys.exit(0)
        except (ValueError, psutil.NoSuchProcess, psutil.AccessDenied):
            pass  # stale PID file — fall through to remove and retry

        pid_file.unlink(missing_ok=True)


def _release_instance_lock(pid_file: Path) -> None:
    """Remove the PID file on clean exit (registered via atexit)."""
    try:
        pid_file.unlink(missing_ok=True)
    except Exception:
        pass
