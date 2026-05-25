# Helm

An agentic product-management cockpit that connects Jira, Confluence, Figma, and
Google Drive with Claude (Anthropic) to keep an epic-level program coherent.
Built for a single PM running a small portfolio.

Five tabs:

- **Projects Dashboard** — tracked epics with AI analysis (state-of-play, risks,
  gaps, action items, recommendations), per-role progress split, drag-drop
  ordering, background refresh + confirm-to-swap banner.
- **PPR** (Project Portfolio Review) — three-bucket lifecycle view (preparation
  / development / recently completed) with stakeholder-flavored one-line
  summaries. Designed for a tight 10-minute exec walk-through.
- **Actions** — all open action items across the portfolio, grouped by project,
  filterable by "only mine" and "show done".
- **Ideas** — kanban capture (exploring / parked / queued / promoted / dropped)
  for stakeholder ideas with multi-doc attachments. Promote an idea → jumps to
  Manage requirements with title + notes + cached doc bodies prefilled.
- **Manage requirements** — paste meeting notes / requirements; Claude proposes
  ticket creates / updates / closes / links; you review and approve in an
  editable preview before anything writes to Jira. Edit log persists for prompt
  refinement.
- **Slack reply** — draft polished replies in the team tone, C-suite vs peer
  audience-aware.

## Architecture

- **Backend**: Python 3.11+, FastAPI, SQLite (single `helm.db` file at project
  root). One persistent SQLite connection in WAL mode. Auth via password +
  HttpOnly session cookie.
- **Frontend**: vanilla JS + plain CSS (no build step). Served as static files
  from FastAPI. Drag-drop is native HTML5.
- **LLM**: Anthropic SDK (`messages.parse` with Pydantic schemas). System
  prompts live as editable Markdown files in `prompts/` and template in
  company-specific values from the `app_config` table at runtime — no restart
  needed when prompts change.
- **Integrations**: Atlassian REST API v3 (Jira + Confluence on the same site),
  Figma API, Google Drive + Slides via OAuth 2.

## Setup

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in tokens + APP_PASSWORD
```

Edit `.env` — see comments inside for what each value is.

`ATLASSIAN_API_TOKEN` — generate at <https://id.atlassian.com/manage-profile/security/api-tokens>

`FIGMA_API_TOKEN` (optional) — <https://www.figma.com/developers/api#access-tokens>

Google Drive (optional) — set up via the in-app Settings tab (OAuth client
walkthrough in a modal).

## Run

```sh
source .venv/bin/activate
uvicorn app.main:app --reload --port 8765
```

Open <http://localhost:8765>. Log in with the `APP_PASSWORD` you set in `.env`.

## Configure for your team

After the first start, open the **Settings** tab and set:

- **Company config**: name, frontend project key, backend project key,
  required label, business context blurb.
- **Team members**: add stakeholders with name + role + (optional) email. The
  LLM prompts will use these names when proposing assignments and framing
  observations.

Settings persists to the `app_config` and `team_members` tables in `helm.db`.

## Editing the prompts

System prompts live in `prompts/*.md` as plain Markdown with optional
Claude-skill-style frontmatter. Edit any file with your editor — the change
takes effect on the next LLM call (no restart). The files can also be used
as standalone Claude Skills by copying them into `~/.claude/skills/`.

Placeholders in the prompts (filled at runtime from `app_config` +
`team_members`):

- `{company_name}` — your company / team name
- `{fe_project}` / `{be_project}` — Jira project keys
- `{required_label}` — label that every ticket must carry
- `{business_context}` — short blurb about current focus / active initiatives
- `{stakeholders}` — bullet list of team members from the DB

## Data layout

| File | What |
| --- | --- |
| `helm.db` (gitignored) | All persistent state: tracked epics, ideas, team, overrides, metadata, LLM cache, edit + closure logs, app config. |
| `.env` (gitignored) | Credentials + password. |
| `prompts/*.md` (in repo) | Editable LLM system prompts. |
| `app/*.py` (in repo) | FastAPI app + storage modules + LLM helpers. |
| `app/static/*` (in repo) | Frontend bundle (HTML / CSS / JS). |

## License

Personal project; no license declared. Treat as source-available reference for
how to glue an AI cockpit onto Jira + Confluence + Figma + Drive.
