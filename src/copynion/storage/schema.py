"""Database schema.

Two tiers of data live here, and the distinction drives the retention policy:

``spans``
    The detailed record: one row per contiguous stretch on one window. Titles
    are stored only as ciphertext plus a keyed fingerprint. This tier is
    sensitive and expires (default 90 days).

``daily_rollups`` / ``daily_transitions``
    Pre-aggregated counters with no free text at all. These are what long-term
    trends are built from, so the detailed tier can be purged aggressively
    without losing the user's history.

``span_titles`` and ``span_inputs`` are separate tables from ``spans`` so that
"forget all titles but keep my statistics", or "forget everything I typed", is a
single DELETE rather than a schema migration.

Input *counters* live on ``spans`` as plaintext columns because they contain no
content at all - a number of keystrokes is not a keystroke. The events
themselves live sealed in ``span_inputs`` and expire far sooner.
"""

SCHEMA_VERSION = 2

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Tier 1: detailed, sensitive, expires.
CREATE TABLE IF NOT EXISTS spans (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   REAL    NOT NULL,
    ended_at     REAL    NOT NULL,
    duration     REAL    NOT NULL,
    day          TEXT    NOT NULL,              -- local YYYY-MM-DD, for cheap grouping
    app          TEXT    NOT NULL DEFAULT '',
    title_hash   TEXT,                          -- keyed HMAC, never reversible
    category     TEXT    NOT NULL DEFAULT 'uncategorised',
    subcategory  TEXT,
    rule_id      TEXT,
    confidence   REAL    NOT NULL DEFAULT 0.0,
    afk          INTEGER NOT NULL DEFAULT 0,
    visibility   TEXT    NOT NULL DEFAULT 'app_only',
    sample_count INTEGER NOT NULL DEFAULT 1,
    backend      TEXT    NOT NULL DEFAULT 'unknown',
    -- Content-free input aggregates, safe to keep in plaintext.
    keystrokes     INTEGER NOT NULL DEFAULT 0,
    clicks         INTEGER NOT NULL DEFAULT 0,
    scrolls        INTEGER NOT NULL DEFAULT 0,
    mouse_distance REAL    NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_spans_day       ON spans(day);
CREATE INDEX IF NOT EXISTS idx_spans_started   ON spans(started_at);
CREATE INDEX IF NOT EXISTS idx_spans_app       ON spans(app);
CREATE INDEX IF NOT EXISTS idx_spans_category  ON spans(category);
CREATE INDEX IF NOT EXISTS idx_spans_hash      ON spans(title_hash);

-- Sealed titles, deliberately separable from the rest of the record.
CREATE TABLE IF NOT EXISTS span_titles (
    span_id INTEGER PRIMARY KEY REFERENCES spans(id) ON DELETE CASCADE,
    sealed  BLOB NOT NULL
);

-- Recorded keystrokes and mouse events, compressed then sealed. This is the
-- most sensitive table in the database and has the shortest retention.
CREATE TABLE IF NOT EXISTS span_inputs (
    span_id     INTEGER PRIMARY KEY REFERENCES spans(id) ON DELETE CASCADE,
    sealed      BLOB    NOT NULL,
    event_count INTEGER NOT NULL DEFAULT 0,
    fidelity    TEXT    NOT NULL DEFAULT 'off'
);

-- Tier 2: aggregated, non-sensitive, kept indefinitely.
CREATE TABLE IF NOT EXISTS daily_rollups (
    day         TEXT NOT NULL,
    app         TEXT NOT NULL,
    category    TEXT NOT NULL,
    seconds     REAL NOT NULL DEFAULT 0.0,
    span_count  INTEGER NOT NULL DEFAULT 0,
    afk_seconds REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY (day, app, category)
);

CREATE TABLE IF NOT EXISTS daily_transitions (
    day       TEXT NOT NULL,
    from_app  TEXT NOT NULL,
    to_app    TEXT NOT NULL,
    count     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, from_app, to_app)
);

-- User corrections to categorisation. These outrank every rule and survive
-- rule-file changes, because a person's own labelling of their work is the
-- ground truth stage 3 will learn from.
CREATE TABLE IF NOT EXISTS category_overrides (
    match_kind  TEXT NOT NULL,                 -- 'app' or 'title_hash'
    match_value TEXT NOT NULL,
    category    TEXT NOT NULL,
    subcategory TEXT,
    created_at  REAL NOT NULL,
    PRIMARY KEY (match_kind, match_value)
);

-- Append-only audit of privacy-relevant actions, so the user can always answer
-- "what did this thing do, and when did it stop watching me?".
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        REAL NOT NULL,
    action    TEXT NOT NULL,
    detail    TEXT NOT NULL DEFAULT ''
);
"""


#: Incremental upgrades for databases created by an older version. Applied in
#: order for anything below SCHEMA_VERSION; a freshly created database gets the
#: full SCHEMA above and skips these entirely.
MIGRATIONS: dict[int, list[str]] = {
    2: [
        "ALTER TABLE spans ADD COLUMN keystrokes INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE spans ADD COLUMN clicks INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE spans ADD COLUMN scrolls INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE spans ADD COLUMN mouse_distance REAL NOT NULL DEFAULT 0.0",
        """CREATE TABLE IF NOT EXISTS span_inputs (
               span_id     INTEGER PRIMARY KEY REFERENCES spans(id) ON DELETE CASCADE,
               sealed      BLOB    NOT NULL,
               event_count INTEGER NOT NULL DEFAULT 0,
               fidelity    TEXT    NOT NULL DEFAULT 'off'
           )""",
    ],
}
