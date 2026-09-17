"""Stage 1: sampling, span collapsing and the privacy ordering."""

from __future__ import annotations

from copynion.models import Observation, Visibility
from copynion.observer.backends import FakeBackend
from copynion.observer.sampler import Sampler
from tests.conftest import feed


def test_identical_samples_collapse_into_one_span(sampler, store):
    """Twelve samples on one window become one span, not twelve events."""
    feed(sampler, [("code", "main.py")] * 12)  # t = 10000 .. 10055
    sampler._flush(10_060.0)
    spans = store.spans()
    assert len(spans) == 1
    # Last sample at 10055; the flush at 10060 is within the extension cap, so
    # it counts as evidence the window was still in front of the user.
    assert spans[0]["duration"] == 60.0
    assert spans[0]["sample_count"] == 12


def test_switching_apps_closes_the_previous_span(sampler, store):
    feed(sampler, [("code", "a.py")] * 4 + [("firefox", "docs")] * 4)
    sampler._flush(10_040.0)
    spans = store.spans()
    assert [s["app"] for s in spans] == ["code", "firefox"]
    assert spans[0]["duration"] == 20.0


def test_alt_tab_noise_is_discarded(sampler, store):
    """A window touched for a second is not a fact worth keeping."""
    feed(sampler, [("code", "a.py"), ("slack", "#general"), ("code", "a.py")], step=1.0)
    sampler._flush(10_003.0)
    assert sampler.stats.spans_too_short >= 2
    assert all(s["duration"] >= 3.0 for s in store.spans())


def test_long_sessions_are_split_at_max_span(store, vault, settings, full_policy):
    settings.max_span_seconds = 60.0
    s = Sampler(FakeBackend([]), store, policy=full_policy, vault=vault, settings=settings,
                clock=lambda: 0.0)
    feed(s, [("code", "main.py")] * 40)
    s._flush(10_200.0)
    spans = store.spans()
    assert len(spans) > 1
    assert all(sp["duration"] <= 65.0 for sp in spans)


def test_idle_time_is_marked_afk_and_not_counted_as_work(sampler, store):
    feed(sampler, [("code", "a.py")] * 4 + [("code", "a.py", 300.0)] * 4)
    sampler._flush(10_040.0)
    spans = store.spans()
    assert [bool(s["afk"]) for s in spans] == [False, True]
    assert spans[1]["category"] == "idle"


def test_dropped_windows_are_never_stored(store, vault, settings):
    from copynion.privacy import Policy

    policy = Policy(app_rules={"code": "full", "1password": "drop"})
    s = Sampler(FakeBackend([]), store, policy=policy, vault=vault, settings=settings,
                clock=lambda: 0.0)
    feed(s, [("code", "a.py")] * 3 + [("1password", "Bank login")] * 5 + [("code", "a.py")] * 3)
    s._flush(10_055.0)
    apps = [sp["app"] for sp in store.spans()]
    assert "1password" not in apps
    assert s.stats.dropped_by_policy == 5


def test_titles_are_redacted_before_they_are_sealed(sampler, store):
    feed(sampler, [("firefox", "Invoice for john.doe@acme.com")] * 4)
    sampler._flush(10_020.0)
    span = store.spans()[0]
    stored = store.title_of(span["id"])
    assert "john.doe@acme.com" not in stored
    assert "<email>" in stored
    assert sampler.stats.redaction_hits.get("email") == 4


def test_app_only_windows_store_no_title(store, vault, settings):
    from copynion.privacy import Policy

    s = Sampler(FakeBackend([]), store, policy=Policy(), vault=vault, settings=settings,
                clock=lambda: 0.0)
    feed(s, [("someapp", "Very sensitive client name")] * 4)
    s._flush(10_020.0)
    span = store.spans()[0]
    assert span["visibility"] == Visibility.APP_ONLY.value
    assert span["title_hash"] is None
    assert store.title_of(span["id"]) is None


def test_transitions_are_recorded(sampler, store):
    feed(sampler, [("code", "a")] * 3 + [("firefox", "b")] * 3 + [("code", "a")] * 3)
    sampler._flush(10_045.0)
    pairs = {(t["from_app"], t["to_app"]): t["count"] for t in store.transitions()}
    assert pairs[("code", "firefox")] == 1
    assert pairs[("firefox", "code")] == 1


def test_suspend_gap_does_not_invent_time(sampler, store):
    """Closing the lid for three days must not become a three-day work session."""
    feed(sampler, [("code", "a.py")] * 3)
    sampler._flush(10_010.0 + 3 * 86400)
    assert store.spans()[0]["duration"] < 30.0


def test_backend_failure_is_survivable(store, vault, settings, full_policy):
    class ExplodingBackend(FakeBackend):
        def poll(self):
            raise RuntimeError("compositor went away")

    s = Sampler(ExplodingBackend([]), store, policy=full_policy, vault=vault, settings=settings,
                clock=lambda: 0.0)
    assert s.tick() is None
    assert s.stats.backend_failures == 1


