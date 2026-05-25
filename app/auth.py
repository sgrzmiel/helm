"""Password-based session auth for Helm.

Design:
- A single shared password from the `APP_PASSWORD` env var (set in .env).
- If `APP_PASSWORD` is empty/unset, the app runs in *open mode*: every request
  is treated as authenticated. Lets dev / single-user setups skip the login
  flow entirely.
- On login, server mints a random token, stores it in-memory with an expiry,
  and sets it as an HttpOnly cookie. On each request, the cookie is matched
  against the in-memory store.
- Tokens live in memory only - server restart logs everyone out. That's fine
  for an internal tool.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Cookie, HTTPException, Response

COOKIE_NAME = "pmtk_session"
SESSION_TTL = timedelta(days=7)

# token -> expires_at_iso
_SESSIONS: dict[str, datetime] = {}


def _password_configured() -> bool:
    return bool((os.environ.get("APP_PASSWORD") or "").strip())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _prune() -> None:
    now = _now()
    expired = [t for t, exp in _SESSIONS.items() if exp < now]
    for t in expired:
        _SESSIONS.pop(t, None)


def is_open_mode() -> bool:
    """True when no APP_PASSWORD is set - the app is wide open."""
    return not _password_configured()


def is_token_valid(token: Optional[str]) -> bool:
    if is_open_mode():
        return True
    if not token:
        return False
    _prune()
    return token in _SESSIONS


def login(password: str, response: Response) -> bool:
    """Verify password and set session cookie. Returns True on success."""
    if not _password_configured():
        # Open mode - "log in" is a no-op success.
        return True
    expected = (os.environ.get("APP_PASSWORD") or "").strip()
    if not secrets.compare_digest(password.strip(), expected):
        return False
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = _now() + SESSION_TTL
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=int(SESSION_TTL.total_seconds()),
    )
    return True


def logout(token: Optional[str], response: Response) -> None:
    if token:
        _SESSIONS.pop(token, None)
    response.delete_cookie(COOKIE_NAME)


def current_status(token: Optional[str]) -> dict:
    """Reflect whether the current request is authenticated. Used by the
    frontend to decide whether to render the read-only mode."""
    if is_open_mode():
        return {"authenticated": True, "mode": "open"}
    return {
        "authenticated": is_token_valid(token),
        "mode": "password",
    }


def require_auth(pmtk_session: Optional[str] = Cookie(default=None, alias=COOKIE_NAME)) -> None:
    """FastAPI dependency. Inject on any endpoint that mutates state.

    In open mode it's a no-op; otherwise 401 if the session cookie is missing
    or invalid.
    """
    if is_open_mode():
        return
    if not is_token_valid(pmtk_session):
        raise HTTPException(status_code=401, detail="authentication required")
