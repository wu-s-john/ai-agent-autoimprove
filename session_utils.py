"""Shared session and transcript helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

TRANSCRIPTS_DIR = Path(__file__).parent / "transcripts"
DEFAULT_CLAUDE_SOURCE = Path.home() / ".claude" / "projects"
DEFAULT_CODEX_SOURCES = (
    Path.home() / ".codex" / "sessions",
    Path.home() / ".codex" / "archived_sessions",
)


def canonical_session_id(source_app: str, native_session_id: str) -> str:
    return f"{source_app}:{native_session_id}"


def native_session_id_from_canonical(session_id: str | None) -> str | None:
    if not session_id:
        return None
    if ":" not in session_id:
        return session_id
    return session_id.split(":", 1)[1]


def transcript_path(
    transcripts_dir: Path,
    source_app: str,
    native_session_id: str,
    *,
    parent_session_id: str | None = None,
) -> Path:
    base_dir = transcripts_dir / source_app
    parent_native = native_session_id_from_canonical(parent_session_id)
    if parent_native:
        return base_dir / parent_native / f"{native_session_id}.md"
    return base_dir / f"{native_session_id}.md"


def legacy_claude_transcript_path(
    transcripts_dir: Path,
    native_session_id: str,
    *,
    parent_session_id: str | None = None,
) -> Path:
    parent_native = native_session_id_from_canonical(parent_session_id)
    if parent_native:
        return transcripts_dir / parent_native / f"{native_session_id}.md"
    return transcripts_dir / f"{native_session_id}.md"


def transcript_candidates(
    transcripts_dir: Path,
    source_app: str,
    native_session_id: str,
    *,
    parent_session_id: str | None = None,
) -> list[Path]:
    candidates = [
        transcript_path(
            transcripts_dir,
            source_app,
            native_session_id,
            parent_session_id=parent_session_id,
        )
    ]
    if source_app == "claude":
        candidates.append(
            legacy_claude_transcript_path(
                transcripts_dir,
                native_session_id,
                parent_session_id=parent_session_id,
            )
        )
    return candidates


def render_transcript(data: dict[str, Any]) -> str:
    events = data.get("_events", [])
    lines: list[str] = [
        f"# Conversation: {data['session_id']}",
        "",
        f"- **Source**: {data.get('source_app', 'unknown')}",
        f"- **Native Session ID**: {data.get('native_session_id', 'unknown')}",
        f"- **Project**: {data.get('project', 'unknown')}",
        f"- **Model**: {data.get('model', 'unknown')}",
        f"- **Started**: {data.get('started_at', '?')}",
        f"- **Duration**: {data.get('duration_minutes', 0)} min",
        f"- **Friction**: {data.get('friction_score', '?')}/10",
        f"- **Efficiency**: {data.get('efficiency_score', '?')}/10",
        f"- **Complexity**: {data.get('complexity_score', '?')}/10",
    ]
    if data.get("detected_skill"):
        lines.append(f"- **Skill**: {data['detected_skill']}")
    if data.get("agent_role"):
        lines.append(f"- **Agent Role**: {data['agent_role']}")
    if data.get("agent_name"):
        lines.append(f"- **Agent Name**: {data['agent_name']}")
    lines.extend(["", "---", ""])

    for event in events:
        etype = event["type"]
        if etype == "user":
            lines.extend(["## User", "", event["text"], ""])
            continue
        if etype == "assistant_text":
            lines.extend(["## Assistant", "", event["text"], ""])
            continue
        if etype == "tool_use":
            name = event["name"]
            one_liner = tool_call_one_liner(name, event.get("input", {}))
            lines.extend([f"> **{name}**: {one_liner}", ""])

    return "\n".join(lines)


def write_transcript(data: dict[str, Any], output_dir: Path = TRANSCRIPTS_DIR) -> Path:
    out_path = transcript_path(
        output_dir,
        data["source_app"],
        data["native_session_id"],
        parent_session_id=data.get("parent_session_id"),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_transcript(data), encoding="utf-8")
    return out_path


def tool_call_one_liner(name: str, inp: Any) -> str:
    if isinstance(inp, str):
        text = inp
        return f"`{text[:120]}{'...' if len(text) > 120 else ''}`"
    if not isinstance(inp, dict):
        return str(inp)[:120] if inp is not None else "(no input)"

    if name in {"Bash", "exec_command"}:
        cmd = inp.get("command", "")
        if isinstance(cmd, list):
            cmd = " ".join(str(part) for part in cmd)
        return f"`{str(cmd)[:120]}{'...' if len(str(cmd)) > 120 else ''}`"
    if name in {"Edit", "Write"}:
        fp = inp.get("file_path", inp.get("path", "?"))
        return short_path(str(fp))
    if name in {"Read", "exec_command_read"}:
        fp = inp.get("file_path", inp.get("path", "?"))
        return short_path(str(fp))
    if name in {"Grep", "Glob"}:
        pattern = inp.get("pattern", "?")
        path = inp.get("path", ".")
        return f"`{pattern}` in {short_path(str(path))}"
    if name == "Skill":
        return str(inp.get("skill", "?"))
    if name == "web_search":
        query = inp.get("query") or inp.get("url") or inp.get("action", {}).get("query", "?")
        return f"`{str(query)[:120]}`"
    if name.startswith("mcp__"):
        first_key = next(iter(inp), None)
        if first_key is None:
            return "(no input)"
        return f"{first_key}={str(inp[first_key])[:120]}"
    first_key = next(iter(inp), None)
    if first_key is None:
        return "(no input)"
    return f"{first_key}={str(inp[first_key])[:120]}"


def short_path(file_path: str) -> str:
    parts = Path(file_path).parts
    if len(parts) > 3:
        return f".../{'/'.join(parts[-3:])}"
    return file_path


def normalize_tag_list(tags: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        clean = tag.strip().lower().replace(" ", "-")
        if not clean:
            continue
        if clean in seen:
            continue
        seen.add(clean)
        normalized.append(clean)
    return normalized


def json_or_none(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
