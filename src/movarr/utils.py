"""Miscellaneous project utilities for movarr."""

from pathlib import Path


def get_project_root() -> Path:
    """Return the root directory of the movarr project.

    Resolves to the parent of the ``src/`` package directory, which is the
    repository root when installed in editable mode.
    """
    return Path(__file__).parent.parent.parent
