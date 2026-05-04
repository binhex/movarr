"""Two-stage filtering pipeline for movarr.

Stage 1 — ``filter_by_index``:
  Runs before any IMDb lookup.  Fast, cheap checks on torrent index metadata.

Stage 2 — ``filter_by_imdb``:
  Runs after IMDb metadata is fetched.  Applies IMDb-based quality/rating gates
  and library dedup using the canonical IMDb title.

Override chain (stage 2):
  character → director → writer → cast → movie title = hard-pass (skip all
  rating/votes checks).
  genre = relaxed thresholds only (still checks rating/votes).
"""

from __future__ import annotations

import os
import re
from decimal import Decimal
from typing import TYPE_CHECKING, cast

from loguru import logger as _logger

from movarr.parsing import (
    bad_keyword_search,
    extract_group,
    extract_movie_title,
    extract_resolution,
    extract_year,
    is_tv_content,
    normalise_for_compare,
    quality_score,
    sanitise,
)

if TYPE_CHECKING:
    from movarr.config import Config
    from movarr.models import ResultDict

__all__ = ["filter_by_index", "filter_by_imdb"]

_VIDEO_EXTS = (".mkv", ".mp4", ".avi")
_RE_SPECIAL = re.compile(r"\b(extended|directors\scut|unrated|theatrical)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def filter_by_index(
    result: ResultDict,
    index_site: dict,
    config: Config,
    library_walk: list[tuple[str, list[str], list[str]]] | None = None,
) -> ResultDict:
    """Apply pre-IMDb index-level filters.

    Args:
        result: Pipeline dict populated by the Jackett parser.
        index_site: The active index site config dict.
        config: Application configuration.
        library_walk: Pre-built os.walk generator for library dedup (optional).

    Returns:
        Updated result dict; ``result['result']`` is ``'Passed'`` on success.
    """
    checks = [
        lambda r: _check_search_criteria(r, index_site),
        lambda r: _check_minimum_size(r, index_site),
        lambda r: _check_maximum_size(r, index_site),
        lambda r: _check_bad_keywords(r, config),
        lambda r: _check_tv_type(r),
        lambda r: _check_bad_movie_titles(r, config),
        lambda r: _check_library(r, config, library_walk),
    ]
    for check in checks:
        result = check(result)
        if result.get("result") != "Passed":
            return result
    return result


def filter_by_imdb(
    result: ResultDict,
    config: Config,
    library_walk: list[tuple[str, list[str], list[str]]] | None = None,
) -> ResultDict:
    """Apply post-IMDb metadata filters.

    Args:
        result: Pipeline dict fully populated with IMDb metadata.
        config: Application configuration.
        library_walk: Pre-built os.walk generator for library dedup (optional).

    Returns:
        Updated result dict; ``result['result']`` is ``'Passed'`` on success.
    """
    # Ordered gate chain — bail as soon as one check fails.
    checks = [
        lambda r: _check_good_title_type(r, config),
        lambda r: _check_bad_genre(r, config),
        lambda r: _check_bitrate(r, result),
        lambda r: _check_year(r, config),
        lambda r: _check_runtime(r, config),
        lambda r: _check_language_country(r, config, "language"),
        lambda r: _check_language_country(r, config, "country"),
    ]
    for check in checks:
        result = check(result)
        if result.get("result") != "Passed":
            return result

    # Override chain: hard-pass any of these → skip rating/votes (but still dedup).
    override_matched = False
    for person_type in ("character", "director", "writer", "cast"):
        if _override_person(result, config, person_type):
            _pass(result, f"Override {person_type} match; skipping rating/votes gates.")
            override_matched = True
            break
    if not override_matched and _override_movie_title(result, config):
        override_matched = True

    if not override_matched:
        # Genre override can relax rating/votes thresholds.
        override_thresholds = _override_genre(result, config)

        if not (
            _check_rating(result, config, override_thresholds) and _check_votes(result, config, override_thresholds)
        ):
            return result

    # Library dedup post-IMDb using canonical title (runs even on override hard-passes).
    if library_walk is not None:
        result = _check_library_canonical(result, config, library_walk)

    return result


# ---------------------------------------------------------------------------
# Stage 1 helpers
# ---------------------------------------------------------------------------


