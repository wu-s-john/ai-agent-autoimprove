#!/usr/bin/env python3
"""
Harvest Claude Code conversation logs into a database + condensed markdown transcripts.

Usage:
    uv run harvest.py                              # index everything
    uv run harvest.py --since 2026-03-27           # from date
    uv run harvest.py --since 2026-03-27 --until 2026-03-28
    uv run harvest.py --source ~/conversation-archive/
    uv run harvest.py --database-url postgresql://...
"""

import argparse
import json
import math
import os
import re
import socket
from datetime import datetime
from pathlib import Path

from db import init_db, upsert_conversation

DEFAULT_SOURCE = Path.home() / ".claude" / "projects"
TRANSCRIPTS_DIR = Path(__file__).parent / "transcripts"


# ---------------------------------------------------------------------------
# JSONL Parsing
# ---------------------------------------------------------------------------


def parse_conversation(file_path: Path) -> dict | None:
    """Parse a single JSONL conversation file and return metadata + raw events."""
    first_ts = last_ts = None
    user_msgs: list[str] = []
    assistant_msg_count = 0
    tool_calls: list[dict] = []
    tool_results: list[dict] = []
    total_input_tokens = 0
    total_output_tokens = 0
    model = None
    cwd = None
    detected_skill = None

    # Scoring signals
    bash_errors = 0
    edit_file_counts: dict[str, int] = {}
    unique_files_touched: set[str] = set()
    tool_type_set: set[str] = set()

    # For transcript generation
    events: list[dict] = []

    try:
        with open(file_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_type = obj.get("type")
                ts_str = obj.get("timestamp")
                ts = None
                if ts_str and isinstance(ts_str, str):
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if first_ts is None or ts < first_ts:
                            first_ts = ts
                        if last_ts is None or ts > last_ts:
                            last_ts = ts
                    except ValueError:
                        pass

                if not cwd:
                    cwd = obj.get("cwd")

                if msg_type == "user":
                    content = obj.get("message", {}).get("content", "")

                    # Extract user text messages
                    if isinstance(content, str) and content.strip():
                        user_msgs.append(content)
                        events.append({"type": "user", "text": content, "ts": ts})
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict):
                                if block.get("type") == "tool_result":
                                    tool_results.append(block)
                                    _process_tool_result(
                                        block, events, bash_errors_ref=[bash_errors]
                                    )
                                    bash_errors = events[-1].get(
                                        "_bash_errors", bash_errors
                                    )

                elif msg_type == "assistant":
                    assistant_msg_count += 1
                    msg = obj.get("message", {})
                    if not model:
                        model = msg.get("model")

                    usage = msg.get("usage", {})
                    total_input_tokens += usage.get("input_tokens", 0)
                    total_output_tokens += usage.get("output_tokens", 0)

                    content = msg.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            block_type = block.get("type")

                            if block_type == "text":
                                text = block.get("text", "")
                                if text.strip():
                                    events.append(
                                        {"type": "assistant_text", "text": text, "ts": ts}
                                    )

                            elif block_type == "tool_use":
                                name = block.get("name", "unknown")
                                inp = block.get("input", {})
                                tool_calls.append({"name": name, "input": inp})
                                tool_type_set.add(name)

                                # Track files touched
                                fp = inp.get("file_path", "")
                                if fp:
                                    unique_files_touched.add(fp)

                                # Track edit churn
                                if name == "Edit" and fp:
                                    edit_file_counts[fp] = (
                                        edit_file_counts.get(fp, 0) + 1
                                    )

                                # Detect skill invocations
                                if name == "Skill" and not detected_skill:
                                    detected_skill = inp.get("skill", inp.get("name"))

                                events.append(
                                    {
                                        "type": "tool_use",
                                        "name": name,
                                        "input": inp,
                                        "ts": ts,
                                    }
                                )
    except (OSError, IOError):
        return None

    if first_ts is None:
        return None

    # Detect skill from user messages if not found via tool calls
    if not detected_skill:
        detected_skill = _detect_skill_from_messages(user_msgs)

    # Count bash errors from tool results
    bash_errors = _count_bash_errors(tool_results)

    # Compute scores
    friction = _compute_friction(bash_errors, edit_file_counts, len(tool_calls), len(user_msgs))
    efficiency = _compute_efficiency(
        total_input_tokens + total_output_tokens,
        len(user_msgs),
        first_ts,
        last_ts,
        len(tool_calls),
    )
    complexity = _compute_complexity(
        len(tool_calls), len(unique_files_touched), len(tool_type_set)
    )

    # Tool breakdown
    tool_counts: dict[str, int] = {}
    for tc in tool_calls:
        n = tc["name"]
        tool_counts[n] = tool_counts.get(n, 0) + 1

    # Subagent and parent detection
    path_parts = file_path.parts
    is_subagent = "subagents" in path_parts
    parent_session_id = None
    if is_subagent:
        subagents_idx = list(path_parts).index("subagents")
        if subagents_idx > 0:
            parent_session_id = path_parts[subagents_idx - 1]

    session_id = file_path.stem
    duration_minutes = (
        round((last_ts - first_ts).total_seconds() / 60, 1)
        if first_ts and last_ts
        else 0
    )

    # Extract project name
    project = ""
    for part in file_path.parts:
        if part.startswith("-Users-") or part.startswith("-home-"):
            project = part
            break

    return {
        "session_id": session_id,
        "project": project,
        "cwd": cwd,
        "model": model,
        "started_at": first_ts.isoformat() if first_ts else None,
        "ended_at": last_ts.isoformat() if last_ts else None,
        "duration_minutes": duration_minutes,
        "user_message_count": len(user_msgs),
        "assistant_message_count": assistant_msg_count,
        "tool_call_count": len(tool_calls),
        "tool_breakdown": json.dumps(
            dict(sorted(tool_counts.items(), key=lambda x: -x[1]))
        ),
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "friction_score": friction,
        "efficiency_score": efficiency,
        "complexity_score": complexity,
        "detected_skill": detected_skill,
        "first_user_message": (user_msgs[0][:500] if user_msgs else ""),
        "file_path": str(file_path),
        "file_size_bytes": os.path.getsize(file_path),
        "is_subagent": 1 if is_subagent else 0,
        "parent_session_id": parent_session_id,
        # Not stored in DB, used for transcript generation
        "_events": events,
    }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 1.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, value))


