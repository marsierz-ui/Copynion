"""Backend interface for reading the foreground window.

A backend answers two questions about *this instant*: which window has focus,
and how long since the user last touched the machine. Deliberately nothing else
- no screenshots, no accessibility-tree walking, no keystroke content. Stage 1
is about the shape of the day, not its contents.
"""

from __future__ import annotations

import shutil
import subprocess
from abc import ABC, abstractmethod

from copynion.models import Observation


class BackendUnavailable(RuntimeError):
    """The backend cannot run here (wrong OS, missing helper, no permission)."""


class WindowBackend(ABC):
    """Reads the active window and the idle timer."""

    name: str = "base"
    #: Human-readable note shown by ``copynion doctor`` when unavailable.
    requirement: str = ""

    @classmethod
    @abstractmethod
    def is_available(cls) -> bool:
        """True when this backend can actually read windows on this system."""

    @abstractmethod
    def poll(self) -> Observation:
        """Return one observation. Must never raise for transient failures.

        When the window cannot be read (locked screen, a compositor that refuses
        to say), return an observation with an empty ``app``. The policy layer
        turns that into an OPAQUE record so totals stay correct without
        pretending to know more than we do.
        """

    def close(self) -> None:  # noqa: B027 - an optional hook, not a requirement
        """Release any handles. Safe to call more than once.

        Deliberately concrete and empty: most backends hold nothing that needs
        releasing, and forcing every one of them to write an empty override
        would be noise.
        """


def _run(cmd: list[str], timeout: float = 1.0) -> str | None:
    """Run a helper command, returning stripped stdout or None.

    Every backend failure has to be non-fatal: a wedged helper must cost one
    sample, not the session.
    """
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _have(binary: str) -> bool:
    return shutil.which(binary) is not None
