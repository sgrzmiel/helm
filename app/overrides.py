"""Per-epic overrides on top of AI analysis.

Backed by SQLite. Stores:
- manual `actions_added`
- dismissed `risks_dismissed`, `gaps_dismissed`
- done `actions_done`
- `metadata` (one_pager_url, stakeholder, idea_id, segments, documents)

The signature helpers stay deterministic so dismissals survive re-analyses
even though the LLM may regenerate items with slightly different wording.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from .db import _q, _qa, _qone
from .models import ActionItem


# ---------------------------------------------------------------------------
# Stable signatures
# ---------------------------------------------------------------------------


def risk_signature(title: str, detail: str) -> str:
    raw = f"{(title or '').strip().lower()}|{(detail or '').strip().lower()[:200]}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def action_signature(title: str, detail: str) -> str:
    raw = f"{(title or '').strip().lower()}|{(detail or '').strip().lower()[:200]}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Manual actions
# ---------------------------------------------------------------------------


def get_actions(key: str) -> list[ActionItem]:
    rows = _qa(
        """SELECT title, detail, urgency, for_user, source, ticket_keys_json
           FROM epic_actions_added WHERE epic_key = ? ORDER BY position ASC""",
        (key,),
    )
    return [
        ActionItem(
            title=r["title"],
            detail=r["detail"],
            urgency=r["urgency"],
            for_user=bool(r["for_user"]),
            source=r["source"],
            ticket_keys=json.loads(r["ticket_keys_json"] or "[]"),
        )
        for r in rows
    ]


def add_action(key: str, action: ActionItem) -> list[ActionItem]:
    row = _qone(
        "SELECT COALESCE(MAX(position), -1) + 1 AS pos FROM epic_actions_added WHERE epic_key = ?",
        (key,),
    )
    pos = row["pos"] if row else 0
    _q(
        """INSERT INTO epic_actions_added
           (epic_key, position, title, detail, urgency, for_user, source, ticket_keys_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            key, pos,
            action.title,
            action.detail,
            action.urgency,
            1 if action.for_user else 0,
            action.source,
            json.dumps(action.ticket_keys or []),
        ),
    )
    return get_actions(key)


