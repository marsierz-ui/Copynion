"""Turns an observed window into a category.

Precedence, highest first:

1. **User overrides.** If someone has said "this app is deep work", that is the
   answer, permanently. Stage 3 will learn from these labels, so they must never
   be silently overwritten by a rule-pack update.
2. **Rules**, most specific first (app+title, then app, then title).
3. **Idle**, when the sampler marked the span as away-from-keyboard.
4. ``uncategorised``, reported honestly rather than guessed.

The engine is pure: give it the same inputs and it gives the same answer, with
no I/O. That keeps "why was this classified this way?" a question the CLI can
answer offline.
"""

from __future__ import annotations

from pathlib import Path

from copynion.categorize import taxonomy
from copynion.categorize.rules import Rule, load_rules
from copynion.models import Category

IDLE_CATEGORY = Category("idle", None, rule_id="builtin:idle", confidence=1.0, source="default")
UNKNOWN_CATEGORY = Category("uncategorised", None, rule_id=None, confidence=0.0, source="default")


class Categoriser:
    """Applies overrides and rules to produce a :class:`Category`."""

    def __init__(
        self,
        rules: list[Rule] | None = None,
        overrides: dict[tuple[str, str], Category] | None = None,
        user_rules_path: Path | None = None,
    ) -> None:
        self.rules = rules if rules is not None else load_rules(user_rules_path)
        self.overrides = overrides or {}

    def classify(self, app: str, title: str = "", *, afk: bool = False) -> Category:
        if afk:
            return IDLE_CATEGORY

        app = (app or "").lower()
        if not app:
            return UNKNOWN_CATEGORY

        if (override := self.overrides.get(("app", app))) is not None:
            return override

        for rule in self.rules:
            if rule.matches(app, title):
                return Category(
                    name=rule.category,
                    subcategory=rule.subcategory,
                    rule_id=rule.id,
                    confidence=rule.confidence,
                    source="rule",
                )
        return UNKNOWN_CATEGORY

    def classify_hashed(self, app: str, title_hash: str | None, title: str = "") -> Category:
        """Classify, consulting title-hash overrides first.

        Lets a user correct one specific recurring window ("this particular
        report is data entry, not browsing") without storing its title.
        """
        if title_hash and (override := self.overrides.get(("title_hash", title_hash))) is not None:
            return override
        return self.classify(app, title)

    def explain(self, app: str, title: str = "") -> dict[str, object]:
        """Show every rule that matched, for ``copynion rules test``."""
        app = (app or "").lower()
        matched = [
            {
                "id": r.id,
                "category": r.category,
                "subcategory": r.subcategory,
                "confidence": r.confidence,
                "specificity": r.specificity,
                "via": ("app+title" if r.apps and r.title else "app" if r.apps else "title"),
            }
            for r in self.rules
            if r.matches(app, title)
        ]
        override = self.overrides.get(("app", app))
        result = self.classify(app, title)
        return {
            "app": app,
            "title": title,
            "override": override.name if override else None,
            "matched_rules": matched,
            "winner": result.rule_id or ("user override" if override else None),
            "category": result.name,
            "subcategory": result.subcategory,
            "confidence": result.confidence,
            "automation_affinity": taxonomy.affinity(result.name),
        }
