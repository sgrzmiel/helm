# Helm prompts

System prompts used by the LLM helpers in `app/llm.py`. Each file is a Markdown
document with optional Claude-skill-style frontmatter at the top - the
application strips the frontmatter and uses the body as the system prompt.

## Files

- `jira-proposal.md` - powers the Manage requirements flow. Converts free-form
  context + (optional) existing tickets into a Jira proposal (creates / updates
  / closes / links).
- `analyze-epic.md` - powers the Projects Dashboard analysis. Reads an Epic
  and its children, returns state_of_play / stakeholder_summary /
  progress_assessment / action_items / risks / gaps / recommendations.
- `extract-actions.md` - powers the "Extract from discussion" affordance on an
  Epic detail. Turns meeting notes / chat history into a list of proposed
  ActionItems for review.
- `slack-reply.md` - powers the Slack reply tab. Drafts a polished reply tuned
  to the audience (c-suite vs peer).

## Placeholders

Each prompt may reference these tokens, which are filled in at runtime from
the `app_config` table in `helm.db`:

| Token | Source (app_config key) | Example |
| --- | --- | --- |
| `{company_name}` | `company_name` | `Acme Inc.` |
| `{fe_project}` | `fe_project_key` | `FE` |
| `{be_project}` | `be_project_key` | `BE` |
| `{required_label}` | `required_label` | `Commercial` |
| `{business_context}` | `business_context` | one-liner about current focus |
| `{stakeholders}` | derived from `team_members` table | `- Jane Doe (backend), John Roe (frontend)...` |

Edit `app_config` values via the Settings tab in the UI (no restart needed -
prompts are re-read on every call).

## Editing

Edit any `.md` file with your favorite editor and save. The next API call
picks up the new content immediately - no server restart required.

You can also use these files as standalone Claude Skills. Copy them into
`~/.claude/skills/<name>/SKILL.md` if you want them available outside Helm.
