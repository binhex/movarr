"""Unit tests for movarr.cli — Click command-line interface."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
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
) -> MagicMock:
    """Return a minimal mock Config object accepted by the cli function."""
    cfg = MagicMock()
    cfg.general.log_level_console = log_level_console
    cfg.general.log_path = log_path
    cfg.general.pid_path = pid_path
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

    def test_help_mentions_test(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert "--test" in result.output

    def test_help_mentions_log_path(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert "--log-path" in result.output

    def test_help_mentions_log_level(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert "--log-level" in result.output

    def test_help_mentions_pid_path(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert "--pid-path" in result.output


# --test (config-validation / dry-run mode)


class TestLoadDiagnosticsReplay:
    """Warnings logged while the config loads must reach the configured sinks."""

    def test_load_warning_reaches_configured_sink(self, mocker: MockerFixture) -> None:
        """A warning emitted before create_logger is replayed into the new sink."""
        from loguru import logger as loguru_logger

        captured: list[str] = []

        def _fake_load(_path: object) -> MagicMock:
            loguru_logger.warning("Unknown config keys (will be ignored): schedule.acquisition.bogus")
            return _make_config_mock()

        def _fake_create_logger(**_kwargs: object) -> object:
            loguru_logger.remove()
            loguru_logger.add(
                lambda message: captured.append(message.record["message"]),
                level="DEBUG",
                format="{message}",
            )
            return loguru_logger

        mocker.patch("movarr.config.load_config", side_effect=_fake_load)
        mocker.patch("movarr.cli.create_logger", side_effect=_fake_create_logger)
        try:
            result = CliRunner().invoke(cli, ["--test"])
        finally:
            loguru_logger.remove()

        assert result.exit_code == 0, result.output
        assert any("Unknown config keys" in message for message in captured)

    def test_failed_load_still_replays_diagnostics(self, mocker: MockerFixture) -> None:
        """A load failure re-emits the captured lines to stderr before propagating."""
        from loguru import logger as loguru_logger

        def _fake_load(_path: object) -> MagicMock:
            loguru_logger.warning("Config migrated from v2.21.0 to v2.22.0")
            raise ValueError("invalid config")

        mocker.patch("movarr.config.load_config", side_effect=_fake_load)
        try:
            result = CliRunner().invoke(cli, ["--test"])
        finally:
            loguru_logger.remove()

        assert isinstance(result.exception, ValueError)
        emitted = result.output + getattr(result, "stderr", "")
        assert "Config migrated from v2.21.0 to v2.22.0" in emitted

    def test_unusable_log_path_still_emits_diagnostics(self, mocker: MockerFixture) -> None:
        """A logger that cannot be created must not swallow the captured diagnostics."""
        from loguru import logger as loguru_logger

        def _fake_load(_path: object) -> MagicMock:
            loguru_logger.warning("Config migrated from v2.24.0 to v2.25.0")
            return _make_config_mock()

        def _fake_create_logger(**_kwargs: object) -> object:
            raise OSError("permission denied")

        mocker.patch("movarr.config.load_config", side_effect=_fake_load)
        mocker.patch("movarr.cli.create_logger", side_effect=_fake_create_logger)
        try:
            result = CliRunner().invoke(cli, ["--test"])
        finally:
            loguru_logger.remove()

        assert isinstance(result.exception, OSError)
        emitted = result.output + getattr(result, "stderr", "")
        assert "Config migrated from v2.24.0 to v2.25.0" in emitted

    def test_replayed_diagnostic_obeys_the_configured_level(self, mocker: MockerFixture) -> None:
        """Replayed lines pass through the configured levels, as the docstring states."""
        from loguru import logger as loguru_logger

        delivered: list[str] = []

        def _fake_load(_path: object) -> MagicMock:
            loguru_logger.warning("Config migrated from v2.24.0 to v2.25.0")
            return _make_config_mock()

        def _fake_create_logger(**_kwargs: object) -> object:
            loguru_logger.remove()
            loguru_logger.add(
                lambda message: delivered.append(message.record["message"]),
                level="ERROR",
                format="{message}",
            )
            return loguru_logger

        mocker.patch("movarr.config.load_config", side_effect=_fake_load)
        mocker.patch("movarr.cli.create_logger", side_effect=_fake_create_logger)
        try:
            result = CliRunner().invoke(cli, ["--test"])
        finally:
            loguru_logger.remove()

        assert result.exit_code == 0, result.output
        assert not any("Config migrated" in message for message in delivered)

    def test_tracker_name_with_loguru_markup_does_not_drop_records(self, mocker: MockerFixture) -> None:
        """An indexer-controlled tracker name must not break or forge log records."""
        from loguru import logger as loguru_logger

        rendered: list[str] = []
        trackers = (
            "Movies <1080p>",
            "Movies {1080p}",
            "Movies {",
            "Movies >x<",
            "Movies\nFORGED",
            "Movies\x1b]0;pwn\x07",
            "Movies\x9b31m",
        )

        def _fake_create_logger(log_format: Any, **_kwargs: object) -> object:
            loguru_logger.remove()
            loguru_logger.add(
                lambda message: rendered.append(str(message)),
                level="DEBUG",
                format=log_format,
                colorize=False,
            )
            return loguru_logger

        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())
        mocker.patch("movarr.cli.create_logger", side_effect=_fake_create_logger)
        try:
            result = CliRunner().invoke(cli, ["--test"])
            for tracker in trackers:
                with loguru_logger.contextualize(tracker=tracker):
                    loguru_logger.info("process result")
        finally:
            loguru_logger.remove()

        assert result.exit_code == 0, result.output
        lines = [line for line in rendered if "process result" in line]
        assert len(lines) == len(trackers)
        # One record per line, and no terminal control sequences from the tracker.
        assert all(line.count("\n") == 1 for line in lines)
        assert all("\x1b" not in line and "\x07" not in line for line in lines)


class TestCliStartupVersionLog:
    """movarr logs its version as the first line after logger initialisation."""

    def test_version_logged_on_startup(self, mocker: MockerFixture) -> None:
        """_logger.info is called with the version string before any other work."""
        mocker.patch("movarr.cli.create_logger")
        mocker.patch("movarr.config.load_config", return_value=_make_config_mock())
        mock_log = mocker.patch("movarr.cli._logger")

        CliRunner().invoke(cli, ["--test"])

        # First info call must mention the version.
        first_call = mock_log.info.call_args_list[0]
        fmt, version_arg = first_call[0][0], first_call[0][1]
        assert "v" in fmt or "v" in str(version_arg)
        # Value is either the real version string or 'unknown'.
        assert any(char.isdigit() for char in str(version_arg)) or version_arg == "unknown"

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
        """Without --log-path, create_logger uses config.general.log_path."""
        mock_logger = mocker.patch("movarr.cli.create_logger")
        mocker.patch(
            "movarr.config.load_config",
            return_value=_make_config_mock(log_path="/var/log/movarr.log"),
        )
        CliRunner().invoke(cli, ["--test"])
        _, kwargs = mock_logger.call_args
        assert kwargs.get("log_path") == "/var/log/movarr.log"

    def test_log_path_flag_overrides_config(self, mocker: MockerFixture) -> None:
        """--log-path overrides config.general.log_path when supplied."""
        mock_logger = mocker.patch("movarr.cli.create_logger")
        mocker.patch(
            "movarr.config.load_config",
            return_value=_make_config_mock(log_path="/var/log/movarr.log"),
        )
        CliRunner().invoke(cli, ["--log-path", "/tmp/debug.log", "--test"])
        _, kwargs = mock_logger.call_args
        assert kwargs.get("log_path") == "/tmp/debug.log"

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
        """The import-time fallback is exercised, and the real value is restored."""
        import importlib
        from importlib.metadata import PackageNotFoundError

        import movarr.cli as cli_mod

        def _raise(_name: str) -> str:
            raise PackageNotFoundError("movarr")

        patcher = mocker.patch("importlib.metadata.version", side_effect=_raise)
        try:
            importlib.reload(cli_mod)
            assert cli_mod._VERSION == "unknown"
        finally:
            # Restore the real module so later tests are not order-dependent.
            patcher.stop()
            importlib.reload(cli_mod)


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


class TestCliOverrides:
    """CLI options override the corresponding config values when supplied."""

    def _invoke(
        self,
        mocker: MockerFixture,
        args: list[str],
        log_level_console: str = "info",
        log_path: str = "",
        pid_path: str = "",
    ) -> MagicMock:
        """Invoke CLI with *args*, return the config mock that cli() operated on."""
        mocker.patch("movarr.cli.create_logger")
        cfg = _make_config_mock(
            log_level_console=log_level_console,
            log_path=log_path,
            pid_path=pid_path,
        )
        cfg.general.db_path = "db/movarr.db"
        cfg.general.library_path_list = []
        cfg.torrent_client.qbittorrent.host = "localhost"
        cfg.torrent_client.qbittorrent.port = 8080
        cfg.torrent_client.qbittorrent.username = "admin"
        cfg.torrent_client.qbittorrent.password = "adminadmin"
        cfg.index_proxy.selected = "jackett"
        cfg.index_proxy.jackett.host = "localhost"
        cfg.index_proxy.jackett.port = 9117
        cfg.index_proxy.jackett.api_key = ""
        cfg.index_proxy.prowlarr.host = "localhost"
        cfg.index_proxy.prowlarr.port = 9696
        cfg.index_proxy.prowlarr.api_key = ""
        mocker.patch("movarr.config.load_config", return_value=cfg)
        mocker.patch("movarr.scheduler.run")
        CliRunner().invoke(cli, args)
        return cfg

    def test_db_path_overrides_config(self, mocker: MockerFixture) -> None:
        cfg = self._invoke(mocker, ["--db-path", "/data/movarr.db", "--test"])
        assert cfg.general.db_path == "/data/movarr.db"

    def test_db_path_absent_leaves_config_unchanged(self, mocker: MockerFixture) -> None:
        cfg = self._invoke(mocker, ["--test"])
        assert cfg.general.db_path == "db/movarr.db"

    def test_library_path_list_single_path(self, mocker: MockerFixture) -> None:
        cfg = self._invoke(mocker, ["--library-path", "/media/movies", "--test"])
        assert cfg.general.library_path_list == ["/media/movies"]

    def test_library_path_list_multiple_paths(self, mocker: MockerFixture) -> None:
        cfg = self._invoke(mocker, ["--library-path", "/media/movies,/media/4k", "--test"])
        assert cfg.general.library_path_list == ["/media/movies", "/media/4k"]

    def test_library_path_list_strips_whitespace(self, mocker: MockerFixture) -> None:
        cfg = self._invoke(mocker, ["--library-path", "/media/movies, /media/4k", "--test"])
        assert cfg.general.library_path_list == ["/media/movies", "/media/4k"]

    def test_library_path_list_absent_leaves_config_unchanged(self, mocker: MockerFixture) -> None:
        cfg = self._invoke(mocker, ["--test"])
        assert cfg.general.library_path_list == []

    def test_qbt_host_overrides_config(self, mocker: MockerFixture) -> None:
        cfg = self._invoke(mocker, ["--qbt-host", "192.168.1.50", "--test"])
        assert cfg.torrent_client.qbittorrent.host == "192.168.1.50"

    def test_qbt_port_overrides_config(self, mocker: MockerFixture) -> None:
        cfg = self._invoke(mocker, ["--qbt-port", "8090", "--test"])
        assert cfg.torrent_client.qbittorrent.port == 8090

    def test_qbt_username_overrides_config(self, mocker: MockerFixture) -> None:
        cfg = self._invoke(mocker, ["--qbt-username", "myuser", "--test"])
        assert cfg.torrent_client.qbittorrent.username == "myuser"

    def test_qbt_password_overrides_config(self, mocker: MockerFixture) -> None:
        cfg = self._invoke(mocker, ["--qbt-password", "s3cr3t", "--test"])
        assert cfg.torrent_client.qbittorrent.password == "s3cr3t"

    def test_qbt_options_absent_leave_config_unchanged(self, mocker: MockerFixture) -> None:
        cfg = self._invoke(mocker, ["--test"])
        assert cfg.torrent_client.qbittorrent.host == "localhost"
        assert cfg.torrent_client.qbittorrent.port == 8080
        assert cfg.torrent_client.qbittorrent.username == "admin"
        assert cfg.torrent_client.qbittorrent.password == "adminadmin"

    def test_index_proxy_overrides_config(self, mocker: MockerFixture) -> None:
        cfg = self._invoke(mocker, ["--index-proxy", "prowlarr", "--test"])
        assert cfg.index_proxy.selected == "prowlarr"

    def test_index_proxy_case_insensitive(self, mocker: MockerFixture) -> None:
        """Click normalises mixed-case input to lowercase via case_sensitive=False."""
        cfg = self._invoke(mocker, ["--index-proxy", "PROWLARR", "--test"])
        assert cfg.index_proxy.selected == "prowlarr"

    def test_index_proxy_invalid_choice_exits_nonzero(self) -> None:
        result = CliRunner().invoke(cli, ["--index-proxy", "sonarr"])
        assert result.exit_code != 0

    def test_jackett_host_overrides_config(self, mocker: MockerFixture) -> None:
        cfg = self._invoke(mocker, ["--jackett-host", "192.168.1.60", "--test"])
        assert cfg.index_proxy.jackett.host == "192.168.1.60"

    def test_jackett_port_overrides_config(self, mocker: MockerFixture) -> None:
        cfg = self._invoke(mocker, ["--jackett-port", "9118", "--test"])
        assert cfg.index_proxy.jackett.port == 9118

    def test_jackett_api_key_overrides_config(self, mocker: MockerFixture) -> None:
        cfg = self._invoke(mocker, ["--jackett-api-key", "abc123", "--test"])
        assert cfg.index_proxy.jackett.api_key == "abc123"

    def test_jackett_options_absent_leave_config_unchanged(self, mocker: MockerFixture) -> None:
        cfg = self._invoke(mocker, ["--test"])
        assert cfg.index_proxy.jackett.host == "localhost"
        assert cfg.index_proxy.jackett.port == 9117
        assert cfg.index_proxy.jackett.api_key == ""

    def test_prowlarr_host_overrides_config(self, mocker: MockerFixture) -> None:
        cfg = self._invoke(mocker, ["--prowlarr-host", "192.168.1.70", "--test"])
        assert cfg.index_proxy.prowlarr.host == "192.168.1.70"

    def test_prowlarr_port_overrides_config(self, mocker: MockerFixture) -> None:
        cfg = self._invoke(mocker, ["--prowlarr-port", "9697", "--test"])
        assert cfg.index_proxy.prowlarr.port == 9697

    def test_prowlarr_api_key_overrides_config(self, mocker: MockerFixture) -> None:
        cfg = self._invoke(mocker, ["--prowlarr-api-key", "xyz789", "--test"])
        assert cfg.index_proxy.prowlarr.api_key == "xyz789"

    def test_prowlarr_options_absent_leave_config_unchanged(self, mocker: MockerFixture) -> None:
        cfg = self._invoke(mocker, ["--test"])
        assert cfg.index_proxy.prowlarr.host == "localhost"
        assert cfg.index_proxy.prowlarr.port == 9696
        assert cfg.index_proxy.prowlarr.api_key == ""

    def test_all_overrides_applied_together(self, mocker: MockerFixture) -> None:
        """All CLI overrides can be supplied simultaneously without conflict."""
        cfg = self._invoke(
            mocker,
            [
                "--db-path",
                "/data/movarr.db",
                "--library-path",
                "/media/movies,/media/4k",
                "--qbt-host",
                "10.0.0.5",
                "--qbt-port",
                "8090",
                "--qbt-username",
                "admin2",
                "--qbt-password",
                "pass2",
                "--index-proxy",
                "prowlarr",
                "--jackett-host",
                "10.0.0.6",
                "--jackett-port",
                "9118",
                "--jackett-api-key",
                "jkey",
                "--prowlarr-host",
                "10.0.0.7",
                "--prowlarr-port",
                "9697",
                "--prowlarr-api-key",
                "pkey",
                "--pid-path",
                "/run/movarr",
                "--test",
            ],
        )
        assert cfg.general.db_path == "/data/movarr.db"
        assert cfg.general.library_path_list == ["/media/movies", "/media/4k"]
        assert cfg.torrent_client.qbittorrent.host == "10.0.0.5"
        assert cfg.torrent_client.qbittorrent.port == 8090
        assert cfg.torrent_client.qbittorrent.username == "admin2"
        assert cfg.torrent_client.qbittorrent.password == "pass2"
        assert cfg.index_proxy.selected == "prowlarr"
        assert cfg.index_proxy.jackett.host == "10.0.0.6"
        assert cfg.index_proxy.jackett.port == 9118
        assert cfg.index_proxy.jackett.api_key == "jkey"
        assert cfg.index_proxy.prowlarr.host == "10.0.0.7"
        assert cfg.index_proxy.prowlarr.port == 9697
        assert cfg.index_proxy.prowlarr.api_key == "pkey"
        assert cfg.general.pid_path == "/run/movarr"

    def test_help_shows_all_new_options(self) -> None:
        """Every new CLI override option appears in the --help output."""
        result = CliRunner().invoke(cli, ["--help"])
        for opt in (
            "--db-path",
            "--library-path",
            "--qbt-host",
            "--qbt-port",
            "--qbt-username",
            "--qbt-password",
            "--index-proxy",
            "--jackett-host",
            "--jackett-port",
            "--jackett-api-key",
            "--prowlarr-host",
            "--prowlarr-port",
            "--prowlarr-api-key",
            "--pid-path",
        ):
            assert opt in result.output, f"{opt!r} missing from --help"


class TestCliPidPathOverride:
    """--pid-path CLI flag overrides config.general.pid_path."""

    def test_pid_path_flag_passed_to_override(self, mocker: MockerFixture) -> None:
        """--pid-path is forwarded via _apply_cli_overrides."""
        mocker.patch("movarr.cli.create_logger")
        mock_cfg = _make_config_mock(pid_path="pids")
        mocker.patch("movarr.config.load_config", return_value=mock_cfg)
        CliRunner().invoke(cli, ["--pid-path", "/run/movarr", "--test"])
        # config.general.pid_path was updated by the override
        assert mock_cfg.general.pid_path == "/run/movarr"

    def test_pid_path_default_from_config_used_when_not_supplied(self, mocker: MockerFixture) -> None:
        """Without --pid-path, config.general.pid_path is unchanged."""
        mocker.patch("movarr.cli.create_logger")
        mock_cfg = _make_config_mock(pid_path="/custom/pids")
        mocker.patch("movarr.config.load_config", return_value=mock_cfg)
        CliRunner().invoke(cli, ["--test"])
        assert mock_cfg.general.pid_path == "/custom/pids"


class TestOnceFlag:
    """--once runs each enabled task a single time and exits."""

    def test_once_invokes_run_once(self, mocker: MockerFixture, tmp_path: Path) -> None:
        from movarr.config import create_default_config

        create_default_config(tmp_path)  # --config-path takes a directory
        mock_once = mocker.patch("movarr.scheduler.run_once")
        mock_run = mocker.patch("movarr.scheduler.run")

        result = CliRunner().invoke(cli, ["--config-path", str(tmp_path), "--once"])

        assert result.exit_code == 0
        mock_once.assert_called_once()
        mock_run.assert_not_called()

    def test_help_mentions_once(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])

        assert "--once" in result.output
