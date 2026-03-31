"""SQLite schema and query helpers for the conversation index."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "conversations.db"


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = connect(db_path)

    conn.execute("""
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
            harvested_at TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS summaries (
            session_id TEXT PRIMARY KEY REFERENCES conversations(session_id),
            summary TEXT,
            goal TEXT,
            outcome TEXT,
            issues TEXT,
            generated_at TEXT DEFAULT (datetime('now')),
            model_used TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS analysis_runs (
            run_id TEXT PRIMARY KEY,
            ran_at TEXT DEFAULT (datetime('now')),
            analyzed_from TEXT,
            analyzed_to TEXT,
            conversation_count INTEGER,
            findings TEXT,
            skills_affected TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS improvements (
            improvement_id TEXT PRIMARY KEY,
            run_id TEXT REFERENCES analysis_runs(run_id),
            skill_name TEXT NOT NULL,
            description TEXT,
            diff TEXT,
            source_session_ids TEXT,
            applied_at TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_project ON conversations(project)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_started ON conversations(started_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_friction ON conversations(friction_score)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_parent ON conversations(parent_session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_skill ON conversations(detected_skill)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_improvements_skill ON improvements(skill_name)")

    conn.commit()
    return conn


def upsert_conversation(conn: sqlite3.Connection, data: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO conversations (
            session_id, project, cwd, model, started_at, ended_at,
            duration_minutes, user_message_count, assistant_message_count,
            tool_call_count, tool_breakdown, input_tokens, output_tokens,
            friction_score, efficiency_score, complexity_score,
            detected_skill, first_user_message, file_path, file_size_bytes,
            is_subagent, parent_session_id
        ) VALUES (
            :session_id, :project, :cwd, :model, :started_at, :ended_at,
            :duration_minutes, :user_message_count, :assistant_message_count,
            :tool_call_count, :tool_breakdown, :input_tokens, :output_tokens,
            :friction_score, :efficiency_score, :complexity_score,
            :detected_skill, :first_user_message, :file_path, :file_size_bytes,
            :is_subagent, :parent_session_id
        )
        """,
        data,
    )