def _compute_friction(
    bash_errors: int,
    edit_file_counts: dict[str, int],
    tool_call_count: int,
    user_message_count: int,
) -> float:
    """Friction (1-10): how much the agent struggled."""
    score = 1.0

    # Bash errors: each error adds ~1 point, capped contribution at 4
    score += min(bash_errors * 0.8, 4.0)

    # Edit churn: files edited 3+ times suggest struggle
    churn_files = sum(1 for c in edit_file_counts.values() if c >= 3)
    score += min(churn_files * 1.0, 3.0)

    # Tool call density: high tool calls per user message = thrashing
    if user_message_count > 0:
        density = tool_call_count / user_message_count
        if density > 50:
            score += 2.0
        elif density > 20:
            score += 1.0

    return round(_clamp(score), 1)


def _compute_efficiency(
    total_tokens: int,
    user_message_count: int,
    first_ts: datetime | None,
    last_ts: datetime | None,
    tool_call_count: int,
) -> float:
    """Efficiency (1-10): 10 = very efficient, 1 = very wasteful."""
    score = 10.0

    # Tokens per user message: high = bloated
    if user_message_count > 0:
        tokens_per_msg = total_tokens / user_message_count
        if tokens_per_msg > 100_000:
            score -= 3.0
        elif tokens_per_msg > 50_000:
            score -= 2.0
        elif tokens_per_msg > 20_000:
            score -= 1.0

    # Duration vs tool calls: long idle gaps = stuck
    if first_ts and last_ts and tool_call_count > 0:
        duration_sec = (last_ts - first_ts).total_seconds()
        if duration_sec > 0:
            calls_per_min = tool_call_count / (duration_sec / 60)
            if calls_per_min < 0.5:
                score -= 2.0
            elif calls_per_min < 1.0:
                score -= 1.0

    # Very high token counts in absolute terms
    if total_tokens > 500_000:
        score -= 2.0
    elif total_tokens > 200_000:
        score -= 1.0

    return round(_clamp(score), 1)


def _compute_complexity(
    tool_call_count: int,
    unique_files_count: int,
    tool_type_count: int,
) -> float:
    """Complexity (1-10): how big/hard the task was."""
    score = 1.0

    # Tool call volume
    if tool_call_count > 500:
        score += 3.0
    elif tool_call_count > 100:
        score += 2.0
    elif tool_call_count > 30:
        score += 1.0

    # Files touched
    if unique_files_count > 20:
        score += 3.0
    elif unique_files_count > 10:
        score += 2.0
    elif unique_files_count > 5:
        score += 1.0

    # Tool diversity
    if tool_type_count > 10:
        score += 2.0
    elif tool_type_count > 5:
        score += 1.0

    return round(_clamp(score), 1)


