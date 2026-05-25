from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class IssueLinkRef(BaseModel):
    type: str
    direction: Literal["inward", "outward"]
    key: str


class TicketSnapshot(BaseModel):
    key: str
    project: str
    summary: str
    description: Optional[str] = None
    status: str
    status_category: str
    issuetype: str
    priority: Optional[str] = None
    assignee: Optional[str] = None
    assignee_email: Optional[str] = None
    labels: list[str] = Field(default_factory=list)
    duedate: Optional[str] = None
    updated: Optional[str] = None
    parent_key: Optional[str] = None
    links: list[IssueLinkRef] = Field(default_factory=list)
    modifiable: bool = True


class ProposedFields(BaseModel):
    summary: Optional[str] = None
    description: Optional[str] = None
    duedate: Optional[str] = None
    labels: Optional[list[str]] = None
    priority: Optional[str] = None


class ProposedUpdate(BaseModel):
    key: str
    fields: ProposedFields
    reasoning: str


class ProposedCreate(BaseModel):
    temp_id: str = Field(..., description="Stable id within proposal; used for cross-links")
    project: Literal["KAHOOT", "BACK"]
    summary: str
    description: str
    issuetype: str = "Story"
    labels: list[str] = Field(default_factory=lambda: ["Commercial"])
    priority: Optional[str] = "Major"
    duedate: Optional[str] = None
    parent_key: Optional[str] = None
    components: list[str] = Field(
        default_factory=list,
        description="Jira component names. Required for BACK project; pick from the available list provided in the user message.",
    )
    reasoning: str


class ProposedClose(BaseModel):
    key: str
    reasoning: str
    transition_name: Optional[str] = Field(
        default=None,
        description='Preferred transition name (e.g. "Won\'t Do", "Cancelled"). Resolved at apply time.',
    )


class ProposedLink(BaseModel):
    from_ref: str = Field(..., description="Existing key (e.g. FE-1) or proposal temp_id")
    to_ref: str
    type: Literal["Blocks", "Relates"] = "Blocks"
    reasoning: str


class Proposal(BaseModel):
    updates: list[ProposedUpdate] = Field(default_factory=list)
    creates: list[ProposedCreate] = Field(default_factory=list)
    closes: list[ProposedClose] = Field(default_factory=list)
    links: list[ProposedLink] = Field(default_factory=list)
    notes: Optional[str] = Field(
        default=None,
        description="Free-form notes from the model: assumptions, things skipped, follow-ups",
    )


class PlanRequest(BaseModel):
    tickets_source: Optional[str] = Field(
        default=None,
        description="Epic URL / JQL / comma- or newline-separated keys. Empty → create a new Epic in KAHOOT plus child tickets.",
    )
    context: str = Field(..., description="Meeting summary, requirements, conversation, etc.")
    figma_url: Optional[str] = Field(
        default=None,
        description="Optional Figma file URL - content fetched and injected into context for the model.",
    )


class PlanResponse(BaseModel):
    tickets: list[TicketSnapshot]
    proposal: Proposal


class ApplyRequest(BaseModel):
    proposal: Proposal
    original_proposal: Optional[Proposal] = Field(
        default=None,
        description="Untouched proposal returned by /api/plan. If provided, server diffs against `proposal` and appends an edit-log entry. Optional for backwards compatibility.",
    )
    context_excerpt: Optional[str] = Field(
        default=None,
        description="First ~200 chars of the original context, for searchability in the edit log.",
    )


class ApplyOutcome(BaseModel):
    kind: Literal["update", "create", "close", "link"]
    ref: str
    ok: bool
    detail: str


class ApplyResponse(BaseModel):
    outcomes: list[ApplyOutcome]


class SettingsUpdate(BaseModel):
    ANTHROPIC_API_KEY: Optional[str] = None
    ATLASSIAN_DOMAIN: Optional[str] = None
    ATLASSIAN_EMAIL: Optional[str] = None
    ATLASSIAN_API_TOKEN: Optional[str] = None
    FIGMA_API_TOKEN: Optional[str] = None
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None


