"""Queue management task for movarr.

Deletes torrents that have been stuck in metaDL or stalledDL states longer than
the configured maximum wait time.  This prevents the queue from filling with
torrents that will never complete.
"""

from __future__ import annotations

from loguru import logger

from movarr.config import Config
from movarr.qbittorrent import QBittorrentClient

__all__ = ["run_queue_management"]


def run_queue_management(config: Config, qbt: QBittorrentClient) -> None:
    """Check for stuck torrents and delete them.

    Args:
        config: Application configuration.
        qbt: An already-connected ``QBittorrentClient`` instance.
    """
    qm_cfg = config.queue_management
    if not qm_cfg.queue_management_enabled:
        logger.debug("Queue management disabled; skipping.")
        return

    if not qbt.is_connected():
        logger.warning("qBittorrent is unreachable; skipping queue management.")
        return

    if qm_cfg.metadata_monitor_enabled:
        _delete_stuck(
            qbt=qbt,
            state="metaDL",
            filter_type="added_on",
            max_mins=qm_cfg.metadata_delete_torrent_max_mins,
            label="metadata",
            delete_data=qm_cfg.metadata_delete_torrent_data,
        )

    if qm_cfg.stalled_monitor_enabled:
        _delete_stuck(
            qbt=qbt,
            state="stalledDL",
            filter_type="last_activity",
            max_mins=qm_cfg.stalled_delete_torrent_max_mins,
            label="stalled",
            delete_data=qm_cfg.stalled_delete_torrent_data,
        )


def _delete_stuck(
    qbt: QBittorrentClient,
    state: str,
    filter_type: str,
    max_mins: int,
    label: str,
    delete_data: bool,
) -> None:
    torrent_map = qbt.list_by_category()
    if not torrent_map:
        return

    to_delete = qbt.identify_for_deletion(
        torrent_map=torrent_map,
        state=state,
        delay_max_mins=max_mins,
        filter_type=filter_type,
    )

    if not to_delete:
        logger.debug("No {} torrents to delete.", label)
        return

    logger.info("Deleting {} {} torrent(s) in state '{}'.", len(to_delete), label, state)
    qbt.delete_stalled(to_delete, state=state, delete_data=delete_data)
