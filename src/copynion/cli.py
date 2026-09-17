"""Command-line interface.

Design rule for this file: every command that touches recorded data says what it
is about to do in plain language, and every destructive one asks first unless
explicitly told not to. Someone should be able to run any command out of
curiosity without discovering afterwards that it deleted their history or
started watching them.
"""

from __future__ import annotations

import argparse
import csv
import json
import signal
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from copynion import __version__
from copynion.categorize import Categoriser, taxonomy
from copynion.config import Config, config_dir, data_dir
from copynion.models import Visibility
from copynion.observer.backends import (
    BackendUnavailable,
    FakeBackend,
    backend_report,
    detect_backend,
)
from copynion.observer.sampler import Sampler
from copynion.privacy import Policy, Redactor, Vault
from copynion.runtime import State
from copynion.stats import human_duration, render, render_compact, summarise
from copynion.storage import Store

# -- wiring -------------------------------------------------------------------

def _build_policy(cfg: Config) -> Policy:
    return Policy(
        default_visibility=Visibility(cfg.privacy.default_visibility),
        app_rules=cfg.privacy.apps,
        title_denylist=cfg.privacy.title_denylist,
        respect_private_browsing=cfg.privacy.respect_private_browsing,
        use_sensitive_defaults=cfg.privacy.use_sensitive_app_defaults,
    )


def _build_vault(cfg: Config, create: bool = False) -> Vault:
    return Vault.load(
        cfg.key_path,
        create=create,
        use_keyring=cfg.privacy.use_keyring,
        allow_unencrypted=cfg.privacy.allow_unencrypted_titles,
    )


def _open_store(cfg: Config, create_key: bool = False) -> Store:
    return Store(cfg.db_path, _build_vault(cfg, create=create_key))


def _period(args) -> tuple[float, float]:
    """Resolve --today / --days N / --since into a timestamp range."""
    now = time.time()
    if getattr(args, "today", False):
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return start.timestamp(), now
    if getattr(args, "since", None):
        try:
            start = datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError:
            raise SystemExit(f"--since expects YYYY-MM-DD, got {args.since!r}") from None
        return start.timestamp(), now
    days = getattr(args, "days", 7) or 7
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=days - 1
    )
    return start.timestamp(), now