# ---------------------------------------------------------------------------
# Skill Detection
# ---------------------------------------------------------------------------

SKILL_PATTERN = re.compile(
    r"(?:activate|invoke|use|run)\s+(?:your\s+)?(?:the\s+)?(\w[\w-]+?)(?:\s+skill)",
    re.IGNORECASE,
)
SLASH_PATTERN = re.compile(r"^/?(\w[\w-]+)\s*$")

# Known skills to match against
KNOWN_SKILLS = {
    "address-reviews", "constant-time-analysis", "crypto-analysis",
    "ec2-compute", "grill-me", "perf-engineer", "rust-best-practices-review",
    "rust-reviewer", "snark-guidelines", "spec-to-code-compliance",
    "zk-constraint-profiler", "autoimprove", "frontend-design",
    "code-review",
}

BUILTIN_COMMANDS = {
    "help", "clear", "exit", "quit", "fast", "compact", "commit",
    "review-pr", "loop", "schedule", "plan", "init",
}


def _detect_skill_from_messages(user_msgs: list[str]) -> str | None:
    """Try to detect which skill was used from user messages."""
    for msg in user_msgs[:5]:
        stripped = msg.strip()

        # Check for explicit "activate your X skill" patterns
        m = SKILL_PATTERN.search(stripped)
        if m:
            skill = m.group(1).lower().rstrip(",.")
            if skill in KNOWN_SKILLS:
                return skill

        # Check for slash commands that are known skills
        if stripped.startswith("/"):
            parts = stripped[1:].split()
            if parts:
                candidate = parts[0].lower().rstrip(",.")
                if candidate in KNOWN_SKILLS:
                    return candidate

    return None


# ---------------------------------------------------------------------------
# Error Counting
# ---------------------------------------------------------------------------


def _count_bash_errors(tool_results: list[dict]) -> int:
    """Count tool results that indicate errors."""
    errors = 0
    for tr in tool_results:
        if tr.get("is_error", False):
            errors += 1
            continue
        # Check for non-zero exit codes in result text
        text = _extract_tool_result_text(tr)
        if "Exit code" in text and "Exit code 0" not in text:
            errors += 1
    return errors


def _extract_tool_result_text(tr: dict) -> str:
    """Extract text from a tool_result block."""
    content = tr.get("content", [])
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def _process_tool_result(block: dict, events: list, bash_errors_ref: list) -> None:
    """Process a tool result block, tracking errors."""
    # We just record the existence of tool results for transcript purposes
    # Error counting is done separately in _count_bash_errors
    pass


# ---------------------------------------------------------------------------
# Transcript Generation
# ---------------------------------------------------------------------------


