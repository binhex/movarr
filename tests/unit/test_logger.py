"""Unit tests for movarr.logger — create_logger configuration.

The actual logger.py aliases the loguru singleton as ``_logger``, uses a
lambda as the console sink, and has backtrace/diagnose set to False.  All
assertions here reflect the real implementation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture

from movarr.logger import create_logger


class TestCreateLoggerWithoutFile:
    """create_logger() behaviour when no log_path is provided."""

    def test_remove_called_once(self, mocker: MockerFixture) -> None:
        """Existing handlers should be removed exactly once."""
        mock_remove = mocker.patch("movarr.logger._logger.remove")
        mocker.patch("movarr.logger._logger.add")

        create_logger(log_format="{message}", log_level="INFO")

        mock_remove.assert_called_once()

    def test_add_called_once_for_console_only(self, mocker: MockerFixture) -> None:
        """Without log_path, _logger.add() should be called exactly once."""
        mocker.patch("movarr.logger._logger.remove")
        mock_add = mocker.patch("movarr.logger._logger.add")

        create_logger(log_format="{message}", log_level="DEBUG")

        assert mock_add.call_count == 1

    def test_console_sink_is_callable(self, mocker: MockerFixture) -> None:
        """Console sink should be a callable (lambda)."""
        mocker.patch("movarr.logger._logger.remove")
        mock_add = mocker.patch("movarr.logger._logger.add")

        create_logger(log_format="{message}", log_level="INFO")

        assert callable(mock_add.call_args.kwargs["sink"])

    def test_colorize_is_true(self, mocker: MockerFixture) -> None:
        """Console handler should have colorize=True."""
        mocker.patch("movarr.logger._logger.remove")
        mock_add = mocker.patch("movarr.logger._logger.add")

        create_logger(log_format="{message}", log_level="INFO")

        assert mock_add.call_args.kwargs.get("colorize") is True

    def test_backtrace_is_false(self, mocker: MockerFixture) -> None:
        """Console handler should have backtrace=False."""
        mocker.patch("movarr.logger._logger.remove")
        mock_add = mocker.patch("movarr.logger._logger.add")

        create_logger(log_format="{message}", log_level="INFO")

        assert mock_add.call_args.kwargs.get("backtrace") is False

    def test_diagnose_is_false(self, mocker: MockerFixture) -> None:
        """Console handler should have diagnose=False."""
        mocker.patch("movarr.logger._logger.remove")
        mock_add = mocker.patch("movarr.logger._logger.add")

        create_logger(log_format="{message}", log_level="INFO")

        assert mock_add.call_args.kwargs.get("diagnose") is False

    def test_log_level_uppercased(self, mocker: MockerFixture) -> None:
        """The log level should be uppercased before being forwarded."""
        mocker.patch("movarr.logger._logger.remove")
        mock_add = mocker.patch("movarr.logger._logger.add")

        create_logger(log_format="{message}", log_level="error")

        assert mock_add.call_args.kwargs.get("level") == "ERROR"

    def test_log_format_forwarded(self, mocker: MockerFixture) -> None:
        """The log format should be passed unchanged to the handler."""
        mocker.patch("movarr.logger._logger.remove")
        mock_add = mocker.patch("movarr.logger._logger.add")

        fmt = "<level>{message}</level>"
        create_logger(log_format=fmt, log_level="INFO")

        assert mock_add.call_args.kwargs.get("format") == fmt

    def test_returns_logger(self, mocker: MockerFixture) -> None:
        """create_logger should return the configured logger instance."""
        mocker.patch("movarr.logger._logger.remove")
        mocker.patch("movarr.logger._logger.add")

        result = create_logger(log_format="{message}", log_level="INFO")

        assert result is not None


class TestCreateLoggerWithFile:
    """create_logger() behaviour when log_path is provided."""

    def test_add_called_twice(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """With log_path, _logger.add() should be called for console and file."""
        mocker.patch("movarr.logger._logger.remove")
        mock_add = mocker.patch("movarr.logger._logger.add")

        create_logger(log_format="{message}", log_level="INFO", log_path=str(tmp_path / "app.log"))

        assert mock_add.call_count == 2

    def test_file_sink_is_log_path(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """The file handler sink kwarg should equal the provided log_path."""
        mocker.patch("movarr.logger._logger.remove")
        mock_add = mocker.patch("movarr.logger._logger.add")

        log_file = str(tmp_path / "app.log")
        create_logger(log_format="{message}", log_level="INFO", log_path=log_file)

        file_kwargs = mock_add.call_args_list[1].kwargs
        assert file_kwargs["sink"] == log_file

    def test_file_handler_rotation(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """File handler should be configured with '10 MB' rotation."""
        mocker.patch("movarr.logger._logger.remove")
        mock_add = mocker.patch("movarr.logger._logger.add")

        create_logger(log_format="{message}", log_level="WARNING", log_path=str(tmp_path / "app.log"))

        file_kwargs = mock_add.call_args_list[1].kwargs
        assert file_kwargs.get("rotation") == "10 MB"

    def test_file_handler_retention_is_3(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """File handler should have retention=3."""
        mocker.patch("movarr.logger._logger.remove")
        mock_add = mocker.patch("movarr.logger._logger.add")

        create_logger(log_format="{message}", log_level="WARNING", log_path=str(tmp_path / "app.log"))

        file_kwargs = mock_add.call_args_list[1].kwargs
        assert file_kwargs.get("retention") == 3

    def test_file_handler_encoding_is_utf8(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """File handler should use UTF-8 encoding."""
        mocker.patch("movarr.logger._logger.remove")
        mock_add = mocker.patch("movarr.logger._logger.add")

        create_logger(log_format="{message}", log_level="INFO", log_path=str(tmp_path / "app.log"))

        file_kwargs = mock_add.call_args_list[1].kwargs
        assert file_kwargs.get("encoding") == "utf-8"

    def test_file_handler_level_uppercased(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """log_level_file should be uppercased and passed to the file handler."""
        mocker.patch("movarr.logger._logger.remove")
        mock_add = mocker.patch("movarr.logger._logger.add")

        create_logger(
            log_format="{message}", log_level="debug", log_level_file="debug", log_path=str(tmp_path / "app.log")
        )

        file_kwargs = mock_add.call_args_list[1].kwargs
        assert file_kwargs.get("level") == "DEBUG"

    def test_parent_directory_created(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """os.makedirs should be called with the log file's parent directory."""
        mocker.patch("movarr.logger._logger.remove")
        mocker.patch("movarr.logger._logger.add")
        mock_makedirs = mocker.patch("movarr.logger.os.makedirs")

        log_file = str(tmp_path / "subdir" / "app.log")
        create_logger(log_format="{message}", log_level="INFO", log_path=log_file)

        mock_makedirs.assert_called_once_with(str(tmp_path / "subdir"), exist_ok=True)

    def test_none_log_path_skips_file_handler(self, mocker: MockerFixture) -> None:
        """Passing log_path=None must not add a file handler."""
        mocker.patch("movarr.logger._logger.remove")
        mock_add = mocker.patch("movarr.logger._logger.add")

        create_logger(log_format="{message}", log_level="INFO", log_path=None)

        assert mock_add.call_count == 1


class TestCreateLoggerFileSinkLevel:
    """File sink should use log_level_file, not log_level."""

    def test_file_sink_uses_separate_log_level(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """File sink level must come from log_level_file, not log_level."""
        mocker.patch("movarr.logger._logger.remove")
        mock_add = mocker.patch("movarr.logger._logger.add")

        log_file = str(tmp_path / "app.log")
        create_logger(
            log_format="{message}",
            log_level="WARNING",
            log_level_file="DEBUG",
            log_path=log_file,
        )

        file_kwargs = mock_add.call_args_list[1].kwargs
        assert file_kwargs.get("level") == "DEBUG"
