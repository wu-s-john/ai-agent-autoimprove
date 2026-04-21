# Syncthing Plan For Codex Session Sync

This is the recommended Syncthing setup if you want one machine to analyze
Codex sessions produced on other machines without syncing live SQLite state.

The goal is to sync exactly these Codex state items:

- `~/.codex/sessions`
- `~/.codex/archived_sessions`

## Important Design Choice

Do not sync the entire `~/.codex` directory.

Use one Syncthing folder rooted at `~/.codex`, then use `.stignore` to sync
only immutable session artifacts.

Why:

- `sessions/` and `archived_sessions/` are the durable rollout JSONL artifacts.
- `state_5.sqlite*` and `logs_2.sqlite*` are live WAL databases and should stay
  local to each machine.
- `history.jsonl` and `session_index.jsonl` are mutable metadata files that are
  easy to conflict if multiple machines are writing.
- `auth.json`, caches, shell snapshots, worktrees, and local app state should
  remain machine-local.

## Folder Plan

Create one Syncthing folder with:

- Folder ID: `codex-state`
- Label: `codex-state`
- Path: `/Users/johnwu/.codex`
- Folder Type: `Send & Receive`
- Share With: every machine that should contribute Codex session artifacts
- File Watcher: enabled
- Rescan Interval: leave the default or use `3600`

Do not add separate Syncthing folders for:

- `sessions`
- `archived_sessions`

Those are handled by the whitelist `.stignore` below.

## Exact Ignore List

Create this file on each machine:

```text
/Users/johnwu/.codex/.stignore
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

!/sessions
!/sessions/**
!/archived_sessions
!/archived_sessions/**

**
```

What this does:

- keeps the synced scope to Codex rollout artifacts only
- excludes all SQLite files because they are not whitelisted
- ignores everything else under `~/.codex`
- drops common editor and OS junk files if they appear

## What We Are Not Syncing

These Codex files and directories should stay local:

- `~/.codex/state_5.sqlite`
- `~/.codex/state_5.sqlite-wal`
- `~/.codex/state_5.sqlite-shm`
- `~/.codex/logs_2.sqlite`
- `~/.codex/logs_2.sqlite-wal`
- `~/.codex/logs_2.sqlite-shm`
- `~/.codex/history.jsonl`
- `~/.codex/session_index.jsonl`
- `~/.codex/auth.json`
- `~/.codex/cache`
- `~/.codex/plugins`
- `~/.codex/shell_snapshots`
- `~/.codex/worktrees`
- `~/.codex/models_cache.json`
- `~/.codex/.codex-global-state.json`

## Setup Steps

You can either configure this in the Syncthing GUI manually or use
[setup_syncthing.py](/Users/johnwu/code/ai-agent-autoimprove/setup_syncthing.py).

### Scripted path

Examples:

```bash
python3 setup_syncthing.py --app codex --list-devices
python3 setup_syncthing.py --app codex --dry-run --all-configured-devices
python3 setup_syncthing.py --app codex --all-configured-devices
python3 setup_syncthing.py --app codex --device-id <remote-device-id>
```

The script edits Syncthing's `config.xml` and writes the Codex `.stignore`.
It refuses to edit `config.xml` while Syncthing appears to be running. Stop
Syncthing first, run the script, then start Syncthing again.

### Manual path

1. Open the Syncthing web UI on this machine.
   On this Mac it is normally `http://127.0.0.1:8384`.
2. Add a new folder.
3. Use the folder plan above:
   `Folder ID = codex-state`, `Path = /Users/johnwu/.codex`, `Type = Send & Receive`.
4. Share that folder to the other machine(s).
5. Create `/Users/johnwu/.codex/.stignore` with the exact contents above.
6. On each other machine, accept the shared folder at that machine's own Codex directory path.
7. Put the same `.stignore` file on each machine.
   Syncthing does not sync `.stignore` for you.
8. Wait for Syncthing to reach `Up to Date` before harvesting on the analysis machine.

## Handoff Rules

Follow these rules or you will create conflicts:

- Treat synced Codex files as analysis artifacts, not a live shared app state directory.
- Only one machine should actively write to a given Codex thread.
- Do not try to sync or resume the same live thread from two machines at once.
- Wait for Syncthing to show `Up to Date` before harvesting on the analysis machine.
- If a conflict file appears, inspect it before deleting anything.

## Path Stability

This works best when both machines use the same repo paths.

Codex session metadata records absolute paths like:

```text
/Users/johnwu/code/ai-agent-autoimprove
```

So for the smoothest downstream analysis:

- keep the same checkout path on both machines when possible
- keep similar shell/tooling available on both machines
- keep both machines on the same OS if possible

If the paths differ, the synced sessions are still harvestable, but path-based
analysis and resume assumptions become less predictable.
