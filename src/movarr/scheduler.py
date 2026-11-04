"""APScheduler-based task scheduler for movarr.

Three tasks run on configurable intervals:
  1. **search** — poll Jackett, filter results, add to qBittorrent, persist to DB.
  2. **queue_management** — delete metaDL/stalledDL torrents that exceed wait times.
  3. **post_processing** — copy completed torrents to the library.

The scheduler runs in **daemon mode** — a long-running foreground process
(suitable for systemd/Docker, writes a PID file) with one interval job per
enabled task.  ``run_once()`` executes each enabled task a single time; it is
the foreground/test helper, and it logs and exits when nothing is enabled.
"""

from __future__ import annotations

import datetime
import os
import signal
import threading
from contextlib import suppress
from typing import TYPE_CHECKING

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import SchedulerNotRunningError
from loguru import logger

from movarr.config import PID_FILENAME
from movarr.database import Database
from movarr.post_processor import run_post_processing
from movarr.qbittorrent import QBittorrentClient
from movarr.queue_manager import run_queue_management
from movarr.search import run_search

if TYPE_CHECKING:
    import types
    from collections.abc import Callable

    from movarr.config import Config, ScheduleTaskConfig

__all__ = ["run", "run_once"]


def _pid_file_path(pid_path: str) -> str:
    """Return the PID file path for *pid_path*.

    A directory whose name contains a dot (e.g. ``/run/movarr-1.3``) is still a
    directory, so the filesystem is consulted before the extension heuristic.
    """
    if os.path.isdir(pid_path) or not os.path.splitext(pid_path)[1]:
        return os.path.join(pid_path, PID_FILENAME)
    return pid_path


def _cleanup_pid_file(pid_path: str | None) -> None:
    """Remove the PID file at *pid_path* if it exists."""
    if pid_path:
        pid_file = _pid_file_path(pid_path)
        try:
            if os.path.exists(pid_file):
                os.unlink(pid_file)
        except OSError as exc:
            # Cleanup runs in a finally block; raising here would mask the real
            # failure that triggered shutdown.
            logger.warning("Could not remove PID file '{}': {}.", pid_file, exc)


def run(config: Config) -> None:
    """Start the scheduler in daemon mode.

    Runs the continuous scheduler (``_run_daemon``) when any task is enabled.
    With every task disabled there is nothing to schedule, so the process logs
    that and exits.

    The PID file path is taken from ``config.general.pid_path``;
    an empty string means no PID file is written.

    Args:
        config: Application configuration.
    """
    pid_path = config.general.pid_path or None
    if pid_path:
        _write_pid(pid_path)

    try:
        if _any_scheduler_enabled(config):
            _run_daemon(config)
        else:
            logger.info("All scheduler tasks are disabled; nothing to schedule.")
    finally:
        _cleanup_pid_file(pid_path)


def _any_scheduler_enabled(config: Config) -> bool:
    """Return True if at least one scheduler task is enabled in *config*."""
    return (
        config.schedule.acquisition.enabled
        or config.schedule.queue_management.enabled
        or config.schedule.post_processing.enabled
    )


def run_once(config: Config) -> None:
    """Execute each task exactly once (foreground / test mode).

    Args:
        config: Application configuration.
    """
    if not _any_scheduler_enabled(config):
        logger.info("All scheduler tasks are disabled; nothing to run in single-pass mode.")
        return

    logger.info("movarr running in single-pass foreground mode.")

    db = Database(config.general.db_path)
    qbt = _connect_qbt(config)

    if config.schedule.acquisition.enabled:
        _task_search(config, qbt, db)
    else:
        logger.info("Search task disabled; skipping.")

    if config.schedule.queue_management.enabled:
        _task_queue_management(config, qbt, db)
    else:
        logger.info("Queue management task disabled; skipping.")

    if config.schedule.post_processing.enabled:
        _task_post_processing(config, qbt, db)
    else:
        logger.info("Post-processing task disabled; skipping.")

    logger.info("Single-pass complete.")


def _next_run_kwargs(run_on_start: bool) -> dict:
    """Return ``next_run_time`` kwarg dict when *run_on_start* is True, else empty dict."""
    if run_on_start:
        return {"next_run_time": datetime.datetime.now(tz=datetime.UTC)}
    return {}


