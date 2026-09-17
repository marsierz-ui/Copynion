# Architecture

## The pipeline

```
  ┌────────────────┐
  │ WindowBackend  │  linux-x11 / macos / windows / fake
  │  .poll()       │  -> Observation(app, title, idle_seconds)
  └───────┬────────┘
          │
  ┌───────▼────────┐
  │    Policy      │  may we record this, and at what fidelity?
  │  .apply()      │  -> full | app_only | opaque | drop
  └───────┬────────┘
          │
  ┌───────▼────────┐
  │   Redactor     │  strip emails, cards, tokens, paths...
  │  .scrub()      │  -> cleaned title + hit report
  └───────┬────────┘
          │
  ┌───────▼────────┐
  │     Vault      │  HMAC fingerprint, then AES-GCM seal
  │ .fingerprint() │
  │ .seal()        │
  └───────┬────────┘
          │
  ┌───────▼────────┐
  │  Categoriser   │  overrides > rules > idle > uncategorised
  │  .classify()   │
  └───────┬────────┘
          │
  ┌───────▼────────┐
  │    Sampler     │  collapse identical consecutive samples into spans
  │  .ingest()     │
  └───────┬────────┘
          │
  ┌───────▼────────┐        ┌──────────────┐
  │     Store      │───────►│  summarise() │ -> Summary -> render()
  │  SQLite, 0600  │        │   (stats)    │
  └────────────────┘        └──────────────┘
```

The ordering inside `Sampler.ingest()` is load-bearing and documented there.
Policy before redaction; redaction before fingerprinting; fingerprinting before
sealing. See [PRIVACY.md](PRIVACY.md) for why each of those matters.

## Module map

| Module | Responsibility |
|--------|----------------|
| `models.py` | Plain dataclasses: `Observation`, `ActivitySpan`, `Category`, `Visibility` |
| `config.py` | TOML config, XDG/platform paths, validation |
| `runtime.py` | Shared state file (pause flag, watcher pid) — how `pause` reaches a running watcher |
| `observer/backends/` | Per-platform active-window reading, plus a `FakeBackend` for tests |
| `observer/sampler.py` | The polling loop and span collapsing |
| `privacy/policy.py` | Fidelity decisions, denylists, private-browsing detection |
| `privacy/redact.py` | Pattern-based scrubbing of titles |
| `privacy/vault.py` | Key management, AES-GCM sealing, keyed fingerprints |
| `storage/schema.py` | Two-tier schema: detailed spans vs. durable rollups |
| `storage/store.py` | All reads and writes, retention, purge, export, audit log |
| `categorize/` | Taxonomy, JSON rule pack, matching engine |
| `stats/` | Aggregation and text rendering |
| `demo.py` | Synthetic week, so the pipeline is demonstrable with no real data |
| `cli.py` | Commands |

## Key design decisions

**Spans, not events.** Twelve consecutive samples of the same window become one
row. This is a storage optimisation, but mostly it is a privacy one: a
5-second-resolution event stream reconstructs someone's day almost perfectly,
whereas a list of spans is the summary a person would give you themselves.

**Two storage tiers.** Detailed spans expire; daily rollups (no free text) are
kept forever. This is what lets retention be aggressive without destroying
long-term history.

**Titles sealed individually, not whole-database encryption.** Timestamps, app
names and categories must stay queryable for statistics to be fast. The titles
are the revealing part, so those are what get AES-GCM. Trade-off documented in
PRIVACY.md rather than hidden.

**Keyed fingerprints.** `HMAC(key, redacted_title)[:16]` lets stage 2 count
repetition — and will let stage 3 find patterns — with the statistics engine
never decrypting anything. Keyed rather than plain-hashed, so a stolen database
cannot be attacked with a dictionary of likely titles.

**Rules as data.** Classification lives in a JSON file the user can read and
diff, and every stored span keeps the `rule_id` that classified it, so "why did
it call this work?" is answerable by pointing at a line.

**User labels are permanent.** A correction via `copynion label` is written to
`category_overrides`, re-labels history, rebuilds the rollups, and outranks
every rule forever. Stage 3 will learn from these, so they must never be
silently overwritten by a rule-pack update.

**Fail closed, everywhere.** No encryption key means no stored titles. A broken
`cryptography` install means no stored titles. An unknown config key is an
error, not a silent ignore — because silently ignoring `redaction_enable` (note
the typo) would silently disable redaction.

**Everything must survive a bad day.** A backend that raises costs one sample,
not the session. A corrupt state file reads as "not paused, not running". A
three-day suspend does not become a three-day work session (`_max_extension`).

## Testing strategy

168 tests, no network, no display server required — the `FakeBackend` and
synthetic clocks mean the entire pipeline is testable headless, which is where
CI runs.

The one architectural test is `tests/test_no_network.py`: it parses every source
file's AST and fails the build if any networking module is imported anywhere.
That test is what converts "your data stays local" from a promise into a
property of the codebase.
