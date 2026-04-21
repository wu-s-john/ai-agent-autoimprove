# Syncthing Plan For Claude Session Handoff

This is the recommended Syncthing setup if you want one machine to hand off a Claude session to another machine.

The goal is to sync exactly these Claude state items:

- `~/.claude/projects`
- `~/.claude/plans`
- `~/.claude/file-history`
- `~/.claude/sessions`
- `~/.claude/history.jsonl`

## Important Design Choice

Do not create five separate Syncthing folders.

Use one Syncthing folder rooted at `~/.claude`, then use `.stignore` to sync only the five items above.

Why:

- Syncthing syncs folders, not standalone files, so `history.jsonl` does not fit as its own folder.
- Nested Syncthing folders under the same tree are avoidable complexity.
- One root folder plus a whitelist ignore file is the cleanest way to sync exactly the desired Claude state.

## Folder Plan

Create one Syncthing folder with:

- Folder ID: `claude-state`
- Label: `claude-state`
- Path: `/Users/johnwu/.claude`
- Folder Type: `Send & Receive`
- Share With: every machine that should be able to resume your Claude work
- File Watcher: enabled
- Rescan Interval: leave the default or use `3600`

Do not add separate Syncthing folders for:

- `projects`
- `plans`
- `file-history`
- `sessions`
- `history.jsonl`

Those are handled by the whitelist `.stignore` below.

## Exact Ignore List

Create this file on each machine:

```text
/Users/johnwu/.claude/.stignore
```

Use this content:

```text
(?d).DS_Store
(?d)Thumbs.db
(?d)._*
(?d)**/*.swp
(?d)**/*~
(?d)**/*.tmp
(?d)**/*.temp

!/projects
!/projects/**
!/plans
!/plans/**
!/file-history
!/file-history/**
!/sessions
!/sessions/**
!/history.jsonl

**
```

What this does:

- keeps the synced scope to the five Claude state items you chose
- ignores everything else under `~/.claude`
- drops common editor and OS junk files if they appear

## Setup Steps

You can either configure this in the Syncthing GUI manually or use [setup_syncthing.py](/Users/johnwu/code/ai-agent-autoimprove/setup_syncthing.py).

### Scripted path

The script edits Syncthing's `config.xml` and writes the Claude `.stignore`.

Examples:

```bash
python3 setup_syncthing.py --list-devices
python3 setup_syncthing.py --dry-run --all-configured-devices
python3 setup_syncthing.py --all-configured-devices
python3 setup_syncthing.py --device-id <remote-device-id>
```

The script refuses to edit `config.xml` while Syncthing appears to be running. Stop Syncthing first, run the script, then start Syncthing again.

### Manual path

1. Open the Syncthing web UI on this machine.
   On this Mac it is normally `http://127.0.0.1:8384`.
2. Add a new folder.
3. Use the folder plan above:
   `Folder ID = claude-state`, `Path = /Users/johnwu/.claude`, `Type = Send & Receive`.
4. Share that folder to the other machine(s).
5. Create `/Users/johnwu/.claude/.stignore` with the exact contents above.
6. On each other machine, accept the shared folder at that machine's own Claude directory path.
7. Put the same `.stignore` file on each machine.
   Syncthing does not sync `.stignore` for you.
8. Wait for Syncthing to reach `Up to Date` before testing a handoff.

## Handoff Rules

Follow these rules or you will create conflicts:

- Only one machine should actively write to a given Claude session at a time.
- Stop Claude on machine A before resuming the same session on machine B.
- Wait for Syncthing to show `Up to Date` on both sides before resuming.
- If a conflict file appears, inspect it before deleting anything.

## Path Stability

This works best when both machines use the same repo paths.

Claude stores project session history keyed by absolute paths such as:

```text
-Users-johnwu-code-ai-agent-autoimprove
```

So for the smoothest resume behavior:

- keep the same checkout path on both machines
- keep similar shell/tooling available on both machines
- keep both machines on the same OS if possible

If the paths differ, synced history is still useful for reading context, but live resume behavior is less predictable.

## What We Are Not Syncing

These Claude directories should stay local:

- `~/.claude/cache`
- `~/.claude/plugins`
- `~/.claude/downloads`
- `~/.claude/image-cache`
- `~/.claude/telemetry`
- `~/.claude/tasks`
- `~/.claude/session-env`
- `~/.claude/shell-snapshots`
- `~/.claude/paste-cache`
- `~/.claude/settings.local.json`

The whitelist `.stignore` already excludes them.

## Current Local Syncthing State

On this Mac:

- Syncthing is installed at `/opt/homebrew/bin/syncthing`
- Version is `v2.0.15`
- The active Syncthing config lives at:

```text
/Users/johnwu/Library/Application Support/Syncthing/config.xml
```

You do not need to edit `config.xml` directly for this plan. The GUI is safer.

## Recommended Next Step

After Syncthing is working, use a shared Postgres database for analysis state.

That gives you:

- machine-to-machine Claude session handoff via Syncthing
- centralized harvesting and analysis via Postgres
