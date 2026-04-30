# ai-agent-autoimprove

This repo is a Postgres-backed feedback loop for improving AI coding skills from past sessions.

It now has three real command stages:

1. `harvest`: ingest Claude and Codex sessions into Postgres and write normalized transcripts
2. `summarize`: generate one structured factual summary per session with OpenAI
3. `refresh`: run `harvest` first, then summarize the harvested sessions

There is intentionally no public `analyze` command in v1. The `autoimprove` skill is the analysis layer, and this repo provides its evidence base plus internal persistence helpers.

For multi-machine Claude session handoff, see [SYNCTHING.md](/Users/johnwu/code/ai-agent-autoimprove/SYNCTHING.md).  
For Codex session syncing, see [CODEX_SYNCTHING.md](/Users/johnwu/code/ai-agent-autoimprove/CODEX_SYNCTHING.md).  
For shared Postgres setup, see [POSTGRES.md](/Users/johnwu/code/ai-agent-autoimprove/POSTGRES.md).

## What Exists

- [harvest.py](/Users/johnwu/code/ai-agent-autoimprove/harvest.py): Claude + Codex session ingestion
- [summarize.py](/Users/johnwu/code/ai-agent-autoimprove/summarize.py): OpenAI-backed per-session summaries
- [refresh.py](/Users/johnwu/code/ai-agent-autoimprove/refresh.py): orchestration for `harvest + summarize`
- [db.py](/Users/johnwu/code/ai-agent-autoimprove/db.py): schema, URL resolution, queries, and upserts
- [autoimprove_helpers.py](/Users/johnwu/code/ai-agent-autoimprove/autoimprove_helpers.py): internal cohort selection, evidence loading, report rendering, and persistence helpers for the `autoimprove` skill
- [session_utils.py](/Users/johnwu/code/ai-agent-autoimprove/session_utils.py): transcript paths and rendering
- [config/op.envmap](/Users/johnwu/code/ai-agent-autoimprove/config/op.envmap): committed 1Password reference map
- [skills/autoimprove/SKILL.md](/Users/johnwu/code/ai-agent-autoimprove/skills/autoimprove/SKILL.md): the canonical analysis workflow

## Requirements

- Python 3.12+
- `uv`
- `just`
- `op`
- `psql` for interactive SQL work
- local Claude history in `~/.claude/projects`
- local Codex history in `~/.codex/sessions` and/or `~/.codex/archived_sessions`
- `OPENAI_API_KEY` available through `config/op.envmap`
- `OPENAI_MODEL` exported in your shell or passed with `--model`

## Quick Start

```bash
cd /Users/johnwu/code/ai-agent-autoimprove
just schema-init
just harvest
just summarize --model gpt-5.4-mini
```

Or run the full loop:

```bash
just refresh --model gpt-5.4-mini
```

Useful commands:

```bash
just load-dev-token
just setup-env
just database-url
just database-url-admin
just psql
just psql-admin
just schema-init
just reset-db
just harvest
just summarize --model gpt-5.4-mini
just refresh --model gpt-5.4-mini
```

## 1Password Runtime Auth

Normal runtime commands (`harvest`, `summarize`, `refresh`, `schema-init`, `database-url`, `psql`) read from `config/op.envmap`, which is scoped to the `ai-agent-army-dev` vault (`ihqmf2zd73upmihfnh4o4t2tam`).

For non-interactive use, seed the dev service-account token once:

```bash
just load-dev-token
```

That writes `AI_AGENT_ARMY_DEV_SERVICE_ACCOUNT_TOKEN` to local `.env` (`.gitignore`d). After that, `just harvest` and the other runtime commands can resolve 1Password refs through the service account. `just setup-env` is optional; it materializes runtime secrets into `.env` for tools that do not call `op run` themselves.

Admin-only commands (`database-url-admin`, `psql-admin`, `reset-db`) still require interactive access to the `ai-agent-army` vault because they use `config/op-admin.envmap`.

## Command UX

`just harvest`
- wraps `uv run harvest`
- ingests Claude and Codex by default
- writes `conversations` rows and transcripts under `transcripts/<source_app>/...`

Examples:

```bash
just harvest --source codex
just harvest --since 2026-04-01 --until 2026-04-21
just harvest --claude-source ~/conversation-archive
```

`just summarize`
- wraps `uv run summarize`
- selects sessions from Postgres
- skips existing summaries unless `--force`
- stores one factual row per session in `summaries`

Examples:

```bash
just summarize --model gpt-5.4-mini
just summarize --source claude --query postgres --limit 20 --model gpt-5.4-mini
just summarize --include-subagents --force --model gpt-5.4-mini
```

`just refresh`
- wraps `uv run refresh`
- harvests first
- then summarizes the harvested sessions

Examples:

```bash
just refresh --model gpt-5.4-mini
just refresh --source codex --since 2026-04-01 --query reviewer --model gpt-5.4-mini
```

## Data Model

`conversations`
- source-qualified session ids, e.g. `claude:...` and `codex:...`
- metadata for both Claude and Codex sessions
- `source_app`, `native_session_id`, `agent_role`, and `agent_name`

`summaries`
- `summary`
- `goal`
- `outcome`
- `issues`
- `struggles`
- `user_corrections`
- `resolution_status`
- `tags`

`analysis_runs`
- one row per completed `autoimprove` skill analysis
- stores query/filter metadata, the rendered report, and whether research was performed

`improvements`
- one row per proposed recommendation emitted by the `autoimprove` skill
- supports `skill`, `cli`, and `tool` recommendations with evidence session ids and web references

## Inspect The Results

Open SQL:

```bash
just psql
```

Example query:

```sql
SELECT
  session_id,
  source_app,
  project,
  started_at,
  friction_score,
  substr(first_user_message, 1, 80) AS first_msg
FROM conversations
WHERE COALESCE(is_subagent, 0) = 0
ORDER BY NULLIF(started_at, '')::timestamptz DESC
LIMIT 20;
```

Example summary query:

```sql
SELECT
  c.session_id,
  c.source_app,
  s.goal,
  s.outcome,
  s.struggles,
  s.user_corrections,
  s.resolution_status,
  s.tags
FROM conversations c
JOIN summaries s ON s.session_id = c.session_id
ORDER BY NULLIF(c.started_at, '')::timestamptz DESC
LIMIT 20;
```

Transcript layout:

```text
transcripts/claude/<native_session_id>.md
transcripts/claude/<parent_native_session_id>/<child_native_session_id>.md
transcripts/codex/<native_session_id>.md
transcripts/codex/<parent_native_session_id>/<child_native_session_id>.md
```

## Notes

- `db.py` is the schema source of truth.
- `just reset-db` drops and recreates the Postgres schema from scratch.
- `summarize` is intentionally per-session and factual; it does not propose skill edits.
- `autoimprove` is intentionally the only analysis surface in v1; there is no `just analyze` or `uv run analyze`.
- `skills/autoimprove/SKILL.md` reads from `summaries` first, then drills into transcripts and persists `analysis_runs` plus `improvements`.
- `main.py` is not part of the real workflow.
