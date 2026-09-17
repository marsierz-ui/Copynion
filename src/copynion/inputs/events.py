"""Input event types and key classification.

Stage 4 needs to *replay* a process, which means knowing what was typed and
clicked, not merely that typing happened. That is a genuine requirement and this
module serves it - but it is also the most dangerous data Copynion can hold, so
every event carries only what the configured fidelity tier permits, and the
classification below exists so that a useful middle ground is possible at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Fidelity(StrEnum):
    """How much of the user's input may be recorded.

    The tiers are a ladder of capability against exposure, and the choice is the
    user's to make explicitly - the default is the bottom rung.
    """

    OFF = "off"
    """Nothing. The default."""

    COUNTS = "counts"
    """Aggregate rates only - keystrokes/min, clicks, scroll, mouse distance.
    No key identity, no coordinates. Useful for statistics, useless for replay."""

    STRUCTURE = "structure"
    """Key *classes* (printable/navigation/editing), shortcut combinations, and
    mouse events with coordinates. Enough to recognise and match a repeated
    process; not enough to read what was typed."""

    FULL = "full"
    """Literal characters, coordinates and timings. Full replay fidelity."""

    @property
    def rank(self) -> int:
        return {"off": 0, "counts": 1, "structure": 2, "full": 3}[self.value]

    def at_least(self, other: Fidelity) -> bool:
        return self.rank >= other.rank


class KeyClass(StrEnum):
    """What kind of key was pressed, without saying which one."""

    PRINTABLE = "printable"
    WHITESPACE = "whitespace"
    NAVIGATION = "navigation"
    EDITING = "editing"
    MODIFIER = "modifier"
    FUNCTION = "function"
    OTHER = "other"


_NAVIGATION = {
    "up", "down", "left", "right", "home", "end", "page_up", "page_down",
    "pageup", "pagedown", "prior", "next",
}
_EDITING = {"backspace", "delete", "insert", "clear"}
_WHITESPACE = {"space", "tab", "enter", "return", "kp_enter"}
_MODIFIERS = {
    "shift", "shift_r", "shift_l", "ctrl", "ctrl_l", "ctrl_r", "control",
    "alt", "alt_l", "alt_r", "alt_gr", "cmd", "cmd_l", "cmd_r", "super", "meta",
    "caps_lock", "num_lock", "scroll_lock",
}


def classify_key(key: str) -> KeyClass:
    """Bucket a key name into a class.

    ``key`` is the backend's name for the key: a single character for printable
    keys, or a lowercase name like ``backspace`` for the rest.
    """
    if not key:
        return KeyClass.OTHER
    lowered = key.lower()
    if lowered in _MODIFIERS:
        return KeyClass.MODIFIER
    if lowered in _WHITESPACE:
        return KeyClass.WHITESPACE
    if lowered in _NAVIGATION:
        return KeyClass.NAVIGATION
    if lowered in _EDITING:
        return KeyClass.EDITING
    if len(lowered) >= 2 and lowered[0] == "f" and lowered[1:].isdigit():
        return KeyClass.FUNCTION
    if len(key) == 1 and key.isprintable():
        return KeyClass.PRINTABLE
    return KeyClass.OTHER


#: Keys that are never secret and are highly informative for automation
#: discovery: a copy-paste loop is exactly the kind of thing worth automating.
#: These are recorded literally from the STRUCTURE tier upward.
SAFE_SHORTCUT_KEYS = set("abcdefghijklmnopqrstuvwxyz0123456789") | {
    "tab", "enter", "return", "escape", "space", "delete", "backspace",
    "home", "end", "left", "right", "up", "down", "page_up", "page_down",
} | {f"f{n}" for n in range(1, 25)}


@dataclass(slots=True)
class InputEvent:
    """One recorded interaction.

    Fields are populated according to the fidelity tier in force when it was
    captured; ``kind`` says which ones are meaningful.
    """

    at: float
    kind: str
    """One of: ``key``, ``shortcut``, ``click``, ``scroll``, ``move``."""

    key_class: str | None = None
    text: str | None = None
    """The literal character(s). Only ever set at FULL fidelity."""

    combo: str | None = None
    """A shortcut such as ``ctrl+c``."""

    count: int | None = None
    """Run length: how many printable keys were coalesced into this event, or
    how many clicks (2 = double-click)."""

    button: str | None = None
    x: int | None = None
    y: int | None = None
    dx: int | None = None
    dy: int | None = None

    def to_dict(self) -> dict:
        """Compact dict for serialisation - omits everything unset."""
        out: dict = {"t": round(self.at, 3), "k": self.kind}
        for short, value in (
            ("c", self.key_class), ("s", self.text), ("m", self.combo),
            ("n", self.count), ("b", self.button), ("x", self.x), ("y", self.y),
            ("dx", self.dx), ("dy", self.dy),
        ):
            if value is not None:
                out[short] = value
        return out

    @staticmethod
    def from_dict(raw: dict) -> InputEvent:
        return InputEvent(
            at=raw["t"], kind=raw["k"], key_class=raw.get("c"), text=raw.get("s"),
            combo=raw.get("m"), count=raw.get("n"), button=raw.get("b"),
            x=raw.get("x"), y=raw.get("y"), dx=raw.get("dx"), dy=raw.get("dy"),
        )


@dataclass(slots=True)
class InputCounters:
    """Aggregates that are kept at every tier, including COUNTS.

    These carry no content whatsoever, which is why they can be stored in
    plaintext columns and survive the retention purge of the event detail.
    """

    keystrokes: int = 0
    printable_keys: int = 0
    editing_keys: int = 0
    navigation_keys: int = 0
    shortcuts: int = 0
    clicks: int = 0
    double_clicks: int = 0
    scrolls: int = 0
    mouse_distance: float = 0.0
    suppressed_events: int = 0
    """Events dropped by a secure-input or policy interlock. Counting them keeps
    the statistics honest without recording what they were."""

    def merge(self, other: InputCounters) -> None:
        self.keystrokes += other.keystrokes
        self.printable_keys += other.printable_keys
        self.editing_keys += other.editing_keys
        self.navigation_keys += other.navigation_keys
        self.shortcuts += other.shortcuts
        self.clicks += other.clicks
        self.double_clicks += other.double_clicks
        self.scrolls += other.scrolls
        self.mouse_distance += other.mouse_distance
        self.suppressed_events += other.suppressed_events

    def to_dict(self) -> dict:
        return {
            "keystrokes": self.keystrokes,
            "printable_keys": self.printable_keys,
            "editing_keys": self.editing_keys,
            "navigation_keys": self.navigation_keys,
            "shortcuts": self.shortcuts,
            "clicks": self.clicks,
            "double_clicks": self.double_clicks,
            "scrolls": self.scrolls,
            "mouse_distance": round(self.mouse_distance, 1),
            "suppressed_events": self.suppressed_events,
        }

    @property
    def total(self) -> int:
        return self.keystrokes + self.clicks + self.scrolls

    @property
    def edit_ratio(self) -> float:
        """Backspaces per printable key - a rough correction/hesitation signal."""
        return self.editing_keys / self.printable_keys if self.printable_keys else 0.0


@dataclass(slots=True)
class InputBatch:
    """Everything recorded during one activity span."""

    events: list[InputEvent] = field(default_factory=list)
    counters: InputCounters = field(default_factory=InputCounters)
    fidelity: str = Fidelity.OFF.value

    def to_dict(self) -> dict:
        return {
            "fidelity": self.fidelity,
            "counters": self.counters.to_dict(),
            "events": [e.to_dict() for e in self.events],
        }

    @staticmethod
    def from_dict(raw: dict) -> InputBatch:
        counters = InputCounters(**raw.get("counters", {}))
        return InputBatch(
            events=[InputEvent.from_dict(e) for e in raw.get("events", [])],
            counters=counters,
            fidelity=raw.get("fidelity", Fidelity.OFF.value),
        )
