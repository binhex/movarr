"""Unit tests for movarr.scheduler."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

import datetime

import pytest
from apscheduler.schedulers.base import SchedulerNotRunningError

from movarr.config import Config, GeneralConfig
from movarr.scheduler import (
    _connect_qbt,
    _log_next_run,
    _run_daemon,
    _task_post_processing,
    _task_queue_management,
    _task_search,
    _write_pid,
    run,
    run_once,
)

# _write_pid


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


# run_once


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


# run


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
        mock_write_pid = mocker.patch("movarr.scheduler._write_pid")
        mocker.patch("movarr.scheduler.run_once")
        pid_path = str(tmp_path / "run" / "movarr.pid")
        config = Config()

        run(config, pid_path=pid_path)

        mock_write_pid.assert_called_once_with(pid_path)

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

    def test_pid_file_removed_on_clean_exit(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """PID file is deleted in the finally block after run_once completes."""
        mocker.patch("movarr.scheduler.run_once")
        pid_path = str(tmp_path / "run" / "movarr.pid")
        config = Config()

        run(config, pid_path=pid_path)

        assert not Path(pid_path).exists()

    def test_pid_file_removed_on_exception(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """PID file is deleted in the finally block even when an exception is raised."""
        mocker.patch("movarr.scheduler.run_once", side_effect=RuntimeError("boom"))
        pid_path = str(tmp_path / "run" / "movarr.pid")
        config = Config()

        with pytest.raises(RuntimeError):
            run(config, pid_path=pid_path)

        assert not Path(pid_path).exists()


# _task_search


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


# _task_queue_management


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


# _task_post_processing


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


# _connect_qbt


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


# _run_daemon


class TestRunDaemon:
    """Tests for _run_daemon — APScheduler-based background loop."""

    def test_starts_three_scheduled_jobs(self, mocker: MockerFixture) -> None:
        """All three tasks are registered and the scheduler is started."""
        mocker.patch("movarr.scheduler.Database")
        mocker.patch("movarr.scheduler._connect_qbt")
        mock_sched_cls = mocker.patch("movarr.scheduler.BackgroundScheduler")
        mock_sched = mock_sched_cls.return_value
        mocker.patch("movarr.scheduler.threading.Event", return_value=mocker.MagicMock())

        _run_daemon(Config())

        assert mock_sched.add_job.call_count == 3
        mock_sched.start.assert_called_once()

    def test_job_ids_are_correct(self, mocker: MockerFixture) -> None:
        """The three scheduled jobs have the expected IDs."""
        mocker.patch("movarr.scheduler.Database")
        mocker.patch("movarr.scheduler._connect_qbt")
        mock_sched_cls = mocker.patch("movarr.scheduler.BackgroundScheduler")
        mock_sched = mock_sched_cls.return_value
        mocker.patch("movarr.scheduler.threading.Event", return_value=mocker.MagicMock())

        _run_daemon(Config())

        # check via keyword args
        kwarg_ids = {c.kwargs.get("id") for c in mock_sched.add_job.call_args_list}
        assert kwarg_ids == {"search", "queue_management", "post_processing"}


class TestRunDaemonSignalHandler:
    """Tests that _shutdown signal handler is properly invoked."""

    def test_shutdown_handler_shuts_down_scheduler_and_sets_stop_event(self, mocker: MockerFixture) -> None:
        """The _shutdown inner function shuts down the scheduler and sets the stop event."""
        mocker.patch("movarr.scheduler.Database")
        mocker.patch("movarr.scheduler._connect_qbt")
        mock_sched_cls = mocker.patch("movarr.scheduler.BackgroundScheduler")
        mock_sched = mock_sched_cls.return_value
        mock_event = mocker.MagicMock()
        mocker.patch("movarr.scheduler.threading.Event", return_value=mock_event)

        captured_handlers: dict = {}

        def capture_signal(sig: object, handler: object) -> None:
            captured_handlers[sig] = handler

        mocker.patch("movarr.scheduler.signal.signal", side_effect=capture_signal)

        _run_daemon(Config())

        import signal as signal_mod

        handler = captured_handlers.get(signal_mod.SIGTERM)
        assert handler is not None

        handler(signal_mod.SIGTERM, None)

        mock_sched.shutdown.assert_called()
        mock_event.set.assert_called()

    def test_signal_handler_when_scheduler_already_stopped_does_not_raise(self, mocker: MockerFixture) -> None:
        """If _shutdown is called after the scheduler is already stopped,
        it must not raise SchedulerNotRunningError."""
        mocker.patch("movarr.scheduler.Database")
        mocker.patch("movarr.scheduler._connect_qbt")
        mock_sched_cls = mocker.patch("movarr.scheduler.BackgroundScheduler")
        mock_sched = mock_sched_cls.return_value
        mock_event = mocker.MagicMock()
        mocker.patch("movarr.scheduler.threading.Event", return_value=mock_event)

        captured_handlers: dict = {}

        def capture_signal(sig: object, handler: object) -> None:
            captured_handlers[sig] = handler

        mocker.patch("movarr.scheduler.signal.signal", side_effect=capture_signal)

        _run_daemon(Config())

        import signal as signal_mod

        handler = captured_handlers.get(signal_mod.SIGINT)
        assert handler is not None

        # Simulate scheduler already stopped when handler fires.
        mock_sched.shutdown.side_effect = SchedulerNotRunningError

        # Must not raise SchedulerNotRunningError.
        handler(signal_mod.SIGINT, None)
        mock_event.set.assert_called()

    def test_stop_event_wait_is_called(self, mocker: MockerFixture) -> None:
        """_run_daemon blocks on stop_event.wait() until signalled."""
        mocker.patch("movarr.scheduler.Database")
        mocker.patch("movarr.scheduler._connect_qbt")
        mocker.patch("movarr.scheduler.BackgroundScheduler")
        mock_event = mocker.MagicMock()
        mocker.patch("movarr.scheduler.threading.Event", return_value=mock_event)

        _run_daemon(Config())

        mock_event.wait.assert_called_once()


# _run_daemon — run_on_start


class TestRunDaemonRunOnStart:
    """run_on_start=True must pass next_run_time to add_job; False must not."""

    def _kwargs_by_id(self, mock_sched: Any, job_id: str) -> dict[str, Any]:
        for call in mock_sched.add_job.call_args_list:
            if call.kwargs.get("id") == job_id:
                return call.kwargs  # type: ignore[no-any-return]
        raise AssertionError(f"No add_job call with id={job_id!r}")

    def test_run_on_start_false_does_not_pass_next_run_time(self, mocker: MockerFixture) -> None:
        """Explicit run_on_start=False: no next_run_time kwarg on any add_job call."""
        mocker.patch("movarr.scheduler.Database")
        mocker.patch("movarr.scheduler._connect_qbt")
        mock_sched_cls = mocker.patch("movarr.scheduler.BackgroundScheduler")
        mock_sched = mock_sched_cls.return_value
        mocker.patch("movarr.scheduler.threading.Event", return_value=mocker.MagicMock())

        config = Config()
        config.schedule.acquisition.run_on_start = False
        config.schedule.queue_management.run_on_start = False
        config.schedule.post_processing.run_on_start = False

        _run_daemon(config)

        for job_id in ("search", "queue_management", "post_processing"):
            assert "next_run_time" not in self._kwargs_by_id(mock_sched, job_id)

    def test_acquisition_run_on_start_sets_datetime_on_search_job(self, mocker: MockerFixture) -> None:
        """acquisition.run_on_start=True: search add_job gets a datetime next_run_time."""
        import datetime

        mocker.patch("movarr.scheduler.Database")
        mocker.patch("movarr.scheduler._connect_qbt")
        mock_sched_cls = mocker.patch("movarr.scheduler.BackgroundScheduler")
        mock_sched = mock_sched_cls.return_value
        mocker.patch("movarr.scheduler.threading.Event", return_value=mocker.MagicMock())

        config = Config()
        config.schedule.acquisition.run_on_start = True

        _run_daemon(config)

        kwargs = self._kwargs_by_id(mock_sched, "search")
        assert "next_run_time" in kwargs
        assert isinstance(kwargs["next_run_time"], datetime.datetime)

    def test_queue_management_run_on_start_sets_datetime_on_qm_job(self, mocker: MockerFixture) -> None:
        """queue_management.run_on_start=True: qm add_job gets a datetime next_run_time."""
        import datetime

        mocker.patch("movarr.scheduler.Database")
        mocker.patch("movarr.scheduler._connect_qbt")
        mock_sched_cls = mocker.patch("movarr.scheduler.BackgroundScheduler")
        mock_sched = mock_sched_cls.return_value
        mocker.patch("movarr.scheduler.threading.Event", return_value=mocker.MagicMock())

        config = Config()
        config.schedule.queue_management.run_on_start = True

        _run_daemon(config)

        kwargs = self._kwargs_by_id(mock_sched, "queue_management")
        assert "next_run_time" in kwargs
        assert isinstance(kwargs["next_run_time"], datetime.datetime)

    def test_post_processing_run_on_start_sets_datetime_on_pp_job(self, mocker: MockerFixture) -> None:
        """post_processing.run_on_start=True: pp add_job gets a datetime next_run_time."""
        import datetime

        mocker.patch("movarr.scheduler.Database")
        mocker.patch("movarr.scheduler._connect_qbt")
        mock_sched_cls = mocker.patch("movarr.scheduler.BackgroundScheduler")
        mock_sched = mock_sched_cls.return_value
        mocker.patch("movarr.scheduler.threading.Event", return_value=mocker.MagicMock())

        config = Config()
        config.schedule.post_processing.run_on_start = True

        _run_daemon(config)

        kwargs = self._kwargs_by_id(mock_sched, "post_processing")
        assert "next_run_time" in kwargs
        assert isinstance(kwargs["next_run_time"], datetime.datetime)

    def test_run_on_start_is_independent_per_task(self, mocker: MockerFixture) -> None:
        """Only the task with run_on_start=True gets next_run_time; others do not."""
        mocker.patch("movarr.scheduler.Database")
        mocker.patch("movarr.scheduler._connect_qbt")
        mock_sched_cls = mocker.patch("movarr.scheduler.BackgroundScheduler")
        mock_sched = mock_sched_cls.return_value
        mocker.patch("movarr.scheduler.threading.Event", return_value=mocker.MagicMock())

        config = Config()
        config.schedule.acquisition.run_on_start = True
        config.schedule.queue_management.run_on_start = False
        config.schedule.post_processing.run_on_start = False

        _run_daemon(config)

        assert "next_run_time" in self._kwargs_by_id(mock_sched, "search")
        assert "next_run_time" not in self._kwargs_by_id(mock_sched, "queue_management")
        assert "next_run_time" not in self._kwargs_by_id(mock_sched, "post_processing")


# _log_next_run


class TestLogNextRun:
    """Tests for _log_next_run — logs the next scheduled execution time."""

    def test_logs_next_run_time_as_hms(self, mocker: MockerFixture) -> None:
        """When a job has a valid next_run_time, it is logged as HH:MM:SS."""
        mock_scheduler = mocker.MagicMock()
        mock_job = mocker.MagicMock()
        mock_job.next_run_time = datetime.datetime(2026, 5, 4, 15, 30, 45)
        mock_scheduler.get_job.return_value = mock_job
        mock_info = mocker.patch("movarr.scheduler.logger.info")

        _log_next_run(mock_scheduler, "search")

        mock_scheduler.get_job.assert_called_once_with("search")
        logged_msg = mock_info.call_args[0][0]
        assert "15:30:45" in logged_msg

    def test_logs_job_id_in_message(self, mocker: MockerFixture) -> None:
        """The job_id is included in the log message so the user can tell which task re-runs next."""
        mock_scheduler = mocker.MagicMock()
        mock_job = mocker.MagicMock()
        mock_job.next_run_time = datetime.datetime(2026, 5, 4, 8, 0, 0)
        mock_scheduler.get_job.return_value = mock_job
        mock_info = mocker.patch("movarr.scheduler.logger.info")

        _log_next_run(mock_scheduler, "queue_management")

        logged_msg = mock_info.call_args[0][0]
        assert "queue_management" in logged_msg

    def test_handles_none_next_run_time(self, mocker: MockerFixture) -> None:
        """When job.next_run_time is None, a fallback message is logged without error."""
        mock_scheduler = mocker.MagicMock()
        mock_job = mocker.MagicMock()
        mock_job.next_run_time = None
        mock_scheduler.get_job.return_value = mock_job
        mock_info = mocker.patch("movarr.scheduler.logger.info")

        _log_next_run(mock_scheduler, "post_processing")

        mock_info.assert_called_once()

    def test_handles_job_not_found(self, mocker: MockerFixture) -> None:
        """When get_job returns None, a fallback message is logged without error."""
        mock_scheduler = mocker.MagicMock()
        mock_scheduler.get_job.return_value = None
        mock_info = mocker.patch("movarr.scheduler.logger.info")

        _log_next_run(mock_scheduler, "search")

        mock_info.assert_called_once()

    def test_called_after_task_in_daemon_closure(self, mocker: MockerFixture) -> None:
        """When _run_daemon registers jobs, the callable invokes _log_next_run after the task."""
        mocker.patch("movarr.scheduler.Database")
        mocker.patch("movarr.scheduler._connect_qbt")
        mocker.patch("movarr.scheduler._task_search")
        mock_log_next_run = mocker.patch("movarr.scheduler._log_next_run")
        mock_sched_cls = mocker.patch("movarr.scheduler.BackgroundScheduler")
        mock_sched = mock_sched_cls.return_value
        mocker.patch("movarr.scheduler.threading.Event", return_value=mocker.MagicMock())

        _run_daemon(Config())

        # Retrieve the callable registered for the "search" job and invoke it.
        search_callable = None
        for call in mock_sched.add_job.call_args_list:
            if call.kwargs.get("id") == "search":
                search_callable = call.args[0]
                break
        assert search_callable is not None, "No job registered with id='search'"

        search_callable()

        mock_log_next_run.assert_called_once_with(mock_sched, "search")

    def test_queue_management_closure_calls_log_next_run(self, mocker: MockerFixture) -> None:
        """queue_management closure calls _log_next_run with the correct job_id."""
        mocker.patch("movarr.scheduler.Database")
        mocker.patch("movarr.scheduler._connect_qbt")
        mocker.patch("movarr.scheduler._task_queue_management")
        mock_log_next_run = mocker.patch("movarr.scheduler._log_next_run")
        mock_sched_cls = mocker.patch("movarr.scheduler.BackgroundScheduler")
        mock_sched = mock_sched_cls.return_value
        mocker.patch("movarr.scheduler.threading.Event", return_value=mocker.MagicMock())

        _run_daemon(Config())

        qm_callable = None
        for call in mock_sched.add_job.call_args_list:
            if call.kwargs.get("id") == "queue_management":
                qm_callable = call.args[0]
                break
        assert qm_callable is not None

        qm_callable()

        mock_log_next_run.assert_called_once_with(mock_sched, "queue_management")

    def test_post_processing_closure_calls_log_next_run(self, mocker: MockerFixture) -> None:
        """post_processing closure calls _log_next_run with the correct job_id."""
        mocker.patch("movarr.scheduler.Database")
        mocker.patch("movarr.scheduler._connect_qbt")
        mocker.patch("movarr.scheduler._task_post_processing")
        mock_log_next_run = mocker.patch("movarr.scheduler._log_next_run")
        mock_sched_cls = mocker.patch("movarr.scheduler.BackgroundScheduler")
        mock_sched = mock_sched_cls.return_value
        mocker.patch("movarr.scheduler.threading.Event", return_value=mocker.MagicMock())

        _run_daemon(Config())

        pp_callable = None
        for call in mock_sched.add_job.call_args_list:
            if call.kwargs.get("id") == "post_processing":
                pp_callable = call.args[0]
                break
        assert pp_callable is not None

        pp_callable()

        mock_log_next_run.assert_called_once_with(mock_sched, "post_processing")

    def _get_job_callable(self, mock_sched: Any, job_id: str) -> Any:
        for call in mock_sched.add_job.call_args_list:
            if call.kwargs.get("id") == job_id:
                return call.args[0]
        raise AssertionError(f"No add_job call with id={job_id!r}")

    def test_log_next_run_exception_swallowed_in_search_closure(self, mocker: MockerFixture) -> None:
        """If _log_next_run raises in _search_job, the exception is swallowed and logged."""
        mocker.patch("movarr.scheduler.Database")
        mocker.patch("movarr.scheduler._connect_qbt")
        mocker.patch("movarr.scheduler._task_search")
        mocker.patch("movarr.scheduler._log_next_run", side_effect=RuntimeError("scheduler gone"))
        mock_sched_cls = mocker.patch("movarr.scheduler.BackgroundScheduler")
        mock_sched = mock_sched_cls.return_value
        mocker.patch("movarr.scheduler.threading.Event", return_value=mocker.MagicMock())

        _run_daemon(Config())

        # Must not raise
        self._get_job_callable(mock_sched, "search")()

    def test_log_next_run_exception_swallowed_in_queue_management_closure(self, mocker: MockerFixture) -> None:
        """If _log_next_run raises in _queue_management_job, the exception is swallowed."""
        mocker.patch("movarr.scheduler.Database")
        mocker.patch("movarr.scheduler._connect_qbt")
        mocker.patch("movarr.scheduler._task_queue_management")
        mocker.patch("movarr.scheduler._log_next_run", side_effect=RuntimeError("scheduler gone"))
        mock_sched_cls = mocker.patch("movarr.scheduler.BackgroundScheduler")
        mock_sched = mock_sched_cls.return_value
        mocker.patch("movarr.scheduler.threading.Event", return_value=mocker.MagicMock())

        _run_daemon(Config())

        self._get_job_callable(mock_sched, "queue_management")()

    def test_log_next_run_exception_swallowed_in_post_processing_closure(self, mocker: MockerFixture) -> None:
        """If _log_next_run raises in _post_processing_job, the exception is swallowed."""
        mocker.patch("movarr.scheduler.Database")
        mocker.patch("movarr.scheduler._connect_qbt")
        mocker.patch("movarr.scheduler._task_post_processing")
        mocker.patch("movarr.scheduler._log_next_run", side_effect=RuntimeError("scheduler gone"))
        mock_sched_cls = mocker.patch("movarr.scheduler.BackgroundScheduler")
        mock_sched = mock_sched_cls.return_value
        mocker.patch("movarr.scheduler.threading.Event", return_value=mocker.MagicMock())

        _run_daemon(Config())

        self._get_job_callable(mock_sched, "post_processing")()
