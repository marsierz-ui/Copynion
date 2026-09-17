"""End-to-end CLI behaviour, including the confirmation guards."""

from __future__ import annotations

import json
import re

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
    # --database is a global option, so it precedes the subcommand.
    assert main(["--database", str(demo_db), "demo", "--days", "1"]) == 0
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


def _first_input_span_id(capsys) -> int:
    """Run `replay` with no span and return the first listed span id."""
    capsys.readouterr()  # discard anything buffered from earlier commands
    assert main(["--database", str(_DEMO_DB[0]), "replay"]) == 0
    listing = capsys.readouterr().out
    ids = [int(m) for m in re.findall(r"^\s+(\d+)\s+\d{4}-", listing, re.MULTILINE)]
    assert ids, f"no span ids found in:\n{listing}"
    return ids[0]


_DEMO_DB: list = [None]


# -- input capture ------------------------------------------------------------

def _enable_input(config_dir, fidelity="full", extra=""):
    (config_dir / "config.toml").write_text(
        f'[privacy.apps]\ncode = "full"\n\n[input]\nfidelity = "{fidelity}"\n{extra}'
    )


def test_input_is_off_by_default(initialised, capsys):
    main(["status"])
    assert "Input:     off" in capsys.readouterr().out


def test_status_shows_input_fidelity_when_on(initialised, capsys):
    config, _ = initialised
    _enable_input(config)
    main(["status"])
    assert "Input:     FULL (text redacted)" in capsys.readouterr().out


def test_status_flags_unredacted_capture(initialised, capsys):
    config, _ = initialised
    _enable_input(config, extra="redact_typed_text = false\n")
    main(["status"])
    assert "UNREDACTED" in capsys.readouterr().out


def test_doctor_flags_unredacted_input_as_a_problem(initialised, capsys):
    config, _ = initialised
    _enable_input(config, extra="redact_typed_text = false\n")
    assert main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "redact_typed_text is false" in out
    assert "everything you type is" in out


def test_doctor_reports_unavailable_input_backend(initialised, capsys):
    """Doctor must flag unavailable input capture whichever reason applies.

    The two reasons are distinct - pynput absent, versus installed but with no
    display to attach to - and which one holds depends on whether the `input`
    extra is installed in the environment running the tests. Asserting on the
    reason made this test pass or fail depending on that, so assert on the
    problem line instead, which doctor emits either way.
    """
    config, _ = initialised
    _enable_input(config)
    assert main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "Input capture is configured but unavailable" in out


def test_invalid_input_fidelity_is_rejected(initialised, capsys):
    config, _ = initialised
    (config / "config.toml").write_text('[input]\nfidelity = "everything"\n')
    assert main(["status"]) == 1
    assert "input.fidelity must be one of" in capsys.readouterr().err


def test_demo_records_input_and_replay_reads_it_back(initialised, tmp_path, capsys):
    _DEMO_DB[0] = tmp_path / "demo.db"
    assert main(["--database", str(_DEMO_DB[0]), "demo", "--days", "1"]) == 0
    assert "fidelity" in capsys.readouterr().out

    span_id = _first_input_span_id(capsys)
    assert main(["--database", str(_DEMO_DB[0]), "replay", "--span", str(span_id)]) == 0
    out = capsys.readouterr().out
    assert "keystrokes" in out
    assert "stage 4" in out


def test_replay_events_are_in_chronological_order(initialised, tmp_path, capsys):
    """Pins the ordering fix: a coalesced text run must not land after the
    events that happened during it."""
    _DEMO_DB[0] = tmp_path / "demo.db"
    main(["--database", str(_DEMO_DB[0]), "demo", "--days", "1"])
    span_id = _first_input_span_id(capsys)

    main(["--database", str(_DEMO_DB[0]), "replay", "--span", str(span_id), "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out[out.index("{"):])
    times = [e["t"] for e in payload["events"]]
    assert times == sorted(times)
    assert len(times) > 5


def test_replay_without_input_says_so(initialised, capsys):
    assert main(["replay"]) == 1
    assert "No spans with recorded input" in capsys.readouterr().out


def test_replay_rejects_an_unknown_span(initialised, capsys):
    assert main(["replay", "--span", "9999"]) == 1
    assert "No span with id 9999" in capsys.readouterr().out


def test_purge_inputs_only_keeps_statistics(initialised, tmp_path, capsys):
    demo_db = tmp_path / "demo.db"
    main(["--database", str(demo_db), "demo", "--days", "1"])
    capsys.readouterr()

    assert main(["--database", str(demo_db), "purge", "--inputs-only", "--yes"]) == 0
    assert "Deleted" in capsys.readouterr().out

    assert main(["--database", str(demo_db), "stats", "--days", "30", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    # The events are gone but the content-free counts survive.
    assert payload["input"]["keystrokes"] > 0
    assert main(["--database", str(demo_db), "replay"]) == 0
