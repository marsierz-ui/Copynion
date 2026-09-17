"""Turns raw input callbacks into a storable, tier-appropriate batch.

This is the piece that decides what is actually kept. It is deliberately pure -
no OS hooks, no I/O - so the rules below can be tested exhaustively without a
display server, which matters because these rules are the difference between a
process recorder and a keylogger.

Three mechanisms keep the volume and the exposure down:

*Tiering* - :class:`Fidelity` decides whether a keystroke is stored as a literal
character, as a class ("something printable"), or only as a number.

*Coalescing* - a run of printable keys becomes one event with a length, and
mouse movement is decimated to the points that carry information (a direction
change, or the position at a click). Storing every 125 Hz mouse sample would be
both enormous and no more useful.

*Interlocks* - suppression that configuration cannot switch off: secure input
fields, denied applications, and pause.
"""

from __future__ import annotations

from dataclasses import dataclass

from copynion.inputs.events import (
    SAFE_SHORTCUT_KEYS,
    Fidelity,
    InputBatch,
    InputCounters,
    InputEvent,
    KeyClass,
    classify_key,
)
from copynion.privacy.redact import Redactor

#: Keys that end a typed run and commit the buffer.
_COMMIT_KEYS = {"enter", "return", "kp_enter", "tab", "escape"}


@dataclass(slots=True)
class RecorderSettings:
    fidelity: str = Fidelity.OFF.value

    redact_typed_text: bool = True
    """Run captured text through the title redactor before storing.

    On by default, and it is not merely a privacy measure: for *replicating* a
    process you want the variable slot, not one run's value. "Type <the invoice
    number> here" generalises; "type 4455667788" does not. Turn it off only if
    you need literal replay of constant text.
    """

    capture_mouse_moves: bool = True
    move_min_distance: float = 40.0
    """Pixels the pointer must travel before another move point is recorded."""

    move_max_interval: float = 0.5
    """...or this many seconds, whichever comes first."""

    max_events_per_span: int = 5000
    """Hard ceiling, so a stuck key cannot fill the disk."""


