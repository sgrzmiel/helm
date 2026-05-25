from __future__ import annotations

import os
import re
from typing import Any, Optional
from urllib.parse import unquote

import httpx


FIGMA_FILE_RE = re.compile(r"figma\.com/(?:file|design|proto|board)/([a-zA-Z0-9]+)")
NODE_ID_RE = re.compile(r"node-id=([^&]+)")


class FigmaError(Exception):
    pass


def parse_figma_url(url: str) -> tuple[str, Optional[str]]:
    m = FIGMA_FILE_RE.search(url)
    if not m:
        raise FigmaError(f"could not extract Figma file key from URL: {url}")
    node_match = NODE_ID_RE.search(url)
    node_id = unquote(node_match.group(1)) if node_match else None
    return m.group(1), node_id


class FigmaClient:
    def __init__(self, token: Optional[str] = None):
        token = token or os.environ.get("FIGMA_API_TOKEN")
        if not token:
            raise FigmaError("FIGMA_API_TOKEN not set in .env")
        verify_env = os.environ.get("JIRA_VERIFY_SSL", "true").strip().lower()
        verify = verify_env not in ("false", "0", "no")
        self._client = httpx.AsyncClient(
            headers={"X-Figma-Token": token},
            timeout=30.0,
            verify=verify,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str) -> dict[str, Any]:
        resp = await self._client.get(f"https://api.figma.com{path}")
        if resp.status_code >= 400:
            raise FigmaError(f"GET {path} → {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    async def extract_context(self, url: str, max_chars: int = 30000) -> str:
        file_key, node_id = parse_figma_url(url)

        try:
            file_data = await self._get(f"/v1/files/{file_key}")
        except FigmaError as e:
            raise FigmaError(f"file fetch: {e}")

        try:
            comments_data = await self._get(f"/v1/files/{file_key}/comments")
            comments = comments_data.get("comments", [])
        except FigmaError:
            comments = []  # comments are nice-to-have, not load-bearing

        out: list[str] = []
        out.append(f"## Figma file: {file_data.get('name', '?')}")
        out.append(f"URL: {url}")
        if node_id:
            out.append(f"Focused node: {node_id}")
        out.append(f"Last modified: {file_data.get('lastModified', '?')}")
        out.append("")

        document = file_data.get("document", {})
        out.append("### Structure (pages → frames → text)")
        for page in document.get("children", []) or []:
            out.append(f"\n**Page: {page.get('name', '?')}**")
            for child in page.get("children", []) or []:
                _render_node(child, out, indent=0)

        if comments:
            out.append("\n### Comments")
            for c in comments[:50]:
                user = (c.get("user") or {}).get("handle") or "?"
                msg = (c.get("message") or "").strip()
                if msg:
                    out.append(f"- [{user}]: {msg}")

        result = "\n".join(out)
        if len(result) > max_chars:
            result = result[:max_chars] + "\n\n... [truncated, file larger than 30KB]"
        return result


def _render_node(node: dict[str, Any], out: list[str], indent: int) -> None:
    prefix = "  " * indent
    node_type = node.get("type", "?")
    name = (node.get("name") or "").strip()

    if node_type == "TEXT":
        text = (node.get("characters") or "").replace("\n", " ").strip()
        if text:
            out.append(f"{prefix}- text: \"{text}\"")
        return

    if node_type in ("FRAME", "GROUP", "COMPONENT", "COMPONENT_SET", "INSTANCE", "SECTION"):
        label = node_type.lower()
        if name:
            out.append(f"{prefix}- {label}: {name}")
        for child in node.get("children", []) or []:
            _render_node(child, out, indent + 1)
        return

    # Other node types: skip the wrapper line but still recurse
    for child in node.get("children", []) or []:
        _render_node(child, out, indent)
