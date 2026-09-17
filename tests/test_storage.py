"""Storage, retention, deletion and export."""

from __future__ import annotations

import time

import pytest

from copynion.models import ActivitySpan, Category, Visibility
from copynion.privacy import Vault, VaultError
from copynion.storage import Store


def make_span(store, offset=0, app="code", title="main.py", afk=False, category="development"):
    now = time.time() - offset
    span = ActivitySpan(
        started_at=now - 300,
        ended_at=now,
        app=app,
        title=title,
        title_hash=store.vault.fingerprint(title),
        category=Category(category, None, "rule:test", 0.9),
        visibility=Visibility.FULL,
        afk=afk,
    )
    store.add_span(span)
    return span


def test_database_is_owner_only(tmp_path, vault):
    store = Store(tmp_path / "c.db", vault)
    assert store.path.stat().st_mode & 0o777 == 0o600
    store.close()


def test_span_and_title_roundtrip(store):
    span = make_span(store)
    assert store.title_of(span.id) == "main.py"
    assert store.spans()[0]["app"] == "code"


def test_rollups_accumulate(store):
    make_span(store)
    make_span(store, app="code")
    rollup = store.rollups()[0]
    assert rollup["span_count"] == 2
    assert rollup["seconds"] == 600.0


def test_user_label_relabels_history_and_statistics(store):
    make_span(store, app="localc", category="uncategorised")
    store.set_override("app", "localc", "data_entry")
    assert store.spans()[0]["category"] == "data_entry"
    assert store.rollups()[0]["category"] == "data_entry"
    assert store.spans()[0]["rule_id"] == "user"


def test_retention_deletes_detail_but_keeps_statistics(store):
    make_span(store, offset=100 * 86400)   # old
    make_span(store, offset=0)             # today
    assert store.counts()["spans"] == 2
    rollup_days_before = store.counts()["rollup_days"]

    removed = store.enforce_retention(detail_days=90, title_days=30)
    assert removed["spans"] == 1
    assert store.counts()["spans"] == 1
    # The point of the two-tier design: history survives the purge of detail.
    assert store.counts()["rollup_days"] == rollup_days_before


def test_titles_expire_before_spans(store):
    make_span(store, offset=45 * 86400)
    store.enforce_retention(detail_days=90, title_days=30)
    assert store.counts()["spans"] == 1
    assert store.counts()["titles"] == 0


def test_forget_titles_keeps_everything_else(store):
    span = make_span(store)
    assert store.forget_titles() == 1
    assert store.title_of(span.id) is None
    assert store.counts()["spans"] == 1


def test_purge_all_removes_everything(store):
    make_span(store)
    make_span(store, app="firefox")
    store.purge(everything=True)
    counts = store.counts()
    assert counts["spans"] == 0
    assert counts["titles"] == 0
    assert counts["rollup_days"] == 0


def test_purge_by_app(store):
    make_span(store, app="code")
    make_span(store, app="firefox")
    store.purge(app="firefox")
    assert [s["app"] for s in store.spans()] == ["code"]
    assert all(r["app"] == "code" for r in store.rollups())


def test_purge_requires_a_filter(store):
    with pytest.raises(ValueError, match="needs a filter"):
        store.purge()


def test_export_omits_titles_by_default(store):
    make_span(store)
    record = list(store.export())[0]
    assert "title" not in record
    assert "title_hash" in record
    assert list(store.export(include_titles=True))[0]["title"] == "main.py"


def test_audit_log_records_privacy_actions(store):
    make_span(store)
    store.forget_titles()
    store.purge(everything=True)
    actions = [row["action"] for row in store.audit_entries()]
    assert "forget_titles" in actions
    assert "purge_all" in actions


def test_afk_time_is_tracked_separately(store):
    make_span(store, afk=True, category="idle")
    assert store.rollups()[0]["afk_seconds"] == 300.0


def test_titles_are_not_readable_without_the_key(tmp_path):
    """The database on its own must not give up window titles."""
    vault = Vault(Vault.generate_key())
    store = Store(tmp_path / "c.db", vault)
    make_span(store, title="Acme Corp acquisition terms")
    store.close()

    raw = (tmp_path / "c.db").read_bytes()
    assert b"Acme Corp" not in raw

    stranger = Store(tmp_path / "c.db", Vault(Vault.generate_key()))
    with pytest.raises(VaultError, match="failed authentication"):
        stranger.title_of(1)
    stranger.close()
