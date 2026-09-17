"""Classification rules and precedence."""

from __future__ import annotations

import pytest

from copynion.categorize import Categoriser, load_rules, taxonomy
from copynion.categorize.rules import Rule
from copynion.models import Category


@pytest.fixture
def engine():
    return Categoriser()


@pytest.mark.parametrize(
    "app, title, expected",
    [
        ("code", "sampler.py - copynion", "development"),
        ("alacritty", "~/projects", "terminal"),
        ("localc", "budget.ods", "data_entry"),
        ("thunderbird", "Inbox", "communication"),
        ("zoom", "Zoom Meeting", "meetings"),
        ("nautilus", "Downloads", "file_mgmt"),
        ("figma", "Design system", "design"),
        ("firefox", "GitHub - a/b: Pull requests", "development"),
        ("firefox", "Google Sheets - Q3", "data_entry"),
        ("firefox", "YouTube - music", "leisure"),
        ("firefox", "MDN Web Docs", "research"),
        ("totally-unknown-app", "whatever", "uncategorised"),
    ],
)
def test_classification(engine, app, title, expected):
    assert engine.classify(app, title).name == expected


def test_app_rules_outrank_generic_title_rules(engine):
    """An IDE showing a .py file is the IDE rule's call, not the filename regex's."""
    result = engine.classify("code", "main.py - project")
    assert result.rule_id == "dev.ide"
    assert result.subcategory == "ide"


def test_app_plus_title_outranks_app_alone(engine):
    result = engine.classify("firefox", "JIRA OPS-1 - board")
    assert result.name == "project_mgmt"
    assert result.confidence > engine.classify("firefox", "a blog").confidence


def test_user_override_beats_every_rule():
    override = Category("data_entry", None, "user", 1.0, source="user")
    engine = Categoriser(overrides={("app", "code"): override})
    assert engine.classify("code", "main.py").name == "data_entry"
    assert engine.classify("code", "main.py").source == "user"


def test_title_hash_override_targets_one_window():
    override = Category("data_entry", None, "user", 1.0, source="user")
    engine = Categoriser(overrides={("title_hash", "abc123"): override})
    assert engine.classify_hashed("firefox", "abc123").name == "data_entry"
    assert engine.classify_hashed("firefox", "other", "a blog").name == "research"


def test_afk_is_always_idle(engine):
    assert engine.classify("code", "main.py", afk=True).name == "idle"


def test_title_rule_cannot_fire_without_a_title(engine):
    """With titles withheld by policy, a title rule must not guess."""
    assert engine.classify("firefox", "").name == "research"  # falls back to the app rule
    assert engine.classify("firefox", "").subcategory == "general-browsing"


def test_unknown_app_is_reported_honestly(engine):
    result = engine.classify("mystery-app", "mystery window")
    assert result.name == "uncategorised"
    assert result.confidence == 0.0


def test_explain_shows_all_matches_and_the_winner(engine):
    result = engine.explain("firefox", "GitHub - pull request")
    ids = [r["id"] for r in result["matched_rules"]]
    assert "dev.browser.gh" in ids and "research.browser" in ids
    assert result["winner"] == "dev.browser.gh"


def test_every_builtin_rule_uses_a_known_category():
    for rule in load_rules():
        assert rule.category in taxonomy.VALID_CATEGORIES


def test_rule_with_unknown_category_is_rejected():
    with pytest.raises(ValueError, match="unknown category"):
        Rule.from_dict({"id": "x", "apps": ["a"], "category": "not_a_category"})


def test_rule_matching_nothing_is_rejected():
    with pytest.raises(ValueError, match="matches nothing"):
        Rule.from_dict({"id": "x", "category": "development"})


def test_rule_with_bad_regex_is_rejected():
    with pytest.raises(ValueError, match="invalid title regex"):
        Rule.from_dict({"id": "x", "title": "(unclosed", "category": "development"})


def test_user_rules_override_builtins_by_id(tmp_path):
    user = tmp_path / "rules.json"
    user.write_text(
        '{"rules": [{"id": "dev.ide", "apps": ["code"], "category": "documents", '
        '"confidence": 0.99}]}'
    )
    engine = Categoriser(user_rules_path=user)
    assert engine.classify("code", "notes").name == "documents"


def test_taxonomy_affinities_are_sane():
    assert taxonomy.affinity("data_entry") > taxonomy.affinity("design")
    assert taxonomy.affinity("idle") == 0.0
    for entry in taxonomy.TAXONOMY:
        assert 0.0 <= entry.automation_affinity <= 1.0
