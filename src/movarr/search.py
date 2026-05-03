"""Top-level search pipeline for movarr.

For each configured search criteria tier:
  1. Fetch Jackett results (paginated).
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

from movarr.file_utils import walk_library
from movarr.filters import filter_by_imdb, filter_by_index
from movarr.imdb_metadata import fetch_metadata
from movarr.imdb_search import search_for_imdb_id
from movarr.jackett import JackettClient
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
    jackett: JackettClient
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

    jackett = JackettClient(config)
    if not jackett.is_reachable():
        logger.warning("Jackett is not reachable; skipping search.")
        return

    library_walk: list[tuple[str, list[str], list[str]]] | None = None
    if config.general.library_path_list:
        library_walk = list(walk_library(config.general.library_path_list))

    session = _SearchSession(
        config=config,
        jackett=jackett,
        qbt=qbt,
        db=db,
        library_walk=library_walk,
    )

    for criteria_cfg in site_cfg.search:
        # Apply any per-indexer category override.
        indexer = site_cfg.jackett_indexer
        category = criteria_cfg.category
        if indexer in site_cfg.override_search:
            overrides = site_cfg.override_search[indexer]
            if "category" in overrides:
                category = overrides["category"]

        logger.info(
            "Searching indexer '{}' for '{}' (category '{}').",
            indexer,
            criteria_cfg.criteria,
            category,
        )
        _process_criteria(criteria_cfg=criteria_cfg, category=category, indexer=indexer, session=session)


def _process_criteria(
    criteria_cfg: SearchCriteriaConfig,
    category: str,
    indexer: str,
    session: _SearchSession,
) -> None:
    """Fetch and process all Jackett results for one criteria tier."""
    site_dict = criteria_cfg.model_dump()

    for result in session.jackett.search(indexer, criteria_cfg.criteria, category):
        result = _enrich_index_metadata(result)

        index_title = result.get("index_title", "")
        if session.db.has_passed(index_title):
            logger.info("'{}' already in DB; skipping.", index_title)
            continue

        if not result.get("movie_title"):
            logger.debug("No movie title from '{}'; skipping.", result.get("index_title"))
            continue

        if not result.get("movie_title_year"):
            logger.debug("No year from '{}'; skipping.", result.get("index_title"))
            continue

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

        result = fetch_metadata(result, session.config)
        if result.get("result") != "Passed":
            session.db.write(result)
            continue

        result = filter_by_imdb(result, session.config, session.library_walk)
        if result.get("result") != "Passed":
            session.db.write(result)
            continue

        logger.success("'{}' passed all filters.", result.get("index_title"))

        send_queued_notification(result, session.config)

        updated = session.qbt.add_torrent(result)
        if updated is not None:
            result = updated
        session.db.write(result)


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

    # index_title_compare: normalised sanitised title, used by all IMDb search
    # strategies to verify that a candidate title matches this index entry.
    result["index_title_compare"] = normalise_for_compare(san)

    # Build compare/search strings used by duplicate/bad-title checks and
    # IMDb search strategies.
    if title and year:
        result["movie_title_compare"] = normalise_for_compare(title)
        result["movie_title_and_year_compare"] = normalise_for_compare(f"{title} {year}")
        result["movie_title_and_year_search"] = f"{title} {year}"

    result["result"] = "Passed"
    result["result_details"] = []

    return result
