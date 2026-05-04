"""APScheduler-based task scheduler for movarr.

Three tasks run on configurable intervals:
  1. **search** — poll Jackett, filter results, add to qBittorrent, persist to DB.
  2. **queue_management** — delete metaDL/stalledDL torrents that exceed wait times.
  3. **post_processing** — copy completed torrents to the library.

The scheduler can run in:
  - **daemon mode** — background process (detaches, writes PID file).
  - **foreground mode** — runs each task once then exits (useful for testing).
"""

from __future__ import annotations

import datetime
import os
import signal
import sys
import time
from contextlib import suppress
from typing import TYPE_CHECKING

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import SchedulerNotRunningError
from loguru import logger

from movarr.database import Database
from movarr.post_processor import run_post_processing
from movarr.qbittorrent import QBittorrentClient
from movarr.queue_manager import run_queue_management
from movarr.search import run_search

if TYPE_CHECKING:
    import types

    from movarr.config import Config

__all__ = ["run", "run_once"]


def run(config: Config, pid_path: str | None = None) -> None:
    """Start the scheduler in daemon/foreground mode.

    Reads ``config.general.daemon`` to decide between background and single-pass.

    Args:
        config: Application configuration.
        pid_path: Optional path to write the PID file.
    """
    if pid_path:
        _write_pid(pid_path)

    try:
        if config.general.daemon_mode == "background":
            _run_daemon(config)
        else:
            run_once(config)
    finally:
        if pid_path and os.path.exists(pid_path):
            os.unlink(pid_path)


def run_once(config: Config) -> None:
    """Execute each task exactly once (foreground / test mode).

    Args:
        config: Application configuration.
    """
    logger.info("movarr running in single-pass foreground mode.")

    db = Database(config.general.db_path)
    qbt = _connect_qbt(config)

    _task_search(config, qbt, db)
    _task_queue_management(config, qbt, db)
    _task_post_processing(config, qbt, db)

    logger.info("Single-pass complete.")


def _next_run_kwargs(run_on_start: bool) -> dict:
    """Return ``next_run_time`` kwarg dict when *run_on_start* is True, else empty dict."""
    if run_on_start:
        return {"next_run_time": datetime.datetime.now()}
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

    def _search_job() -> None:
        _task_search(config, qbt, db)
        try:
            _log_next_run(scheduler, "search")
        except Exception:
            logger.exception("Failed to log next search run time.")

    def _queue_management_job() -> None:
        _task_queue_management(config, qbt, db)
        try:
            _log_next_run(scheduler, "queue_management")
        except Exception:
            logger.exception("Failed to log next queue_management run time.")

    def _post_processing_job() -> None:
        _task_post_processing(config, qbt, db)
        try:
            _log_next_run(scheduler, "post_processing")
        except Exception:
            logger.exception("Failed to log next post_processing run time.")

    scheduler.add_job(
        _search_job,
        trigger="interval",
        minutes=search_mins,
        id="search",
        name="Jackett search + filter + add",
        max_instances=1,
        coalesce=True,
        **_next_run_kwargs(config.schedule.acquisition.run_on_start),
    )
    scheduler.add_job(
        _queue_management_job,
        trigger="interval",
        minutes=qm_mins,
        id="queue_management",
        max_instances=1,
        coalesce=True,
        **_next_run_kwargs(config.schedule.queue_management.run_on_start),
    )
    scheduler.add_job(
        _post_processing_job,
        trigger="interval",
        minutes=pp_mins,
        id="post_processing",
        name="Post-processing (copy to library)",
        max_instances=1,
        coalesce=True,
        **_next_run_kwargs(config.schedule.post_processing.run_on_start),
    )

    scheduler.start()
    logger.info(
        "Scheduler started — search every {}m, queue_management every {}m, post_processing every {}m.",
        search_mins,
        qm_mins,
        pp_mins,
    )

    # Block until SIGTERM or SIGINT.
    def _shutdown(signum: int, frame: types.FrameType | None) -> None:
        logger.info("Received signal {}; shutting down.", signum)
        with suppress(SchedulerNotRunningError):
            scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        while True:
            time.sleep(30)
    except (KeyboardInterrupt, SystemExit):
        with suppress(SchedulerNotRunningError):
            scheduler.shutdown(wait=False)


# Task wrappers — catch all exceptions so one bad run doesn't kill the scheduler


def _task_search(config: Config, qbt: QBittorrentClient, db: Database) -> None:
    try:
        if n := db.expire_stalled(config.database.stalled_expiry_days):
            logger.info(f"Expired {n} stalled history record(s) older than {config.database.stalled_expiry_days} days.")
        if n := db.expire_failed(config.database.failed_expiry_days):
            logger.info(f"Expired {n} failed history record(s) older than {config.database.failed_expiry_days} days.")
        if n := db.expire_passed(config.database.passed_expiry_days):
            logger.info(f"Expired {n} passed history record(s) older than {config.database.passed_expiry_days} days.")
        run_search(config, qbt, db)
    except Exception:
        logger.exception("Search task failed.")


def _task_queue_management(config: Config, qbt: QBittorrentClient, db: Database) -> None:
    try:
        run_queue_management(config, qbt, db)
    except Exception:
        logger.exception("Queue management task failed.")


def _task_post_processing(config: Config, qbt: QBittorrentClient, db: Database) -> None:
    try:
        run_post_processing(config, qbt, db)
    except Exception:
        logger.exception("Post-processing task failed.")


# Helpers


def _connect_qbt(config: Config) -> QBittorrentClient:
    qbt = QBittorrentClient(config)
    if not qbt.is_connected():
        logger.warning("qBittorrent is not reachable at startup; tasks will retry each interval.")
    return qbt


def _log_next_run(scheduler: BackgroundScheduler, job_id: str) -> None:
    """Log when the given APScheduler job will next run."""
    job = scheduler.get_job(job_id)
    if job is None or job.next_run_time is None:
        logger.info(f"Next {job_id} run time unavailable.")
        return
    next_time = job.next_run_time.isoformat(sep=" ", timespec="seconds")
    logger.info(f"Next {job_id} run at {next_time}.")


def _write_pid(pid_path: str) -> None:
    try:
        os.makedirs(os.path.dirname(pid_path), exist_ok=True)
        with open(pid_path, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        logger.debug("PID {} written to '{}'.", os.getpid(), pid_path)
    except OSError:
        logger.warning("Could not write PID file '{}'.", pid_path)
