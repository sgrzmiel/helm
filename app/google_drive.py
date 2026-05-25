"""Google Drive client for fetching Docs and Slides content.

OAuth 2 flow:
1. User clicks "Connect" in Settings; frontend opens our /api/google/auth-url
   which redirects to Google's consent screen.
2. Google redirects back to /api/google/callback?code=... we exchange the code
   for an access + refresh token. Refresh token is persisted in .env.
3. The refresh token is long-lived; the access token expires every ~1h and we
   refresh on demand using the refresh token.

Storage: GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET + GOOGLE_REFRESH_TOKEN live in
.env via the settings module. Access token is cached in-process only.

Scopes: drive.readonly (for Docs export), presentations.readonly (for Slides).
"""

from __future__ import annotations

import os
import re
import time
from typing import Optional
from urllib.parse import urlencode

import httpx

# OAuth endpoints
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# Read-only Drive (covers Docs export) + read-only Slides API
SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/presentations.readonly",
]

MAX_BODY_CHARS = 8000

DOC_URL_RE = re.compile(r"https?://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)", re.IGNORECASE)
SLIDES_URL_RE = re.compile(r"https?://docs\.google\.com/presentation/d/([a-zA-Z0-9_-]+)", re.IGNORECASE)


class GoogleDriveError(Exception):
    pass


# --------------------------------------------------------------------------
# URL helpers
# --------------------------------------------------------------------------


def is_google_url(url: str) -> bool:
    if not url:
        return False
    return bool(DOC_URL_RE.search(url) or SLIDES_URL_RE.search(url))


def parse_url(url: str) -> tuple[str, str]:
    """Return (kind, id) where kind is 'doc' or 'slides'."""
    m = DOC_URL_RE.search(url or "")
    if m:
        return "doc", m.group(1)
    m = SLIDES_URL_RE.search(url or "")
    if m:
        return "slides", m.group(1)
    raise GoogleDriveError(f"not a recognized Google Docs/Slides URL: {url!r}")


# --------------------------------------------------------------------------
# OAuth + token plumbing
# --------------------------------------------------------------------------


