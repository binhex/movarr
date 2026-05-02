"""Unit tests for movarr.utils."""

from __future__ import annotations

from pathlib import Path

from movarr.utils import get_project_root


class TestGetProjectRoot:
    """Tests for get_project_root()."""

    def test_returns_path_instance(self) -> None:
        """get_project_root() must return a Path object."""
        assert isinstance(get_project_root(), Path)

    def test_path_exists(self) -> None:
        """Returned path must exist on the filesystem."""
        assert get_project_root().exists()

    def test_path_is_directory(self) -> None:
        """Returned path must be a directory."""
        assert get_project_root().is_dir()

    def test_is_absolute(self) -> None:
        """Returned path must be absolute."""
        assert get_project_root().is_absolute()

    def test_contains_pyproject_toml(self) -> None:
        """Repo root must contain pyproject.toml."""
        assert (get_project_root() / "pyproject.toml").exists()

    def test_contains_src_directory(self) -> None:
        """Repo root must contain a src/ directory."""
        assert (get_project_root() / "src").is_dir()

    def test_consistent_on_repeated_calls(self) -> None:
        """Repeated calls must return the same path."""
        assert get_project_root() == get_project_root()
