"""Scrub sensitive strings out of window titles before they are stored.

Window titles are startlingly revealing. A single title can contain a customer's
full name, an invoice total, a password-reset URL with a live token, or the
subject line of a confidential email. Copynion needs the *shape* of a title to
spot repetition, not its contents, so we replace anything that looks like
personal or secret data with a stable placeholder.

The rules are deliberately aggressive. A false positive costs us a little
categorisation accuracy; a false negative writes someone's bank details to disk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from re import Pattern

# Each rule is (name, compiled pattern, replacement).
# Order matters: the most specific patterns run first so that, for example, an
# email address is not first mangled by the generic "long number" rule.
_RULES: list[tuple[str, Pattern[str], str]] = [
    # --- Credentials and secrets -------------------------------------------------
    (
        "secret_assignment",
        re.compile(
            r"(?i)\b(pass(?:word|wd|phrase)?|secret|token|api[-_ ]?key|auth|bearer|otp|pin)\b"
            r"\s*[:=]\s*\S+"
        ),
        r"\1=<redacted>",
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        "<jwt>",
    ),
    (
        "api_key_like",
        re.compile(
            r"\b(?:sk|pk|rk|ghp|gho|ghu|ghs|ghr|xox[baprs]|AKIA|ASIA)[-_][A-Za-z0-9_-]{12,}\b"
        ),
        "<api-key>",
    ),
    (
        "hex_secret",
        re.compile(r"\b[0-9a-fA-F]{32,}\b"),
        "<hex>",
    ),
    # --- Direct identifiers ------------------------------------------------------
    (
        "email",
        re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b"),
        "<email>",
    ),
    (
        "iban",
        re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}[ ]?[A-Z0-9]{1,4}\b"),
        "<iban>",
    ),
    (
        "card",
        re.compile(r"\b(?:\d[ -]?){13,19}\b"),
        "<card>",
    ),
    (
        "phone",
        re.compile(r"(?<![\w.])\+\d[\d ()-]{7,}\d(?![\w.])"),
        "<phone>",
    ),
    # --- Locations that leak identity --------------------------------------------
    (
        "url_query",
        re.compile(r"(https?://[^\s?#]+)\?[^\s#]*"),
        r"\1?<query>",
    ),
    (
        "home_path",
        re.compile(r"(?i)(/home/|/Users/|[A-Z]:\\Users\\)[^/\\\s]+"),
        r"\1<user>",
    ),
    # --- Generic numbers last ----------------------------------------------------
    (
        "long_number",
        re.compile(r"(?<![\w.])\d{7,}(?![\w.])"),
        "<number>",
    ),
]

_WHITESPACE = re.compile(r"\s+")

#: Titles longer than this are truncated. Extremely long titles are almost always
#: document contents bleeding into the title bar rather than a useful label.
MAX_TITLE_LENGTH = 180


@dataclass(slots=True)
class RedactionReport:
    """What a scrub actually changed - used by ``copynion doctor`` and tests."""

    text: str
    hits: dict[str, int] = field(default_factory=dict)
    truncated: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.hits) or self.truncated

    @property
    def total_hits(self) -> int:
        return sum(self.hits.values())


class Redactor:
    """Applies the redaction rules, plus any user-supplied custom patterns."""

    def __init__(
        self,
        custom_patterns: list[str] | None = None,
        max_length: int = MAX_TITLE_LENGTH,
        enabled: bool = True,
    ) -> None:
        self.enabled = enabled
        self.max_length = max_length
        self._rules = list(_RULES)
        for i, raw in enumerate(custom_patterns or []):
            try:
                self._rules.append((f"custom_{i}", re.compile(raw), "<redacted>"))
            except re.error as exc:  # A bad user regex must never crash the observer.
                raise ValueError(f"invalid custom redaction pattern {raw!r}: {exc}") from exc

    @property
    def rule_names(self) -> list[str]:
        return [name for name, _, _ in self._rules]

    def scrub(self, text: str) -> RedactionReport:
        """Return ``text`` with sensitive substrings replaced by placeholders."""
        if not text:
            return RedactionReport(text="")
        if not self.enabled:
            # Even with redaction switched off we still normalise and truncate,
            # so the storage layer sees a bounded, predictable string.
            return RedactionReport(text=self._finish(text)[0], truncated=len(text) > self.max_length)

        hits: dict[str, int] = {}
        out = text
        for name, pattern, replacement in self._rules:
            out, count = pattern.subn(replacement, out)
            if count:
                hits[name] = hits.get(name, 0) + count

        out, truncated = self._finish(out)
        return RedactionReport(text=out, hits=hits, truncated=truncated)

    def scrub_text(self, text: str) -> str:
        """Convenience wrapper returning just the cleaned string."""
        return self.scrub(text).text

    def _finish(self, text: str) -> tuple[str, bool]:
        text = _WHITESPACE.sub(" ", text).strip()
        if len(text) > self.max_length:
            return text[: self.max_length].rstrip() + "...", True
        return text, False
