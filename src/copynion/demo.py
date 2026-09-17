"""Synthetic activity, for trying Copynion without watching anyone.

A realistic week matters for more than a pretty screenshot: it is how the
statistics are tested, how someone evaluates whether they want this running on
their machine, and how contributors work on stage 3 without needing a month of
their own recorded life first.

The generated week deliberately contains a repeating morning routine
(mail -> spreadsheet -> mail -> ticket) so that the repetition signals have
something true to find.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from copynion.models import Observation

# (app, title, minutes) - titles are written the way real ones look, including
# the personal data that redaction is supposed to strip.
_MORNING_ROUTINE: list[tuple[str, str, int]] = [
    ("thunderbird", "Inbox - daily figures from ops@example.com", 6),
    ("localc", "daily-report.ods - LibreOffice Calc", 14),
    ("thunderbird", "Re: daily figures - send to team@example.com", 5),
    ("firefox", "JIRA OPS-412 - update daily report", 4),
]

_DEEP_WORK: list[tuple[str, str, int]] = [
    ("code", "sampler.py - copynion - VSCode", 35),
    ("alacritty", "pytest - ~/projects/copynion", 8),
    ("code", "aggregate.py - copynion - VSCode", 28),
    ("firefox", "sqlite3 docs - SQLite Documentation", 6),
    ("code", "store.py - copynion - VSCode", 22),
]

_INTERRUPTIONS: list[tuple[str, str, int]] = [
    ("slack", "#engineering - Slack", 4),
    ("thunderbird", "Inbox - 3 unread", 3),
    ("firefox", "YouTube - background music", 7),
    ("slack", "DM with a colleague - Slack", 5),
]

_AFTERNOON: list[tuple[str, str, int]] = [
    ("zoom", "Zoom Meeting - Weekly sync", 45),
    ("code", "cli.py - copynion - VSCode", 30),
    ("nautilus", "Downloads", 3),
    ("localc", "budget-q3.ods - LibreOffice Calc", 18),
    ("firefox", "GitHub - marsierz-ui/copynion - Pull requests", 9),
]


def generate_week(
    days: int = 5,
    start: datetime | None = None,
    seed: int = 7,
    poll_interval: float = 5.0,
) -> list[Observation]:
    """Produce a stream of observations covering ``days`` working days."""
    rng = random.Random(seed)
    start = start or (datetime.now() - timedelta(days=days)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    observations: list[Observation] = []

    for day in range(days):
        cursor = (start + timedelta(days=day)).replace(hour=9, minute=rng.randint(0, 25))
        plan: list[tuple[str, str, int]] = []
        plan += _MORNING_ROUTINE  # the same routine every single morning
        plan += _interleave(_DEEP_WORK, _INTERRUPTIONS, rng)
        plan += [("", "", 40)]  # lunch: away from the machine
        plan += _interleave(_AFTERNOON, _INTERRUPTIONS[:2], rng)

        for app, title, minutes in plan:
            minutes = max(1, int(minutes * rng.uniform(0.75, 1.3)))
            idle_start = cursor
            samples = int(minutes * 60 / poll_interval)
            for i in range(samples):
                timestamp = (cursor + timedelta(seconds=i * poll_interval)).timestamp()
                if app == "":
                    # Away from keyboard: the idle timer keeps climbing.
                    idle = (timestamp - idle_start.timestamp()) + 60
                    observations.append(
                        Observation(timestamp, "firefox", "Inbox", idle_seconds=idle, backend="demo")
                    )
                else:
                    observations.append(
                        Observation(timestamp, app, title, idle_seconds=0.0, backend="demo")
                    )
            cursor += timedelta(minutes=minutes)

    return observations


def _interleave(main, noise, rng) -> list[tuple[str, str, int]]:
    """Scatter short interruptions through a block of real work."""
    out: list[tuple[str, str, int]] = []
    for item in main:
        out.append(item)
        if rng.random() < 0.55:
            out.append(rng.choice(noise))
    return out
