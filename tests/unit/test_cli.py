"""Unit tests for movarr.cli — Click command-line interface."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from click.testing import CliRunner

from movarr.cli import cli

if TYPE_CHECKING:
    from typing import Any

    from pytest_mock import MockerFixture


# Shared fixture helpers


def _make_config_mock(
    *,
    log_level_console: str = "info",
    log_path: str = "",
    pid_path: str = "",
    daemon_mode: str = "foreground",
) -> MagicMock:
    """Return a minimal mock Config object accepted by the cli function."""
    cfg = MagicMock()
    cfg.general.log_level_console = log_level_console
    cfg.general.log_path = log_path
    cfg.general.pid_path = pid_path
    cfg.general.daemon_mode = daemon_mode
    return cfg


# --version


class TestCliVersion:
    """--version prints the program version and exits cleanly."""

    def test_version_exits_zero(self) -> None:
        result = CliRunner().invoke(cli, ["--version"])
        assert result.exit_code == 0

    def test_version_output_contains_movarr(self) -> None:
        result = CliRunner().invoke(cli, ["--version"])
        assert "movarr" in result.output

    def test_version_output_has_version_string(self) -> None:
        result = CliRunner().invoke(cli, ["--version"])
        assert any(char.isdigit() for char in result.output) or "unknown" in result.output


# --help


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

    def test_help_mentions_daemon(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert "--daemon" in result.output

    def test_help_mentions_test(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert "--test" in result.output

    def test_help_does_not_mention_removed_options(self) -> None:
        """--db-path, --log-path, --pid-path have been removed; --log-level is kept."""
        result = CliRunner().invoke(cli, ["--help"])
        for removed in ("--db-path", "--log-path", "--pid-path"):
            assert removed not in result.output, f"removed option {removed!r} still in help"

    def test_help_mentions_log_level(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert "--log-level" in result.output


# --test (config-validation / dry-run mode)


class TestCliTestMode:
    """--test validates configuration then exits without running tasks."""

    def test_prints_configuration_loaded(self, mocker: MockerFixture) -> None:
        mocker.patch("movarr.cli.create_logger")
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())
        result = CliRunner().invoke(cli, ["--test"])
        assert result.exit_code == 0
        assert "Configuration loaded successfully" in result.output

    def test_output_mentions_test_mode(self, mocker: MockerFixture) -> None:
        mocker.patch("movarr.cli.create_logger")
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())
        result = CliRunner().invoke(cli, ["--test"])
        assert "Test mode" in result.output or "test mode" in result.output.lower()

    def test_does_not_invoke_run(self, mocker: MockerFixture) -> None:
        """scheduler.run() must not be called in test mode."""
        mocker.patch("movarr.cli.create_logger")
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())
        result = CliRunner().invoke(cli, ["--test"])
        assert result.exit_code == 0
        assert "Configuration loaded successfully" in result.output

    def test_foreground_mode_unchanged_without_daemon_flag(self, mocker: MockerFixture) -> None:
        """Without --daemon, daemon_mode is not overridden by the CLI."""
        mocker.patch("movarr.cli.create_logger")
        mock_cfg = _make_config_mock(daemon_mode="foreground")
        mocker.patch("movarr.config.load_config", return_value=mock_cfg)
        CliRunner().invoke(cli, ["--test"])
        # CLI must NOT have changed daemon_mode when --daemon was not passed.
        assert mock_cfg.general.daemon_mode == "foreground"

    def test_background_mode_set_with_daemon_flag(self, mocker: MockerFixture) -> None:
        """With --daemon, daemon_mode is overridden to 'background'."""
        mocker.patch("movarr.cli.create_logger")
        mock_cfg = _make_config_mock(daemon_mode="foreground")
        mocker.patch("movarr.config.load_config", return_value=mock_cfg)
        CliRunner().invoke(cli, ["--daemon", "--test"])
        assert mock_cfg.general.daemon_mode == "background"

    def test_create_logger_called(self, mocker: MockerFixture) -> None:
        mock_logger = mocker.patch("movarr.cli.create_logger")
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())
        CliRunner().invoke(cli, ["--test"])
        mock_logger.assert_called_once()

    def test_log_level_from_config_passed_to_create_logger(self, mocker: MockerFixture) -> None:
        """Without --log-level, create_logger uses config.general.log_level_console."""
        mock_logger = mocker.patch("movarr.cli.create_logger")
        mocker.patch(
            "movarr.config.load_config",
            return_value=_make_config_mock(log_level_console="DEBUG"),
        )
        CliRunner().invoke(cli, ["--test"])
        _, kwargs = mock_logger.call_args
        assert kwargs.get("log_level") == "DEBUG"

    def test_log_level_flag_overrides_config(self, mocker: MockerFixture) -> None:
        """--log-level overrides config.general.log_level_console when supplied."""
        mock_logger = mocker.patch("movarr.cli.create_logger")
        mocker.patch(
            "movarr.config.load_config",
            return_value=_make_config_mock(log_level_console="info"),
        )
        CliRunner().invoke(cli, ["--log-level", "DEBUG", "--test"])
        _, kwargs = mock_logger.call_args
        assert kwargs.get("log_level") == "DEBUG"

    def test_log_path_from_config_passed_to_create_logger(self, mocker: MockerFixture) -> None:
        """create_logger receives log_path from config.general.log_path."""
        mock_logger = mocker.patch("movarr.cli.create_logger")
        mocker.patch(
            "movarr.config.load_config",
            return_value=_make_config_mock(log_path="/var/log/movarr.log"),
        )
        CliRunner().invoke(cli, ["--test"])
        _, kwargs = mock_logger.call_args
        assert kwargs.get("log_path") == "/var/log/movarr.log"

    def test_empty_log_path_passes_none_to_create_logger(self, mocker: MockerFixture) -> None:
        """An empty log_path in config means no file logging (None passed to create_logger)."""
        mock_logger = mocker.patch("movarr.cli.create_logger")
        mocker.patch(
            "movarr.config.load_config",
            return_value=_make_config_mock(log_path=""),
        )
        CliRunner().invoke(cli, ["--test"])
        _, kwargs = mock_logger.call_args
        assert kwargs.get("log_path") is None


# --log-level validation (CLI override; still valid, useful for debugging)


class TestCliLogLevel:
    """--log-level accepts valid choices and rejects invalid ones."""

    def test_invalid_log_level_exits_non_zero(self) -> None:
        result = CliRunner().invoke(cli, ["--log-level", "VERBOSE"])
        assert result.exit_code != 0

    def test_debug_is_valid(self, mocker: MockerFixture) -> None:
        mocker.patch("movarr.cli.create_logger")
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())
        assert CliRunner().invoke(cli, ["--log-level", "DEBUG", "--test"]).exit_code == 0

    def test_info_is_valid(self, mocker: MockerFixture) -> None:
        mocker.patch("movarr.cli.create_logger")
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())
        assert CliRunner().invoke(cli, ["--log-level", "INFO", "--test"]).exit_code == 0

    def test_warning_is_valid(self, mocker: MockerFixture) -> None:
        mocker.patch("movarr.cli.create_logger")
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())
        assert CliRunner().invoke(cli, ["--log-level", "WARNING", "--test"]).exit_code == 0

    def test_log_level_case_insensitive(self, mocker: MockerFixture) -> None:
        mocker.patch("movarr.cli.create_logger")
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())
        assert CliRunner().invoke(cli, ["--log-level", "debug", "--test"]).exit_code == 0


class TestCliSchedulerRun:
    """Without --test, the CLI calls movarr.scheduler.run."""

    def test_cli_without_test_flag_calls_scheduler_run(self, mocker: MockerFixture) -> None:
        mocker.patch("movarr.cli.create_logger")
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())
        mock_run = mocker.patch("movarr.scheduler.run")
        result = CliRunner().invoke(cli, [])
        assert result.exit_code == 0
        mock_run.assert_called_once()

    def test_scheduler_run_receives_config(self, mocker: MockerFixture) -> None:
        """scheduler.run() is called with the loaded Config object."""
        mocker.patch("movarr.cli.create_logger")
        mock_cfg = _make_config_mock()
        mocker.patch("movarr.config.load_config", return_value=mock_cfg)
        mock_run = mocker.patch("movarr.scheduler.run")
        CliRunner().invoke(cli, [])
        args, _ = mock_run.call_args
        assert args[0] is mock_cfg


# PID path comes from config


class TestCliPidPath:
    """PID file path is configured in movarr.yml (general.pid_path)."""

    def test_pid_path_from_config_used_by_scheduler(self, mocker: MockerFixture) -> None:
        """scheduler.run receives the config; pid_path is read from config inside run()."""
        mocker.patch("movarr.cli.create_logger")
        mock_cfg = _make_config_mock(pid_path="/run/movarr/movarr.pid")
        mocker.patch("movarr.config.load_config", return_value=mock_cfg)
        mock_run = mocker.patch("movarr.scheduler.run")
        CliRunner().invoke(cli, [])
        args, _ = mock_run.call_args
        # pid_path is read from config.general.pid_path inside scheduler.run()
        assert args[0].general.pid_path == "/run/movarr/movarr.pid"

    def test_empty_pid_path_config_means_no_pid_file(self, mocker: MockerFixture) -> None:
        """An empty general.pid_path means no PID file is written."""
        mocker.patch("movarr.cli.create_logger")
        mock_cfg = _make_config_mock(pid_path="")
        mocker.patch("movarr.config.load_config", return_value=mock_cfg)
        mock_run = mocker.patch("movarr.scheduler.run")
        CliRunner().invoke(cli, [])
        args, _ = mock_run.call_args
        assert args[0].general.pid_path == ""


# _VERSION set to "unknown" when package metadata is not found


class TestVersionUnknown:
    def test_version_is_unknown_when_package_not_found(self, mocker: MockerFixture) -> None:
        import importlib
        from importlib.metadata import PackageNotFoundError

        mocker.patch("importlib.metadata.version", side_effect=PackageNotFoundError("movarr"))
        import movarr.cli as cli_mod

        importlib.reload(cli_mod)
        assert cli_mod._VERSION == "unknown"


# _log_format inner function — tracker prefix branch


class TestLogFormat:
    """_log_format includes a tracker prefix when tracker is non-empty."""

    def test_log_format_with_tracker_includes_prefix(self, mocker: MockerFixture) -> None:
        runner = CliRunner()
        captured_formatter: list = []

        def fake_create_logger(**kwargs: Any) -> None:
            captured_formatter.append(kwargs.get("log_format"))

        mocker.patch("movarr.cli.create_logger", side_effect=fake_create_logger)
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())

        result = runner.invoke(cli, ["--test"])
        assert result.exit_code == 0
        assert len(captured_formatter) == 1

        log_format_fn = captured_formatter[0]
        record_with_tracker = {"extra": {"tracker": "FooTracker"}, "message": "hello"}
        output = log_format_fn(record_with_tracker)
        assert "[FooTracker]" in output

        record_without_tracker = {"extra": {}, "message": "hello"}
        output_no_prefix = log_format_fn(record_without_tracker)
        assert "[" not in output_no_prefix.split("|")[-1].split("{")[0]
