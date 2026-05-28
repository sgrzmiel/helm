from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

import anthropic

from .db import get_all_config
from .models import DemoSummary, EpicAnalysis, ExtractedActionsResponse, Proposal, SlackReply, TicketSnapshot


MODEL = "claude-opus-4-7"
MAX_TOKENS = 16000

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


# ---------------------------------------------------------------------------
# Prompt loading + templating
# ---------------------------------------------------------------------------


def _strip_frontmatter(text: str) -> str:
    """Drop a leading `---`-delimited YAML frontmatter block, if present."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    # Skip past the closing --- + the following newline
    rest = text[end + 4:]
    return rest.lstrip("\n")


def _stakeholders_block() -> str:
    """Build the `{stakeholders}` placeholder content from team_members."""
    # Local import to avoid circular dependency at module load
    from .team import list_members
    members = list_members()
    if not members:
        return "(no team members configured - add them in the Settings tab)"
    lines: list[str] = []
    for m in members:
        line = f"- {m.name}"
        if m.role:
            line += f" ({m.role})"
        if m.email:
            line += f" <{m.email}>"
        lines.append(line)
    return "\n".join(lines)


def render_prompt(name: str) -> str:
    """Read prompts/<name>.md, strip frontmatter, substitute {placeholders}
    from the app_config table + team_members. Called on every LLM invocation
    so edits to the .md files take effect with no restart."""
    path = PROMPTS_DIR / f"{name}.md"
    raw = path.read_text(encoding="utf-8")
    body = _strip_frontmatter(raw)

    cfg = get_all_config()
    tokens = {
        "company_name": cfg.get("company_name", "the company"),
        "fe_project": cfg.get("fe_project_key", "FE"),
        "be_project": cfg.get("be_project_key", "BE"),
        "required_label": cfg.get("required_label", ""),
        "business_context": cfg.get("business_context", ""),
        "stakeholders": _stakeholders_block(),
    }
    # Use str.replace per-token rather than .format() because the prompt body
    # contains literal `{` / `}` characters (JSON examples) we mustn't break.
    for k, v in tokens.items():
        body = body.replace("{" + k + "}", v)
    return body



def build_user_message(
    tickets: list[TicketSnapshot],
    context: str,
    figma_context: Optional[str] = None,
    project_components: Optional[dict[str, list[str]]] = None,
) -> str:
    today = date.today().isoformat()

    parts: list[str] = [f"Today's date: {today}", ""]

    cfg = get_all_config()
    fe_project = cfg.get("fe_project_key", "FE")
    be_project = cfg.get("be_project_key", "BE")

    if tickets:
        tickets_json = json.dumps([t.model_dump() for t in tickets], indent=2, ensure_ascii=False)
        parts.append(f"## Existing tickets ({len(tickets)})\n\n```json\n{tickets_json}\n```")
    else:
        parts.append(
            f"## Existing tickets\n\n(none - this is Epic-creation mode: produce one Epic in {fe_project} plus child tickets, see system rules)"
        )

    if project_components:
        parts.append("\n## Available components")
        for proj, comps in project_components.items():
            if comps:
                parts.append(f"\n**{proj}** ({len(comps)} available): {', '.join(comps)}")
            else:
                parts.append(f"\n**{proj}**: (none defined)")
        parts.append(
            f"\nReminder: {be_project} requires `components` to be non-empty (pick from the {be_project} list). "
            f"{fe_project} is optional - include only when there's a clear match."
        )

    parts.append(f"\n## Context / requirements / conversation\n\n{context}")

    if figma_context:
        parts.append(f"\n## Figma design reference\n\n{figma_context}")

    parts.append(
        "\n## Task\n\nProduce a Proposal JSON object. Use the rules and schema from the system prompt. "
        "Every proposed change must trace to a specific cue in the context above."
    )

    return "\n".join(parts)


def build_proposal(
    tickets: list[TicketSnapshot],
    context: str,
    figma_context: Optional[str] = None,
    project_components: Optional[dict[str, list[str]]] = None,
) -> Proposal:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    response = client.messages.parse(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=[
            {
                "type": "text",
                "text": render_prompt("jira-proposal"),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": build_user_message(tickets, context, figma_context, project_components),
            },
        ],
        output_format=Proposal,
    )

    parsed = response.parsed_output
    if parsed is None:
        raise RuntimeError(
            f"Model did not return valid structured output. stop_reason={response.stop_reason}"
        )
    return parsed


# ---------------------------------------------------------------------------
# Epic analysis (Status dashboard)
# ---------------------------------------------------------------------------


# In-memory cache mirrors the SQLite `analysis_cache` table so we don't hit
# the DB on every read. Source of truth is the DB; mem cache fills lazily.
_ANALYSIS_CACHE: dict[str, tuple[EpicAnalysis, str]] = {}


from .db import _q, _qa, _qone  # noqa: E402


def _load_analysis_cache_from_db() -> None:
    rows = _qa("SELECT epic_key, analysis_json, analyzed_at FROM analysis_cache")
    for r in rows:
        try:
            analysis = EpicAnalysis.model_validate(json.loads(r["analysis_json"]))
            _ANALYSIS_CACHE[r["epic_key"]] = (analysis, r["analyzed_at"] or "")
        except Exception:
            continue


_load_analysis_cache_from_db()


def cached_analysis(epic_key: str) -> Optional[tuple[EpicAnalysis, str]]:
    return _ANALYSIS_CACHE.get(epic_key)


def clear_analysis_cache(epic_key: Optional[str] = None) -> None:
    if epic_key is None:
        _ANALYSIS_CACHE.clear()
        _q("DELETE FROM analysis_cache")
    else:
        _ANALYSIS_CACHE.pop(epic_key, None)
        _q("DELETE FROM analysis_cache WHERE epic_key = ?", (epic_key,))


_DOC_TOTAL_BUDGET = 30000  # chars across all linked docs in one analysis prompt


def _format_linked_docs(docs: Optional[list[dict]]) -> str:
    if not docs:
        return ""
    chunks: list[str] = ["\n## Linked knowledge\n"]
    budget = _DOC_TOTAL_BUDGET
    for d in docs:
        url = d.get("url") or ""
        label = d.get("label") or url
        text = d.get("cached_text")
        err = d.get("fetch_error")
        if text:
            body = text
            if len(body) > budget:
                body = body[:budget] + f"\n\n... [truncated; doc budget exhausted]"
            chunks.append(f"\n### {label}\nSource: {url}\n\n{body}\n")
            budget -= len(body)
            if budget <= 0:
                break
        elif err:
            chunks.append(f"\n### {label}\nSource: {url}\n(could not fetch: {err})\n")
    return "".join(chunks) if len(chunks) > 1 else ""


def analyze_epic(
    epic: TicketSnapshot,
    children: list[TicketSnapshot],
    current_user_email: Optional[str],
    linked_docs: Optional[list[dict]] = None,
    segments: Optional[list[str]] = None,
) -> tuple[EpicAnalysis, str]:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    today = date.today().isoformat()
    epic_json = json.dumps(epic.model_dump(), indent=2, ensure_ascii=False)
    children_json = json.dumps([t.model_dump() for t in children], indent=2, ensure_ascii=False)

    linked_section = _format_linked_docs(linked_docs)
    segments_line = ""
    if segments:
        segments_line = f"\n## Segments\n\nThis Epic targets the following audience segments: {', '.join(segments)}. Frame risks / actions / gaps in terms of how they affect these segments where relevant.\n"

    user_msg = f"""Today's date: {today}
