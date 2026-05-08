"""Top-level search pipeline for movarr.

For each configured search criteria tier:
  1. Fetch indexer proxy results (paginated).
  2. Enrich each result with parsed index metadata.
  3. Run index-level filters.
  4. Resolve IMDb ID (if not supplied by the index).
  5. Fetch IMDb metadata.
  6. Run IMDb-level filters.
  7. On full pass: send notification + add to qBittorrent.
  8. Persist every result to the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from movarr import torrent_client_health
from movarr.file_utils import walk_library
from movarr.filters import filter_by_imdb, filter_by_index
from movarr.imdb_metadata import fetch_metadata
from movarr.imdb_search import search_for_imdb_id
from movarr.index_proxy_health import check_and_notify
from movarr.indexer import IndexProxyProtocol, get_indexer_client
from movarr.notifications import send_queued_notification
from movarr.parsing import (
    extract_after_year,
    extract_movie_title,
    extract_resolution,
    extract_year,
    normalise_for_compare,
    sanitise,
)

if TYPE_CHECKING:
    from movarr.config import Config, SearchCriteriaConfig
    from movarr.database import Database
    from movarr.models import ResultDict
    from movarr.qbittorrent import QBittorrentClient

__all__ = ["run_search"]


@dataclass(frozen=True)
class _SearchSession:
    """Immutable session-level dependencies shared across all criteria tiers."""

    config: Config
    indexer: IndexProxyProtocol
    qbt: QBittorrentClient
    db: Database
    library_walk: list | None


def run_search(config: Config, qbt: QBittorrentClient, db: Database) -> None:
    """Run the full search pipeline for all configured criteria tiers.

    Args:
        config: Application configuration.
        qbt: An already-connected ``QBittorrentClient`` instance.
        db: Open database instance.
    """
    site_cfg = config.index_site
    if not site_cfg.search:
        logger.info("No search criteria configured; skipping search.")
        return

    if not qbt.is_connected():
        logger.warning("qBittorrent is unreachable; skipping search.")
        torrent_client_health.check_and_notify(is_reachable=False, db=db, config=config)
        return
    torrent_client_health.check_and_notify(is_reachable=True, db=db, config=config)

    proxy_name = config.index_proxy.selected.capitalize()

    indexer_client = get_indexer_client(config)
    if not indexer_client.is_reachable():
        logger.warning(
            "{} is not reachable; skipping search.",
            proxy_name,
        )
        check_and_notify(has_results=False, proxy_name=proxy_name, db=db, config=config)
        return

    library_walk: list[tuple[str, list[str], list[str]]] | None = None
    if config.general.library_path_list:
        library_walk = list(walk_library(config.general.library_path_list))

    session = _SearchSession(
        config=config,
        indexer=indexer_client,
        qbt=qbt,
        db=db,
        library_walk=library_walk,
    )

    total_raw = 0
    for criteria_cfg in site_cfg.search:
        # Select the indexer slug/id based on the configured proxy.
        index_site = site_cfg.jackett_indexer if config.index_proxy.selected == "jackett" else site_cfg.prowlarr_indexer
        category = criteria_cfg.category
        if index_site in site_cfg.override_search:
            overrides = site_cfg.override_search[index_site]
            if "category" in overrides:
                category = overrides["category"]

        logger.info(
            "Searching indexer '{}' for '{}' (category '{}').",
            index_site,
            criteria_cfg.criteria,
            category,
        )
        total_raw += _process_criteria(
            criteria_cfg=criteria_cfg, category=category, indexer=index_site, session=session
        )

    check_and_notify(has_results=total_raw > 0, proxy_name=proxy_name, db=db, config=config)


def _process_criteria(  # noqa: PLR0912
    criteria_cfg: SearchCriteriaConfig,
    category: str,
    indexer: str,
    session: _SearchSession,
) -> int:
    """Fetch and process all indexer results for one criteria tier.

    Returns:
        The number of raw results yielded by the indexer (before any filtering).
    """
    site_dict = criteria_cfg.model_dump()
    raw_count = 0

    for raw_result in session.indexer.search(indexer, criteria_cfg.criteria, category):
        raw_count += 1
        result = _enrich_index_metadata(raw_result)
        result["_filter_minimum_bitrate_mb"] = criteria_cfg.minimum_bitrate_mb

        index_title = result.get("index_title", "")
        tracker = result.get("index_tracker") or indexer
        with logger.contextualize(tracker=tracker):
            ignore_list = session.config.index_site.ignore_list
            if ignore_list and tracker in ignore_list:
                logger.debug("Skipping result from ignored indexer '{}'.", tracker)
                continue
            if session.db.is_duplicate_exact(index_title):
                logger.debug("'{}' already in DB; skipping.", index_title)
                continue

            if not result.get("movie_title"):
                logger.debug("No movie title from '{}'; skipping.", result.get("index_title"))
                continue

            if not result.get("movie_title_year"):
                logger.debug("No year from '{}'; skipping.", result.get("index_title"))
                continue

            logger.opt(colors=True).info("<blue>Processing index title '{}'</blue>", index_title)

            result = filter_by_index(result, site_dict, session.config, session.library_walk)
            if result.get("result") != "Passed":
                session.db.write(result)
                continue

            # Resolve IMDb ID if not supplied by the index.
            if not result.get("imdb_id"):
                result = search_for_imdb_id(result, session.config)
            if result.get("result") != "Passed" or not result.get("imdb_id"):
                session.db.write(result)
                continue

            # Try cached IMDb metadata before hitting the API.
            cached = session.db.find_imdb_metadata(result["imdb_id"])
            if cached:
                logger.info("IMDb metadata cache hit for '{}' — skipping API call.", result["imdb_id"])
                for key, value in cached.items():
                    result[key] = value  # type: ignore[literal-required]
                result["result"] = "Passed"
                result.setdefault("result_details", [])
            else:
                result = fetch_metadata(result, session.config)
                if result.get("result") != "Passed":
                    session.db.write(result)
                    continue

            result = filter_by_imdb(result, session.config, session.library_walk)
            if result.get("result") != "Passed":
                session.db.write(result)
                continue

            logger.success("'{}' passed all filters.", result.get("index_title"))

            updated = session.qbt.add_torrent(result)
            if updated is None:
                details: list[str] = result.get("result_details") or []
                details.append("Failed: add_torrent returned None; torrent not queued.")
                result["result"] = "Failed"
                result["result_details"] = details
                session.db.write(result)
                continue
            result = updated
            send_queued_notification(result, session.config)
            session.db.write(result)

    return raw_count


def _enrich_index_metadata(result: ResultDict) -> ResultDict:
    """Extract and store parsed title metadata into the result dict."""
    raw = result.get("index_title") or ""
    san = sanitise(raw)
    if not san:
        return result

    result["index_title_sanitised"] = san

    title = extract_movie_title(san)
    year = extract_year(san)
    after_year = extract_after_year(san)
    resolution = extract_resolution(san)

    result["movie_title"] = title
    result["movie_title_year"] = year
    result["index_title_after_year_to_end"] = after_year
    result["index_title_resolution"] = resolution

    # Build compare/search strings used by duplicate/bad-title checks and
    # IMDb search strategies.
    if title and year:
        result["movie_title_compare"] = normalise_for_compare(title)
        result["movie_title_and_year_compare"] = normalise_for_compare(f"{title} {year}")
        result["movie_title_and_year_search"] = f"{title} {year}"

    result["result"] = "Passed"
    result["result_details"] = []

    return result