def _check_search_criteria(result: ResultDict, index_site: dict) -> ResultDict:
    index_title = (result.get("index_title") or "").lower()
    criteria = (index_site.get("criteria") or "").lower()
    for token in criteria.split():
        if token not in index_title:
            return _fail(result, f"Index title '{index_title}' missing criteria token '{token}'.")
    return _pass(result, f"Index title passes search criteria '{criteria}'.")


def _check_minimum_size(result: ResultDict, index_site: dict) -> ResultDict:
    """Fail if the torrent size (MB) is below the configured minimum."""
    threshold_mb = index_site.get("minimum_size_mb")
    if not threshold_mb:
        return _pass(result, "No minimum size defined; skipping.")
    return _check_size_bound(result, threshold_mb, "minimum")


def _check_maximum_size(result: ResultDict, index_site: dict) -> ResultDict:
    """Fail if the torrent size (MB) exceeds the configured maximum."""
    threshold_mb = index_site.get("maximum_size_mb")
    if not threshold_mb:
        return _pass(result, "No maximum size defined; skipping.")
    return _check_size_bound(result, threshold_mb, "maximum")


def _check_size_bound(result: ResultDict, threshold_mb: int, bound: str) -> ResultDict:
    """Compare raw index size against a threshold."""
    raw_size = result.get("index_size")
    if not raw_size:
        return _fail(result, "No index size available; assuming below threshold.")

    try:
        size_mb = int(raw_size) // 1_000_000
    except (ValueError, TypeError):
        return _fail(result, f"Could not parse index size '{raw_size}'.")
    ok = (size_mb >= threshold_mb) if bound == "minimum" else (size_mb <= threshold_mb)
    msg = f"Size {size_mb} MB {'≥' if bound == 'minimum' else '≤'} {threshold_mb} MB."
    return _pass(result, msg) if ok else _fail(result, msg)


def _check_bad_keywords(result: ResultDict, config: Config) -> ResultDict:
    bad_list = config.filters.bad_index_title_list
    if not bad_list:
        return _pass(result, "No bad index title keywords defined.")

    index_title = result.get("index_title") or ""
    for keyword in bad_list:
        if bad_keyword_search(index_title, keyword):
            return _fail(result, f"Index title contains bad keyword '{keyword}'.")
    return _pass(result, "Index title passes bad keyword check.")


def _check_tv_type(result: ResultDict) -> ResultDict:
    # is_tv_content expects a full sanitised title (including year) so that it can
    # correctly locate the year boundary before searching for TV markers.
    sanitised = result.get("index_title_sanitised") or result.get("index_title") or ""
    if is_tv_content(sanitised):
        return _fail(result, f"Index title appears to be TV content: '{sanitised}'.")
    return _pass(result, "Index title is not TV content.")


def _check_bad_movie_titles(result: ResultDict, config: Config) -> ResultDict:
    bad_list = config.filters.bad_movie_title_list
    if not bad_list:
        return _pass(result, "No bad movie titles defined.")

    title_and_year_compare = result.get("movie_title_and_year_compare") or ""
    for bad_title in bad_list:
        norm = normalise_for_compare(bad_title)
        if norm and norm in title_and_year_compare:
            return _fail(result, f"Index title matches bad movie title '{bad_title}'.")
    return _pass(result, "Index title passes bad movie title check.")


def _check_library(
    result: ResultDict, config: Config, library_walk: list[tuple[str, list[str], list[str]]] | None
) -> ResultDict:
    """Check whether a matching title/year already exists in the library."""
    library_paths = config.general.library_path_list
    if not library_paths or library_walk is None:
        return _pass(result, "No library paths; assuming movie not in library.")

    index_title = result.get("index_title") or ""
    index_resolution = result.get("index_title_resolution") or ""
    if not index_resolution:
        return _fail(result, f"Cannot determine resolution for '{index_title}'; skipping.")

    matches = _library_files_for_title(result, library_walk)
    if not matches:
        return _pass(result, "Movie not found in library.")

    return _evaluate_library_files(result, matches, index_resolution, config)


# ---------------------------------------------------------------------------
# Stage 2 helpers
# ---------------------------------------------------------------------------


def _check_good_title_type(result: ResultDict, config: Config) -> ResultDict:
    good_types = config.filters.good_imdb_title_type_list
    if not good_types:
        return _pass(result, "No IMDb title type filter defined.")

    title_type = result.get("imdb_title_type") or ""
    title_type_lower = title_type.lower()  # safe: null-guarded above
    good_lower = [t.lower() for t in good_types]

    if title_type_lower not in good_lower:
        return _fail(result, f"IMDb title type '{title_type_lower}' not in allowed types {good_lower}.")
    return _pass(result, f"IMDb title type '{title_type_lower}' is allowed.")


