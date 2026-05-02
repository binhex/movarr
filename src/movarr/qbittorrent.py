"""qBittorrent WebUI client wrapper for movarr."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import qbittorrentapi
from loguru import logger as _logger

from movarr.config import Config
from movarr.models import ResultDict

__all__ = ["QBittorrentClient", "QBittorrentError"]

# Tag prefix so movarr torrents can be distinguished from all others.
_TAG_PREFIX = "movarr-"


class QBittorrentError(Exception):
    """Raised when a qBittorrent API call fails unrecoverably."""


class QBittorrentClient:
    """Wraps the qBittorrent WebUI API for movarr operations.

    The client uses lazy login — authentication is established on the
    first operation and re-used for the lifetime of the instance.

    Args:
        config: Application configuration.
    """

    def __init__(self, config: Config) -> None:
        qbt_cfg = config.torrent_client.qbittorrent
        self._category = qbt_cfg.category
        self._add_paused = qbt_cfg.add_paused
        self._client = qbittorrentapi.Client(
            host=qbt_cfg.host,
            port=qbt_cfg.port,
            username=qbt_cfg.username,
            password=qbt_cfg.password,
            VERIFY_WEBUI_CERTIFICATE=False,
        )

    # ------------------------------------------------------------------
    # Connectivity
    # ------------------------------------------------------------------

    def is_connected(self) -> bool:
        """Return True if qBittorrent reports ``connected`` status."""
        try:
            status = self._client.sync_maindata().server_state.connection_status
            connected = status == "connected"
            _logger.debug("qBittorrent connection status: {}.", status)
            return connected
        except qbittorrentapi.APIError as exc:
            _logger.warning("qBittorrent connectivity check failed: {}.", exc)
            return False

    # ------------------------------------------------------------------
    # Adding torrents
    # ------------------------------------------------------------------

    def add_torrent(self, result: ResultDict) -> ResultDict | None:
        """Add a torrent to qBittorrent and tag it with a unique identifier.

        Tries ``magnet_url`` first, falls back to ``torrent_url``.  Returns
        an updated *result* dict with ``torrent_tag`` set, or ``None`` on failure.

        Args:
            result: Pipeline result dict containing URL fields.
        """
        download_url = result.get("magnet_url") or result.get("torrent_url")
        if not download_url:
            _logger.info(
                "No magnet or torrent URL for '{}'; cannot add.",
                result.get("index_title"),
            )
            return None

        tag = f"{_TAG_PREFIX}{uuid.uuid4()}"
        try:
            self._client.torrents_add(
                urls=download_url,
                category=self._category,
                is_paused=self._add_paused,
                tags=tag,
            )
            _logger.debug(
                "Added torrent '{}' with tag '{}', category '{}', paused={}.",
                result.get("index_title"),
                tag,
                self._category,
                self._add_paused,
            )
        except qbittorrentapi.APIError as exc:
            _logger.warning("Failed to add torrent '{}': {}.", result.get("index_title"), exc)
            return None

        self._client.torrents_reannounce(torrent_hashes="all")
        result = dict(result)  # type: ignore[assignment]
        result["torrent_tag"] = tag
        return result  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Querying torrents
    # ------------------------------------------------------------------

    def list_by_category(self) -> dict[str, Any]:
        """Return a ``{hash: torrent_info}`` dict for all category torrents."""
        torrents = self._client.torrents_info(category=self._category)
        torrent_map = {t["hash"]: t for t in torrents}
        _logger.debug("Found {} torrent(s) in category '{}'.", len(torrent_map), self._category)
        return torrent_map

    def list_completed(self) -> list[dict[str, Any]]:
        """Return details for all 100%-complete movarr-tagged stopped torrents."""
        try:
            stopped = self._client.torrents_info(status_filter="stopped")
        except qbittorrentapi.APIError as exc:
            _logger.warning("Failed to list completed torrents: {}.", exc)
            return []

        results = []
        for torrent in stopped:
            if not any(t.strip().startswith(_TAG_PREFIX) for t in torrent.tags.split(",")):
                continue
            if int(torrent.amount_left) != 0:
                continue

            try:
                files = self._client.torrents_files(torrent.hash)
                props = self._client.torrents_properties(torrent.hash)
            except qbittorrentapi.APIError as exc:
                _logger.warning("Failed to fetch metadata for torrent '{}': {}; skipping.", torrent.hash, exc)
                continue
            results.append(
                {
                    "torrent_name": torrent.name,
                    "torrent_hash": torrent.hash,
                    "torrent_tag": next(
                        (t.strip() for t in torrent.tags.split(",") if t.strip().startswith(_TAG_PREFIX)),
                        "",
                    ),
                    "torrent_save_path": props.save_path,
                    "torrent_file_list": [{"file_name": f.name, "file_size": f.size} for f in files],
                }
            )
        return results

    def identify_for_deletion(
        self,
        torrent_map: dict[str, Any],
        state: str,
        delay_max_mins: int,
        filter_type: str,
    ) -> dict[str, Any]:
        """Return torrents matching *state* that exceed *delay_max_mins*.

        Args:
            torrent_map: ``{hash: torrent_info}`` from :meth:`list_by_category`.
            state: qBittorrent state string (e.g. ``"stalledDL"`` or ``"metaDL"``).
            delay_max_mins: Maximum tolerated idle minutes.
            filter_type: ``"last_activity"`` or ``"added_on"`` timestamp field.
        """
        if filter_type not in {"last_activity", "added_on"}:
            raise ValueError("filter_type must be 'last_activity' or 'added_on'.")

        now = datetime.datetime.now(tz=datetime.UTC)
        candidates: dict[str, Any] = {}

        for torrent_hash, info in torrent_map.items():
            if info.get("state") != state or "name" not in info:
                continue

            ts = info.get("last_activity") if filter_type == "last_activity" else info.get("added_on")
            if not ts:  # None or 0 (never had network activity)
                continue

            age_mins = int((now - datetime.datetime.fromtimestamp(ts, tz=datetime.UTC)).total_seconds() / 60)
            candidates[torrent_hash] = {
                "name": info["name"],
                "age_mins": age_mins,
                "state": state,
            }

        return {h: v for h, v in candidates.items() if v["age_mins"] > delay_max_mins}

    # ------------------------------------------------------------------
    # Deleting torrents
    # ------------------------------------------------------------------

    def delete_torrent(self, torrent_hash: str, delete_data: bool, state: str) -> bool:
        """Delete a single torrent by hash.

        Args:
            torrent_hash: qBittorrent info-hash.
            delete_data: Whether to remove the downloaded files too.
            state: Human-readable state string for log messages.
        """
        try:
            self._client.torrents_delete(delete_files=delete_data, torrent_hashes=torrent_hash)
        except qbittorrentapi.APIError as exc:
            _logger.warning("Failed to delete torrent '{}': {}.", torrent_hash, exc)
            return False

        _logger.info(
            "Deleted torrent '{}' (state='{}', data_deleted={}).",
            torrent_hash,
            state,
            delete_data,
        )
        return True

    def delete_stalled(self, stalled_map: dict[str, Any], state: str, delete_data: bool) -> None:
        """Delete all torrents in *stalled_map*.

        Args:
            stalled_map: Output of :meth:`identify_for_deletion`.
            state: State label for log messages.
            delete_data: Whether to remove downloaded files.
        """
        for torrent_hash, info in stalled_map.items():
            if not self.delete_torrent(torrent_hash, delete_data, state):
                _logger.warning("Could not delete '{}' ({}).", info["name"], torrent_hash)
