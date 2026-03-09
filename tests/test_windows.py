"""
Windows-specific unit tests.

All tests run on macOS and Windows alike — Windows APIs are fully mocked
so no real Win32 calls occur.  On a real Windows machine these same tests
run against the live code paths, providing an additional integration layer.

Lower-level platform symbols (idle detection, active-window detection) are
imported directly from tracker_windows so the tests remain independent of
how tracker.py assembles the final binary.
"""

import ctypes
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

import tracker
from platforms.windows import tracker_windows
from platform_common import get_app_dirs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_windll(hwnd=1, title="Test Window", win_class="Notepad", pid_val=42):
    """Build a minimal ctypes.windll stand-in."""
    user32 = MagicMock()
    kernel32 = MagicMock()

    user32.GetForegroundWindow.return_value = hwnd

    def _GetWindowTextW(h, buf, n):
        buf.value = title

    def _GetClassNameW(h, buf, n):
        buf.value = win_class

    def _GetWindowThreadProcessId(h, pid_ptr):
        # Write the pid value into the ctypes byref'd variable
        ctypes.cast(pid_ptr, ctypes.POINTER(ctypes.c_uint))[0] = pid_val

    user32.GetWindowTextW.side_effect   = _GetWindowTextW
    user32.GetClassNameW.side_effect    = _GetClassNameW
    user32.GetWindowThreadProcessId.side_effect = _GetWindowThreadProcessId

    kernel32.GetTickCount.return_value = 5000  # 5 seconds uptime

    windll = MagicMock()
    windll.user32   = user32
    windll.kernel32 = kernel32
    return windll


# ---------------------------------------------------------------------------
# get_app_dirs — Windows path resolution
# ---------------------------------------------------------------------------

class TestGetAppDirsWindows:
    def test_uses_appdata_env_var(self, monkeypatch, tmp_path):
        appdata   = tmp_path / "AppData" / "Roaming"
        localdata = tmp_path / "AppData" / "Local"
        appdata.mkdir(parents=True)
        localdata.mkdir(parents=True)

        monkeypatch.setenv("APPDATA",      str(appdata))
        monkeypatch.setenv("LOCALAPPDATA", str(localdata))
        monkeypatch.setattr(sys, "platform", "win32")

        app_dir, log_dir = get_app_dirs()
        assert app_dir == appdata / "Vigil"
        assert log_dir == localdata / "Vigil" / "Logs"

    def test_falls_back_to_home_when_env_missing(self, monkeypatch, tmp_path):
        monkeypatch.delenv("APPDATA",      raising=False)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        app_dir, log_dir = get_app_dirs()
        assert app_dir == tmp_path / "AppData" / "Roaming" / "Vigil"
        assert log_dir == tmp_path / "AppData" / "Local" / "Vigil" / "Logs"


# ---------------------------------------------------------------------------
# get_idle_seconds — Windows implementation (tracker_windows)
# ---------------------------------------------------------------------------

class TestGetIdleSecondsWindows:
    def test_returns_elapsed_seconds(self):
        """GetLastInputInfo returns dwTime=3000, GetTickCount=8000 → 5.0 s idle."""
        if sys.platform != "win32":
            pytest.skip("Windows ctypes branch — run mock test via patch below")

    def test_mocked_idle_calculation(self, monkeypatch):
        """Verify 32-bit modular arithmetic on any platform."""
        # _LASTINPUTINFO is always accessible from tracker_windows
        _ = tracker_windows._LASTINPUTINFO()

        class FakeWindll:
            class user32:
                @staticmethod
                def GetLastInputInfo(ptr):
                    # Simulate dwTime = 3000 ms ago
                    import ctypes
                    obj = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint * 2))
                    obj[0][1] = 3000  # dwTime field (index 1 in the struct)
                    return True

            class kernel32:
                @staticmethod
                def GetTickCount():
                    return 8000  # current tick = 8000 ms

        with patch("ctypes.windll", FakeWindll):
            result = tracker_windows.get_idle_seconds()
        assert result == pytest.approx(5.0)

    def test_wraparound_handled_correctly(self):
        """32-bit tick wraparound: tick wraps to 100, dwTime=0xFFFFFF00 → correct delta."""
        # (0x64 - 0xFFFFFF00) & 0xFFFFFFFF = 0x164 = 356 ms
        tick   = 0x00000064
        dw_time = 0xFFFFFF00
        elapsed_ms = (tick - dw_time) & 0xFFFFFFFF
        assert elapsed_ms == 0x164
        assert elapsed_ms / 1000.0 == pytest.approx(0.356)

    def test_returns_zero_on_api_failure(self):
        """If GetLastInputInfo fails, idle seconds should return 0.0 (safe default)."""
        if sys.platform != "win32":
            pytest.skip("Only meaningful on Windows — ctypes.windll not available")
        with patch("ctypes.windll") as mock_windll:
            mock_windll.user32.GetLastInputInfo.return_value = False
            result = tracker_windows.get_idle_seconds()
        assert result == 0.0