def _check_bad_genre(result: ResultDict, config: Config) -> ResultDict:
    bad_list = config.filters.bad_genre_list
    if not bad_list:
        return _pass(result, "No bad genre list defined.")

    genres = result.get("imdb_genres_list") or []
    genres_lower = [g.lower() for g in genres]
    bad_lower = [b.lower() for b in bad_list]

    for bad in bad_lower:
        if bad in genres_lower:
            return _fail(result, f"Genre '{bad}' is in bad genre list.")
    return _pass(result, f"Genres {genres_lower} pass bad genre check.")


def _check_bitrate(result: ResultDict, _config: object) -> ResultDict:
    # Use the index_site dict attached to the result during Jackett search.
    min_bitrate_mb = result.get("_filter_minimum_bitrate_mb")
    if not min_bitrate_mb:
        return _pass(result, "No minimum bitrate defined.")

    raw_size = result.get("index_size")
    runtime = result.get("imdb_running_time_in_minutes")
    if not raw_size:
        return _fail(result, "No index size available for bitrate check.")
    if not runtime:
        return _fail(result, "No runtime available for bitrate check.")

    try:
        size_mb = int(raw_size) // 1_000_000
        bitrate_mb = size_mb // int(runtime)
    except (ValueError, TypeError, ZeroDivisionError):
        return _fail(result, f"Could not parse size '{raw_size}' or runtime '{runtime}' for bitrate check.")
    if bitrate_mb >= int(min_bitrate_mb):
        return _pass(result, f"Bitrate {bitrate_mb} MB/min ≥ {min_bitrate_mb}.")
    return _fail(result, f"Bitrate {bitrate_mb} MB/min < {min_bitrate_mb}.")


def _check_year(result: ResultDict, config: Config) -> ResultDict:
    min_year = config.filters.minimum_year
    if not min_year:
        return _pass(result, "No minimum year defined.")

    year = result.get("movie_title_year")
    if not year:
        return _fail(result, "No movie year available for year check.")

    if int(year) >= int(min_year):
        return _pass(result, f"Year {year} ≥ {min_year}.")
    return _fail(result, f"Year {year} < {min_year}.")


def _check_runtime(result: ResultDict, config: Config) -> ResultDict:
    min_runtime = config.filters.minimum_runtime_mins
    if not min_runtime:
        return _pass(result, "No minimum runtime defined.")

    runtime = result.get("imdb_running_time_in_minutes")
    if not runtime:
        return _fail(result, "No runtime available for runtime check.")

    if int(runtime) >= int(min_runtime):
        return _pass(result, f"Runtime {runtime} min ≥ {min_runtime} min.")
    return _fail(result, f"Runtime {runtime} min < {min_runtime} min.")


def _check_language_country(result: ResultDict, config: Config, kind: str) -> ResultDict:
    good_list = getattr(config.filters, f"good_{kind}_list", []) or []
    if not good_list:
        return _pass(result, f"No good {kind} list defined.")

    imdb_list: list[str] = cast("list[str]", result.get(f"imdb_{kind}_list") or [])
    if not imdb_list:
        return _pass(result, f"No IMDb {kind} found; assuming OK.")

    imdb_lower = [x.lower() for x in imdb_list]
    good_lower = [x.lower() for x in good_list]

    for item in good_lower:
        if item in imdb_lower:
            return _pass(result, f"IMDb {kind} list {imdb_lower} matches allowed {kind} list.")
    return _fail(result, f"IMDb {kind} list {imdb_lower} not in allowed {kind} list {good_lower}.")


def _override_person(result: ResultDict, config: Config, person_type: str) -> bool:
    """Return True if any override person matches the IMDb credits list."""
    config_list = getattr(config.filters, f"override_{person_type}_list", []) or []
    if not config_list:
        return False

    credits_field = "cast" if person_type == "cast" else person_type
    imdb_list: list[str] = cast("list[str]", result.get(f"imdb_credits_{credits_field}_list") or [])
    if not imdb_list:
        return False

    imdb_lower = [x.lower() for x in imdb_list]
    for name in config_list:
        if name.lower() in imdb_lower:
            _logger.info("Override {}: '{}' matched.", person_type, name)
            return True
    return False