Current user email: {current_user_email or "(unknown)"}

## Epic

```json
{epic_json}
```

## Children ({len(children)} tickets)

```json
{children_json}
```
{segments_line}{linked_section}

## Task

Produce an EpicAnalysis JSON object. Use the heuristics from the system prompt to spot stuck / stale / overdue items and blocking chains. Be specific - reference ticket keys.

If a "Linked knowledge" section is present above, use it as background context for understanding scope and intent. Do NOT restate items from the linked docs as risks/gaps unless they match a ticket-level signal too.
"""

    response = client.messages.parse(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=[
            {"type": "text", "text": render_prompt("analyze-epic"), "cache_control": {"type": "ephemeral"}}
        ],
        messages=[{"role": "user", "content": user_msg}],
        output_format=EpicAnalysis,
    )

    parsed = response.parsed_output
    if parsed is None:
        raise RuntimeError(
            f"Analysis model did not return valid structured output. stop_reason={response.stop_reason}"
        )

    analyzed_at = datetime.now(timezone.utc).isoformat()
    _ANALYSIS_CACHE[epic.key] = (parsed, analyzed_at)
    _q(
        "INSERT OR REPLACE INTO analysis_cache(epic_key, analysis_json, analyzed_at) VALUES (?, ?, ?)",
        (epic.key, json.dumps(parsed.model_dump()), analyzed_at),
    )
    return parsed, analyzed_at


# ---------------------------------------------------------------------------
# Slack reply generator
# ---------------------------------------------------------------------------


def generate_slack_reply(context: str, draft: Optional[str], audience: str) -> SlackReply:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    parts = [f"## Audience\n\n{audience}", f"\n## Context (thread / doc / situation)\n\n{context}"]
    if draft and draft.strip():
        parts.append(f"\n## User's draft\n\n{draft}")
    else:
        parts.append("\n## User's draft\n\n(none - infer reply from context, note assumptions)")
    parts.append(
        "\n## Task\n\nProduce a SlackReply JSON object. Reply in English regardless of input language. "
        "Apply the team tone rules and the audience-specific guidance from the system prompt."
    )

    response = client.messages.parse(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=[
            {"type": "text", "text": render_prompt("slack-reply"), "cache_control": {"type": "ephemeral"}}
        ],
        messages=[{"role": "user", "content": "\n".join(parts)}],
        output_format=SlackReply,
    )

    parsed = response.parsed_output
    if parsed is None:
        raise RuntimeError(
            f"Slack reply model did not return valid structured output. stop_reason={response.stop_reason}"
        )
    return parsed


# ---------------------------------------------------------------------------
# Discussion -> action items extractor
# ---------------------------------------------------------------------------



def extract_actions_from_discussion(
    epic: TicketSnapshot,
    children: list[TicketSnapshot],
    discussion: str,
) -> ExtractedActionsResponse:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    today = date.today().isoformat()
    epic_json = json.dumps(epic.model_dump(), indent=2, ensure_ascii=False)
    children_json = json.dumps(
        [t.model_dump() for t in children], indent=2, ensure_ascii=False,
    )

    user_msg = f"""Today's date: {today}

