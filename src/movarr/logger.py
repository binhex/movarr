"""Logging utilities for movarr."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from loguru import logger as _logger


def create_logger(
    log_format: str | Callable,
    log_level: str = "INFO",
    log_level_file: str = "INFO",
    log_path: str | None = None,
) -> Any:
    """Return a configured Loguru logger instance.

    Args:
        log_format: Loguru format string for console output.
        log_level: Minimum log level for the console sink.
        log_level_file: Minimum log level for the file sink.
        log_path: Optional directory path for the log file.  The file
            ``movarr.log`` is created inside this directory.  Parent
            directories are created automatically if they do not exist.
    """
    _logger.remove()

    # Console sink
    _logger.add(
        sink=lambda message: print(message, end=""),
        level=log_level.upper(),
        format=log_format,
        colorize=True,
        backtrace=False,
        diagnose=False,
    )

    # File sink
    if log_path:
        # If log_path has an extension, treat as a full file path (backward compat).
        # Otherwise, treat as a directory and create movarr.log inside.
        log_file = log_path if os.path.splitext(log_path)[1] else os.path.join(log_path, "movarr.log")
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        _logger.add(
            sink=log_file,
            level=log_level_file.upper(),
            format=log_format,
            rotation="10 MB",
            retention=3,
            encoding="utf-8",
            backtrace=False,
            diagnose=False,
        )

    return _logger
