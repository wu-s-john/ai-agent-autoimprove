#!/usr/bin/env python3
"""Run harvest first, then summarize the harvested sessions."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from harvest import harvest
from logging_utils import setup_logging
from summarize import summarize

LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh the conversation index by harvesting and summarizing."
    )
    parser.add_argument(
        "--source",
        choices=("all", "claude", "codex"),
        default="all",
        help="Which source app to refresh (default: all)",
    )
    parser.add_argument("--query", default=None, help="Metadata query filter for summarization")
    parser.add_argument("--since", default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--until", default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=None, help="Maximum sessions to summarize")
    parser.add_argument(
        "--include-subagents",
        action="store_true",
        help="Include subagent sessions in summarization",
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
        "--claude-source",
        action="append",
        default=[],
        help="Claude source directory (default: ~/.claude/projects)",
    )
    parser.add_argument(
        "--codex-source",
        action="append",
        default=[],
        help="Codex source directory. Defaults to ~/.codex/sessions and ~/.codex/archived_sessions.",
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


def main() -> int:
    args = parse_args()
    log_level = getattr(args, "log_level", None)
    setup_logging(log_level)
    LOGGER.info("Starting refresh")
    harvest_result = harvest(
        source=args.source,
        since=args.since,
        until=args.until,
        claude_sources=[Path(path).expanduser() for path in args.claude_source],
        codex_sources=[Path(path).expanduser() for path in args.codex_source],
        database_url=args.database_url,
        log_level=log_level,
    )
    LOGGER.info(
        "Harvest phase complete with %s sessions",
        harvest_result.harvested_count,
    )
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
        session_ids=harvest_result.harvested_session_ids,
        log_level=log_level,
    )
    LOGGER.info("Refresh complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
