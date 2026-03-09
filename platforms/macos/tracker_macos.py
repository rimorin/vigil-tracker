"""
tracker_macos.py — macOS-specific tracking implementation for Vigil.

Provides:
  - Idle detection via CoreGraphics CGEventSourceSecondsSinceLastEventType
  - Active-window / browser-URL detection via AppleScript (osascript)
  - No-op exit-handler registration (SIGTERM is used on macOS, not atexit)

Imported exclusively by tracker.py when sys.platform != "win32" so that
Windows code paths are never touched by macOS changes and vice versa.

The CoreGraphics CDLL is loaded lazily (on first call to get_idle_seconds)
so this module can be safely imported on non-macOS platforms for testing
purposes (e.g. testing get_active_tab_applescript string building on Windows).
"""

import ctypes
import subprocess
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Idle detection — CoreGraphics API (10.4+)
# Ref: developer.apple.com/documentation/coregraphics/cgeventsource/
#      secondssincelasteventtype(_:eventtype:)
# ---------------------------------------------------------------------------

_kCGEventSourceStateCombinedSessionState = 0        # Apple: combinedSessionState = 0
_kCGAnyInputEventType                    = 0xFFFFFFFF  # Apple: kCGAnyInputEventType macro

_cg: Optional[ctypes.CDLL] = None  # loaded on first call to get_idle_seconds()


def _get_cg() -> ctypes.CDLL:
    """Return the CoreGraphics CDLL, loaded once on first call."""
    global _cg
    if _cg is None:
        lib = ctypes.CDLL(
            "/System/Library/Frameworks/ApplicationServices.framework"
            "/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        lib.CGEventSourceSecondsSinceLastEventType.restype  = ctypes.c_double
        lib.CGEventSourceSecondsSinceLastEventType.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        _cg = lib
    return _cg


def get_idle_seconds() -> float:
    """Return seconds since the last keyboard/mouse/tablet input event (macOS)."""
    try:
        cg = _get_cg()
        return cg.CGEventSourceSecondsSinceLastEventType(
            _kCGEventSourceStateCombinedSessionState,
            _kCGAnyInputEventType,
        )
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# AppleScript browser-tab detection
# ---------------------------------------------------------------------------

# Chromium-based browsers that expose "active tab of front window" via AppleScript.
# Arc's support is limited — wrapped in try/end try for silent fallback.
# Vivaldi is excluded (known AppleScript gap — does not expose active tab URL).
CHROMIUM_ACTIVE_TAB_BROWSERS = ["Google Chrome", "Microsoft Edge", "Brave Browser", "Arc"]


def _installed_chromium_browsers() -> list:
    """Return only the CHROMIUM_ACTIVE_TAB_BROWSERS that are installed on this machine.

    AppleScript validates 'tell application X' blocks against the app's scripting
    dictionary at COMPILE time.  Including a block for an app that isn't installed
    causes a parse error (-2741).  Only installed browsers are added to the script.
    """
    search_roots = ["/Applications", str(Path.home() / "Applications")]
    installed = []
    for browser in CHROMIUM_ACTIVE_TAB_BROWSERS:
        if any((Path(root) / f"{browser}.app").is_dir() for root in search_roots):
            installed.append(browser)
    return installed


def get_active_tab_applescript() -> str:
    """Build AppleScript that returns '[Browser] URL' for the frontmost browser only.

    Uses inline 'and application X is running' guards to avoid nested if/end if
    blocks before else-if chains (which are invalid in AppleScript).
    Safari uses 'current tab' (confirmed via sdef, property cTab).
    Chromium browsers use 'active tab' (confirmed via sdef, property acTa).
    Arc is wrapped in try/end try — limited sdef, silent skip on failure.
    Note: 'result' is a reserved AppleScript keyword; variable named 'activeTab'.
    """
    chromium_blocks = ""
    for browser in _installed_chromium_browsers():
        chromium_blocks += f"""
else if frontApp is "{browser}" and application "{browser}" is running then
    tell application "{browser}"
        try
            set activeURL to URL of active tab of front window
            if activeURL is not missing value then
                set activeTab to "[{browser}] " & activeURL
            end if
        end try
    end tell"""

    return f"""
set activeTab to ""
set frontApp to ""
tell application "System Events"
    try
        set frontApp to name of first application process whose frontmost is true
    end try
end tell
if frontApp is "Safari" and application "Safari" is running then
    tell application "Safari"
        try
            set activeURL to URL of current tab of front window
            if activeURL is not missing value then
                set activeTab to "[Safari] " & activeURL
            end if
        end try
    end tell{chromium_blocks}
end if
return activeTab"""


# Build the AppleScript once at module load — _installed_chromium_browsers() does
# filesystem I/O and we don't want it running on every 5-second poll.
_APPLESCRIPT_SOURCE: str = get_active_tab_applescript()


def get_active_label() -> str:
    """Return '[Browser] URL' or window title for the front application (macOS)."""
    proc = subprocess.Popen(
        ["osascript", "-e", _APPLESCRIPT_SOURCE],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    out, _ = proc.communicate()
    label = out.strip() if out else ""
    return "" if "missing value" in label else label


# ---------------------------------------------------------------------------
# Exit handler (no-op — SIGTERM via signal module covers macOS exit)
# ---------------------------------------------------------------------------

def register_exit_handler(get_session_fn, finalize_session_fn) -> None:
    """No-op on macOS — SIGTERM is delivered and handled via signal.signal()."""
    pass
