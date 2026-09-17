"""Platform backends and their auto-detection."""

from __future__ import annotations

from copynion.observer.backends.base import BackendUnavailable, WindowBackend
from copynion.observer.backends.fake import FakeBackend
from copynion.observer.backends.linux import LinuxBackend
from copynion.observer.backends.macos import MacOSBackend
from copynion.observer.backends.windows import WindowsBackend

#: Probed in order; the first available one wins.
REGISTRY: tuple[type[WindowBackend], ...] = (WindowsBackend, MacOSBackend, LinuxBackend)


def detect_backend() -> WindowBackend:
    """Return a backend for this machine, or raise with an actionable message."""
    for cls in REGISTRY:
        if cls.is_available():
            return cls()
    hints = [f"  - {c.name}: {c.requirement}" for c in REGISTRY]
    extra = ""
    if LinuxBackend.is_wayland():
        extra = (
            "\n\nYou appear to be on Wayland, which does not let ordinary apps read the\n"
            "active window. Options: run an X11/Xorg session, or use `copynion demo`\n"
            "to explore the pipeline with synthetic data."
        )
    raise BackendUnavailable(
        "No window backend is available here. Supported:\n" + "\n".join(hints) + extra
    )


def backend_report() -> list[dict[str, object]]:
    """Availability of every backend, for ``copynion doctor``."""
    return [
        {"name": c.name, "available": c.is_available(), "requirement": c.requirement}
        for c in REGISTRY
    ]


__all__ = [
    "BackendUnavailable",
    "FakeBackend",
    "LinuxBackend",
    "MacOSBackend",
    "REGISTRY",
    "WindowBackend",
    "WindowsBackend",
    "backend_report",
    "detect_backend",
]
