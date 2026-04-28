"""Internal helper primitives for the autoimprove skill."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

from db import (
    ConversationFilters,
    IMPROVEMENT_STATUSES,
    IMPROVEMENT_TYPES,
    connect,
    insert_analysis_run,
    insert_improvements,
    list_analysis_candidates,
)
from session_utils import TRANSCRIPTS_DIR, transcript_candidates

REPORT_SECTION_ORDER = (
    "Observed Patterns",
    "Skill Improvements",
    "CLI Opportunities",
    "External Tool Recommendations",
)


@dataclass(slots=True)
class ImprovementProposal:
    improvement_type: str
    target_name: str
    description: str
    rationale: str
    evidence_session_ids: list[str]
    web_references: list[dict[str, str]] | None = None
    status: str = "proposed"
    skill_name: str | None = None
    diff: str | None = None
    improvement_id: str | None = None
    applied_at: str | None = None


def select_analysis_cohort(
    *,
    source: str = "all",
    query: str | None = None,
    since: str | None = None,
    until: str | None = None,
    include_subagents: bool = False,
    limit: int | None = None,
    session_ids: list[str] | None = None,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    conn = connect(database_url=database_url)
    try:
        return list_analysis_candidates(
            conn,
            ConversationFilters(
                source=source,
                query=query,
                since=since,
                until=until,
                include_subagents=include_subagents,
                limit=limit,
                session_ids=session_ids,
            ),
        )
    finally:
        conn.close()


def load_session_evidence(
    session: Mapping[str, Any],
    *,
    transcripts_dir: Path = TRANSCRIPTS_DIR,
) -> dict[str, Any]:
    transcript_path: Path | None = None
    transcript_text: str | None = None
    for candidate in transcript_candidates(
        transcripts_dir,
        str(session["source_app"]),
        str(session["native_session_id"]),
        parent_session_id=session.get("parent_session_id"),
    ):
        if candidate.exists():
            transcript_path = candidate
            transcript_text = candidate.read_text(encoding="utf-8")
            break

    raw_jsonl_path = None
    raw_jsonl_exists = False
    if session.get("file_path"):
        raw_jsonl_path = Path(str(session["file_path"]))
        raw_jsonl_exists = raw_jsonl_path.exists()

    return {
        "session_id": session.get("session_id"),
        "transcript_path": transcript_path,
        "transcript_text": transcript_text,
        "raw_jsonl_path": raw_jsonl_path,
        "raw_jsonl_exists": raw_jsonl_exists,
    }


def group_recurring_patterns(
    rows: Sequence[Mapping[str, Any]],
    *,
    top_n: int = 10,
) -> dict[str, list[dict[str, Any]]]:
    return {
        "tags": _group_rows_by_tag(rows, top_n=top_n),
        "detected_skills": _group_rows_by_field(rows, "detected_skill", top_n=top_n),
        "resolution_statuses": _group_rows_by_field(rows, "resolution_status", top_n=top_n),
        "struggles": _group_rows_by_field(rows, "struggles", top_n=top_n),
    }


def normalize_improvement_payload(
    improvement: ImprovementProposal | Mapping[str, Any],
) -> dict[str, Any]:
    if is_dataclass(improvement):
        raw = asdict(improvement)
    else:
        raw = dict(improvement)

    improvement_type = str(raw.get("improvement_type", "skill")).strip().lower()
    if improvement_type == "mixed":
        raise ValueError(
            "Mixed findings must be split into multiple persisted improvements."
        )
    if improvement_type not in IMPROVEMENT_TYPES:
        raise ValueError(f"Invalid improvement_type: {improvement_type}")

    status = str(raw.get("status", "proposed")).strip().lower()
    if status not in IMPROVEMENT_STATUSES:
        raise ValueError(f"Invalid improvement status: {status}")

    target_name = str(raw.get("target_name") or raw.get("skill_name") or "").strip()
    if not target_name:
        raise ValueError("Improvement proposals require a target_name")

    evidence_session_ids = [
        str(session_id).strip()
        for session_id in raw.get("evidence_session_ids", [])
        if str(session_id).strip()
    ]
    web_references = normalize_web_references(raw.get("web_references", []))

    description = str(raw.get("description") or "").strip()
    rationale = str(raw.get("rationale") or description).strip()

    return {
        "improvement_id": raw.get("improvement_id"),
        "improvement_type": improvement_type,
        "target_name": target_name,
        "skill_name": str(raw.get("skill_name") or target_name).strip(),
        "description": description,
        "rationale": rationale,
        "diff": raw.get("diff"),
        "status": status,
        "applied_at": raw.get("applied_at"),
        "evidence_session_ids": evidence_session_ids,
        "source_session_ids": ",".join(evidence_session_ids),
        "web_references": web_references,
    }


def persist_analysis_artifacts(
    *,
    report_markdown: str,
    recommendations: Sequence[ImprovementProposal | Mapping[str, Any]],
    cohort_rows: Sequence[Mapping[str, Any]],
    query_text: str | None = None,
    filters: ConversationFilters | Mapping[str, Any] | None = None,
    research_performed: bool = False,
    model_used: str | None = None,
    findings: str | None = None,
    run_id: str | None = None,
    database_url: str | None = None,
) -> str:
    normalized_recommendations = [
        normalize_improvement_payload(recommendation)
        for recommendation in recommendations
    ]
    analyzed_from, analyzed_to = derive_analysis_window(cohort_rows)
    skills_affected = ", ".join(
        sorted(
            {
                recommendation["target_name"]
                for recommendation in normalized_recommendations
                if recommendation["improvement_type"] == "skill"
            }
        )
    ) or None

    conn = connect(database_url=database_url)
    try:
        actual_run_id = insert_analysis_run(
            conn,
            run_id=run_id,
            analyzed_from=analyzed_from,
            analyzed_to=analyzed_to,
            conversation_count=len(cohort_rows),
            findings=findings,
            skills_affected=skills_affected,
            query_text=query_text,
            filters_json=_filters_to_json(filters),
            report_markdown=report_markdown.strip(),
            research_performed=research_performed,
            model_used=model_used,
        )
        insert_improvements(
            conn,
            run_id=actual_run_id,
            improvements=normalized_recommendations,
        )
        conn.commit()
        return actual_run_id
    finally:
        conn.close()


def render_analysis_report(sections: Mapping[str, str | Sequence[str]]) -> str:
    blocks: list[str] = []
    for title in REPORT_SECTION_ORDER:
        body = sections.get(title, "")
        if isinstance(body, str):
            content = body.strip() or "- None."
        else:
            items = [str(item).strip() for item in body if str(item).strip()]
            content = "\n".join(f"- {item}" for item in items) or "- None."
        blocks.append(f"## {title}\n\n{content}")
    return "\n\n".join(blocks).strip() + "\n"


def derive_analysis_window(rows: Sequence[Mapping[str, Any]]) -> tuple[str | None, str | None]:
    timestamps = [
        str(row["started_at"])
        for row in rows
        if row.get("started_at")
    ]
    if not timestamps:
        return None, None
    return min(timestamps), max(timestamps)


def normalize_web_references(
    references: Iterable[str | Mapping[str, Any]] | None,
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    if references is None:
        return normalized

    for reference in references:
        if isinstance(reference, str):
            url = reference.strip()
            if not url:
                continue
            parsed = urlparse(url)
            title = parsed.netloc or url
            domain = parsed.netloc
        else:
            url = str(reference.get("url") or "").strip()
            if not url:
                continue
            parsed = urlparse(url)
            title = str(reference.get("title") or parsed.netloc or url).strip()
            domain = str(reference.get("domain") or parsed.netloc).strip()

        item = {
            "title": title,
            "url": url,
            "domain": domain,
        }
        dedupe_key = (item["title"], item["url"], item["domain"])
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(item)

    return normalized


def _filters_to_json(
    filters: ConversationFilters | Mapping[str, Any] | None,
) -> dict[str, Any]:
    if filters is None:
        return {}
    if isinstance(filters, ConversationFilters):
        return asdict(filters)
    return dict(filters)


def _group_rows_by_tag(
    rows: Sequence[Mapping[str, Any]],
    *,
    top_n: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        for tag in row.get("tags") or []:
            label = _normalize_group_label(tag)
            if not label:
                continue
            grouped[label].add(str(row["session_id"]))
    return _finalize_grouped_patterns(grouped, top_n=top_n)


def _group_rows_by_field(
    rows: Sequence[Mapping[str, Any]],
    field_name: str,
    *,
    top_n: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        label = _normalize_group_label(row.get(field_name))
        if not label:
            continue
        grouped[label].add(str(row["session_id"]))
    return _finalize_grouped_patterns(grouped, top_n=top_n)


def _finalize_grouped_patterns(
    grouped: Mapping[str, set[str]],
    *,
    top_n: int,
) -> list[dict[str, Any]]:
    ranked = sorted(
        grouped.items(),
        key=lambda item: (-len(item[1]), item[0]),
    )
    return [
        {
            "label": label,
            "count": len(session_ids),
            "session_ids": sorted(session_ids),
        }
        for label, session_ids in ranked[:top_n]
    ]


def _normalize_group_label(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip().lower()
    if not text or text == "none":
        return None
    return text
