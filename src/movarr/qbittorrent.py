"""qBittorrent WebUI client wrapper for movarr."""

from __future__ import annotations

import datetime
import re
import uuid
from typing import TYPE_CHECKING, Any

import qbittorrentapi
from loguru import logger as _logger

if TYPE_CHECKING:
    from movarr.config import Config
    from movarr.models import ResultDict

__all__ = ["QBittorrentClient", "QBittorrentError"]

# Tag prefix so movarr torrents can be distinguished from all others.
_TAG_PREFIX = "movarr-"


def extract_movarr_tag(tags_str: str) -> str:
    """Return the first ``movarr-`` tag from *tags_str*, or empty string.

    Exported as public API for use by :mod:`movarr.queue_manager`.
    """
    return next(
        (t.strip() for t in tags_str.split(",") if t.strip().startswith(_TAG_PREFIX)),
        "",
    )


def _build_supersede_tag(imdb_id: str, score: int) -> str:
    """Build a movarr torrent tag encoding IMDb ID and base quality score.

    Format: ``movarr-<8hex>-imdb-<ttid>-score-<score>``

    Args:
        imdb_id: IMDb ID (e.g. ``"tt1234567"``).
        score: Base quality score from :func:`movarr.parsing.quality_score`.

    Returns:
        A structured tag string.
    """
    short_uuid = uuid.uuid4().hex[:8]
    return f"movarr-{short_uuid}-imdb-{imdb_id}-score-{score}"


def _parse_imdb_id_from_tags(tags_str: str) -> str | None:
    """Extract an IMDb ID from a movarr torrent tag.

    Looks for ``imdb-ttNNNNNNN`` segment. Returns ``None`` if not found.

    Args:
        tags_str: Comma-separated tag string from qBittorrent.

    Returns:
        The IMDb ID string (e.g. ``"tt1234567"``) or ``None``.
    """
    m = re.search(r"imdb-(tt\d{7,8})", tags_str)
    return m.group(1) if m else None


