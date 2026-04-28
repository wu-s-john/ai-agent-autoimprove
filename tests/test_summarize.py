from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import summarize
from summarize import SessionSummaryPayload


class _FakeResponse:
    def __init__(self, payload: SessionSummaryPayload) -> None:
        self.output_parsed = payload


class _FakeResponses:
    def __init__(self, payload: SessionSummaryPayload) -> None:
        self._payload = payload

    def parse(self, **_: object) -> _FakeResponse:
        return _FakeResponse(self._payload)


class _FakeClient:
    def __init__(self, payload: SessionSummaryPayload) -> None:
        self.responses = _FakeResponses(payload)


class _FakeConn:
    def commit(self) -> None:
        return None

    def close(self) -> None:
        return None


class SummarizeTests(unittest.TestCase):
    def test_load_transcript_uses_legacy_claude_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript_path = Path(temp_dir) / "legacy-session.md"
            transcript_path.write_text("# legacy", encoding="utf-8")
            with mock.patch.object(summarize, "TRANSCRIPTS_DIR", Path(temp_dir)):
                path, text = summarize.load_transcript(
                    source_app="claude",
                    native_session_id="legacy-session",
                    parent_session_id=None,
                )
            self.assertEqual(path, transcript_path)
            self.assertEqual(text, "# legacy")

    @mock.patch("summarize.upsert_summary")
    @mock.patch("summarize.connect")
    @mock.patch("summarize.list_summary_candidates")
    def test_summarize_writes_structured_summary(
        self,
        list_candidates_mock: mock.Mock,
        connect_mock: mock.Mock,
        upsert_summary_mock: mock.Mock,
    ) -> None:
        payload = SessionSummaryPayload(
            summary="The AI updated the schema and cleaned up the command surface.",
            goal="Create Postgres-backed refresh commands.",
            outcome="The harvester and just recipes were updated.",
            struggles="The AI initially mixed old schema assumptions into the fresh-start design.",
            user_corrections="The user asked to start from scratch instead of migrating old data.",
            resolution_status="resolved",
            tags=["Postgres", "Refresh", "Codex"],
        )
        connect_mock.return_value = _FakeConn()
        list_candidates_mock.return_value = [
            {
                "session_id": "claude:test-session",
                "source_app": "claude",
                "native_session_id": "test-session",
                "parent_session_id": None,
                "project": "demo-repo",
                "cwd": "/Users/johnwu/code/demo-repo",
                "model": "claude-3-7-sonnet",
                "started_at": "2026-04-01T12:00:00+00:00",
                "ended_at": "2026-04-01T12:01:00+00:00",
                "duration_minutes": 1.0,
                "detected_skill": "rust-reviewer",
                "first_user_message": "Use the rust-reviewer skill to inspect the parser bug",
                "friction_score": 2.0,
                "efficiency_score": 8.0,
                "complexity_score": 3.0,
                "is_subagent": 0,
                "summarized_session_id": None,
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            transcript_path = Path(temp_dir) / "claude" / "test-session.md"
            transcript_path.parent.mkdir(parents=True, exist_ok=True)
            transcript_path.write_text("# transcript", encoding="utf-8")
            with (
                mock.patch.object(summarize, "TRANSCRIPTS_DIR", Path(temp_dir)),
                mock.patch("summarize.create_openai_client", return_value=_FakeClient(payload)),
            ):
                result = summarize.summarize(model="gpt-5.4")

        self.assertEqual(result.created_count, 1)
        upsert_summary_mock.assert_called_once()
        self.assertEqual(
            upsert_summary_mock.call_args.kwargs["tags"],
            ["postgres", "refresh", "codex"],
        )
