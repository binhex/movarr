"""Jackett Torznab XML feed fetcher and parser for movarr."""

from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING, Any, cast

import requests
import xmltodict
from loguru import logger as _logger

from movarr.downloader import HttpClient, HttpError
from movarr.models import build_result_dict
from movarr.parsing import normalise_imdb_id
from movarr.utils import bytes_to_mb, redact_secrets

if TYPE_CHECKING:
    from collections.abc import Generator

    from movarr.config import Config
    from movarr.models import ResultDict

__all__ = ["JackettClient"]

# Torznab namespace used as a dict key by xmltodict
_TORZNAB_NS = "http://torznab.com/schemas/2015/feed"


class JackettClient:
    """Fetches and parses Torznab search feeds from Jackett.

    Args:
        config: Application configuration.
    """

    def __init__(self, config: Config) -> None:
        self._cfg = config.index_proxy.jackett
        self._http = HttpClient(
            connect_timeout=30.0,
            read_timeout=self._cfg.read_timeout,
        )
        self.search_failed = False

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def is_reachable(self) -> bool:
        """Return True if the Jackett API responds to a basic indexer list request.

        Uses a single-attempt direct HTTP call with a short timeout so the
        caller gets immediate feedback.  The retry logic in ``HttpClient`` is
        intentionally bypassed here — retrying a connectivity probe would
        cause silent multi-minute hangs when the host is unreachable.
        """
        url = (
            f"http://{self._cfg.host}:{self._cfg.port}"
            f"/api/v2.0/indexers/all/results/torznab/api"
            f"?configured=true&apikey={self._cfg.api_key}&t=indexers&q="
        )
        _logger.info("Checking Jackett connectivity at {}:{}...", self._cfg.host, self._cfg.port)
        try:
            resp = requests.get(
                url,
                timeout=(5.0, 10.0),  # connect / read — single attempt, no backoff
            )
            resp.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "Jackett is not reachable at {}:{} — {}.",
                self._cfg.host,
                self._cfg.port,
                redact_secrets(exc),
            )
            return False

    def search(
        self,
        index_site: str,
        criteria: str,
        category: str,
    ) -> Generator[ResultDict, None, None]:
        """Yield one :class:`~movarr.models.ResultDict` per search result.

        Paginates through results starting at offset 0, advancing the offset by
        the number of items each page actually returned (at least one) until
        ``max_offset`` is reached or the feed returns an empty page.

        Args:
            index_site: Jackett indexer slug (e.g. ``"rarbg"`` or ``"all"``).
            criteria: Quality/keyword search string (e.g. ``"1080p"`` or ``"2160p remux"``).
            category: Torznab category IDs (e.g. ``"2000,5000"``).
        """
        _logger.info(
            "Searching Jackett indexer '{}' for '{}' in category '{}'.",
            index_site,
            criteria,
            category,
        )
        limit = self._cfg.limit
        max_offset = self._cfg.offset
        encoded_criteria = urllib.parse.quote_plus(criteria.replace(",", " "))
        offset = 0

        while offset <= max_offset:
            url = (
                f"http://{self._cfg.host}:{self._cfg.port}"
                f"/api/v2.0/indexers/{index_site}/results/torznab/api"
                f"?apikey={self._cfg.api_key}&t=search&cat={category}"
                f"&q={encoded_criteria}&extended=1&limit={limit}&offset={offset}"
            )
            items = self._fetch_page(url, index_site)
            if items is None:
                self.search_failed = True
                break
            if not items:
                _logger.debug("Empty page at offset {}; stopping.", offset)
                break

            for item in items:
                result = self._parse_item(item)
                if result is not None:
                    yield result

            # Advance by the number of items actually returned: an indexer that
            # caps the page below our requested limit would otherwise skip
            # everything between the page end and the next offset.
            offset += max(len(items), 1)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_page(self, url: str, index_site: str) -> list[dict[str, Any]] | None:
        """Fetch and parse one Torznab page.  Returns item list or None on error."""
        try:
            response = self._http.get(url, read_timeout=self._cfg.read_timeout)
        except HttpError as exc:
            _logger.warning("Jackett HTTP error for '{}': {}.", index_site, redact_secrets(exc))
            return None
        except Exception as exc:
            _logger.warning("Jackett request failed for '{}': {}.", index_site, redact_secrets(exc))
            return None

        try:
            parsed = xmltodict.parse(response.content, process_namespaces=True)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Cannot parse Torznab feed for indexer '{}': {}.", index_site, exc)
            return None

        try:
            items = parsed["rss"]["channel"]["item"]
        except (KeyError, TypeError):
            # A valid feed with no results returns no <item> elements.
            # This is not a parse error — return an empty list.
            return []

        # xmltodict returns a dict (not a list) when there is exactly one item.
        if isinstance(items, dict):
            items = [items]

        return cast("list[dict[str, Any]]", items)

    def _parse_item(self, item: dict[str, Any]) -> ResultDict | None:
        """Extract a :class:`~movarr.models.ResultDict` from a single Torznab item."""
        if not isinstance(item, dict):
            _logger.debug("Skipping malformed Torznab item (not an object).")
            return None
        index_title: str | None = item.get("title")
        if not index_title:
            return None

        magnet_url = self._attr(item, "magneturl")
        tracker_elem = item.get("jackettindexer")
        if isinstance(tracker_elem, dict):
            tracker = tracker_elem.get("#text", "")
        else:
            tracker = str(tracker_elem) if tracker_elem else ""
        result: ResultDict = build_result_dict(
            index_title=index_title,
            index_tracker=tracker,
            index_pubdate=item.get("pubDate", ""),
            index_details=item.get("comments", ""),
            index_seeders=self._attr(item, "seeders"),
            index_peers=self._attr(item, "peers"),
            # Deliberately the plain RSS <size> element only: Jackett always
            # emits it, and a torznab size attr is not trusted (see
            # tests/unit/test_jackett.py::test_size_in_torznab_attr_only_is_ignored).
            index_size=item.get("size", ""),
            index_size_mb=bytes_to_mb(item.get("size", "")),
            torrent_url=self._torrent_url(item),
            magnet_url=magnet_url or self._enclosure_magnet(item),
            category=self._attr(item, "category"),
        )

        # Prefer an embedded IMDb ID if present, but never trust it verbatim:
        # the value reaches the notification body, the qBittorrent tag and the
        # IMDbPie request URL, so a malformed ID from an indexer feed must be
        # normalised or dropped.
        imdb_id = normalise_imdb_id(self._attr(item, "imdbid"))
        if imdb_id:
            result["imdb_id"] = imdb_id
            result["_imdb_id_source"] = "indexer"

        return result

    @staticmethod
    def _torrent_url(item: dict[str, Any]) -> str:
        """Return the torrent download URL from an item dict.

        Prefers ``<enclosure url="..."/>`` (the standard .torrent container
        element) over ``<link>`` (which many indexers use for the detail page).
        Falls back to ``<link>`` when no enclosure is present or its ``@url``
        is empty (malformed feed).  Returns empty string if the enclosure URL
        is a magnet URI — the caller should use ``magnet_url`` in that case.
        """
        enclosure = item.get("enclosure")
        if isinstance(enclosure, dict):
            url = enclosure.get("@url", "")
            if not url:
                return str(item.get("link") or "")  # malformed enclosure — use link
            if str(url).startswith("magnet:"):
                return ""  # magnet-only release; magnet_url is populated separately
            return str(url)
        return str(item.get("link") or "")

    @staticmethod
    def _enclosure_magnet(item: dict[str, Any]) -> str:
        """Return the magnet URI from ``<enclosure>`` if present, else empty string.

        Used as a fallback when ``torznab:attr name="magneturl"`` is absent but
        the enclosure element carries a magnet URI directly.
        """
        enclosure = item.get("enclosure")
        if isinstance(enclosure, dict):
            url = enclosure.get("@url") or ""
            if str(url).startswith("magnet:"):
                return str(url)
        return ""

    @staticmethod
    def _attr(item: dict[str, Any], name: str) -> str:
        """Extract a Torznab ``torznab:attr`` value by name."""
        torznab_ns_key = f"{_TORZNAB_NS}:attr"
        attrs = item.get(torznab_ns_key) or []
        if isinstance(attrs, dict):
            attrs = [attrs]
        for attr in attrs:
            if isinstance(attr, dict) and attr.get("@name") == name:
                return str(attr.get("@value", ""))
        return ""
