from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from .figma_client import FigmaClient, FigmaError  # noqa: E402
from .jira_client import JiraClient, JiraError  # noqa: E402
from .llm import (  # noqa: E402
    analyze_epic,
    build_proposal,
    cached_analysis,
    clear_analysis_cache,
    refine_ppr_summary,
    generate_slack_reply,
)
from .models import (  # noqa: E402
    ActionItem,
    ActionItemForList,
    ActionsGroup,
    ActionsResponse,
    PPRGroup,
    PPRProject,
    PPRRefineRequest,
    PPRRefineResponse,
    PPRResponse,
    PPRSummaryUpdate,
    AddActionRequest,
    ApplyOutcome,
    ApplyRequest,
    ApplyResponse,
    CloseRequest,
    CreateFromItemRequest,
    CreateFromItemResponse,
    CreateIdeaRequest,
    EpicDashboardEntry,
    EpicDashboardResponse,
    EpicDetail,
    EpicMetadata,
    ExtractActionsRequest,
    ExtractedActionsResponse,
    Gap,
    Idea,
    Recommendation,
    IdeasResponse,
    LoginRequest,
    PlanRequest,
    ReorderIdeasRequest,
    UpdateIdeaRequest,
    PlanResponse,
    Risk,
    ReorderTrackedRequest,
    RoleSplit,
    SettingsUpdate,
    SlackReply,
    SlackReplyRequest,
    StatusCounts,
    TeamMember,
    TeamMembersResponse,
    TicketSnapshot,
    TrackRequest,
)
from . import auth, closure_log, edit_log, google_drive, ideas as ideas_store, overrides, settings, team, tracked  # noqa: E402
from fastapi import Cookie, Depends, Response  # noqa: E402


app = FastAPI(title="Helm")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# Tab routes: serve the same SPA so deep links survive page refresh.
# The frontend reads window.location.pathname on load to pick the active tab.
@app.get("/requirements", include_in_schema=False)
@app.get("/projects", include_in_schema=False)
@app.get("/ppr", include_in_schema=False)
@app.get("/actions", include_in_schema=False)
@app.get("/ideas", include_in_schema=False)
@app.get("/slack", include_in_schema=False)
@app.get("/settings", include_in_schema=False)
async def index_tab() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/auth/status")
async def auth_status(pmtk_session: Optional[str] = Cookie(default=None, alias=auth.COOKIE_NAME)) -> dict:
    return auth.current_status(pmtk_session)


@app.post("/api/auth/login")
async def auth_login(req: LoginRequest, response: Response) -> dict:
    ok = auth.login(req.password, response)
    if not ok:
        raise HTTPException(status_code=401, detail="invalid password")
    return {"ok": True}


@app.post("/api/auth/logout")
async def auth_logout(
    response: Response,
    pmtk_session: Optional[str] = Cookie(default=None, alias=auth.COOKIE_NAME),
) -> dict:
    auth.logout(pmtk_session, response)
    return {"ok": True}


@app.get("/api/settings")
async def get_settings() -> dict:
    return settings.get_status()


@app.post("/api/settings", dependencies=[Depends(auth.require_auth)])
async def post_settings(req: SettingsUpdate) -> dict:
    return settings.update(req.model_dump(exclude_none=True))


# ---------------------------------------------------------------------------
# App config (company name, project keys, required label, business context)
# ---------------------------------------------------------------------------


@app.get("/api/config")
async def get_app_config() -> dict:
    from .db import get_all_config
    return get_all_config()


@app.put("/api/config", dependencies=[Depends(auth.require_auth)])
async def put_app_config(updates: dict[str, str]) -> dict:
    from .db import set_config
    # Only allow known keys to avoid junk piling up in the table.
    ALLOWED = {"company_name", "fe_project_key", "be_project_key", "required_label", "business_context"}
    safe = {k: v for k, v in updates.items() if k in ALLOWED}
    return set_config(safe)


# ---------------------------------------------------------------------------
# Google Drive OAuth + status
# ---------------------------------------------------------------------------


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@app.get("/api/google/status")
async def google_status() -> dict:
    return {
        "configured": google_drive.is_configured(),
        "connected": google_drive.is_connected(),
    }


@app.get("/api/google/auth-url", dependencies=[Depends(auth.require_auth)])
async def google_auth_url(request: Request) -> dict:
    try:
        return {"url": google_drive.auth_redirect_url(_base_url(request))}
    except google_drive.GoogleDriveError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/google/callback")
async def google_callback(request: Request, code: Optional[str] = None, error: Optional[str] = None):
    # Auth on the callback is implicit - Google's redirect lands here on the
    # user's already-authenticated session.
    if error:
        return HTMLResponse(_callback_html(f"Google returned an error: {error}", ok=False))
    if not code:
        return HTMLResponse(_callback_html("Missing `code` parameter.", ok=False))
    try:
        refresh = await google_drive.exchange_code(_base_url(request), code)
    except google_drive.GoogleDriveError as e:
        return HTMLResponse(_callback_html(str(e), ok=False))
    settings.update({"GOOGLE_REFRESH_TOKEN": refresh})
    return HTMLResponse(_callback_html("Google Drive connected. You can close this tab.", ok=True))


@app.post("/api/google/disconnect", dependencies=[Depends(auth.require_auth)])
async def google_disconnect() -> dict:
    settings.update({"GOOGLE_REFRESH_TOKEN": ""})
    return {"ok": True}


def _callback_html(msg: str, ok: bool) -> str:
    color = "#26890C" if ok else "#E21B3C"
    return f"""<!doctype html>
<html><head><title>Google Drive callback</title></head>
<body style="font-family: -apple-system, sans-serif; padding: 40px; text-align: center;">
  <div style="display:inline-block; padding: 24px 40px; border-radius: 12px; background: white; box-shadow: 0 2px 8px rgba(0,0,0,0.1); border-top: 4px solid {color};">
    <h2 style="margin: 0 0 12px; color: {color};">{'Connected' if ok else 'Failed'}</h2>
    <p style="margin: 0; color: #333;">{msg}</p>
    <p style="margin-top: 16px;"><a href="/settings" style="color: #46178F;">Back to Settings</a></p>
  </div>
  <script>
    try {{ if (window.opener) {{ window.opener.postMessage({{ google: {{ ok: {str(ok).lower()} }} }}, '*'); }} }} catch (e) {{}}
  </script>
</body></html>"""


# ---------------------------------------------------------------------------
# Team members (used for per-role progress splits on the Projects Dashboard)
# ---------------------------------------------------------------------------


@app.get("/api/team", response_model=TeamMembersResponse)
async def get_team() -> TeamMembersResponse:
    return TeamMembersResponse(members=team.list_members())


# ---------------------------------------------------------------------------
# Ideas (pre-project capture, kanban-style)
# ---------------------------------------------------------------------------


@app.get("/api/ideas", response_model=IdeasResponse)
async def get_ideas() -> IdeasResponse:
    return IdeasResponse(ideas=ideas_store.list_ideas())


@app.post("/api/ideas", response_model=Idea, dependencies=[Depends(auth.require_auth)])
async def post_idea(req: CreateIdeaRequest) -> Idea:
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="title is required")
    docs_payload = [d.model_dump() for d in (req.documents or [])]
    return await ideas_store.create(
        title=req.title,
        notes=req.notes,
        one_pager_url=req.one_pager_url,
        stakeholder=req.stakeholder,
        status=req.status,
        documents=docs_payload,
        segments=req.segments,
    )


