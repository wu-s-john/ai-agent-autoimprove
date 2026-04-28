"""Database helpers for the shared PostgreSQL conversation index."""

from __future__ import annotations

import os
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.parse import quote
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

CONVERSATION_COLUMNS = (
    "session_id",
    "source_app",
    "native_session_id",
    "project",
    "cwd",
    "model",
    "started_at",
    "ended_at",
    "duration_minutes",
    "user_message_count",
    "assistant_message_count",
    "tool_call_count",
    "tool_breakdown",
    "input_tokens",
    "output_tokens",
    "friction_score",
    "efficiency_score",
    "complexity_score",
    "detected_skill",
    "first_user_message",
    "file_path",
    "file_size_bytes",
    "is_subagent",
    "parent_session_id",
    "source_machine",
    "agent_role",
    "agent_name",
)

SUMMARY_RESOLUTION_STATUSES = ("resolved", "partially_resolved", "unresolved")
IMPROVEMENT_TYPES = ("skill", "cli", "tool")
IMPROVEMENT_STATUSES = ("proposed", "approved", "rejected", "implemented")


@dataclass(slots=True)
class ConversationFilters:
    source: str = "all"
    query: str | None = None
    since: str | None = None
    until: str | None = None
    include_subagents: bool = False
    limit: int | None = None
    force: bool = False
    session_ids: list[str] | None = None


def _build_database_url_from_env(prefix: str = "") -> str | None:
    env_prefix = f"{prefix}_" if prefix else ""
    host = os.environ.get(f"{env_prefix}PGHOST" if prefix else "PGHOST")
    port = os.environ.get(f"{env_prefix}PGPORT" if prefix else "PGPORT")
    database = os.environ.get(f"{env_prefix}PGDATABASE" if prefix else "PGDATABASE")
    user = os.environ.get(f"{env_prefix}PGUSER" if prefix else "PGUSER")
    password = os.environ.get(f"{env_prefix}PGPASSWORD" if prefix else "PGPASSWORD")
    sslmode = os.environ.get(f"{env_prefix}PGSSLMODE" if prefix else "PGSSLMODE")

    if prefix:
        host = os.environ.get(f"{prefix}_HOST", host)
        port = os.environ.get(f"{prefix}_PORT", port)
        database = os.environ.get(f"{prefix}_DATABASE", database)
        user = os.environ.get(f"{prefix}_USER", user)
        password = os.environ.get(f"{prefix}_PASSWORD", password)
        sslmode = os.environ.get(f"{prefix}_SSLMODE", sslmode)

    required = (host, port, database, user, password)
    if not all(required):
        return None

    url = (
        f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{host}:{port}/{quote(database, safe='')}"
    )
    if sslmode:
        url += f"?sslmode={quote(sslmode, safe='')}"
    return url


def resolve_database_url(
    database_url: str | None = None,
    *,
    env_prefix: str = "",
) -> str:
    url = database_url
    if not url:
        if env_prefix:
            url = _build_database_url_from_env(prefix=env_prefix)
        if not url:
            url = os.environ.get("DATABASE_URL")
        if not url and not env_prefix:
            url = _build_database_url_from_env(prefix=env_prefix)
    if not url:
        prefix_hint = f"{env_prefix}_*" if env_prefix else "PG* or DATABASE_URL"
        raise RuntimeError(
            "PostgreSQL credentials are required. Set DATABASE_URL or the "
            f"{prefix_hint} environment variables."
        )
    if not url.startswith(("postgres://", "postgresql://")):
        raise ValueError(
            "Only PostgreSQL is supported. DATABASE_URL must start with "
            "`postgres://` or `postgresql://`."
        )
    return url


def connect(
    database_url: str | None = None,
    *,
    env_prefix: str = "",
) -> Any:
    return psycopg.connect(
        resolve_database_url(database_url=database_url, env_prefix=env_prefix),
        row_factory=dict_row,
    )


