"""A scriptable input backend for tests and the demo.

Input capture is the part of Copynion that most needs exhaustive testing and is
least testable against a real OS hook, so the fake is a first-class citizen.
"""

from __future__ import annotations

from collections.abc import Sequence

from copynion.inputs.backends.base import InputBackend
from copynion.inputs.recorder import InputRecorder


class FakeInputBackend(InputBackend):
    """Replays a scripted list of input actions on demand.

    Each action is a tuple whose first element names the kind:
    ``("key", key, at, modifiers)``, ``("click", button, x, y, at)``,
    ``("scroll", dx, dy, x, y, at)`` or ``("move", x, y, at)``.
    """

    name = "fake"
    requirement = "always available"

    def __init__(self, script: Sequence[tuple] = ()) -> None:
        self.script = list(script)
        self.recorder: InputRecorder | None = None
        self.started = False

    @classmethod
    def is_available(cls) -> bool:
        return True

    def start(self, recorder: InputRecorder) -> None:
        self.recorder = recorder
        self.started = True

    def stop(self) -> None:
        self.started = False

    def replay(self, script: Sequence[tuple] | None = None) -> None:
        """Push the scripted actions into the recorder."""
        if self.recorder is None:
            raise RuntimeError("start() the backend before replaying")
        for action in script if script is not None else self.script:
            kind, rest = action[0], action[1:]
            if kind == "key":
                key, at = rest[0], rest[1]
                modifiers = frozenset(rest[2]) if len(rest) > 2 and rest[2] else frozenset()
                self.recorder.record_key(key, at, modifiers)
            elif kind == "click":
                button, x, y, at = rest[0], rest[1], rest[2], rest[3]
                count = rest[4] if len(rest) > 4 else 1
                self.recorder.record_click(button, x, y, at, count)
            elif kind == "scroll":
                self.recorder.record_scroll(*rest[:5])
            elif kind == "move":
                self.recorder.record_move(*rest[:3])
            else:
                raise ValueError(f"unknown scripted input action {kind!r}")
