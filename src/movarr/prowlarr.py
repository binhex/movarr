"""Prowlarr JSON REST API client for movarr."""

from __future__ import annotations

import contextlib
import urllib.parse
from typing import TYPE_CHECKING

import requests
from loguru import logger as _logger

from movarr.downloader import HttpClient, HttpError
from movarr.models import build_result_dict
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
        """Return True if the Prowlarr API responds to the indexer list request.

        Uses a single-attempt direct HTTP call with a short timeout so the
        caller gets immediate feedback.  The retry logic in ``HttpClient`` is
        intentionally bypassed here — retrying a connectivity probe would
        cause silent multi-minute hangs when the host is unreachable.
        """
        url = f"http://{self._cfg.host}:{self._cfg.port}/api/v1/indexer"
        _logger.info("Checking Prowlarr connectivity at {}:{}...", self._cfg.host, self._cfg.port)
        try:
            resp = requests.get(
                url,
                headers=self._auth_headers(),
                timeout=(5.0, 10.0),  # connect / read — single attempt, no backoff
            )
            resp.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "Prowlarr is not reachable at {}:{} — {}.",
                self._cfg.host,
                self._cfg.port,
                exc,
            )
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
        items = self._fetch_search_results(url, index_site)
        if items is None:
            return

        for item in items:
            result = self._parse_result(item)
            if result is not None:
                yield result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_search_results(
        self,
        url: str,
        index_site: str,
    ) -> list | None:
        """Fetch and return the parsed JSON list from Prowlarr, or None on error."""
        try:
            response = self._http.get(url, headers=self._auth_headers(), read_timeout=self._cfg.read_timeout)
            items = response.json()
        except HttpError as exc:
            _logger.warning("Prowlarr HTTP error for '{}': {}.", index_site, exc)
            return None
        except (ValueError, TypeError) as exc:
            _logger.warning("Prowlarr JSON parse error for '{}': {}.", index_site, exc)
            return None
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Prowlarr request failed for '{}': {}.", index_site, exc)
            return None

        if not isinstance(items, list):
            _logger.warning("Prowlarr returned unexpected response type for '{}'.", index_site)
            return None
        return items

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

    @staticmethod
    def _extract_imdb_id(item: dict) -> str | None:
        """Extract and normalise an IMDb ID from a Prowlarr result item."""
        imdb_id_raw = item.get("imdbId")
        if not imdb_id_raw:
            return None
        with contextlib.suppress(ValueError, TypeError):
            if isinstance(imdb_id_raw, str) and imdb_id_raw.startswith("tt"):
                return imdb_id_raw
            return f"tt{int(imdb_id_raw):07d}"
        return None

    def _parse_result(self, item: dict) -> ResultDict | None:
        """Extract a :class:`~movarr.models.ResultDict` from one Prowlarr JSON result."""
        index_title: str | None = item.get("title")
        if not index_title:
            return None

        # Skip non-torrent results (e.g. Usenet/NZB from mixed indexer setups).
        if item.get("protocol", "torrent").lower() != "torrent":
            return None

        size_bytes = item.get("size", 0) or 0
        imdb_id = self._extract_imdb_id(item)
        result: ResultDict = build_result_dict(
            index_title=index_title,
            index_tracker=item.get("indexer", ""),
            index_pubdate=item.get("publishDate", ""),
            index_details=item.get("infoUrl", ""),
            index_seeders=str(item.get("seeders", "")),
            index_peers=str(item.get("leechers", "")),
            index_size=str(size_bytes),
            index_size_mb=bytes_to_mb(size_bytes),
            torrent_url=item.get("downloadUrl", "") or "",
            magnet_url=item.get("magnetUrl", "") or "",
            category="",
        )
        if imdb_id:
            result["imdb_id"] = imdb_id

        return result