def _column_exists(conn: Any, table_name: str, column_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = %s
          AND column_name = %s
        """,
        (table_name, column_name),
    ).fetchone()
    return row is not None


def _ensure_column(
    conn: Any,
    table_name: str,
    column_name: str,
    column_type_sql: str,
) -> None:
    if _column_exists(conn, table_name, column_name):
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type_sql}")


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = current_schema()
          AND table_name = %s
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _parse_session_ids_text(value: str | None) -> list[str]:
    if not value:
        return []

    raw = value.strip()
    if raw.startswith("[") and raw.endswith("]"):
        try:
            json_loaded = json.loads(raw)
        except json.JSONDecodeError:
            json_loaded = None
        if isinstance(json_loaded, list):
            return [str(item).strip() for item in json_loaded if str(item).strip()]

    parts = [part.strip() for part in raw.replace("\n", ",").split(",")]
    return [part for part in parts if part]


def init_db(
    database_url: str | None = None,
) -> Any:
    conn = connect(database_url=database_url)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            session_id TEXT PRIMARY KEY,
            source_app TEXT NOT NULL,
            native_session_id TEXT NOT NULL,
            project TEXT NOT NULL,
            cwd TEXT,
            model TEXT,
            started_at TEXT,
            ended_at TEXT,
            duration_minutes REAL,
            user_message_count INTEGER,
            assistant_message_count INTEGER,
            tool_call_count INTEGER,
            tool_breakdown TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            friction_score REAL,
            efficiency_score REAL,
            complexity_score REAL,
            detected_skill TEXT,
            first_user_message TEXT,
            file_path TEXT,
            file_size_bytes INTEGER,
            is_subagent INTEGER DEFAULT 0,
            parent_session_id TEXT,
            source_machine TEXT,
            agent_role TEXT,
            agent_name TEXT,
            harvested_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS summaries (
            session_id TEXT PRIMARY KEY REFERENCES conversations(session_id) ON DELETE CASCADE,
            summary TEXT,
            goal TEXT,
            outcome TEXT,
            issues TEXT,
            struggles TEXT,
            user_corrections TEXT,
            resolution_status TEXT CHECK (
                resolution_status IN ('resolved', 'partially_resolved', 'unresolved')
            ),
            tags JSONB NOT NULL DEFAULT '[]'::jsonb,
            generated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            model_used TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS analysis_runs (
            run_id TEXT PRIMARY KEY,
            ran_at TEXT DEFAULT CURRENT_TIMESTAMP,
            analyzed_from TEXT,
            analyzed_to TEXT,
            conversation_count INTEGER,
            findings TEXT,
            skills_affected TEXT,
            query_text TEXT,
            filters_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            report_markdown TEXT,
            research_performed BOOLEAN NOT NULL DEFAULT FALSE,
            model_used TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS improvements (
            improvement_id TEXT PRIMARY KEY,
            run_id TEXT REFERENCES analysis_runs(run_id),
            skill_name TEXT NOT NULL,
            description TEXT,
            diff TEXT,
            source_session_ids TEXT,
            applied_at TEXT,
            improvement_type TEXT CHECK (
                improvement_type IN ('skill', 'cli', 'tool')
            ),
            target_name TEXT,
            status TEXT NOT NULL DEFAULT 'proposed' CHECK (
                status IN ('proposed', 'approved', 'rejected', 'implemented')
            ),
            rationale TEXT,
            evidence_session_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            web_references JSONB NOT NULL DEFAULT '[]'::jsonb
        )
        """
    )

    for column_name, column_type in (
        ("source_app", "TEXT"),
        ("native_session_id", "TEXT"),
        ("source_machine", "TEXT"),
        ("agent_role", "TEXT"),
        ("agent_name", "TEXT"),
    ):
        _ensure_column(conn, "conversations", column_name, column_type)

    for column_name, column_type in (
        ("struggles", "TEXT"),
        ("user_corrections", "TEXT"),
        (
            "resolution_status",
            "TEXT CHECK (resolution_status IN ('resolved', 'partially_resolved', 'unresolved'))",
        ),
        ("tags", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
    ):
        _ensure_column(conn, "summaries", column_name, column_type)

    for column_name, column_type in (
        ("query_text", "TEXT"),
        ("filters_json", "JSONB NOT NULL DEFAULT '{}'::jsonb"),
        ("report_markdown", "TEXT"),
        ("research_performed", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("model_used", "TEXT"),
    ):
        _ensure_column(conn, "analysis_runs", column_name, column_type)

    for column_name, column_type in (
        (
            "improvement_type",
            "TEXT CHECK (improvement_type IN ('skill', 'cli', 'tool'))",
        ),
        ("target_name", "TEXT"),
        (
            "status",
            "TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'approved', 'rejected', 'implemented'))",
        ),
        ("rationale", "TEXT"),
        ("evidence_session_ids", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
        ("web_references", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
    ):
        _ensure_column(conn, "improvements", column_name, column_type)

    conn.execute("UPDATE summaries SET tags = '[]'::jsonb WHERE tags IS NULL")
    conn.execute("UPDATE analysis_runs SET filters_json = '{}'::jsonb WHERE filters_json IS NULL")
    conn.execute(
        """
        UPDATE analysis_runs
        SET research_performed = FALSE
        WHERE research_performed IS NULL
        """
    )
    conn.execute(
        """
        UPDATE analysis_runs
        SET report_markdown = COALESCE(report_markdown, findings)
        WHERE report_markdown IS NULL
        """
    )
    conn.execute(
        """
        UPDATE improvements
        SET improvement_type = COALESCE(improvement_type, 'skill')
        WHERE improvement_type IS NULL
        """
    )
    conn.execute(
        """
        UPDATE improvements
        SET target_name = COALESCE(target_name, skill_name)
        WHERE target_name IS NULL
        """
    )
    conn.execute(
        """
        UPDATE improvements
        SET status = CASE
            WHEN status IS NOT NULL THEN status
            WHEN applied_at IS NOT NULL THEN 'implemented'
            ELSE 'proposed'
        END
        WHERE status IS NULL
        """
    )
    conn.execute(
        """
        UPDATE improvements
        SET rationale = COALESCE(rationale, description)
        WHERE rationale IS NULL
        """
    )
    conn.execute(
        """
        UPDATE improvements
        SET evidence_session_ids = '[]'::jsonb
        WHERE evidence_session_ids IS NULL
        """
    )
    conn.execute(
        """
        UPDATE improvements
        SET web_references = '[]'::jsonb
        WHERE web_references IS NULL
        """
    )
    conn.execute("ALTER TABLE improvements ALTER COLUMN applied_at DROP DEFAULT")

    if _table_exists(conn, "conversations"):
        conn.execute(
            """
            UPDATE conversations
            SET source_app = COALESCE(NULLIF(source_app, ''), 'claude')
            WHERE source_app IS NULL OR source_app = ''
            """
        )
        conn.execute(
            """
            UPDATE conversations
            SET native_session_id = COALESCE(NULLIF(native_session_id, ''), session_id)
            WHERE native_session_id IS NULL OR native_session_id = ''
            """
        )

    if _table_exists(conn, "improvements"):
        rows = conn.execute(
            """
            SELECT improvement_id, source_session_ids, evidence_session_ids
            FROM improvements
            """
        ).fetchall()
        for row in rows:
            if row.get("evidence_session_ids") not in (None, []):
                continue
            parsed_session_ids = _parse_session_ids_text(row.get("source_session_ids"))
            conn.execute(
                """
                UPDATE improvements
                SET evidence_session_ids = %s
                WHERE improvement_id = %s
                """,
                (Jsonb(parsed_session_ids), row["improvement_id"]),
            )

    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_conv_source_native ON conversations(source_app, native_session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_project ON conversations(project)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_started ON conversations(source_app, started_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_friction ON conversations(friction_score)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_parent ON conversations(parent_session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_skill ON conversations(detected_skill)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_native_session ON conversations(native_session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_source_machine ON conversations(source_machine)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_summaries_resolution_status ON summaries(resolution_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_summaries_tags ON summaries USING GIN(tags)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_improvements_skill ON improvements(skill_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_improvements_type ON improvements(improvement_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_improvements_status ON improvements(status)")

    conn.commit()
    return conn


def upsert_conversation(conn: Any, data: dict[str, Any]) -> None:
    values = [data.get(column) for column in CONVERSATION_COLUMNS]
    columns_sql = ", ".join(CONVERSATION_COLUMNS)
    placeholders = ", ".join("%s" for _ in CONVERSATION_COLUMNS)
    updates = ", ".join(
        f"{column} = EXCLUDED.{column}"
        for column in CONVERSATION_COLUMNS
        if column != "session_id"
    )
    conn.execute(
        f"""
        INSERT INTO conversations ({columns_sql})
        VALUES ({placeholders})
        ON CONFLICT (session_id) DO UPDATE SET
            {updates}
        """,
        values,
    )


def upsert_summary(
    conn: Any,
    *,
    session_id: str,
    summary: str,
    goal: str,
    outcome: str,
    struggles: str,
    user_corrections: str,
    resolution_status: str,
    tags: list[str],
    model_used: str,
) -> None:
    if resolution_status not in SUMMARY_RESOLUTION_STATUSES:
        raise ValueError(f"Invalid resolution_status: {resolution_status}")

    tags_json = Jsonb(tags)
    conn.execute(
        """
        INSERT INTO summaries (
            session_id,
            summary,
            goal,
            outcome,
            issues,
            struggles,
            user_corrections,
            resolution_status,
            tags,
            model_used
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (session_id) DO UPDATE SET
            summary = EXCLUDED.summary,
            goal = EXCLUDED.goal,
            outcome = EXCLUDED.outcome,
            issues = EXCLUDED.issues,
            struggles = EXCLUDED.struggles,
            user_corrections = EXCLUDED.user_corrections,
            resolution_status = EXCLUDED.resolution_status,
            tags = EXCLUDED.tags,
            model_used = EXCLUDED.model_used,
            generated_at = CURRENT_TIMESTAMP
        """,
        (
            session_id,
            summary,
            goal,
            outcome,
            struggles,
            struggles,
            user_corrections,
            resolution_status,
            tags_json,
            model_used,
        ),
    )


def list_summary_candidates(
    conn: Any,
    filters: ConversationFilters,
) -> list[dict[str, Any]]:
    where = ["1 = 1"]
    params: list[Any] = []

    if filters.source != "all":
        where.append("c.source_app = %s")
        params.append(filters.source)

    if not filters.include_subagents:
        where.append("COALESCE(c.is_subagent, 0) = 0")

    if filters.since:
        where.append("NULLIF(c.started_at, '')::timestamptz >= %s::timestamptz")
        params.append(f"{filters.since}T00:00:00+00:00")

    if filters.until:
        where.append("NULLIF(c.started_at, '')::timestamptz <= %s::timestamptz")
        params.append(f"{filters.until}T23:59:59+00:00")

    if filters.query:
        pattern = f"%{filters.query}%"
        where.append(
            """
            (
                c.project ILIKE %s OR
                COALESCE(c.cwd, '') ILIKE %s OR
                COALESCE(c.detected_skill, '') ILIKE %s OR
                COALESCE(c.first_user_message, '') ILIKE %s OR
                c.source_app ILIKE %s OR
                c.native_session_id ILIKE %s
            )
            """
        )
        params.extend([pattern] * 6)

    if filters.session_ids:
        where.append("c.session_id = ANY(%s)")
        params.append(filters.session_ids)

    if not filters.force:
        where.append("s.session_id IS NULL")

    sql = f"""
        SELECT
            c.session_id,
            c.source_app,
            c.native_session_id,
            c.parent_session_id,
            c.project,
            c.cwd,
            c.model,
            c.started_at,
            c.ended_at,
            c.duration_minutes,
            c.detected_skill,
            c.first_user_message,
            c.friction_score,
            c.efficiency_score,
            c.complexity_score,
            c.is_subagent,
            s.session_id AS summarized_session_id
        FROM conversations c
        LEFT JOIN summaries s ON s.session_id = c.session_id
        WHERE {' AND '.join(where)}
        ORDER BY NULLIF(c.started_at, '')::timestamptz DESC NULLS LAST
    """

    if filters.limit is not None:
        sql += " LIMIT %s"
        params.append(filters.limit)

    return list(conn.execute(sql, params).fetchall())


def list_analysis_candidates(
    conn: Any,
    filters: ConversationFilters,
) -> list[dict[str, Any]]:
    where = ["1 = 1"]
    params: list[Any] = []

    if filters.source != "all":
        where.append("c.source_app = %s")
        params.append(filters.source)

    if not filters.include_subagents:
        where.append("COALESCE(c.is_subagent, 0) = 0")

    if filters.since:
        where.append("NULLIF(c.started_at, '')::timestamptz >= %s::timestamptz")
        params.append(f"{filters.since}T00:00:00+00:00")

    if filters.until:
        where.append("NULLIF(c.started_at, '')::timestamptz <= %s::timestamptz")
        params.append(f"{filters.until}T23:59:59+00:00")

    if filters.query:
        pattern = f"%{filters.query}%"
        where.append(
            """
            (
                COALESCE(c.project, '') ILIKE %s OR
                COALESCE(c.cwd, '') ILIKE %s OR
                COALESCE(c.detected_skill, '') ILIKE %s OR
                COALESCE(c.first_user_message, '') ILIKE %s OR
                COALESCE(s.summary, '') ILIKE %s OR
                COALESCE(s.struggles, '') ILIKE %s OR
                COALESCE(s.user_corrections, '') ILIKE %s OR
                COALESCE(s.tags::text, '[]') ILIKE %s OR
                COALESCE(c.source_app, '') ILIKE %s OR
                COALESCE(c.native_session_id, '') ILIKE %s
            )
            """
        )
        params.extend([pattern] * 10)

    if filters.session_ids:
        where.append("c.session_id = ANY(%s)")
        params.append(filters.session_ids)

    sql = f"""
        SELECT
            c.session_id,
            c.source_app,
            c.native_session_id,
            c.parent_session_id,
            c.project,
            c.cwd,
            c.model,
            c.started_at,
            c.ended_at,
            c.duration_minutes,
            c.detected_skill,
            c.first_user_message,
            c.friction_score,
            c.efficiency_score,
            c.complexity_score,
            c.is_subagent,
            c.file_path,
            c.agent_role,
            c.agent_name,
            s.summary,
            s.goal,
            s.outcome,
            s.issues,
            s.struggles,
            s.user_corrections,
            s.resolution_status,
            s.tags,
            s.generated_at,
            s.model_used
        FROM conversations c
        JOIN summaries s ON s.session_id = c.session_id
        WHERE {' AND '.join(where)}
        ORDER BY
            COALESCE(c.friction_score, 0) DESC,
            NULLIF(c.started_at, '')::timestamptz DESC NULLS LAST
    """

    if filters.limit is not None:
        sql += " LIMIT %s"
        params.append(filters.limit)

    return list(conn.execute(sql, params).fetchall())


def insert_analysis_run(
    conn: Any,
    *,
    run_id: str | None = None,
    analyzed_from: str | None = None,
    analyzed_to: str | None = None,
    conversation_count: int = 0,
    findings: str | None = None,
    skills_affected: str | None = None,
    query_text: str | None = None,
    filters_json: Mapping[str, Any] | None = None,
    report_markdown: str | None = None,
    research_performed: bool = False,
    model_used: str | None = None,
) -> str:
    actual_run_id = run_id or uuid4().hex
    findings_text = findings if findings is not None else report_markdown
    conn.execute(
        """
        INSERT INTO analysis_runs (
            run_id,
            analyzed_from,
            analyzed_to,
            conversation_count,
            findings,
            skills_affected,
            query_text,
            filters_json,
            report_markdown,
            research_performed,
            model_used
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (run_id) DO UPDATE SET
            analyzed_from = EXCLUDED.analyzed_from,
            analyzed_to = EXCLUDED.analyzed_to,
            conversation_count = EXCLUDED.conversation_count,
            findings = EXCLUDED.findings,
            skills_affected = EXCLUDED.skills_affected,
            query_text = EXCLUDED.query_text,
            filters_json = EXCLUDED.filters_json,
            report_markdown = EXCLUDED.report_markdown,
            research_performed = EXCLUDED.research_performed,
            model_used = EXCLUDED.model_used
        """,
        (
            actual_run_id,
            analyzed_from,
            analyzed_to,
            conversation_count,
            findings_text,
            skills_affected,
            query_text,
            Jsonb(dict(filters_json or {})),
            report_markdown,
            research_performed,
            model_used,
        ),
    )
    return actual_run_id


def insert_improvements(
    conn: Any,
    *,
    run_id: str,
    improvements: Sequence[Mapping[str, Any]],
) -> list[str]:
    inserted_ids: list[str] = []

    for improvement in improvements:
        improvement_type = str(
            improvement.get("improvement_type", "skill")
        ).strip().lower()
        if improvement_type not in IMPROVEMENT_TYPES:
            raise ValueError(f"Invalid improvement_type: {improvement_type}")

        status = str(improvement.get("status", "proposed")).strip().lower()
        if status not in IMPROVEMENT_STATUSES:
            raise ValueError(f"Invalid improvement status: {status}")

        target_name = (
            str(improvement.get("target_name") or improvement.get("skill_name") or "").strip()
        )
        if not target_name:
            raise ValueError("Each improvement requires a target_name")

        evidence_session_ids = [
            str(session_id).strip()
            for session_id in improvement.get("evidence_session_ids", [])
            if str(session_id).strip()
        ]
        if not evidence_session_ids:
            evidence_session_ids = _parse_session_ids_text(
                str(improvement.get("source_session_ids") or "")
            )
        web_references = list(improvement.get("web_references", []))
        improvement_id = str(improvement.get("improvement_id") or uuid4().hex)
        description = str(improvement.get("description") or "").strip()
        rationale = str(improvement.get("rationale") or description).strip()
        applied_at = improvement.get("applied_at")

        if status == "implemented" and applied_at is None:
            applied_at = "CURRENT_TIMESTAMP"

        conn.execute(
            """
            INSERT INTO improvements (
                improvement_id,
                run_id,
                skill_name,
                description,
                diff,
                source_session_ids,
                applied_at,
                improvement_type,
                target_name,
                status,
                rationale,
                evidence_session_ids,
                web_references
            )
            VALUES (
                %s, %s, %s, %s, %s, %s,
                CASE WHEN %s = 'CURRENT_TIMESTAMP' THEN CURRENT_TIMESTAMP ELSE %s END,
                %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (improvement_id) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                skill_name = EXCLUDED.skill_name,
                description = EXCLUDED.description,
                diff = EXCLUDED.diff,
                source_session_ids = EXCLUDED.source_session_ids,
                applied_at = EXCLUDED.applied_at,
                improvement_type = EXCLUDED.improvement_type,
                target_name = EXCLUDED.target_name,
                status = EXCLUDED.status,
                rationale = EXCLUDED.rationale,
                evidence_session_ids = EXCLUDED.evidence_session_ids,
                web_references = EXCLUDED.web_references
            """,
            (
                improvement_id,
                run_id,
                str(improvement.get("skill_name") or target_name),
                description,
                improvement.get("diff"),
                ",".join(evidence_session_ids),
                applied_at,
                applied_at,
                improvement_type,
                target_name,
                status,
                rationale,
                Jsonb(evidence_session_ids),
                Jsonb(web_references),
            ),
        )
        inserted_ids.append(improvement_id)

    return inserted_ids
