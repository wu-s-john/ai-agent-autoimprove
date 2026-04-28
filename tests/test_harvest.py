from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harvest import parse_claude_conversation, parse_codex_conversation, register_file_touch
from session_utils import write_transcript

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class HarvestParserTests(unittest.TestCase):
    def test_parse_claude_fixture(self) -> None:
        data = parse_claude_conversation(FIXTURES_DIR / "claude_session.jsonl")
        self.assertIsNotNone(data)
        assert data is not None
        self.assertEqual(data["session_id"], "claude:claude_session")
        self.assertEqual(data["source_app"], "claude")
        self.assertEqual(data["project"], "demo-repo")
        self.assertEqual(data["detected_skill"], "rust-reviewer")
        self.assertEqual(data["tool_call_count"], 1)

    def test_parse_codex_fixture_and_write_transcript(self) -> None:
        data = parse_codex_conversation(FIXTURES_DIR / "codex_session.jsonl")
        self.assertIsNotNone(data)
        assert data is not None
        self.assertEqual(data["session_id"], "codex:019d-test-child")
        self.assertEqual(data["parent_session_id"], "codex:019d-parent")
        self.assertEqual(data["agent_name"], "Hilbert")
        self.assertEqual(data["agent_role"], "explorer")
        self.assertEqual(data["model"], "gpt-5.4")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = write_transcript(data, Path(temp_dir))
            self.assertEqual(
                path,
                Path(temp_dir) / "codex" / "019d-parent" / "019d-test-child.md",
            )
            self.assertTrue(path.exists())

    def test_parse_codex_session_meta_with_string_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = Path(temp_dir) / "rollout.jsonl"
            fixture.write_text(
                "\n".join(
                    [
                        '{"timestamp":"2026-04-21T13:18:38Z","type":"session_meta","payload":{"id":"019db031-5c6d-7400-a3fd-2f6d95ac258a","cwd":"/Users/johnwu/code/sample-app","source":"vscode","forked_from_id":"019daff9-e4ff-7d73-b15e-133b93b2a639","agent_role":"worker","agent_nickname":"Noether"}}',
                        '{"timestamp":"2026-04-21T13:18:39Z","type":"event_msg","payload":{"type":"user_message","message":"Please review the schema"}}',
                    ]
                ),
                encoding="utf-8",
            )
            data = parse_codex_conversation(fixture)

        self.assertIsNotNone(data)
        assert data is not None
        self.assertEqual(data["session_id"], "codex:019db031-5c6d-7400-a3fd-2f6d95ac258a")
        self.assertEqual(data["parent_session_id"], "codex:019daff9-e4ff-7d73-b15e-133b93b2a639")
        self.assertEqual(data["agent_name"], "Noether")
        self.assertEqual(data["agent_role"], "worker")

    def test_register_file_touch_ignores_non_dict_inputs(self) -> None:
        edit_counts: dict[str, int] = {}
        touched: set[str] = set()
        register_file_touch("exec_command", "plain string input", edit_counts, touched)
        self.assertEqual(edit_counts, {})
        self.assertEqual(touched, set())
