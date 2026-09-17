"""Rendering summaries as text for the terminal.

Plain ASCII bars, no colour libraries, no dependencies. The point of stage 2 is
that a person can look at their week and recognise it, so the report is ordered
the way someone actually asks the questions: how long, on what, arranged how,
and what kept coming back.
"""

from __future__ import annotations

from datetime import datetime

from copynion.stats.aggregate import Summary

BAR_WIDTH = 28


def human_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {secs:02d}s"


def bar(share: float, width: int = BAR_WIDTH) -> str:
    filled = int(round(max(0.0, min(1.0, share)) * width))
    return "#" * filled + "." * (width - filled)


def _heading(text: str) -> str:
    return f"\n{text}\n{'-' * len(text)}"


def render(summary: Summary, *, show_hours: bool = True, top_apps: int = 8) -> str:
    if summary.span_count == 0:
        return (
            "No activity recorded for this period.\n\n"
            "If you expected data here, run `copynion doctor` to check that a\n"
            "window backend is available and that recording is not paused."
        )

    lines: list[str] = []
    start = datetime.fromtimestamp(summary.since).strftime("%Y-%m-%d %H:%M")
    end = datetime.fromtimestamp(summary.until).strftime("%Y-%m-%d %H:%M")
    lines.append(f"Copynion summary  {start}  ->  {end}   ({summary.distinct_days} day(s))")

    lines.append(_heading("Totals"))
    lines.append(f"  Active          {human_duration(summary.active_seconds):>10}")
    lines.append(f"  Idle / away     {human_duration(summary.idle_seconds):>10}")
    lines.append(f"  Tracked         {human_duration(summary.tracked_seconds):>10}")
    lines.append(f"  Activity spans  {summary.span_count:>10}")
    lines.append(f"  Mean span       {human_duration(summary.mean_span_seconds):>10}")

    lines.append(_heading("Where the time went"))
    for cat in summary.categories:
        lines.append(
            f"  {cat.label[:22]:<22} {human_duration(cat.seconds):>9}  "
            f"{cat.share * 100:5.1f}%  {bar(cat.share)}"
        )

    lines.append(_heading("Applications"))
    for app in summary.apps[:top_apps]:
        lines.append(
            f"  {app.app[:22]:<22} {human_duration(app.seconds):>9}  "
            f"{app.share * 100:5.1f}%  {bar(app.share)}"
        )

    lines.append(_heading("Shape of the work"))
    lines.append(f"  Context switches        {summary.context_switches:>8}")
    lines.append(f"  Switches per active hr  {summary.switches_per_active_hour:>8.1f}")
    lines.append(
        f"  Fragmentation           {summary.fragmentation:>8.2f}  "
        f"({_fragmentation_word(summary.fragmentation)})"
    )
    lines.append(
        f"  Focus blocks (>=15m)    {len(summary.focus_blocks):>8}   "
        f"totalling {human_duration(summary.focus_seconds)} "
        f"({summary.focus_share * 100:.0f}% of active time)"
    )
    longest = summary.longest_focus_block
    if longest:
        when = datetime.fromtimestamp(longest.started_at).strftime("%a %H:%M")
        lines.append(
            f"  Longest focus block     {human_duration(longest.seconds):>8}   "
            f"{longest.category} starting {when}"
        )

    if show_hours and summary.hourly_seconds:
        lines.append(_heading("Time of day"))
        peak = max(summary.hourly_seconds.values()) or 1.0
        for hour in range(24):
            seconds = summary.hourly_seconds.get(hour, 0.0)
            if seconds <= 0:
                continue
            lines.append(f"  {hour:02d}:00  {human_duration(seconds):>9}  {bar(seconds / peak, 24)}")

    if summary.has_input_data:
        lines.append(_heading("Interaction"))
        lines.append(f"  Keystrokes              {summary.keystrokes:>8}  "
                     f"({summary.keys_per_active_minute:.0f}/active min)")
        lines.append(f"  Clicks                  {summary.clicks:>8}  "
                     f"({summary.clicks_per_active_minute:.0f}/active min)")
        lines.append(f"  Scrolls                 {summary.scrolls:>8}")
        lines.append(f"  Pointer travelled       {summary.mouse_distance / 1000:>8.1f} k-px")
        lines.append(
            f"  Typing vs clicking      {summary.typing_share * 100:>7.0f}%  "
            f"({_interaction_word(summary.typing_share)})"
        )
        if summary.input_by_category:
            lines.append("\n  By category:")
            lines.append(f"    {'category':<18} {'keys':>8} {'clicks':>8}")
            for name, (keys, clicks) in sorted(
                summary.input_by_category.items(), key=lambda kv: -(kv[1][0] + kv[1][1])
            )[:8]:
                lines.append(f"    {name[:18]:<18} {keys:>8} {clicks:>8}")

    lines.append(_heading("Repetition signals"))
    lines.append(
        f"  Time in recurring windows  {summary.repetition_share * 100:5.1f}% of active time"
    )
    lines.append(
        f"  Weighted automation affinity  {summary.weighted_automation_affinity:.2f}  "
        "(how automatable this *kind* of work tends to be)"
    )

    if summary.recurring_windows:
        lines.append("\n  Windows you returned to most:")
        lines.append(f"    {'app':<18} {'times':>6} {'total':>10} {'average':>9}  days")
        for window in summary.recurring_windows[:8]:
            lines.append(
                f"    {window.app[:18]:<18} {window.occurrences:>6} "
                f"{human_duration(window.total_seconds):>10} "
                f"{human_duration(window.mean_seconds):>9}  {window.distinct_days}"
            )

    if summary.sequences:
        lines.append("\n  Paths you walked repeatedly:")
        for seq in summary.sequences[:8]:
            path = " -> ".join(seq.apps)
            lines.append(f"    {path[:52]:<52} {seq.occurrences:>4}x  on {seq.distinct_days} day(s)")

    lines.append(
        "\n  These are counts, not recommendations. Ranking them into actual\n"
        "  automation proposals is stage 3."
    )
    return "\n".join(lines)


def _interaction_word(typing_share: float) -> str:
    if typing_share > 0.8:
        return "almost entirely typing"
    if typing_share > 0.55:
        return "typing-led"
    if typing_share > 0.3:
        return "mixed"
    return "navigation-led - the shape automation usually targets"


def _fragmentation_word(value: float) -> str:
    if value < 0.2:
        return "long uninterrupted stretches"
    if value < 0.45:
        return "moderately interrupted"
    if value < 0.7:
        return "heavily interrupted"
    return "constant switching"


def render_compact(summary: Summary) -> str:
    """One-line-per-category digest, for ``copynion status``."""
    if summary.span_count == 0:
        return "no activity recorded"
    parts = [
        f"{c.label}: {human_duration(c.seconds)}"
        for c in summary.categories[:4]
    ]
    return (
        f"{human_duration(summary.active_seconds)} active | "
        + ", ".join(parts)
        + f" | {summary.context_switches} switches"
    )
