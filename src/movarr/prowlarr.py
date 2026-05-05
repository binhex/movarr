"""Prowlarr JSON REST API client for movarr."""

from __future__ import annotations

import contextlib
import urllib.parse
from typing import TYPE_CHECKING

from loguru import logger as _logger

from movarr.downloader import HttpClient, HttpError
from movarr.utils import bytes_to_mb

if TYPE_CHECKING:
    from collections.abc import Generator

    from movarr.config import Config
    from movarr.models import ResultDict

__all__ = ["ProwlarrClient", "ProwlarrError"]


class ProwlarrError(Exception):
    """Raised when Prowlarr returns an unusable response."""


class ProwlarrClient:
    """Fetches search results from the Prowlarr JSON REST API.

    Args:
        config: Application configuration.
    """

    def __init__(self, config: Config) -> None:
        self._cfg = config.index_proxy.prowlarr
        self._http = HttpClient(
            connect_timeout=30.0,
            read_timeout=self._cfg.read_timeout,
        )

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def is_reachable(self) -> bool:
        """Return True if the Prowlarr API responds to the indexer list request."""
        url = f"http://{self._cfg.host}:{self._cfg.port}/api/v1/indexer"
        try:
            self._http.get(url, headers=self._auth_headers(), read_timeout=self._cfg.read_timeout)
            return True
        except (HttpError, Exception) as exc:
            _logger.warning("Prowlarr health check failed: {}.", exc)
            return False

    def search(
        self,
        index_site: str,
        criteria: str,
        category: str,
    ) -> Generator[ResultDict, None, None]:
        """Yield one :class:`~movarr.models.ResultDict` per search result.

        Args:
            index_site: ``"all"`` or a numeric Prowlarr indexer ID string (e.g. ``"7"``).
            criteria: Quality/keyword search string (e.g. ``"1080p"``).
            category: Torznab category IDs (e.g. ``"2000,5000"``).
        """
        _logger.info(
            "Searching Prowlarr indexer '{}' for '{}' in category '{}'.",
            index_site,
            criteria,
            category,
        )
        indexer_id = self._resolve_indexer_id(index_site)
        if indexer_id is False:
            return

        encoded_criteria = urllib.parse.quote_plus(criteria.replace(",", " "))
        cat_params = "&".join(f"categories={cat.strip()}" for cat in category.split(","))
        base_url = (
            f"http://{self._cfg.host}:{self._cfg.port}/api/v1/search?query={encoded_criteria}&type=search&{cat_params}"
        )
        url = base_url if indexer_id is None else f"{base_url}&indexerIds={indexer_id}"
        try:
            response = self._http.get(url, headers=self._auth_headers(), read_timeout=self._cfg.read_timeout)
            items = response.json()
        except HttpError as exc:
            _logger.warning("Prowlarr HTTP error for '{}': {}.", index_site, exc)
            return
        except (ValueError, TypeError) as exc:
            _logger.warning("Prowlarr JSON parse error for '{}': {}.", index_site, exc)
            return
        except Exception as exc:
            _logger.warning("Prowlarr request failed for '{}': {}.", index_site, exc)
            return

        if not isinstance(items, list):
            _logger.warning("Prowlarr returned unexpected response type for '{}'.", index_site)
            return

        for item in items:
            result = self._parse_result(item)
            if result is not None:
                yield result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        """Return the Prowlarr authentication header."""
        return {"X-Api-Key": self._cfg.api_key}

    def _resolve_indexer_id(self, index_site: str) -> int | None | bool:
        """Return the Prowlarr indexer ID for *index_site*.

        Returns:
            ``None`` for ``"all"`` (omit ``indexerIds`` from the request).
            An ``int`` for a valid numeric indexer ID string.
            ``False`` (with a warning log) for non-numeric non-all values —
            callers should skip the search in this case.
        """
        if index_site == "all":
            return None
        try:
            return int(index_site)
        except ValueError:
            _logger.warning(
                "Prowlarr indexer '{}' is not numeric and not 'all'; skipping.",
                index_site,
            )
            return False

    def _parse_result(self, item: dict) -> ResultDict | None:
        """Extract a :class:`~movarr.models.ResultDict` from one Prowlarr JSON result."""
        index_title: str | None = item.get("title")
        if not index_title:
            return None

        # Skip non-torrent results (e.g. Usenet/NZB from mixed indexer setups).
        if item.get("protocol", "torrent").lower() != "torrent":
            return None

        size_bytes = item.get("size", 0) or 0
        result: ResultDict = {
            "index_title": index_title,
            "index_tracker": item.get("indexer", ""),
            "index_pubdate": item.get("publishDate", ""),
            "index_details": item.get("infoUrl", ""),
            "index_seeders": str(item.get("seeders", "")),
            "index_peers": str(item.get("leechers", "")),
            "index_size": str(size_bytes),
            "index_size_mb": bytes_to_mb(size_bytes),
            "torrent_url": item.get("downloadUrl", "") or "",
            "magnet_url": item.get("magnetUrl", "") or "",
            "category": "",
            "result": "Passed",
            "result_details": [],
        }

        imdb_id_raw = item.get("imdbId")
        if imdb_id_raw:
            with contextlib.suppress(ValueError, TypeError):
                if isinstance(imdb_id_raw, str) and imdb_id_raw.startswith("tt"):
                    result["imdb_id"] = imdb_id_raw
                else:
                    result["imdb_id"] = f"tt{int(imdb_id_raw):07d}"

        return result
