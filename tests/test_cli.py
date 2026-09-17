"""End-to-end CLI behaviour, including the confirmation guards."""

from __future__ import annotations

import json

import pytest

from copynion.cli import main


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated config/data directory pair for one CLI test."""
    config = tmp_path / "config"
    data = tmp_path / "data"
    monkeypatch.setenv("COPYNION_CONFIG_DIR", str(config))
    monkeypatch.setenv("COPYNION_DATA_DIR", str(data))
    return config, data


@pytest.fixture
def initialised(home, capsys):
    assert main(["init"]) == 0
    capsys.readouterr()
    return home


def test_init_creates_config_database_and_key(home, capsys):
    config, data = home
    assert main(["init"]) == 0
    out = capsys.readouterr().out
    assert (config / "config.toml").exists()
    assert (data / "copynion.db").exists()
    assert "application names are recorded, window titles are not" in out


def test_init_is_idempotent(initialised, capsys):
    assert main(["init"]) == 0
    assert "already exists" in capsys.readouterr().out


def test_status_reports_nothing_recorded(initialised, capsys):
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Watcher:   not running" in out
    assert "no activity recorded" in out


def test_doctor_flags_the_missing_backend_here(initialised, capsys):
    # This environment is headless, so doctor must report a problem, not pretend.
    code = main(["doctor"])
    out = capsys.readouterr().out
    assert "Copynion contains no network client" in out
    assert code == 1
    assert "No window backend is available" in out


def test_pause_and_resume_round_trip(initialised, capsys):
    assert main(["pause", "--reason", "1:1 meeting"]) == 0
    main(["status"])
    assert "PAUSED (1:1 meeting)" in capsys.readouterr().out
    assert main(["resume"]) == 0
    main(["status"])
    assert "PAUSED" not in capsys.readouterr().out


def test_pause_is_recorded_in_the_audit_log(initialised, capsys):
    main(["pause", "--reason", "private"])
    capsys.readouterr()
    main(["audit"])
    assert "pause" in capsys.readouterr().out


def test_stats_on_empty_database(initialised, capsys):
    assert main(["stats", "--days", "7"]) == 0
    assert "No activity recorded" in capsys.readouterr().out


def test_stats_json_is_machine_readable(initialised, capsys):
    """Even with nothing recorded, --json must emit the full, valid structure."""
    assert main(["stats", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"]["span_count"] == 0
    assert "repetition" in payload and "shape" in payload and "categories" in payload


def test_demo_database_is_separate_from_the_real_one(initialised, capsys, tmp_path):
    """`copynion demo` must never write synthetic data into real statistics."""
    demo_db = tmp_path / "demo.db"
    assert main(["demo", "--days", "1", "--database", str(demo_db)]) == 0
    capsys.readouterr()
    assert demo_db.exists()

    main(["status"])
    assert "0 spans" in capsys.readouterr().out


def test_demo_produces_a_full_report(initialised, capsys):
    assert main(["demo", "--days", "2"]) == 0
    out = capsys.readouterr().out
    assert "Where the time went" in out
    assert "Repetition signals" in out
    assert "stage 3" in out


def test_rules_test_explains_a_classification(initialised, capsys):
    assert main(["rules", "test", "firefox", "Google Sheets - budget"]) == 0
    out = capsys.readouterr().out
    assert "data.sheets.web" in out
    assert "winner" in out


def test_rules_list_and_categories(initialised, capsys):
    assert main(["rules", "list"]) == 0
    assert "dev.ide" in capsys.readouterr().out
    assert main(["rules", "categories"]) == 0
    assert "data_entry" in capsys.readouterr().out


def test_label_rejects_an_unknown_category(initialised, capsys):
    assert main(["label", "code", "not_a_category"]) == 1
    assert "Unknown category" in capsys.readouterr().out


def test_label_applies_a_user_override(initialised, capsys):
    assert main(["label", "localc", "data_entry", "--subcategory", "invoices"]) == 0
    out = capsys.readouterr().out
    assert "outranks the built-in rules" in out


def test_export_json_is_valid_and_omits_titles(initialised, capsys):
    assert main(["export", "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["includes_titles"] is False
    assert "spans" in payload


def test_export_to_file_is_owner_only(initialised, tmp_path, capsys):
    target = tmp_path / "export.json"
    assert main(["export", "--output", str(target)]) == 0
    assert target.stat().st_mode & 0o777 == 0o600


def test_export_with_titles_warns_on_stderr(initialised, capsys):
    main(["export", "--include-titles"])
    assert "decrypted window titles in plain text" in capsys.readouterr().err


def test_purge_refuses_without_confirmation_when_not_a_tty(initialised, capsys):
    """A non-interactive shell must not be able to nuke the database by accident."""
    assert main(["purge", "--all"]) == 1
    out = capsys.readouterr().out
    assert "Refusing to proceed without confirmation" in out


def test_purge_all_with_explicit_yes(initialised, capsys):
    main(["purge", "--all", "--yes"])
    assert "Deleted" in capsys.readouterr().out


def test_purge_needs_a_filter(initialised, capsys):
    assert main(["purge"]) == 1
    assert "Nothing specified" in capsys.readouterr().out


def test_watch_exits_cleanly_when_no_backend_is_available(initialised, capsys):
    assert main(["watch"]) == 2
    assert "No window backend is available" in capsys.readouterr().err


def test_stop_reports_when_nothing_is_running(initialised, capsys):
    assert main(["stop"]) == 1
    assert "No watcher is running" in capsys.readouterr().out