def _run_daemon(config: Config) -> None:
    """Run all three tasks on repeat using APScheduler BackgroundScheduler."""
    logger.info("movarr starting in daemon mode.")

    db = Database(config.general.db_path)
    qbt = _connect_qbt(config)

    scheduler = BackgroundScheduler()

    search_mins = config.schedule.acquisition.schedule_time_mins
    qm_mins = config.schedule.queue_management.schedule_time_mins
    pp_mins = config.schedule.post_processing.schedule_time_mins

    _add_job_if_enabled(
        scheduler,
        _task_search,
        "search",
        config.schedule.acquisition,
        config,
        qbt,
        db,
        name="Jackett search + filter + add",
    )
    _add_job_if_enabled(
        scheduler,
        _task_queue_management,
        "queue_management",
        config.schedule.queue_management,
        config,
        qbt,
        db,
    )
    _add_job_if_enabled(
        scheduler,
        _task_post_processing,
        "post_processing",
        config.schedule.post_processing,
        config,
        qbt,
        db,
        name="Post-processing (copy to library)",
    )

    # Block until SIGTERM or SIGINT.  The handlers are installed *before* the
    # scheduler starts: a signal arriving in the window between start() and
    # handler installation would take the default action, skipping the
    # finally-block PID cleanup in run().
    stop_event = threading.Event()

    def _shutdown(signum: int, frame: types.FrameType | None) -> None:
        logger.info("Received signal {}; shutting down.", signum)
        with suppress(SchedulerNotRunningError):
            scheduler.shutdown(wait=False)
        stop_event.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    scheduler.start()
    logger.info(
        "Scheduler started — search every {}m, queue_management every {}m, post_processing every {}m.",
        search_mins,
        qm_mins,
        pp_mins,
    )

    stop_event.wait()
    logger.info("movarr stopped.")


# Task wrappers — catch all exceptions so one bad run doesn't kill the scheduler


def _run_guarded(
    label: str,
    fn: Callable[[Config, QBittorrentClient, Database], None],
    config: Config,
    qbt: QBittorrentClient,
    db: Database,
) -> None:
    """Call *fn(config, qbt, db)*, logging any exception at ERROR level."""
    try:
        fn(config, qbt, db)
    except Exception:  # noqa: BLE001
        logger.exception("{} task failed.", label)


def _search_with_expiry(config: Config, qbt: QBittorrentClient, db: Database) -> None:
    """Expire history records then run search — inner callable for _task_search."""
    if n := db.expire_stalled(config.database.stalled_expiry_days):
        logger.info("Expired {} stalled history record(s) older than {} days.", n, config.database.stalled_expiry_days)
    if n := db.expire_failed(config.database.failed_expiry_days):
        logger.info("Expired {} failed history record(s) older than {} days.", n, config.database.failed_expiry_days)
    if n := db.expire_passed(config.database.passed_expiry_days):
        logger.info("Expired {} passed history record(s) older than {} days.", n, config.database.passed_expiry_days)
    run_search(config, qbt, db)


def _task_search(config: Config, qbt: QBittorrentClient, db: Database) -> None:
    _run_guarded("Search", _search_with_expiry, config, qbt, db)


def _task_queue_management(config: Config, qbt: QBittorrentClient, db: Database) -> None:
    _run_guarded("Queue management", run_queue_management, config, qbt, db)


def _task_post_processing(config: Config, qbt: QBittorrentClient, db: Database) -> None:
    _run_guarded("Post-processing", run_post_processing, config, qbt, db)


# Helpers


def _add_job_if_enabled(
    scheduler: BackgroundScheduler,
    task_fn: Callable[..., None],
    job_id: str,
    schedule_cfg: ScheduleTaskConfig,
    config: Config,
    qbt: QBittorrentClient,
    db: Database,
    *,
    name: str | None = None,
) -> None:
    """Register *task_fn* as a recurring interval job unless *schedule_cfg.enabled* is False."""
    if not schedule_cfg.enabled:
        logger.info("Scheduled {} is disabled — skipping.", job_id)
        return

    def _job() -> None:
        task_fn(config, qbt, db)
        try:
            _log_next_run(scheduler, job_id)
        except Exception:
            logger.exception("Failed to log next {} run time.", job_id)

    extra: dict = {"name": name} if name is not None else {}
    scheduler.add_job(
        _job,
        trigger="interval",
        minutes=schedule_cfg.schedule_time_mins,
        id=job_id,
        max_instances=1,
        coalesce=True,
        **_next_run_kwargs(schedule_cfg.run_on_start),
        **extra,
    )


def _connect_qbt(config: Config) -> QBittorrentClient:
    qbt = QBittorrentClient(config)
    if not qbt.is_reachable():
        logger.warning("qBittorrent WebUI is not reachable at startup; tasks will retry each interval.")
    elif not qbt.is_connected():
        logger.warning("qBittorrent is reachable but reports no internet connection.")
    return qbt


def _log_next_run(scheduler: BackgroundScheduler, job_id: str) -> None:
    """Log when the given APScheduler job will next run."""
    job = scheduler.get_job(job_id)
    if job is None or job.next_run_time is None:
        logger.info("Next {} run time unavailable.", job_id)
        return
    next_time = job.next_run_time.isoformat(sep=" ", timespec="seconds")
    logger.info("Next {} run at {}.", job_id, next_time)


def _write_pid(pid_path: str) -> None:
    pid_file = _pid_file_path(pid_path)
    try:
        pid_dir = os.path.dirname(pid_file)
        if pid_dir:
            os.makedirs(pid_dir, exist_ok=True)
        with open(pid_file, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        logger.debug("PID {} written to '{}'.", os.getpid(), pid_file)
    except OSError:
        logger.warning("Could not write PID file '{}'.", pid_file)
