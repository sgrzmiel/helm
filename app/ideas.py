"""Pre-project idea capture.

Backed by SQLite (see app/db.py). Ideas live in a kanban with status:
exploring / parked / queued / promoted / dropped. They can be transitioned via
drag-drop on the frontend or promoted into a tracked Epic via the existing
Manage requirements flow.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Optional

from .db import _q, _qa, _qone
from .docs import refresh_documents
from .models import Document, Idea


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_idea(row, docs: list[dict]) -> Idea:
    try:
        segs = json.loads(row["segments_json"] or "[]")
    except (KeyError, IndexError, json.JSONDecodeError):
        segs = []
    return Idea(
        id=row["id"],
        title=row["title"],
        notes=row["notes"] or "",
        one_pager_url=row["one_pager_url"],
        stakeholder=row["stakeholder"],
        status=row["status"],
        position=row["position"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        promoted_epic_key=row["promoted_epic_key"],
        documents=[Document(**d) for d in docs],
        segments=segs,
    )


def _docs_for(idea_id: str) -> list[dict]:
    rows = _qa(
        "SELECT url, label, kind, cached_text, cached_at, fetch_error "
        "FROM idea_documents WHERE idea_id = ? ORDER BY position ASC",
        (idea_id,),
    )
    return [dict(r) for r in rows]


def _backfill_docs_from_legacy(idea_row) -> list[dict]:
    """If documents table has no rows but there's a legacy one_pager_url field,
    return a synthetic single-doc list so the frontend keeps working."""
    docs = _docs_for(idea_row["id"])
    if not docs and idea_row["one_pager_url"]:
        docs = [{
            "url": idea_row["one_pager_url"],
            "label": "one-pager",
            "kind": "other",
            "cached_text": None,
            "cached_at": None,
            "fetch_error": None,
        }]
    return docs


def list_ideas() -> list[Idea]:
    rows = _qa("SELECT * FROM ideas")
    out: list[Idea] = []
    for r in rows:
        out.append(_row_to_idea(r, _backfill_docs_from_legacy(r)))
    return out


def get(idea_id: str) -> Optional[Idea]:
    row = _qone("SELECT * FROM ideas WHERE id = ?", (idea_id,))
    if row is None:
        return None
    return _row_to_idea(row, _backfill_docs_from_legacy(row))


def _next_position(status: str) -> int:
    """Position for a new idea = top of the column (most negative existing - 1)
    so the newest idea floats to the top."""
    row = _qone(
        "SELECT MIN(position) AS m FROM ideas WHERE status = ?",
        (status,),
    )
    if row is None or row["m"] is None:
        return 0
    return row["m"] - 1


async def create(
    title: str,
    notes: str = "",
    one_pager_url: Optional[str] = None,
    stakeholder: Optional[str] = None,
    status: str = "exploring",
    documents: Optional[list[dict]] = None,
    segments: Optional[list[str]] = None,
) -> Idea:
    new_id = secrets.token_urlsafe(8)
    now = _now()
    _q(
        """INSERT INTO ideas
           (id, title, notes, one_pager_url, stakeholder, status, position, created_at, updated_at, promoted_epic_key, segments_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)""",
        (
            new_id,
            title.strip(),
            notes or "",
            (one_pager_url or "").strip() or None,
            (stakeholder or "").strip() or None,
            status,
            _next_position(status),
            now, now,
            json.dumps(list(segments or [])),
        ),
    )
    docs = await refresh_documents(documents or [], existing=[])
    _write_idea_documents(new_id, docs)
    return get(new_id)  # type: ignore[return-value]


def _write_idea_documents(idea_id: str, docs: list[dict]) -> None:
    _q("DELETE FROM idea_documents WHERE idea_id = ?", (idea_id,))
    for i, d in enumerate(docs):
        _q(
            """INSERT INTO idea_documents
               (idea_id, position, url, label, kind, cached_text, cached_at, fetch_error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                idea_id, i,
                d.get("url") or "",
                d.get("label"),
                d.get("kind") or "other",
                d.get("cached_text"),
                d.get("cached_at"),
                d.get("fetch_error"),
            ),
        )


async def update(idea_id: str, fields: dict) -> Optional[Idea]:
    existing = _qone("SELECT * FROM ideas WHERE id = ?", (idea_id,))
    if existing is None:
        return None

    # Scalar fields
    column_map = {
        "title": "title",
        "notes": "notes",
        "one_pager_url": "one_pager_url",
        "stakeholder": "stakeholder",
        "status": "status",
        "promoted_epic_key": "promoted_epic_key",
    }
    for k, col in column_map.items():
        if fields.get(k) is not None:
            _q(f"UPDATE ideas SET {col} = ? WHERE id = ?", (fields[k], idea_id))

    if fields.get("segments") is not None:
        _q(
            "UPDATE ideas SET segments_json = ? WHERE id = ?",
            (json.dumps(list(fields["segments"])), idea_id),
        )

    # Documents - refresh on change
    if "documents" in fields and fields["documents"] is not None:
        existing_docs = _docs_for(idea_id)
        new_docs = await refresh_documents(fields["documents"], existing=existing_docs)
        _write_idea_documents(idea_id, new_docs)

    _q("UPDATE ideas SET updated_at = ? WHERE id = ?", (_now(), idea_id))
    return get(idea_id)


def delete(idea_id: str) -> bool:
    row = _qone("SELECT id FROM ideas WHERE id = ?", (idea_id,))
    if row is None:
        return False
    _q("DELETE FROM idea_documents WHERE idea_id = ?", (idea_id,))
    _q("DELETE FROM ideas WHERE id = ?", (idea_id,))
    return True


def reorder(entries: list[dict]) -> list[Idea]:
    """Bulk-apply new (status, position) to a set of ideas. Frontend sends the
    full updated set after a drag operation; server overlays."""
    now = _now()
    for e in entries:
        _q(
            "UPDATE ideas SET status = ?, position = ?, updated_at = ? WHERE id = ?",
            (e["status"], e["position"], now, e["id"]),
        )
    return list_ideas()