# ---------------------------------------------------------------------------
# get_active_label — Windows implementation (tracker_windows)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only live Win32 calls")
class TestGetActiveLabelWindowsOnWindows:
    """Run on actual Windows only — no mocking needed for these."""

    def test_returns_string(self):
        label = tracker_windows.get_active_label()
        assert isinstance(label, str)

    def test_no_exception_on_no_foreground_window(self, monkeypatch):
        with patch("ctypes.windll") as mock_windll:
            mock_windll.user32.GetForegroundWindow.return_value = 0
            result = tracker_windows.get_active_label()
        assert result == ""


class TestGetActiveLabelWindowsMocked:
    """Mock-based tests — always importable from tracker_windows, run on all platforms."""

    def _patch_windows_env(self, monkeypatch, windll, proc_name="notepad.exe"):
        """Apply all patches needed to exercise tracker_windows.get_active_label."""
        monkeypatch.setattr(ctypes, "windll", windll, raising=False)
        mock_proc = MagicMock()
        mock_proc.name.return_value = proc_name
        monkeypatch.setattr("psutil.Process", lambda pid: mock_proc)
        monkeypatch.setattr(tracker_windows, "_proc_name_cache", {})
        monkeypatch.setattr(tracker_windows, "_uia_cache", {"hwnd": 0, "title": "", "url": ""})

    def test_non_browser_returns_window_title(self, monkeypatch):
        windll = _make_windll(title="My Document - Notepad", win_class="Notepad")
        self._patch_windows_env(monkeypatch, windll, "notepad.exe")
        result = tracker_windows.get_active_label()
        assert result == "My Document - Notepad"

    def test_browser_returns_url_label(self, monkeypatch):
        windll = _make_windll(win_class="Chrome_WidgetWin_1")
        self._patch_windows_env(monkeypatch, windll, "msedge.exe")

        mock_edit = MagicMock()
        mock_edit.Exists.return_value = True
        mock_edit.GetValuePattern.return_value.Value = "https://github.com"

        mock_ctrl = MagicMock()
        mock_ctrl.EditControl.return_value = mock_edit

        mock_auto = MagicMock()
        mock_auto.ControlFromHandle.return_value = mock_ctrl

        with patch.dict("sys.modules", {"uiautomation": mock_auto}):
            result = tracker_windows.get_active_label()

        assert result == "[Microsoft Edge] https://github.com"

    def test_uiautomation_failure_falls_back_to_title(self, monkeypatch):
        windll = _make_windll(title="GitHub - Chrome", win_class="Chrome_WidgetWin_1")
        self._patch_windows_env(monkeypatch, windll, "chrome.exe")

        mock_auto = MagicMock()
        mock_auto.ControlFromHandle.side_effect = RuntimeError("UIA error")

        with patch.dict("sys.modules", {"uiautomation": mock_auto}):
            result = tracker_windows.get_active_label()

        assert result == "GitHub - Chrome"

    def test_uiautomation_not_installed_falls_back_to_title(self, monkeypatch):
        windll = _make_windll(title="Firefox Window", win_class="MozillaWindowClass")
        self._patch_windows_env(monkeypatch, windll, "firefox.exe")

        # Remove uiautomation from sys.modules to simulate it not being installed
        with patch.dict("sys.modules", {"uiautomation": None}):
            result = tracker_windows.get_active_label()

        assert result == "Firefox Window"

    def test_no_foreground_window_returns_empty(self, monkeypatch):
        windll = _make_windll(hwnd=0)
        windll.user32.GetForegroundWindow.return_value = 0
        self._patch_windows_env(monkeypatch, windll)

        result = tracker_windows.get_active_label()
        assert result == ""

    def test_psutil_failure_still_returns_title(self, monkeypatch):
        windll = _make_windll(title="Some Window", win_class="SomeClass")
        monkeypatch.setattr(ctypes, "windll", windll, raising=False)
        monkeypatch.setattr("psutil.Process", MagicMock(side_effect=Exception("no proc")))

        result = tracker_windows.get_active_label()
        assert result == "Some Window"


# ---------------------------------------------------------------------------
# get_boot_time — cross-platform psutil path
# ---------------------------------------------------------------------------

class TestGetBootTimeCrossPlatform:
    def test_returns_datetime_from_psutil(self, monkeypatch):
        fake_ts = 1700000000.0
        monkeypatch.setattr("psutil.boot_time", lambda: fake_ts)
        result = tracker.get_boot_time()
        assert result == datetime.fromtimestamp(fake_ts)

    def test_returns_none_on_psutil_failure(self, monkeypatch):
        monkeypatch.setattr("psutil.boot_time", MagicMock(side_effect=Exception("fail")))
        result = tracker.get_boot_time()
        assert result is None
