"""Input backends and their detection."""

from __future__ import annotations

from copynion.inputs.backends.base import InputBackend
from copynion.inputs.backends.fake import FakeInputBackend
from copynion.inputs.backends.pynput_backend import PynputBackend, unavailable_reason

REGISTRY: tuple[type[InputBackend], ...] = (PynputBackend,)


def detect_input_backend() -> InputBackend | None:
    """Return an input backend, or None when input capture is unavailable."""
    for cls in REGISTRY:
        if cls.is_available():
            return cls()
    return None


def input_backend_report() -> list[dict[str, object]]:
    return [
        {
            "name": c.name,
            "available": c.is_available(),
            "requirement": c.requirement,
            "state": c.describe_state(),
        }
        for c in REGISTRY
    ]


__all__ = [
    "FakeInputBackend",
    "InputBackend",
    "PynputBackend",
    "REGISTRY",
    "detect_input_backend",
    "input_backend_report",
    "unavailable_reason",
]