def _parse_score_from_tags(tags_str: str) -> int | None:
    """Extract the base quality score from a movarr torrent tag.

    Looks for ``score-NNN`` segment. Returns ``None`` if not found.

    Args:
        tags_str: Comma-separated tag string from qBittorrent.

    Returns:
        The integer score or ``None``.
    """
    m = re.search(r"score-(\d+)", tags_str)
    return int(m.group(1)) if m else None


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
        """Return True if qBittorrent has internet access (connected or firewalled).

        qBittorrent reports three states: ``"connected"`` (internet up, port
        forwarded), ``"firewalled"`` (internet up, behind NAT — the common home
        setup), and ``"disconnected"`` (no internet).  Both ``connected`` and
        ``firewalled`` mean the internet is available, so queue management
        should run for both.  Only ``disconnected`` should cause a skip —
        otherwise stalled torrents could be incorrectly deleted during an outage.
        """
        try:
            status = self._client.sync_maindata().server_state.connection_status
            _logger.debug("qBittorrent connection status: {}.", status)
            return status in {"connected", "firewalled"}
        except qbittorrentapi.APIError as exc:
            _logger.warning("qBittorrent connectivity check failed: {}.", exc)
            return False

    # ------------------------------------------------------------------
    # Adding torrents
    # ------------------------------------------------------------------

    def _reannounce_by_tag(self, tag: str, index_title: str) -> None:
        """Reannounce the torrent identified by *tag*, logging on failure."""
        try:
            infos = self._client.torrents_info(tag=tag)
            new_hash = str(infos[0].hash) if infos else None
            if new_hash:
                self._client.torrents_reannounce(torrent_hashes=new_hash)
            else:
                _logger.debug("Could not find new torrent by tag '{}' for reannounce.", tag)
        except qbittorrentapi.APIError as exc:
            _logger.warning("Reannounce failed for '{}': {}; continuing.", index_title, exc)

    @staticmethod
    def _build_tag_from_result(result: ResultDict) -> str:
        """Build a movarr tag from an acquisition pipeline result dict."""
        from movarr.parsing import quality_score

        imdb_id = result.get("imdb_id") or ""
        if imdb_id:
            sanitised = result.get("index_title_sanitised") or ""
            score = quality_score(sanitised) if sanitised else 0
            return _build_supersede_tag(imdb_id, score)
        return f"{_TAG_PREFIX}{uuid.uuid4().hex[:8]}"

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

        tag = self._build_tag_from_result(result)
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

        # Reannounce only the newly added torrent rather than every active
        # torrent in qBittorrent to avoid violating tracker re-announce intervals.
        self._reannounce_by_tag(tag, result.get("index_title") or "")

        result["torrent_tag"] = tag
        return result

    # ------------------------------------------------------------------
    # Querying torrents
    # ------------------------------------------------------------------

    def list_by_category(self) -> dict[str, Any]:
        """Return a ``{hash: torrent_info}`` dict for all category torrents."""
        torrents = self._client.torrents_info(category=self._category)
        torrent_map: dict[str, Any] = {str(t["hash"]): t for t in torrents}
        _logger.debug("Found {} torrent(s) in category '{}'.", len(torrent_map), self._category)
        return torrent_map

    @staticmethod
    def _torrent_has_movarr_tag(tags_str: str) -> bool:
        """Return True if *tags_str* contains at least one movarr- prefixed tag."""
        return bool(extract_movarr_tag(tags_str))

    @staticmethod
    def _build_torrent_entry(torrent: Any, files: Any, props: Any) -> dict[str, Any]:
        """Build a torrent info dict from qBittorrent API objects."""
        return {
            "torrent_name": torrent.name,
            "torrent_hash": torrent.hash,
            "torrent_tag": extract_movarr_tag(torrent.tags),
            "torrent_save_path": props.save_path,
            "torrent_file_list": [{"file_name": f.name, "file_size": f.size} for f in files],
        }

    def list_completed(self) -> list[dict[str, Any]]:
        """Return details for all 100%-complete movarr-tagged torrents.

        Queries all movarr-managed torrents by category rather than filtering
        by the 'stopped' state.  With ``add_paused=False`` (the default), a
        finished torrent will typically be in an active upload state
        (``uploading`` / ``stalledUP``) rather than ``stopped``, so
        restricting to ``status_filter='stopped'`` would silently miss them.
        """
        try:
            all_torrents = self._client.torrents_info(category=self._category)
        except qbittorrentapi.APIError as exc:
            _logger.warning("Failed to list completed torrents: {}.", exc)
            return []

        results = []
        for torrent in all_torrents:
            if not self._torrent_has_movarr_tag(torrent.tags):
                continue
            if int(torrent.amount_left) != 0:
                continue

            try:
                files = self._client.torrents_files(torrent.hash)
                props = self._client.torrents_properties(torrent.hash)
            except qbittorrentapi.APIError as exc:
                _logger.warning("Failed to fetch metadata for torrent '{}': {}; skipping.", torrent.hash, exc)
                continue
            results.append(self._build_torrent_entry(torrent, files, props))
        return results

    @staticmethod
    def _compute_torrent_age_mins(
        info: dict[str, Any],
        filter_type: str,
        now: datetime.datetime,
    ) -> int | None:
        """Return the staleness age in minutes for *info*, or None if undetermined.

        For ``last_activity`` filter: falls back to ``added_on`` when last_activity==0.
        For ``added_on`` filter: skips when ``added_on==0`` (not yet set).
        """
        if filter_type == "last_activity":
            ts = info.get("last_activity")
            if ts is None:
                return None
            if ts == 0:
                added_ts = info.get("added_on") or 0
                if added_ts == 0:
                    return None
                return int((now - datetime.datetime.fromtimestamp(added_ts, tz=datetime.UTC)).total_seconds() / 60)
            return int((now - datetime.datetime.fromtimestamp(ts, tz=datetime.UTC)).total_seconds() / 60)
        else:
            # added_on: 0 is "not set" in qBittorrent — skip.
            ts = info.get("added_on")
            if ts is None or ts == 0:
                return None
            return int((now - datetime.datetime.fromtimestamp(ts, tz=datetime.UTC)).total_seconds() / 60)

    @staticmethod
    def _collect_deletion_candidates(
        torrent_map: dict[str, Any],
        state: str,
        filter_type: str,
        now: datetime.datetime,
    ) -> dict[str, Any]:
        """Build the candidate map for *state* torrents with a measurable age."""
        candidates: dict[str, Any] = {}
        for torrent_hash, info in torrent_map.items():
            if info.get("state") != state or "name" not in info:
                continue
            age_mins = QBittorrentClient._compute_torrent_age_mins(info, filter_type, now)
            if age_mins is None:
                continue
            candidates[torrent_hash] = {
                "name": info["name"],
                "age_mins": age_mins,
                "state": state,
            }
        return candidates

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
        candidates = self._collect_deletion_candidates(torrent_map, state, filter_type, now)
        return {h: v for h, v in candidates.items() if v["age_mins"] > delay_max_mins}

    # ------------------------------------------------------------------
    # Deleting torrents
    # ------------------------------------------------------------------

    def delete_torrent(self, torrent_hash: str, delete_data: bool, state: str, name: str = "") -> bool:
        """Delete a single torrent by hash.

        Args:
            torrent_hash: qBittorrent info-hash.
            delete_data: Whether to remove the downloaded files too.
            state: Human-readable state string for log messages.
            name: Optional human-readable torrent title for log messages.
        """
        try:
            self._client.torrents_delete(delete_files=delete_data, torrent_hashes=torrent_hash)
        except qbittorrentapi.APIError as exc:
            _logger.warning("Failed to delete torrent '{}': {}.", torrent_hash, exc)
            return False

        if name:
            _logger.info(
                "Deleted torrent '{}' ({}) (state='{}', data_deleted={}).",
                torrent_hash,
                name,
                state,
                delete_data,
            )
        else:
            _logger.info(
                "Deleted torrent '{}' (state='{}', data_deleted={}).",
                torrent_hash,
                state,
                delete_data,
            )
        return True

    def delete_stalled(self, stalled_map: dict[str, Any], state: str, delete_data: bool) -> set[str]:
        """Delete all torrents in *stalled_map*.

        Args:
            stalled_map: Output of :meth:`identify_for_deletion`.
            state: State label for log messages.
            delete_data: Whether to remove downloaded files.

        Returns:
            Set of torrent hashes that were successfully deleted.
        """
        deleted: set[str] = set()
        for torrent_hash, info in stalled_map.items():
            if self.delete_torrent(torrent_hash, delete_data, state, name=info.get("name", "")):
                deleted.add(torrent_hash)
            else:
                _logger.warning("Could not delete '{}' ({}).", info["name"], torrent_hash)
        return deleted
