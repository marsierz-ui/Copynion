"""Linux/X11 backend.

Uses the small X utilities rather than binding to Xlib, so the package keeps its
zero-dependency promise. ``xdotool`` is preferred; ``xprop`` is the fallback
present on almost every desktop install.

Wayland is handled honestly rather than cleverly: most compositors refuse to
tell an arbitrary client which window has focus, and the workarounds involve
installing a shell extension that can read far more than Copynion should ever
ask for. When the title cannot be read we record the time as OPAQUE and say so
in ``copynion doctor``, instead of escalating privilege to get it.
"""

from __future__ import annotations

import os
import re

from copynion.models import Observation
from copynion.observer.backends.base import WindowBackend, _have, _run

_WM_CLASS = re.compile(r'WM_CLASS\(STRING\) = "([^"]*)", "([^"]*)"')
_WM_NAME = re.compile(r'WM_NAME\(\w+\) = "(.*)"', re.DOTALL)
_ACTIVE = re.compile(r"window id # (0x[0-9a-fA-F]+)")


class LinuxBackend(WindowBackend):
    name = "linux-x11"
    requirement = "an X11 session with xdotool or xprop installed"

    def __init__(self) -> None:
        self._xdotool = _have("xdotool")
        self._xprop = _have("xprop")
        self._xprintidle = _have("xprintidle")

    @classmethod
    def is_available(cls) -> bool:
        if not os.environ.get("DISPLAY"):
            return False
        return _have("xdotool") or _have("xprop")

    @classmethod
    def is_wayland(cls) -> bool:
        return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(
            os.environ.get("WAYLAND_DISPLAY")
        )

    def poll(self) -> Observation:
        app, title = "", ""
        if self._xdotool:
            app, title = self._via_xdotool()
        if not app and self._xprop:
            app, title = self._via_xprop()
        return Observation(
            timestamp=__import__("time").time(),
            app=_normalise(app),
            title=title,
            idle_seconds=self._idle_seconds(),
            backend=self.name,
        )

    # -- helpers --------------------------------------------------------------

    def _via_xdotool(self) -> tuple[str, str]:
        win = _run(["xdotool", "getactivewindow"])
        if not win:
            return "", ""
        title = _run(["xdotool", "getwindowname", win]) or ""
        cls = _run(["xprop", "-id", win, "WM_CLASS"]) if self._xprop else None
        app = ""
        if cls and (m := _WM_CLASS.search(cls)):
            app = m.group(2) or m.group(1)
        if not app:
            pid = _run(["xdotool", "getwindowpid", win])
            if pid:
                app = _run(["ps", "-p", pid, "-o", "comm="]) or ""
        return app, title

    def _via_xprop(self) -> tuple[str, str]:
        root = _run(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
        if not root:
            return "", ""
        m = _ACTIVE.search(root)
        if not m or m.group(1) == "0x0":
            return "", ""
        props = _run(["xprop", "-id", m.group(1), "WM_CLASS", "WM_NAME", "_NET_WM_NAME"])
        if not props:
            return "", ""
        app = ""
        if cm := _WM_CLASS.search(props):
            app = cm.group(2) or cm.group(1)
        title = ""
        if tm := _WM_NAME.search(props):
            title = tm.group(1).split('"\n')[0]
        return app, title

    def _idle_seconds(self) -> float:
        if self._xprintidle:
            out = _run(["xprintidle"])
            if out and out.isdigit():
                return int(out) / 1000.0
        return 0.0


def _normalise(app: str) -> str:
    """Reduce a window class to a stable lowercase identifier."""
    app = (app or "").strip().lower()
    for suffix in (".exe", ".bin", ".desktop"):
        if app.endswith(suffix):
            app = app[: -len(suffix)]
    return app.replace(" ", "-")