def _override_movie_title(result: ResultDict, config: Config) -> bool:
    """Return True (and update result) if any override movie title matches."""
    override_list = config.filters.override_movie_title_list or []
    if not override_list:
        return False

    compare = result.get("movie_title_and_year_compare") or ""
    for title in override_list:
        norm = normalise_for_compare(title)
        if norm and norm in compare:
            _pass(result, f"Override movie title '{title}' matched.")
            return True

    return False


def _override_genre(result: ResultDict, config: Config) -> dict:
    """Return a dict with relaxed thresholds if any genre overrides match."""
    genres = result.get("imdb_genres_list") or []
    override = {}
    for genre in genres:
        genre_cfg = config.filters.override_genre.get(genre.lower())
        if genre_cfg is None:
            continue
        if genre_cfg.minimum_rating:
            override["minimum_rating"] = genre_cfg.minimum_rating
        if genre_cfg.minimum_votes:
            override["minimum_votes"] = genre_cfg.minimum_votes
    return override


def _check_rating(result: ResultDict, config: Config, override: dict) -> bool:
    min_rating = override.get("minimum_rating") or config.filters.minimum_rating
    if not min_rating:
        _pass(result, "No minimum rating defined.")
        return True

    imdb_rating = result.get("imdb_rating")
    if not imdb_rating:
        _fail(result, "No IMDb rating available; assuming below threshold.")
        return False

    threshold = Decimal(str(min_rating))
    if threshold > Decimal("10.0"):
        _pass(result, f"Configured min rating {threshold} > 10.0; treating as no threshold.")
        return True

    if Decimal(str(imdb_rating)) >= threshold:
        _pass(result, f"Rating {imdb_rating} ≥ {threshold}.")
        return True

    _fail(result, f"Rating {imdb_rating} < {threshold}.")
    return False


def _check_votes(result: ResultDict, config: Config, override: dict) -> bool:
    min_votes = override.get("minimum_votes") or config.filters.minimum_votes
    if not min_votes:
        _pass(result, "No minimum votes defined.")
        return True

    imdb_votes = result.get("imdb_votes")
    if not imdb_votes:
        _fail(result, "No IMDb votes available; assuming below threshold.")
        return False

    if int(imdb_votes) >= int(min_votes):
        _pass(result, f"Votes {imdb_votes} ≥ {min_votes}.")
        return True

    _fail(result, f"Votes {imdb_votes} < {min_votes}.")
    return False


def _check_library_canonical(
    result: ResultDict, config: Config, library_walk: list[tuple[str, list[str], list[str]]]
) -> ResultDict:
    """Library dedup using the canonical IMDb title — cleaner than index title."""
    imdb_title = result.get("imdb_title") or ""
    imdb_year = str(result.get("imdb_year") or "")
    index_resolution = result.get("index_title_resolution") or ""
    if not (imdb_title and imdb_year and index_resolution):
        return _pass(result, "Insufficient IMDb fields for canonical library check.")

    canonical_compare = normalise_for_compare(imdb_title)
    if not canonical_compare:
        return _pass(result, "Cannot normalise IMDb title for canonical library check.")
    matches = _find_library_files_by_compare(canonical_compare, imdb_year, library_walk)
    if not matches:
        return _pass(result, f"'{imdb_title} ({imdb_year})' not found in library.")

    return _evaluate_library_files(result, matches, index_resolution, config)


# ---------------------------------------------------------------------------
# Library walk helpers
# ---------------------------------------------------------------------------


def _library_files_for_title(result: ResultDict, library_walk: list[tuple[str, list[str], list[str]]]) -> list[str]:
    """Return absolute paths to library video files matching the index title+year."""
    title_compare = result.get("movie_title_compare") or ""
    year = result.get("movie_title_year") or ""
    found: list[str] = []

    for root, _dirs, files in library_walk:
        for fname in files:
            if not fname.lower().endswith(_VIDEO_EXTS):
                continue
            san = sanitise(fname)
            if not san:
                continue
            lib_title = extract_movie_title(san)
            lib_year = extract_year(san)
            if not lib_title or not lib_year:
                continue
            norm = normalise_for_compare(lib_title)
            if not norm or norm not in title_compare:
                continue
            if lib_year not in year:
                continue
            found.append(os.path.join(root, fname))

    return found