# ---------------------------------------------------------------------------
# Epic tracking / status dashboard
# ---------------------------------------------------------------------------


Urgency = Literal["high", "medium", "low"]


class ActionItem(BaseModel):
    title: str
    detail: str
    ticket_keys: list[str] = Field(default_factory=list)
    urgency: Urgency = "medium"
    for_user: bool = Field(
        default=False,
        description="True if this action is specifically for the current user (matched by ATLASSIAN_EMAIL).",
    )
    source: Literal["ai", "manual"] = Field(
        default="ai",
        description="`ai` if Claude generated it, `manual` if the user added it directly or approved it from a discussion-extract.",
    )
    sig: Optional[str] = Field(
        default=None,
        description="Stable 12-char signature (title+detail). Used to persist done state across re-analyses.",
    )
    done: bool = Field(default=False, description="Server-side flag, set when the action's sig is in epic_overrides.actions_done.")


class Risk(BaseModel):
    title: str
    detail: str
    ticket_keys: list[str] = Field(default_factory=list)
    severity: Urgency = "medium"
    sig: Optional[str] = Field(
        default=None,
        description="Stable 12-char signature (computed from title+detail) used by the dismiss feature.",
    )
    dismissed: bool = Field(default=False, description="Set by server when the user has dismissed this risk.")


class Gap(BaseModel):
    title: str
    detail: str
    suggested_summary: Optional[str] = Field(
        default=None,
        description='If the gap should become a new ticket, propose its summary (one line) - UI may offer "Create in Sync".',
    )
    suggested_project: Optional[Literal["KAHOOT", "BACK"]] = None
    sig: Optional[str] = Field(default=None, description="Stable 12-char signature; mirrors Risk.sig usage.")
    dismissed: bool = Field(default=False, description="Set by server when the user has dismissed this gap.")


class CloseRequest(BaseModel):
    reason: Optional[str] = Field(
        default=None,
        description="User's free-text explanation for closing. Captured to closure_log.jsonl so the prompt can be refined.",
    )


class LoginRequest(BaseModel):
    password: str


# ---------------------------------------------------------------------------
# Ideas (pre-project capture)
# ---------------------------------------------------------------------------


IdeaStatus = Literal["exploring", "parked", "queued", "promoted", "dropped"]
IDEA_STATUSES: tuple[str, ...] = ("exploring", "parked", "queued", "promoted", "dropped")


class Idea(BaseModel):
    id: str
    title: str
    notes: str = ""
    one_pager_url: Optional[str] = None  # legacy; superseded by documents
    stakeholder: Optional[str] = None
    status: IdeaStatus = "exploring"
    position: int = 0  # ordering within a column; lower = top
    created_at: str
    updated_at: str
    promoted_epic_key: Optional[str] = None  # set when promoted via /promote
    documents: list[Document] = Field(default_factory=list)


class IdeasResponse(BaseModel):
    ideas: list[Idea]


class DocumentInput(BaseModel):
    """Slim input variant for clients - we only need url + optional label.
    Server fills in kind / cached_text / cached_at / fetch_error during save."""
    url: str
    label: Optional[str] = None


class CreateIdeaRequest(BaseModel):
    title: str
    notes: str = ""
    one_pager_url: Optional[str] = None
    stakeholder: Optional[str] = None
    status: IdeaStatus = "exploring"
    documents: Optional[list[DocumentInput]] = None


class UpdateIdeaRequest(BaseModel):
    title: Optional[str] = None
    notes: Optional[str] = None
    one_pager_url: Optional[str] = None
    stakeholder: Optional[str] = None
    status: Optional[IdeaStatus] = None
    promoted_epic_key: Optional[str] = None
    documents: Optional[list[DocumentInput]] = None


