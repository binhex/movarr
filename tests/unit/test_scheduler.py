"""Unit tests for movarr.scheduler."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

from movarr.config import Config, GeneralConfig
from movarr.scheduler import (
    _connect_qbt,
    _run_daemon,
    _task_post_processing,
    _task_queue_management,
    _task_search,
    _write_pid,
    run,
    run_once,
)

# ---------------------------------------------------------------------------
# _write_pid
# ---------------------------------------------------------------------------


class TestWritePid:
    """Tests for _write_pid."""

    def test_writes_current_pid_to_file(self, tmp_path: Path) -> None:
        """PID file contains the current process ID."""
        pid_path = str(tmp_path / "run" / "movarr.pid")
        _write_pid(pid_path)
        assert Path(pid_path).exists()
        assert Path(pid_path).read_text() == str(os.getpid())

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        """Parent directories are created if they do not exist."""
        pid_path = str(tmp_path / "nested" / "dir" / "movarr.pid")
        _write_pid(pid_path)
        assert Path(pid_path).exists()

    def test_oserror_does_not_propagate(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """OSError while writing PID is logged but not re-raised."""
        mocker.patch("os.makedirs", side_effect=OSError("permission denied"))
        pid_path = str(tmp_path / "run" / "movarr.pid")
        # Must not raise
        _write_pid(pid_path)


# ---------------------------------------------------------------------------
# run_once
# ---------------------------------------------------------------------------


class TestRunOnce:
    """Tests for run_once — single-pass foreground mode."""

    def test_instantiates_database_and_qbt(self, mocker: MockerFixture) -> None:
        mock_db_cls = mocker.patch("movarr.scheduler.Database")
        mock_qbt_cls = mocker.patch("movarr.scheduler.QBittorrentClient")
        mocker.patch("movarr.scheduler.run_search")
        mocker.patch("movarr.scheduler.run_queue_management")
        mocker.patch("movarr.scheduler.run_post_processing")
        config = Config()

        run_once(config)

        mock_db_cls.assert_called_once_with(config.general.db_path)
        mock_qbt_cls.assert_called_once_with(config)

    def test_calls_all_three_task_wrappers(self, mocker: MockerFixture) -> None:
        mocker.patch("movarr.scheduler.Database")
        mocker.patch("movarr.scheduler.QBittorrentClient")
        mock_search = mocker.patch("movarr.scheduler.run_search")
        mock_qm = mocker.patch("movarr.scheduler.run_queue_management")
        mock_pp = mocker.patch("movarr.scheduler.run_post_processing")
        config = Config()

        run_once(config)

        mock_search.assert_called_once()
        mock_qm.assert_called_once()
        mock_pp.assert_called_once()

    def test_task_wrappers_receive_correct_types(self, mocker: MockerFixture) -> None:
        mock_db_cls = mocker.patch("movarr.scheduler.Database")
        mock_qbt_cls = mocker.patch("movarr.scheduler.QBittorrentClient")
        mock_search = mocker.patch("movarr.scheduler.run_search")
        mocker.patch("movarr.scheduler.run_queue_management")
        mocker.patch("movarr.scheduler.run_post_processing")
        config = Config()

        run_once(config)

        call_args = mock_search.call_args
        assert call_args[0][0] is config
        assert call_args[0][1] is mock_qbt_cls.return_value
        assert call_args[0][2] is mock_db_cls.return_value


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


class TestRun:
    """Tests for run — dispatcher to foreground or daemon mode."""

    def test_foreground_mode_delegates_to_run_once(self, mocker: MockerFixture) -> None:
        mock_run_once = mocker.patch("movarr.scheduler.run_once")
        mock_run_daemon = mocker.patch("movarr.scheduler._run_daemon")
        config = Config()  # daemon_mode="foreground" by default

        run(config)

        mock_run_once.assert_called_once_with(config)
        mock_run_daemon.assert_not_called()

    def test_background_mode_delegates_to_run_daemon(self, mocker: MockerFixture) -> None:
        mock_run_once = mocker.patch("movarr.scheduler.run_once")
        mock_run_daemon = mocker.patch("movarr.scheduler._run_daemon")
        config = Config(general=GeneralConfig(daemon_mode="background"))

        run(config)

        mock_run_daemon.assert_called_once_with(config)
        mock_run_once.assert_not_called()

    def test_writes_pid_when_pid_path_provided(self, mocker: MockerFixture, tmp_path: Path) -> None:
        mocker.patch("movarr.scheduler.run_once")
        pid_path = str(tmp_path / "run" / "movarr.pid")
        config = Config()

        run(config, pid_path=pid_path)

        assert Path(pid_path).exists()

    def test_no_pid_written_when_pid_path_is_none(self, mocker: MockerFixture) -> None:
        mock_write_pid = mocker.patch("movarr.scheduler._write_pid")
        mocker.patch("movarr.scheduler.run_once")
        config = Config()

        run(config)

        mock_write_pid.assert_not_called()

    def test_no_pid_written_when_pid_path_falsy(self, mocker: MockerFixture) -> None:
        mock_write_pid = mocker.patch("movarr.scheduler._write_pid")
        mocker.patch("movarr.scheduler.run_once")
        config = Config()

        run(config, pid_path=None)

        mock_write_pid.assert_not_called()


# ---------------------------------------------------------------------------
# _task_search
# ---------------------------------------------------------------------------


class TestTaskSearch:
    """Tests for _task_search — exception-swallowing wrapper."""

    def test_calls_run_search_with_correct_args(self, mocker: MockerFixture) -> None:
        mock_run_search = mocker.patch("movarr.scheduler.run_search")
        config = Config()
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()

        _task_search(config, qbt, db)

        mock_run_search.assert_called_once_with(config, qbt, db)

    def test_expire_stalled_and_failed_called_before_search(self, mocker: MockerFixture) -> None:
        """expire_stalled, expire_failed and expire_passed must be called before run_search."""
        call_order: list[str] = []
        mocker.patch("movarr.scheduler.run_search", side_effect=lambda *_: call_order.append("search"))
        config = Config()
        db = mocker.MagicMock()
        db.expire_stalled.side_effect = lambda _: call_order.append("expire_stalled")
        db.expire_failed.side_effect = lambda _: call_order.append("expire_failed")
        db.expire_passed.side_effect = lambda _: call_order.append("expire_passed")

        _task_search(config, mocker.MagicMock(), db)

        assert call_order.index("expire_stalled") < call_order.index("search")
        assert call_order.index("expire_failed") < call_order.index("search")
        assert call_order.index("expire_passed") < call_order.index("search")

    def test_expire_failed_called_with_config_days(self, mocker: MockerFixture) -> None:
        """expire_failed receives the failed_expiry_days from config."""
        mocker.patch("movarr.scheduler.run_search")
        config = Config()
        config.database.failed_expiry_days = 14
        db = mocker.MagicMock()

        _task_search(config, mocker.MagicMock(), db)

        db.expire_failed.assert_called_once_with(14)

    def test_expire_passed_called_with_config_days(self, mocker: MockerFixture) -> None:
        """expire_passed receives the passed_expiry_days from config."""
        mocker.patch("movarr.scheduler.run_search")
        config = Config()
        config.database.passed_expiry_days = 60
        db = mocker.MagicMock()

        _task_search(config, mocker.MagicMock(), db)

        db.expire_passed.assert_called_once_with(60)

    def test_exception_is_swallowed(self, mocker: MockerFixture) -> None:
        """run_search raising must not propagate out of _task_search."""
        mocker.patch("movarr.scheduler.run_search", side_effect=RuntimeError("network error"))
        config = Config()
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()

        # Must not raise
        _task_search(config, qbt, db)

    def test_value_error_is_swallowed(self, mocker: MockerFixture) -> None:
        mocker.patch("movarr.scheduler.run_search", side_effect=ValueError("bad data"))
        config = Config()

        _task_search(config, mocker.MagicMock(), mocker.MagicMock())


# ---------------------------------------------------------------------------
# _task_queue_management
# ---------------------------------------------------------------------------


class TestTaskQueueManagement:
    """Tests for _task_queue_management — exception-swallowing wrapper."""

    def test_calls_run_queue_management_with_correct_args(self, mocker: MockerFixture) -> None:
        mock_qm = mocker.patch("movarr.scheduler.run_queue_management")
        config = Config()
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()

        _task_queue_management(config, qbt, db)

        mock_qm.assert_called_once_with(config, qbt, db)

    def test_exception_is_swallowed(self, mocker: MockerFixture) -> None:
        mocker.patch("movarr.scheduler.run_queue_management", side_effect=RuntimeError("timeout"))
        config = Config()

        # Must not raise
        _task_queue_management(config, mocker.MagicMock(), mocker.MagicMock())

    def test_connection_error_is_swallowed(self, mocker: MockerFixture) -> None:
        mocker.patch("movarr.scheduler.run_queue_management", side_effect=ConnectionError("refused"))
        config = Config()

        _task_queue_management(config, mocker.MagicMock(), mocker.MagicMock())


# ---------------------------------------------------------------------------
# _task_post_processing
# ---------------------------------------------------------------------------


class TestTaskPostProcessing:
    """Tests for _task_post_processing — exception-swallowing wrapper."""

    def test_calls_run_post_processing_with_correct_args(self, mocker: MockerFixture) -> None:
        mock_pp = mocker.patch("movarr.scheduler.run_post_processing")
        config = Config()
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()

        _task_post_processing(config, qbt, db)

        mock_pp.assert_called_once_with(config, qbt, db)

    def test_exception_is_swallowed(self, mocker: MockerFixture) -> None:
        mocker.patch("movarr.scheduler.run_post_processing", side_effect=RuntimeError("copy failed"))
        config = Config()

        # Must not raise
        _task_post_processing(config, mocker.MagicMock(), mocker.MagicMock())

    def test_os_error_is_swallowed(self, mocker: MockerFixture) -> None:
        mocker.patch("movarr.scheduler.run_post_processing", side_effect=OSError("disk full"))
        config = Config()

        _task_post_processing(config, mocker.MagicMock(), mocker.MagicMock())


# ---------------------------------------------------------------------------
# _connect_qbt
# ---------------------------------------------------------------------------


class TestConnectQbt:
    """Tests for _connect_qbt — QBittorrent startup connection check."""

    def test_no_warning_when_connected(self, mocker: MockerFixture) -> None:
        mock_cls = mocker.patch("movarr.scheduler.QBittorrentClient")
        mock_cls.return_value.is_connected.return_value = True
        mock_warn = mocker.patch("movarr.scheduler.logger.warning")

        _connect_qbt(Config())

        mock_warn.assert_not_called()

    def test_warns_when_not_connected(self, mocker: MockerFixture) -> None:
        """When is_connected() returns False a warning is emitted (line 176)."""
        mock_cls = mocker.patch("movarr.scheduler.QBittorrentClient")
        mock_cls.return_value.is_connected.return_value = False
        mock_warn = mocker.patch("movarr.scheduler.logger.warning")

        _connect_qbt(Config())

        mock_warn.assert_called_once()

    def test_returns_qbt_instance(self, mocker: MockerFixture) -> None:
        mock_cls = mocker.patch("movarr.scheduler.QBittorrentClient")
        expected = mock_cls.return_value
        expected.is_connected.return_value = True

        result = _connect_qbt(Config())

        assert result is expected


# ---------------------------------------------------------------------------
# _run_daemon
# ---------------------------------------------------------------------------


class TestRunDaemon:
    """Tests for _run_daemon — APScheduler-based background loop."""

    def test_starts_three_scheduled_jobs_then_exits_on_keyboard_interrupt(self, mocker: MockerFixture) -> None:
        """All three tasks are registered then the loop exits on KeyboardInterrupt."""
        mocker.patch("movarr.scheduler.Database")
        mocker.patch("movarr.scheduler._connect_qbt")
        mock_sched_cls = mocker.patch("movarr.scheduler.BackgroundScheduler")
        mock_sched = mock_sched_cls.return_value
        mocker.patch("movarr.scheduler.time.sleep", side_effect=KeyboardInterrupt)

        _run_daemon(Config())

        assert mock_sched.add_job.call_count == 3
        mock_sched.start.assert_called_once()
        mock_sched.shutdown.assert_called()

    def test_job_ids_are_correct(self, mocker: MockerFixture) -> None:
        """The three scheduled jobs have the expected IDs."""
        mocker.patch("movarr.scheduler.Database")
        mocker.patch("movarr.scheduler._connect_qbt")
        mock_sched_cls = mocker.patch("movarr.scheduler.BackgroundScheduler")
        mock_sched = mock_sched_cls.return_value
        mocker.patch("movarr.scheduler.time.sleep", side_effect=KeyboardInterrupt)

        _run_daemon(Config())

        # check via keyword args
        kwarg_ids = {c.kwargs.get("id") for c in mock_sched.add_job.call_args_list}
        assert kwarg_ids == {"search", "queue_management", "post_processing"}


class TestRunDaemonSignalHandler:
    """Tests that _shutdown signal handler is properly invoked."""

    def test_shutdown_handler_calls_sys_exit(self, mocker: MockerFixture) -> None:
        """The _shutdown inner function calls scheduler.shutdown and sys.exit(0)."""
        mocker.patch("movarr.scheduler.Database")
        mocker.patch("movarr.scheduler._connect_qbt")
        mock_sched_cls = mocker.patch("movarr.scheduler.BackgroundScheduler")
        mock_sched = mock_sched_cls.return_value
        mock_sys_exit = mocker.patch("movarr.scheduler.sys.exit")

        captured_handlers: dict = {}

        def capture_signal(sig, handler):
            captured_handlers[sig] = handler

        mocker.patch("movarr.scheduler.signal.signal", side_effect=capture_signal)
        # After handlers are registered, raise SystemExit to stop the sleep loop.
        mocker.patch("movarr.scheduler.time.sleep", side_effect=SystemExit)

        _run_daemon(Config())

        import signal as signal_mod

        handler = captured_handlers.get(signal_mod.SIGTERM)
        assert handler is not None

        # Call the captured handler directly.
        handler(signal_mod.SIGTERM, None)

        mock_sched.shutdown.assert_called()
        mock_sys_exit.assert_called_with(0)
