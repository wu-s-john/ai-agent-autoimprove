from __future__ import annotations

import argparse
import unittest
from unittest import mock

import refresh
from harvest import HarvestResult


class RefreshTests(unittest.TestCase):
    @mock.patch("refresh.summarize")
    @mock.patch("refresh.harvest")
    @mock.patch("refresh.parse_args")
    def test_refresh_runs_harvest_then_summarize(
        self,
        parse_args_mock: mock.Mock,
        harvest_mock: mock.Mock,
        summarize_mock: mock.Mock,
    ) -> None:
        parse_args_mock.return_value = argparse.Namespace(
            source="all",
            query="postgres",
            since="2026-04-01",
            until="2026-04-21",
            limit=10,
            include_subagents=False,
            force=False,
            model="gpt-5.4",
            claude_source=[],
            codex_source=[],
            database_url="postgresql://example",
        )
        harvest_mock.return_value = HarvestResult(
            harvested_count=2,
            harvested_session_ids=["claude:one", "codex:two"],
            files_seen=2,
        )

        refresh.main()

        harvest_mock.assert_called_once()
        summarize_mock.assert_called_once_with(
            source="all",
            query="postgres",
            since="2026-04-01",
            until="2026-04-21",
            limit=10,
            include_subagents=False,
            force=False,
            model="gpt-5.4",
            database_url="postgresql://example",
            session_ids=["claude:one", "codex:two"],
            log_level=None,
        )
