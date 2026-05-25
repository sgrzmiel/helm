"""SQLite storage for Helm.

Replaces the previous mix of JSON files and JSONL logs with a single file
(`helm.db`) at the project root. On first import, if the DB doesn't exist:
  1. Create schema.
  2. Import data from any legacy JSON / JSONL files.
  3. Rename those files to `*.imported` so they don't get re-imported.

The connection is a single shared `sqlite3.Connection` opened in WAL mode for
the lifetime of the process. SQLite handles single-writer concurrency fine for
our workload (one local FastAPI app, no fan-out).

All higher-level modules (tracked, team, ideas, overrides, etc.) should depend
on this module and never touch the JSON files directly.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(__file__).parent.parent / "helm.db"
ROOT = DB_PATH.parent

_conn: Optional[sqlite3.Connection] = None


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        first_run = not DB_PATH.exists()
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode = WAL")
        _conn.execute("PRAGMA foreign_keys = ON")
        _ensure_schema(_conn)
        if first_run:
            _import_legacy_json(_conn)
    return _conn


def _q(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    return conn().execute(sql, params)


def _qa(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return list(_q(sql, params).fetchall())


def _qone(sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    return _q(sql, params).fetchone()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tracked_epics (
    key         TEXT PRIMARY KEY,
    position    INTEGER NOT NULL DEFAULT 0,
    added_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tracked_position ON tracked_epics(position);

CREATE TABLE IF NOT EXISTS team_members (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    email       TEXT,
    role        TEXT NOT NULL DEFAULT 'other',
    position    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_team_position ON team_members(position);

CREATE TABLE IF NOT EXISTS ideas (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    notes               TEXT NOT NULL DEFAULT '',
    one_pager_url       TEXT,
    stakeholder         TEXT,
    status              TEXT NOT NULL DEFAULT 'exploring',
    position            INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    promoted_epic_key   TEXT
);
CREATE INDEX IF NOT EXISTS idx_ideas_status_position ON ideas(status, position);

CREATE TABLE IF NOT EXISTS idea_documents (
    idea_id      TEXT NOT NULL,
    position     INTEGER NOT NULL,
    url          TEXT NOT NULL,
    label        TEXT,
    kind         TEXT NOT NULL DEFAULT 'other',
    cached_text  TEXT,
    cached_at    TEXT,
    fetch_error  TEXT,
    PRIMARY KEY (idea_id, position),
    FOREIGN KEY (idea_id) REFERENCES ideas(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS epic_metadata (
    epic_key       TEXT PRIMARY KEY,
    one_pager_url  TEXT,
    stakeholder    TEXT,
    idea_id        TEXT,
    segments_json  TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS epic_metadata_documents (
    epic_key     TEXT NOT NULL,
    position     INTEGER NOT NULL,
    url          TEXT NOT NULL,
    label        TEXT,
    kind         TEXT NOT NULL DEFAULT 'other',
    cached_text  TEXT,
    cached_at    TEXT,
    fetch_error  TEXT,
    PRIMARY KEY (epic_key, position)
);

CREATE TABLE IF NOT EXISTS epic_actions_added (
    epic_key          TEXT NOT NULL,
    position          INTEGER NOT NULL,
    title             TEXT NOT NULL,
    detail            TEXT NOT NULL DEFAULT '',
    urgency           TEXT NOT NULL DEFAULT 'medium',
    for_user          INTEGER NOT NULL DEFAULT 0,
    source            TEXT NOT NULL DEFAULT 'manual',
    ticket_keys_json  TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (epic_key, position)
);

CREATE TABLE IF NOT EXISTS epic_risks_dismissed (
    epic_key  TEXT NOT NULL,
    sig       TEXT NOT NULL,
    PRIMARY KEY (epic_key, sig)
);

CREATE TABLE IF NOT EXISTS epic_gaps_dismissed (
    epic_key  TEXT NOT NULL,
    sig       TEXT NOT NULL,
    PRIMARY KEY (epic_key, sig)
);

CREATE TABLE IF NOT EXISTS epic_actions_done (
    epic_key  TEXT NOT NULL,
    sig       TEXT NOT NULL,
    PRIMARY KEY (epic_key, sig)
);

CREATE TABLE IF NOT EXISTS epic_action_for_user (
    epic_key  TEXT NOT NULL,
    sig       TEXT NOT NULL,
    for_user  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (epic_key, sig)
);

CREATE TABLE IF NOT EXISTS analysis_cache (
    epic_key       TEXT PRIMARY KEY,
    analysis_json  TEXT NOT NULL,
    analyzed_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS detail_cache (
    epic_key     TEXT PRIMARY KEY,
    detail_json  TEXT NOT NULL,
    cached_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    payload    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edit_log_ts ON edit_log(ts);

CREATE TABLE IF NOT EXISTS closure_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    epic_key   TEXT NOT NULL,
    type       TEXT NOT NULL,
    action     TEXT NOT NULL,
    sig        TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT '',
    reason     TEXT
);
CREATE INDEX IF NOT EXISTS idx_closure_log_epic ON closure_log(epic_key, ts);

CREATE TABLE IF NOT EXISTS app_config (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL DEFAULT ''
);
"""


