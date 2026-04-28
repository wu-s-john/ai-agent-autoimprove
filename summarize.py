#!/usr/bin/env python3
"""Generate structured per-session summaries for harvested conversations."""

from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field

from db import ConversationFilters, connect, list_summary_candidates, upsert_summary
from logging_utils import setup_logging
from session_utils import TRANSCRIPTS_DIR, normalize_tag_list, transcript_candidates

MAX_TRANSCRIPT_CHARS = 120_000
LOGGER = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are summarizing one AI coding session.

Be factual, concise, and concrete. Describe what happened in the session itself.

Rules:
- Do not recommend changes to prompts or skills.
- Do not generalize across other sessions.
- `summary` should be a short narrative recap of the session.
- `goal` should say what the user wanted.
- `outcome` should say what the AI actually achieved.
- `struggles` should focus on where the AI got stuck, hesitated, or wasted effort.
- `user_corrections` should describe any moments where the user redirected or corrected the AI. If none, say `None`.
- `resolution_status` must be one of `resolved`, `partially_resolved`, or `unresolved`.
- `tags` must be short lowercase topic labels, 2-8 items when possible.
"""


class SessionSummaryPayload(BaseModel):
    summary: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    struggles: str = Field(min_length=1)
    user_corrections: str = Field(min_length=1)
    resolution_status: Literal["resolved", "partially_resolved", "unresolved"]
    tags: list[str] = Field(default_factory=list)


@dataclass(slots=True)
class SummarizeResult:
    selected_count: int = 0
    created_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize harvested Claude and Codex conversations into PostgreSQL."
    )
    parser.add_argument(
        "--source",
        choices=("all", "claude", "codex"),
        default="all",
        help="Which source app to summarize (default: all)",
    )
    parser.add_argument("--query", default=None, help="Metadata query filter")
    parser.add_argument("--since", default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--until", default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=None, help="Maximum sessions to summarize")
    parser.add_argument(
        "--include-subagents",
        action="store_true",
        help="Include subagent sessions in the candidate set",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-summarize sessions even if they already have summaries",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="OpenAI model to use. Defaults to OPENAI_MODEL if set.",
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
    return parser.parse_args()


def summarize(
    *,
    source: str = "all",
    query: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
    include_subagents: bool = False,
    force: bool = False,
    model: str | None = None,
    database_url: str | None = None,
    session_ids: list[str] | None = None,
    log_level: str | None = None,
) -> SummarizeResult:
    setup_logging(log_level)
    model_name = resolve_model(model)
    client = create_openai_client()
    conn = connect(database_url=database_url)
    filters = ConversationFilters(
        source=source,
        query=query,
        since=since,
        until=until,
        include_subagents=include_subagents,
        limit=limit,
        force=force,
        session_ids=session_ids,
    )
    rows = list_summary_candidates(conn, filters)
    result = SummarizeResult(selected_count=len(rows))

    LOGGER.info("Selected %s sessions for summarization", len(rows))

    for index, row in enumerate(rows, start=1):
        LOGGER.info(
            "Summarizing %s (%s/%s)",
            row["session_id"],
            index,
            len(rows),
        )
        transcript_path, transcript_text = load_transcript(
            source_app=row["source_app"],
            native_session_id=row["native_session_id"],
            parent_session_id=row.get("parent_session_id"),
        )
        if transcript_text is None:
            result.failed_count += 1
            LOGGER.warning("Skipping %s: transcript not found", row["session_id"])
            continue

        payload = summarize_row(
            client=client,
            model=model_name,
            row=row,
            transcript_text=transcript_text,
            transcript_path=transcript_path,
        )
        upsert_summary(
            conn,
            session_id=row["session_id"],
            summary=payload.summary.strip(),
            goal=payload.goal.strip(),
            outcome=payload.outcome.strip(),
            struggles=payload.struggles.strip(),
            user_corrections=payload.user_corrections.strip(),
            resolution_status=payload.resolution_status,
            tags=normalize_tag_list(payload.tags),
            model_used=model_name,
        )
        conn.commit()
        if row.get("summarized_session_id"):
            result.updated_count += 1
            LOGGER.info("Updated summary for %s", row["session_id"])
        else:
            result.created_count += 1
            LOGGER.info("Created summary for %s", row["session_id"])

    conn.close()
    result.skipped_count = max(
        0,
        result.selected_count - result.created_count - result.updated_count - result.failed_count,
    )
    LOGGER.info(
        "Summaries complete: %s created, %s updated, %s failed",
        result.created_count,
        result.updated_count,
        result.failed_count,
    )
    return result


def resolve_model(cli_model: str | None) -> str:
    model = cli_model or os.environ.get("OPENAI_MODEL")
    if not model:
        raise RuntimeError(
            "An OpenAI model is required. Pass --model or set OPENAI_MODEL."
        )
    return model


def create_openai_client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is required. Load it through config/op.envmap or export it explicitly."
        )
    return OpenAI(api_key=api_key)


def load_transcript(
    *,
    source_app: str,
    native_session_id: str,
    parent_session_id: str | None,
) -> tuple[Path | None, str | None]:
    for candidate in transcript_candidates(
        TRANSCRIPTS_DIR,
        source_app,
        native_session_id,
        parent_session_id=parent_session_id,
    ):
        if candidate.exists():
            return candidate, candidate.read_text(encoding="utf-8")
    return None, None


def summarize_row(
    *,
    client: OpenAI,
    model: str,
    row: dict,
    transcript_text: str,
    transcript_path: Path | None,
) -> SessionSummaryPayload:
    metadata = {
        "session_id": row["session_id"],
        "source_app": row["source_app"],
        "native_session_id": row["native_session_id"],
        "project": row.get("project"),
        "cwd": row.get("cwd"),
        "model": row.get("model"),
        "started_at": row.get("started_at"),
        "ended_at": row.get("ended_at"),
        "duration_minutes": row.get("duration_minutes"),
        "detected_skill": row.get("detected_skill"),
        "friction_score": row.get("friction_score"),
        "efficiency_score": row.get("efficiency_score"),
        "complexity_score": row.get("complexity_score"),
        "first_user_message": row.get("first_user_message"),
        "transcript_path": str(transcript_path) if transcript_path else None,
    }
    prompt = (
        "Session metadata:\n"
        f"{json.dumps(metadata, indent=2)}\n\n"
        "Transcript:\n"
        f"{truncate_transcript(transcript_text)}"
    )

    if hasattr(client.responses, "parse"):
        response = client.responses.parse(
            model=model,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            text_format=SessionSummaryPayload,
        )
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise RuntimeError(f"No structured output returned for {row['session_id']}")
        return parsed

    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "session_summary",
                "schema": SessionSummaryPayload.model_json_schema(),
                "strict": True,
            }
        },
    )
    output_text = getattr(response, "output_text", None)
    if not output_text:
        raise RuntimeError(f"No text output returned for {row['session_id']}")
    return SessionSummaryPayload.model_validate_json(output_text)


def truncate_transcript(transcript_text: str) -> str:
    if len(transcript_text) <= MAX_TRANSCRIPT_CHARS:
        return transcript_text
    head = transcript_text[: MAX_TRANSCRIPT_CHARS // 2]
    tail = transcript_text[-MAX_TRANSCRIPT_CHARS // 3 :]
    return (
        f"{head}\n\n[... transcript truncated for summarization ...]\n\n{tail}"
    )


def main() -> int:
    args = parse_args()
    summarize(
        source=args.source,
        query=args.query,
        since=args.since,
        until=args.until,
        limit=args.limit,
        include_subagents=args.include_subagents,
        force=args.force,
        model=args.model,
        database_url=args.database_url,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
