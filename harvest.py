#!/usr/bin/env python3
"""
Harvest Claude and Codex conversation logs into PostgreSQL + normalized transcripts.

Usage:
    uv run harvest.py
    uv run harvest.py --source codex
    uv run harvest.py --since 2026-03-27 --until 2026-03-28
    uv run harvest.py --claude-source ~/conversation-archive/
    uv run harvest.py --codex-source ~/.codex/sessions
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import socket
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from db import init_db, upsert_conversation
from logging_utils import setup_logging
from session_utils import (
    DEFAULT_CLAUDE_SOURCE,
    DEFAULT_CODEX_SOURCES,
    TRANSCRIPTS_DIR,
    canonical_session_id,
    json_or_none,
    write_transcript,
)

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class HarvestResult:
    harvested_count: int
    harvested_session_ids: list[str]
    files_seen: int


SKILL_PATTERN = re.compile(
    r"(?:activate|invoke|use|run)\s+(?:your\s+)?(?:the\s+)?(\w[\w-]+?)(?:\s+skill)",
    re.IGNORECASE,
)

KNOWN_SKILLS = {
    "address-reviews",
    "autoimprove",
    "code-review",
    "constant-time-analysis",
    "crypto-analysis",
    "ec2-compute",
    "frontend-design",
    "grill-me",
    "perf-engineer",
    "rust-best-practices-review",
    "rust-reviewer",
    "snark-guidelines",
    "spec-to-code-compliance",
    "zk-constraint-profiler",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Harvest Claude and Codex conversations into PostgreSQL + transcripts"
    )
    parser.add_argument(
        "--source",
        choices=("all", "claude", "codex"),
        default="all",
        help="Which source app to harvest (default: all)",
    )
    parser.add_argument("--since", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--until", help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--claude-source",
        action="append",
        default=[],
        help=f"Claude source directory (default: {DEFAULT_CLAUDE_SOURCE})",
    )
    parser.add_argument(
        "--codex-source",
        action="append",
        default=[],
        help="Codex source directory. Defaults to ~/.codex/sessions and ~/.codex/archived_sessions.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Explicit PostgreSQL database URL. Defaults to DATABASE_URL or PG* env vars.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Log level (DEBUG, INFO, WARNING, ERROR). Defaults to AUTOIMPROVE_LOG_LEVEL or INFO.",
    )
    parser.add_argument(
        "--source-machine",
        default=socket.gethostname(),
        help="Machine label stored with harvested rows (default: local hostname)",
    )
    return parser.parse_args()


def parse_conversation(file_path: Path, source_app: str) -> dict[str, Any] | None:
    if source_app == "claude":
        return parse_claude_conversation(file_path)
    if source_app == "codex":
        return parse_codex_conversation(file_path)
    raise ValueError(f"Unsupported source_app: {source_app}")


def parse_claude_conversation(file_path: Path) -> dict[str, Any] | None:
    first_ts = last_ts = None
    user_msgs: list[str] = []
    assistant_msg_count = 0
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    total_input_tokens = 0
    total_output_tokens = 0
    model = None
    cwd = None
    detected_skill = None
    edit_file_counts: dict[str, int] = {}
    unique_files_touched: set[str] = set()
    tool_type_set: set[str] = set()
    events: list[dict[str, Any]] = []

    try:
        with file_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = _parse_timestamp(obj.get("timestamp"))
                if ts is not None:
                    if first_ts is None or ts < first_ts:
                        first_ts = ts
                    if last_ts is None or ts > last_ts:
                        last_ts = ts

                cwd = cwd or obj.get("cwd")
                msg_type = obj.get("type")
                if msg_type == "user":
                    content = obj.get("message", {}).get("content", "")
                    if isinstance(content, str) and content.strip():
                        user_msgs.append(content)
                        events.append({"type": "user", "text": content, "ts": ts})
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                tool_results.append(block)
                elif msg_type == "assistant":
                    assistant_msg_count += 1
                    msg = obj.get("message", {})
                    model = model or msg.get("model")
                    usage = msg.get("usage", {})
                    total_input_tokens += usage.get("input_tokens", 0)
                    total_output_tokens += usage.get("output_tokens", 0)
                    for block in msg.get("content", []):
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
                            file_path_value = inp.get("file_path", "")
                            if file_path_value:
                                unique_files_touched.add(file_path_value)
                            if name == "Edit" and file_path_value:
                                edit_file_counts[file_path_value] = (
                                    edit_file_counts.get(file_path_value, 0) + 1
                                )
                            if name == "Skill" and not detected_skill:
                                detected_skill = inp.get("skill", inp.get("name"))
                            events.append(
                                {"type": "tool_use", "name": name, "input": inp, "ts": ts}
                            )
    except OSError:
        return None

    if first_ts is None:
        return None

    if not detected_skill:
        detected_skill = detect_skill_from_messages(user_msgs)

    native_session_id = file_path.stem
    is_subagent, parent_native_session_id = detect_claude_parent(file_path)
    project = extract_project(cwd, file_path)
    tool_breakdown = count_tool_breakdown(tool_calls)

    return {
        "session_id": canonical_session_id("claude", native_session_id),
        "source_app": "claude",
        "native_session_id": native_session_id,
        "project": project,
        "cwd": cwd,
        "model": model,
        "started_at": first_ts.isoformat() if first_ts else None,
        "ended_at": last_ts.isoformat() if last_ts else None,
        "duration_minutes": duration_minutes(first_ts, last_ts),
        "user_message_count": len(user_msgs),
        "assistant_message_count": assistant_msg_count,
        "tool_call_count": len(tool_calls),
        "tool_breakdown": json.dumps(tool_breakdown),
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "friction_score": compute_friction(
            bash_errors=count_claude_errors(tool_results),
            edit_file_counts=edit_file_counts,
            tool_call_count=len(tool_calls),
            user_message_count=len(user_msgs),
        ),
        "efficiency_score": compute_efficiency(
            total_tokens=total_input_tokens + total_output_tokens,
            user_message_count=len(user_msgs),
            first_ts=first_ts,
            last_ts=last_ts,
            tool_call_count=len(tool_calls),
        ),
        "complexity_score": compute_complexity(
            tool_call_count=len(tool_calls),
            unique_files_count=len(unique_files_touched),
            tool_type_count=len(tool_type_set),
        ),
        "detected_skill": detected_skill,
        "first_user_message": user_msgs[0][:500] if user_msgs else "",
        "file_path": str(file_path),
        "file_size_bytes": os.path.getsize(file_path),
        "is_subagent": 1 if is_subagent else 0,
        "parent_session_id": (
            canonical_session_id("claude", parent_native_session_id)
            if parent_native_session_id
            else None
        ),
        "agent_role": None,
        "agent_name": None,
        "_events": events,
    }


def parse_codex_conversation(file_path: Path) -> dict[str, Any] | None:
    first_ts = last_ts = None
    user_msgs: list[str] = []
    assistant_msg_count = 0
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    total_input_tokens = 0
    total_output_tokens = 0
    model = None
    cwd = None
    detected_skill = None
    agent_role = None
    agent_name = None
    native_session_id = None
    parent_native_session_id = None
    is_subagent = False
    edit_file_counts: dict[str, int] = {}
    unique_files_touched: set[str] = set()
    tool_type_set: set[str] = set()
    events: list[dict[str, Any]] = []

    try:
        with file_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = _parse_timestamp(obj.get("timestamp"))
                if ts is not None:
                    if first_ts is None or ts < first_ts:
                        first_ts = ts
                    if last_ts is None or ts > last_ts:
                        last_ts = ts

                entry_type = obj.get("type")
                payload = obj.get("payload", {})

                if entry_type == "session_meta" and isinstance(payload, dict):
                    native_session_id = payload.get("id", native_session_id)
                    cwd = cwd or payload.get("cwd")
                    agent_role = agent_role or payload.get("agent_role")
                    agent_name = agent_name or payload.get("agent_nickname")
                    model = model or payload.get("model")
                    source_payload = payload.get("source", {})
                    if isinstance(source_payload, dict):
                        subagent = source_payload.get("subagent", {})
                    else:
                        subagent = {}
                    thread_spawn = (
                        subagent.get("thread_spawn", {})
                        if isinstance(subagent, dict)
                        else {}
                    )
                    parent_native_session_id = (
                        thread_spawn.get("parent_thread_id")
                        or payload.get("forked_from_id")
                        or parent_native_session_id
                    )
                    is_subagent = bool(parent_native_session_id)
                    continue

                if entry_type == "turn_context" and isinstance(payload, dict):
                    cwd = cwd or payload.get("cwd")
                    model = model or payload.get("model")
                    continue

                if entry_type == "event_msg" and isinstance(payload, dict):
                    payload_type = payload.get("type")
                    if payload_type == "user_message":
                        message = payload.get("message", "")
                        if message.strip():
                            user_msgs.append(message)
                            events.append({"type": "user", "text": message, "ts": ts})
                            detected_skill = detected_skill or detect_skill_from_messages(
                                [message]
                            )
                    elif payload_type == "token_count":
                        totals = payload.get("info", {}) or {}
                        total_usage = totals.get("total_token_usage", {})
                        total_input_tokens = max(
                            total_input_tokens, total_usage.get("input_tokens", 0)
                        )
                        total_output_tokens = max(
                            total_output_tokens, total_usage.get("output_tokens", 0)
                        )
                    elif payload_type in {"exec_command_end", "mcp_tool_call_end", "patch_apply_end"}:
                        tool_results.append(payload)
                    continue

                if entry_type != "response_item" or not isinstance(payload, dict):
                    continue

                payload_type = payload.get("type")
                if payload_type == "message":
                    role = payload.get("role")
                    if role == "assistant":
                        assistant_msg_count += 1
                        for block in payload.get("content", []):
                            if block.get("type") == "output_text" and block.get("text", "").strip():
                                events.append(
                                    {
                                        "type": "assistant_text",
                                        "text": block["text"],
                                        "ts": ts,
                                    }
                                )
                    continue

                if payload_type == "function_call":
                    name = payload.get("name", "unknown")
                    tool_name = normalize_codex_tool_name(name)
                    tool_input = json_or_none(payload.get("arguments"))
                    tool_calls.append({"name": tool_name, "input": tool_input})
                    tool_type_set.add(tool_name)
                    register_file_touch(tool_name, tool_input, edit_file_counts, unique_files_touched)
                    events.append({"type": "tool_use", "name": tool_name, "input": tool_input, "ts": ts})
                    continue

                if payload_type == "custom_tool_call":
                    name = payload.get("name", "custom_tool")
                    tool_input = payload.get("input", {})
                    tool_calls.append({"name": name, "input": tool_input})
                    tool_type_set.add(name)
                    register_file_touch(name, tool_input, edit_file_counts, unique_files_touched)
                    events.append({"type": "tool_use", "name": name, "input": tool_input, "ts": ts})
                    continue

                if payload_type == "web_search_call":
                    action = payload.get("action", {})
                    tool_calls.append({"name": "web_search", "input": action})
                    tool_type_set.add("web_search")
                    events.append({"type": "tool_use", "name": "web_search", "input": action, "ts": ts})
                    continue

                if payload_type in {"function_call_output", "custom_tool_call_output"}:
                    tool_results.append(payload)

    except OSError:
        return None

    if first_ts is None:
        return None

    native_session_id = native_session_id or file_path.stem
    project = extract_project(cwd, file_path)
    tool_breakdown = count_tool_breakdown(tool_calls)

    return {
        "session_id": canonical_session_id("codex", native_session_id),
        "source_app": "codex",
        "native_session_id": native_session_id,
        "project": project,
        "cwd": cwd,
        "model": model,
        "started_at": first_ts.isoformat() if first_ts else None,
        "ended_at": last_ts.isoformat() if last_ts else None,
        "duration_minutes": duration_minutes(first_ts, last_ts),
        "user_message_count": len(user_msgs),
        "assistant_message_count": assistant_msg_count,
        "tool_call_count": len(tool_calls),
        "tool_breakdown": json.dumps(tool_breakdown),
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "friction_score": compute_friction(
            bash_errors=count_codex_errors(tool_results),
            edit_file_counts=edit_file_counts,
            tool_call_count=len(tool_calls),
            user_message_count=len(user_msgs),
        ),
        "efficiency_score": compute_efficiency(
            total_tokens=total_input_tokens + total_output_tokens,
            user_message_count=len(user_msgs),
            first_ts=first_ts,
            last_ts=last_ts,
            tool_call_count=len(tool_calls),
        ),
        "complexity_score": compute_complexity(
            tool_call_count=len(tool_calls),
            unique_files_count=len(unique_files_touched),
            tool_type_count=len(tool_type_set),
        ),
        "detected_skill": detected_skill,
        "first_user_message": user_msgs[0][:500] if user_msgs else "",
        "file_path": str(file_path),
        "file_size_bytes": os.path.getsize(file_path),
        "is_subagent": 1 if is_subagent else 0,
        "parent_session_id": (
            canonical_session_id("codex", parent_native_session_id)
            if parent_native_session_id
            else None
        ),
        "agent_role": agent_role,
        "agent_name": agent_name,
        "_events": events,
    }


def harvest(
    *,
    source: str = "all",
    since: str | None = None,
    until: str | None = None,
    claude_sources: list[Path] | None = None,
    codex_sources: list[Path] | None = None,
    database_url: str | None = None,
    source_machine: str | None = None,
    log_level: str | None = None,
) -> HarvestResult:
    setup_logging(log_level)
    conn = init_db(database_url=database_url)
    source_machine = source_machine or socket.gethostname()
    seen_session_ids: set[str] = set()
    harvested_session_ids: list[str] = []
    files = discover_source_files(
        source=source,
        claude_sources=claude_sources,
        codex_sources=codex_sources,
    )

    LOGGER.info("Found %s JSONL files across %s sources", len(files), source)

    for index, (source_app, file_path) in enumerate(files, start=1):
        LOGGER.debug(
            "Parsing %s file %s/%s: %s",
            source_app,
            index,
            len(files),
            file_path,
        )
        data = parse_conversation(file_path, source_app)
        if data is None or not in_date_range(data, since=since, until=until):
            LOGGER.debug("Skipping %s because it did not produce an in-range session", file_path)
            continue
        if data["session_id"] in seen_session_ids:
            LOGGER.debug("Skipping duplicate session %s from %s", data["session_id"], file_path)
            continue
        seen_session_ids.add(data["session_id"])
        data["source_machine"] = source_machine
        events = data.pop("_events", [])
        upsert_conversation(conn, data)
        data["_events"] = events
        write_transcript(data, TRANSCRIPTS_DIR)
        harvested_session_ids.append(data["session_id"])
        LOGGER.info(
            "Harvested %s (%s, %s/%s)",
            data["session_id"],
            data["source_app"],
            len(harvested_session_ids),
            len(files),
        )

    conn.commit()
    conn.close()
    LOGGER.info(
        "Harvested %s conversations into PostgreSQL",
        len(harvested_session_ids),
    )
    LOGGER.info("Transcripts written to %s/", TRANSCRIPTS_DIR)
    return HarvestResult(
        harvested_count=len(harvested_session_ids),
        harvested_session_ids=harvested_session_ids,
        files_seen=len(files),
    )


def discover_source_files(
    *,
    source: str,
    claude_sources: list[Path] | None,
    codex_sources: list[Path] | None,
) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    if source in {"all", "claude"}:
        for base in claude_sources or [DEFAULT_CLAUDE_SOURCE]:
            if base.exists():
                files.extend(("claude", path) for path in sorted(base.rglob("*.jsonl")))
    if source in {"all", "codex"}:
        for base in codex_sources or list(DEFAULT_CODEX_SOURCES):
            if base.exists():
                files.extend(("codex", path) for path in sorted(base.rglob("*.jsonl")))
    return files


def extract_project(cwd: str | None, file_path: Path) -> str:
    if cwd:
        return Path(cwd).name or cwd
    return file_path.parent.name


def detect_claude_parent(file_path: Path) -> tuple[bool, str | None]:
    parts = file_path.parts
    if "subagents" not in parts:
        return False, None
    subagents_index = list(parts).index("subagents")
    if subagents_index <= 0:
        return False, None
    return True, parts[subagents_index - 1]


def normalize_codex_tool_name(name: str) -> str:
    if name.startswith("mcp__"):
        return name
    return name


def register_file_touch(
    tool_name: str,
    tool_input: Any,
    edit_file_counts: dict[str, int],
    unique_files_touched: set[str],
) -> None:
    if not isinstance(tool_input, dict):
        return
    file_path = tool_input.get("file_path") or tool_input.get("path")
    if isinstance(file_path, str) and file_path:
        unique_files_touched.add(file_path)
        if tool_name in {"Edit", "Write", "apply_patch"}:
            edit_file_counts[file_path] = edit_file_counts.get(file_path, 0) + 1


def in_date_range(data: dict[str, Any], *, since: str | None, until: str | None) -> bool:
    started_at = data.get("started_at")
    ended_at = data.get("ended_at")
    if since and ended_at:
        start = datetime.fromisoformat(f"{since}T00:00:00+00:00")
        if datetime.fromisoformat(ended_at) < start:
            return False
    if until and started_at:
        end = datetime.fromisoformat(f"{until}T23:59:59+00:00")
        if datetime.fromisoformat(started_at) > end:
            return False
    return True


def count_tool_breakdown(tool_calls: list[dict[str, Any]]) -> dict[str, int]:
    tool_counts: dict[str, int] = {}
    for tool_call in tool_calls:
        name = tool_call["name"]
        tool_counts[name] = tool_counts.get(name, 0) + 1
    return dict(sorted(tool_counts.items(), key=lambda item: (-item[1], item[0])))


def duration_minutes(first_ts: datetime | None, last_ts: datetime | None) -> float:
    if not first_ts or not last_ts:
        return 0.0
    return round((last_ts - first_ts).total_seconds() / 60, 1)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def compute_friction(
    *,
    bash_errors: int,
    edit_file_counts: dict[str, int],
    tool_call_count: int,
    user_message_count: int,
) -> float:
    score = 1.0
    score += min(bash_errors * 0.8, 4.0)
    score += min(sum(1 for count in edit_file_counts.values() if count >= 3), 3.0)
    if user_message_count > 0:
        density = tool_call_count / user_message_count
        if density > 50:
            score += 2.0
        elif density > 20:
            score += 1.0
    return round(clamp(score), 1)


def compute_efficiency(
    *,
    total_tokens: int,
    user_message_count: int,
    first_ts: datetime | None,
    last_ts: datetime | None,
    tool_call_count: int,
) -> float:
    score = 10.0
    if user_message_count > 0:
        tokens_per_message = total_tokens / user_message_count
        if tokens_per_message > 100_000:
            score -= 3.0
        elif tokens_per_message > 50_000:
            score -= 2.0
        elif tokens_per_message > 20_000:
            score -= 1.0
    if first_ts and last_ts and tool_call_count > 0:
        duration_seconds = (last_ts - first_ts).total_seconds()
        if duration_seconds > 0:
            calls_per_minute = tool_call_count / (duration_seconds / 60)
            if calls_per_minute < 0.5:
                score -= 2.0
            elif calls_per_minute < 1.0:
                score -= 1.0
    if total_tokens > 500_000:
        score -= 2.0
    elif total_tokens > 200_000:
        score -= 1.0
    return round(clamp(score), 1)


def compute_complexity(
    *,
    tool_call_count: int,
    unique_files_count: int,
    tool_type_count: int,
) -> float:
    score = 1.0
    if tool_call_count > 500:
        score += 3.0
    elif tool_call_count > 100:
        score += 2.0
    elif tool_call_count > 30:
        score += 1.0
    if unique_files_count > 20:
        score += 3.0
    elif unique_files_count > 10:
        score += 2.0
    elif unique_files_count > 5:
        score += 1.0
    if tool_type_count > 10:
        score += 2.0
    elif tool_type_count > 5:
        score += 1.0
    return round(clamp(score), 1)


def clamp(value: float, low: float = 1.0, high: float = 10.0) -> float:
    return max(low, min(high, value))


def detect_skill_from_messages(user_messages: list[str]) -> str | None:
    for message in user_messages[:5]:
        match = SKILL_PATTERN.search(message.strip())
        if match:
            skill = match.group(1).lower().rstrip(",.")
            if skill in KNOWN_SKILLS:
                return skill
    return None


def count_claude_errors(tool_results: list[dict[str, Any]]) -> int:
    errors = 0
    for block in tool_results:
        if block.get("is_error", False):
            errors += 1
            continue
        content = block.get("content", [])
        texts: list[str] = []
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and "text" in item:
                    texts.append(item["text"])
        joined = "\n".join(texts)
        if "Exit code" in joined and "Exit code 0" not in joined:
            errors += 1
    return errors


def count_codex_errors(tool_results: list[dict[str, Any]]) -> int:
    errors = 0
    for payload in tool_results:
        payload_type = payload.get("type")
        if payload_type == "exec_command_end" and payload.get("stderr"):
            errors += 1
        elif payload_type == "patch_apply_end" and payload.get("success") is False:
            errors += 1
        elif payload_type in {"function_call_output", "custom_tool_call_output"}:
            output = payload.get("output")
            if isinstance(output, dict) and "Err" in output:
                errors += 1
    return errors


def main() -> int:
    args = parse_args()
    result = harvest(
        source=args.source,
        since=args.since,
        until=args.until,
        claude_sources=[Path(path).expanduser() for path in args.claude_source],
        codex_sources=[Path(path).expanduser() for path in args.codex_source],
        database_url=args.database_url,
        source_machine=args.source_machine,
        log_level=args.log_level,
    )
    return 0 if result.harvested_count >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
