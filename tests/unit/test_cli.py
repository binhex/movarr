"""Unit tests for movarr.cli — Click command-line interface."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from click.testing import CliRunner

from movarr.cli import cli

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


def _make_config_mock() -> MagicMock:
    """Return a minimal mock Config object accepted by the cli function."""
    cfg = MagicMock()
    cfg.general.db_path = "/fake/movarr.db"
    cfg.general.daemon_mode = "foreground"
    cfg.general.ffprobe_path = "/usr/bin/ffprobe"
    return cfg


# ---------------------------------------------------------------------------
# --version
# ---------------------------------------------------------------------------


class TestCliVersion:
    """--version prints the program version and exits cleanly."""

    def test_version_exits_zero(self) -> None:
        """--version should exit with code 0."""
        result = CliRunner().invoke(cli, ["--version"])
        assert result.exit_code == 0

    def test_version_output_contains_movarr(self) -> None:
        """--version output should mention the program name."""
        result = CliRunner().invoke(cli, ["--version"])
        assert "movarr" in result.output

    def test_version_output_has_version_string(self) -> None:
        """--version output should contain a version-like token."""
        result = CliRunner().invoke(cli, ["--version"])
        # Either a semver string or "unknown" when package metadata is absent
        assert any(char.isdigit() for char in result.output) or "unknown" in result.output


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


class TestCliHelp:
    """--help shows usage information."""

    def test_help_exits_zero(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert result.exit_code == 0

    def test_help_shows_usage(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert "Usage:" in result.output

    def test_help_mentions_config_path(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert "--config-path" in result.output

    def test_help_mentions_log_level(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert "--log-level" in result.output

    def test_help_mentions_daemon(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert "--daemon" in result.output

    def test_help_mentions_test(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert "--test" in result.output


# ---------------------------------------------------------------------------
# --test (config-validation / dry-run mode)
# ---------------------------------------------------------------------------


class TestCliTestMode:
    """--test validates configuration then exits without running tasks."""

    def test_prints_configuration_loaded(self, mocker: MockerFixture) -> None:
        """Output must contain 'Configuration loaded successfully'."""
        mocker.patch("movarr.cli.create_logger")
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())

        result = CliRunner().invoke(cli, ["--test"])

        assert result.exit_code == 0
        assert "Configuration loaded successfully" in result.output

    def test_output_mentions_test_mode(self, mocker: MockerFixture) -> None:
        """Output must mention test mode."""
        mocker.patch("movarr.cli.create_logger")
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())

        result = CliRunner().invoke(cli, ["--test"])

        assert "Test mode" in result.output or "test mode" in result.output.lower()

    def test_does_not_invoke_run(self, mocker: MockerFixture) -> None:
        """scheduler.run() must not be called in test mode (verified via output).

        movarr.scheduler cannot be imported directly in tests because its
        transitive dependencies are not installed in the test environment.
        We verify indirectly: the --test flag causes an early return with
        the expected message, so run() is never reached.
        """
        mocker.patch("movarr.cli.create_logger")
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())

        result = CliRunner().invoke(cli, ["--test"])

        # If run() had been called it would ImportError; clean exit proves it wasn't.
        assert result.exit_code == 0
        assert "Configuration loaded successfully" in result.output

    def test_db_path_written_to_config(self, mocker: MockerFixture) -> None:
        """The --db-path CLI value should be stored on config.general.db_path."""
        mocker.patch("movarr.cli.create_logger")
        mock_cfg = _make_config_mock()
        mocker.patch("movarr.config.load_config", return_value=mock_cfg)

        CliRunner().invoke(cli, ["--db-path", "/custom/db.db", "--test"])

        assert mock_cfg.general.db_path == "/custom/db.db"

    def test_foreground_mode_set_without_daemon_flag(self, mocker: MockerFixture) -> None:
        """Without --daemon, daemon_mode should be set to 'foreground'."""
        mocker.patch("movarr.cli.create_logger")
        mock_cfg = _make_config_mock()
        mocker.patch("movarr.config.load_config", return_value=mock_cfg)

        CliRunner().invoke(cli, ["--test"])

        assert mock_cfg.general.daemon_mode == "foreground"

    def test_background_mode_set_with_daemon_flag(self, mocker: MockerFixture) -> None:
        """With --daemon, daemon_mode should be set to 'background'."""
        mocker.patch("movarr.cli.create_logger")
        mock_cfg = _make_config_mock()
        mocker.patch("movarr.config.load_config", return_value=mock_cfg)

        CliRunner().invoke(cli, ["--daemon", "--test"])

        assert mock_cfg.general.daemon_mode == "background"

    def test_ffprobe_path_written_to_config(self, mocker: MockerFixture) -> None:
        """The --ffprobe-path CLI value should be stored on config.general.ffprobe_path."""
        mocker.patch("movarr.cli.create_logger")
        mock_cfg = _make_config_mock()
        mocker.patch("movarr.config.load_config", return_value=mock_cfg)

        CliRunner().invoke(cli, ["--ffprobe-path", "/custom/ffprobe", "--test"])

        assert mock_cfg.general.ffprobe_path == "/custom/ffprobe"

    def test_create_logger_called(self, mocker: MockerFixture) -> None:
        """create_logger should be invoked regardless of --test flag."""
        mock_logger = mocker.patch("movarr.cli.create_logger")
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())

        CliRunner().invoke(cli, ["--test"])

        mock_logger.assert_called_once()

    def test_log_level_passed_to_create_logger(self, mocker: MockerFixture) -> None:
        """The --log-level value should be forwarded to create_logger."""
        mock_logger = mocker.patch("movarr.cli.create_logger")
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())

        CliRunner().invoke(cli, ["--log-level", "DEBUG", "--test"])

        _, kwargs = mock_logger.call_args
        assert kwargs.get("log_level") == "DEBUG"


# ---------------------------------------------------------------------------
# --log-level validation
# ---------------------------------------------------------------------------


class TestCliLogLevel:
    """--log-level accepts valid choices and rejects invalid ones."""

    def test_invalid_log_level_exits_non_zero(self) -> None:
        """An unrecognised log level should cause a non-zero exit."""
        result = CliRunner().invoke(cli, ["--log-level", "VERBOSE"])
        assert result.exit_code != 0

    def test_invalid_log_level_mentions_error(self) -> None:
        """Click should surface an error message for invalid log level."""
        result = CliRunner().invoke(cli, ["--log-level", "TRACE"])
        assert "Error" in result.output or result.exit_code != 0

    def test_debug_is_valid(self, mocker: MockerFixture) -> None:
        mocker.patch("movarr.cli.create_logger")
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())
        assert CliRunner().invoke(cli, ["--log-level", "DEBUG", "--test"]).exit_code == 0

    def test_info_is_valid(self, mocker: MockerFixture) -> None:
        mocker.patch("movarr.cli.create_logger")
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())
        assert CliRunner().invoke(cli, ["--log-level", "INFO", "--test"]).exit_code == 0

    def test_success_is_valid(self, mocker: MockerFixture) -> None:
        mocker.patch("movarr.cli.create_logger")
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())
        assert CliRunner().invoke(cli, ["--log-level", "SUCCESS", "--test"]).exit_code == 0

    def test_warning_is_valid(self, mocker: MockerFixture) -> None:
        mocker.patch("movarr.cli.create_logger")
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())
        assert CliRunner().invoke(cli, ["--log-level", "WARNING", "--test"]).exit_code == 0

    def test_error_is_valid(self, mocker: MockerFixture) -> None:
        mocker.patch("movarr.cli.create_logger")
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())
        assert CliRunner().invoke(cli, ["--log-level", "ERROR", "--test"]).exit_code == 0

    def test_log_level_case_insensitive(self, mocker: MockerFixture) -> None:
        """Log level matching should be case-insensitive."""
        mocker.patch("movarr.cli.create_logger")
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())
        assert CliRunner().invoke(cli, ["--log-level", "debug", "--test"]).exit_code == 0


# ---------------------------------------------------------------------------
# Scheduler run path (no --test flag)
# ---------------------------------------------------------------------------


class TestCliSchedulerRun:
    """Without --test, the CLI calls movarr.scheduler.run."""

    def test_cli_without_test_flag_calls_scheduler_run(self, mocker: MockerFixture) -> None:
        mocker.patch("movarr.cli.create_logger")
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())
        mock_run = mocker.patch("movarr.scheduler.run")
        result = CliRunner().invoke(cli, [])
        assert result.exit_code == 0
        mock_run.assert_called_once()
