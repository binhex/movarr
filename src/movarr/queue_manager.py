"""Queue management task for movarr.

Deletes torrents that have been stuck in metaDL or stalledDL states longer than
the configured maximum wait time.  This prevents the queue from filling with
torrents that will never complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from movarr import torrent_client_health

if TYPE_CHECKING:
    from movarr.config import Config
    from movarr.database import Database
    from movarr.qbittorrent import QBittorrentClient

__all__ = ["run_queue_management"]

_TAG_PREFIX = "movarr-"


@dataclass(frozen=True, slots=True)
class _StuckConfig:
    """Parameters that describe one class of stuck torrents to delete."""

    state: str
    filter_type: str
    max_mins: int
    label: str
    delete_data: bool


def _filter_to_movarr_tagged(to_delete: dict[str, Any], torrent_map: dict[str, Any]) -> dict[str, Any]:
    """Return only candidates whose torrent has a movarr- tag in *torrent_map*."""
    return {
        h: info
        for h, info in to_delete.items()
        if any(t.strip().startswith(_TAG_PREFIX) for t in (torrent_map.get(h, {}).get("tags", "") or "").split(","))
    }


def _find_movarr_tag(torrent_map: dict[str, Any], torrent_hash: str) -> str | None:
    """Return the first movarr- tag found in *torrent_map* for *torrent_hash*, or None."""
    torrent_info = torrent_map.get(torrent_hash, {})
    raw_tags: str = torrent_info.get("tags", "") or ""
    return next(
        (t.strip() for t in raw_tags.split(",") if t.strip().startswith(_TAG_PREFIX)),
        None,
    )


def run_queue_management(config: Config, qbt: QBittorrentClient, db: Database) -> None:
    """Check for stuck torrents and delete them.

    Args:
        config: Application configuration.
        qbt: An already-connected ``QBittorrentClient`` instance.
        db: History database (used to mark deleted torrents as stalled).
    """
    qm_cfg = config.queue_management
    if not qm_cfg.queue_management_enabled:
        logger.debug("Queue management disabled; skipping.")
        return

    if not qbt.is_connected():
        logger.warning("qBittorrent is unreachable; skipping queue management.")
        torrent_client_health.check_and_notify(is_reachable=False, db=db, config=config)
        return
    torrent_client_health.check_and_notify(is_reachable=True, db=db, config=config)

    if qm_cfg.metadata_monitor_enabled:
        _delete_stuck(
            qbt,
            db,
            _StuckConfig(
                state="metaDL",
                filter_type="added_on",
                max_mins=qm_cfg.metadata_delete_torrent_max_mins,
                label="metadata",
                delete_data=qm_cfg.metadata_delete_torrent_data,
            ),
        )

    if qm_cfg.stalled_monitor_enabled:
        _delete_stuck(
            qbt,
            db,
            _StuckConfig(
                state="stalledDL",
                filter_type="last_activity",
                max_mins=qm_cfg.stalled_delete_torrent_max_mins,
                label="stalled",
                delete_data=qm_cfg.stalled_delete_torrent_data,
            ),
        )


def _delete_stuck(qbt: QBittorrentClient, db: Database, cfg: _StuckConfig) -> None:
    torrent_map = qbt.list_by_category()
    if not torrent_map:
        return

    to_delete = qbt.identify_for_deletion(
        torrent_map=torrent_map,
        state=cfg.state,
        delay_max_mins=cfg.max_mins,
        filter_type=cfg.filter_type,
    )

    if not to_delete:
        logger.debug("No {} torrents to delete.", cfg.label)
        return

    to_delete = _filter_to_movarr_tagged(to_delete, torrent_map)
    if not to_delete:
        logger.debug("No {} torrents with movarr tag to delete.", cfg.label)
        return

    logger.info("Deleting {} {} torrent(s) in state '{}'.", len(to_delete), cfg.label, cfg.state)
    deleted_hashes = qbt.delete_stalled(to_delete, state=cfg.state, delete_data=cfg.delete_data)

    for torrent_hash in deleted_hashes:
        tag = _find_movarr_tag(torrent_map, torrent_hash)
        if tag:
            db.mark_stalled(tag)
            logger.debug("Marked torrent '{}' (tag='{}') as Stalled in DB.", torrent_hash, tag)
        else:
            logger.debug("No movarr tag on deleted torrent '{}'; skipping DB update.", torrent_hash)
