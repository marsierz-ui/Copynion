"""A scripted backend, used by the test suite and ``copynion demo``.

Having a first-class fake means the entire pipeline - policy, redaction,
sealing, categorisation, statistics - is testable on a headless machine with no
display server, which is exactly where CI runs.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from copynion.models import Observation
from copynion.observer.backends.base import WindowBackend


class FakeBackend(WindowBackend):
    """Replays a fixed list of ``(app, title, idle_seconds)`` tuples."""

    name = "fake"
    requirement = "always available"

    def __init__(self, script: Sequence[tuple[str, str, float]], clock=None, loop: bool = False):
        self.script = list(script)
        self.loop = loop
        self.index = 0
        self._clock = clock or time.time

    @classmethod
    def is_available(cls) -> bool:
        return True

    @property
    def exhausted(self) -> bool:
        return not self.loop and self.index >= len(self.script)

    def poll(self) -> Observation:
        if not self.script:
            return Observation(self._clock(), "", "", backend=self.name)
        if self.index >= len(self.script):
            if not self.loop:
                return Observation(self._clock(), "", "", backend=self.name)
            self.index = 0
        app, title, idle = self.script[self.index]
        self.index += 1
        return Observation(
            timestamp=self._clock(), app=app, title=title, idle_seconds=idle, backend=self.name
        )