@app.put("/api/ideas/{idea_id}", response_model=Idea, dependencies=[Depends(auth.require_auth)])
async def put_idea(idea_id: str, req: UpdateIdeaRequest) -> Idea:
    fields = req.model_dump(exclude_unset=True)
    if "documents" in fields and fields["documents"] is not None:
        fields["documents"] = [
            d if isinstance(d, dict) else d.model_dump()
            for d in fields["documents"]
        ]
    updated = await ideas_store.update(idea_id, fields)
    if not updated:
        raise HTTPException(status_code=404, detail=f"idea {idea_id} not found")
    return updated


@app.delete("/api/ideas/{idea_id}", dependencies=[Depends(auth.require_auth)])
async def delete_idea(idea_id: str) -> dict:
    if not ideas_store.delete(idea_id):
        raise HTTPException(status_code=404, detail=f"idea {idea_id} not found")
    return {"ok": True}


@app.post("/api/ideas/reorder", response_model=IdeasResponse, dependencies=[Depends(auth.require_auth)])
async def reorder_ideas(req: ReorderIdeasRequest) -> IdeasResponse:
    new_list = ideas_store.reorder([e.model_dump() for e in req.order])
    return IdeasResponse(ideas=new_list)


@app.put("/api/team", response_model=TeamMembersResponse, dependencies=[Depends(auth.require_auth)])
async def put_team(req: TeamMembersResponse) -> TeamMembersResponse:
    # Cleared cache so role splits recompute on next refresh with new mapping.
    _DASHBOARD_CACHE.clear()
    return TeamMembersResponse(members=team.replace_all(req.members))


# ---------------------------------------------------------------------------
# Projects Dashboard / Tracked Epics
# ---------------------------------------------------------------------------

# In-memory cache for the dashboard list. Survives across page visits but not
# server restarts - that's fine, the user can click Refresh.
_DASHBOARD_CACHE: dict[str, EpicDashboardEntry] = {}
_DASHBOARD_LAST_SYNCED: Optional[str] = None


def _action_counts(key: str) -> tuple[Optional[int], Optional[int]]:
    """Returns (open_count, for_user_open_count). Both None when the epic has
    no action data at all - the dashboard card hides the chips in that case.

    Manual additions count even when no LLM analysis exists yet."""
    cached = cached_analysis(key)
    ai_actions = list(cached[0].action_items) if cached else []
    manual = overrides.get_actions(key) or []
    if not ai_actions and not manual:
        return None, None
    done = overrides.get_done_actions(key) or set()
    for_user_overrides = overrides.get_action_for_user_overrides(key)
    open_count = 0
    for_user_count = 0
    for a in list(manual) + list(ai_actions):
        sig = overrides.action_signature(a.title, a.detail)
        if sig in done:
            continue
        open_count += 1
        # Honor manual override of for_user when present
        for_user = for_user_overrides.get(sig, getattr(a, "for_user", False))
        if for_user:
            for_user_count += 1
    return open_count, for_user_count


async def _fetch_one_entry(jira: JiraClient, key: str) -> EpicDashboardEntry:
    now = datetime.now(timezone.utc).isoformat()
    try:
        epic, children = await jira.fetch_epic_with_children(key)
        counts = _compute_counts(children)
        cached = cached_analysis(key)
        assessment = cached[0].progress_assessment if cached else None
        meta = overrides.get_metadata(key)
        open_count, for_user_count = _action_counts(key)
        return EpicDashboardEntry(
            key=epic.key,
            summary=epic.summary,
            status=epic.status,
            status_category=epic.status_category,
            duedate=epic.duedate,
            counts=counts,
            progress_pct=_progress(counts),
            last_synced=now,
            role_split=_compute_role_split(children),
            assessment=assessment,
            open_action_count=open_count,
            for_user_action_count=for_user_count,
            metadata=EpicMetadata(**meta) if meta else None,
        )
    except JiraError as e:
        return EpicDashboardEntry(
            key=key,
            summary=f"(error: {e})",
            status="unknown",
            status_category="unknown",
            counts=StatusCounts(),
            progress_pct=0,
            last_synced=now,
        )