def _find_library_files_by_compare(
    title_compare: str, year: str, library_walk: list[tuple[str, list[str], list[str]]]
) -> list[str]:
    """Walk the library and return video files matching *title_compare* and *year*."""
    found: list[str] = []
    for root, _dirs, files in library_walk:
        for fname in files:
            if not fname.lower().endswith(_VIDEO_EXTS):
                continue
            san = sanitise(fname)
            if not san:
                continue
            lib_title = extract_movie_title(san)
            lib_year = extract_year(san)
            if not lib_title or not lib_year:
                continue
            norm = normalise_for_compare(lib_title)
            if not norm or norm not in title_compare:
                continue
            if lib_year not in year:
                continue
            found.append(os.path.join(root, fname))
    return found


def _evaluate_library_files(
    result: ResultDict,
    library_files: list[str],
    index_resolution: str,
    config: Config,
) -> ResultDict:
    """Compare index resolution/quality against each matching library file.

    Fails immediately if the library has a higher resolution.  For equal
    resolution, computes a composite quality score (including preferred-group
    and special-edition bonuses) and fails if the library scores equal or better.
    """
    index_title = result.get("index_title") or ""
    raw_san = result.get("index_title_sanitised") or index_title
    index_san = sanitise(raw_san) or ""

    best_reason = ""

    for lib_path in library_files:
        lib_fname = os.path.basename(lib_path)
        lib_san = sanitise(lib_fname) or ""
        lib_res = extract_resolution(lib_san)

        if not lib_res:
            # Conservative: library file matched by title/year but resolution is unknown.
            # Treat as present and skip re-download to avoid duplicates.
            return _fail(
                result, f"Library file '{lib_fname}' has no parseable resolution; assuming library copy is present."
            )

        try:
            idx_res_int = int(index_resolution)
            lib_res_int = int(lib_res)
        except (ValueError, TypeError):
            continue

        if idx_res_int < lib_res_int:
            return _fail(result, f"Library has higher resolution ({lib_res}p > {index_resolution}p).")

        if idx_res_int > lib_res_int:
            best_reason = (
                f"library file '{lib_fname}' exists at lower resolution ({lib_res}p); index is {index_resolution}p"
            )
            continue

        # Same resolution — compare quality scores.
        idx_score = quality_score(index_san) + _group_bonus(index_san, lib_san, config)
        lib_score = quality_score(lib_san) + _group_bonus(lib_san, index_san, config)
        idx_score += _special_edition_bonus(index_san, lib_san)
        lib_score += _special_edition_bonus(lib_san, index_san)

        if lib_score >= idx_score:
            return _fail(
                result, f"Library file '{lib_fname}': library quality score ({lib_score}) ≥ index ({idx_score})."
            )

        best_reason = f"library file '{lib_fname}' at {index_resolution}p found (lib score: {lib_score}, index score: {idx_score})"

    if best_reason:
        return _pass(result, f"Index title '{index_title}' passes library check — {best_reason}.")
    return _pass(result, f"Index title '{index_title}' passes library check.")


def _group_bonus(candidate_san: str, other_san: str, config: Config) -> int:
    """Return +10 if *candidate* is a preferred group and *other* is not."""
    preferred_groups = config.filters.preferred_index_group_list or []
    if not preferred_groups:
        return 0
    preferred_lower = [g.lower() for g in preferred_groups]
    cand_group = (extract_group(candidate_san) or "").lower()
    other_group = (extract_group(other_san) or "").lower()
    if cand_group in preferred_lower and other_group not in preferred_lower:
        return 10
    return 0


def _special_edition_bonus(candidate_san: str, other_san: str) -> int:
    """Return +10 if *candidate* has a special edition token and *other* does not."""
    if _RE_SPECIAL.search(candidate_san) and not _RE_SPECIAL.search(other_san):
        return 10
    return 0


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------


def _pass(result: ResultDict, message: str) -> ResultDict:
    _logger.info(message)
    details: list[str] = result.get("result_details") or []
    details.append(f"Passed: {message}")
    result["result"] = "Passed"
    result["result_details"] = details
    return result


def _fail(result: ResultDict, message: str) -> ResultDict:
    _logger.warning(message)
    details: list[str] = result.get("result_details") or []
    details.append(f"Failed: {message}")
    result["result"] = "Failed"
    result["result_details"] = details
    return result
