"""Stage 2: turn stored spans into statistics about how the computer is used.

Three families of numbers come out of here:

*Composition* - where the time went, by category and by application.

*Shape* - how the time was arranged: focus blocks, context switches,
fragmentation, the hour-by-hour profile. Two people with identical category
totals can have completely different days, and the difference is usually what
they actually want to know.

*Repetition* - which windows and which app-to-app sequences recur. These are
plain frequency counts, deliberately kept as observations rather than advice:
turning them into ranked automation proposals is stage 3's job, and doing it
here would mean guessing without the causal analysis that stage needs.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from copynion.categorize import taxonomy

#: A gap longer than this between spans breaks a focus block.
FOCUS_GAP_TOLERANCE = 120.0
#: Minimum length for a stretch of work to count as a focus block.
MIN_FOCUS_BLOCK = 900.0  # 15 minutes


def _rows(spans: Iterable[Any]) -> list[dict]:
    """Accept sqlite3.Row, dicts or ActivitySpan-like objects uniformly."""
    out = []
    for s in spans:
        if isinstance(s, dict):
            out.append(s)
        elif hasattr(s, "keys"):  # sqlite3.Row
            out.append(dict(s))
        else:
            out.append(s.to_row())
    for row in out:
        # Guarantee a `day` for every row: the sequence and recurrence logic
        # depends on knowing which working day a span belongs to.
        if not row.get("day"):
            row["day"] = datetime.fromtimestamp(row["started_at"]).strftime("%Y-%m-%d")
    out.sort(key=lambda r: r["started_at"])
    return out


@dataclass(slots=True)
class CategoryStat:
    name: str
    label: str
    seconds: float
    span_count: int
    share: float = 0.0
    automation_affinity: float = 0.0


@dataclass(slots=True)
class AppStat:
    app: str
    seconds: float
    span_count: int
    share: float = 0.0
    categories: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class FocusBlock:
    started_at: float
    ended_at: float
    category: str
    span_count: int

    @property
    def seconds(self) -> float:
        return self.ended_at - self.started_at


@dataclass(slots=True)
class RecurringWindow:
    """A specific window the user returns to again and again.

    Identified by its keyed title fingerprint, so this is computed without ever
    decrypting or even reading a title.
    """

    app: str
    title_hash: str | None
    occurrences: int
    total_seconds: float
    distinct_days: int

    @property
    def mean_seconds(self) -> float:
        return self.total_seconds / self.occurrences if self.occurrences else 0.0


@dataclass(slots=True)
class SequencePattern:
    """An app-to-app-to-app path walked repeatedly, e.g. mail -> sheets -> mail."""

    apps: tuple[str, ...]
    occurrences: int
    distinct_days: int
    mean_seconds: float = 0.0


@dataclass(slots=True)
class Summary:
    """Everything stage 2 knows about a period."""

    since: float
    until: float
    tracked_seconds: float = 0.0
    active_seconds: float = 0.0
    idle_seconds: float = 0.0
    span_count: int = 0
    distinct_days: int = 0

    categories: list[CategoryStat] = field(default_factory=list)
    apps: list[AppStat] = field(default_factory=list)
    focus_blocks: list[FocusBlock] = field(default_factory=list)
    hourly_seconds: dict[int, float] = field(default_factory=dict)

    context_switches: int = 0
    recurring_windows: list[RecurringWindow] = field(default_factory=list)
    sequences: list[SequencePattern] = field(default_factory=list)

    # Input aggregates. Present whenever input capture was on at any tier;
    # these come from content-free plaintext columns, so they are available even
    # after the recorded events themselves have expired.
    keystrokes: int = 0
    clicks: int = 0
    scrolls: int = 0
    mouse_distance: float = 0.0
    input_by_category: dict[str, tuple[int, int]] = field(default_factory=dict)
    """category -> (keystrokes, clicks)"""

    # -- derived ---------------------------------------------------------------

    @property
    def switches_per_active_hour(self) -> float:
        hours = self.active_seconds / 3600.0
        return self.context_switches / hours if hours > 0.05 else 0.0

    @property
    def focus_seconds(self) -> float:
        return sum(b.seconds for b in self.focus_blocks)

    @property
    def focus_share(self) -> float:
        return self.focus_seconds / self.active_seconds if self.active_seconds else 0.0

    @property
    def longest_focus_block(self) -> FocusBlock | None:
        return max(self.focus_blocks, default=None, key=lambda b: b.seconds)

    @property
    def fragmentation(self) -> float:
        """0 = long uninterrupted stretches, 1 = constant switching.

        Anchored at 30 switches per active hour, which in practice is where a
        day stops containing any recoverable deep work.
        """
        return min(1.0, self.switches_per_active_hour / 30.0)

    @property
    def has_input_data(self) -> bool:
        return bool(self.keystrokes or self.clicks or self.scrolls)

    @property
    def keys_per_active_minute(self) -> float:
        minutes = self.active_seconds / 60.0
        return self.keystrokes / minutes if minutes > 0.5 else 0.0

    @property
    def clicks_per_active_minute(self) -> float:
        minutes = self.active_seconds / 60.0
        return self.clicks / minutes if minutes > 0.5 else 0.0

    @property
    def typing_share(self) -> float:
        """Keystrokes as a fraction of all discrete input actions.

        Near 1.0 means the work is typing; near 0.0 means it is navigating and
        clicking. The latter is the classic shape of automatable form-filling
        and file-shuffling work.
        """
        total = self.keystrokes + self.clicks
        return self.keystrokes / total if total else 0.0

    @property
    def mean_span_seconds(self) -> float:
        return self.active_seconds / self.span_count if self.span_count else 0.0

    @property
    def repetition_share(self) -> float:
        """Fraction of active time spent in windows that recur (>= 3 times)."""
        if not self.active_seconds:
            return 0.0
        repeated = sum(w.total_seconds for w in self.recurring_windows)
        return min(1.0, repeated / self.active_seconds)

    @property
    def weighted_automation_affinity(self) -> float:
        """Time-weighted mean automation affinity across categories.

        A *descriptive* statistic - "the kind of work you did is the kind that
        tends to be automatable" - not a claim that anything specific can be
        automated. That claim needs stage 3.
        """
        if not self.active_seconds:
            return 0.0
        total = sum(c.seconds * c.automation_affinity for c in self.categories)
        return total / self.active_seconds

    def category(self, name: str) -> CategoryStat | None:
        return next((c for c in self.categories if c.name == name), None)

    def as_dict(self) -> dict:
        return {
            "period": {
                "since": self.since,
                "until": self.until,
                "distinct_days": self.distinct_days,
            },
            "totals": {
                "tracked_seconds": round(self.tracked_seconds, 1),
                "active_seconds": round(self.active_seconds, 1),
                "idle_seconds": round(self.idle_seconds, 1),
                "span_count": self.span_count,
                "mean_span_seconds": round(self.mean_span_seconds, 1),
            },
            "categories": [
                {
                    "name": c.name,
                    "label": c.label,
                    "seconds": round(c.seconds, 1),
                    "share": round(c.share, 4),
                    "span_count": c.span_count,
                    "automation_affinity": c.automation_affinity,
                }
                for c in self.categories
            ],
            "apps": [
                {
                    "app": a.app,
                    "seconds": round(a.seconds, 1),
                    "share": round(a.share, 4),
                    "span_count": a.span_count,
                }
                for a in self.apps
            ],
            "shape": {
                "context_switches": self.context_switches,
                "switches_per_active_hour": round(self.switches_per_active_hour, 2),
                "focus_blocks": len(self.focus_blocks),
                "focus_seconds": round(self.focus_seconds, 1),
                "focus_share": round(self.focus_share, 4),
                "longest_focus_seconds": round(
                    self.longest_focus_block.seconds if self.longest_focus_block else 0.0, 1
                ),
                "fragmentation": round(self.fragmentation, 3),
            },
            "input": {
                "captured": self.has_input_data,
                "keystrokes": self.keystrokes,
                "clicks": self.clicks,
                "scrolls": self.scrolls,
                "mouse_distance_px": round(self.mouse_distance, 1),
                "keys_per_active_minute": round(self.keys_per_active_minute, 1),
                "clicks_per_active_minute": round(self.clicks_per_active_minute, 1),
                "typing_share": round(self.typing_share, 3),
                "by_category": {
                    name: {"keystrokes": k, "clicks": c}
                    for name, (k, c) in sorted(
                        self.input_by_category.items(), key=lambda kv: -(kv[1][0] + kv[1][1])
                    )
                },
            },
            "hourly_seconds": {str(h): round(s, 1) for h, s in sorted(self.hourly_seconds.items())},
            "repetition": {
                "share_of_active_time": round(self.repetition_share, 4),
                "weighted_automation_affinity": round(self.weighted_automation_affinity, 3),
                "recurring_windows": [
                    {
                        "app": w.app,
                        "title_hash": w.title_hash,
                        "occurrences": w.occurrences,
                        "total_seconds": round(w.total_seconds, 1),
                        "mean_seconds": round(w.mean_seconds, 1),
                        "distinct_days": w.distinct_days,
                    }
                    for w in self.recurring_windows
                ],
                "sequences": [
                    {
                        "apps": list(s.apps),
                        "occurrences": s.occurrences,
                        "distinct_days": s.distinct_days,
                    }
                    for s in self.sequences
                ],
            },
        }


def summarise(
    spans: Sequence[Any],
    *,
    since: float | None = None,
    until: float | None = None,
    min_focus_block: float = MIN_FOCUS_BLOCK,
    focus_gap_tolerance: float = FOCUS_GAP_TOLERANCE,
    min_recurrence: int = 3,
    top_n: int = 15,
) -> Summary:
    """Compute the full stage-2 summary for a set of spans."""
    rows = _rows(spans)
    if not rows:
        return Summary(since=since or 0.0, until=until or 0.0)

    summary = Summary(
        since=since if since is not None else rows[0]["started_at"],
        until=until if until is not None else rows[-1]["ended_at"],
    )

    cat_seconds: dict[str, float] = defaultdict(float)
    cat_counts: Counter[str] = Counter()
    app_seconds: dict[str, float] = defaultdict(float)
    app_counts: Counter[str] = Counter()
    app_cat: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    days: set[str] = set()

    for row in rows:
        duration = float(row["duration"])
        summary.tracked_seconds += duration
        summary.span_count += 1
        days.add(row.get("day") or datetime.fromtimestamp(row["started_at"]).strftime("%Y-%m-%d"))

        if row.get("afk"):
            summary.idle_seconds += duration
            continue

        summary.active_seconds += duration
        category = row.get("category") or "uncategorised"

        keys = int(row.get("keystrokes") or 0)
        clicks = int(row.get("clicks") or 0)
        summary.keystrokes += keys
        summary.clicks += clicks
        summary.scrolls += int(row.get("scrolls") or 0)
        summary.mouse_distance += float(row.get("mouse_distance") or 0.0)
        if keys or clicks:
            prev_k, prev_c = summary.input_by_category.get(category, (0, 0))
            summary.input_by_category[category] = (prev_k + keys, prev_c + clicks)

        app = row.get("app") or "(unknown)"
        cat_seconds[category] += duration
        cat_counts[category] += 1
        app_seconds[app] += duration
        app_counts[app] += 1
        app_cat[app][category] += duration
        _spread_over_hours(summary.hourly_seconds, row["started_at"], row["ended_at"])

    summary.distinct_days = len(days)

    active = summary.active_seconds or 1.0
    summary.categories = sorted(
        (
            CategoryStat(
                name=name,
                label=taxonomy.get(name).label,
                seconds=seconds,
                span_count=cat_counts[name],
                share=seconds / active,
                automation_affinity=taxonomy.affinity(name),
            )
            for name, seconds in cat_seconds.items()
        ),
        key=lambda c: -c.seconds,
    )
    summary.apps = sorted(
        (
            AppStat(
                app=app,
                seconds=seconds,
                span_count=app_counts[app],
                share=seconds / active,
                categories=dict(app_cat[app]),
            )
            for app, seconds in app_seconds.items()
        ),
        key=lambda a: -a.seconds,
    )[:top_n]

    active_rows = [r for r in rows if not r.get("afk")]
    summary.context_switches = _count_switches(active_rows)
    summary.focus_blocks = _focus_blocks(active_rows, min_focus_block, focus_gap_tolerance)
    summary.recurring_windows = _recurring_windows(active_rows, min_recurrence, top_n)
    summary.sequences = _sequences(active_rows, min_recurrence, top_n)
    return summary


# -- shape --------------------------------------------------------------------

def _spread_over_hours(bucket: dict[int, float], start: float, end: float) -> None:
    """Attribute a span's seconds to the clock hours it actually covers."""
    if end <= start:
        return
    cursor = start
    guard = 0
    while cursor < end and guard < 48:
        dt = datetime.fromtimestamp(cursor)
        hour_end = dt.replace(minute=0, second=0, microsecond=0).timestamp() + 3600
        slice_end = min(end, hour_end)
        bucket[dt.hour] = bucket.get(dt.hour, 0.0) + (slice_end - cursor)
        cursor = slice_end
        guard += 1