async def _refresh_all_entries() -> EpicDashboardResponse:
    global _DASHBOARD_LAST_SYNCED
    keys = tracked.list_keys()
    if not keys:
        _DASHBOARD_CACHE.clear()
        _DASHBOARD_LAST_SYNCED = datetime.now(timezone.utc).isoformat()
        return EpicDashboardResponse(entries=[], last_synced=_DASHBOARD_LAST_SYNCED)

    _require_credentials("ATLASSIAN_DOMAIN", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN")
    jira = JiraClient()
    try:
        entries = await asyncio.gather(*[_fetch_one_entry(jira, k) for k in keys])
    finally:
        await jira.aclose()

    _DASHBOARD_CACHE.clear()
    for e in entries:
        _DASHBOARD_CACHE[e.key] = e
    _DASHBOARD_LAST_SYNCED = datetime.now(timezone.utc).isoformat()
    return EpicDashboardResponse(entries=list(entries), last_synced=_DASHBOARD_LAST_SYNCED)


def _cached_response() -> EpicDashboardResponse:
    # Return entries in the order of tracked.list_keys(), preserving cached rows
    # and inserting placeholders for newly-tracked-but-not-yet-fetched epics.
    keys = tracked.list_keys()
    entries: list[EpicDashboardEntry] = []
    for k in keys:
        if k in _DASHBOARD_CACHE:
            row = _DASHBOARD_CACHE[k]
            # Always overlay the latest cached assessment + metadata - both can
            # be updated after the dashboard row was last fetched (user opened
            # detail / saved metadata), and we want the card to reflect them.
            updates = {}
            cached = cached_analysis(k)
            if cached:
                updates["assessment"] = cached[0].progress_assessment
            open_count, for_user_count = _action_counts(k)
            updates["open_action_count"] = open_count
            updates["for_user_action_count"] = for_user_count
            meta = overrides.get_metadata(k)
            updates["metadata"] = EpicMetadata(**meta) if meta else None
            if updates:
                row = row.model_copy(update=updates)
                _DASHBOARD_CACHE[k] = row
            entries.append(row)
        else:
            entries.append(
                EpicDashboardEntry(
                    key=k,
                    summary="(not fetched yet - click Refresh)",
                    status="pending",
                    status_category="unknown",
                    counts=StatusCounts(),
                    progress_pct=0,
                    last_synced=None,
                )
            )
    # Global last_synced = most recent row fetch, OR the full-refresh timestamp,
    # whichever is more recent. Reflects "freshest data on this page".
    candidates = [t for t in (e.last_synced for e in entries) if t]
    if _DASHBOARD_LAST_SYNCED:
        candidates.append(_DASHBOARD_LAST_SYNCED)
    global_ts = max(candidates) if candidates else None
    return EpicDashboardResponse(entries=entries, last_synced=global_ts)


@app.get("/api/tracked", response_model=EpicDashboardResponse)
async def list_tracked() -> EpicDashboardResponse:
    # Cache-first: return whatever's cached. If cache is empty AND we have
    # tracked keys, fetch once to populate (first-ever load); otherwise the
    # user clicks Refresh to update.
    if not _DASHBOARD_CACHE and tracked.list_keys():
        return await _refresh_all_entries()
    return _cached_response()


@app.post("/api/tracked/refresh-all", response_model=EpicDashboardResponse, dependencies=[Depends(auth.require_auth)])
async def refresh_all_tracked() -> EpicDashboardResponse:
    return await _refresh_all_entries()


@app.post("/api/tracked/reorder", response_model=EpicDashboardResponse, dependencies=[Depends(auth.require_auth)])
async def reorder_tracked(req: ReorderTrackedRequest) -> EpicDashboardResponse:
    tracked.reorder(req.keys)
    return _cached_response()


@app.get("/api/ppr", response_model=PPRResponse)
async def get_ppr(recent_days: int = 60) -> PPRResponse:
    """Project Portfolio Review aggregate. Classifies tracked epics into three
    lifecycle buckets and surfaces a short stakeholder-facing summary per
    project. Used by the PPR tab for the 10-minute exec walk-through."""
    # Seed the dashboard cache if it's empty - PPR needs the summaries +
    # status_category that only come from a Jira fetch. Mirrors what
    # GET /api/tracked does on first call.
    if not _DASHBOARD_CACHE and tracked.list_keys():
        try:
            await _refresh_all_entries()
        except Exception:
            pass  # Render whatever we have; UI surfaces an empty state.

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=recent_days)

    # Group by segment (business/school/home/students/other). Items inside a
    # group are a mix of tracked projects and queued ideas, each carrying
    # their own lifecycle stage so the UI can render a stage badge.
    SEGMENT_ORDER: list[str] = ["business", "school", "home", "students", "internal", "other"]
    SEGMENT_LABELS: dict[str, str] = {
        "business": "Business",
        "school": "School",
        "home": "Home",
        "students": "Students",
        "internal": "Internal",
        "other": "Other / unsegmented",
    }
    buckets: dict[str, list[PPRProject]] = {s: [] for s in SEGMENT_ORDER}

    def primary_segment(segments: list[str]) -> str:
        # "Most matching" isn't something we can infer cheaply, so take the
        # first declared segment - that's the editor's natural ordering.
        return segments[0] if segments else "other"

    # --- tracked projects ---
    for key in tracked.list_keys():
        row = _DASHBOARD_CACHE.get(key)
        if row is None:
            continue

        # Always read metadata fresh - the dashboard cache may hold a stale
        # row from before the user edited the PPR summary or segments.
        meta_dict = overrides.get_metadata(key)
        segments = list(meta_dict.get("segments") or [])

        # User-edited override wins; otherwise prefer the LLM stakeholder
        # summary; fall back to the first 1-2 sentences of state_of_play.
        stakeholder_summary: Optional[str] = (meta_dict.get("ppr_summary") or "").strip() or None
        if not stakeholder_summary:
            cached = cached_analysis(key)
            if cached:
                analysis = cached[0]
                stakeholder_summary = (analysis.stakeholder_summary or "").strip() or None
                if not stakeholder_summary and analysis.state_of_play:
                    sentences = re.split(r"(?<=[.!?])\s+", analysis.state_of_play.strip())
                    stakeholder_summary = " ".join(sentences[:2]).strip()

        cat = row.status_category
        if cat == "done":
            include = True
            if row.last_synced:
                try:
                    include = datetime.fromisoformat(row.last_synced) >= cutoff
                except ValueError:
                    include = True
            if not include:
                continue
            stage: PPRStage = "recently_completed"
        elif cat == "indeterminate":
            stage = "development"
        else:
            stage = "preparation"

        proj = PPRProject(
            kind="project",
            stage=stage,
            key=row.key,
            summary=row.summary,
            progress_pct=row.progress_pct,
            counts=row.counts,
            duedate=row.duedate,
            segments=segments,
            assessment=row.assessment,
            stakeholder=meta_dict.get("stakeholder"),
            one_pager_url=meta_dict.get("one_pager_url"),
            stakeholder_summary=stakeholder_summary,
        )
        buckets[primary_segment(segments)].append(proj)

    # --- queued ideas shown as "in preparation" ---
    for idea in ideas_store.list_ideas():
        if idea.status != "queued":
            continue
        idea_segments = list(idea.segments or [])
        summary_text = (idea.ppr_summary or "").strip() or (idea.notes or "").strip() or None
        item = PPRProject(
            kind="idea",
            stage="preparation",
            key=idea.id,
            summary=idea.title,
            progress_pct=0,
            counts=StatusCounts(),
            duedate=None,
            segments=idea_segments,
            assessment=None,
            stakeholder=idea.stakeholder,
            one_pager_url=idea.one_pager_url,
            stakeholder_summary=summary_text,
        )
        buckets[primary_segment(idea_segments)].append(item)

    # Sort each segment bucket: in development > in preparation > recently
    # completed; tie-break by progress desc so the most-active sits on top.
    stage_rank = {"development": 0, "preparation": 1, "recently_completed": 2}
    for items in buckets.values():
        items.sort(key=lambda p: (stage_rank.get(p.stage, 99), -p.progress_pct))

    groups = [
        PPRGroup(segment=s, label=SEGMENT_LABELS[s], projects=buckets[s])
        for s in SEGMENT_ORDER
        if buckets[s]
    ]
    return PPRResponse(groups=groups, recent_window_days=recent_days)


@app.patch("/api/ppr/summary", dependencies=[Depends(auth.require_auth)])
async def update_ppr_summary(req: PPRSummaryUpdate) -> dict:
    """Persist a user-edited stakeholder summary. Empty / None clears it."""
    new_text = (req.summary or "").strip() or None
    if req.kind == "project":
        if req.key not in tracked.list_keys():
            raise HTTPException(status_code=404, detail=f"epic {req.key} not tracked")
        overrides.set_metadata(req.key, {"ppr_summary": new_text or ""})
        return {"kind": "project", "key": req.key, "summary": new_text}
    # idea
    updated = await ideas_store.update(req.key, {"ppr_summary": new_text or ""})
    if updated is None:
        raise HTTPException(status_code=404, detail=f"idea {req.key} not found")
    return {"kind": "idea", "key": req.key, "summary": updated.ppr_summary}


@app.post("/api/ppr/refine-summary", response_model=PPRRefineResponse, dependencies=[Depends(auth.require_auth)])
async def refine_summary(req: PPRRefineRequest) -> PPRRefineResponse:
    """Ask the LLM to rewrite the stakeholder summary per a user instruction."""
    _require_credentials("ANTHROPIC_API_KEY")
    # Gather title + a bit of extra context the LLM can latch onto.
    title = ""
    extra_context = ""
    if req.kind == "project":
        row = _DASHBOARD_CACHE.get(req.key)
        title = row.summary if row else req.key
        cached = cached_analysis(req.key)
        if cached:
            extra_context = (cached[0].state_of_play or "")[:1200]
    else:
        idea = ideas_store.get(req.key)
        if idea is None:
            raise HTTPException(status_code=404, detail=f"idea {req.key} not found")
        title = idea.title
        extra_context = (idea.notes or "")[:1200]
    try:
        suggested = await asyncio.to_thread(
            refine_ppr_summary,
            req.kind, title, req.current_text, req.instruction, extra_context,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"llm failure: {e}")
    return PPRRefineResponse(suggested=suggested)


# ---------------------------------------------------------------------------
# Background analysis warm-up
# ---------------------------------------------------------------------------

# Module-level state so concurrent triggers don't fire duplicate work.
_WARMUP_LOCK = asyncio.Lock()
_WARMUP_PENDING: set[str] = set()  # epic keys currently being analyzed
_WARMUP_FAILED: dict[str, str] = {}  # epic key -> error message


async def _warmup_single(key: str) -> None:
    """Run analyze_epic for one epic if it's still missing. Best-effort."""
    if cached_analysis(key) is not None:
        return
    try:
        _require_credentials(
            "ANTHROPIC_API_KEY", "ATLASSIAN_DOMAIN", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN"
        )
        jira = JiraClient()
        try:
            epic, children = await jira.fetch_epic_with_children(key)
        finally:
            await jira.aclose()
        linked_docs = overrides.get_metadata(key).get("documents", []) or []
        segments_now = overrides.get_metadata(key).get("segments", []) or []
        await asyncio.to_thread(
            analyze_epic,
            epic, children, os.environ.get("ATLASSIAN_EMAIL"),
            linked_docs, segments_now,
        )
        _WARMUP_FAILED.pop(key, None)
    except Exception as e:
        _WARMUP_FAILED[key] = str(e)[:300]
    finally:
        _WARMUP_PENDING.discard(key)


