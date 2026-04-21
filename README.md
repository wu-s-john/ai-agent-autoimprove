# ai-agent-autoimprove

This repo is a small feedback loop for improving Claude Code skills from past conversations.

Right now it does two practical things:

1. Harvest Claude Code conversation logs from `~/.claude/projects/`
2. Turn them into a database index plus condensed Markdown transcripts

It also includes a draft skill spec at `skills/autoimprove/SKILL.md` that describes how an agent should analyze those indexed conversations and propose skill improvements.

For multi-machine Claude session handoff, see `SYNCTHING.md`.
For multi-machine Codex session syncing without SQLite state, see `CODEX_SYNCTHING.md`.
For shared Postgres setup via 1Password + `just`, see `POSTGRES.md`.

## What Exists Today

- `harvest.py`: parses Claude JSONL conversations and writes structured data
- `db.py`: creates the database schema and upserts conversation rows
- `config/op.envmap`: committed 1Password reference map for Postgres credentials
- `justfile`: operational commands for building URLs, opening `psql`, initializing schema, and harvesting
- `init_schema.py`: tiny wrapper that runs the idempotent schema bootstrap from `db.py`
- `transcripts/`: generated condensed Markdown transcripts
- `skills/autoimprove/SKILL.md`: manual/agent workflow for analyzing the indexed data

The broad `autoimprove` CLI discussed in the earlier Claude session is not fully implemented yet. There is no working `list`, `show`, `summarize`, `analyze`, or `improve` command in this repo today.

## Requirements

- Python 3.12+
- `uv`
- `just`
- `op` if you want to use the Postgres + 1Password flow
- `psql` if you want interactive Postgres access from the shell
- local Claude conversation history in `~/.claude/projects`

## Quick Start

### Postgres + 1Password

```bash
cd /Users/johnwu/code/ai-agent-autoimprove
just schema-init
just harvest
```

Useful commands:

```bash
just database-url
just database-url-admin
just psql
just psql-admin
just schema-init
just schema-init-admin
just harvest
```

These commands:

- load Postgres credentials from `config/op.envmap` through 1Password
- initialize the shared schema idempotently
- connect with `psql`
- harvest local conversation logs into the shared Postgres database

Expected output looks like:

```text
Found 322 JSONL files in /Users/johnwu/.claude/projects
Harvested 41 conversations into PostgreSQL
Transcripts written to /Users/johnwu/code/ai-agent-autoimprove/transcripts/
```

## Inspect The Results

### 1. Query the database

Example: find high-friction main sessions:

```bash
just psql
```

Then run:

```sql
SELECT
  session_id,
  project,
  duration_minutes,
  friction_score,
  efficiency_score,
  complexity_score,
  detected_skill,
  substr(first_user_message, 1, 80) AS first_msg
FROM conversations
WHERE is_subagent = 0
ORDER BY friction_score DESC
LIMIT 20;
```

### 2. Read a condensed transcript

```bash
sed -n '1,120p' transcripts/<session_id>.md
```

Subagent transcripts are written under:

```text
transcripts/<parent_session_id>/<subagent_session_id>.md
```

### 3. Check the schema

The database contains these tables:

- `conversations`
- `summaries`
- `analysis_runs`
- `improvements`

Only `conversations` is populated automatically by `harvest.py` today. The other tables exist so you can store later analysis and approved improvements.

## How To Use The Analysis Workflow

The intended workflow is described in `skills/autoimprove/SKILL.md`.

In practice, the current loop is:

1. Run `just harvest` or `uv run harvest.py --database-url "$DATABASE_URL"` to refresh the index.
2. Use `psql` to find the sessions you care about.
3. Read the matching files in `transcripts/`.
4. Look for patterns: user corrections, repeated failures, wasted tool calls, missing guidance, and successful patterns worth codifying.
5. Propose one skill change at a time.
6. Record summaries, analysis runs, and approved improvements in the database.

The skill file includes ready-made SQL examples for:

- checking the last analysis watermark
- listing high-friction conversations
- writing conversation summaries
- recording approved improvements
- updating `analysis_runs`

## Important Notes

- `uv run harvest` does not currently work as a packaged console command here. Use `uv run harvest.py`.
- `db.py` is the source of truth for schema creation. `just schema-init` and `just schema-init-admin` simply invoke that idempotent bootstrap path.
- SQLite support has been removed from the active workflow. This repo now expects PostgreSQL for schema initialization and harvesting.
- `main.py` is just a placeholder and is not part of the real workflow.
- `skills/autoimprove/SKILL.md` currently points at `/Users/johnwu/code/ai-agent-army/claude/skills/` as the target skills directory. If you want to analyze or edit a different skills repo, change that path.
- `transcripts/` is a generated artifact and will grow as you harvest more sessions.
- If you want Claude sessions to move cleanly between machines, use Syncthing for Claude state and keep the database local for now. See `SYNCTHING.md`.
- If you want multiple machines to write into one shared analysis database, use the Postgres + 1Password + `just` flow in `POSTGRES.md`.

## Planned But Not Implemented Yet

The earlier Claude session sketched a larger CLI with commands like:

- `list`
- `show`
- `summarize`
- `analyze`
- `improve`

That design is useful as a roadmap, but those commands do not exist in the current codebase yet. For now, usage is:

1. harvest with `just harvest` or `harvest.py`
2. inspect with `psql`
3. read transcripts from disk
4. follow the workflow in `skills/autoimprove/SKILL.md`
