"""Core data types shared by every stage of the pipeline.

These are plain dataclasses on purpose: they are easy to inspect, easy to
serialise for an export, and carry no behaviour that could quietly reach out to
the network or the filesystem.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from enum import StrEnum


class Visibility(StrEnum):
    """How much of an observation the privacy policy allows us to keep.

    The observer always captures at the highest fidelity the OS offers, then the
    policy immediately downgrades it. Nothing is written to disk before this
    decision has been made.
    """

    FULL = "full"
    """Application name plus a redacted window title."""

    APP_ONLY = "app_only"
    """Application name only; the title is dropped before it touches disk."""

    OPAQUE = "opaque"
    """Neither app nor title: only that *some* activity happened, for accurate totals."""

    DROP = "drop"
    """Nothing at all is recorded. Used for paused/private mode and denied apps."""


@dataclass(frozen=True, slots=True)
class Observation:
    """A single sample of what the user's foreground window looked like.

    This is the raw, in-memory form. It may still contain sensitive text in
    ``title`` and is never persisted in this shape.
    """

    timestamp: float
    app: str
    title: str
    idle_seconds: float = 0.0
    pid: int | None = None
    backend: str = "unknown"

    @property
    def is_idle(self) -> bool:
        return self.idle_seconds >= 0.0 and self.app == ""

    @staticmethod
    def now(app: str, title: str, **kw) -> Observation:
        return Observation(timestamp=time.time(), app=app, title=title, **kw)


@dataclass(frozen=True, slots=True)
class Category:
    """The outcome of classifying an activity."""

    name: str
    subcategory: str | None = None
    rule_id: str | None = None
    confidence: float = 0.0
    source: str = "rule"
    """One of ``rule``, ``default``, ``user``. ``user`` always wins and is never
    overwritten by a later rule change."""


@dataclass(slots=True)
class ActivitySpan:
    """A contiguous stretch of time spent on one app/title pair.

    The sampler collapses many identical observations into a single span, which
    is both cheaper to store and far less revealing than a raw event stream.
    """

    started_at: float
    ended_at: float
    app: str
    title: str = ""
    title_hash: str | None = None
    category: Category | None = None
    visibility: Visibility = Visibility.FULL
    afk: bool = False
    sample_count: int = 1
    backend: str = "unknown"
    id: int | None = None
    extra: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return max(0.0, self.ended_at - self.started_at)

    def redacted_copy(self) -> ActivitySpan:
        """Return a copy with the plaintext title removed.

        Used whenever a span leaves the trusted core (reports, exports without
        ``--include-titles``, anything handed to a future stage-3 model).
        """
        return replace(self, title="")

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration": self.duration,
            "app": self.app,
            "title_hash": self.title_hash,
            "category": self.category.name if self.category else "uncategorised",
            "subcategory": self.category.subcategory if self.category else None,
            "rule_id": self.category.rule_id if self.category else None,
            "confidence": self.category.confidence if self.category else 0.0,
            "afk": int(self.afk),
            "visibility": self.visibility.value,
            "sample_count": self.sample_count,
        }