class ReorderEntry(BaseModel):
    id: str
    status: IdeaStatus
    position: int


class ReorderIdeasRequest(BaseModel):
    order: list[ReorderEntry] = Field(
        ..., description="New status + position for each affected idea. Server overlays this onto the existing store."
    )


class Recommendation(BaseModel):
    title: str
    detail: str
    ticket_keys: list[str] = Field(default_factory=list)


class EpicAnalysis(BaseModel):
    state_of_play: str = Field(..., description="2-4 sentence summary of where the initiative stands.")
    progress_assessment: Literal["on-track", "at-risk", "behind", "ahead", "stalled", "unknown"]
    stakeholder_summary: Optional[str] = Field(
        default=None,
        description="1-2 sentence non-technical summary aimed at executive stakeholders. No ticket keys / jargon. Powers the PPR (Project Portfolio Review) tab.",
    )
    action_items: list[ActionItem] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)


class StatusCounts(BaseModel):
    to_do: int = 0
    in_progress: int = 0
    done: int = 0
    total: int = 0


TeamRole = Literal["backend", "frontend", "design", "other"]
ROLE_BUCKETS = ("backend", "frontend", "design", "other", "unassigned")


class TeamMember(BaseModel):
    name: str
    email: Optional[str] = None
    role: TeamRole = "other"


class TeamMembersResponse(BaseModel):
    members: list[TeamMember]


class RoleSplit(BaseModel):
    backend: StatusCounts = Field(default_factory=StatusCounts)
    frontend: StatusCounts = Field(default_factory=StatusCounts)
    design: StatusCounts = Field(default_factory=StatusCounts)
    other: StatusCounts = Field(default_factory=StatusCounts)
    unassigned: StatusCounts = Field(default_factory=StatusCounts)


class Document(BaseModel):
    url: str
    label: Optional[str] = None  # human-readable name; falls back to URL if missing
    kind: Literal["confluence", "figma", "google-drive", "other"] = "other"
    cached_text: Optional[str] = None
    cached_at: Optional[str] = None
    fetch_error: Optional[str] = None


Segment = Literal["business", "school", "home", "students"]
SEGMENTS: tuple[str, ...] = ("business", "school", "home", "students")


class EpicMetadata(BaseModel):
    one_pager_url: Optional[str] = None  # legacy; superseded by documents
    stakeholder: Optional[str] = None
    idea_id: Optional[str] = Field(default=None, description="ID of the source Idea (if this Epic was promoted from one).")
    documents: list[Document] = Field(default_factory=list)
    segments: list[Segment] = Field(
        default_factory=list,
        description="Which audience segments will benefit. Multi-select: business, school, home, students.",
    )


class EpicDashboardEntry(BaseModel):
    key: str
    summary: str
    status: str
    status_category: str
    duedate: Optional[str] = None
    counts: StatusCounts
    progress_pct: int = 0  # 0-100, integer for the bar
    last_synced: Optional[str] = None  # ISO timestamp of last Jira fetch for this row
    role_split: Optional[RoleSplit] = None
    assessment: Optional[Literal["on-track", "at-risk", "behind", "ahead", "stalled", "unknown"]] = Field(
        default=None,
        description="Mirror of EpicAnalysis.progress_assessment when an analysis has been computed for this epic. Lets the dashboard card match the detail view.",
    )
    open_action_count: Optional[int] = Field(
        default=None,
        description="Number of action items that are not yet marked done (manual + AI combined). None when no analysis has been computed yet.",
    )
    for_user_action_count: Optional[int] = Field(
        default=None,
        description="Subset of open_action_count flagged for the current user.",
    )
    metadata: Optional[EpicMetadata] = None


class EpicDashboardResponse(BaseModel):
    entries: list[EpicDashboardEntry]
    last_synced: Optional[str] = None  # ISO timestamp of most recent full refresh


