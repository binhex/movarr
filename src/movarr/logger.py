"""Logging utilities for movarr."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from loguru import logger as _logger

from movarr.config import LOG_FILENAME


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
        log_path: Optional directory path for the log file, or a full file
            path.  When it is an existing directory or has no file extension,
            ``movarr.log`` is created inside it; otherwise it is used as the
            log file itself.  Parent directories are created automatically if
            they do not exist.
    """
    _logger.remove()

    # Console sink
    _logger.add(
        sink=lambda message: print(message, end=""),
        level=log_level.upper(),
        format=log_format,
        # Avoid ANSI escapes in Docker logs / journald / piped output.
        colorize=sys.stdout.isatty(),
        backtrace=False,
        diagnose=False,
    )

    # File sink
    if log_path:
        # A directory whose name contains a dot (e.g. /var/log/movarr-1.3) is still
        # a directory, so consult the filesystem before the extension heuristic.
        if os.path.isdir(log_path) or not os.path.splitext(log_path)[1]:
            log_file = os.path.join(log_path, LOG_FILENAME)
        else:
            log_file = log_path
        log_dir = os.path.dirname(log_file)
        if log_dir:
            try:
                os.makedirs(log_dir, exist_ok=True)
            except OSError as exc:
                # Console logging still works; a badge/inspection host may mount
                # the log directory read-only.
                _logger.warning("Cannot create log directory '{}': {}; file logging disabled.", log_dir, exc)
                return _logger
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