def _confirm(prompt: str, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("Refusing to proceed without confirmation (stdin is not a terminal).")
        print("Re-run with --yes if you are certain.")
        return False
    return input(f"{prompt} Type 'yes' to confirm: ").strip().lower() == "yes"


# -- commands -----------------------------------------------------------------

def cmd_init(args) -> int:
    cfg = Config.load(args.config) if args.config else Config()
    config_path = Path(args.config) if args.config else config_dir() / "config.toml"

    if config_path.exists() and not args.force:
        print(f"Config already exists at {config_path} (use --force to overwrite).")
    else:
        Config().write_default(config_path)
        print(f"Wrote default config to {config_path}")

    cfg = Config.load(config_path)
    vault = _build_vault(cfg, create=True)
    store = Store(cfg.db_path, vault)
    store.audit("init", f"version {__version__}")
    status = vault.status()

    print(f"Created database at {cfg.db_path}")
    if status["encryption_active"]:
        print(f"Title encryption is active; the key is stored in {vault.describe_key_location()}.")
    else:
        print("\nWARNING: title encryption is NOT active.")
        if not status["crypto_library"]:
            print("  The 'cryptography' package is unavailable. Install it with:")
            print("      pip install 'copynion[crypto]'")
        print("  Until then, window titles will not be stored at all (fail-closed).")
        print("  Everything else - app names, durations, categories - still works.")

    print("\nDefaults: application names are recorded, window titles are not.")
    print(f"To capture titles for specific tools, edit [privacy.apps] in {config_path}.")
    print("\nNext:  copynion watch     (start observing)")
    print("       copynion demo      (see what a week looks like, with fake data)")
    store.close()
    return 0


def cmd_watch(args) -> int:
    cfg = Config.load(args.config)
    state = State.load(cfg.state_path)

    if state.watcher_running and not args.force:
        print(f"A watcher is already running (pid {state.watcher_pid}).")
        print("Use --force to start another, or `copynion stop` to stop it.")
        return 1

    try:
        backend = detect_backend()
    except BackendUnavailable as exc:
        print(exc, file=sys.stderr)
        return 2

    store = _open_store(cfg)
    vault = store.vault
    sampler = Sampler(
        backend,
        store,
        policy=_build_policy(cfg),
        redactor=Redactor(
            custom_patterns=cfg.privacy.custom_redaction_patterns,
            enabled=cfg.privacy.redaction_enabled,
        ),
        vault=vault,
        categoriser=Categoriser(overrides=store.overrides(), user_rules_path=cfg.rules_path),
        settings=cfg.observation,
    )

    state.claim_watcher()
    state.save(cfg.state_path)
    store.audit("watch_start", f"backend={backend.name}")

    print(f"Copynion is observing via the '{backend.name}' backend.")
    print(f"  Sampling every {cfg.observation.poll_interval_seconds:g}s; "
          f"idle after {cfg.observation.idle_threshold_seconds:g}s.")
    print(f"  Default fidelity: {cfg.privacy.default_visibility}. "
          f"Titles encrypted: {vault.available}.")
    print(f"  Data: {cfg.db_path}")
    print("  Pause any time with `copynion pause`. Stop with Ctrl-C.\n")

    stop = threading.Event()

    def handle_signal(signum, frame):
        print("\nStopping; flushing the current activity span...")
        stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    last_retention = 0.0
    ticks = 0
    try:
        while not stop.is_set():
            started = time.time()

            # Honour pause/resume issued from another terminal.
            live = State.load(cfg.state_path)
            if live.paused and not sampler.policy.paused:
                sampler.policy.pause(live.pause_reason or "user requested")
                sampler.flush()
                print(f"[{_clock()}] paused ({live.pause_reason or 'user requested'})")
            elif not live.paused and sampler.policy.paused:
                sampler.policy.resume()
                print(f"[{_clock()}] resumed")

            sampler.tick()
            ticks += 1

            # Retention runs hourly so an always-on watcher enforces it without
            # needing a separate scheduled job.
            if started - last_retention > 3600:
                removed = store.enforce_retention(
                    cfg.retention.detail_days, cfg.retention.title_days
                )
                if removed["spans"] or removed["titles"]:
                    print(f"[{_clock()}] retention: {removed}")
                last_retention = started

            live.last_tick_at = started
            live.stats = sampler.stats.as_dict()
            live.watcher_pid = state.watcher_pid
            live.watcher_started_at = state.watcher_started_at
            live.save(cfg.state_path)

            if args.max_ticks and ticks >= args.max_ticks:
                break
            elapsed = time.time() - started
            stop.wait(max(0.0, cfg.observation.poll_interval_seconds - elapsed))
    finally:
        sampler.flush()
        backend.close()
        final = State.load(cfg.state_path)
        final.release_watcher()
        final.stats = sampler.stats.as_dict()
        final.save(cfg.state_path)
        store.audit("watch_stop", json.dumps(sampler.stats.as_dict()))
        store.close()

    print(f"\nRecorded {sampler.stats.spans_written} activity spans "
          f"from {sampler.stats.samples} samples.")
    if sampler.stats.redaction_hits:
        total = sum(sampler.stats.redaction_hits.values())
        print(f"Redaction removed {total} sensitive fragment(s) before storing: "
              f"{', '.join(sorted(sampler.stats.redaction_hits))}")
    if sampler.stats.titles_withheld:
        print(f"{sampler.stats.titles_withheld} title(s) were withheld because "
              "encryption is unavailable.")
    return 0


def cmd_stop(args) -> int:
    cfg = Config.load(args.config)
    state = State.load(cfg.state_path)
    if not state.watcher_running:
        print("No watcher is running.")
        return 1
    import os

    os.kill(state.watcher_pid, signal.SIGTERM)
    print(f"Asked watcher (pid {state.watcher_pid}) to stop.")
    return 0


def cmd_status(args) -> int:
    cfg = Config.load(args.config)
    state = State.load(cfg.state_path)
    store = _open_store(cfg)

    print(f"Copynion {__version__}")
    if state.watcher_running:
        uptime = time.time() - (state.watcher_started_at or time.time())
        print(f"  Watcher:   running (pid {state.watcher_pid}, up {human_duration(uptime)})")
    else:
        print("  Watcher:   not running")
    if state.paused:
        print(f"  Recording: PAUSED ({state.pause_reason or 'user requested'})")
    else:
        print("  Recording: active" if state.watcher_running else "  Recording: idle")

    vault_status = store.vault.status()
    print(f"  Titles:    {'encrypted' if vault_status['encryption_active'] else 'NOT stored (fail-closed)'}")
    print(f"  Fidelity:  {cfg.privacy.default_visibility} by default")
    print(f"  Retention: {cfg.retention.detail_days}d detail, {cfg.retention.title_days}d titles")
    print(f"  Database:  {cfg.db_path}")

    counts = store.counts()
    print(f"  Stored:    {counts['spans']} spans over {counts['rollup_days']} day(s), "
          f"{counts['titles']} titles, {counts['overrides']} user labels")

    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    today = summarise(store.spans(since=start))
    print(f"\n  Today:     {render_compact(today)}")
    store.close()
    return 0


def cmd_stats(args) -> int:
    cfg = Config.load(args.config)
    store = _open_store(cfg)
    since, until = _period(args)
    summary = summarise(
        store.spans(since=since, until=until),
        since=since,
        until=until,
        min_recurrence=args.min_recurrence,
    )
    if args.json:
        print(json.dumps(summary.as_dict(), indent=2))
    else:
        print(render(summary, show_hours=not args.no_hours))
    store.close()
    return 0


def cmd_demo(args) -> int:
    """Populate a throwaway database with a synthetic week and show the report."""
    from copynion.demo import generate_week

    path = Path(args.database) if args.database else data_dir() / "demo.db"
    if path.exists() and not args.keep:
        path.unlink()

    vault = Vault(Vault.generate_key())
    store = Store(path, vault)
    cfg = Config()
    cfg.observation.min_span_seconds = 30

    # The demo opts every app into full fidelity: it is fabricated data, and the
    # point is to show what the richest mode actually produces.
    demo_apps = dict.fromkeys(
        ["code", "firefox", "localc", "thunderbird", "alacritty", "slack", "zoom", "nautilus"],
        "full",
    )
    sampler = Sampler(
        FakeBackend([]),
        store,
        policy=Policy(app_rules=demo_apps),
        redactor=Redactor(),
        vault=vault,
        settings=cfg.observation,
    )
    observations = generate_week(days=args.days)
    for obs in observations:
        sampler.ingest(obs)
    sampler._flush(observations[-1].timestamp)

    print(f"Generated {len(observations)} synthetic samples over {args.days} day(s) "
          f"-> {store.counts()['spans']} spans.\n")
    print(render(summarise(store.spans())))
    print(f"\n(Demo database: {path} - delete it whenever you like.)")
    store.close()
    return 0


def cmd_doctor(args) -> int:
    cfg = Config.load(args.config)
    problems: list[str] = []
    notes: list[str] = []

    print("Backends")
    for entry in backend_report():
        mark = "ok " if entry["available"] else "-- "
        print(f"  [{mark}] {entry['name']:<10} {entry['requirement']}")
    if not any(e["available"] for e in backend_report()):
        problems.append("No window backend is available; `copynion watch` cannot run here.")

    print("\nPrivacy")
    vault = _build_vault(cfg)
    status = vault.status()
    print(f"  cryptography installed .... {'yes' if status['crypto_library'] else 'NO'}")
    print(f"  vault key loaded .......... {'yes' if status['key_loaded'] else 'no'}")
    print(f"  key stored in ............. {vault.describe_key_location()}")
    print(f"  title encryption active ... {'yes' if status['encryption_active'] else 'NO'}")
    print(f"  redaction ................. {'on' if cfg.privacy.redaction_enabled else 'OFF'}")
    print(f"  default fidelity .......... {cfg.privacy.default_visibility}")
    print(f"  private browsing respected  {'yes' if cfg.privacy.respect_private_browsing else 'no'}")

    if not status["encryption_active"]:
        if cfg.privacy.allow_unencrypted_titles:
            problems.append(
                "Titles would be stored UNENCRYPTED "
                "(privacy.allow_unencrypted_titles = true). Install the crypto extra."
            )
        else:
            notes.append("Titles are not stored at all until encryption is available.")
    if not cfg.privacy.redaction_enabled:
        problems.append("Redaction is disabled; titles will be stored as-is.")

    print("\nFiles")
    for label, path in (
        ("config", cfg.path or config_dir() / "config.toml"),
        ("database", cfg.db_path),
        ("key file", cfg.key_path),
    ):
        if not Path(path).exists():
            print(f"  {label:<9} {path}  (not created yet)")
            continue
        mode = Path(path).stat().st_mode & 0o777
        ok = not (mode & 0o077)
        print(f"  {label:<9} {path}  mode {oct(mode)} {'ok' if ok else '<- TOO OPEN'}")
        if not ok:
            problems.append(f"{path} is readable by other users; run: chmod 600 {path}")

    print("\nNetwork")
    print("  Copynion contains no network client. Nothing is uploaded, ever.")

    if notes:
        print("\nNotes")
        for note in notes:
            print(f"  - {note}")
    if problems:
        print("\nProblems")
        for problem in problems:
            print(f"  ! {problem}")
        return 1
    print("\nNo problems found.")
    return 0


def cmd_pause(args) -> int:
    cfg = Config.load(args.config)
    state = State.load(cfg.state_path)
    state.paused = True
    state.pause_reason = args.reason or "user requested"
    state.paused_at = time.time()
    state.save(cfg.state_path)
    store = _open_store(cfg)
    store.audit("pause", state.pause_reason)
    store.close()
    if state.watcher_running:
        print(f"Recording paused; the watcher will stop within one poll interval "
              f"({cfg.observation.poll_interval_seconds:g}s).")
    else:
        print("Recording paused. It will stay paused when a watcher next starts.")
    print("Resume with: copynion resume")
    return 0


def cmd_resume(args) -> int:
    cfg = Config.load(args.config)
    state = State.load(cfg.state_path)
    if not state.paused:
        print("Recording is not paused.")
        return 0
    paused_for = time.time() - (state.paused_at or time.time())
    state.paused = False
    state.pause_reason = ""
    state.paused_at = None
    state.save(cfg.state_path)
    store = _open_store(cfg)
    store.audit("resume", f"after {human_duration(paused_for)}")
    store.close()
    print(f"Recording resumed after {human_duration(paused_for)}.")
    return 0


def cmd_rules(args) -> int:
    cfg = Config.load(args.config)
    store = _open_store(cfg)
    engine = Categoriser(overrides=store.overrides(), user_rules_path=cfg.rules_path)

    if args.rules_command == "test":
        result = engine.explain(args.app, args.title or "")
        if args.json:
            print(json.dumps(result, indent=2))
            store.close()
            return 0
        print(f"app:   {result['app']}")
        print(f"title: {result['title'] or '(none)'}")
        if result["override"]:
            print(f"\nUser override -> {result['override']}")
        print("\nMatching rules (most specific first):")
        if not result["matched_rules"]:
            print("  (none)")
        for rule in result["matched_rules"]:
            winner = " <- winner" if rule["id"] == result["winner"] else ""
            print(f"  {rule['id']:<22} {rule['category']:<15} via {rule['via']:<10} "
                  f"conf {rule['confidence']:.2f}{winner}")
        print(f"\nResult: {result['category']}"
              f"{'/' + result['subcategory'] if result['subcategory'] else ''} "
              f"(confidence {result['confidence']:.2f})")
        print(f"Automation affinity of this category: {result['automation_affinity']:.2f}")
    elif args.rules_command == "categories":
        print(f"{'name':<16} {'affinity':>9}  {'focus':<6} description")
        for c in taxonomy.TAXONOMY:
            print(f"{c.name:<16} {c.automation_affinity:>9.2f}  "
                  f"{'yes' if c.focus_work else 'no':<6} {c.description}")
    else:
        print(f"{len(engine.rules)} rules loaded "
              f"(built-in + {cfg.rules_path if cfg.rules_path.exists() else 'no user rules'})\n")
        for rule in engine.rules:
            target = f"{rule.category}/{rule.subcategory}" if rule.subcategory else rule.category
            match = []
            if rule.apps:
                match.append(f"apps={','.join(rule.apps[:4])}{'...' if len(rule.apps) > 4 else ''}")
            if rule.title:
                match.append(f"title=/{rule.title[:34]}/")
            print(f"  {rule.id:<22} -> {target:<26} {' '.join(match)}")
    store.close()
    return 0


def cmd_label(args) -> int:
    """Correct a categorisation. User labels outrank every rule, permanently."""
    cfg = Config.load(args.config)
    if args.category not in taxonomy.VALID_CATEGORIES:
        print(f"Unknown category {args.category!r}. Valid categories:")
        print("  " + ", ".join(sorted(taxonomy.VALID_CATEGORIES)))
        return 1
    store = _open_store(cfg)
    store.set_override("app", args.app.lower(), args.category, args.subcategory)
    affected = len(store.spans(app=args.app.lower()))
    print(f"'{args.app}' is now labelled '{args.category}'"
          f"{'/' + args.subcategory if args.subcategory else ''}.")
    print(f"Re-labelled {affected} existing span(s); statistics have been rebuilt.")
    print("This label outranks the built-in rules and survives rule updates.")
    store.close()
    return 0


def cmd_export(args) -> int:
    cfg = Config.load(args.config)
    store = _open_store(cfg)
    since, until = _period(args)
    records = list(store.export(include_titles=args.include_titles, since=since))

    if args.include_titles:
        print(f"NOTE: this export contains {sum(1 for r in records if r.get('title'))} "
              "decrypted window titles in plain text.", file=sys.stderr)

    out = Path(args.output) if args.output else None
    if args.format == "csv":
        fields = [
            "id", "started_at", "ended_at", "duration", "day", "app", "category",
            "subcategory", "rule_id", "confidence", "afk", "visibility", "title_hash",
        ]
        if args.include_titles:
            fields.append("title")
        handle = out.open("w", newline="", encoding="utf-8") if out else sys.stdout
        try:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(records)
        finally:
            if out:
                handle.close()
    else:
        payload = {
            "copynion_version": __version__,
            "exported_at": time.time(),
            "includes_titles": args.include_titles,
            "spans": records,
        }
        text = json.dumps(payload, indent=2)
        if out:
            out.write_text(text, encoding="utf-8")
        else:
            print(text)

    if out:
        out.chmod(0o600)
        print(f"Exported {len(records)} span(s) to {out} (mode 600).", file=sys.stderr)
    store.audit("export", f"{len(records)} spans, titles={args.include_titles}")
    store.close()
    return 0


def cmd_purge(args) -> int:
    cfg = Config.load(args.config)
    store = _open_store(cfg)

    if args.titles_only:
        count = store.counts()["titles"]
        if not count:
            print("No stored titles to delete.")
            store.close()
            return 0
        if not _confirm(f"Delete all {count} stored window titles (statistics are kept)?", args.yes):
            print("Cancelled.")
            store.close()
            return 1
        print(f"Deleted {store.forget_titles()} title(s). Spans and statistics are intact.")
        store.close()
        return 0

    if args.all:
        counts = store.counts()
        print(f"This deletes EVERYTHING: {counts['spans']} spans, {counts['titles']} titles,")
        print(f"and all daily statistics across {counts['rollup_days']} day(s).")
        print("It cannot be undone.")
        if not _confirm("Delete all recorded data?", args.yes):
            print("Cancelled.")
            store.close()
            return 1
        removed = store.purge(everything=True)
        print(f"Deleted {removed['spans']} spans and all statistics.")
        store.close()
        return 0

    before = after = None
    if args.before:
        before = datetime.strptime(args.before, "%Y-%m-%d").timestamp()
    if args.after:
        after = datetime.strptime(args.after, "%Y-%m-%d").timestamp()
    if not any([before, after, args.app]):
        print("Nothing specified. Use --before, --after, --app, --titles-only or --all.")
        store.close()
        return 1

    doomed = len(store.spans(since=after, until=before, app=args.app))
    what = []
    if args.app:
        what.append(f"app '{args.app}'")
    if before:
        what.append(f"before {args.before}")
    if after:
        what.append(f"after {args.after}")
    if not _confirm(f"Delete {doomed} span(s) matching {' and '.join(what)}?", args.yes):
        print("Cancelled.")
        store.close()
        return 1
    removed = store.purge(before=before, after=after, app=args.app)
    print(f"Deleted {removed['spans']} span(s); statistics rebuilt.")
    store.close()
    return 0


def cmd_audit(args) -> int:
    cfg = Config.load(args.config)
    store = _open_store(cfg)
    entries = store.audit_entries(args.limit)
    if not entries:
        print("No audit entries yet.")
    for entry in entries:
        when = datetime.fromtimestamp(entry["at"]).strftime("%Y-%m-%d %H:%M:%S")
        print(f"  {when}  {entry['action']:<18} {entry['detail'][:70]}")
    store.close()
    return 0


def _clock() -> str:
    return datetime.now().strftime("%H:%M:%S")


# -- argument parsing ---------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="copynion",
        description="A local-first companion that learns how you use your computer.",
        epilog="All data stays on this machine. Copynion has no network code.",
    )
    parser.add_argument("--version", action="version", version=f"copynion {__version__}")
    parser.add_argument("--config", type=Path, help="path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create the config, key and database")
    p.add_argument("--force", action="store_true", help="overwrite an existing config")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("watch", help="start observing (stage 1)")
    p.add_argument("--force", action="store_true", help="start even if a watcher is running")
    p.add_argument("--max-ticks", type=int, help="stop after N samples (for testing)")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("stop", help="stop the running watcher")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("status", help="what is running, and today so far")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("stats", help="usage statistics (stage 2)")
    p.add_argument("--days", type=int, default=7, help="how many days back (default 7)")
    p.add_argument("--today", action="store_true", help="today only")
    p.add_argument("--since", help="start date, YYYY-MM-DD")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--no-hours", action="store_true", help="omit the time-of-day breakdown")
    p.add_argument("--min-recurrence", type=int, default=3,
                   help="how many repeats before something counts as recurring")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("demo", help="generate a synthetic week and show the report")
    p.add_argument("--days", type=int, default=5)
    p.add_argument("--database", help="where to put the demo database")
    p.add_argument("--keep", action="store_true", help="append to an existing demo database")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("doctor", help="check backends, permissions and privacy settings")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("pause", help="stop recording immediately")
    p.add_argument("--reason", help="note for the audit log")
    p.set_defaults(func=cmd_pause)

    p = sub.add_parser("resume", help="resume recording")
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("rules", help="inspect classification rules")
    rules_sub = p.add_subparsers(dest="rules_command")
    rules_sub.add_parser("list", help="list all rules")
    t = rules_sub.add_parser("test", help="explain how one window would be classified")
    t.add_argument("app")
    t.add_argument("title", nargs="?")
    t.add_argument("--json", action="store_true")
    rules_sub.add_parser("categories", help="list the category taxonomy")
    p.set_defaults(func=cmd_rules, rules_command="list", json=False)

    p = sub.add_parser("label", help="correct a category; your label wins permanently")
    p.add_argument("app")
    p.add_argument("category")
    p.add_argument("--subcategory")
    p.set_defaults(func=cmd_label)

    p = sub.add_parser("export", help="export your data")
    p.add_argument("--format", choices=["json", "csv"], default="json")
    p.add_argument("--output", "-o", help="write to a file instead of stdout")
    p.add_argument("--days", type=int, default=3650)
    p.add_argument("--since", help="start date, YYYY-MM-DD")
    p.add_argument("--today", action="store_true")
    p.add_argument("--include-titles", action="store_true",
                   help="decrypt and include window titles (sensitive)")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("purge", help="delete recorded data")
    p.add_argument("--all", action="store_true", help="delete everything")
    p.add_argument("--titles-only", action="store_true", help="delete titles, keep statistics")
    p.add_argument("--before", help="delete data before YYYY-MM-DD")
    p.add_argument("--after", help="delete data after YYYY-MM-DD")
    p.add_argument("--app", help="delete data for one application")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p.set_defaults(func=cmd_purge)

    p = sub.add_parser("audit", help="show the log of privacy-relevant actions")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_audit)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
