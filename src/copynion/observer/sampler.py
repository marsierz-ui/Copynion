"""Stage 1: turn a stream of samples into stored activity spans.

The sampler polls the foreground window on a timer and collapses consecutive
identical samples into one span. Collapsing is not just a storage optimisation -
it is a privacy measure. A raw event stream at 5-second resolution is a
near-perfect reconstruction of someone's day; a list of spans is the summary a
person would give you themselves.

Ordering inside :meth:`tick` is load-bearing and must not be rearranged:

    poll -> policy -> redact -> fingerprint -> categorise -> buffer -> store

Policy runs before redaction so that a dropped window is never even scrubbed,
and redaction runs before fingerprinting so the hash covers the cleaned text.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from copynion.categorize.engine import IDLE_CATEGORY, Categoriser
from copynion.config import ObservationSettings
from copynion.inputs.recorder import InputRecorder
from copynion.models import ActivitySpan, Observation, Visibility
from copynion.observer.backends.base import WindowBackend
from copynion.privacy.policy import Policy
from copynion.privacy.redact import Redactor
from copynion.privacy.vault import Vault
from copynion.storage.store import Store


@dataclass(slots=True)
class SamplerStats:
    """Counters for ``copynion status`` - and for the user to sanity-check us."""

    samples: int = 0
    spans_written: int = 0
    spans_too_short: int = 0
    dropped_by_policy: int = 0
    titles_stored: int = 0
    titles_withheld: int = 0
    redaction_hits: dict[str, int] = field(default_factory=dict)
    backend_failures: int = 0
    input_events: int = 0
    input_suppressed: int = 0

    def note_redactions(self, hits: dict[str, int]) -> None:
        for name, count in hits.items():
            self.redaction_hits[name] = self.redaction_hits.get(name, 0) + count

    def as_dict(self) -> dict:
        return {
            "samples": self.samples,
            "spans_written": self.spans_written,
            "spans_too_short": self.spans_too_short,
            "dropped_by_policy": self.dropped_by_policy,
            "titles_stored": self.titles_stored,
            "titles_withheld": self.titles_withheld,
            "redaction_hits": dict(self.redaction_hits),
            "backend_failures": self.backend_failures,
            "input_events": self.input_events,
            "input_suppressed": self.input_suppressed,
        }


class Sampler:
    """Drives the observation pipeline."""

    def __init__(
        self,
        backend: WindowBackend,
        store: Store,
        *,
        policy: Policy | None = None,
        redactor: Redactor | None = None,
        vault: Vault | None = None,
        categoriser: Categoriser | None = None,
        settings: ObservationSettings | None = None,
        recorder: InputRecorder | None = None,
        clock=time.time,
    ) -> None:
        self.backend = backend
        self.store = store
        self.policy = policy or Policy()
        self.redactor = redactor or Redactor()
        self.vault = vault or store.vault
        self.categoriser = categoriser or Categoriser(overrides=store.overrides())
        self.settings = settings or ObservationSettings()
        self.recorder = recorder
        self.clock = clock
        self.stats = SamplerStats()

        #: How far past the last observed sample a span may be extended on flush.
        #: Two intervals tolerates one slow poll without inventing time.
        self._max_extension = max(self.settings.poll_interval_seconds * 2, 1.0)

        self._current: ActivitySpan | None = None
        self._current_key: tuple | None = None
        self._last_app: str | None = None

    # -- the pipeline ---------------------------------------------------------

    def tick(self) -> ActivitySpan | None:
        """Take one sample. Returns a span if this sample completed one."""
        try:
            obs = self.backend.poll()
        except Exception:
            # A backend must never stop the session. Count it, carry on, and let
            # `copynion doctor` surface a persistent problem.
            self.stats.backend_failures += 1
            return None

        self.stats.samples += 1
        return self.ingest(obs)

    def ingest(self, obs: Observation) -> ActivitySpan | None:
        """Process a single observation. Separated from :meth:`tick` for testing."""
        now = obs.timestamp
        afk = obs.idle_seconds >= self.settings.idle_threshold_seconds

        permitted, decision = self.policy.apply(obs)
        if permitted is None:
            self.stats.dropped_by_policy += 1
            # Close whatever was open: the user's privacy choice ends the span.
            # Flush *before* tightening the interlock - at this moment the
            # recorder still holds input belonging to the previous, permitted
            # window, and suppression discards its buffer.
            flushed = self._flush(now)
            self._sync_input_interlock(decision, permitted)
            return flushed

        title = ""
        if decision.visibility is Visibility.FULL and permitted.title:
            report = self.redactor.scrub(permitted.title)
            title = report.text
            self.stats.note_redactions(report.hits)

        title_hash = self.vault.fingerprint(title) if title else None
        category = (
            IDLE_CATEGORY
            if afk
            else self.categoriser.classify_hashed(permitted.app, title_hash, title)
        )

        key = (permitted.app, title_hash, afk, decision.visibility)
        if key == self._current_key and self._current is not None:
            self._current.ended_at = now
            self._current.sample_count += 1
            if self._current.duration >= self.settings.max_span_seconds:
                flushed = self._flush(now)
                self._start(now, permitted, title, title_hash, category, decision.visibility, afk)
                return flushed
            return None

        # Same ordering rule as above: the open span's input is banked before the
        # new window's policy can suppress the recorder.
        flushed = self._flush(now)
        self._sync_input_interlock(decision, permitted)
        self._start(now, permitted, title, title_hash, category, decision.visibility, afk)
        return flushed

    def _sync_input_interlock(self, decision, permitted) -> None:
        """Keep input capture in step with the window policy.

        Input is at least as sensitive as the title, so it is suppressed
        whenever the title would be withheld: denied apps, password managers,
        private-browsing windows and pause. Without this, setting an app to
        ``app_only`` would hide its titles while still recording every character
        typed into it - which would make the policy a lie.
        """
        if self.recorder is None or not self.recorder.enabled:
            return
        # Anything short of FULL visibility means the title is withheld, and
        # input is strictly more sensitive than a title.
        if permitted is None or not decision.keeps_title:
            self.recorder.suppress(f"policy: {decision.reason}")
        elif self.recorder.suppress_reason.startswith("policy:"):
            self.recorder.unsuppress()

    def _start(self, now, obs, title, title_hash, category, visibility, afk) -> None:
        self._current = ActivitySpan(
            started_at=now,
            ended_at=now,
            app=obs.app,
            title=title,
            title_hash=title_hash,
            category=category,
            visibility=visibility,
            afk=afk,
            sample_count=1,
            backend=obs.backend,
        )
        self._current_key = (obs.app, title_hash, afk, visibility)

    def _flush(self, now: float | None = None) -> ActivitySpan | None:
        span = self._current
        self._current, self._current_key = None, None

        # Always close the input batch, even when no span is open. Suppressed
        # events are counted here, and dropping them on the floor would make
        # `watch` under-report how much it refused to record - exactly the
        # number a user needs to trust the interlocks.
        batch = None
        if self.recorder is not None and self.recorder.enabled:
            at = now if now is not None else (span.ended_at if span else self.clock())
            batch = self.recorder.take_batch(at)
            self.stats.input_suppressed += batch.counters.suppressed_events

        if span is None:
            return None
        if now is not None:
            # The user was only ever *observed* at `span.ended_at`. Credit at most
            # a couple of polling intervals beyond that, because `now` can be far
            # in the future for entirely mundane reasons: the laptop was suspended,
            # NTP stepped the clock, or the process was stopped and resumed. Without
            # this cap, closing the lid on Friday and opening it on Monday would be
            # recorded as one 72-hour coding session.
            span.ended_at = max(span.ended_at, min(now, span.ended_at + self._max_extension))

        if span.duration < self.settings.min_span_seconds:
            self.stats.spans_too_short += 1
            return None

        self.store.add_span(span, batch)
        self.stats.spans_written += 1
        if batch is not None:
            # Counted after storage, so the number means "events kept", not
            # "events seen".
            self.stats.input_events += len(batch.events)
        if span.title and span.visibility is Visibility.FULL:
            if self.vault.can_store_titles:
                self.stats.titles_stored += 1
            else:
                self.stats.titles_withheld += 1

        if self._last_app is not None and not span.afk:
            self.store.record_transition(self._last_app, span.app, span.started_at)
        if not span.afk:
            self._last_app = span.app
        return span

    def flush(self) -> ActivitySpan | None:
        """Close the open span, e.g. on shutdown."""
        return self._flush(self.clock())

    # -- the loop -------------------------------------------------------------

    def run(self, stop: threading.Event | None = None, max_ticks: int | None = None) -> SamplerStats:
        """Poll until ``stop`` is set (or ``max_ticks`` samples have been taken)."""
        stop = stop or threading.Event()
        interval = self.settings.poll_interval_seconds
        ticks = 0
        try:
            while not stop.is_set():
                started = self.clock()
                self.tick()
                ticks += 1
                if max_ticks is not None and ticks >= max_ticks:
                    break
                # Sleep the remainder of the interval so slow polls do not drift.
                elapsed = self.clock() - started
                if stop.wait(max(0.0, interval - elapsed)):
                    break
        finally:
            self.flush()
            self.backend.close()
        return self.stats