## Epic
```json
{epic_json}
```

## Children ({len(children)} tickets)
```json
{children_json}
```

## Discussion / notes

{discussion}

## Task

Produce an `ExtractedActionsResponse` JSON object. Only include actions explicitly grounded in the discussion above.
"""

    response = client.messages.parse(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=[{"type": "text", "text": render_prompt("extract-actions"), "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_msg}],
        output_format=ExtractedActionsResponse,
    )

    parsed = response.parsed_output
    if parsed is None:
        raise RuntimeError(
            f"Extract-actions model did not return valid structured output. stop_reason={response.stop_reason}"
        )
    # Mark every proposed action as manual-source so the UI shows them apart from AI analysis items
    for a in parsed.proposed:
        a.source = "manual"
    return parsed


# ---------------------------------------------------------------------------
# PPR stakeholder summary refiner
# ---------------------------------------------------------------------------


def refine_ppr_summary(
    item_kind: str,
    title: str,
    current_text: str,
    instruction: str,
    extra_context: str = "",
) -> str:
    """Rewrite a PPR stakeholder summary based on a user instruction.

    Plain text in / plain text out - no Pydantic schema needed for a single
    string response, so we use messages.create instead of messages.parse.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    user_msg = (
        f"## Item kind\n{item_kind}\n\n"
        f"## Title\n{title}\n\n"
        f"## Current summary\n{current_text or '(empty)'}\n\n"
        f"## User instruction\n{instruction or '(none - just polish it)'}"
    )
    if extra_context:
        user_msg += f"\n\n## Extra context\n{extra_context}"

    system = (
        "You rewrite stakeholder-facing project summaries for a Project Portfolio Review.\n"
        "\n"
        "Goal: explain WHAT THE PROJECT IS and WHAT VALUE IT DELIVERS - not its dev status.\n"
        "\n"
        "Structure:\n"
        "- sentence 1: what the project is in plain non-technical language (the user-facing thing)\n"
        "- sentence 2 (optional): the value it brings - who benefits and how\n"
        "\n"
        "Avoid:\n"
        "- dev-status language ('shipped', 'in flight', 'in active build', 'on track to land', '% complete', 'next milestone')\n"
        "- ticket keys, Jira jargon, codenames, named individuals or teams\n"
        "- restating the title back at the reader\n"
        "- preamble, quotes, labels, or commentary - output ONLY the rewritten summary as plain text\n"
        "\n"
        "Other rules:\n"
        "- 1-2 short sentences, executive-level, timeless framing (the kind of line that lives on a leadership slide)\n"
        "- respect the user's instruction verbatim if they specified one\n"
        "- write in English even if the input is in another language"
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=600,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_msg}],
    )
    text_parts = [block.text for block in response.content if getattr(block, "type", None) == "text"]
    return "\n".join(text_parts).strip()


# ---------------------------------------------------------------------------
# Demo-session slide summary
# ---------------------------------------------------------------------------


def generate_demo_summary(
    item_kind: str,
    title: str,
    body_context: str,
    segments: list[str],
    instruction: str = "",
) -> DemoSummary:
    """Produce a 4-section demo-slide summary (Purpose / Description / Value /
    Available to) from whatever context is available about the project or idea.

    Uses messages.parse with the DemoSummary schema so the response is always
    structured exactly the way the slide expects.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    segments_line = ", ".join(segments) if segments else "(none declared)"
    parts = [
        f"## Item kind\n{item_kind}",
        f"\n## Title\n{title}",
        f"\n## Declared segments\n{segments_line}",
        f"\n## Source context (epic state-of-play or idea notes)\n{body_context or '(empty)'}",
    ]
    if instruction.strip():
        parts.append(f"\n## User steer\n{instruction.strip()}")
    parts.append(
        "\n## Task\nReturn a DemoSummary JSON object. Follow the four-section structure exactly. "
        "Reread the system prompt rules before writing - especially the bans on dev-status language and 'we' as subject."
    )

    response = client.messages.parse(
        model=MODEL,
        max_tokens=2000,
        system=[{"type": "text", "text": render_prompt("demo-summary"), "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": "\n".join(parts)}],
        output_format=DemoSummary,
    )

    parsed = response.parsed_output
    if parsed is None:
        raise RuntimeError(
            f"Demo-summary model did not return valid structured output. stop_reason={response.stop_reason}"
        )
    return parsed
