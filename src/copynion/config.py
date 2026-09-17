"""Configuration and filesystem locations.

Config is a TOML file the user can read and edit by hand. Defaults are chosen so
that an install that is never configured is still the *private* option: titles
off for unknown apps, redaction on, encryption required, 90-day retention.
Turning fidelity up is an explicit act.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from copynion.models import Visibility

APP_NAME = "copynion"


# -- locations ---------------------------------------------------------------

def config_dir() -> Path:
    if override := os.environ.get("COPYNION_CONFIG_DIR"):
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME


def data_dir() -> Path:
    if override := os.environ.get("COPYNION_DATA_DIR"):
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME


# -- settings ----------------------------------------------------------------

@dataclass(slots=True)
class ObservationSettings:
    poll_interval_seconds: float = 5.0
    """How often the foreground window is sampled. Five seconds is enough to
    reconstruct a working day while costing almost nothing."""

    idle_threshold_seconds: float = 120.0
    """No input for this long means the person left; time stops counting as work."""

    min_span_seconds: float = 3.0
    """Spans shorter than this are dropped as alt-tab noise."""

    max_span_seconds: float = 1800.0
    """Spans are cut at this length so a day of one window still has structure."""


@dataclass(slots=True)
class PrivacySettings:
    default_visibility: str = Visibility.APP_ONLY.value
    """What to record for an app with no explicit rule. Conservative by default."""

    apps: dict[str, str] = field(default_factory=dict)
    """App glob -> ``full`` | ``app_only`` | ``opaque`` | ``drop``."""

    redaction_enabled: bool = True
    custom_redaction_patterns: list[str] = field(default_factory=list)
    title_denylist: list[str] = field(default_factory=list)
    respect_private_browsing: bool = True

    allow_unencrypted_titles: bool = False
    """Off means: no working encryption, no stored titles. Fail closed."""

    use_keyring: bool = True
    use_sensitive_app_defaults: bool = True


@dataclass(slots=True)
class InputSettings:
    """Keystroke and mouse capture. Off unless deliberately switched on."""

    fidelity: str = "off"
    """``off`` | ``counts`` | ``structure`` | ``full``.

    Only ``full`` supports exact replay, and only ``full`` records literal
    characters. See docs/PRIVACY.md before raising this.
    """

    redact_typed_text: bool = True
    capture_mouse_moves: bool = True
    move_min_distance: float = 40.0
    move_max_interval: float = 0.5
    max_events_per_span: int = 5000


@dataclass(slots=True)
class RetentionSettings:
    detail_days: int = 90
    """Per-window records older than this are deleted. Rollups survive."""

    title_days: int = 30
    """Titles expire sooner than the spans that carry them."""

    input_days: int = 7
    """Recorded keystrokes and mouse events expire soonest of all - they are the
    most revealing thing stored, and their value for finding repeated processes
    is concentrated in the recent past."""


@dataclass(slots=True)
class Config:
    observation: ObservationSettings = field(default_factory=ObservationSettings)
    privacy: PrivacySettings = field(default_factory=PrivacySettings)
    inputs: InputSettings = field(default_factory=InputSettings)
    retention: RetentionSettings = field(default_factory=RetentionSettings)
    path: Path | None = None
    database_override: Path | None = None
    """Set by ``--database`` so commands can be pointed at another file, such as
    the demo database, without touching the real one."""

    # -- derived paths --------------------------------------------------------

    @property
    def db_path(self) -> Path:
        return self.database_override or (data_dir() / "copynion.db")

    @property
    def key_path(self) -> Path:
        return data_dir() / "copynion.key"

    @property
    def rules_path(self) -> Path:
        return config_dir() / "rules.json"

    @property
    def state_path(self) -> Path:
        return data_dir() / "state.json"

    # -- loading --------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = Path(path) if path else config_dir() / "config.toml"
        cfg = cls(path=path)
        if not path.exists():
            return cfg
        with path.open("rb") as fh:
            raw = tomllib.load(fh)

        for section, target in (
            ("observation", cfg.observation),
            ("privacy", cfg.privacy),
            ("input", cfg.inputs),
            ("retention", cfg.retention),
        ):
            for key, value in (raw.get(section) or {}).items():
                if hasattr(target, key):
                    setattr(target, key, value)
                else:
                    raise ValueError(
                        f"unknown setting [{section}].{key} in {path}"
                    )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        valid = {v.value for v in Visibility}
        if self.privacy.default_visibility not in valid:
            raise ValueError(
                f"privacy.default_visibility must be one of {sorted(valid)}, "
                f"got {self.privacy.default_visibility!r}"
            )
        for app, vis in self.privacy.apps.items():
            if vis not in valid:
                raise ValueError(f"privacy.apps.{app!r} must be one of {sorted(valid)}, got {vis!r}")
        valid_fidelity = {"off", "counts", "structure", "full"}
        if self.inputs.fidelity not in valid_fidelity:
            raise ValueError(
                f"input.fidelity must be one of {sorted(valid_fidelity)}, "
                f"got {self.inputs.fidelity!r}"
            )
        if self.observation.poll_interval_seconds <= 0:
            raise ValueError("observation.poll_interval_seconds must be positive")
        if self.observation.idle_threshold_seconds <= 0:
            raise ValueError("observation.idle_threshold_seconds must be positive")

    def write_default(self, path: Path | None = None) -> Path:
        path = Path(path or self.path or (config_dir() / "config.toml"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
        path.chmod(0o600)
        return path


DEFAULT_CONFIG_TOML = """\
# Copynion configuration.
#
# Everything here is local. Copynion has no accounts, no sync and no network
# code; changing these settings cannot send data anywhere, because there is
# nowhere for it to go.