def _count_switches(rows: list[dict]) -> int:
    """Count changes of application between consecutive active spans."""
    switches = 0
    previous = None
    for row in rows:
        app = row.get("app") or ""
        if previous is not None and app != previous:
            switches += 1
        previous = app
    return switches


def _focus_blocks(rows: list[dict], min_block: float, gap_tolerance: float) -> list[FocusBlock]:
    """Merge adjacent spans of the same focus-work category into blocks."""
    blocks: list[FocusBlock] = []
    current: FocusBlock | None = None

    for row in rows:
        category = row.get("category") or "uncategorised"
        if not taxonomy.is_focus_work(category):
            if current and current.seconds >= min_block:
                blocks.append(current)
            current = None
            continue

        if (
            current is not None
            and current.category == category
            and row["started_at"] - current.ended_at <= gap_tolerance
        ):
            current.ended_at = row["ended_at"]
            current.span_count += 1
            continue

        if current and current.seconds >= min_block:
            blocks.append(current)
        current = FocusBlock(row["started_at"], row["ended_at"], category, 1)

    if current and current.seconds >= min_block:
        blocks.append(current)
    return blocks


# -- repetition ---------------------------------------------------------------

def _recurring_windows(rows: list[dict], min_recurrence: int, top_n: int) -> list[RecurringWindow]:
    grouped: dict[tuple[str, str | None], list[dict]] = defaultdict(list)
    for row in rows:
        if not row.get("title_hash"):
            # Without a title fingerprint we cannot tell two windows of the same
            # app apart, and counting them together would invent repetition.
            continue
        grouped[(row.get("app") or "", row["title_hash"])].append(row)

    out = []
    for (app, title_hash), group in grouped.items():
        if len(group) < min_recurrence:
            continue
        out.append(
            RecurringWindow(
                app=app,
                title_hash=title_hash,
                occurrences=len(group),
                total_seconds=sum(float(r["duration"]) for r in group),
                distinct_days=len({r.get("day") for r in group}),
            )
        )
    return sorted(out, key=lambda w: (-w.occurrences, -w.total_seconds))[:top_n]