class InputRecorder:
    """Accumulates input for the current activity span."""

    def __init__(
        self,
        settings: RecorderSettings | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self.settings = settings or RecorderSettings()
        self.fidelity = Fidelity(self.settings.fidelity)
        self.redactor = redactor or Redactor()

        self._events: list[InputEvent] = []
        self._counters = InputCounters()
        self._text_buffer: list[str] = []
        self._buffer_started_at: float | None = None
        self._printable_run = 0

        self._suppressed = False
        self._suppress_reason = ""
        self._last_move: tuple[float, float] | None = None
        self._last_recorded_move: tuple[float, float, float] | None = None
        self._dropped_for_cap = 0

    # -- interlocks -----------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self.fidelity is not Fidelity.OFF

    @property
    def suppressed(self) -> bool:
        return self._suppressed

    def suppress(self, reason: str) -> None:
        """Stop recording input entirely until :meth:`unsuppress`.

        Called for password fields (secure input), denied applications and
        pause. Any buffered text is discarded rather than committed - if we
        learn mid-word that this is a password field, the partial word must not
        survive.
        """
        if not self._suppressed:
            self._discard_buffer()
        self._suppressed = True
        self._suppress_reason = reason

    def unsuppress(self) -> None:
        self._suppressed = False
        self._suppress_reason = ""

    @property
    def suppress_reason(self) -> str:
        return self._suppress_reason

    # -- recording ------------------------------------------------------------

    def record_key(self, key: str, at: float, modifiers: frozenset[str] | None = None) -> None:
        """Record one key press.

        ``key`` is a single character for printable keys, otherwise a lowercase
        name (``backspace``, ``f5``). ``modifiers`` holds any held modifiers.
        """
        if not self._admit():
            return

        modifiers = modifiers or frozenset()
        real_modifiers = modifiers - {"shift"}  # shift alone is just capitalisation
        key_class = classify_key(key)

        self._counters.keystrokes += 1
        if key_class is KeyClass.PRINTABLE:
            self._counters.printable_keys += 1
        elif key_class is KeyClass.EDITING:
            self._counters.editing_keys += 1
        elif key_class is KeyClass.NAVIGATION:
            self._counters.navigation_keys += 1

        if self.fidelity is Fidelity.COUNTS:
            return

        # Shortcut combinations are recorded literally from STRUCTURE upward:
        # ctrl+c is not a secret, and a copy-paste loop is exactly the kind of
        # repetition worth automating.
        if real_modifiers and key.lower() in SAFE_SHORTCUT_KEYS:
            self._commit_buffer(at)
            self._counters.shortcuts += 1
            combo = "+".join(sorted(real_modifiers) + [key.lower()])
            self._append(InputEvent(at=at, kind="shortcut", combo=combo))
            return
        if real_modifiers:
            # A modified key we do not recognise: record that it happened, not what it was.
            self._commit_buffer(at)
            self._counters.shortcuts += 1
            self._append(InputEvent(at=at, kind="shortcut", combo="<other>"))
            return

        if key_class is KeyClass.PRINTABLE:
            if self._buffer_started_at is None:
                self._buffer_started_at = at
            if self.fidelity is Fidelity.FULL:
                self._text_buffer.append(key)
            else:
                self._printable_run += 1
            return

        # Any non-printable key ends the current run.
        self._commit_buffer(at)
        if key_class is KeyClass.MODIFIER:
            return  # a bare modifier press carries no information
        self._append(
            InputEvent(at=at, kind="key", key_class=key_class.value,
                       text=key.lower() if self.fidelity is Fidelity.FULL else None)
        )

    def record_click(self, button: str, x: int, y: int, at: float, count: int = 1) -> None:
        if not self._admit():
            return
        self._counters.clicks += 1
        if count >= 2:
            self._counters.double_clicks += 1
        if self.fidelity is Fidelity.COUNTS:
            return
        self._commit_buffer(at)
        # The pointer position at the moment of a click is the single most
        # valuable coordinate for replay, so it is never decimated away.
        self._append(
            InputEvent(at=at, kind="click", button=button, x=int(x), y=int(y),
                       count=count if count > 1 else None)
        )
        self._last_recorded_move = (float(x), float(y), at)

    def record_scroll(self, dx: int, dy: int, x: int, y: int, at: float) -> None:
        if not self._admit():
            return
        self._counters.scrolls += 1
        if self.fidelity is Fidelity.COUNTS:
            return
        self._append(InputEvent(at=at, kind="scroll", dx=int(dx), dy=int(dy),
                                x=int(x), y=int(y)))

    def record_move(self, x: float, y: float, at: float) -> None:
        """Record pointer movement, decimated.

        Distance always accrues to the counters; a *point* is only stored when
        the pointer has travelled far enough or enough time has passed.
        """
        if not self._admit():
            return
        if self._last_move is not None:
            dx, dy = x - self._last_move[0], y - self._last_move[1]
            self._counters.mouse_distance += (dx * dx + dy * dy) ** 0.5
        self._last_move = (x, y)

        if self.fidelity.rank < Fidelity.STRUCTURE.rank or not self.settings.capture_mouse_moves:
            return

        if self._last_recorded_move is None:
            self._append(InputEvent(at=at, kind="move", x=int(x), y=int(y)))
            self._last_recorded_move = (x, y, at)
            return

        px, py, pat = self._last_recorded_move
        travelled = ((x - px) ** 2 + (y - py) ** 2) ** 0.5
        if travelled >= self.settings.move_min_distance or (
            at - pat >= self.settings.move_max_interval and travelled > 0
        ):
            self._append(InputEvent(at=at, kind="move", x=int(x), y=int(y)))
            self._last_recorded_move = (x, y, at)

    # -- batching -------------------------------------------------------------

    def take_batch(self, at: float) -> InputBatch:
        """Close the current batch and start a new one.

        Called when the sampler flushes a span, so each span carries exactly the
        input that happened during it.
        """
        self._commit_buffer(at)
        # A coalesced run of printable keys carries the timestamp of its *first*
        # key but is appended when the run ends, so it can land after events that
        # happened during it. Replay depends on chronological order, so sort on
        # the way out. The sort is stable, so events sharing a timestamp keep the
        # order they were recorded in.
        self._events.sort(key=lambda e: e.at)
        batch = InputBatch(
            events=self._events,
            counters=self._counters,
            fidelity=self.fidelity.value,
        )
        self._events = []
        self._counters = InputCounters()
        self._last_recorded_move = None
        self._dropped_for_cap = 0
        return batch

    # -- internals ------------------------------------------------------------

    def _admit(self) -> bool:
        if not self.enabled:
            return False
        if self._suppressed:
            self._counters.suppressed_events += 1
            return False
        return True

    def _append(self, event: InputEvent) -> None:
        if len(self._events) >= self.settings.max_events_per_span:
            self._dropped_for_cap += 1
            return
        self._events.append(event)

    def _commit_buffer(self, at: float) -> None:
        """Flush accumulated printable keys into a single event."""
        if self.fidelity is Fidelity.FULL and self._text_buffer:
            text = "".join(self._text_buffer)
            length = len(text)
            if self.settings.redact_typed_text:
                text = self.redactor.scrub_text(text)
            started = self._buffer_started_at if self._buffer_started_at is not None else at
            self._append(
                InputEvent(at=started, kind="key",
                           key_class=KeyClass.PRINTABLE.value, text=text, count=length)
            )
        elif self._printable_run:
            started = self._buffer_started_at if self._buffer_started_at is not None else at
            self._append(
                InputEvent(at=started, kind="key",
                           key_class=KeyClass.PRINTABLE.value, count=self._printable_run)
            )
        self._discard_buffer()

    def _discard_buffer(self) -> None:
        self._text_buffer = []
        self._printable_run = 0
        self._buffer_started_at = None
