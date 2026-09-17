"""Rule definitions and loading.

Rules are data, not code: a JSON file the user can read, diff and edit. That
matters for trust - "why did it call this work?" has to be answerable by
pointing at a line, which is also why every stored span keeps the ``rule_id``
that classified it.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from copynion.categorize import taxonomy


@dataclass(frozen=True, slots=True)
class Rule:
    """One classification rule.

    ``apps`` are matched as case-insensitive globs against the application name.
    ``title`` is a regex matched against the *redacted* title - by the time a
    rule sees it, personal data has already been replaced with placeholders.
    A rule with both must satisfy both.
    """

    id: str
    category: str
    apps: tuple[str, ...] = ()
    title: str | None = None
    subcategory: str | None = None
    confidence: float = 0.8
    _title_re: re.Pattern[str] | None = None

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> Rule:
        category = raw["category"]
        if category not in taxonomy.VALID_CATEGORIES:
            raise ValueError(
                f"rule {raw.get('id')!r} uses unknown category {category!r}; "
                f"valid: {sorted(taxonomy.VALID_CATEGORIES)}"
            )
        apps = raw.get("apps") or ([raw["app"]] if "app" in raw else [])
        title = raw.get("title")
        compiled = None
        if title:
            try:
                compiled = re.compile(title, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"rule {raw.get('id')!r} has an invalid title regex: {exc}") from exc
        if not apps and not compiled:
            raise ValueError(f"rule {raw.get('id')!r} matches nothing (needs apps and/or title)")
        return Rule(
            id=raw["id"],
            category=category,
            apps=tuple(a.lower() for a in apps),
            title=title,
            subcategory=raw.get("subcategory"),
            confidence=float(raw.get("confidence", 0.8)),
            _title_re=compiled,
        )

    def matches(self, app: str, title: str) -> bool:
        if self.apps and not any(fnmatch.fnmatch(app, pattern) for pattern in self.apps):
            return False
        if self._title_re is None:
            return True
        # A title rule cannot fire when the policy withheld the title; falling
        # back to app-only matching would silently over-claim.
        return bool(title) and self._title_re.search(title) is not None

    @property
    def specificity(self) -> int:
        """Rules that constrain more win: app+title (3) > app (2) > title (1).

        Naming the application is weighted above a title regex because the app
        is the reliable signal - it says which tool the person actually had in
        front of them. Title regexes are broad by nature ("*.py" appears in a
        file manager and a chat window too), so they only decide the answer when
        no app rule applies.
        """
        return (2 if self.apps else 0) + (1 if self._title_re is not None else 0)


def load_rules(path: Path | None = None) -> list[Rule]:
    """Load the built-in rule pack, then merge a user pack over it by id."""
    rules: dict[str, Rule] = {}
    builtin = json.loads(
        resources.files("copynion.data").joinpath("default_rules.json").read_text("utf-8")
    )
    for raw in builtin["rules"]:
        rules[raw["id"]] = Rule.from_dict(raw)

    if path and Path(path).exists():
        user = json.loads(Path(path).read_text("utf-8"))
        for raw in user.get("rules", []):
            rules[raw["id"]] = Rule.from_dict(raw)

    # Most specific first, then most confident: the engine takes the first match.
    return sorted(rules.values(), key=lambda r: (-r.specificity, -r.confidence, r.id))
