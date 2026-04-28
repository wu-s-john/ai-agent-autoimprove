from __future__ import annotations

import os
import unittest
from unittest import mock

import db
from db import (
    ConversationFilters,
    IMPROVEMENT_TYPES,
    init_db,
    insert_analysis_run,
    insert_improvements,
    list_analysis_candidates,
    resolve_database_url,
)


class _FakeCursor:
    def __init__(self, *, row=None, rows=None) -> None:
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _RecordingConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, object]] = []
        self.tables = {"conversations", "summaries", "analysis_runs", "improvements"}
        self.improvement_rows = [
            {
                "improvement_id": "old-row",
                "source_session_ids": "claude:one,codex:two",
                "evidence_session_ids": None,
            }
        ]

    def execute(self, sql: str, params=None):
        self.executed.append((sql, params))
        if "FROM information_schema.columns" in sql:
            return _FakeCursor(row=None)
        if "FROM information_schema.tables" in sql:
            table_name = params[0]
            return _FakeCursor(row=1 if table_name in self.tables else None)
        if "SELECT improvement_id, source_session_ids, evidence_session_ids" in sql:
            return _FakeCursor(rows=self.improvement_rows)
        if "FROM conversations c" in sql and "JOIN summaries s" in sql:
            return _FakeCursor(rows=[{"session_id": "claude:test"}])
        return _FakeCursor()

    def commit(self) -> None:
        return None

    def close(self) -> None:
        return None


class ResolveDatabaseUrlTests(unittest.TestCase):
    def test_builds_app_database_url_from_pg_env(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "PGHOST": "example.rds.amazonaws.com",
                "PGPORT": "5432",
                "PGDATABASE": "autoimprove",
                "PGUSER": "autoimprove_app",
                "PGPASSWORD": "secret password",
                "PGSSLMODE": "require",
            },
            clear=True,
        ):
            url = resolve_database_url()
        self.assertEqual(
            url,
            "postgresql://autoimprove_app:secret%20password@example.rds.amazonaws.com:5432/autoimprove?sslmode=require",
        )

    def test_builds_admin_database_url_from_prefixed_env(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "POSTGRES_ADMIN_HOST": "example.rds.amazonaws.com",
                "POSTGRES_ADMIN_PORT": "5432",
                "POSTGRES_ADMIN_DATABASE": "postgres",
                "POSTGRES_ADMIN_USER": "admin",
                "POSTGRES_ADMIN_PASSWORD": "topsecret",
                "POSTGRES_ADMIN_SSLMODE": "require",
            },
            clear=True,
        ):
            url = resolve_database_url(env_prefix="POSTGRES_ADMIN")
        self.assertEqual(
            url,
            "postgresql://admin:topsecret@example.rds.amazonaws.com:5432/postgres?sslmode=require",
        )


class InitDbTests(unittest.TestCase):
    def test_init_db_adds_analysis_columns_idempotently(self) -> None:
        fake_conn = _RecordingConn()
        with mock.patch("db.connect", return_value=fake_conn):
            init_db("postgresql://example")

        executed_sql = "\n".join(sql for sql, _ in fake_conn.executed)
        self.assertIn("query_text TEXT", executed_sql)
        self.assertIn("filters_json JSONB NOT NULL DEFAULT '{}'::jsonb", executed_sql)
        self.assertIn("research_performed BOOLEAN NOT NULL DEFAULT FALSE", executed_sql)
        self.assertIn("improvement_type IN ('skill', 'cli', 'tool')", executed_sql)
        self.assertIn("status IN ('proposed', 'approved', 'rejected', 'implemented')", executed_sql)
        self.assertIn("ALTER TABLE improvements ALTER COLUMN applied_at DROP DEFAULT", executed_sql)

    def test_init_db_backfills_legacy_evidence_session_ids(self) -> None:
        fake_conn = _RecordingConn()
        with mock.patch("db.connect", return_value=fake_conn):
            init_db("postgresql://example")

        update_calls = [
            params
            for sql, params in fake_conn.executed
            if "SET evidence_session_ids = %s" in sql
        ]
        self.assertEqual(len(update_calls), 1)
        jsonb_payload, improvement_id = update_calls[0]
        self.assertEqual(jsonb_payload.obj, ["claude:one", "codex:two"])
        self.assertEqual(improvement_id, "old-row")


class AnalysisHelpersInDbTests(unittest.TestCase):
    def test_list_analysis_candidates_queries_summary_fields(self) -> None:
        fake_conn = _RecordingConn()
        rows = list_analysis_candidates(
            fake_conn,
            ConversationFilters(
                source="codex",
                query="postgres",
                since="2026-04-01",
                until="2026-04-21",
                include_subagents=False,
                limit=5,
            ),
        )
        self.assertEqual(rows, [{"session_id": "claude:test"}])
        sql, params = fake_conn.executed[-1]
        self.assertIn("COALESCE(s.summary, '') ILIKE %s", sql)
        self.assertIn("COALESCE(s.struggles, '') ILIKE %s", sql)
        self.assertIn("COALESCE(s.user_corrections, '') ILIKE %s", sql)
        self.assertIn("COALESCE(s.tags::text, '[]') ILIKE %s", sql)
        self.assertEqual(params[0], "codex")
        self.assertEqual(params[-1], 5)

    def test_insert_analysis_run_stores_report_metadata(self) -> None:
        fake_conn = _RecordingConn()
        run_id = insert_analysis_run(
            fake_conn,
            analyzed_from="2026-04-01T00:00:00+00:00",
            analyzed_to="2026-04-21T00:00:00+00:00",
            conversation_count=3,
            query_text="postgres",
            filters_json={"source": "claude", "query": "postgres"},
            report_markdown="## Observed Patterns\n\n- RDS setup churn",
            research_performed=True,
            model_used="gpt-5.4",
        )

        self.assertTrue(run_id)
        sql, params = fake_conn.executed[-1]
        self.assertIn("INSERT INTO analysis_runs", sql)
        self.assertEqual(params[6], "postgres")
        self.assertEqual(params[7].obj, {"source": "claude", "query": "postgres"})
        self.assertTrue(params[9])
        self.assertEqual(params[10], "gpt-5.4")

    def test_insert_improvements_stores_typed_recommendations(self) -> None:
        fake_conn = _RecordingConn()
        inserted_ids = insert_improvements(
            fake_conn,
            run_id="run-123",
            improvements=[
                {
                    "improvement_type": "cli",
                    "target_name": "rds-connectivity-check",
                    "description": "Add a connectivity helper CLI.",
                    "rationale": "Repeated manual reachability checks wasted time.",
                    "status": "proposed",
                    "evidence_session_ids": ["claude:one", "codex:two"],
                    "web_references": [
                        {
                            "title": "AWS RDS docs",
                            "url": "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_ConnectToPostgreSQLInstance.html",
                            "domain": "docs.aws.amazon.com",
                        }
                    ],
                }
            ],
        )

        self.assertEqual(len(inserted_ids), 1)
        self.assertIn("cli", IMPROVEMENT_TYPES)
        sql, params = fake_conn.executed[-1]
        self.assertIn("INSERT INTO improvements", sql)
        self.assertEqual(params[8], "cli")
        self.assertEqual(params[9], "rds-connectivity-check")
        self.assertEqual(params[10], "proposed")
        self.assertEqual(params[12].obj, ["claude:one", "codex:two"])
        self.assertEqual(params[13].obj[0]["domain"], "docs.aws.amazon.com")
