"""Database helpers for the shared PostgreSQL conversation index."""

from __future__ import annotations

import os
from typing import Any

import psycopg

CONVERSATION_COLUMNS = (
    "session_id",
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
)


def resolve_database_url(database_url: str | None = None) -> str:
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "PostgreSQL DATABASE_URL is required. "
            "Use `just database-url` or set DATABASE_URL explicitly."
        )
    if not url.startswith(("postgres://", "postgresql://")):
        raise ValueError(
            "Only PostgreSQL is supported. DATABASE_URL must start with "
            "`postgres://` or `postgresql://`."
        )
    return url


def connect(
    database_url: str | None = None,
) -> Any:
    return psycopg.connect(resolve_database_url(database_url=database_url))


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


def init_db(
    database_url: str | None = None,
) -> Any:
    conn = connect(database_url=database_url)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            session_id TEXT PRIMARY KEY,
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
            harvested_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS summaries (
            session_id TEXT PRIMARY KEY REFERENCES conversations(session_id),
            summary TEXT,
            goal TEXT,
            outcome TEXT,
            issues TEXT,
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
            skills_affected TEXT
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
            applied_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    _ensure_column(conn, "conversations", "source_machine", "TEXT")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_project ON conversations(project)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_started ON conversations(started_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_friction ON conversations(friction_score)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_parent ON conversations(parent_session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_skill ON conversations(detected_skill)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_conv_source_machine ON conversations(source_machine)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_improvements_skill ON improvements(skill_name)")

    conn.commit()
    return conn


def upsert_conversation(conn: Any, data: dict) -> None:
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
