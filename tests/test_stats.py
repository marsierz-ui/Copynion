"""Stage 2: the statistics themselves."""

from __future__ import annotations

from datetime import datetime

from copynion.stats import summarise
from copynion.stats.report import human_duration, render, render_compact

BASE = datetime(2026, 5, 4, 9, 0, 0).timestamp()  # a Monday at 09:00


def span(offset_min, length_min, app="code", category="development", title_hash=None, afk=False,
         day="2026-05-04"):
    start = BASE + offset_min * 60
    return {
        "started_at": start,
        "ended_at": start + length_min * 60,
        "duration": length_min * 60.0,
        "day": day,
        "app": app,
        "category": category,
        "title_hash": title_hash,
        "afk": int(afk),
    }


def test_empty_input_is_handled():
    summary = summarise([])
    assert summary.span_count == 0
    assert "No activity recorded" in render(summary)


def test_totals_separate_active_from_idle():
    summary = summarise([span(0, 30), span(30, 15, afk=True, category="idle"), span(45, 30)])
    assert summary.active_seconds == 60 * 60
    assert summary.idle_seconds == 15 * 60
    assert summary.tracked_seconds == 75 * 60


def test_category_shares_are_of_active_time():
    summary = summarise([span(0, 30), span(30, 30, app="slack", category="communication")])
    assert summary.category("development").share == 0.5
    assert summary.category("communication").share == 0.5
    assert sum(c.share for c in summary.categories) == 1.0


def test_context_switches_count_app_changes():
    summary = summarise([
        span(0, 10, app="code"), span(10, 5, app="slack"),
        span(15, 10, app="code"), span(25, 5, app="slack"),
    ])
    assert summary.context_switches == 3


def test_focus_blocks_merge_adjacent_same_category_work():
    summary = summarise([span(0, 20), span(20, 20), span(40, 20)])
    assert len(summary.focus_blocks) == 1
    assert summary.focus_blocks[0].seconds == 60 * 60


def test_focus_block_is_broken_by_non_focus_work():
    summary = summarise([
        span(0, 20),
        span(20, 10, app="slack", category="communication"),
        span(30, 20),
    ])
    assert len(summary.focus_blocks) == 2


def test_short_stretches_are_not_focus_blocks():
    summary = summarise([span(0, 5), span(10, 5)])
    assert summary.focus_blocks == []


def test_fragmentation_reflects_switching():
    calm = summarise([span(i * 30, 30) for i in range(4)])
    frantic = summarise([
        span(i * 2, 2, app="code" if i % 2 else "slack",
             category="development" if i % 2 else "communication")
        for i in range(60)
    ])
    assert calm.fragmentation < frantic.fragmentation
    assert frantic.switches_per_active_hour > calm.switches_per_active_hour


def test_recurring_windows_need_a_title_hash():
    """Without a fingerprint we cannot tell windows apart, so we must not guess."""
    summary = summarise([span(i * 10, 10) for i in range(5)])
    assert summary.recurring_windows == []

    hashed = summarise([span(i * 10, 10, title_hash="abc") for i in range(5)])
    assert len(hashed.recurring_windows) == 1
    assert hashed.recurring_windows[0].occurrences == 5


def test_recurrence_threshold_is_respected():
    spans = [span(i * 10, 10, title_hash="abc") for i in range(2)]
    assert summarise(spans, min_recurrence=3).recurring_windows == []
    assert summarise(spans, min_recurrence=2).recurring_windows != []


def test_repeated_paths_are_detected():
    """The morning routine: mail -> sheet -> mail, three days running."""
    spans = []
    for day in range(3):
        offset = day * 600
        spans += [
            span(offset, 10, app="thunderbird", category="communication", day=f"2026-05-0{4+day}"),
            span(offset + 10, 20, app="localc", category="data_entry", day=f"2026-05-0{4+day}"),
            span(offset + 30, 10, app="thunderbird", category="communication", day=f"2026-05-0{4+day}"),
        ]
    summary = summarise(spans)
    paths = {s.apps for s in summary.sequences}
    assert ("thunderbird", "localc") in paths
    assert ("thunderbird", "localc", "thunderbird") in paths
    routine = next(s for s in summary.sequences if s.apps == ("thunderbird", "localc", "thunderbird"))
    assert routine.occurrences == 3
    assert routine.distinct_days == 3


def test_single_app_repetition_is_not_a_workflow():
    summary = summarise([span(i * 10, 10, app="code") for i in range(10)])
    assert summary.sequences == []


def test_consecutive_duplicates_collapse_before_sequencing():
    spans = []
    for day in range(3):
        offset = day * 600
        spans += [span(offset, 10, app="a"), span(offset + 10, 10, app="a"),
                  span(offset + 20, 10, app="b")]
    summary = summarise(spans)
    assert ("a", "b") in {s.apps for s in summary.sequences}
    assert ("a", "a") not in {s.apps for s in summary.sequences}


def test_hourly_distribution_splits_across_hour_boundaries():
    summary = summarise([span(30, 60)])  # 09:30 -> 10:30
    assert summary.hourly_seconds[9] == 30 * 60
    assert summary.hourly_seconds[10] == 30 * 60


def test_weighted_affinity_reflects_the_kind_of_work():
    entry = summarise([span(0, 60, app="localc", category="data_entry")])
    design = summarise([span(0, 60, app="figma", category="design")])
    assert entry.weighted_automation_affinity > design.weighted_automation_affinity


def test_summary_serialises_to_json_safe_dict():
    import json

    summary = summarise([span(0, 30, title_hash="abc"), span(30, 30, app="slack",
                                                             category="communication")])
    payload = json.loads(json.dumps(summary.as_dict()))
    assert payload["totals"]["active_seconds"] == 3600.0
    assert payload["shape"]["context_switches"] == 1


def test_report_renders_without_error():
    summary = summarise([span(0, 30, title_hash="abc") for _ in range(1)] +
                        [span(30, 30, app="slack", category="communication")])
    text = render(summary)
    assert "Where the time went" in text
    assert "Repetition signals" in text
    assert "stage 3" in text
    assert render_compact(summary)


def test_human_duration_formats():
    assert human_duration(45) == "45s"
    assert human_duration(90) == "1m 30s"
    assert human_duration(3900) == "1h 05m"


def test_sequences_do_not_span_midnight():
    """Monday's last app and Tuesday's first are not a workflow."""
    spans = [
        span(0, 30, app="code", day="2026-05-04"),
        span(30, 30, app="slack", day="2026-05-04"),
        span(60, 30, app="slack", day="2026-05-05"),
        span(90, 30, app="code", day="2026-05-05"),
    ]
    summary = summarise(spans, min_recurrence=1)
    paths = {s.apps for s in summary.sequences}
    assert ("code", "slack") in paths
    assert ("slack", "code") in paths
    # The Monday->Tuesday boundary must not produce a three-step "routine".
    assert ("code", "slack", "code") not in paths
    for seq in summary.sequences:
        assert seq.occurrences == 1
        assert seq.distinct_days == 1


def test_day_is_derived_when_missing():
    rows = [
        {"started_at": BASE, "ended_at": BASE + 600, "duration": 600.0,
         "app": "code", "category": "development", "title_hash": None, "afk": 0},
    ]
    summary = summarise(rows)
    assert summary.distinct_days == 1
