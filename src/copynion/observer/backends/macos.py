"""macOS backend.

Reads the frontmost application through AppleScript and the idle timer through
``ioreg``. Window *titles* require the Accessibility permission; without it the
app name is still available, so Copynion degrades to APP_ONLY rather than
failing - and ``copynion doctor`` explains how to grant it if the user wants
more detail.
"""

from __future__ import annotations

import sys
import time

from copynion.models import Observation
from copynion.observer.backends.base import WindowBackend, _have, _run

# Ask for the app name first and the title second, so a missing Accessibility
# permission costs us only the title.
_SCRIPT = '''
tell application "System Events"
    set frontApp to first application process whose frontmost is true
    set appName to name of frontApp
    set winTitle to ""
    try
        set winTitle to name of front window of frontApp
    end try
end tell
return appName & "\\n" & winTitle
'''


class MacOSBackend(WindowBackend):
    name = "macos"
    requirement = "macOS; window titles need Accessibility permission for your terminal"

    @classmethod
    def is_available(cls) -> bool:
        return sys.platform == "darwin" and _have("osascript")

    def poll(self) -> Observation:
        out = _run(["osascript", "-e", _SCRIPT], timeout=2.0) or ""
        parts = out.split("\n", 1)
        app = parts[0].strip() if parts else ""
        title = parts[1].strip() if len(parts) > 1 else ""
        return Observation(
            timestamp=time.time(),
            app=app.lower().replace(" ", "-"),
            title=title,
            idle_seconds=self._idle_seconds(),
            backend=self.name,
        )

    def _idle_seconds(self) -> float:
        out = _run(["sh", "-c", "ioreg -c IOHIDSystem | awk '/HIDIdleTime/ {print $NF; exit}'"])
        if out and out.isdigit():
            return int(out) / 1_000_000_000.0  # nanoseconds
        return 0.0
