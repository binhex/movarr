"""Miscellaneous project utilities for movarr."""

from pathlib import Path


def get_project_root() -> Path:
    """Return the root directory of the movarr project.

    Resolves to the parent of the ``src/`` package directory, which is the
    repository root when installed in editable mode.
    """
    return Path(__file__).parent.parent.parent


def bytes_to_mb(size_bytes: object) -> str:
    """Convert a raw byte count to a decimal megabyte string.

    Truncates fractional MB.  Returns ``"0"`` on invalid input.
    """
    try:
        return str(int(float(str(size_bytes))) // 1_000_000)
    except (ValueError, TypeError):
        return "0"
