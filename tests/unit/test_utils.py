"""Unit tests for movarr.utils."""

from __future__ import annotations

from pathlib import Path

from movarr.utils import bytes_to_mb, get_project_root


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


class TestBytesToMb:
    """Tests for bytes_to_mb shared helper."""

    def test_converts_bytes_to_mb(self) -> None:
        assert bytes_to_mb(8_589_934_592) == "8589"

    def test_truncates_remainder(self) -> None:
        assert bytes_to_mb(1_000_001) == "1"

    def test_zero_returns_zero(self) -> None:
        assert bytes_to_mb(0) == "0"

    def test_float_input_works(self) -> None:
        assert bytes_to_mb(1_000_000.0) == "1"
        assert bytes_to_mb(8_589_934_592.0) == "8589"

    def test_invalid_string_returns_zero(self) -> None:
        assert bytes_to_mb("not-a-number") == "0"

    def test_empty_string_returns_zero(self) -> None:
        assert bytes_to_mb("") == "0"
