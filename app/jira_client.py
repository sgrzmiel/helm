from __future__ import annotations

import os
import re
from typing import Any, Optional

import httpx

from .models import IssueLinkRef, TicketSnapshot


EPIC_URL_RE = re.compile(r"https?://[^/]+/browse/([A-Z][A-Z0-9_]*-\d+)")
KEY_RE = re.compile(r"\b([A-Z][A-Z0-9_]*-\d+)\b")
JQL_HINTS = ("project=", "status=", "issuekey", " AND ", " OR ", "labels=", " in (", "assignee=", "key in")


# ---------------------------------------------------------------------------
# ADF helpers — Jira API v3 takes descriptions as Atlassian Document Format.
# We convert outbound markdown into a minimal ADF subset (paragraph, heading,
# bullet list, code, bold/italic/code marks) and convert inbound ADF back into
# plain text so the LLM and the preview can work with strings.
# ---------------------------------------------------------------------------

INLINE_TOKEN_RE = re.compile(
    r"(\*\*.+?\*\*)|(`[^`]+`)|(\*[^*\s][^*]*\*)"
)


def _inline_to_adf(text: str) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    pos = 0
    for m in INLINE_TOKEN_RE.finditer(text):
        start, end = m.span()
        if start > pos:
            nodes.append({"type": "text", "text": text[pos:start]})
        token = m.group(0)
        if token.startswith("**"):
            nodes.append({"type": "text", "text": token[2:-2], "marks": [{"type": "strong"}]})
        elif token.startswith("`"):
            nodes.append({"type": "text", "text": token[1:-1], "marks": [{"type": "code"}]})
        else:
            nodes.append({"type": "text", "text": token[1:-1], "marks": [{"type": "em"}]})
        pos = end
    if pos < len(text):
        nodes.append({"type": "text", "text": text[pos:]})
    return nodes or [{"type": "text", "text": text}]


def markdown_to_adf(md: str) -> dict[str, Any]:
    if not md:
        return {"type": "doc", "version": 1, "content": []}

    lines = md.splitlines()
    content: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if stripped.startswith("```"):
            buf: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            content.append({
                "type": "codeBlock",
                "content": [{"type": "text", "text": "\n".join(buf)}],
            })
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2)
            content.append({
                "type": "heading",
                "attrs": {"level": min(level, 6)},
                "content": _inline_to_adf(text),
            })
            i += 1
            continue

        if re.match(r"^[*-]\s+", stripped):
            items: list[dict[str, Any]] = []
            while i < len(lines) and re.match(r"^[*-]\s+", lines[i].strip()):
                text = re.sub(r"^[*-]\s+", "", lines[i].strip())
                items.append({
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": _inline_to_adf(text)}],
                })
                i += 1
            content.append({"type": "bulletList", "content": items})
            continue

        para_lines: list[str] = []
        while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith("#") \
                and not re.match(r"^[*-]\s+", lines[i].strip()) \
                and not lines[i].strip().startswith("```"):
            para_lines.append(lines[i].strip())
            i += 1
        text = " ".join(para_lines)
        content.append({"type": "paragraph", "content": _inline_to_adf(text)})

    return {"type": "doc", "version": 1, "content": content}