class EpicDetail(BaseModel):
    epic: TicketSnapshot
    tickets: list[TicketSnapshot]
    counts: StatusCounts
    progress_pct: int
    analysis: EpicAnalysis
    analyzed_at: str  # ISO timestamp
    role_split: Optional[RoleSplit] = None
    metadata: Optional[EpicMetadata] = None


class AddActionRequest(BaseModel):
    title: str
    detail: str = ""
    urgency: Urgency = "medium"
    ticket_keys: list[str] = Field(default_factory=list)
    for_user: bool = False


class ExtractActionsRequest(BaseModel):
    discussion: str = Field(..., description="Free-form text: meeting notes, Slack thread, etc.")


class ExtractedActionsResponse(BaseModel):
    proposed: list[ActionItem]
    notes: Optional[str] = None


class CreateFromItemRequest(BaseModel):
    project: Literal["KAHOOT", "BACK"] = "KAHOOT"
    summary: str
    description: str = ""
    issuetype: str = "Story"
    labels: list[str] = Field(default_factory=lambda: ["Commercial"])
    priority: Optional[str] = "Major"
    duedate: Optional[str] = None
    components: list[str] = Field(default_factory=list)
    # Origin tracking so we can mark the source item closed
    source_type: Literal["gap", "action"]
    source_sig: Optional[str] = None
    source_index: Optional[int] = None  # for manual-action source: actions_added index


class CreateFromItemResponse(BaseModel):
    key: str
    transitioned_to: Optional[str] = None


class TrackRequest(BaseModel):
    key: str = Field(..., description='Epic key, e.g. "FE-101"')


class ReorderTrackedRequest(BaseModel):
    keys: list[str] = Field(..., description="Epic keys in the new display order.")


class ActionItemForList(ActionItem):
    """ActionItem extended with extra fields the Actions tab needs."""
    manual_index: Optional[int] = Field(
        default=None,
        description="Index in epic_overrides.actions_added when source=='manual'; lets the UI hit DELETE /api/tracked/{key}/actions/{index}.",
    )


class ActionsGroup(BaseModel):
    epic_key: str
    epic_summary: str
    epic_assessment: Optional[str] = None  # so the section header can show status
    actions: list[ActionItemForList]


class ActionsResponse(BaseModel):
    groups: list[ActionsGroup]


# ---------------------------------------------------------------------------
# PPR (Project Portfolio Review)
# ---------------------------------------------------------------------------


PPRStage = Literal["preparation", "development", "recently_completed"]


class PPRProject(BaseModel):
    key: str
    summary: str
    progress_pct: int
    counts: StatusCounts
    duedate: Optional[str] = None
    segments: list[Segment] = Field(default_factory=list)
    assessment: Optional[str] = None
    stakeholder_summary: Optional[str] = Field(
        default=None,
        description="Short stakeholder-facing summary. Prefers EpicAnalysis.stakeholder_summary; falls back to the first sentence of state_of_play; None if no analysis cached yet.",
    )


class PPRGroup(BaseModel):
    stage: PPRStage
    label: str
    projects: list[PPRProject]


class PPRResponse(BaseModel):
    groups: list[PPRGroup]
    recent_window_days: int = 60


# ---------------------------------------------------------------------------
# Slack reply generator
# ---------------------------------------------------------------------------


Audience = Literal["c-suite", "other"]


class SlackReplyRequest(BaseModel):
    context: str = Field(..., description="Thread, doc, or anything that frames the reply.")
    draft: Optional[str] = Field(
        default=None, description="User's draft of what they want to say - may be terse or in Polish."
    )
    audience: Audience = "other"


class SlackReply(BaseModel):
    message: str = Field(..., description="Ready-to-paste Slack reply, English, team tone.")
    notes: Optional[str] = Field(
        default=None,
        description="Brief notes on tone choices, assumptions, or alternative phrasings - optional.",
    )
