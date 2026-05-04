"""Jackett Torznab XML feed fetcher and parser for movarr."""

from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING, Any, cast

import xmltodict
from loguru import logger as _logger

from movarr.downloader import HttpClient, HttpError
from movarr.utils import bytes_to_mb

if TYPE_CHECKING:
    from collections.abc import Generator

    from movarr.config import Config
    from movarr.models import ResultDict

__all__ = ["JackettClient", "JackettError"]

# Torznab namespace used as a dict key by xmltodict
_TORZNAB_NS = "http://torznab.com/schemas/2015/feed"


class JackettError(Exception):
    """Raised when Jackett cannot be reached or returns unusable data."""


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

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def is_reachable(self) -> bool:
        """Return True if the Jackett API responds to a basic indexer list request."""
        url = (
            f"http://{self._cfg.host}:{self._cfg.port}"
            f"/api/v2.0/indexers/all/results/torznab/api"
            f"?configured=true&apikey={self._cfg.api_key}&t=indexers&q="
        )
        try:
            self._http.get(url, read_timeout=self._cfg.read_timeout)
            return True
        except (HttpError, Exception) as exc:
            _logger.warning("Jackett health check failed: {}.", exc)
            return False

    def search(
        self,
        index_site: str,
        criteria: str,
        category: str,
    ) -> Generator[ResultDict, None, None]:
        """Yield one :class:`~movarr.models.ResultDict` per search result.

        Paginates through results starting at offset 0, stepping by
        *limit* on each page until ``max_offset`` is reached or the feed
        returns an empty page.

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
                break
            if not items:
                _logger.debug("Empty page at offset {}; stopping.", offset)
                break

            for item in items:
                result = self._parse_item(item)
                if result is not None:
                    yield result

            # Advance by the actual page size, not a hardcoded 100 (bug fix).
            offset += limit

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_page(self, url: str, index_site: str) -> list[dict[str, Any]] | None:
        """Fetch and parse one Torznab page.  Returns item list or None on error."""
        try:
            response = self._http.get(url, read_timeout=self._cfg.read_timeout)
        except HttpError as exc:
            _logger.warning("Jackett HTTP error for '{}': {}.", index_site, exc)
            return None
        except Exception as exc:
            _logger.warning("Jackett request failed for '{}': {}.", index_site, exc)
            return None

        try:
            parsed = xmltodict.parse(response.content, process_namespaces=True)
            items = parsed["rss"]["channel"]["item"]
        except (ValueError, TypeError, KeyError):
            _logger.warning("Cannot parse Torznab feed for indexer '{}'.", index_site)
            return None

        # xmltodict returns a dict (not a list) when there is exactly one item.
        if isinstance(items, dict):
            items = [items]

        return cast("list[dict[str, Any]]", items)

    def _parse_item(self, item: dict[str, Any]) -> ResultDict | None:
        """Extract a :class:`~movarr.models.ResultDict` from a single Torznab item."""
        index_title: str | None = item.get("title")
        if not index_title:
            return None

        magnet_url = self._attr(item, "magneturl")
        tracker_elem = item.get("jackettindexer")
        if isinstance(tracker_elem, dict):
            tracker = tracker_elem.get("#text", "")
        else:
            tracker = str(tracker_elem) if tracker_elem else ""
        result: ResultDict = {
            "index_title": index_title,
            "index_tracker": tracker,
            "index_pubdate": item.get("pubDate", ""),
            "index_details": item.get("comments", ""),
            "index_seeders": self._attr(item, "seeders"),
            "index_peers": self._attr(item, "peers"),
            "index_size": item.get("size", ""),
            "index_size_mb": bytes_to_mb(item.get("size", "")),
            "torrent_url": self._torrent_url(item),
            "magnet_url": magnet_url or self._enclosure_magnet(item),
            "category": self._attr(item, "category"),
            "result": "Passed",
            "result_details": [],
        }

        # Prefer an embedded IMDb ID if present.
        imdb_id = self._attr(item, "imdbid")
        if imdb_id:
            result["imdb_id"] = imdb_id

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
                return str(item.get("link", ""))  # malformed enclosure — use link
            if url.startswith("magnet:"):
                return ""  # magnet-only release; magnet_url is populated separately
            return str(url)
        return str(item.get("link", ""))

    @staticmethod
    def _enclosure_magnet(item: dict[str, Any]) -> str:
        """Return the magnet URI from ``<enclosure>`` if present, else empty string.

        Used as a fallback when ``torznab:attr name="magneturl"`` is absent but
        the enclosure element carries a magnet URI directly.
        """
        enclosure = item.get("enclosure")
        if isinstance(enclosure, dict):
            url = enclosure.get("@url", "")
            if url.startswith("magnet:"):
                return str(url)
        return ""

    @staticmethod
    def _attr(item: dict[str, Any], name: str) -> str:
        """Extract a Torznab ``torznab:attr`` value by name."""
        torznab_ns_key = f"{_TORZNAB_NS}:attr"
        attrs = item.get(torznab_ns_key, [])
        if isinstance(attrs, dict):
            attrs = [attrs]
        for attr in attrs:
            if isinstance(attr, dict) and attr.get("@name") == name:
                return str(attr.get("@value", ""))
        return ""