async def _warmup_runner(keys: list[str]) -> None:
    """Sequentially analyze the supplied keys. Sequential (not parallel) to
    keep token spend predictable and to not hammer Jira."""
    for k in keys:
        await _warmup_single(k)


def _missing_analysis_keys() -> list[str]:
    return [k for k in tracked.list_keys() if cached_analysis(k) is None]


@app.get("/api/analyze-missing/status")
async def analyze_missing_status() -> dict:
    pending = list(_WARMUP_PENDING)
    missing = _missing_analysis_keys()
    return {
        "in_progress": bool(pending),
        "pending": pending,
        "missing": missing,
        "total_tracked": len(tracked.list_keys()),
        "failed": dict(_WARMUP_FAILED),
    }


@app.post("/api/analyze-missing", dependencies=[Depends(auth.require_auth)])
async def analyze_missing() -> dict:
    """Kick off background analysis for every tracked epic that doesn't have
    a cached EpicAnalysis. Idempotent - if work is already in progress, just
    return the current status."""
    async with _WARMUP_LOCK:
        if _WARMUP_PENDING:
            return {"started": False, "reason": "already in progress", "pending": list(_WARMUP_PENDING)}
        missing = _missing_analysis_keys()
        if not missing:
            return {"started": False, "reason": "all epics already analyzed", "pending": []}
        _WARMUP_PENDING.update(missing)
        asyncio.create_task(_warmup_runner(missing))
    return {"started": True, "pending": missing}


@app.get("/api/actions", response_model=ActionsResponse)
async def list_all_actions() -> ActionsResponse:
    """Aggregate every tracked epic's action items into one list, grouped by
    epic. Manual actions come first (matches the detail view). Each entry
    includes sig + done so the UI can drive the same per-action operations
    as the detail panel."""
    groups: list[ActionsGroup] = []
    for k in tracked.list_keys():
        cached = cached_analysis(k)
        ai_actions = list(cached[0].action_items) if cached else []
        manual = overrides.get_actions(k) or []
        if not ai_actions and not manual:
            continue
        done = overrides.get_done_actions(k) or set()
        for_user_overrides = overrides.get_action_for_user_overrides(k)
        items: list[ActionItemForList] = []
        # Manual first (mirrors _apply_overrides ordering).
        for idx, a in enumerate(manual):
            base = a.model_dump()
            sig = overrides.action_signature(a.title, a.detail)
            base["sig"] = sig
            base["done"] = sig in done
            base["for_user"] = for_user_overrides.get(sig, base.get("for_user", False))
            items.append(ActionItemForList(**base, manual_index=idx))
        for a in ai_actions:
            base = a.model_dump()
            sig = overrides.action_signature(a.title, a.detail)
            base["sig"] = sig
            base["done"] = sig in done
            base["for_user"] = for_user_overrides.get(sig, base.get("for_user", False))
            items.append(ActionItemForList(**base, manual_index=None))

        # Pull summary from the dashboard cache row if we have it; otherwise
        # use the epic key as the display.
        cached_row = _DASHBOARD_CACHE.get(k)
        summary = cached_row.summary if cached_row else k
        assessment = None
        if cached:
            assessment = cached[0].progress_assessment

        groups.append(ActionsGroup(
            epic_key=k,
            epic_summary=summary,
            epic_assessment=assessment,
            actions=items,
        ))
    return ActionsResponse(groups=groups)