def replace_actions(key: str, actions: list[ActionItem]) -> list[ActionItem]:
    _q("DELETE FROM epic_actions_added WHERE epic_key = ?", (key,))
    for i, a in enumerate(actions):
        _q(
            """INSERT INTO epic_actions_added
               (epic_key, position, title, detail, urgency, for_user, source, ticket_keys_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                key, i,
                a.title, a.detail, a.urgency,
                1 if a.for_user else 0,
                a.source,
                json.dumps(a.ticket_keys or []),
            ),
        )
    return get_actions(key)


def remove_action(key: str, index: int) -> list[ActionItem]:
    # Read in order, drop the target, renumber
    rows = _qa(
        "SELECT position FROM epic_actions_added WHERE epic_key = ? ORDER BY position ASC",
        (key,),
    )
    positions = [r["position"] for r in rows]
    if 0 <= index < len(positions):
        target_pos = positions[index]
        _q(
            "DELETE FROM epic_actions_added WHERE epic_key = ? AND position = ?",
            (key, target_pos),
        )
        # Renumber to keep dense 0..N-1 ordering
        remaining = _qa(
            "SELECT position FROM epic_actions_added WHERE epic_key = ? ORDER BY position ASC",
            (key,),
        )
        for new_pos, r in enumerate(remaining):
            if r["position"] != new_pos:
                _q(
                    "UPDATE epic_actions_added SET position = ? WHERE epic_key = ? AND position = ?",
                    (new_pos, key, r["position"]),
                )
    return get_actions(key)


# ---------------------------------------------------------------------------
# Dismissed sets
# ---------------------------------------------------------------------------


def get_dismissed_risks(key: str) -> set[str]:
    rows = _qa("SELECT sig FROM epic_risks_dismissed WHERE epic_key = ?", (key,))
    return {r["sig"] for r in rows}


def dismiss_risk(key: str, sig: str) -> set[str]:
    _q("INSERT OR IGNORE INTO epic_risks_dismissed(epic_key, sig) VALUES (?, ?)", (key, sig))
    return get_dismissed_risks(key)


def restore_risk(key: str, sig: str) -> set[str]:
    _q("DELETE FROM epic_risks_dismissed WHERE epic_key = ? AND sig = ?", (key, sig))
    return get_dismissed_risks(key)


def get_dismissed_gaps(key: str) -> set[str]:
    rows = _qa("SELECT sig FROM epic_gaps_dismissed WHERE epic_key = ?", (key,))
    return {r["sig"] for r in rows}


def dismiss_gap(key: str, sig: str) -> set[str]:
    _q("INSERT OR IGNORE INTO epic_gaps_dismissed(epic_key, sig) VALUES (?, ?)", (key, sig))
    return get_dismissed_gaps(key)


def restore_gap(key: str, sig: str) -> set[str]:
    _q("DELETE FROM epic_gaps_dismissed WHERE epic_key = ? AND sig = ?", (key, sig))
    return get_dismissed_gaps(key)


def get_done_actions(key: str) -> set[str]:
    rows = _qa("SELECT sig FROM epic_actions_done WHERE epic_key = ?", (key,))
    return {r["sig"] for r in rows}


def mark_action_done(key: str, sig: str) -> set[str]:
    _q("INSERT OR IGNORE INTO epic_actions_done(epic_key, sig) VALUES (?, ?)", (key, sig))
    return get_done_actions(key)


def unmark_action_done(key: str, sig: str) -> set[str]:
    _q("DELETE FROM epic_actions_done WHERE epic_key = ? AND sig = ?", (key, sig))
    return get_done_actions(key)


# ---------------------------------------------------------------------------
# Metadata (one-pager, stakeholder, segments, idea link, documents)
# ---------------------------------------------------------------------------


def _row_to_meta_dict(row) -> dict[str, Any]:
    docs = _qa(
        """SELECT url, label, kind, cached_text, cached_at, fetch_error
           FROM epic_metadata_documents WHERE epic_key = ? ORDER BY position ASC""",
        (row["epic_key"],),
    )
    return {
        "one_pager_url": row["one_pager_url"],
        "stakeholder": row["stakeholder"],
        "idea_id": row["idea_id"],
        "segments": json.loads(row["segments_json"] or "[]"),
        "documents": [dict(d) for d in docs],
    }


def _backfill_metadata_docs(meta: dict[str, Any]) -> dict[str, Any]:
    """Lift legacy one_pager_url into documents[] when documents is empty."""
    docs = meta.get("documents") or []
    if not docs and meta.get("one_pager_url"):
        meta = dict(meta)
        meta["documents"] = [{
            "url": meta["one_pager_url"],
            "label": "one-pager",
            "kind": "other",
            "cached_text": None,
            "cached_at": None,
            "fetch_error": None,
        }]
    return meta


def get_metadata(key: str) -> dict[str, Any]:
    row = _qone(
        """SELECT epic_key, one_pager_url, stakeholder, idea_id, segments_json
           FROM epic_metadata WHERE epic_key = ?""",
        (key,),
    )
    if row is None:
        return {}
    return _backfill_metadata_docs(_row_to_meta_dict(row))


def _ensure_metadata_row(key: str) -> None:
    _q(
        """INSERT OR IGNORE INTO epic_metadata(epic_key, one_pager_url, stakeholder, idea_id, segments_json)
           VALUES (?, NULL, NULL, NULL, '[]')""",
        (key,),
    )


def set_metadata(key: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Overlay non-None values onto the stored metadata. Set a field to empty
    string explicitly to clear it; pass None to leave it alone.

    `documents` is NOT handled here - use set_metadata_documents() since it
    needs async fetches in the caller.
    """
    _ensure_metadata_row(key)
    for k, v in fields.items():
        if v is None:
            continue
        if k == "segments":
            _q("UPDATE epic_metadata SET segments_json = ? WHERE epic_key = ?",
               (json.dumps(list(v)), key))
            continue
        if k not in ("one_pager_url", "stakeholder", "idea_id"):
            continue
        if v == "":
            _q(f"UPDATE epic_metadata SET {k} = NULL WHERE epic_key = ?", (key,))
        else:
            _q(f"UPDATE epic_metadata SET {k} = ? WHERE epic_key = ?", (v, key))
    return get_metadata(key)


def set_metadata_documents(key: str, documents: list[dict]) -> dict[str, Any]:
    _ensure_metadata_row(key)
    _q("DELETE FROM epic_metadata_documents WHERE epic_key = ?", (key,))
    for i, d in enumerate(documents):
        _q(
            """INSERT INTO epic_metadata_documents
               (epic_key, position, url, label, kind, cached_text, cached_at, fetch_error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                key, i,
                d.get("url") or "",
                d.get("label"),
                d.get("kind") or "other",
                d.get("cached_text"),
                d.get("cached_at"),
                d.get("fetch_error"),
            ),
        )
    return get_metadata(key)
