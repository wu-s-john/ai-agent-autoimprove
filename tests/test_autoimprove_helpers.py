from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autoimprove_helpers as helpers
from autoimprove_helpers import ImprovementProposal
from db import ConversationFilters


class _FakeConn:
    def __init__(self) -> None:
        self.committed = False
        self.closed = False

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


class AutoimproveHelperTests(unittest.TestCase):
    def test_load_session_evidence_reads_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript_path = Path(temp_dir) / "claude" / "session-1.md"
            transcript_path.parent.mkdir(parents=True, exist_ok=True)
            transcript_path.write_text("# transcript", encoding="utf-8")
            session = {
                "session_id": "claude:session-1",
                "source_app": "claude",
                "native_session_id": "session-1",
                "parent_session_id": None,
            }

            evidence = helpers.load_session_evidence(
                session,
                transcripts_dir=Path(temp_dir),
            )

        self.assertEqual(evidence["transcript_path"], transcript_path)
        self.assertEqual(evidence["transcript_text"], "# transcript")
        self.assertFalse(evidence["raw_jsonl_exists"])

    def test_group_recurring_patterns_groups_tags_and_struggles(self) -> None:
        rows = [
            {
                "session_id": "claude:one",
                "tags": ["postgres", "networking"],
                "detected_skill": "autoimprove",
                "resolution_status": "unresolved",
                "struggles": "RDS networking confusion",
            },
            {
                "session_id": "codex:two",
                "tags": ["postgres"],
                "detected_skill": "autoimprove",
                "resolution_status": "unresolved",
                "struggles": "RDS networking confusion",
            },
        ]

        grouped = helpers.group_recurring_patterns(rows)

        self.assertEqual(grouped["tags"][0]["label"], "postgres")
        self.assertEqual(grouped["tags"][0]["count"], 2)
        self.assertEqual(grouped["struggles"][0]["label"], "rds networking confusion")

    def test_normalize_improvement_payload_rejects_mixed(self) -> None:
        with self.assertRaises(ValueError):
            helpers.normalize_improvement_payload(
                {
                    "improvement_type": "mixed",
                    "target_name": "postgres-debugging",
                    "description": "Needs both prompt and tooling changes.",
                    "rationale": "Repeated across sessions.",
                    "evidence_session_ids": ["claude:one"],
                }
            )

    def test_normalize_improvement_payload_normalizes_web_references(self) -> None:
        normalized = helpers.normalize_improvement_payload(
            ImprovementProposal(
                improvement_type="tool",
                target_name="aws-rds-connectivity-docs",
                description="Use official AWS RDS connectivity guidance.",
                rationale="The same manual setup steps repeated across sessions.",
                evidence_session_ids=["claude:one"],
                web_references=[
                    "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_ConnectToPostgreSQLInstance.html"
                ],
            )
        )

        self.assertEqual(normalized["improvement_type"], "tool")
        self.assertEqual(normalized["web_references"][0]["domain"], "docs.aws.amazon.com")

    def test_render_analysis_report_includes_all_sections(self) -> None:
        report = helpers.render_analysis_report(
            {
                "Observed Patterns": ["Repeated RDS debugging loops."],
                "Skill Improvements": ["Add a networking checklist."],
                "CLI Opportunities": ["Build an RDS connectivity CLI."],
                "External Tool Recommendations": ["Review official AWS RDS docs."],
            }
        )

        self.assertIn("## Observed Patterns", report)
        self.assertIn("## Skill Improvements", report)
        self.assertIn("## CLI Opportunities", report)
        self.assertIn("## External Tool Recommendations", report)

    @mock.patch("autoimprove_helpers.insert_improvements")
    @mock.patch("autoimprove_helpers.insert_analysis_run", return_value="run-123")
    @mock.patch("autoimprove_helpers.connect")
    def test_persist_analysis_artifacts_writes_run_and_improvements(
        self,
        connect_mock: mock.Mock,
        insert_analysis_run_mock: mock.Mock,
        insert_improvements_mock: mock.Mock,
    ) -> None:
        fake_conn = _FakeConn()
        connect_mock.return_value = fake_conn
        cohort_rows = [
            {"session_id": "claude:one", "started_at": "2026-04-01T12:00:00+00:00"},
            {"session_id": "codex:two", "started_at": "2026-04-03T12:00:00+00:00"},
        ]

        run_id = helpers.persist_analysis_artifacts(
            report_markdown="## Observed Patterns\n\n- RDS setup loops",
            cohort_rows=cohort_rows,
            query_text="postgres",
            filters=ConversationFilters(query="postgres", limit=5),
            research_performed=True,
            model_used="gpt-5.4",
            recommendations=[
                ImprovementProposal(
                    improvement_type="skill",
                    target_name="postgres-debugging",
                    description="Add an RDS triage checklist.",
                    rationale="Repeated across sessions.",
                    evidence_session_ids=["claude:one", "codex:two"],
                )
            ],
        )

        self.assertEqual(run_id, "run-123")
        insert_analysis_run_mock.assert_called_once()
        insert_improvements_mock.assert_called_once()
        self.assertTrue(fake_conn.committed)
        self.assertTrue(fake_conn.closed)
        self.assertEqual(
            insert_improvements_mock.call_args.kwargs["improvements"][0]["target_name"],
            "postgres-debugging",
        )
