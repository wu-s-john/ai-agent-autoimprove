# ai-agent-autoimprove

This repo is a small feedback loop for improving Claude Code skills from past conversations.

Right now it does two practical things:

1. Harvest Claude Code conversation logs from `~/.claude/projects/`
2. Turn them into a SQLite index plus condensed Markdown transcripts

It also includes a draft skill spec at `skills/autoimprove/SKILL.md` that describes how an agent should analyze those indexed conversations and propose skill improvements.

## What Exists Today

- `harvest.py`: parses Claude JSONL conversations and writes structured data
- `db.py`: creates the SQLite schema and upserts conversation rows
- `conversations.db`: generated SQLite index
- `transcripts/`: generated condensed Markdown transcripts
- `skills/autoimprove/SKILL.md`: manual/agent workflow for analyzing the indexed data

The broad `autoimprove` CLI discussed in the earlier Claude session is not fully implemented yet. There is no working `list`, `show`, `summarize`, `analyze`, or `improve` command in this repo today.

## Requirements

- Python 3.12+
- `uv`
- local Claude conversation history in `~/.claude/projects`
- `sqlite3` if you want to inspect the database from the shell

## Quick Start

From the repo root:

```bash
cd /Users/johnwu/code/ai-agent-autoimprove
uv run harvest.py --since 2026-03-31 --until 2026-03-31
```

Useful flags:

```bash
uv run harvest.py --help
uv run harvest.py --since 2026-03-27
uv run harvest.py --since 2026-03-27 --until 2026-03-28
uv run harvest.py --source ~/conversation-archive/
uv run harvest.py --db /tmp/autoimprove.db
```

What this command does:

- scans `~/.claude/projects` by default for `*.jsonl`
- creates or updates `conversations.db`
- writes condensed Markdown transcripts to `transcripts/`

Expected output looks like:

```text
Found 322 JSONL files in /Users/johnwu/.claude/projects
Harvested 41 conversations → /Users/johnwu/code/ai-agent-autoimprove/conversations.db
Transcripts written to /Users/johnwu/code/ai-agent-autoimprove/transcripts/
```

## Inspect The Results

### 1. Query the database

Example: find high-friction main sessions:

```bash
sqlite3 -header -column conversations.db "
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
LIMIT 20
"
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

1. Run `uv run harvest.py` to refresh the index.
2. Use `sqlite3` to find the sessions you care about.
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
- `main.py` is just a placeholder and is not part of the real workflow.
- `skills/autoimprove/SKILL.md` currently points at `/Users/johnwu/code/ai-agent-army/claude/skills/` as the target skills directory. If you want to analyze or edit a different skills repo, change that path.
- `conversations.db` and `transcripts/` are generated artifacts. They will grow as you harvest more sessions.

## Planned But Not Implemented Yet

The earlier Claude session sketched a larger CLI with commands like:

- `list`
- `show`
- `summarize`
- `analyze`
- `improve`

That design is useful as a roadmap, but those commands do not exist in the current codebase yet. For now, usage is:

1. harvest with `harvest.py`
2. inspect with `sqlite3`
3. read transcripts from disk
4. follow the workflow in `skills/autoimprove/SKILL.md`
