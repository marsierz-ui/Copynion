"""Input capture: fidelity tiers, interlocks, decimation and ordering.

This is the most sensitive subsystem in the project, so it gets the most
paranoid tests. Several of these pin behaviour that a plausible-looking
refactor would silently break.
"""

from __future__ import annotations

import pytest

from copynion.inputs.events import Fidelity, InputBatch, KeyClass, classify_key
from copynion.inputs.recorder import InputRecorder, RecorderSettings


def make(fidelity="full", **kw) -> InputRecorder:
    return InputRecorder(RecorderSettings(fidelity=fidelity, **kw))


def type_text(recorder: InputRecorder, text: str, start: float = 0.0, step: float = 0.1) -> float:
    at = start
    for ch in text:
        recorder.record_key(ch, at)
        at += step
    return at


# -- classification -----------------------------------------------------------

@pytest.mark.parametrize(
    "key, expected",
    [
        ("a", KeyClass.PRINTABLE), ("Z", KeyClass.PRINTABLE), ("7", KeyClass.PRINTABLE),
        ("backspace", KeyClass.EDITING), ("delete", KeyClass.EDITING),
        ("left", KeyClass.NAVIGATION), ("page_up", KeyClass.NAVIGATION),
        ("enter", KeyClass.WHITESPACE), ("tab", KeyClass.WHITESPACE),
        ("ctrl", KeyClass.MODIFIER), ("shift_l", KeyClass.MODIFIER),
        ("f7", KeyClass.FUNCTION), ("", KeyClass.OTHER),
    ],
)
def test_key_classification(key, expected):
    assert classify_key(key) is expected


# -- fidelity tiers -----------------------------------------------------------

def test_off_records_nothing():
    r = make("off")
    type_text(r, "secret")
    r.record_click("left", 5, 5, 1.0)
    batch = r.take_batch(2.0)
    assert batch.events == []
    assert batch.counters.total == 0


def test_counts_records_totals_but_no_content():
    r = make("counts")
    type_text(r, "my password")
    r.record_click("left", 5, 5, 2.0)
    r.record_scroll(0, 3, 5, 5, 2.5)
    batch = r.take_batch(3.0)
    assert batch.events == [], "the counts tier must store no events at all"
    assert batch.counters.keystrokes == 11
    assert batch.counters.clicks == 1
    assert batch.counters.scrolls == 1


def test_structure_records_classes_but_not_characters():
    r = make("structure")
    at = type_text(r, "hunter2")
    r.record_key("enter", at)
    batch = r.take_batch(at + 1)
    serialised = str(batch.to_dict())
    assert "hunter2" not in serialised
    assert "hunter" not in serialised
    run = next(e for e in batch.events if e.key_class == KeyClass.PRINTABLE.value)
    assert run.count == 7
    assert run.text is None


def test_full_records_literal_text():
    r = make("full", redact_typed_text=False)
    at = type_text(r, "hello world")
    batch = r.take_batch(at)
    assert batch.events[0].text == "hello world"
    assert batch.events[0].count == 11


def test_full_redacts_typed_text_by_default():
    """The default keeps the shape of what was typed, not the value."""
    r = make("full")
    at = type_text(r, "invoice 4455667788 for bob@acme.com")
    batch = r.take_batch(at)
    text = batch.events[0].text
    assert "bob@acme.com" not in text
    assert "4455667788" not in text
    assert "<email>" in text and "<number>" in text


# -- interlocks ---------------------------------------------------------------

def test_suppression_records_nothing_but_counts_it():
    r = make("full")
    r.suppress("password field")
    type_text(r, "hunter2")
    r.record_click("left", 1, 2, 5.0)
    batch = r.take_batch(6.0)
    assert batch.events == []
    assert batch.counters.keystrokes == 0
    assert batch.counters.suppressed_events == 8


def test_suppression_discards_the_partial_buffer():
    """Learning mid-word that this is a password field must drop the word."""
    r = make("full")
    type_text(r, "hunte")
    r.suppress("secure input")
    batch = r.take_batch(9.0)
    assert batch.events == []
    assert "hunte" not in str(batch.to_dict())


def test_unsuppress_resumes_recording():
    r = make("full")
    r.suppress("password field")
    type_text(r, "secret")
    r.unsuppress()
    at = type_text(r, "public", start=10.0)
    batch = r.take_batch(at)
    assert batch.events[0].text == "public"
    assert "secret" not in str(batch.to_dict())


# -- shortcuts ----------------------------------------------------------------

def test_shortcuts_are_recorded_literally():
    """ctrl+c is not a secret, and a copy-paste loop is worth automating."""
    r = make("structure")
    r.record_key("c", 1.0, frozenset({"ctrl"}))
    r.record_key("v", 2.0, frozenset({"ctrl"}))
    batch = r.take_batch(3.0)
    assert [e.combo for e in batch.events] == ["ctrl+c", "ctrl+v"]
    assert batch.counters.shortcuts == 2


def test_unrecognised_modified_key_is_recorded_opaquely():
    r = make("full")
    r.record_key("§", 1.0, frozenset({"ctrl", "alt"}))
    batch = r.take_batch(2.0)
    assert batch.events[0].combo == "<other>"


def test_shift_alone_is_not_a_shortcut():
    r = make("full")
    r.record_key("A", 1.0, frozenset({"shift"}))
    batch = r.take_batch(2.0)
    assert batch.events[0].kind == "key"
    assert batch.counters.shortcuts == 0


def test_bare_modifier_presses_are_not_events():
    r = make("full")
    r.record_key("ctrl", 1.0)
    r.record_key("shift", 1.1)
    batch = r.take_batch(2.0)
    assert batch.events == []


