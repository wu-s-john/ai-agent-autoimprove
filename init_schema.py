#!/usr/bin/env python3
"""Initialize the application database schema."""

from __future__ import annotations

import argparse
import logging

from db import init_db
from logging_utils import setup_logging

LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or update the autoimprove database schema."
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="PostgreSQL database URL to initialize. Defaults to DATABASE_URL if set.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Log level (DEBUG, INFO, WARNING, ERROR). Defaults to AUTOIMPROVE_LOG_LEVEL or INFO.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(args.log_level)
    conn = init_db(database_url=args.database_url)
    conn.close()
    LOGGER.info("Schema initialized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