def test_run_loop_stops_after_max_ticks(store, vault, settings, full_policy):
    backend = FakeBackend([("code", "a.py", 0.0)], loop=True)
    s = Sampler(backend, store, policy=full_policy, vault=vault, settings=settings)
    s.settings.poll_interval_seconds = 0.001
    stats = s.run(max_ticks=5)
    assert stats.samples == 5


# -- input capture integration ------------------------------------------------

def _recorder(fidelity="full", **kw):
    from copynion.inputs.recorder import InputRecorder, RecorderSettings

    return InputRecorder(RecorderSettings(fidelity=fidelity, **kw))


def _sampler_with_input(store, vault, settings, policy, recorder):
    return Sampler(FakeBackend([]), store, policy=policy, vault=vault,
                   settings=settings, recorder=recorder, clock=lambda: 0.0)


def test_input_is_attached_to_the_span_it_happened_in(store, vault, settings, full_policy):
    rec = _recorder(redact_typed_text=False)
    s = _sampler_with_input(store, vault, settings, full_policy, rec)
    t = 10_000.0
    s.ingest(Observation(t, "code", "a.py", backend="f"))
    for ch in "hello":
        rec.record_key(ch, t)
        t += 0.2
    t = 10_010.0
    s.ingest(Observation(t, "firefox", "docs", backend="f"))
    for ch in "world":
        rec.record_key(ch, t)
        t += 0.2
    s._flush(10_020.0)

    spans = store.spans()
    assert [store.inputs_of(sp["id"]).events[0].text for sp in spans] == ["hello", "world"]


def test_policy_denied_app_suppresses_input(store, vault, settings):
    """Input must never outlive the policy that hides the window's title."""
    from copynion.privacy import Policy

    rec = _recorder(redact_typed_text=False)
    policy = Policy(app_rules={"code": "full", "1password": "drop"})
    s = _sampler_with_input(store, vault, settings, policy, rec)

    t = 10_000.0
    s.ingest(Observation(t, "code", "a.py", backend="f"))
    for ch in "public":
        rec.record_key(ch, t)
        t += 0.1
    t = 10_010.0
    s.ingest(Observation(t, "1password", "Bank", backend="f"))
    for ch in "hunter2":
        rec.record_key(ch, t)
        t += 0.1
    s._flush(10_020.0)

    stored = " ".join(
        str(store.inputs_of(sp["id"]).to_dict()) for sp in store.spans()
        if store.inputs_of(sp["id"])
    )
    assert "public" in stored
    assert "hunter2" not in stored
    assert s.stats.input_suppressed == 7


def test_app_only_visibility_also_suppresses_input(store, vault, settings):
    """Hiding titles while recording every keystroke would make the policy a lie."""
    from copynion.privacy import Policy

    rec = _recorder(redact_typed_text=False)
    s = _sampler_with_input(store, vault, settings, Policy(), rec)  # default app_only
    t = 10_000.0
    s.ingest(Observation(t, "someapp", "Client X", backend="f"))
    for ch in "sensitive":
        rec.record_key(ch, t)
        t += 0.1
    s._flush(10_020.0)
    assert store.counts()["input_batches"] == 0
    assert s.stats.input_suppressed == 9


def test_input_is_banked_before_a_denied_window_suppresses_it(store, vault, settings):
    """The buffer at suppression time belongs to the previous, permitted window."""
    from copynion.privacy import Policy

    rec = _recorder(redact_typed_text=False)
    policy = Policy(app_rules={"code": "full", "1password": "drop"})
    s = _sampler_with_input(store, vault, settings, policy, rec)

    t = 10_000.0
    s.ingest(Observation(t, "code", "a.py", backend="f"))
    for ch in "typed":  # never terminated by Enter - only the flush commits it
        rec.record_key(ch, t)
        t += 0.1
    s.ingest(Observation(10_010.0, "1password", "Bank", backend="f"))
    s._flush(10_020.0)

    batches = [store.inputs_of(sp["id"]) for sp in store.spans()]
    texts = [e.text for b in batches if b for e in b.events]
    assert "typed" in texts, "input from the permitted window was discarded"


def test_pause_stops_input_capture(store, vault, settings, full_policy):
    rec = _recorder(redact_typed_text=False)
    s = _sampler_with_input(store, vault, settings, full_policy, rec)
    t = 10_000.0
    s.ingest(Observation(t, "code", "a.py", backend="f"))
    full_policy.pause("lunch")
    s.ingest(Observation(10_010.0, "code", "a.py", backend="f"))
    for ch in "private":
        rec.record_key(ch, 10_011.0)
    s._flush(10_020.0)
    stored = " ".join(
        str(store.inputs_of(sp["id"]).to_dict()) for sp in store.spans()
        if store.inputs_of(sp["id"])
    )
    assert "private" not in stored


def test_no_recorder_means_no_input_columns(store, vault, settings, full_policy):
    s = Sampler(FakeBackend([]), store, policy=full_policy, vault=vault,
                settings=settings, clock=lambda: 0.0)
    feed(s, [("code", "a.py")] * 4)
    s._flush(10_020.0)
    row = store.spans()[0]
    assert row["keystrokes"] == 0 and row["clicks"] == 0
    assert store.counts()["input_batches"] == 0
