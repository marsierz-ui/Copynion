"""Windows backend.

Pure ctypes against user32/kernel32 - no pywin32 dependency. Reads the
foreground window's title and the owning process's executable name, plus the
system idle timer via ``GetLastInputInfo``.
"""

from __future__ import annotations

import sys
import time

from copynion.models import Observation
from copynion.observer.backends.base import WindowBackend


class WindowsBackend(WindowBackend):
    name = "windows"
    requirement = "Windows with user32.dll (standard)"

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        self._LASTINPUTINFO = LASTINPUTINFO

    @classmethod
    def is_available(cls) -> bool:
        if sys.platform != "win32":
            return False
        try:
            import ctypes

            ctypes.windll.user32  # noqa: B018 - presence check
            return True
        except (ImportError, AttributeError, OSError):
            return False

    def poll(self) -> Observation:
        ctypes = self._ctypes
        hwnd = self._user32.GetForegroundWindow()
        title, app = "", ""
        if hwnd:
            length = self._user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                self._user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
            app = self._process_name(hwnd)
        return Observation(
            timestamp=time.time(),
            app=app,
            title=title,
            idle_seconds=self._idle_seconds(),
            backend=self.name,
        )

    def _process_name(self, hwnd) -> str:
        ctypes, wintypes = self._ctypes, self._wintypes
        pid = wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = self._kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(260)
            buf = ctypes.create_unicode_buffer(size.value)
            if self._kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                exe = buf.value.rsplit("\\", 1)[-1].lower()
                return exe[:-4] if exe.endswith(".exe") else exe
        finally:
            self._kernel32.CloseHandle(handle)
        return ""

    def _idle_seconds(self) -> float:
        info = self._LASTINPUTINFO()
        info.cbSize = self._ctypes.sizeof(info)
        if not self._user32.GetLastInputInfo(self._ctypes.byref(info)):
            return 0.0
        millis = self._kernel32.GetTickCount() - info.dwTime
        return max(0.0, millis / 1000.0)
