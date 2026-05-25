from __future__ import annotations

from typing import Optional

from .db import _q, _qa
from .models import TeamMember


def list_members() -> list[TeamMember]:
    rows = _qa("SELECT name, email, role FROM team_members ORDER BY position ASC")
    return [TeamMember(name=r["name"], email=r["email"], role=r["role"]) for r in rows]


def replace_all(members: list[TeamMember]) -> list[TeamMember]:
    _q("DELETE FROM team_members")
    for i, m in enumerate(members):
        _q(
            "INSERT INTO team_members(name, email, role, position) VALUES (?, ?, ?, ?)",
            (m.name, m.email, m.role, i),
        )
    return list_members()


# ---------------------------------------------------------------------------
# Role bucketing for tickets
# ---------------------------------------------------------------------------


def _norm(s: Optional[str]) -> str:
    return (s or "").lower().strip()


def _identifier_matches_assignee(
    identifier: str,
    assignee_email: str,
    assignee_name: str,
) -> bool:
    """True if `identifier` (member's name or email field) plausibly identifies
    the given Jira assignee.

    Accepts any of these forms typed by the user in Settings:
    - Full email: `username@company.com` matches assignee_email
    - Email local part / handle: `janedoe` matches `janedoe@*` email AND
      prefix-matches `Jane` in display name (so `janedoe` -> `Jane Doe`)
    - Display name: `Jane Doe` matches case-insensitively
    - First-name only: `jane` matches first token of display name
    """
    c = _norm(identifier)
    if not c:
        return False

    if assignee_email:
        if c == assignee_email:
            return True
        if "@" in assignee_email and c == assignee_email.split("@", 1)[0]:
            return True

    if assignee_name:
        if c == assignee_name or c in assignee_name or assignee_name in c:
            return True
        # Tokenized prefix match - handles "janedoe" vs "Jane Doe"
        # (one is a prefix of the other after the trailing-s quirk vanishes).
        for tok in assignee_name.split():
            if tok and (tok.startswith(c) or c.startswith(tok)):
                return True

    return False


def bucket_for_assignee(
    assignee_email: Optional[str],
    assignee_name: Optional[str],
    members: list[TeamMember],
) -> str:
    """Return the role bucket for a given assignee."""
    if not assignee_email and not assignee_name:
        return "unassigned"

    email_n = _norm(assignee_email)
    name_n = _norm(assignee_name)

    for m in members:
        for candidate in (m.email, m.name):
            if _identifier_matches_assignee(candidate or "", email_n, name_n):
                return m.role

    return "other"
