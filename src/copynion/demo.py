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

from copynion.inputs.recorder import InputRecorder
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


# How each application is typically driven, per 5-second sample: (keys, clicks,
# sample phrases). Used only to make the demo's interaction statistics plausible.
#
# These are averages across a whole session, not burst rates. Real typing happens
# in short bursts separated by thinking, reading and navigating, so a developer
# averages well under one keystroke per second over an hour even though they can
# manage six while actually typing.
_INTERACTION_PROFILES: dict[str, tuple[float, float, tuple[str, ...]]] = {
    "code": (4.5, 0.4, ("def summarise(rows):", "return total / count", "# TODO: refactor")),
    "localc": (3.5, 1.8, ("4455.20", "=SUM(B2:B31)", "Acme Ltd", "2026-09-14")),
    "thunderbird": (2.5, 1.2, ("Please find attached the daily figures.", "Thanks, Jan")),
    "slack": (2.0, 0.8, ("on it", "can you check OPS-412?")),
    "firefox": (0.6, 2.2, ("daily report template", "invoice 4455667788")),
    "alacritty": (1.5, 0.1, ("pytest -q", "git commit -m 'fix sampler'")),
    "nautilus": (0.2, 3.0, ("report-2026-09",)),
    "zoom": (0.1, 0.2, ()),
}

_SHORTCUTS = ("c", "v", "s", "z", "f")


def synthesise_input(observation: Observation, recorder: InputRecorder, rng) -> None:
    """Feed one sample's worth of plausible interaction into ``recorder``.

    Entirely fabricated. It exists so that ``copynion demo`` can show what the
    interaction statistics and ``copynion replay`` actually look like, without
    anyone having to switch on real input capture to find out.
    """
    profile = _INTERACTION_PROFILES.get(observation.app)
    if profile is None or observation.idle_seconds > 60:
        return
    keys_per_sample, clicks_per_sample, phrases = profile

    # One cursor, only ever moving forward: real interaction is monotonic in
    # time, and demo data that is not would misrepresent what `replay` shows.
    at = observation.timestamp

    if phrases and rng.random() < 0.12:
        phrase = rng.choice(phrases)
        for ch in phrase:
            recorder.record_key(ch, at)
            at += 0.08
        recorder.record_key(rng.choice(["enter", "tab"]), at)
        at += 0.1
    else:
        for _ in range(max(0, int(rng.gauss(keys_per_sample, keys_per_sample / 3)))):
            recorder.record_key(rng.choice("abcdefghijklmnopqrstuvwxyz "), at)
            at += 0.09

    if rng.random() < 0.25:
        recorder.record_key(rng.choice(_SHORTCUTS), at, frozenset({"ctrl"}))
        at += 0.2

    for _ in range(max(0, int(rng.gauss(clicks_per_sample, 1.0)))):
        x, y = rng.randint(50, 1800), rng.randint(60, 1000)
        for step in range(4):  # a short pointer path into the click
            recorder.record_move(x - (4 - step) * 30, y - (4 - step) * 18, at)
            at += 0.02
        recorder.record_click("left", x, y, at)
        at += 0.25

    if rng.random() < 0.3:
        recorder.record_scroll(0, rng.choice([-3, -1, 1, 3]), 900, 500, at)
