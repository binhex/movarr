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

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from movarr import torrent_client_health
from movarr.file_utils import walk_library
from movarr.filters import filter_by_imdb, filter_by_index
from movarr.imdb_metadata import fetch_metadata
from movarr.imdb_search import search_for_imdb_id
from movarr.index_proxy_health import (
    check_and_notify,
    is_search_circuit_open,
    record_search_failure,
    record_search_success,
)
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
from movarr.qbittorrent import extract_movarr_tag

if TYPE_CHECKING:
    from movarr.config import Config, IndexSiteConfig, SearchCriteriaConfig
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

    If a recent search errored (circuit breaker open), the pipeline is
    skipped entirely and retries automatically after the configured
    cooldown window expires.

    Args:
        config: Application configuration.
        qbt: An already-connected ``QBittorrentClient`` instance.
        db: Open database instance.
    """
    site_cfg = config.index_site
    if not site_cfg.search:
        logger.info("No search criteria configured; skipping search.")
        return

    if not qbt.is_reachable():
        logger.warning("qBittorrent is unreachable; skipping search.")
        torrent_client_health.check_and_notify(is_reachable=False, db=db, config=config)
        return
    torrent_client_health.check_and_notify(is_reachable=True, db=db, config=config)

    proxy_name = config.index_proxy.selected.capitalize()

    if is_search_circuit_open(db, config):
        logger.warning(
            "{} search circuit is open — skipping search; will retry automatically.",
            proxy_name,
        )
        return

    indexer_client = get_indexer_client(config)

    if not indexer_client.is_reachable():
        # A single lightweight probe (no retries) is not a search failure: do
        # not open the circuit for it, or one blip blocks acquisition for the
        # whole cooldown window.  Documented contract: the circuit tracks
        # *search request* errors.
        logger.warning(
            "{} is not reachable; skipping this cycle (circuit unchanged).",
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

    indexer_client.search_failed = False
    total_raw = _run_search_for_site(session, site_cfg)
    if indexer_client.search_failed:
        record_search_failure(db)
    else:
        record_search_success(db)
    # Proxy health answers "did the proxy return anything?"; a tier that errored
    # is recorded separately by the circuit breaker.  Conflating the two raised a
    # false outage alert whenever a later tier failed after an earlier one
    # returned results.
    check_and_notify(
        has_results=total_raw > 0,
        proxy_name=proxy_name,
        db=db,
        config=config,
    )


def _queue_and_persist(result: ResultDict, session: _SearchSession) -> None:
    """Add result to qBittorrent and persist; handles add_torrent failure in-place."""
    updated = session.qbt.add_torrent(result)
    if updated is None:
        details: list[str] = result.get("result_details") or []
        details.append("Failed: add_torrent returned None; torrent not queued.")
        result["result"] = "Failed"
        result["result_details"] = details
        session.db.write(result)
        return
    result = updated
    # Persist first so the history row exists before the user is told the
    # torrent was queued.  A failed write is logged and the notification is
    # still attempted (the torrent IS in qBittorrent); post-processing will
    # skip it, so the log line is the operator's signal.
    try:
        session.db.write(result)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to persist queued torrent '{}': {}; continuing.", result.get("index_title"), exc)
    send_queued_notification(result, session.config)


def _validate_metadata_present(result: ResultDict) -> bool:
    """Return True if result has movie_title and movie_title_year."""
    if not result.get("movie_title"):
        logger.debug("No movie title from '{}'; skipping.", result.get("index_title"))
        return False
    if not result.get("movie_title_year"):
        logger.debug("No year from '{}'; skipping.", result.get("index_title"))
        return False
    return True


def _collect_supersede_matches(
    torrent_map: dict[str, Any],
    new_imdb_id: str,
    new_san: str,
    config: Config,
    new_raw_title: str = "",
) -> tuple[list[tuple[str, str, str]], bool, str]:
    """Scan torrent_map for same-IMDb-ID entries and return matches.

    Returns:
        (matches, all_new_wins, best_existing_name) where *matches* is
        ``[(hash, name, tags_str), ...]``, *all_new_wins* is ``True``
        if new beats every existing torrent, and *best_existing_name*
        is the name of the highest-scored existing torrent.
    """
    from movarr.filters import edition_token_set, supersession_quality_score
    from movarr.qbittorrent import parse_imdb_id_from_tags

    matches: list[tuple[str, str, str]] = []
    all_new_wins = True
    best_existing_name = ""
    best_existing_score = -1

    raw_new = new_raw_title or new_san
    for torrent_hash, info in torrent_map.items():
        tags_str = info.get("tags", "") or ""
        if parse_imdb_id_from_tags(tags_str) != new_imdb_id:
            continue
        candidate = _supersede_candidate(info, new_san, raw_new, edition_token_set)
        if candidate is None:
            continue
        torrent_name, existing_san = candidate

        new_score = supersession_quality_score(new_san, existing_san, config)
        existing_score = supersession_quality_score(existing_san, new_san, config)

        matches.append((torrent_hash, torrent_name, tags_str))

        if new_score <= existing_score:
            all_new_wins = False

        if existing_score > best_existing_score:
            best_existing_score = existing_score
            best_existing_name = torrent_name

    return matches, all_new_wins, best_existing_name


def _supersede_candidate(
    info: dict[str, Any],
    new_san: str,
    raw_new: str,
    edition_token_set: Any,
) -> tuple[str, str] | None:
    """Return ``(name, sanitised_name)`` when *info* may be superseded, else None.

    Vetoes completed torrents (their data may be the only copy), unparseable
    names, and different editions — a different cut is a different product.
    """
    # A completed torrent has already been post-processed (or its data is the
    # library copy itself when copy_completed is false).  Deleting it would
    # destroy finished content, and `delete_data=True` makes that irreversible.
    if _is_torrent_complete(info):
        logger.debug("Not superseding completed torrent '{}'.", info.get("name", ""))
        return None
    torrent_name = info.get("name", "")
    existing_san = sanitise(torrent_name) if torrent_name else ""
    if not existing_san:
        return None
    # Compare the RAW names: sanitise() drops trailing bracket tags, which would
    # make "[Extended]" and "Theatrical" look identical.
    if edition_token_set(torrent_name) != edition_token_set(raw_new):
        logger.debug("Not superseding '{}': different edition.", torrent_name)
        return None
    return torrent_name, existing_san


def _is_torrent_complete(info: Any) -> bool:
    """Return True when qBittorrent reports the torrent as fully downloaded.

    ``progress`` is a 0..1 float; ``amount_left`` is a byte count.  Either
    signal is enough, and a record with no usable signal is treated as
    *complete* so the deletion path stays conservative.
    """
    if not isinstance(info, dict):
        return True
    amount_left = info.get("amount_left")
    if isinstance(amount_left, (int, float)) and float(amount_left) <= 0:
        return True
    progress = info.get("progress")
    if isinstance(progress, (int, float)):
        return float(progress) >= 1.0
    # No usable signal at all: stay conservative and treat it as complete.
    return not isinstance(amount_left, (int, float))


def _apply_supersede_decision(
    result: ResultDict,
    session: _SearchSession,
    all_new_wins: bool,
    best_existing_name: str,
) -> None:
    """Mark *result* as Failed if new does not beat all existing matches.

    This function does NOT delete any torrents — it only marks the
    new result as Failed when superseded.  Deletion happens in
    ``_process_result_body`` only after the new torrent is successfully
    queued, preventing data loss if ``add_torrent`` fails.
    """
    if not all_new_wins:
        logger.info(
            "Skipping '{}' \u2014 in-queue torrent '{}' has equal or better score.",
            result.get("index_title"),
            best_existing_name,
        )
        details: list[str] = result.get("result_details") or []
        details.append(f"Failed: Superseded by higher-scored in-queue torrent '{best_existing_name}'.")
        result["result"] = "Failed"
        result["result_details"] = details
        session.db.write(result)


def _delete_superseded_matches(
    session: _SearchSession,
    matches: list[tuple[str, str, str]],
    new_imdb_id: str,
    new_title: str,
) -> None:
    """Delete all existing same-IMDb torrents (called AFTER new torrent is queued)."""
    logger.info(
        "Superseding {} in-queue torrent(s) for IMDb '{}' with '{}'.",
        len(matches),
        new_imdb_id,
        new_title,
    )
    for torrent_hash, torrent_name, tags_str in matches:
        deleted = session.qbt.delete_torrent(torrent_hash, delete_data=True, state="superseded", name=torrent_name)
        if not deleted:
            logger.warning("Could not delete superseded torrent '{}'; leaving its record untouched.", torrent_name)
            continue
        movarr_tag = extract_movarr_tag(tags_str)
        if movarr_tag:
            try:
                session.db.mark_stalled(movarr_tag)
            except Exception:
                logger.debug("Could not mark superseded torrent tag '{}' in DB.", movarr_tag)


def _supersede(result: ResultDict, session: _SearchSession) -> list[tuple[str, str, str]]:
    """Check for same-IMDb torrents and block new or return matches to delete.

    Returns a list of ``(hash, name, tags_str)`` tuples for same-IMDb
    torrents that should be deleted AFTER the new torrent is successfully
    queued.  Returns an empty list if there is nothing to delete or the
    new result is superseded.

    Marks *result* as Failed (via ``_apply_supersede_decision``) when
    an existing torrent has equal or better quality.
    """
    if not _supersede_preconditions_met(session):
        return []

    new_imdb_id = result.get("imdb_id")
    if not new_imdb_id:
        return []

    new_san = result.get("index_title_sanitised") or ""
    torrent_map = _fetch_torrent_map(session)
    if not torrent_map:
        return []

    matches, all_new_wins, best_existing_name = _collect_supersede_matches(
        torrent_map,
        new_imdb_id,
        new_san,
        session.config,
        new_raw_title=str(result.get("index_title") or ""),
    )

    if not matches:
        return []

    _apply_supersede_decision(
        result,
        session,
        all_new_wins,
        best_existing_name,
    )

    if result.get("result") == "Failed":
        return []

    return matches


def _supersede_preconditions_met(session: _SearchSession) -> bool:
    """Return True when supersession is enabled and the replacement will start."""
    if not session.config.queue_management.supersede_enabled:
        return False
    if session.config.torrent_client.qbittorrent.add_paused is True:
        # The replacement is queued paused, so deleting the existing download
        # would leave the user with no active torrent at all.
        logger.info("add_paused is enabled; skipping supersession deletion.")
        return False
    return True


def _fetch_torrent_map(session: _SearchSession) -> dict[str, Any]:
    """Return the qBittorrent category snapshot, or an empty dict on failure."""
    try:
        return session.qbt.list_by_category()
    except Exception:  # noqa: BLE001
        logger.warning("Failed to query qBittorrent for supersession check; skipping.")
        return {}


def _run_pre_filter_gates(
    result: ResultDict,
    session: _SearchSession,
    ignore_set: frozenset[str],
    indexer: str,
    index_title: str,
) -> bool | None:
    """Run ignore-set, duplicate, and metadata checks before the filter pipeline.

    Returns:
        ``None`` — continue to filter/enrich/queue pipeline.
        ``True`` — skip this result (already processed / no metadata).
        ``False`` — skip this result (ignored indexer).
    """
    tracker: str = result.get("index_tracker") or indexer
    if ignore_set and tracker.lower() in ignore_set:
        logger.debug("Skipping result from ignored indexer '{}'.", tracker)
        return False
    if session.db.is_duplicate_exact(index_title):
        logger.debug("'{}' already in DB; skipping.", index_title)
        return True
    if not _validate_metadata_present(result):
        return True
    return None


def _run_filters_and_enrich(result: ResultDict, session: _SearchSession, site_dict: dict) -> bool:
    """Run filter_by_index and _enrich_result; write to DB on filter failure.

    Returns:
        ``True`` if the result passed both filter and IMDb enrichment.
        ``False`` if it failed (result already persisted to DB).
    """
    result = filter_by_index(result, site_dict, session.config, session.library_walk)
    if result.get("result") != "Passed":
        session.db.write(result)
        return False
    return _enrich_result(result, session)


def _process_result_body(
    result: ResultDict,
    session: _SearchSession,
    site_dict: dict,
    ignore_set: frozenset[str],
    indexer: str,
) -> bool:
    """Run filter/enrich/queue for one result (body of _process_single_result)."""
    index_title = result.get("index_title", "")
    outcome = _run_pre_filter_gates(result, session, ignore_set, indexer, index_title)
    if outcome is not None:
        return outcome

    logger.opt(colors=True).info("<blue>Processing index title '{}'</blue>", index_title)
    if not _run_filters_and_enrich(result, session, site_dict):
        return True

    logger.success("'{}' passed all filters.", result.get("index_title"))
    return _finalize_and_queue(result, session)


def _finalize_and_queue(result: ResultDict, session: _SearchSession) -> bool:
    """Run supersession check, queue the torrent, and delete superseded matches.

    Always returns ``True`` so callers can use ``return _finalize_and_queue(...)``.
    """
    to_delete = _supersede(result, session)
    if result.get("result") == "Failed":
        return True
    _queue_and_persist(result, session)
    if to_delete and result.get("result") != "Failed":
        _delete_superseded_matches(
            session,
            to_delete,
            result.get("imdb_id") or "",
            result.get("index_title") or "",
        )
    return True


def _process_single_result(
    result: ResultDict,
    session: _SearchSession,
    criteria_cfg: SearchCriteriaConfig,
    site_dict: dict,
    ignore_set: frozenset[str],
    indexer: str,
) -> bool:
    """Process one raw indexer result through the full filter/enrich/queue pipeline.

    Returns True if the result was handled (not discarded by the ignore list).
    Returns False if it was skipped due to the ignore list.
    """
    result["_filter_minimum_bitrate_mb"] = criteria_cfg.minimum_bitrate_mb
    tracker = result.get("index_tracker") or indexer
    with logger.contextualize(tracker=tracker):
        return _process_result_body(result, session, site_dict, ignore_set, indexer)


def _process_criteria(
    criteria_cfg: SearchCriteriaConfig,
    category: str,
    indexer: str,
    session: _SearchSession,
) -> int:
    """Fetch and process all indexer results for one criteria tier.

    Returns:
        The number of raw results yielded by the indexer, including results the
        ignore list discards.  Proxy health asks whether the indexer returned
        anything at all, so a tier whose results are all ignored still counts as
        the proxy having answered.
    """
    site_dict = criteria_cfg.model_dump()
    raw_count = 0
    proxy = session.config.index_proxy
    if indexer == "all":
        proxy_ignore = proxy.jackett.ignore_list if proxy.selected == "jackett" else proxy.prowlarr.ignore_list
        ignore_set = frozenset(t.lower() for t in proxy_ignore)
    else:
        ignore_set = frozenset()

    for raw_result in session.indexer.search(indexer, criteria_cfg.criteria, category):
        raw_count += 1
        # One malformed result (or a transient DB lock) must never abort the
        # rest of the tier -- mirroring run_post_processing's per-item isolation.
        try:
            result = _enrich_index_metadata(raw_result)
            _process_single_result(result, session, criteria_cfg, site_dict, ignore_set, indexer)
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).error(
                "Failed to process one indexer result for '{}'; continuing.",
                raw_result.get("index_title", "<unknown>") if isinstance(raw_result, dict) else "<non-dict>",
            )

    return raw_count


def _resolve_search_category(
    site_cfg: IndexSiteConfig,
    index_site: str,
    criteria_cfg: SearchCriteriaConfig,
) -> str:
    """Return the effective search category, applying any override for *index_site*."""
    category = criteria_cfg.category
    if index_site in site_cfg.override_search:
        overrides = site_cfg.override_search[index_site]
        if "category" in overrides:
            category = overrides["category"]
    return category


def _run_search_for_site(session: _SearchSession, site_cfg: IndexSiteConfig) -> int:
    """Run all search criteria for the configured indexer site.

    Returns:
        Total number of raw indexer results across all criteria, including
        results the ignore list later discards.  This is a proxy-health signal
        ("did the indexer answer?"), not a count of queued titles.
    """
    index_site = (
        site_cfg.jackett_indexer if session.config.index_proxy.selected == "jackett" else site_cfg.prowlarr_indexer
    )

    total_raw = 0
    for criteria_cfg in site_cfg.search:
        category = _resolve_search_category(site_cfg, index_site, criteria_cfg)

        logger.info(
            "Searching indexer '{}' for '{}' (category '{}').",
            index_site,
            criteria_cfg.criteria,
            category,
        )
        total_raw += _process_criteria(
            criteria_cfg=criteria_cfg, category=category, indexer=index_site, session=session
        )

    return total_raw


def _apply_cached_metadata(result: ResultDict, cached: dict) -> None:
    """Apply cached IMDb metadata fields to *result* in place.

    ``None`` values are skipped so a pre-v13 history row cannot overwrite an
    already-populated field with ``None`` — the dual-title fields have no
    stored value there, and clearing them would silently disable
    alternate-title library matching.
    """
    for key, value in cached.items():
        if value is None:
            continue
        result[key] = value  # type: ignore[literal-required]
    result["result"] = "Passed"
    result.setdefault("result_details", [])


def _enrich_result(result: ResultDict, session: _SearchSession) -> bool:
    """Resolve IMDb ID, fetch IMDb metadata, and run IMDb filters.

    Mutates *result* in place. Writes the result to the database on any failure.

    Returns:
        True when the result passes all enrichment steps; False otherwise.
    """
    # Resolve IMDb ID if not supplied by the index.
    if not result.get("imdb_id"):
        result.update(search_for_imdb_id(result, session.config))
    if result.get("result") != "Passed" or not result.get("imdb_id"):
        session.db.write(result)
        return False
    # Try cached IMDb metadata before hitting the API.
    cached = session.db.find_imdb_metadata(result["imdb_id"])
    if cached:
        logger.info("IMDb metadata cache hit for '{}' — skipping API call.", result["imdb_id"])
        _apply_cached_metadata(result, cached)
    else:
        result.update(fetch_metadata(result, session.config))
        if result.get("result") != "Passed":
            session.db.write(result)
            return False

    if not _metadata_matches_release(result):
        if result.get("_imdb_id_source") == "indexer":
            # A feed-supplied ID that resolves to another film is unsafe to queue.
            session.db.write(result)
            return False
        # An ID we resolved ourselves was matched on the release title; keep the
        # candidate and let the IMDb filters decide (a translated or truncated
        # title must not fail here).
        logger.warning(
            "IMDb metadata title mismatch for '{}' (resolved ID); continuing.",
            result.get("index_title"),
        )

    result.update(filter_by_imdb(result, session.config, session.library_walk))
    if result.get("result") != "Passed":
        session.db.write(result)
        return False

    return True


def _metadata_matches_release(result: ResultDict) -> bool:
    """Return True when fetched IMDb metadata plausibly belongs to this release.

    An indexer-supplied ``imdbid`` is taken on trust, so a mis-tagged feed would
    otherwise queue a torrent under a different film's metadata.  A release
    passes when its title (article-stripped, word-for-word) equals the canonical,
    original or an alternate title the metadata reports — so ``"Matrix"`` still
    matches ``"The Matrix"`` while ``"The Matrix Reloaded"`` does not.
    """
    imdb_title = str(result.get("imdb_title") or "")
    imdb_year = str(result.get("imdb_year") or "")
    # ``movie_title`` is the human form ("The Matrix"); ``movie_title_compare``
    # is already squashed to a single normalised token, which would defeat the
    # word-level comparison below.
    release_title = str(result.get("movie_title") or result.get("movie_title_compare") or "")
    release_year = str(result.get("movie_title_year") or "")
    if not imdb_title or not release_title:
        return True
    if _release_matches_metadata_titles(result, release_title):
        return True
    _record_metadata_mismatch(result, imdb_title, imdb_year, release_title, release_year)
    return False


def _record_metadata_mismatch(
    result: ResultDict,
    imdb_title: str,
    imdb_year: str,
    release_title: str,
    release_year: str,
) -> None:
    """Mark *result* Failed because the metadata names a different film."""
    details: list[str] = result.get("result_details") or []
    details.append(
        f"Failed: indexer IMDb ID {result.get('imdb_id')} resolves to "
        f"'{imdb_title} ({imdb_year})', which does not match this release."
    )
    result["result"] = "Failed"
    result["result_details"] = details
    logger.warning(
        "IMDb metadata mismatch for '{}': '{} ({})' vs release '{} ({})'.",
        result.get("index_title"),
        imdb_title,
        imdb_year,
        release_title,
        release_year,
    )


def _release_matches_metadata_titles(result: ResultDict, release_title: str) -> bool:
    """Return True when the release title matches any title the metadata reports.

    The canonical title alone is not enough: a foreign film's release name is
    usually its original title, which lives in ``imdb_original_title``.
    """
    from movarr.parsing import normalise_for_compare  # noqa: PLC0415

    right = normalise_for_compare(release_title) or ""
    if not right:
        return True
    candidates = [
        str(result.get("imdb_title") or ""),
        str(result.get("imdb_original_title") or ""),
        *(str(t) for t in (result.get("imdb_alternate_titles") or [])),
    ]
    return any(_title_matches_candidate(candidate, release_title) for candidate in candidates)


def _title_matches_candidate(candidate: str, release_title: str) -> bool:
    """Return True when *candidate* names the same film as *release_title*."""
    left_words = _title_words(candidate)
    if not left_words:
        return False
    if left_words == _title_words(release_title):
        return True
    # "Face/Off" vs "Face Off" and "M*A*S*H" vs "MASH" only match once all
    # punctuation is removed.
    right_squashed = _squash_title(release_title)
    return bool(right_squashed) and _squash_title(candidate) == right_squashed


def _squash_title(title: str) -> str:
    """Return *title* reduced to lowercase alphanumerics only."""
    return re.sub(r"[^a-z0-9]+", "", title.lower())


def _title_words(title: str) -> tuple[str, ...]:
    """Return article-stripped, normalised words for *title*.

    Word-level comparison means ``"Matrix"`` matches ``"The Matrix"`` while
    ``"The Matrix Reloaded"`` does not (extra word ``reloaded``).
    """
    from movarr.parsing import normalise_for_compare  # noqa: PLC0415

    # Split on anything that is not alphanumeric: "(500) Days of Summer",
    # "Face/Off" and "M*A*S*H" must compare equal to their plain forms.
    words = [re.sub(r"[^a-z0-9]+", "", word.lower()) for word in title.split()]
    words = [normalise_for_compare(word) or word for word in words if word]
    while words and words[0] in {"the", "a", "an"}:
        words.pop(0)
    return tuple(word for word in words if word)


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