# Defaults for app_config. These are the company-specific values that
# previously lived hardcoded in the prompts. On first install they seed the
# table; subsequent installs leave existing rows alone (INSERT OR IGNORE).
_CONFIG_DEFAULTS = {
    "company_name": "Your company",
    "fe_project_key": "FE",
    "be_project_key": "BE",
    "required_label": "",
    "business_context": "",
}


def _seed_app_config(c: sqlite3.Connection) -> None:
    for k, v in _CONFIG_DEFAULTS.items():
        c.execute("INSERT OR IGNORE INTO app_config(key, value) VALUES (?, ?)", (k, v))


# Public helpers for app_config
def get_config(key: str, default: str = "") -> str:
    row = _qone("SELECT value FROM app_config WHERE key = ?", (key,))
    if row is None:
        return default
    return row["value"]


def get_all_config() -> dict[str, str]:
    rows = _qa("SELECT key, value FROM app_config")
    return {r["key"]: r["value"] for r in rows}


def set_config(updates: dict[str, str]) -> dict[str, str]:
    """Upsert a batch of config values. Empty string values are stored (meaning
    'explicitly cleared'). None values are skipped."""
    for k, v in updates.items():
        if v is None:
            continue
        _q(
            """INSERT INTO app_config(key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (k, str(v)),
        )
    return get_all_config()


def _ensure_schema(c: sqlite3.Connection) -> None:
    c.executescript(_SCHEMA)
    _seed_app_config(c)


# ---------------------------------------------------------------------------
# One-time legacy import (JSON files -> SQLite)
# ---------------------------------------------------------------------------


def _safe_load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _import_legacy_json(c: sqlite3.Connection) -> None:
    """Best-effort migration of the legacy JSON / JSONL files.

    Anything that imports successfully gets the file renamed to .imported so
    we don't re-run the import on next start. Anything that fails leaves the
    file alone and is logged - the user can investigate without losing data.
    """
    imported: list[str] = []
    skipped: list[tuple[str, str]] = []  # (file, reason)

    # tracked_epics.json -> tracked_epics table (list order = position)
    p = ROOT / "tracked_epics.json"
    data = _safe_load_json(p)
    if data is not None:
        try:
            for i, e in enumerate(data.get("epics", []) or []):
                c.execute(
                    "INSERT OR IGNORE INTO tracked_epics(key, position, added_at) VALUES (?, ?, ?)",
                    (e["key"], i, e.get("added_at") or ""),
                )
            p.rename(p.with_suffix(".json.imported"))
            imported.append(p.name)
        except Exception as ex:
            skipped.append((p.name, str(ex)))

    # team_members.json
    p = ROOT / "team_members.json"
    data = _safe_load_json(p)
    if data is not None:
        try:
            for i, m in enumerate(data.get("members", []) or []):
                c.execute(
                    "INSERT INTO team_members(name, email, role, position) VALUES (?, ?, ?, ?)",
                    (m.get("name") or "", m.get("email"), m.get("role") or "other", i),
                )
            p.rename(p.with_suffix(".json.imported"))
            imported.append(p.name)
        except Exception as ex:
            skipped.append((p.name, str(ex)))

    # ideas.json + nested documents
    p = ROOT / "ideas.json"
    data = _safe_load_json(p)
    if data is not None:
        try:
            for idea in data.get("ideas", []) or []:
                c.execute(
                    """INSERT OR IGNORE INTO ideas
                       (id, title, notes, one_pager_url, stakeholder, status, position, created_at, updated_at, promoted_epic_key)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        idea["id"],
                        idea.get("title") or "",
                        idea.get("notes") or "",
                        idea.get("one_pager_url"),
                        idea.get("stakeholder"),
                        idea.get("status") or "exploring",
                        idea.get("position", 0),
                        idea.get("created_at") or "",
                        idea.get("updated_at") or "",
                        idea.get("promoted_epic_key"),
                    ),
                )
                for di, doc in enumerate(idea.get("documents", []) or []):
                    c.execute(
                        """INSERT OR REPLACE INTO idea_documents
                           (idea_id, position, url, label, kind, cached_text, cached_at, fetch_error)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            idea["id"], di,
                            doc.get("url") or "",
                            doc.get("label"),
                            doc.get("kind") or "other",
                            doc.get("cached_text"),
                            doc.get("cached_at"),
                            doc.get("fetch_error"),
                        ),
                    )
            p.rename(p.with_suffix(".json.imported"))
            imported.append(p.name)
        except Exception as ex:
            skipped.append((p.name, str(ex)))

    # epic_overrides.json -> multiple tables
    p = ROOT / "epic_overrides.json"
    data = _safe_load_json(p)
    if data is not None:
        try:
            for ek, bucket in (data.get("epics") or {}).items():
                # Actions added
                for i, a in enumerate(bucket.get("actions_added", []) or []):
                    c.execute(
                        """INSERT OR REPLACE INTO epic_actions_added
                           (epic_key, position, title, detail, urgency, for_user, source, ticket_keys_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            ek, i,
                            a.get("title") or "",
                            a.get("detail") or "",
                            a.get("urgency") or "medium",
                            1 if a.get("for_user") else 0,
                            a.get("source") or "manual",
                            json.dumps(a.get("ticket_keys") or []),
                        ),
                    )
                # Sets
                for sig in bucket.get("risks_dismissed", []) or []:
                    c.execute(
                        "INSERT OR IGNORE INTO epic_risks_dismissed(epic_key, sig) VALUES (?, ?)",
                        (ek, sig),
                    )
                for sig in bucket.get("gaps_dismissed", []) or []:
                    c.execute(
                        "INSERT OR IGNORE INTO epic_gaps_dismissed(epic_key, sig) VALUES (?, ?)",
                        (ek, sig),
                    )
                for sig in bucket.get("actions_done", []) or []:
                    c.execute(
                        "INSERT OR IGNORE INTO epic_actions_done(epic_key, sig) VALUES (?, ?)",
                        (ek, sig),
                    )
                # Metadata
                meta = bucket.get("metadata") or {}
                if meta:
                    c.execute(
                        """INSERT OR REPLACE INTO epic_metadata
                           (epic_key, one_pager_url, stakeholder, idea_id, segments_json)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            ek,
                            meta.get("one_pager_url"),
                            meta.get("stakeholder"),
                            meta.get("idea_id"),
                            json.dumps(meta.get("segments") or []),
                        ),
                    )
                    for di, doc in enumerate(meta.get("documents") or []):
                        c.execute(
                            """INSERT OR REPLACE INTO epic_metadata_documents
                               (epic_key, position, url, label, kind, cached_text, cached_at, fetch_error)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                ek, di,
                                doc.get("url") or "",
                                doc.get("label"),
                                doc.get("kind") or "other",
                                doc.get("cached_text"),
                                doc.get("cached_at"),
                                doc.get("fetch_error"),
                            ),
                        )
            p.rename(p.with_suffix(".json.imported"))
            imported.append(p.name)
        except Exception as ex:
            skipped.append((p.name, str(ex)))

    # analysis_cache.json
    p = ROOT / "analysis_cache.json"
    data = _safe_load_json(p)
    if data is not None:
        try:
            for ek, payload in (data or {}).items():
                c.execute(
                    "INSERT OR REPLACE INTO analysis_cache(epic_key, analysis_json, analyzed_at) VALUES (?, ?, ?)",
                    (ek, json.dumps(payload.get("analysis") or {}), payload.get("analyzed_at") or ""),
                )
            p.rename(p.with_suffix(".json.imported"))
            imported.append(p.name)
        except Exception as ex:
            skipped.append((p.name, str(ex)))

    # edit_log.jsonl + closure_log.jsonl (line-by-line)
    for fname, table in (
        ("edit_log.jsonl", "edit_log"),
        ("closure_log.jsonl", "closure_log"),
    ):
        p = ROOT / fname
        if not p.exists():
            continue
        try:
            for line in p.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if table == "edit_log":
                    c.execute(
                        "INSERT INTO edit_log(ts, payload) VALUES (?, ?)",
                        (entry.get("ts") or "", json.dumps(entry)),
                    )
                else:
                    c.execute(
                        """INSERT INTO closure_log(ts, epic_key, type, action, sig, title, detail, reason)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            entry.get("ts") or "",
                            entry.get("epic_key") or "",
                            entry.get("type") or "",
                            entry.get("action") or "",
                            entry.get("sig") or "",
                            entry.get("title") or "",
                            entry.get("detail") or "",
                            entry.get("reason"),
                        ),
                    )
            p.rename(p.with_suffix(".jsonl.imported"))
            imported.append(p.name)
        except Exception as ex:
            skipped.append((p.name, str(ex)))

    if imported:
        print(f"[helm.db] imported legacy files: {', '.join(imported)}")
    if skipped:
        for name, reason in skipped:
            print(f"[helm.db] skipped {name}: {reason}")
