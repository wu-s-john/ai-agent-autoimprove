---
name: autoimprove
description: Analyze summarized AI coding sessions, classify recurring problems as skill, CLI, or tool gaps, and persist evidence-backed recommendations.
tools: Read, Grep, Glob, Bash, Edit, Write
---

# Autoimprove

Use this skill after `/Users/johnwu/code/ai-agent-autoimprove` has already run `refresh`.

This is the only intended analysis surface in v1. There is no public `analyze` command.

## What This Skill Does

Read summarized AI coding sessions, find recurring patterns, and produce recommendations in three tracks:

- `Skill Improvements`
- `CLI Opportunities`
- `External Tool Recommendations`

Use only observable evidence:

- session summaries
- transcript commentary
- command and tool usage
- retries and churn
- user corrections

Do not infer hidden chain-of-thought.

## Key Paths

- **Repo root**: `/Users/johnwu/code/ai-agent-autoimprove`
- **Internal helper module**: `/Users/johnwu/code/ai-agent-autoimprove/autoimprove_helpers.py`
- **Postgres access**: `just database-url` or `just psql`
- **Transcripts**: `/Users/johnwu/code/ai-agent-autoimprove/transcripts/`
- **Shared skills directory**: `/Users/johnwu/code/ai-agent-army/claude/skills/`
- **Raw JSONL**: `conversations.file_path`

## Database Shape

### `conversations`

Important fields:

- `session_id`
- `source_app`
- `native_session_id`
- `project`
- `cwd`
- `model`
- `started_at`
- `duration_minutes`
- `friction_score`
- `efficiency_score`
- `complexity_score`
- `detected_skill`
- `first_user_message`
- `file_path`
- `is_subagent`
- `parent_session_id`
- `agent_role`
- `agent_name`

### `summaries`

This is the primary filter surface.

- `summary`
- `goal`
- `outcome`
- `issues`
- `struggles`
- `user_corrections`
- `resolution_status`
- `tags`
- `model_used`

### `analysis_runs`

One row per completed analysis.

### `improvements`

One row per persisted recommendation.

## Workflow

### 1. Start with summaries

Never start by reading every transcript. Select a cohort from `summaries` first.

Use the helper module when possible:

```bash
op run --env-file config/op.envmap -- uv run python - <<'PY'
from autoimprove_helpers import select_analysis_cohort

rows = select_analysis_cohort(query="postgres", limit=20)
for row in rows[:5]:
    print(row["session_id"], row["struggles"], row["tags"])
PY
```

The query surface includes:

- `project`
- `cwd`
- `detected_skill`
- `first_user_message`
- `summary`
- `struggles`
- `user_corrections`
- `tags`

### 2. Prioritize the best evidence sessions

Prefer cohorts that show:

- repeated struggles across multiple sessions
- high friction with low or medium complexity
- repeated `user_corrections`
- unresolved or partially resolved sessions
- recurring tags, skills, or workflow shapes

Use `group_recurring_patterns()` when you want a quick clustering pass.

### 3. Read transcripts only for evidence

Once the cohort is selected, inspect only the most relevant transcripts.

Use the helper:

```bash
op run --env-file config/op.envmap -- uv run python - <<'PY'
from autoimprove_helpers import load_session_evidence, select_analysis_cohort

row = select_analysis_cohort(query="postgres", limit=1)[0]
evidence = load_session_evidence(row)
print(evidence["transcript_path"])
print(evidence["transcript_text"][:1200])
PY
```

Use raw JSONL only if you need missing tool details.

### 4. Classify the recurring issue

For each recurring pattern, classify it as:

- `skill`
- `cli`
- `tool`
- `mixed`

Interpretation:

- `skill`: the agent needed better instructions, heuristics, or sequencing
- `cli`: the agent needed a better internal command-line workflow or helper CLI
- `tool`: the agent needed an external tool or official integration it did not know to use
- `mixed`: both instruction and tooling gaps are present

If a finding is `mixed`, split it into multiple persisted recommendations before storing it. The database stores only `skill`, `cli`, or `tool` rows.

### 5. Research external tools only when justified

Do not browse for pure skill gaps.

Browse the web only when:

- a `cli` or `tool` gap repeats across sessions, or
- one session shows clearly high wasted effort that a tool could remove

When browsing:

- prefer official docs
- prefer official repos
- prefer primary project pages over blogs or listicles
- record the supporting URLs for each external tool recommendation

### 6. Produce exactly four sections

Every completed analysis must return these sections in this order:

- `Observed Patterns`
- `Skill Improvements`
- `CLI Opportunities`
- `External Tool Recommendations`

Recommendations should stay high-level in v1:

- what the missing capability is
- why it would help
- evidence sessions
- why the current skill or tooling is insufficient
- supporting URLs for external tools

Do not apply changes or edit shared skills until the user explicitly approves them.

### 7. Persist the analysis automatically

Use `persist_analysis_artifacts()` to store:

- one `analysis_runs` row
- one `improvements` row per recommendation

Example:

```bash
op run --env-file config/op.envmap -- uv run python - <<'PY'
from autoimprove_helpers import (
    ImprovementProposal,
    persist_analysis_artifacts,
    render_analysis_report,
    select_analysis_cohort,
)

rows = select_analysis_cohort(query="postgres", limit=5)
report = render_analysis_report({
    "Observed Patterns": [
        "Repeated RDS connectivity debugging consumed multiple sessions.",
    ],
    "Skill Improvements": [
        "Add a networking checklist before retrying Postgres connection commands.",
    ],
    "CLI Opportunities": [
        "Provide a small connectivity-check CLI for RDS reachability and auth.",
    ],
    "External Tool Recommendations": [
        "Research official AWS connectivity diagnostics and Postgres client checks.",
    ],
})

run_id = persist_analysis_artifacts(
    report_markdown=report,
    cohort_rows=rows,
    query_text="postgres",
    filters={"query": "postgres", "limit": 5},
    research_performed=True,
    recommendations=[
        ImprovementProposal(
            improvement_type="skill",
            target_name="postgres-debugging",
            description="Add an RDS networking triage checklist.",
            rationale="Multiple sessions wasted turns on connectivity assumptions before checking SG and reachability.",
            evidence_session_ids=[row["session_id"] for row in rows[:2]],
        ),
        ImprovementProposal(
            improvement_type="cli",
            target_name="rds-connectivity-check",
            description="Create a CLI helper that tests DNS, TCP reachability, and psql auth preconditions.",
            rationale="The same manual checks were repeated across sessions.",
            evidence_session_ids=[row["session_id"] for row in rows[:2]],
        ),
    ],
)
print(run_id)
PY
```

## Rules

- Use summaries as the search surface and transcripts as evidence.
- Prefer recurring patterns over one-off noise.
- Always cite concrete sessions.
- Keep recommendations high-level unless the user asks for detailed implementation.
- Persist proposals automatically, but do not edit shared skills or implement tooling changes without approval.
