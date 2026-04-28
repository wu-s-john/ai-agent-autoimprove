#!/usr/bin/env python3
"""Drop the current schema and recreate it from scratch."""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit, urlunsplit

from psycopg import sql

from db import connect, init_db, resolve_database_url
from logging_utils import setup_logging

LOGGER = logging.getLogger(__name__)

@dataclass(slots=True)
class ParsedDbUrl:
    url: str
    database: str
    username: str
    query: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reset the autoimprove PostgreSQL schema."
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="App PostgreSQL database URL. Defaults to DATABASE_URL or PG* env vars.",
    )
    parser.add_argument(
        "--admin-database-url",
        default=None,
        help="Admin PostgreSQL database URL. Defaults to POSTGRES_ADMIN_* env vars.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Log level (DEBUG, INFO, WARNING, ERROR). Defaults to AUTOIMPROVE_LOG_LEVEL or INFO.",
    )
    return parser.parse_args()


def parse_database_url(url: str) -> ParsedDbUrl:
    split = urlsplit(url)
    database = unquote(split.path.lstrip("/"))
    username = unquote(split.username or "")
    return ParsedDbUrl(
        url=url,
        database=database,
        username=username,
        query=split.query,
    )


def retarget_admin_url(admin_url: str, app_database: str, app_query: str) -> str:
    split = urlsplit(admin_url)
    query = app_query or split.query
    return urlunsplit((split.scheme, split.netloc, f"/{app_database}", query, split.fragment))


def main() -> int:
    args = parse_args()
    setup_logging(args.log_level)

    app_url = resolve_database_url(database_url=args.database_url)
    admin_url = resolve_database_url(
        database_url=args.admin_database_url,
        env_prefix="POSTGRES_ADMIN",
    )
    parsed_app_url = parse_database_url(app_url)
    admin_app_url = retarget_admin_url(
        admin_url,
        app_database=parsed_app_url.database,
        app_query=parsed_app_url.query,
    )

    admin_conn = connect(database_url=admin_app_url)
    LOGGER.info("Dropping and recreating public schema in %s", parsed_app_url.database)
    admin_conn.execute("DROP SCHEMA public CASCADE")
    admin_conn.execute("CREATE SCHEMA public")
    admin_conn.execute(
        sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(
            sql.Identifier(parsed_app_url.username)
        )
    )
    admin_conn.execute(
        sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {}").format(
            sql.Identifier(parsed_app_url.database),
            sql.Identifier(parsed_app_url.username),
        )
    )
    admin_conn.commit()
    admin_conn.close()

    LOGGER.info("Reinitializing schema with app user %s", parsed_app_url.username)
    app_conn = init_db(database_url=app_url)
    app_conn.close()
    LOGGER.info("Database reset.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
