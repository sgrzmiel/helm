"""Confluence page fetcher.

Detects Atlassian Confluence URLs and pulls their body content via the v2 API.
Uses the same ATLASSIAN_* credentials configured for Jira since they live on
the same domain configured in ATLASSIAN_DOMAIN.

Behavior:
- Storage-format XML is converted to a minimal plain-text representation
  (paragraphs, headings, list items) so it can be fed into prompts.
- Bodies are capped at MAX_BODY_CHARS to keep prompts within budget.
- Errors surface as `ConfluenceError`; callers store the message for display.
"""

from __future__ import annotations

import os
import re
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import unquote

import httpx

MAX_BODY_CHARS = 8000


CONFLUENCE_URL_RE = re.compile(
    r"https?://([^/]+\.atlassian\.net)/wiki/(?:.*?/pages/(\d+)|spaces/[^/]+/pages/(\d+))",
    re.IGNORECASE,
)


class ConfluenceError(Exception):
    pass


def is_confluence_url(url: str) -> bool:
    if not url:
        return False
    return bool(CONFLUENCE_URL_RE.search(unquote(url)))


def parse_url(url: str) -> tuple[str, str]:
    """Return (domain, page_id). Raises ConfluenceError if URL is not parseable."""
    m = CONFLUENCE_URL_RE.search(unquote(url or ""))
    if not m:
        raise ConfluenceError(f"could not extract Confluence page id from {url!r}")
    domain = m.group(1)
    page_id = m.group(2) or m.group(3)
    return domain, page_id


class _StripStorage(HTMLParser):
    """Best-effort conversion from Confluence storage XML/HTML to plain text.

    We don't try to fully parse the Confluence schema - just keep the readable
    text and use line breaks for block elements so the prompt model can tell
    paragraphs apart. Tables become tab-separated rows.
    """

    BLOCK_TAGS = {"p", "br", "li", "tr", "div"}
    HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0
        self._in_heading: Optional[str] = None

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        # Skip ac:structured-macro contents that are usually layout junk
        if tag.startswith("ac:") and tag != "ac:link":
            self._skip_depth += 1
            return
        if tag in self.HEADING_TAGS:
            self._in_heading = tag
            self.parts.append("\n\n# ")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")
        elif tag == "td" or tag == "th":
            self.parts.append("\t")

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag.startswith("ac:") and tag != "ac:link":
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in self.HEADING_TAGS:
            self._in_heading = None
            self.parts.append("\n")

    def handle_data(self, data: str):
        if self._skip_depth:
            return
        # Collapse internal whitespace; preserve our injected newlines via separator
        cleaned = re.sub(r"[ \t\r\f\v]+", " ", data)
        self.parts.append(cleaned)

    def text(self) -> str:
        out = "".join(self.parts)
        # Collapse runs of blank lines
        out = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", out)
        return out.strip()


def _strip_storage(storage_xml: str) -> str:
    p = _StripStorage()
    try:
        p.feed(storage_xml or "")
        p.close()
    except Exception:
        # Fall back to a brute strip if HTMLParser chokes on something weird
        return re.sub(r"<[^>]+>", " ", storage_xml or "").strip()
    return p.text()


def _verify() -> bool:
    flag = os.environ.get("JIRA_VERIFY_SSL", "true").strip().lower()
    return flag not in ("false", "0", "no")


async def fetch_page(url: str) -> tuple[str, str]:
    """Fetch a Confluence page. Returns (title, plain_text_body), truncated."""
    domain, page_id = parse_url(url)

    email = (os.environ.get("ATLASSIAN_EMAIL") or "").strip()
    token = (os.environ.get("ATLASSIAN_API_TOKEN") or "").strip()
    if not email or not token:
        raise ConfluenceError("Atlassian credentials missing - set ATLASSIAN_EMAIL and ATLASSIAN_API_TOKEN in Settings")

    api_url = f"https://{domain}/wiki/api/v2/pages/{page_id}?body-format=storage"

    async with httpx.AsyncClient(
        auth=(email, token),
        headers={"Accept": "application/json"},
        timeout=30.0,
        verify=_verify(),
    ) as client:
        try:
            resp = await client.get(api_url)
        except httpx.HTTPError as e:
            raise ConfluenceError(f"network error: {e}")

    if resp.status_code == 404:
        raise ConfluenceError(f"page {page_id} not found (or access denied)")
    if resp.status_code == 401 or resp.status_code == 403:
        raise ConfluenceError(f"not authorized to read page {page_id}")
    if resp.status_code >= 400:
        raise ConfluenceError(f"HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json() or {}
    title = (data.get("title") or "").strip()
    body_storage = (data.get("body") or {}).get("storage", {}).get("value") or ""
    text = _strip_storage(body_storage)

    if len(text) > MAX_BODY_CHARS:
        text = text[:MAX_BODY_CHARS] + f"\n\n... [truncated, page larger than {MAX_BODY_CHARS} chars]"

    return title, text
