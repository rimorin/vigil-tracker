"""
tracker_windows.py — Windows-specific tracking implementation for Vigil.

Provides:
  - Idle detection via GetLastInputInfo (Win32 ctypes, no pywin32 required)
  - Active-window / browser-URL detection via UIA (uiautomation) + ctypes
  - Exit-handler registration (Task Scheduler uses TerminateProcess, not SIGTERM)

Imported exclusively by tracker.py when sys.platform == "win32" so that
macOS code paths are never touched by Windows changes and vice versa.
"""

import atexit
import ctypes
from datetime import datetime

import psutil


# ---------------------------------------------------------------------------
# Idle detection — Win32 GetLastInputInfo
# ---------------------------------------------------------------------------

# GetLastInputInfo returns the tick count (dwTime) of the last input event.
# Use 32-bit modular subtraction to handle GetTickCount wraparound (~49.7 days).
class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


# Pre-allocated reusable ctypes objects — avoids repeated heap allocations in
# the 5-second hot path (get_idle_seconds and get_active_label are called every tick).
_lii = _LASTINPUTINFO()
_lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
_title_buf = ctypes.create_unicode_buffer(512)
_class_buf = ctypes.create_unicode_buffer(256)


def get_idle_seconds() -> float:
    """Return seconds since the last keyboard/mouse input (Windows)."""
    try:
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(_lii)):
            tick = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
            elapsed_ms = (tick - _lii.dwTime) & 0xFFFFFFFF
            return elapsed_ms / 1000.0
    except Exception:
        pass
    return 0.0


# ---------------------------------------------------------------------------
# Active window + browser URL detection
# ---------------------------------------------------------------------------

# Maps process name (without .exe) to a friendly display name shown in logs.
_BROWSER_DISPLAY_NAMES = {
    "chrome":   "Google Chrome",
    "msedge":   "Microsoft Edge",
    "firefox":  "Firefox",
    "brave":    "Brave",
    "opera":    "Opera",
    "vivaldi":  "Vivaldi",
}

# Per-tick caches — eliminate redundant kernel and cross-process UIA calls.
_proc_name_cache: dict = {}  # pid (int) -> process name string
_uia_cache: dict = {"hwnd": 0, "title": "", "url": ""}  # last browser URL result


def get_active_label() -> str:
    """Return the active window label on Windows.

    For Chromium browsers and Firefox, returns '[BrowserName] URL'.
    For all other windows, returns the window title string.
    Uses stdlib ctypes for Win32 calls (no pywin32 required).
    Uses uiautomation (Windows UIA) for address-bar reading.
    """
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd:
        return ""

    ctypes.windll.user32.GetWindowTextW(hwnd, _title_buf, 512)
    title = _title_buf.value.strip()

    ctypes.windll.user32.GetClassNameW(hwnd, _class_buf, 256)
    win_class = _class_buf.value

    pid = ctypes.c_uint(0)  # DWORD is always 4 bytes on Windows
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    pid_val = pid.value
    if pid_val not in _proc_name_cache:
        try:
            _proc_name_cache[pid_val] = psutil.Process(pid_val).name()
        except Exception:
            _proc_name_cache[pid_val] = ""
    proc_exe = _proc_name_cache[pid_val]
    proc_key = proc_exe.lower().replace(".exe", "")

    if win_class in ("Chrome_WidgetWin_1", "MozillaWindowClass"):
        # Skip the cross-process UIA traversal when the window and title are
        # unchanged since the last tick — the URL has almost certainly not
        # changed.  A previously unreadable address bar (url="") always
        # triggers a fresh UIA attempt.
        if hwnd == _uia_cache["hwnd"] and title == _uia_cache["title"] and _uia_cache["url"]:
            display = _BROWSER_DISPLAY_NAMES.get(proc_key, proc_key.capitalize())
            return f"[{display}] {_uia_cache['url']}"
        try:
            import uiautomation as auto
            win_ctrl = auto.ControlFromHandle(hwnd)
            url = ""
            if win_class == "Chrome_WidgetWin_1":
                # Edge exposes a stable AutomationId (localization-safe).
                # Chrome falls back to Name (English-locale label).
                edit = win_ctrl.EditControl(AutomationId="addressEditBox")
                if not edit.Exists(0, 0):
                    edit = win_ctrl.EditControl(Name="Address and search bar")
                if edit.Exists(0, 0):
                    url = edit.GetValuePattern().Value
            else:  # Firefox
                edit = win_ctrl.EditControl()
                if edit.Exists(0, 0):
                    url = edit.GetValuePattern().Value
            _uia_cache["hwnd"] = hwnd
            _uia_cache["title"] = title
            _uia_cache["url"] = url
            if url:
                display = _BROWSER_DISPLAY_NAMES.get(proc_key, proc_key.capitalize())
                return f"[{display}] {url}"
        except Exception:
            _uia_cache.update({"hwnd": 0, "title": "", "url": ""})

    return title


# ---------------------------------------------------------------------------
# Exit handler
# ---------------------------------------------------------------------------

def register_exit_handler(get_session_fn, finalize_session_fn) -> None:
    """Register an atexit handler to flush the open tracking session on exit.

    Task Scheduler uses TerminateProcess() when stopping a task — SIGTERM is
    never delivered on Windows.  The atexit handler fires on clean interpreter
    shutdown and ensures no in-progress session is silently dropped.

    Args:
        get_session_fn:       Zero-argument callable returning the current
                              session dict (or None) from tracker.py.
        finalize_session_fn:  tracker._finalize_session — called with
                              (session, datetime.now()) to write the log entry.
    """
    def _atexit_flush():
        session = get_session_fn()
        if session is not None:
            finalize_session_fn(session, datetime.now())

    atexit.register(_atexit_flush)