@app.post("/api/tracked/{key}/refresh-row", response_model=EpicDashboardEntry, dependencies=[Depends(auth.require_auth)])
async def refresh_tracked_row(key: str) -> EpicDashboardEntry:
    if key not in tracked.list_keys():
        raise HTTPException(status_code=404, detail=f"{key} is not tracked")
    _require_credentials("ATLASSIAN_DOMAIN", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN")
    jira = JiraClient()
    try:
        entry = await _fetch_one_entry(jira, key)
    finally:
        await jira.aclose()
    _DASHBOARD_CACHE[key] = entry
    return entry


_JIRA_KEY_RE = re.compile(r"([A-Z][A-Z0-9_]*-\d+)")


def _extract_jira_key(raw: str) -> Optional[str]:
    """Accept a bare key (FE-101) or a Jira URL
    (https://example.atlassian.net/browse/FE-101) and return the key."""
    if not raw:
        return None
    m = _JIRA_KEY_RE.search(raw.strip().upper())
    return m.group(1) if m else None


@app.post("/api/tracked", response_model=EpicDashboardResponse, dependencies=[Depends(auth.require_auth)])
async def add_tracked(req: TrackRequest) -> EpicDashboardResponse:
    key = _extract_jira_key(req.key)
    if not key:
        raise HTTPException(
            status_code=400,
            detail=f"could not extract Jira key from '{req.key}' (expected FE-123 or a /browse/ URL)",
        )

    _require_credentials("ATLASSIAN_DOMAIN", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN")
    jira = JiraClient()
    try:
        try:
            snapshots = await jira.fetch_tickets([key])
            if not snapshots:
                raise HTTPException(status_code=404, detail=f"epic {key} not found")
        except JiraError as e:
            raise HTTPException(status_code=400, detail=f"could not verify epic: {e}")
        tracked.add(key)
        # Fetch the dashboard row immediately so the user sees populated data
        # without clicking Refresh. Failures here are surfaced (not swallowed)
        # so the user knows if the row will be a placeholder.
        try:
            entry = await _fetch_one_entry(jira, key)
            _DASHBOARD_CACHE[key] = entry
        except JiraError as e:
            raise HTTPException(
                status_code=502,
                detail=f"added {key} but initial fetch failed: {e}",
            )
    finally:
        await jira.aclose()

    return _cached_response()


@app.delete("/api/tracked/{key}", dependencies=[Depends(auth.require_auth)])
async def remove_tracked(key: str) -> dict:
    clear_analysis_cache(key)
    _DASHBOARD_CACHE.pop(key, None)
    return {"keys": tracked.remove(key)}


async def _fetch_epic_basics(key: str) -> tuple[TicketSnapshot, list[TicketSnapshot]]:
    jira = JiraClient()
    try:
        try:
            return await jira.fetch_epic_with_children(key)
        except JiraError as e:
            raise HTTPException(status_code=502, detail=f"jira fetch failed: {e}")
    finally:
        await jira.aclose()


def _basic_detail(key: str, epic, children, analysis=None, analyzed_at=None) -> EpicDetail:
    meta = overrides.get_metadata(key)
    return EpicDetail(
        epic=epic,
        tickets=children,
        counts=_compute_counts(children),
        progress_pct=_progress(_compute_counts(children)),
        analysis=analysis,
        analyzed_at=analyzed_at,
        role_split=_compute_role_split(children),
        metadata=EpicMetadata(**meta) if meta else None,
    )


async def _build_detail(key: str, force_refresh: bool) -> EpicDetail:
    _require_credentials(
        "ANTHROPIC_API_KEY", "ATLASSIAN_DOMAIN", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN"
    )
    epic, children = await _fetch_epic_basics(key)

    if force_refresh:
        clear_analysis_cache(key)

    cached = cached_analysis(key)
    if cached:
        analysis, analyzed_at = cached
    else:
        meta_now = overrides.get_metadata(key)
        linked_docs = meta_now.get("documents", []) or []
        segments_now = meta_now.get("segments", []) or []
        try:
            analysis, analyzed_at = await asyncio.to_thread(
                analyze_epic,
                epic, children, os.environ.get("ATLASSIAN_EMAIL"),
                linked_docs, segments_now,
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"llm failure: {e}")

    analysis = _apply_overrides(key, analysis)

    if key in _DASHBOARD_CACHE:
        _DASHBOARD_CACHE[key] = _DASHBOARD_CACHE[key].model_copy(
            update={"assessment": analysis.progress_assessment},
        )

    return _basic_detail(key, epic, children, analysis=analysis, analyzed_at=analyzed_at)


def _apply_overrides(key: str, analysis):
    """Build an override-applied copy of the AI analysis:
    - prepend manual actions
    - tag each action item with a sig + done flag
    - tag each risk with a sig + dismissed flag (frontend filters/shows)

    Returns a new analysis object - never mutates the cached one (otherwise a
    second open would duplicate manual actions, since they're prepended again).
    """
    analysis = analysis.model_copy(deep=True)
    manual_actions = overrides.get_actions(key)
    dismissed_risks = overrides.get_dismissed_risks(key)
    done_actions = overrides.get_done_actions(key)
    for_user_overrides = overrides.get_action_for_user_overrides(key)

    # Manual actions go first so they're visible above AI-generated ones.
    all_actions = list(manual_actions) + list(analysis.action_items)
    new_actions: list[ActionItem] = []
    for a in all_actions:
        sig = overrides.action_signature(a.title, a.detail)
        for_user = for_user_overrides.get(sig, a.for_user)
        new_actions.append(ActionItem(
            title=a.title,
            detail=a.detail,
            ticket_keys=a.ticket_keys,
            urgency=a.urgency,
            for_user=for_user,
            source=a.source,
            sig=sig,
            done=sig in done_actions,
        ))
    analysis.action_items = new_actions

    new_risks: list[Risk] = []
    for r in analysis.risks:
        sig = overrides.risk_signature(r.title, r.detail)
        new_risks.append(Risk(
            title=r.title,
            detail=r.detail,
            ticket_keys=r.ticket_keys,
            severity=r.severity,
            sig=sig,
            dismissed=sig in dismissed_risks,
        ))
    analysis.risks = new_risks

    dismissed_gaps = overrides.get_dismissed_gaps(key)
    new_gaps: list[Gap] = []
    for g in analysis.gaps:
        sig = overrides.risk_signature(g.title, g.detail)
        new_gaps.append(Gap(
            title=g.title,
            detail=g.detail,
            suggested_summary=g.suggested_summary,
            suggested_project=g.suggested_project,
            sig=sig,
            dismissed=sig in dismissed_gaps,
        ))
    analysis.gaps = new_gaps

    dismissed_recs = overrides.get_dismissed_recommendations(key)
    new_recs: list[Recommendation] = []
    for r in analysis.recommendations:
        sig = overrides.risk_signature(r.title, r.detail)
        new_recs.append(Recommendation(
            title=r.title,
            detail=r.detail,
            ticket_keys=r.ticket_keys,
            sig=sig,
            dismissed=sig in dismissed_recs,
        ))
    analysis.recommendations = new_recs
    return analysis


@app.get("/api/tracked/{key}/basic", response_model=EpicDetail, dependencies=[Depends(auth.require_auth)])
async def get_tracked_basic(key: str) -> EpicDetail:
    """Fast path - Jira fetch + counts + role split + metadata, no LLM trigger.

    If an analysis is already cached on disk, it's attached too; otherwise the
    frontend should fire the full GET in the background to populate it.
    """
    _require_credentials("ATLASSIAN_DOMAIN", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN")
    epic, children = await _fetch_epic_basics(key)
    cached = cached_analysis(key)
    analysis = analyzed_at = None
    if cached:
        analysis, analyzed_at = cached
        analysis = _apply_overrides(key, analysis)
    return _basic_detail(key, epic, children, analysis=analysis, analyzed_at=analyzed_at)


@app.get("/api/tracked/{key}", response_model=EpicDetail, dependencies=[Depends(auth.require_auth)])
async def get_tracked_detail(key: str) -> EpicDetail:
    return await _build_detail(key, force_refresh=False)


@app.post("/api/tracked/{key}/refresh", response_model=EpicDetail, dependencies=[Depends(auth.require_auth)])
async def refresh_tracked(key: str) -> EpicDetail:
    return await _build_detail(key, force_refresh=True)


@app.post("/api/tracked/{key}/create-stub", dependencies=[Depends(auth.require_auth)])
async def create_stub_ticket(key: str) -> dict:
    """Create a placeholder ticket under the given epic and return its URL.

    Used by the "+ Create ticket" button on the dashboard - more reliable than
    Jira's URL-param create modal, which silently ignores the parent/labels we
    pass. The user opens the returned URL in Jira and edits the fields there.
    """
    if key not in tracked.list_keys():
        raise HTTPException(status_code=404, detail=f"{key} is not tracked")
    _require_credentials("ATLASSIAN_DOMAIN", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN")
    from .db import get_config as _get_cfg
    fe_proj = _get_cfg("fe_project_key", "FE")
    req_label = _get_cfg("required_label", "")
    labels = [req_label] if req_label else []
    jira = JiraClient()
    try:
        try:
            new_key = await jira.create_issue(
                project=fe_proj,
                summary="CHANGE ME",
                description="(placeholder - rename in Jira)",
                issuetype="Story",
                labels=labels,
                priority="Major",
                duedate=None,
                parent_key=key,
                components=None,
            )
        except JiraError as e:
            raise HTTPException(status_code=502, detail=f"create failed: {e}")
        try:
            await jira.transition_to(new_key, "Selected for Development")
        except JiraError:
            pass  # not fatal - ticket exists, user can transition manually
    finally:
        await jira.aclose()

    domain = (os.environ.get("ATLASSIAN_DOMAIN") or "").strip()
    url = f"https://{domain}/browse/{new_key}" if domain else None
    return {"key": new_key, "url": url}


# ---- Detail cache (replaces browser localStorage) -----------------------


@app.get("/api/tracked/{key}/detail-cache")
async def get_detail_cache(key: str) -> dict:
    """Return the last EpicDetail we cached for this epic, or {} when empty.
    Public so anonymous users see whatever fresh data the auth'd user last
    populated."""
    from .db import _qone
    row = _qone(
        "SELECT detail_json, cached_at FROM detail_cache WHERE epic_key = ?",
        (key,),
    )
    if row is None:
        return {}
    import json as _json
    return {"detail": _json.loads(row["detail_json"]), "cached_at": row["cached_at"]}


@app.put("/api/tracked/{key}/detail-cache", dependencies=[Depends(auth.require_auth)])
async def put_detail_cache(key: str, body: dict) -> dict:
    """Frontend posts the freshly-rendered detail back here so subsequent loads
    can show it instantly without a fresh fetch."""
    from .db import _q
    import json as _json
    from datetime import datetime, timezone
    _q(
        "INSERT OR REPLACE INTO detail_cache(epic_key, detail_json, cached_at) VALUES (?, ?, ?)",
        (key, _json.dumps(body), datetime.now(timezone.utc).isoformat()),
    )
    return {"ok": True}


@app.put("/api/tracked/{key}/metadata", response_model=EpicMetadata, dependencies=[Depends(auth.require_auth)])
async def put_metadata(key: str, req: EpicMetadata) -> EpicMetadata:
    if key not in tracked.list_keys():
        raise HTTPException(status_code=404, detail=f"{key} is not tracked")
    payload = req.model_dump(exclude_unset=False)
    docs_in = payload.pop("documents", None)
    saved = overrides.set_metadata(key, payload)

    # If the client sent documents, refresh them inline (fetch new Confluence
    # URLs, reuse cached text for unchanged ones).
    if docs_in is not None:
        existing = overrides.get_metadata(key).get("documents", [])
        from .docs import refresh_documents
        new_docs = await refresh_documents(docs_in, existing=existing)
        saved = overrides.set_metadata_documents(key, new_docs)
        saved = overrides.get_metadata(key)  # re-read with backfill

    # Refresh the dashboard cache row so card display updates immediately.
    if key in _DASHBOARD_CACHE:
        _DASHBOARD_CACHE[key] = _DASHBOARD_CACHE[key].model_copy(
            update={"metadata": EpicMetadata(**saved) if saved else None},
        )
    return EpicMetadata(**saved)


# ---- Closure (risks/gaps/actions) ---------------------------------------


def _lookup_item_text(key: str, item_type: str, sig: str) -> tuple[str, str]:
    """Find an item's title+detail from the cached analysis, used for logging.
    Returns ("", "") if not found - the log still gets the sig+ts+reason."""
    cached = cached_analysis(key)
    if not cached:
        return "", ""
    analysis = cached[0]
    pool = []
    if item_type == "risk":
        pool = [(r.title, r.detail, overrides.risk_signature(r.title, r.detail)) for r in analysis.risks]
    elif item_type == "gap":
        pool = [(g.title, g.detail, overrides.risk_signature(g.title, g.detail)) for g in analysis.gaps]
    elif item_type == "recommendation":
        pool = [(r.title, r.detail, overrides.risk_signature(r.title, r.detail)) for r in analysis.recommendations]
    elif item_type == "action":
        merged = list(overrides.get_actions(key)) + list(analysis.action_items)
        pool = [(a.title, a.detail, overrides.action_signature(a.title, a.detail)) for a in merged]
    for title, detail, s in pool:
        if s == sig:
            return title, detail
    return "", ""


@app.post("/api/tracked/{key}/risks/{sig}/dismiss", dependencies=[Depends(auth.require_auth)])
async def dismiss_risk(key: str, sig: str, req: CloseRequest = CloseRequest()) -> dict:
    if key not in tracked.list_keys():
        raise HTTPException(status_code=404, detail=f"{key} is not tracked")
    overrides.dismiss_risk(key, sig)
    title, detail = _lookup_item_text(key, "risk", sig)
    closure_log.record(key, "risk", "close", sig, title, detail, req.reason)
    return {"dismissed": list(overrides.get_dismissed_risks(key))}


@app.delete("/api/tracked/{key}/risks/{sig}/dismiss", dependencies=[Depends(auth.require_auth)])
async def restore_risk(key: str, sig: str) -> dict:
    if key not in tracked.list_keys():
        raise HTTPException(status_code=404, detail=f"{key} is not tracked")
    overrides.restore_risk(key, sig)
    title, detail = _lookup_item_text(key, "risk", sig)
    closure_log.record(key, "risk", "reopen", sig, title, detail)
    return {"dismissed": list(overrides.get_dismissed_risks(key))}


# ---- Gap dismissal -------------------------------------------------------


@app.post("/api/tracked/{key}/gaps/{sig}/dismiss", dependencies=[Depends(auth.require_auth)])
async def dismiss_gap(key: str, sig: str, req: CloseRequest = CloseRequest()) -> dict:
    if key not in tracked.list_keys():
        raise HTTPException(status_code=404, detail=f"{key} is not tracked")
    overrides.dismiss_gap(key, sig)
    title, detail = _lookup_item_text(key, "gap", sig)
    closure_log.record(key, "gap", "close", sig, title, detail, req.reason)
    return {"dismissed": list(overrides.get_dismissed_gaps(key))}


@app.delete("/api/tracked/{key}/gaps/{sig}/dismiss", dependencies=[Depends(auth.require_auth)])
async def restore_gap(key: str, sig: str) -> dict:
    if key not in tracked.list_keys():
        raise HTTPException(status_code=404, detail=f"{key} is not tracked")
    overrides.restore_gap(key, sig)
    title, detail = _lookup_item_text(key, "gap", sig)
    closure_log.record(key, "gap", "reopen", sig, title, detail)
    return {"dismissed": list(overrides.get_dismissed_gaps(key))}


# ---- Recommendation dismissal -------------------------------------------


@app.post("/api/tracked/{key}/recommendations/{sig}/dismiss", dependencies=[Depends(auth.require_auth)])
async def dismiss_recommendation(key: str, sig: str, req: CloseRequest = CloseRequest()) -> dict:
    if key not in tracked.list_keys():
        raise HTTPException(status_code=404, detail=f"{key} is not tracked")
    overrides.dismiss_recommendation(key, sig)
    title, detail = _lookup_item_text(key, "recommendation", sig)
    closure_log.record(key, "recommendation", "close", sig, title, detail, req.reason)
    return {"dismissed": list(overrides.get_dismissed_recommendations(key))}


@app.delete("/api/tracked/{key}/recommendations/{sig}/dismiss", dependencies=[Depends(auth.require_auth)])
async def restore_recommendation(key: str, sig: str) -> dict:
    if key not in tracked.list_keys():
        raise HTTPException(status_code=404, detail=f"{key} is not tracked")
    overrides.restore_recommendation(key, sig)
    title, detail = _lookup_item_text(key, "recommendation", sig)
    closure_log.record(key, "recommendation", "reopen", sig, title, detail)
    return {"dismissed": list(overrides.get_dismissed_recommendations(key))}


# ---- Manual actions ------------------------------------------------------


@app.post("/api/tracked/{key}/actions", response_model=list[ActionItem], dependencies=[Depends(auth.require_auth)])
async def add_action(key: str, req: AddActionRequest) -> list[ActionItem]:
    if key not in tracked.list_keys():
        raise HTTPException(status_code=404, detail=f"{key} is not tracked")
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="title is required")
    action = ActionItem(
        title=req.title.strip(),
        detail=req.detail.strip(),
        urgency=req.urgency,
        ticket_keys=req.ticket_keys,
        for_user=req.for_user,
        source="manual",
    )
    return overrides.add_action(key, action)


@app.delete("/api/tracked/{key}/actions/{index}", response_model=list[ActionItem], dependencies=[Depends(auth.require_auth)])
async def remove_action(key: str, index: int) -> list[ActionItem]:
    if key not in tracked.list_keys():
        raise HTTPException(status_code=404, detail=f"{key} is not tracked")
    return overrides.remove_action(key, index)


@app.post("/api/tracked/{key}/actions/{sig}/done", dependencies=[Depends(auth.require_auth)])
async def mark_action_done(key: str, sig: str, req: CloseRequest = CloseRequest()) -> dict:
    if key not in tracked.list_keys():
        raise HTTPException(status_code=404, detail=f"{key} is not tracked")
    overrides.mark_action_done(key, sig)
    title, detail = _lookup_item_text(key, "action", sig)
    closure_log.record(key, "action", "close", sig, title, detail, req.reason)
    return {"done": list(overrides.get_done_actions(key))}


@app.post("/api/tracked/{key}/actions/{sig}/for-user", dependencies=[Depends(auth.require_auth)])
async def set_action_for_user_endpoint(key: str, sig: str, body: dict) -> dict:
    if key not in tracked.list_keys():
        raise HTTPException(status_code=404, detail=f"{key} is not tracked")
    for_user = bool(body.get("for_user", False))
    overrides.set_action_for_user(key, sig, for_user)
    return {"sig": sig, "for_user": for_user}


@app.delete("/api/tracked/{key}/actions/{sig}/for-user", dependencies=[Depends(auth.require_auth)])
async def clear_action_for_user_endpoint(key: str, sig: str) -> dict:
    """Clear the manual override so the action falls back to its source flag
    (AI-determined or original manual value)."""
    if key not in tracked.list_keys():
        raise HTTPException(status_code=404, detail=f"{key} is not tracked")
    overrides.clear_action_for_user(key, sig)
    return {"ok": True}


@app.delete("/api/tracked/{key}/actions/{sig}/done", dependencies=[Depends(auth.require_auth)])
async def unmark_action_done(key: str, sig: str) -> dict:
    if key not in tracked.list_keys():
        raise HTTPException(status_code=404, detail=f"{key} is not tracked")
    overrides.unmark_action_done(key, sig)
    title, detail = _lookup_item_text(key, "action", sig)
    closure_log.record(key, "action", "reopen", sig, title, detail)
    return {"done": list(overrides.get_done_actions(key))}


# ---- Create Jira ticket from a gap or action ----------------------------


@app.post("/api/tracked/{key}/create-from-item", response_model=CreateFromItemResponse, dependencies=[Depends(auth.require_auth)])
async def create_from_item(key: str, req: CreateFromItemRequest) -> CreateFromItemResponse:
    if key not in tracked.list_keys():
        raise HTTPException(status_code=404, detail=f"{key} is not tracked")
    if not req.summary.strip():
        raise HTTPException(status_code=400, detail="summary is required")
    _require_credentials("ATLASSIAN_DOMAIN", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN")

    labels = list(req.labels or [])
    if "Commercial" not in labels:
        labels.append("Commercial")

    jira = JiraClient()
    try:
        try:
            new_key = await jira.create_issue(
                project=req.project,
                summary=req.summary.strip(),
                description=req.description or "",
                issuetype=req.issuetype or "Story",
                labels=labels,
                priority=req.priority,
                duedate=req.duedate,
                parent_key=key,  # link the new ticket under the source Epic
                components=req.components or None,
            )
        except JiraError as e:
            raise HTTPException(status_code=502, detail=f"create failed: {e}")

        transitioned = None
        if (req.issuetype or "").lower() != "epic":
            try:
                transitioned = await jira.transition_to(new_key, "Selected for Development")
            except JiraError:
                transitioned = None  # not fatal
    finally:
        await jira.aclose()

    # Auto-close the source item now that it's been turned into a real ticket.
    if req.source_type == "gap" and req.source_sig:
        overrides.dismiss_gap(key, req.source_sig)
        closure_log.record(
            key, "gap", "close", req.source_sig,
            req.summary, req.description,
            f"Created Jira ticket {new_key}",
        )
    elif req.source_type == "recommendation" and req.source_sig:
        overrides.dismiss_recommendation(key, req.source_sig)
        closure_log.record(
            key, "recommendation", "close", req.source_sig,
            req.summary, req.description,
            f"Created Jira ticket {new_key}",
        )
    elif req.source_type == "action":
        if req.source_sig:
            overrides.mark_action_done(key, req.source_sig)
            closure_log.record(
                key, "action", "close", req.source_sig,
                req.summary, req.description,
                f"Created Jira ticket {new_key}",
            )
        # If the action was manual, also remove it from the stored list to avoid
        # showing it twice (since the ticket is now the canonical artifact).
        if req.source_index is not None:
            overrides.remove_action(key, req.source_index)

    return CreateFromItemResponse(key=new_key, transitioned_to=transitioned)


# ---- Extract actions from a discussion -----------------------------------


@app.post("/api/tracked/{key}/extract-actions", response_model=ExtractedActionsResponse, dependencies=[Depends(auth.require_auth)])
async def extract_actions(key: str, req: ExtractActionsRequest) -> ExtractedActionsResponse:
    if key not in tracked.list_keys():
        raise HTTPException(status_code=404, detail=f"{key} is not tracked")
    if not req.discussion.strip():
        raise HTTPException(status_code=400, detail="discussion is empty")
    _require_credentials("ANTHROPIC_API_KEY", "ATLASSIAN_DOMAIN", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN")
    # Pull lightweight epic context so the model can ground the actions
    jira = JiraClient()
    try:
        try:
            epic, children = await jira.fetch_epic_with_children(key)
        except JiraError as e:
            raise HTTPException(status_code=502, detail=f"jira fetch failed: {e}")
    finally:
        await jira.aclose()

    from .llm import extract_actions_from_discussion  # local import to avoid cycle at startup
    try:
        return await asyncio.to_thread(
            extract_actions_from_discussion, epic, children, req.discussion
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"llm failure: {e}")


def _missing(env_key: str) -> bool:
    # Pull any external .env edits into os.environ before checking - cheap (~1 file read)
    # and lets the user update creds without restarting the server.
    settings.sync_env_from_disk()
    return not os.environ.get(env_key, "").strip()


def _require_credentials(*keys: str) -> None:
    missing = [k for k in keys if _missing(k)]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"missing credential(s): {', '.join(missing)} - set in Settings",
        )


def _compute_counts(tickets: list[TicketSnapshot]) -> StatusCounts:
    counts = StatusCounts()
    for t in tickets:
        if t.status_category == "new":
            counts.to_do += 1
        elif t.status_category == "indeterminate":
            counts.in_progress += 1
        elif t.status_category == "done":
            counts.done += 1
    counts.total = len(tickets)
    return counts


def _compute_role_split(tickets: list[TicketSnapshot]) -> Optional[RoleSplit]:
    members = team.list_members()
    if not members:
        # Without configured team there's nothing useful to split. Return None
        # so the UI can skip rendering an empty per-role section.
        return None
    split = RoleSplit()
    buckets = {
        "backend": split.backend,
        "frontend": split.frontend,
        "design": split.design,
        "other": split.other,
        "unassigned": split.unassigned,
    }
    for t in tickets:
        bucket_name = team.bucket_for_assignee(t.assignee_email, t.assignee, members)
        bucket = buckets[bucket_name]
        if t.status_category == "new":
            bucket.to_do += 1
        elif t.status_category == "indeterminate":
            bucket.in_progress += 1
        elif t.status_category == "done":
            bucket.done += 1
        bucket.total += 1
    return split


def _progress(counts: StatusCounts) -> int:
    return round(100 * counts.done / counts.total) if counts.total else 0


@app.post("/api/plan", response_model=PlanResponse, dependencies=[Depends(auth.require_auth)])
async def plan(req: PlanRequest) -> PlanResponse:
    if not req.context.strip():
        raise HTTPException(status_code=400, detail="context is empty")

    tickets_source = (req.tickets_source or "").strip()
    figma_url = (req.figma_url or "").strip()

    # Pre-flight credential checks - fail fast with a clear message before
    # any SDK call returns a cryptic auth error.
    missing: list[str] = []
    if _missing("ANTHROPIC_API_KEY"):
        missing.append("ANTHROPIC_API_KEY")
    if tickets_source:
        for k in ("ATLASSIAN_DOMAIN", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN"):
            if _missing(k):
                missing.append(k)
    if figma_url and _missing("FIGMA_API_TOKEN"):
        missing.append("FIGMA_API_TOKEN")
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"missing credential(s): {', '.join(missing)} - set in Settings",
        )

    snapshots = []
    project_components: dict[str, list[str]] = {}

    # Fetch Jira data (tickets if any + project components for KAHOOT + BACK).
    # Components are also needed in Epic-creation mode (no tickets) because the
    # BACK project requires them on create - so we need creds either way.
    if tickets_source or (
        not _missing("ATLASSIAN_DOMAIN")
        and not _missing("ATLASSIAN_EMAIL")
        and not _missing("ATLASSIAN_API_TOKEN")
    ):
        jira = JiraClient()
        try:
            if tickets_source:
                try:
                    keys = await jira.resolve_keys(tickets_source)
                except JiraError as e:
                    raise HTTPException(status_code=400, detail=f"could not resolve tickets: {e}")
                if not keys:
                    raise HTTPException(
                        status_code=400,
                        detail="no ticket keys found in tickets_source - leave empty to create a new Epic instead",
                    )
                try:
                    snapshots = await jira.fetch_tickets(keys)
                except JiraError as e:
                    raise HTTPException(status_code=502, detail=f"jira fetch failed: {e}")

            # Fetch components for both projects in parallel
            from .db import get_config as _get_cfg
            fe_proj = _get_cfg("fe_project_key", "FE")
            be_proj = _get_cfg("be_project_key", "BE")
            try:
                fe_comp, be_comp = await asyncio.gather(
                    jira.get_components(fe_proj),
                    jira.get_components(be_proj),
                )
                project_components = {fe_proj: fe_comp, be_proj: be_comp}
            except JiraError:
                # Non-fatal - model gets no component list, BE creates may fail at apply
                project_components = {}
        finally:
            await jira.aclose()

    figma_context = None
    if req.figma_url and req.figma_url.strip():
        try:
            figma = FigmaClient()
            try:
                figma_context = await figma.extract_context(req.figma_url.strip())
            finally:
                await figma.aclose()
        except FigmaError as e:
            raise HTTPException(status_code=400, detail=f"figma fetch failed: {e}")

    try:
        proposal = await asyncio.to_thread(
            build_proposal, snapshots, req.context, figma_context, project_components
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"llm failure: {e}")

    # Safety net for Epic-creation mode: if the proposal contains a new Epic in
    # KAHOOT, force every other create to point at it via parent_key. The prompt
    # already asks for this; this guard catches the rare case where the model
    # drops it for a backend child.
    epic = next(
        (c for c in proposal.creates if c.issuetype.lower() == "epic" and c.project == "KAHOOT"),
        None,
    )
    if epic is not None:
        for create in proposal.creates:
            if create.temp_id == epic.temp_id:
                continue
            if not create.parent_key:
                create.parent_key = epic.temp_id

    return PlanResponse(tickets=snapshots, proposal=proposal)


@app.post("/api/slack-reply", response_model=SlackReply, dependencies=[Depends(auth.require_auth)])
async def slack_reply(req: SlackReplyRequest) -> SlackReply:
    if not req.context.strip():
        raise HTTPException(status_code=400, detail="context is empty")
    _require_credentials("ANTHROPIC_API_KEY")
    try:
        return await asyncio.to_thread(
            generate_slack_reply, req.context, req.draft, req.audience
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"llm failure: {e}")


@app.post("/api/apply", response_model=ApplyResponse, dependencies=[Depends(auth.require_auth)])
async def apply(req: ApplyRequest) -> ApplyResponse:
    jira = JiraClient()
    outcomes: list[ApplyOutcome] = []
    temp_to_real: dict[str, str] = {}

    try:
        # 1. Creates first so links + child parent_keys can resolve temp_ids.
        #    Sort Epic-typed first so children referencing the Epic's temp_id
        #    in parent_key see it already in temp_to_real.
        sorted_creates = sorted(
            req.proposal.creates,
            key=lambda c: 0 if c.issuetype.lower() == "epic" else 1,
        )
        for create in sorted_creates:
            try:
                labels = create.labels or []
                if "Commercial" not in labels:
                    labels = [*labels, "Commercial"]
                # Resolve parent_key if it points at a sibling create's temp_id
                parent_key = create.parent_key
                if parent_key and parent_key in temp_to_real:
                    parent_key = temp_to_real[parent_key]
                key = await jira.create_issue(
                    project=create.project,
                    summary=create.summary,
                    description=create.description,
                    issuetype=create.issuetype,
                    labels=labels,
                    priority=create.priority,
                    duedate=create.duedate,
                    parent_key=parent_key,
                    components=create.components or None,
                )
                temp_to_real[create.temp_id] = key
                # Auto-transition to "Selected for Development" - team
                # convention. New tickets must not sit in "Open"/"To Do" state.
                # Skip for Epics: they have their own lifecycle and don't fit
                # this workflow.
                transition_detail = ""
                tracked_detail = ""
                if create.issuetype.lower() == "epic":
                    # Auto-track newly created Epics on the Projects Dashboard
                    # so the user doesn't have to add them manually.
                    try:
                        tracked.add(key)
                        try:
                            entry = await _fetch_one_entry(jira, key)
                            _DASHBOARD_CACHE[key] = entry
                        except Exception:
                            pass  # tracking succeeded; dashboard row will populate on next visit
                        tracked_detail = " + tracked on dashboard"
                    except Exception:
                        tracked_detail = " [WARN: could not auto-track]"
                else:
                    try:
                        await jira.transition_to(key, "Selected for Development")
                        transition_detail = " (Selected for Development)"
                    except JiraError as te:
                        transition_detail = f" [WARN: could not transition: {te}]"
                outcomes.append(ApplyOutcome(
                    kind="create",
                    ref=create.temp_id,
                    ok=True,
                    detail=f"created {key}{transition_detail}{tracked_detail}",
                ))
            except JiraError as e:
                outcomes.append(ApplyOutcome(kind="create", ref=create.temp_id, ok=False, detail=str(e)))

        # 2. Updates
        for update in req.proposal.updates:
            try:
                fields = update.fields.model_dump(exclude_none=True)
                if not fields:
                    outcomes.append(ApplyOutcome(kind="update", ref=update.key, ok=True, detail="no changes"))
                    continue
                await jira.update_issue(update.key, fields)
                outcomes.append(
                    ApplyOutcome(kind="update", ref=update.key, ok=True, detail=f"updated: {', '.join(fields)}")
                )
            except JiraError as e:
                outcomes.append(ApplyOutcome(kind="update", ref=update.key, ok=False, detail=str(e)))

        # 3. Closes
        for close in req.proposal.closes:
            try:
                name = await jira.transition_issue(close.key, close.transition_name)
                outcomes.append(ApplyOutcome(kind="close", ref=close.key, ok=True, detail=f"transitioned: {name}"))
            except JiraError as e:
                outcomes.append(ApplyOutcome(kind="close", ref=close.key, ok=False, detail=str(e)))

        # 4. Links - resolve temp_ids; skip if a ref is still a temp_id (no
        #    matching create) so we don't 404 on Jira with a non-key string.
        import re
        JIRA_KEY = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")

        def resolve(ref: str) -> str:
            return temp_to_real.get(ref, ref)

        for link in req.proposal.links:
            inward = resolve(link.from_ref)
            outward = resolve(link.to_ref)
            ref_label = f"{inward} {link.type} {outward}"

            unresolved = [r for r in (inward, outward) if not JIRA_KEY.match(r)]
            if unresolved:
                outcomes.append(
                    ApplyOutcome(
                        kind="link",
                        ref=ref_label,
                        ok=False,
                        detail=f"skipped - unresolved temp_id(s): {', '.join(unresolved)} (the referenced create was removed or failed)",
                    )
                )
                continue

            try:
                await jira.create_link(inward, outward, link.type)
                outcomes.append(
                    ApplyOutcome(kind="link", ref=ref_label, ok=True, detail="linked")
                )
            except JiraError as e:
                outcomes.append(
                    ApplyOutcome(kind="link", ref=ref_label, ok=False, detail=str(e))
                )
    finally:
        await jira.aclose()

    # Record what the user actually shipped vs what Claude proposed, so we can
    # refine the system prompt over time. Best-effort, never blocks apply.
    if req.original_proposal is not None:
        outcomes_summary = {
            "ok": sum(1 for o in outcomes if o.ok),
            "failed": sum(1 for o in outcomes if not o.ok),
        }
        edit_log.record(
            original=req.original_proposal,
            final=req.proposal,
            context_excerpt=req.context_excerpt,
            outcomes_summary=outcomes_summary,
        )

    return ApplyResponse(outcomes=outcomes)
