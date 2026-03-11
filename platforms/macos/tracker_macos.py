"""
tracker_macos.py — macOS-specific tracking implementation for Vigil.

Provides:
  - Idle detection via CoreGraphics CGEventSourceSecondsSinceLastEventType
  - Active-window / browser-URL detection via AppleScript (osascript)
  - Frontmost-app PID check via NSWorkspace ObjC bridge (no subprocess)
  - No-op exit-handler registration (SIGTERM is used on macOS, not atexit)

Imported exclusively by tracker.py when sys.platform != "win32" so that
Windows code paths are never touched by macOS changes and vice versa.

The CoreGraphics CDLL is loaded lazily (on first call to get_idle_seconds)
so this module can be safely imported on non-macOS platforms for testing
purposes (e.g. testing get_active_tab_applescript string building on Windows).
"""

import ctypes
import ctypes.util
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


# ---------------------------------------------------------------------------
# Frontmost-app PID via NSWorkspace ObjC bridge (no subprocess, ~microseconds)
#
# NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier()
# is the standard macOS API for this.  We drive it through the ObjC runtime
# directly with ctypes so there is zero process-spawning overhead.
#
# AppKit is loaded once to make NSWorkspace available.  Loading AppKit in a
# background daemon is safe — the existing osascript calls already trigger the
# same Cocoa infrastructure indirectly via System Events.  No Cocoa run-loop
# is required for these calls.
# ---------------------------------------------------------------------------

_APPKIT_PATH = "/System/Library/Frameworks/AppKit.framework/AppKit"
_LIBOBJC_PATH = "/usr/lib/libobjc.A.dylib"

# ObjC runtime bridge — initialised once in _init_objc_bridge()
_libobjc: Optional[ctypes.CDLL] = None
_msg_voidp = None   # objc_msgSend typed wrapper → void*
_msg_pid   = None   # objc_msgSend typed wrapper → pid_t (int32)
_ws_class                : Optional[int] = None
_sel_shared_workspace    : Optional[int] = None
_sel_frontmost_app       : Optional[int] = None
_sel_process_identifier  : Optional[int] = None


def _init_objc_bridge() -> None:
    """Initialise the NSWorkspace ObjC bridge (called once on first use)."""
    global _libobjc, _msg_voidp, _msg_pid
    global _ws_class, _sel_shared_workspace, _sel_frontmost_app, _sel_process_identifier

    # Ensure AppKit is resident so NSWorkspace class is registered.
    ctypes.CDLL(_APPKIT_PATH)

    lib = ctypes.CDLL(_LIBOBJC_PATH)
    lib.objc_getClass.restype   = ctypes.c_void_p
    lib.objc_getClass.argtypes  = [ctypes.c_char_p]
    lib.sel_registerName.restype  = ctypes.c_void_p
    lib.sel_registerName.argtypes = [ctypes.c_char_p]

    # Create separately-typed CFUNCTYPE wrappers that share the same underlying
    # function pointer.  This avoids mutating lib.objc_msgSend's restype (which
    # would be unsafe across threads) while still calling the same symbol.
    raw_ptr = ctypes.cast(lib.objc_msgSend, ctypes.c_void_p).value
    _msg_voidp = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(raw_ptr)
    _msg_pid   = ctypes.CFUNCTYPE(ctypes.c_int32,  ctypes.c_void_p, ctypes.c_void_p)(raw_ptr)

    _ws_class               = lib.objc_getClass(b"NSWorkspace")
    _sel_shared_workspace   = lib.sel_registerName(b"sharedWorkspace")
    _sel_frontmost_app      = lib.sel_registerName(b"frontmostApplication")
    _sel_process_identifier = lib.sel_registerName(b"processIdentifier")

    _libobjc = lib


def _get_frontmost_pid() -> int:
    """Return the PID of the frontmost application (no subprocess, ~microseconds).

    Uses NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier()
    via the ObjC runtime.  Returns -1 on any failure so callers can treat it
    as 'unknown' and fall through to osascript.
    """
    try:
        if _libobjc is None:
            _init_objc_bridge()
        workspace = _msg_voidp(_ws_class, _sel_shared_workspace)
        front_app = _msg_voidp(workspace, _sel_frontmost_app)
        if not front_app:
            return -1
        return int(_msg_pid(front_app, _sel_process_identifier))
    except Exception:
        return -1


# Per-tick cache — avoids spawning osascript when the frontmost app hasn't
# changed and the previous result was "" (i.e. a non-browser app was active).
_last_frontmost_pid: int = -1
_last_label: str = ""


def get_active_label() -> str:
    """Return '[Browser] URL' for the front application (macOS).

    Checks the frontmost app PID first via a cheap ObjC call (~microseconds).
    osascript is only spawned when the frontmost app has changed, or when the
    previous result was a URL (the user may have switched tabs in the same
    browser window).  For non-browser apps the label is always "" — once that
    is cached, subsequent ticks skip the subprocess entirely until the app
    changes.
    """
    global _last_frontmost_pid, _last_label

    current_pid = _get_frontmost_pid()

    # Skip osascript: same non-browser app as last tick — result is still "".
    if current_pid != -1 and current_pid == _last_frontmost_pid and _last_label == "":
        return ""

    proc = subprocess.Popen(
        ["osascript", "-e", _APPLESCRIPT_SOURCE],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    out, _ = proc.communicate()
    label = out.strip() if out else ""
    label = "" if "missing value" in label else label

    _last_frontmost_pid = current_pid
    _last_label = label
    return label


# ---------------------------------------------------------------------------
# Exit handler (no-op — SIGTERM via signal module covers macOS exit)
# ---------------------------------------------------------------------------

def register_exit_handler(get_session_fn, finalize_session_fn) -> None:
    """No-op on macOS — SIGTERM is delivered and handled via signal.signal()."""
    pass
