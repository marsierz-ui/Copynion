"""Interface for global input listeners."""

from __future__ import annotations

from abc import ABC, abstractmethod

from copynion.inputs.recorder import InputRecorder


class InputBackend(ABC):
    """Listens for global keyboard and mouse events and feeds an InputRecorder."""

    name: str = "base"
    requirement: str = ""

    @classmethod
    @abstractmethod
    def is_available(cls) -> bool:
        """True when this backend can capture input here."""

    @abstractmethod
    def start(self, recorder: InputRecorder) -> None:
        """Begin listening. Must not block."""

    @abstractmethod
    def stop(self) -> None:
        """Stop listening. Safe to call more than once."""