def adf_to_text(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(adf_to_text(n) for n in node if n)
    if not isinstance(node, dict):
        return ""

    node_type = node.get("type")
    if node_type == "text":
        return node.get("text", "")
    if node_type in ("paragraph", "heading"):
        inner = "".join(adf_to_text(c) for c in node.get("content", []))
        prefix = "## " if node_type == "heading" else ""
        return prefix + inner + "\n"
    if node_type == "bulletList":
        return "".join(f"- {adf_to_text(c).strip()}\n" for c in node.get("content", []))
    if node_type == "listItem":
        return "".join(adf_to_text(c) for c in node.get("content", []))
    if node_type == "codeBlock":
        return "```\n" + "".join(adf_to_text(c) for c in node.get("content", [])) + "\n```\n"
    if node_type == "doc":
        return "".join(adf_to_text(c) for c in node.get("content", []))

    inner = node.get("content")
    if inner:
        return "".join(adf_to_text(c) for c in inner)
    return ""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class JiraError(Exception):
    pass


class JiraClient:
    def __init__(
        self,
        domain: Optional[str] = None,
        email: Optional[str] = None,
        token: Optional[str] = None,
    ):
        self.domain = domain or os.environ["ATLASSIAN_DOMAIN"]
        email = email or os.environ["ATLASSIAN_EMAIL"]
        token = token or os.environ["ATLASSIAN_API_TOKEN"]
        self.base = f"https://{self.domain}/rest/api/3"
        # Corporate networks sometimes MITM HTTPS with a self-signed CA.
        # Setting JIRA_VERIFY_SSL=false in .env disables verification so the
        # tool works on those networks. Defaults to verified.
        verify_env = os.environ.get("JIRA_VERIFY_SSL", "true").strip().lower()
        verify = verify_env not in ("false", "0", "no")
        self._client = httpx.AsyncClient(
            auth=(email, token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30.0,
            verify=verify,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = path if path.startswith("http") else f"{self.base}{path}"
        resp = await self._client.request(method, url, **kwargs)
        if resp.status_code >= 400:
            raise JiraError(f"{method} {path} → {resp.status_code}: {resp.text[:500]}")
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    # ---- Source parsing ---------------------------------------------------

    async def resolve_keys(self, source: str) -> list[str]:
        source = source.strip()
        if not source:
            return []

        epic_match = EPIC_URL_RE.search(source)
        if epic_match and "\n" not in source and "," not in source:
            epic_key = epic_match.group(1)
            return await self._epic_children(epic_key)

        if any(hint.lower() in source.lower() for hint in JQL_HINTS):
            return await self._search_jql(source)

        keys = KEY_RE.findall(source)
        return list(dict.fromkeys(keys))

    async def _epic_children(self, epic_key: str) -> list[str]:
        jql = f'parent = "{epic_key}" OR "Epic Link" = "{epic_key}"'
        return await self._search_jql(jql)

    async def _search_jql(self, jql: str) -> list[str]:
        out: list[str] = []
        next_token: Optional[str] = None
        for _ in range(10):
            payload: dict[str, Any] = {"jql": jql, "fields": ["summary"], "maxResults": 100}
            if next_token:
                payload["nextPageToken"] = next_token
            data = await self._request("POST", "/search/jql", json=payload)
            issues = data.get("issues", []) if data else []
            out.extend(issue["key"] for issue in issues)
            next_token = data.get("nextPageToken") if data else None
            if not next_token or data.get("isLast", True):
                break
        return out

    # ---- Snapshots --------------------------------------------------------

    async def fetch_tickets(self, keys: list[str]) -> list[TicketSnapshot]:
        snapshots: list[TicketSnapshot] = []
        for key in keys:
            data = await self._request(
                "GET",
                f"/issue/{key}",
                params={"fields": "summary,description,status,issuetype,priority,assignee,labels,duedate,updated,parent,issuelinks"},
            )
            if data is None:
                continue
            snapshots.append(self._to_snapshot(data))
        return snapshots

    async def fetch_epic_with_children(
        self, epic_key: str
    ) -> tuple[TicketSnapshot, list[TicketSnapshot]]:
        """Return (epic_snapshot, children_snapshots). Raises JiraError if epic missing."""
        epic_data = await self._request(
            "GET",
            f"/issue/{epic_key}",
            params={"fields": "summary,description,status,issuetype,priority,assignee,labels,duedate,updated,parent,issuelinks"},
        )
        if epic_data is None:
            raise JiraError(f"epic {epic_key} not found")
        epic_snapshot = self._to_snapshot(epic_data)

        child_keys = await self._epic_children(epic_key)
        children = await self.fetch_tickets(child_keys)
        return epic_snapshot, children

    @staticmethod
    def _to_snapshot(data: dict[str, Any]) -> TicketSnapshot:
        f = data.get("fields", {})
        status = f.get("status", {}) or {}
        category = (status.get("statusCategory") or {}).get("key", "")

        links: list[IssueLinkRef] = []
        for link in f.get("issuelinks", []) or []:
            link_type = (link.get("type") or {}).get("name") or ""
            if "outwardIssue" in link:
                links.append(IssueLinkRef(
                    type=link_type,
                    direction="outward",
                    key=link["outwardIssue"]["key"],
                ))
            if "inwardIssue" in link:
                links.append(IssueLinkRef(
                    type=link_type,
                    direction="inward",
                    key=link["inwardIssue"]["key"],
                ))

        parent = f.get("parent") or {}
        assignee = f.get("assignee") or {}
        priority = f.get("priority") or {}
        issuetype = f.get("issuetype") or {}

        return TicketSnapshot(
            key=data["key"],
            project=data["key"].split("-", 1)[0],
            summary=f.get("summary") or "",
            description=adf_to_text(f.get("description")).strip() or None,
            status=status.get("name") or "",
            status_category=category,
            issuetype=issuetype.get("name") or "",
            priority=priority.get("name"),
            assignee=assignee.get("displayName"),
            assignee_email=assignee.get("emailAddress"),
            labels=list(f.get("labels") or []),
            duedate=f.get("duedate"),
            updated=f.get("updated"),
            parent_key=parent.get("key"),
            links=links,
            modifiable=category == "new",
        )

    # ---- Mutations --------------------------------------------------------

    async def update_issue(self, key: str, fields: dict[str, Any]) -> None:
        payload: dict[str, Any] = {"fields": {}}
        for k, v in fields.items():
            if v is None:
                continue
            if k == "description":
                payload["fields"]["description"] = markdown_to_adf(v)
            elif k == "priority":
                payload["fields"]["priority"] = {"name": v}
            else:
                payload["fields"][k] = v
        if not payload["fields"]:
            return
        await self._request("PUT", f"/issue/{key}", json=payload)

    async def create_issue(
        self,
        project: str,
        summary: str,
        description: str,
        issuetype: str = "Story",
        labels: Optional[list[str]] = None,
        priority: Optional[str] = "Major",
        duedate: Optional[str] = None,
        parent_key: Optional[str] = None,
        components: Optional[list[str]] = None,
    ) -> str:
        fields: dict[str, Any] = {
            "project": {"key": project},
            "summary": summary,
            "description": markdown_to_adf(description),
            "issuetype": {"name": issuetype},
            "labels": labels or ["Commercial"],
        }
        if priority:
            fields["priority"] = {"name": priority}
        if duedate:
            fields["duedate"] = duedate
        if parent_key:
            fields["parent"] = {"key": parent_key}
        if components:
            fields["components"] = [{"name": c} for c in components]
        data = await self._request("POST", "/issue", json={"fields": fields})
        return data["key"]

    async def get_components(self, project_key: str) -> list[str]:
        """Return list of component names defined on a project."""
        data = await self._request("GET", f"/project/{project_key}/components")
        if not data:
            return []
        return [c.get("name", "") for c in data if c.get("name")]

    async def get_transitions(self, key: str) -> list[dict[str, Any]]:
        data = await self._request("GET", f"/issue/{key}/transitions")
        return data.get("transitions", []) if data else []

    async def transition_to(self, key: str, target_name: str) -> str:
        """Name-strict transition. Used for moving newly-created tickets to a
        specific status (e.g. "Selected for Development"). Raises if the target
        transition is not available - never falls back to a closing transition.
        """
        transitions = await self.get_transitions(key)
        chosen = next(
            (t for t in transitions if t["name"].lower() == target_name.lower()),
            None,
        )
        if not chosen:
            raise JiraError(
                f"{key}: transition '{target_name}' not available "
                f"(have: {[t['name'] for t in transitions]})"
            )
        await self._request(
            "POST",
            f"/issue/{key}/transitions",
            json={"transition": {"id": chosen["id"]}},
        )
        return chosen["name"]

    async def transition_issue(self, key: str, preferred_name: Optional[str] = None) -> str:
        transitions = await self.get_transitions(key)
        if not transitions:
            raise JiraError(f"{key}: no transitions available")

        def category(t: dict[str, Any]) -> str:
            return ((t.get("to") or {}).get("statusCategory") or {}).get("key", "")

        chosen: Optional[dict[str, Any]] = None
        if preferred_name:
            chosen = next((t for t in transitions if t["name"].lower() == preferred_name.lower()), None)
        if not chosen:
            for want in ("Won't Do", "Cancelled", "Canceled", "Reject", "Closed", "Done"):
                chosen = next((t for t in transitions if t["name"].lower() == want.lower()), None)
                if chosen:
                    break
        if not chosen:
            done_transitions = [t for t in transitions if category(t) == "done"]
            if done_transitions:
                chosen = done_transitions[0]
        if not chosen:
            raise JiraError(
                f"{key}: no closing transition (available: {[t['name'] for t in transitions]})"
            )

        await self._request(
            "POST",
            f"/issue/{key}/transitions",
            json={"transition": {"id": chosen["id"]}},
        )
        return chosen["name"]

    async def create_link(self, inward_key: str, outward_key: str, link_type: str) -> None:
        await self._request(
            "POST",
            "/issueLink",
            json={
                "type": {"name": link_type},
                "inwardIssue": {"key": inward_key},
                "outwardIssue": {"key": outward_key},
            },
        )