def generate_transcript(data: dict, output_dir: Path) -> None:
    """Generate a condensed markdown transcript from parsed conversation data."""
    events = data.get("_events", [])
    session_id = data["session_id"]
    is_subagent = data["is_subagent"]
    parent_session_id = data.get("parent_session_id")

    if is_subagent and parent_session_id:
        out_dir = output_dir / parent_session_id
    else:
        out_dir = output_dir

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{session_id}.md"

    lines = []
    lines.append(f"# Conversation: {session_id}")
    lines.append("")
    lines.append(f"- **Project**: {data.get('project', 'unknown')}")
    lines.append(f"- **Model**: {data.get('model', 'unknown')}")
    lines.append(f"- **Started**: {data.get('started_at', '?')}")
    lines.append(f"- **Duration**: {data.get('duration_minutes', 0)} min")
    lines.append(f"- **Friction**: {data.get('friction_score', '?')}/10")
    lines.append(f"- **Efficiency**: {data.get('efficiency_score', '?')}/10")
    lines.append(f"- **Complexity**: {data.get('complexity_score', '?')}/10")
    if data.get("detected_skill"):
        lines.append(f"- **Skill**: {data['detected_skill']}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for event in events:
        etype = event["type"]

        if etype == "user":
            lines.append("## User")
            lines.append("")
            lines.append(event["text"])
            lines.append("")

        elif etype == "assistant_text":
            lines.append("## Assistant")
            lines.append("")
            lines.append(event["text"])
            lines.append("")

        elif etype == "tool_use":
            name = event["name"]
            inp = event.get("input", {})
            one_liner = _tool_call_one_liner(name, inp)
            lines.append(f"> **{name}**: {one_liner}")
            lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


def _tool_call_one_liner(name: str, inp: dict) -> str:
    """Generate a one-line summary of a tool call."""
    if name == "Bash":
        cmd = inp.get("command", "")
        return f"`{cmd[:120]}{'...' if len(cmd) > 120 else ''}`"
    elif name == "Edit":
        fp = inp.get("file_path", "?")
        old = inp.get("old_string", "")
        return f"{_short_path(fp)} ({len(old)} chars replaced)"
    elif name == "Write":
        fp = inp.get("file_path", "?")
        content = inp.get("content", "")
        return f"{_short_path(fp)} ({len(content)} chars)"
    elif name == "Read":
        fp = inp.get("file_path", "?")
        try:
            offset = int(inp.get("offset") or 0)
        except (ValueError, TypeError):
            offset = 0
        try:
            limit = int(inp.get("limit") or 0)
        except (ValueError, TypeError):
            limit = 0
        suffix = ""
        if offset or limit:
            start = offset or 1
            end = start + (limit or 2000)
            suffix = f" (lines {start}-{end})"
        return f"{_short_path(fp)}{suffix}"
    elif name in ("Grep", "Glob"):
        pattern = inp.get("pattern", "?")
        path = inp.get("path", ".")
        return f"`{pattern}` in {_short_path(path)}"
    elif name == "Agent":
        desc = inp.get("description", inp.get("prompt", "")[:80])
        return desc
    elif name == "Skill":
        skill = inp.get("skill", "?")
        return skill
    elif name == "WebSearch":
        query = inp.get("query", "?")
        return f"`{query[:80]}`"
    else:
        # Generic: show first key-value
        if inp:
            first_key = next(iter(inp))
            val = str(inp[first_key])[:80]
            return f"{first_key}={val}"
        return "(no input)"


def _short_path(fp: str) -> str:
    """Shorten a file path for display."""
    parts = Path(fp).parts
    if len(parts) > 3:
        return f".../{'/'.join(parts[-3:])}"
    return fp


# ---------------------------------------------------------------------------
# Main Harvest
# ---------------------------------------------------------------------------


def harvest(
    source: Path = DEFAULT_SOURCE,
    since: str | None = None,
    until: str | None = None,
    database_url: str | None = None,
    source_machine: str | None = None,
) -> int:
    """Harvest conversations from JSONL files into PostgreSQL + transcripts."""
    conn = init_db(database_url=database_url)
    transcripts_dir = TRANSCRIPTS_DIR
    source_machine = source_machine or socket.gethostname()

    jsonl_files = list(source.rglob("*.jsonl"))
    print(f"Found {len(jsonl_files)} JSONL files in {source}")

    count = 0
    for fpath in jsonl_files:
        data = parse_conversation(fpath)
        if data is None:
            continue
        data["source_machine"] = source_machine

        # Date range filter
        if since and data["ended_at"]:
            start = datetime.fromisoformat(since + "T00:00:00+00:00")
            ended = datetime.fromisoformat(data["ended_at"])
            if ended < start:
                continue

        if until and data["started_at"]:
            end = datetime.fromisoformat(until + "T23:59:59+00:00")
            started = datetime.fromisoformat(data["started_at"])
            if started > end:
                continue

        # Store events separately before removing from db data
        events = data.pop("_events", [])

        upsert_conversation(conn, data)

        # Generate condensed transcript
        data["_events"] = events
        generate_transcript(data, transcripts_dir)

        count += 1

    conn.commit()
    conn.close()
    print(f"Harvested {count} conversations into PostgreSQL")
    print(f"Transcripts written to {transcripts_dir}/")
    return count


def main():
    parser = argparse.ArgumentParser(
        description="Harvest Claude Code conversations into a database + transcripts"
    )
    parser.add_argument("--since", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--until", help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--source",
        default=str(DEFAULT_SOURCE),
        help=f"Source directory of JSONL files (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Explicit PostgreSQL database URL. Defaults to DATABASE_URL if set.",
    )
    parser.add_argument(
        "--source-machine",
        default=socket.gethostname(),
        help="Machine label stored with harvested rows (default: local hostname)",
    )
    args = parser.parse_args()

    harvest(
        source=Path(args.source),
        since=args.since,
        until=args.until,
        database_url=args.database_url,
        source_machine=args.source_machine,
    )


if __name__ == "__main__":
    main()