[observation]
# How often to look at the foreground window, in seconds.
poll_interval_seconds = 5.0
# No keyboard or mouse input for this long counts as "away".
idle_threshold_seconds = 120.0
# Ignore windows you touched for less time than this (alt-tab noise).
min_span_seconds = 3.0

[privacy]
# What to record about an application you have not listed below:
#   "full"     - application name and a redacted window title
#   "app_only" - application name only          (the default)
#   "opaque"   - only that you were active
#   "drop"     - nothing at all
default_visibility = "app_only"

# Replace emails, card numbers, tokens and similar in titles before storing.
# Leave this on.
redaction_enabled = true

# Extra regexes of your own to strip from titles, e.g. a client's name.
custom_redaction_patterns = []

# Windows whose title matches any of these are never recorded at all.
title_denylist = []

# Treat incognito/private windows as app-only, whatever the rule below says.
respect_private_browsing = true

# If encryption is unavailable, drop titles rather than store them in the clear.
# Setting this to true writes window titles to disk unencrypted. Don't.
allow_unencrypted_titles = false

# Store the encryption key in the OS keychain when one is available.
use_keyring = true

# Per-application overrides. Globs allowed. Listing an app as "full" is how you
# opt in to title capture for the tools where detail is actually useful.
[privacy.apps]
# code = "full"
# firefox = "full"
# "1password" = "drop"

[input]
# Keystroke and mouse capture. Needed for stage 4 (replaying a process), and the
# most sensitive thing Copynion can record. Install with: pip install 'copynion[input]'
#
#   "off"       - record nothing            (the default)
#   "counts"    - how much typing and clicking, nothing about what
#   "structure" - key *classes*, shortcuts and mouse coordinates: enough to
#                 recognise a repeated process, not enough to read what you typed
#   "full"      - literal characters and coordinates: full replay fidelity
#
# On macOS, password fields switch on "secure input" and capture is suppressed
# automatically. Windows and X11 offer no such signal - see docs/PRIVACY.md.
fidelity = "off"

# Replace emails, numbers, tokens and the like in captured text with
# placeholders. This is also what you want for *replication*: "type <the invoice
# number>" generalises across runs, whereas one run's literal value does not.
redact_typed_text = true

# Mouse movement is decimated to the points that carry information; the position
# at each click is always kept exactly.
capture_mouse_moves = true
move_min_distance = 40.0
move_max_interval = 0.5

# Hard ceiling per activity span, so a stuck key cannot fill your disk.
max_events_per_span = 5000

[retention]
# Delete per-window records older than this. Daily statistics are kept forever;
# they contain no titles and no free text.
detail_days = 90
# Titles expire sooner than the records that carry them.
title_days = 30
# Keystrokes and mouse events expire soonest of all.
input_days = 7
"""
