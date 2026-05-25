"""Append-only log of user closures (dismissals / done / reopen) for risks,
gaps, and action items - including the user's stated reason.

The log is the learning signal for the `refine-jira-prompt` skill: every time
the user dismisses a risk with "this is normal" or marks an action done with
"already covered by FE-X", that's a hint about what to suppress or restructure
in the analysis prompt.

Stored as a `closure_log` table in helm.db (SQLite).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from .db import _q

CloseType = Literal["risk", "gap", "action", "recommendation"]
CloseAction = Literal["close", "reopen"]


def record(
    epic_key: str,
    item_type: CloseType,
    action: CloseAction,
    sig: str,
    title: str,
    detail: str,
    reason: Optional[str] = None,
) -> None:
    """Best-effort append. Never raises so the close flow can't be broken by IO."""
    try:
        _q(
            """INSERT INTO closure_log(ts, epic_key, type, action, sig, title, detail, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                epic_key,
                item_type,
                action,
                sig,
                title or "",
                (detail or "")[:500],
                ((reason or "").strip()[:1000] or None),
            ),
        )
    except Exception:
        pass
