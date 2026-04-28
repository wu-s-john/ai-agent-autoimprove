"""Logging helpers for CLI scripts."""

from __future__ import annotations

import logging
import os

DEFAULT_LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def resolve_log_level(value: str | None) -> int:
    level_name = (value or os.environ.get("AUTOIMPROVE_LOG_LEVEL") or DEFAULT_LOG_LEVEL).upper()
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        raise ValueError(f"Invalid log level: {level_name}")
    return level


def setup_logging(level: str | None = None) -> int:
    resolved = resolve_log_level(level)
    logging.basicConfig(level=resolved, format=LOG_FORMAT, force=True)
    return resolved
