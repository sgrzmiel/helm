from __future__ import annotations

from datetime import datetime, timezone

from .db import _q, _qa


def list_keys() -> list[str]:
    rows = _qa("SELECT key FROM tracked_epics ORDER BY position ASC")
    return [r["key"] for r in rows]


def add(key: str) -> list[str]:
    existing = _qa("SELECT key FROM tracked_epics WHERE key = ?", (key,))
    if not existing:
        # Append at end - find max position + 1
        row = _q("SELECT COALESCE(MAX(position), -1) + 1 AS pos FROM tracked_epics").fetchone()
        next_pos = row["pos"] if row else 0
        _q(
            "INSERT INTO tracked_epics(key, position, added_at) VALUES (?, ?, ?)",
            (key, next_pos, datetime.now(timezone.utc).isoformat()),
        )
    return list_keys()


def remove(key: str) -> list[str]:
    _q("DELETE FROM tracked_epics WHERE key = ?", (key,))
    return list_keys()


def reorder(new_order: list[str]) -> list[str]:
    """Reorder the stored epic list so keys appear in `new_order`. Keys in the
    store but missing from `new_order` are appended at the end (preserving their
    relative original order); unknown keys in `new_order` are ignored."""
    existing = {r["key"]: r for r in _qa("SELECT key, position, added_at FROM tracked_epics")}
    pos = 0
    seen: set[str] = set()
    for k in new_order:
        if k in existing and k not in seen:
            _q("UPDATE tracked_epics SET position = ? WHERE key = ?", (pos, k))
            seen.add(k)
            pos += 1
    # Append unmoved keys in their original order
    for k in sorted((k for k in existing if k not in seen), key=lambda x: existing[x]["position"]):
        _q("UPDATE tracked_epics SET position = ? WHERE key = ?", (pos, k))
        pos += 1
    return list_keys()
