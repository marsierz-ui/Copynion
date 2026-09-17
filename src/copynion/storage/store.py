"""SQLite-backed local store.

Everything the user's machine remembers lives in one file they own, with 0600
permissions, in their own data directory. There is no sync, no telemetry and no
remote backend - not as a configuration choice but because the code to do it
does not exist.
"""

from __future__ import annotations

import json
import sqlite3
import time
import zlib
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta
from pathlib import Path

from copynion.inputs.events import InputBatch, InputCounters
from copynion.models import ActivitySpan, Category, Visibility
from copynion.privacy.vault import Vault
from copynion.storage.schema import MIGRATIONS, SCHEMA, SCHEMA_VERSION


def day_of(timestamp: float) -> str:
    """Local calendar day for a timestamp.

    Spans are attributed to the day they *started*; a span running past midnight
    is not split. At the default 5-second sampling interval the error is bounded
    by one idle timeout per night, which never matters for the statistics we
    produce.
    """
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")


class Store:
    """Owns the database connection and every write path into it."""

    def __init__(self, path: Path, vault: Vault | None = None) -> None:
        self.path = Path(path)
        self.vault = vault or Vault(None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not self.path.exists()
        self.conn = sqlite3.connect(str(self.path), isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        existing_version = self._existing_version()
        self.conn.executescript(SCHEMA)
        if new_file:
            # Owner-only, from the moment the file exists.
            self.path.chmod(0o600)
        if existing_version is not None and existing_version < SCHEMA_VERSION:
            self._migrate(existing_version)
        self._set_meta("schema_version", str(SCHEMA_VERSION))
        self._set_meta("created_at", self._get_meta("created_at") or str(time.time()))

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- meta / audit ---------------------------------------------------------

    def _existing_version(self) -> int | None:
        """Schema version already in the file, or None for a fresh database."""
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
        ).fetchone()
        if row is None:
            return None
        stored = self.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(stored["value"]) if stored else None

    def _migrate(self, from_version: int) -> None:
        """Bring an older database up to date, one version at a time."""
        for version in range(from_version + 1, SCHEMA_VERSION + 1):
            for statement in MIGRATIONS.get(version, []):
                try:
                    self.conn.execute(statement)
                except sqlite3.OperationalError as exc:
                    # A column the fresh-schema path already created is not an
                    # error; anything else is.
                    if "duplicate column name" not in str(exc):
                        raise
            self.audit("migrate", f"schema {version}")

    def _set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def _get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def audit(self, action: str, detail: str = "") -> None:
        """Append to the tamper-evident-ish log of privacy-relevant actions."""
        self.conn.execute(
            "INSERT INTO audit_log(at, action, detail) VALUES(?, ?, ?)",
            (time.time(), action, detail),
        )

    def audit_entries(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM audit_log ORDER BY at DESC LIMIT ?", (limit,)
        ).fetchall()

    # -- writing --------------------------------------------------------------

    def add_span(self, span: ActivitySpan, inputs: InputBatch | None = None) -> int:
        """Persist one span, its sealed title, its input batch and its rollups."""
        day = day_of(span.started_at)
        cat = span.category or Category("uncategorised")
        counters = inputs.counters if inputs else InputCounters()
        cur = self.conn.execute(
            """INSERT INTO spans(started_at, ended_at, duration, day, app, title_hash,
                                 category, subcategory, rule_id, confidence, afk,
                                 visibility, sample_count, backend,
                                 keystrokes, clicks, scrolls, mouse_distance)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                span.started_at, span.ended_at, span.duration, day, span.app,
                span.title_hash, cat.name, cat.subcategory, cat.rule_id, cat.confidence,
                int(span.afk), span.visibility.value, span.sample_count, span.backend,
                counters.keystrokes, counters.clicks, counters.scrolls,
                round(counters.mouse_distance, 1),
            ),
        )
        span_id = int(cur.lastrowid)
        span.id = span_id

        # Only FULL-visibility spans may carry a title, and only if the vault
        # will actually protect it.
        if span.title and span.visibility is Visibility.FULL:
            sealed = self.vault.seal(span.title)
            if sealed is not None:
                self.conn.execute(
                    "INSERT INTO span_titles(span_id, sealed) VALUES(?, ?)", (span_id, sealed)
                )

        if inputs is not None and inputs.events:
            self._store_inputs(span_id, inputs)

        self.conn.execute(
            """INSERT INTO daily_rollups(day, app, category, seconds, span_count, afk_seconds)
               VALUES(?,?,?,?,1,?)
               ON CONFLICT(day, app, category) DO UPDATE SET
                 seconds     = seconds + excluded.seconds,
                 span_count  = span_count + 1,
                 afk_seconds = afk_seconds + excluded.afk_seconds""",
            (day, span.app, cat.name, span.duration, span.duration if span.afk else 0.0),
        )
        return span_id

    def _store_inputs(self, span_id: int, batch: InputBatch) -> None:
        """Compress, seal and store one span's input events.

        One sealed blob per span rather than a row per keystroke: a million-row
        table of individual key events would be both slow and a far more
        inviting target.
        """
        payload = zlib.compress(json.dumps(batch.to_dict()).encode("utf-8"), 6)
        sealed = self.vault.seal_blob(payload)
        if sealed is None:
            # Fail closed, exactly as titles do: no key means nothing recorded.
            return
        self.conn.execute(
            "INSERT INTO span_inputs(span_id, sealed, event_count, fidelity) VALUES(?,?,?,?)",
            (span_id, sealed, len(batch.events), batch.fidelity),
        )

    def inputs_of(self, span_id: int) -> InputBatch | None:
        """Decrypt one span's input events. The only path that reveals them."""
        row = self.conn.execute(
            "SELECT sealed FROM span_inputs WHERE span_id=?", (span_id,)
        ).fetchone()
        if row is None:
            return None
        raw = self.vault.open_blob(row["sealed"])
        if raw is None:
            return None
        return InputBatch.from_dict(json.loads(zlib.decompress(raw).decode("utf-8")))

    def forget_inputs(self) -> int:
        """Drop every recorded keystroke and mouse event, keeping the counts."""
        cur = self.conn.execute("DELETE FROM span_inputs")
        n = cur.rowcount or 0
        self.conn.execute("VACUUM")
        self.audit("forget_inputs", f"{n} input batches deleted")
        return n

    def record_transition(self, from_app: str, to_app: str, at: float) -> None:
        if from_app == to_app:
            return
        self.conn.execute(
            """INSERT INTO daily_transitions(day, from_app, to_app, count) VALUES(?,?,?,1)
               ON CONFLICT(day, from_app, to_app) DO UPDATE SET count = count + 1""",
            (day_of(at), from_app, to_app),
        )

    # -- reading --------------------------------------------------------------

    def spans(
        self,
        since: float | None = None,
        until: float | None = None,
        *,
        app: str | None = None,
        category: str | None = None,
        include_afk: bool = True,
        limit: int | None = None,
    ) -> list[sqlite3.Row]:
        sql = "SELECT * FROM spans WHERE 1=1"
        params: list[object] = []
        if since is not None:
            sql += " AND ended_at >= ?"
            params.append(since)
        if until is not None:
            sql += " AND started_at <= ?"
            params.append(until)
        if app:
            sql += " AND app = ?"
            params.append(app)
        if category:
            sql += " AND category = ?"
            params.append(category)
        if not include_afk:
            sql += " AND afk = 0"
        sql += " ORDER BY started_at ASC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.conn.execute(sql, params).fetchall()

    def title_of(self, span_id: int) -> str | None:
        """Decrypt one stored title. The only path that ever reveals plaintext."""
        row = self.conn.execute(
            "SELECT sealed FROM span_titles WHERE span_id=?", (span_id,)
        ).fetchone()
        return self.vault.open(row["sealed"]) if row else None

    def rollups(self, since_day: str | None = None, until_day: str | None = None):
        sql = "SELECT * FROM daily_rollups WHERE 1=1"
        params: list[object] = []
        if since_day:
            sql += " AND day >= ?"
            params.append(since_day)
        if until_day:
            sql += " AND day <= ?"
            params.append(until_day)
        return self.conn.execute(sql + " ORDER BY day, seconds DESC", params).fetchall()

    def transitions(self, since_day: str | None = None):
        sql = "SELECT * FROM daily_transitions WHERE 1=1"
        params: list[object] = []
        if since_day:
            sql += " AND day >= ?"
            params.append(since_day)
        return self.conn.execute(sql + " ORDER BY count DESC", params).fetchall()

    def counts(self) -> dict[str, int]:
        q = lambda t: self.conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]  # noqa: E731
        return {
            "spans": q("spans"),
            "titles": q("span_titles"),
            "input_batches": q("span_inputs"),
            "rollup_days": self.conn.execute(
                "SELECT COUNT(DISTINCT day) c FROM daily_rollups"
            ).fetchone()["c"],
            "overrides": q("category_overrides"),
        }

    # -- category overrides ---------------------------------------------------

    def set_override(self, kind: str, value: str, category: str, sub: str | None = None) -> None:
        if kind not in ("app", "title_hash"):
            raise ValueError("override kind must be 'app' or 'title_hash'")
        self.conn.execute(
            """INSERT INTO category_overrides(match_kind, match_value, category, subcategory,
                                              created_at) VALUES(?,?,?,?,?)
               ON CONFLICT(match_kind, match_value) DO UPDATE SET
                 category=excluded.category, subcategory=excluded.subcategory""",
            (kind, value, category, sub, time.time()),
        )
        # Re-label history so statistics immediately reflect the correction.
        column = "app" if kind == "app" else "title_hash"
        self.conn.execute(
            f"UPDATE spans SET category=?, subcategory=?, rule_id='user' WHERE {column}=?",
            (category, sub, value),
        )
        self._rebuild_rollups()
        self.audit("category_override", f"{kind}={value} -> {category}")

    def overrides(self) -> dict[tuple[str, str], Category]:
        rows = self.conn.execute("SELECT * FROM category_overrides").fetchall()
        return {
            (r["match_kind"], r["match_value"]): Category(
                r["category"], r["subcategory"], rule_id="user", confidence=1.0, source="user"
            )
            for r in rows
        }

    def _rebuild_rollups(self) -> None:
        """Recompute tier 2 from tier 1 for days the detailed data still covers."""
        oldest = self.conn.execute("SELECT MIN(day) d FROM spans").fetchone()["d"]
        if oldest is None:
            return
        self.conn.execute("DELETE FROM daily_rollups WHERE day >= ?", (oldest,))
        self.conn.execute(
            """INSERT INTO daily_rollups(day, app, category, seconds, span_count, afk_seconds)
               SELECT day, app, category, SUM(duration), COUNT(*),
                      SUM(CASE WHEN afk THEN duration ELSE 0 END)
               FROM spans GROUP BY day, app, category"""
        )

    # -- retention, deletion, export -----------------------------------------

    def enforce_retention(
        self, detail_days: int, title_days: int | None = None, input_days: int | None = None
    ) -> dict[str, int]:
        """Delete detailed rows past their retention window.

        Rollups are untouched: the user keeps their long-term statistics while
        the revealing per-window records expire on schedule.
        """
        removed = {"spans": 0, "titles": 0, "inputs": 0}
        now = time.time()
        if input_days is not None and input_days >= 0:
            # Input detail expires first: it is the most revealing thing stored.
            cutoff = now - input_days * 86400
            cur = self.conn.execute(
                "DELETE FROM span_inputs WHERE span_id IN "
                "(SELECT id FROM spans WHERE ended_at < ?)",
                (cutoff,),
            )
            removed["inputs"] = cur.rowcount or 0
        if title_days is not None and title_days >= 0:
            cutoff = now - title_days * 86400
            cur = self.conn.execute(
                "DELETE FROM span_titles WHERE span_id IN "
                "(SELECT id FROM spans WHERE ended_at < ?)",
                (cutoff,),
            )
            removed["titles"] = cur.rowcount or 0
        if detail_days >= 0:
            cutoff = now - detail_days * 86400
            cur = self.conn.execute("DELETE FROM spans WHERE ended_at < ?", (cutoff,))
            removed["spans"] = cur.rowcount or 0
        if any(removed.values()):
            self.audit("retention", json.dumps(removed))
            self.conn.execute("VACUUM")
        return removed

    def forget_titles(self) -> int:
        """Drop every stored title, keeping spans and statistics intact."""
        cur = self.conn.execute("DELETE FROM span_titles")
        n = cur.rowcount or 0
        self.conn.execute("VACUUM")
        self.audit("forget_titles", f"{n} titles deleted")
        return n

    def purge(
        self,
        *,
        before: float | None = None,
        after: float | None = None,
        app: str | None = None,
        everything: bool = False,
    ) -> dict[str, int]:
        """Delete data on the user's command. The 'own your data' escape hatch."""
        if everything:
            counts = self.counts()
            for table in ("span_inputs", "span_titles", "spans", "daily_rollups",
                          "daily_transitions"):
                self.conn.execute(f"DELETE FROM {table}")
            self.conn.execute("VACUUM")
            self.audit("purge_all", json.dumps(counts))
            return counts

        conditions, params = [], []
        if before is not None:
            conditions.append("ended_at < ?")
            params.append(before)
        if after is not None:
            conditions.append("started_at > ?")
            params.append(after)
        if app:
            conditions.append("app = ?")
            params.append(app)
        if not conditions:
            raise ValueError("purge needs a filter, or everything=True")
        where = " AND ".join(conditions)

        affected_days = [
            r["day"] for r in self.conn.execute(f"SELECT DISTINCT day FROM spans WHERE {where}", params)
        ]
        cur = self.conn.execute(f"DELETE FROM spans WHERE {where}", params)
        removed = cur.rowcount or 0
        for day in affected_days:
            self.conn.execute("DELETE FROM daily_rollups WHERE day = ?", (day,))
        self._rebuild_rollups()
        self.conn.execute("VACUUM")
        self.audit("purge", f"{removed} spans (app={app}, before={before}, after={after})")
        return {"spans": removed}

    def export(self, include_titles: bool = False, since: float | None = None) -> Iterator[dict]:
        """Yield every record as plain dicts, for a full user-owned export.

        Titles are decrypted only when explicitly requested, so the common case
        (moving your statistics to another tool) never produces a file full of
        sensitive strings.
        """
        for row in self.spans(since=since):
            record = dict(row)
            if include_titles:
                record["title"] = self.title_of(row["id"])
            yield record

    def add_spans(self, spans: Iterable[ActivitySpan]) -> int:
        n = 0
        for span in spans:
            self.add_span(span)
            n += 1
        return n

    # -- convenience ----------------------------------------------------------

    @staticmethod
    def day_range(days: int, end: datetime | None = None) -> tuple[str, str]:
        end = end or datetime.now()
        start = end - timedelta(days=days - 1)
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
