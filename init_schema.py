#!/usr/bin/env python3
"""Initialize the application database schema."""

from __future__ import annotations

import argparse

from db import init_db


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or update the autoimprove database schema."
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="PostgreSQL database URL to initialize. Defaults to DATABASE_URL if set.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    conn = init_db(database_url=args.database_url)
    conn.close()
    print("Schema initialized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
