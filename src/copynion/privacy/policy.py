"""Decides, per observation, how much may be recorded.

The policy is the user's steering wheel. It answers one question - "what are we
allowed to keep about this window?" - and it answers it *before* anything is
written. Three mechanisms feed the answer:

* a global pause (private mode), which drops everything;
* per-application rules, matched by glob on the app name;
* a title deny-list, matched by substring/regex against the window title, which
  lets someone exclude a single project or client without excluding the app.

The default is intentionally conservative: apps the user has not classified are
recorded at ``APP_ONLY`` fidelity unless they opt into title capture.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

from copynion.models import Observation, Visibility

#: Apps whose window titles are almost always sensitive. Titles are dropped for
#: these unless the user explicitly overrides them in the config.
SENSITIVE_APP_DEFAULTS: tuple[str, ...] = (
    "1password*",
    "bitwarden*",
    "keepass*",
    "keychain*",
    "gnome-keyring*",
    "seahorse",
    "*password*",
    "*banking*",
    "*wallet*",
    "authy*",
    "*vpn*",
)

#: Title fragments that mean "the user asked their browser to forget this".
PRIVATE_BROWSING_MARKERS: tuple[str, ...] = (
    "private browsing",
    "incognito",
    "inprivate",
    "private window",
)


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """The verdict for one observation."""

    visibility: Visibility
    reason: str
    rule: str | None = None

    @property
    def records_anything(self) -> bool:
        return self.visibility is not Visibility.DROP

    @property
    def keeps_title(self) -> bool:
        return self.visibility is Visibility.FULL


class Policy:
    """Evaluates capture rules against observations."""

    def __init__(
        self,
        *,
        default_visibility: Visibility = Visibility.APP_ONLY,
        app_rules: dict[str, str] | None = None,
        title_denylist: list[str] | None = None,
        respect_private_browsing: bool = True,
        use_sensitive_defaults: bool = True,
    ) -> None:
        self.default_visibility = default_visibility
        self.respect_private_browsing = respect_private_browsing
        self._paused = False
        self._pause_reason = ""

        # app glob -> visibility. User rules are applied after the built-in
        # sensitive defaults so they can override them deliberately.
        self._app_rules: list[tuple[str, Visibility]] = []
        if use_sensitive_defaults:
            self._app_rules += [(g, Visibility.APP_ONLY) for g in SENSITIVE_APP_DEFAULTS]
        for glob, vis in (app_rules or {}).items():
            self._app_rules.append((glob.lower(), Visibility(vis)))

        self._title_denylist: list[re.Pattern[str]] = []
        for raw in title_denylist or []:
            try:
                self._title_denylist.append(re.compile(raw, re.IGNORECASE))
            except re.error as exc:
                raise ValueError(f"invalid title denylist pattern {raw!r}: {exc}") from exc

    # -- private mode ---------------------------------------------------------

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def pause_reason(self) -> str:
        return self._pause_reason

    def pause(self, reason: str = "user requested") -> None:
        """Stop recording entirely until :meth:`resume` is called."""
        self._paused = True
        self._pause_reason = reason

    def resume(self) -> None:
        self._paused = False
        self._pause_reason = ""

    # -- the decision ---------------------------------------------------------

    def decide(self, obs: Observation) -> PolicyDecision:
        """Return how much of ``obs`` may be stored."""
        if self._paused:
            return PolicyDecision(Visibility.DROP, f"paused: {self._pause_reason}", "pause")

        app = (obs.app or "").lower()
        title = obs.title or ""

        # An empty app name means the backend could not read the foreground
        # window (a locked screen, a Wayland compositor without the protocol).
        # We still record the time so totals stay honest, but nothing else.
        if not app:
            return PolicyDecision(Visibility.OPAQUE, "no window information available", "unknown")

        for pattern in self._title_denylist:
            if pattern.search(title):
                return PolicyDecision(
                    Visibility.DROP, "title matches denylist", f"denylist:{pattern.pattern}"
                )

        if self.respect_private_browsing:
            lowered = title.lower()
            for marker in PRIVATE_BROWSING_MARKERS:
                if marker in lowered:
                    return PolicyDecision(
                        Visibility.APP_ONLY, "private browsing window", f"private:{marker}"
                    )

        # Last matching app rule wins, so user config overrides the built-ins.
        decision: PolicyDecision | None = None
        for glob, visibility in self._app_rules:
            if fnmatch.fnmatch(app, glob):
                decision = PolicyDecision(visibility, "app rule", f"app:{glob}")
        if decision is not None:
            return decision

        return PolicyDecision(self.default_visibility, "default policy", "default")

    def apply(self, obs: Observation) -> tuple[Observation | None, PolicyDecision]:
        """Return ``obs`` downgraded to what the policy permits.

        ``None`` means "record nothing at all". Otherwise the returned
        observation has already had forbidden fields stripped, so a caller that
        ignores the decision object still cannot leak anything.
        """
        decision = self.decide(obs)
        if decision.visibility is Visibility.DROP:
            return None, decision
        if decision.visibility is Visibility.OPAQUE:
            return Observation(
                timestamp=obs.timestamp,
                app="",
                title="",
                idle_seconds=obs.idle_seconds,
                backend=obs.backend,
            ), decision
        if decision.visibility is Visibility.APP_ONLY:
            return Observation(
                timestamp=obs.timestamp,
                app=obs.app,
                title="",
                idle_seconds=obs.idle_seconds,
                pid=obs.pid,
                backend=obs.backend,
            ), decision
        return obs, decision