def test_shortcut_commits_the_pending_text_run():
    r = make("full", redact_typed_text=False)
    type_text(r, "abc")
    r.record_key("s", 5.0, frozenset({"ctrl"}))
    batch = r.take_batch(6.0)
    assert [e.text for e in batch.events if e.kind == "key"] == ["abc"]
    assert batch.events[-1].combo == "ctrl+s"


# -- mouse --------------------------------------------------------------------

def test_mouse_moves_are_decimated_but_distance_is_exact():
    r = make("structure", move_min_distance=50.0, move_max_interval=999.0)
    for i in range(100):
        r.record_move(i, 0, i * 0.01)
    batch = r.take_batch(2.0)
    moves = [e for e in batch.events if e.kind == "move"]
    assert len(moves) < 10, "100 samples should not produce 100 stored points"
    assert batch.counters.mouse_distance == pytest.approx(99.0)


def test_click_coordinates_are_never_decimated():
    """The pointer position at a click is the key fact for replay."""
    r = make("structure")
    for i in range(5):
        r.record_click("left", 100 + i, 200 + i, i)
    batch = r.take_batch(6.0)
    clicks = [(e.x, e.y) for e in batch.events if e.kind == "click"]
    assert clicks == [(100, 200), (101, 201), (102, 202), (103, 203), (104, 204)]


def test_mouse_moves_can_be_disabled_while_clicks_remain():
    r = make("full", capture_mouse_moves=False)
    for i in range(50):
        r.record_move(i * 10, 0, i * 0.1)
    r.record_click("left", 7, 8, 9.0)
    batch = r.take_batch(10.0)
    assert not [e for e in batch.events if e.kind == "move"]
    assert [e for e in batch.events if e.kind == "click"]
    assert batch.counters.mouse_distance > 0


# -- ordering and limits ------------------------------------------------------

def test_events_come_out_in_chronological_order():
    """A coalesced text run starts before events appended during it.

    Without an explicit sort the run lands after them and replay reads
    backwards, which showed up as negative offsets in `copynion replay`.
    """
    r = make("full", redact_typed_text=False)
    type_text(r, "abcde", start=0.0, step=1.0)   # spans t=0..4
    r.record_click("left", 1, 1, 2.5)            # happens mid-run
    r.record_key("enter", 5.0)
    batch = r.take_batch(6.0)
    times = [e.at for e in batch.events]
    assert times == sorted(times), f"events out of order: {times}"
    assert batch.events[0].text == "abcde"
    assert batch.events[0].at == 0.0


def test_text_run_keeps_the_timestamp_of_its_first_key():
    r = make("full", redact_typed_text=False)
    type_text(r, "xy", start=0.0, step=0.5)
    r.record_key("enter", 10.0)
    batch = r.take_batch(11.0)
    assert batch.events[0].at == 0.0, "a run starting at t=0 must not inherit a later time"


def test_event_cap_is_enforced():
    r = make("structure", max_events_per_span=10)
    for i in range(500):
        r.record_click("left", i, i, i * 0.01)
    batch = r.take_batch(10.0)
    assert len(batch.events) == 10
    assert batch.counters.clicks == 500, "counters stay honest even when events are capped"


def test_take_batch_resets_state():
    r = make("full")
    type_text(r, "one")
    first = r.take_batch(5.0)
    second = r.take_batch(6.0)
    assert first.events and not second.events
    assert second.counters.keystrokes == 0


def test_batch_serialisation_roundtrip():
    r = make("full", redact_typed_text=False)
    at = type_text(r, "hi")
    r.record_click("right", 3, 4, at, count=2)
    r.record_scroll(1, -2, 5, 6, at + 1)
    batch = r.take_batch(at + 2)
    restored = InputBatch.from_dict(batch.to_dict())
    assert [e.to_dict() for e in restored.events] == [e.to_dict() for e in batch.events]
    assert restored.counters.to_dict() == batch.counters.to_dict()
    assert restored.fidelity == Fidelity.FULL.value


# -- backend reporting --------------------------------------------------------

def test_one_line_flattens_and_truncates_library_errors():
    """pynput's platform error is multi-line; `doctor` needs one line."""
    from copynion.inputs.backends.pynput_backend import _one_line

    messy = "failed to acquire X connection\n\nTry one of the following:\n\n * start an X server"
    assert "\n" not in _one_line(messy)
    assert len(_one_line("x" * 500)) <= 113


def test_backend_state_does_not_claim_missing_when_installed(monkeypatch):
    """Telling someone to install a package they already have is unhelpful.

    The exception type cannot distinguish the cases - pynput raises ImportError
    when installed but display-less - so the code asks the import system.
    """
    from copynion.inputs.backends import pynput_backend as pb

    # Pretend the probe already ran and failed with the package present.
    monkeypatch.setattr(pb, "_pynput", None)
    monkeypatch.setattr(pb, "_pynput_probed", True)
    monkeypatch.setattr(pb, "_pynput_error", "installed, but unusable here: no DISPLAY")

    state = pb.PynputBackend.describe_state()
    assert "not installed" not in state
    assert "pip install" not in state
    assert "no DISPLAY" in state


def test_backend_state_does_say_install_when_genuinely_absent(monkeypatch):
    from copynion.inputs.backends import pynput_backend as pb

    monkeypatch.setattr(pb, "_pynput", None)
    monkeypatch.setattr(pb, "_pynput_probed", True)
    monkeypatch.setattr(pb, "_pynput_error", "not installed")

    assert "pip install 'copynion[input]'" in pb.PynputBackend.describe_state()


def test_backend_report_includes_a_state_line():
    from copynion.inputs.backends import input_backend_report

    for entry in input_backend_report():
        assert entry["state"], "every backend must explain its availability"
        assert "\n" not in entry["state"]
