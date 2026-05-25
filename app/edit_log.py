"""Log user edits to AI-generated proposals.

When the user clicks Apply, the frontend sends both the original proposal
(from /api/plan) and the final edited version. We diff them and append a JSONL
entry to edit_log.jsonl so the prompt can be refined over time.

The log file lives at the project root, not inside `app/`, so it survives code
edits and is easy to inspect. It is added to .gitignore.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from .db import _q
from .models import Proposal


def _diff_fields(original: dict[str, Any], final: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-field diff. Returns {field: {"from": original, "to": final}} for
    fields whose value changed. Ignores fields that match (the common case)."""
    changes: dict[str, dict[str, Any]] = {}
    keys = set(original) | set(final)
    for k in keys:
        a = original.get(k)
        b = final.get(k)
        if a != b:
            changes[k] = {"from": a, "to": b}
    return changes


def _index_by(items: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {it[key]: it for it in items if key in it}


def build_diff(original: Proposal, final: Proposal) -> dict[str, Any]:
    """Produce a structured diff of original vs final proposal.

    Captures: edits per surviving item, items removed by the user, items added
    (rare - the UI doesn't let you add, but defensive), and field-level changes.
    """
    orig_creates = _index_by([c.model_dump() for c in original.creates], "temp_id")
    fin_creates = _index_by([c.model_dump() for c in final.creates], "temp_id")
    orig_updates = _index_by([u.model_dump() for u in original.updates], "key")
    fin_updates = _index_by([u.model_dump() for u in final.updates], "key")
    orig_closes = _index_by([c.model_dump() for c in original.closes], "key")
    fin_closes = _index_by([c.model_dump() for c in final.closes], "key")

    def per_item_diff(orig_map: dict, fin_map: dict, item_key: str) -> dict[str, Any]:
        result: dict[str, Any] = {"removed": [], "edited": [], "kept_as_is": []}
        for k, orig in orig_map.items():
            if k not in fin_map:
                result["removed"].append({item_key: k, "original": orig})
                continue
            changes = _diff_fields(orig, fin_map[k])
            if changes:
                result["edited"].append({item_key: k, "field_changes": changes})
            else:
                result["kept_as_is"].append(k)
        return result

    return {
        "creates": per_item_diff(orig_creates, fin_creates, "temp_id"),
        "updates": per_item_diff(orig_updates, fin_updates, "key"),
        "closes": per_item_diff(orig_closes, fin_closes, "key"),
        "links": {
            "original_count": len(original.links),
            "final_count": len(final.links),
            "removed_count": max(0, len(original.links) - len(final.links)),
        },
    }


def has_any_changes(diff: dict[str, Any]) -> bool:
    for section in ("creates", "updates", "closes"):
        s = diff.get(section, {})
        if s.get("removed") or s.get("edited"):
            return True
    if diff.get("links", {}).get("removed_count", 0) > 0:
        return True
    return False


def record(
    original: Proposal,
    final: Proposal,
    context_excerpt: Optional[str] = None,
    outcomes_summary: Optional[dict[str, int]] = None,
) -> None:
    """Append one entry to edit_log.jsonl. Best-effort - swallows IO errors
    so logging never breaks the apply flow."""
    try:
        diff = build_diff(original, final)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "context_excerpt": (context_excerpt or "")[:300],
            "had_edits": has_any_changes(diff),
            "totals": {
                "creates_proposed": len(original.creates),
                "creates_applied": len(final.creates),
                "updates_proposed": len(original.updates),
                "updates_applied": len(final.updates),
                "closes_proposed": len(original.closes),
                "closes_applied": len(final.closes),
                "links_proposed": len(original.links),
                "links_applied": len(final.links),
            },
            "diff": diff,
            "outcomes_summary": outcomes_summary or {},
        }
        _q(
            "INSERT INTO edit_log(ts, payload) VALUES (?, ?)",
            (entry["ts"], json.dumps(entry, ensure_ascii=False)),
        )
    except Exception:
        pass
