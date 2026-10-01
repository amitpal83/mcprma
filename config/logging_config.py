"""Shared logging configuration for the RMA project.

Call configure_logging() once, near process start (CLI entry points, API
startup, MCP server startup). Library modules should only call
logging.getLogger(__name__) and never configure handlers themselves.
"""
from __future__ import annotations

import logging
import logging.handlers

from config.settings import LOG_FILE


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if root.handlers:
        return

    root.setLevel(level)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    root.addHandler(console_handler)
    root.addHandler(file_handler)
