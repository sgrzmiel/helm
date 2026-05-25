"""Document fetching + caching helpers.

Given a list of DocumentInput (url + optional label) and the existing stored
documents, produce a refreshed list with cached text where possible. Reuses
cached_text for unchanged URLs, fetches new Confluence URLs inline.

Per-doc text capped via confluence_client.MAX_BODY_CHARS. Total cap enforced
at the prompt-include layer, not here.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from .confluence_client import ConfluenceError, fetch_page, is_confluence_url
from .figma_client import FIGMA_FILE_RE, FigmaClient, FigmaError
from .google_drive import GoogleDriveError, fetch_url as fetch_google, is_google_url

FIGMA_MAX_CHARS = 8000


def is_figma_url(url: str) -> bool:
    if not url:
        return False
    return bool(FIGMA_FILE_RE.search(url))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _fetch_one(url: str) -> tuple[str, Optional[str], Optional[str]]:
    """Fetch a single doc. Returns (kind, cached_text, fetch_error).

    Confluence + Figma URLs get their content pulled. Other URLs are stored
    as-is (we don't crawl arbitrary web pages)."""
    if is_confluence_url(url):
        try:
            title, body = await fetch_page(url)
            text = body
            if title:
                text = f"Title: {title}\n\n{body}"
            return "confluence", text, None
        except ConfluenceError as e:
            return "confluence", None, str(e)

    if is_figma_url(url):
        try:
            client = FigmaClient()
            try:
                text = await client.extract_context(url, max_chars=FIGMA_MAX_CHARS)
            finally:
                await client.aclose()
            return "figma", text, None
        except FigmaError as e:
            return "figma", None, str(e)
        except Exception as e:
            # Includes missing token (KeyError on FIGMA_API_TOKEN) - surface clearly
            return "figma", None, f"figma fetch failed: {e}"

    if is_google_url(url):
        try:
            title, body = await fetch_google(url)
            text = f"Title: {title}\n\n{body}" if title else body
            return "google-drive", text, None
        except GoogleDriveError as e:
            return "google-drive", None, str(e)
        except Exception as e:
            return "google-drive", None, f"google fetch failed: {e}"

    return "other", None, None


async def refresh_documents(
    new_inputs: list[dict],
    existing: Optional[list[dict]] = None,
    force_refresh_urls: Optional[set[str]] = None,
) -> list[dict]:
    """Merge incoming doc inputs with previously cached docs.

    For each input:
    - If we already have it cached AND it's not in force_refresh_urls, reuse
      the cached_text / cached_at / kind.
    - Otherwise, fetch (if Confluence) and store the result.

    The result is the canonical list to persist.
    """
    existing = existing or []
    by_url = {d.get("url"): d for d in existing if d.get("url")}
    force_refresh_urls = force_refresh_urls or set()

    out: list[dict] = []

    async def process(doc_in: dict) -> dict:
        url = (doc_in.get("url") or "").strip()
        label = (doc_in.get("label") or "").strip() or None
        if not url:
            return None  # type: ignore[return-value]

        prior = by_url.get(url)
        if prior and url not in force_refresh_urls:
            # Reuse cached payload; refresh label if user changed it.
            return {
                "url": url,
                "label": label or prior.get("label"),
                "kind": prior.get("kind") or "other",
                "cached_text": prior.get("cached_text"),
                "cached_at": prior.get("cached_at"),
                "fetch_error": prior.get("fetch_error"),
            }

        kind, text, err = await _fetch_one(url)
        return {
            "url": url,
            "label": label,
            "kind": kind,
            "cached_text": text,
            "cached_at": _now() if (text or err) else None,
            "fetch_error": err,
        }

    results = await asyncio.gather(*[process(d) for d in new_inputs])
    out = [r for r in results if r is not None]
    return out