def _client_id() -> str:
    val = (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
    if not val:
        raise GoogleDriveError("GOOGLE_CLIENT_ID is not set - configure it in Settings.")
    return val


def _client_secret() -> str:
    val = (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()
    if not val:
        raise GoogleDriveError("GOOGLE_CLIENT_SECRET is not set - configure it in Settings.")
    return val


def redirect_uri(base_url: str) -> str:
    """The OAuth redirect URI we register with Google. Must match exactly."""
    return f"{base_url.rstrip('/')}/api/google/callback"


def is_configured() -> bool:
    return bool(
        (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
        and (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()
    )


def is_connected() -> bool:
    return bool((os.environ.get("GOOGLE_REFRESH_TOKEN") or "").strip())


def auth_redirect_url(base_url: str, state: Optional[str] = None) -> str:
    """Build the URL the user should be sent to so Google can ask for consent."""
    params = {
        "client_id": _client_id(),
        "redirect_uri": redirect_uri(base_url),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",  # forces refresh_token to be issued
        "prompt": "consent",       # forces re-prompt so we always get refresh_token
    }
    if state:
        params["state"] = state
    return f"{AUTH_URL}?{urlencode(params)}"


def _verify() -> bool:
    flag = os.environ.get("JIRA_VERIFY_SSL", "true").strip().lower()
    return flag not in ("false", "0", "no")


async def exchange_code(base_url: str, code: str) -> str:
    """Trade a one-time auth code for tokens. Returns the refresh_token.
    Side effect: caches the access token in memory for immediate use."""
    body = {
        "code": code,
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "redirect_uri": redirect_uri(base_url),
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=30.0, verify=_verify()) as client:
        resp = await client.post(TOKEN_URL, data=body)
    if resp.status_code >= 400:
        raise GoogleDriveError(f"token exchange failed: HTTP {resp.status_code} {resp.text[:300]}")
    data = resp.json()
    refresh = data.get("refresh_token")
    if not refresh:
        raise GoogleDriveError(
            "Google did not return a refresh_token. If you've connected before, revoke at "
            "https://myaccount.google.com/permissions and try again."
        )
    _cache_access_token(data.get("access_token") or "", data.get("expires_in") or 0)
    return refresh


# Access-token cache: {token, expires_at_epoch}
_ACCESS_CACHE: dict[str, float] = {}


def _cache_access_token(token: str, expires_in: int) -> None:
    if not token:
        return
    _ACCESS_CACHE["token"] = token
    _ACCESS_CACHE["expires_at"] = time.time() + max(60, expires_in - 30)


async def _get_access_token() -> str:
    now = time.time()
    cached = _ACCESS_CACHE.get("token")
    if cached and _ACCESS_CACHE.get("expires_at", 0) > now:
        return cached  # type: ignore[return-value]
    refresh = (os.environ.get("GOOGLE_REFRESH_TOKEN") or "").strip()
    if not refresh:
        raise GoogleDriveError("Google Drive is not connected. Open Settings and click Connect.")
    body = {
        "refresh_token": refresh,
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "grant_type": "refresh_token",
    }
    async with httpx.AsyncClient(timeout=30.0, verify=_verify()) as client:
        resp = await client.post(TOKEN_URL, data=body)
    if resp.status_code >= 400:
        raise GoogleDriveError(f"refresh failed: HTTP {resp.status_code} {resp.text[:300]}")
    data = resp.json()
    _cache_access_token(data.get("access_token") or "", data.get("expires_in") or 0)
    return _ACCESS_CACHE["token"]  # type: ignore[return-value]


# --------------------------------------------------------------------------
# Fetchers
# --------------------------------------------------------------------------


async def fetch_doc(file_id: str) -> tuple[str, str]:
    """Export a Google Doc as plain text. Returns (title, body)."""
    token = await _get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=60.0, verify=_verify(), headers=headers) as client:
        # File metadata for the title
        meta = await client.get(f"https://www.googleapis.com/drive/v3/files/{file_id}?fields=name")
        if meta.status_code >= 400:
            raise GoogleDriveError(f"metadata: HTTP {meta.status_code} {meta.text[:200]}")
        title = (meta.json() or {}).get("name") or ""
        # Plain-text export
        body_resp = await client.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}/export?mimeType=text/plain"
        )
    if body_resp.status_code >= 400:
        raise GoogleDriveError(f"export: HTTP {body_resp.status_code} {body_resp.text[:200]}")
    text = body_resp.text or ""
    if len(text) > MAX_BODY_CHARS:
        text = text[:MAX_BODY_CHARS] + f"\n\n... [truncated, doc larger than {MAX_BODY_CHARS} chars]"
    return title, text


async def fetch_slides(presentation_id: str) -> tuple[str, str]:
    """Walk slides and concatenate text frames. Returns (title, body)."""
    token = await _get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://slides.googleapis.com/v1/presentations/{presentation_id}"
    async with httpx.AsyncClient(timeout=60.0, verify=_verify(), headers=headers) as client:
        resp = await client.get(url)
    if resp.status_code >= 400:
        raise GoogleDriveError(f"slides: HTTP {resp.status_code} {resp.text[:200]}")
    data = resp.json() or {}
    title = (data.get("title") or "").strip()

    lines: list[str] = []
    for slide_idx, slide in enumerate(data.get("slides") or [], start=1):
        slide_lines: list[str] = []
        for el in slide.get("pageElements") or []:
            shape = el.get("shape") or {}
            text_obj = shape.get("text") or {}
            for te in text_obj.get("textElements") or []:
                run = te.get("textRun") or {}
                content = (run.get("content") or "").rstrip()
                if content:
                    slide_lines.append(content)
        if slide_lines:
            lines.append(f"--- Slide {slide_idx} ---")
            lines.extend(slide_lines)
    text = "\n".join(lines).strip()
    if len(text) > MAX_BODY_CHARS:
        text = text[:MAX_BODY_CHARS] + f"\n\n... [truncated, slides larger than {MAX_BODY_CHARS} chars]"
    return title, text


async def fetch_url(url: str) -> tuple[str, str]:
    """Generic fetcher for any supported Google URL. Returns (title, body)."""
    kind, file_id = parse_url(url)
    if kind == "doc":
        return await fetch_doc(file_id)
    return await fetch_slides(file_id)