def _sequences(
    rows: list[dict], min_recurrence: int, top_n: int, lengths: tuple[int, ...] = (2, 3)
) -> list[SequencePattern]:
    """Find app-transition n-grams that repeat.

    Consecutive duplicates are collapsed first, so "mail, mail, sheets" and
    "mail, sheets" are the same path. Only sequences that move between at least
    two distinct applications are reported - a single app repeated is not a
    workflow.
    """
    # Group by day first. A path is something walked within a working session;
    # letting an n-gram straddle midnight would invent a "workflow" out of the
    # last app on Monday and the first on Tuesday, and would misattribute which
    # days a pattern occurred on.
    by_day: dict[str | None, list[tuple[str, float]]] = defaultdict(list)
    for row in rows:
        by_day[row.get("day")].append((row.get("app") or "", float(row["duration"])))

    counts: Counter[tuple[str, ...]] = Counter()
    days: dict[tuple[str, ...], set] = defaultdict(set)
    seconds: dict[tuple[str, ...], float] = defaultdict(float)

    for day, entries in by_day.items():
        collapsed: list[tuple[str, float]] = []
        for app, duration in entries:
            if collapsed and collapsed[-1][0] == app:
                collapsed[-1] = (app, collapsed[-1][1] + duration)
            else:
                collapsed.append((app, duration))

        for n in lengths:
            for i in range(len(collapsed) - n + 1):
                window = collapsed[i : i + n]
                apps = tuple(a for a, _ in window)
                if len(set(apps)) < 2 or any(not a for a in apps):
                    continue
                counts[apps] += 1
                days[apps].add(day)
                seconds[apps] += sum(d for _, d in window)

    out = [
        SequencePattern(
            apps=apps,
            occurrences=count,
            distinct_days=len(days[apps]),
            mean_seconds=seconds[apps] / count,
        )
        for apps, count in counts.items()
        if count >= min_recurrence
    ]
    # Longer patterns first at equal frequency: they say more about the workflow.
    return sorted(out, key=lambda s: (-s.occurrences, -len(s.apps)))[:top_n]
