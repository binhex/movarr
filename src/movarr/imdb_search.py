"""IMDb ID search using multiple provider strategies.

Search order: IMDbPie → TMDb → OMDb → DuckDuckGo.
Each strategy updates result['imdb_id'] and result['result'] = 'Passed' on success.
"""

from __future__ import annotations

import contextlib
import json
import re
import unicodedata
import urllib.parse
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from loguru import logger as _logger

from movarr.downloader import HttpClient
from movarr.parsing import normalise_for_compare, sanitise

if TYPE_CHECKING:
    from movarr.config import Config
    from movarr.models import ResultDict

__all__ = ["search_for_imdb_id"]

_ASCII_LIMIT = 128
_DDGS: Any = None
with contextlib.suppress(Exception):
    from ddgs import DDGS

    _DDGS = DDGS

_IMDB_ID_RE = re.compile(r"tt\d+")
_OMDB_NOT_FOUND_ERROR = "Movie not found!"
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")


def search_for_imdb_id(result: ResultDict, config: Config) -> ResultDict:
    """Search for the IMDb ID using all available strategies.

    Tries each strategy in order; returns as soon as one succeeds.

    Args:
        result: Pipeline dict with ``movie_title_and_year_search``,
                ``movie_title``, ``movie_title_compare``, ``movie_title_year``.
        config: Application configuration.

    Returns:
        Updated *result* dict with ``imdb_id`` set and
        ``result == 'Passed'`` on success.
    """
    for strategy in (_search_imdbpie, _search_tmdb, _search_omdb, _search_duckduckgo):
        result = strategy(result, config)
        if result.get("result") == "Passed":
            return result
    _logger.warning("All IMDb ID search strategies exhausted for '{}'.", result.get("movie_title_and_year_search"))
    return result


# Strategy 1 — IMDbPie


def _match_imdbpie_hit(hit: dict, movie_title_compare: str, year: str) -> str | None:
    """Return the IMDb ID from *hit* if it matches *movie_title_compare* and *year*, else None."""
    imdb_title = hit.get("title")
    if not imdb_title:
        return None
    norm = normalise_for_compare(imdb_title)
    if not norm or norm != movie_title_compare:
        return None
    hit_year = hit.get("year")
    if hit_year is None:
        return None
    try:
        if int(hit_year) != int(year):
            return None
    except (ValueError, TypeError):
        return None
    imdb_id = hit.get("imdb_id")
    return imdb_id if imdb_id else None


def _search_imdbpie(result: ResultDict, _config: Config) -> ResultDict:
    search_term = result.get("movie_title_and_year_search", "")
    movie_title_compare = result.get("movie_title_compare") or ""
    year = result.get("movie_title_year") or ""

    try:
        import imdbpie  # noqa: PLC0415

        client = imdbpie.Imdb()
        hits = client.search_for_title(search_term)
    except Exception as exc:  # noqa: BLE001
        _fail(result, f"IMDbPie search error for '{search_term}': {exc}")
        return result

    if not hits:
        _fail(result, f"IMDbPie returned no hits for '{search_term}'.")
        return result

    for hit in hits:
        imdb_id = _match_imdbpie_hit(hit, movie_title_compare, year)
        if imdb_id:
            _pass(result, imdb_id, f"Found via IMDbPie for '{search_term}'.")
            return result

    _fail(result, f"IMDbPie: no match for '{search_term}'.")
    return result


# Strategy 2 — TMDb


def _tmdb_hit_title_matches(hit: dict, movie_title_compare: str) -> bool:
    """Return True if *hit* has a title or original_title that normalises to *movie_title_compare*."""
    for field in ("title", "original_title"):
        candidate = hit.get(field, "")
        if candidate:
            norm = normalise_for_compare(candidate)
            if norm and norm == movie_title_compare:
                return True
    return False


def _find_tmdb_candidate(
    results: list,
    movie_title_compare: str,
    year: str,
) -> int | None:
    """Return the TMDb id of the first result matching *movie_title_compare* and *year*, else None."""
    for hit in results:
        if not _tmdb_hit_title_matches(hit, movie_title_compare):
            continue

        release_date = hit.get("release_date", "")
        try:
            release_year = datetime.strptime(release_date, "%Y-%m-%d").year
            if int(release_year) != int(year):
                continue
        except (ValueError, TypeError):
            continue

        tmdb_id = hit.get("id")
        if tmdb_id is None:
            continue
        return cast("int", tmdb_id)
    return None


def _resolve_imdb_from_tmdb(
    tmdb_id: int,
    api_key: str,
    http: HttpClient,
    result: ResultDict,
) -> str | None:
    """Fetch the IMDb ID for *tmdb_id* via the TMDb movie detail API.

    Returns the IMDb ID string if found, None on any error (also sets result to Failed).
    """
    detail_url = f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={api_key}"
    try:
        resp2 = http.get(detail_url)
        detail = json.loads(resp2.content)
    except Exception as exc:  # noqa: BLE001
        _fail(result, f"TMDb detail request failed: {exc}")
        return None
    imdb_id = detail.get("imdb_id")
    return imdb_id if imdb_id else None


def _search_tmdb(result: ResultDict, config: Config) -> ResultDict:
    api_key = config.credentials.tmdb.api_key
    if not api_key:
        _fail(result, "TMDb: no API key configured.")
        return result

    title = result.get("movie_title") or ""
    year = result.get("movie_title_year") or ""
    movie_title_compare = result.get("movie_title_compare") or ""

    encoded_title = urllib.parse.quote(title)
    url = f"https://api.themoviedb.org/3/search/movie?query={encoded_title}&year={year}&api_key={api_key}"
    http = HttpClient()
    try:
        resp = http.get(url)
        data = json.loads(resp.content)
    except Exception as exc:  # noqa: BLE001
        _fail(result, f"TMDb search request failed: {exc}")
        return result

    tmdb_id = _find_tmdb_candidate(data.get("results", []), movie_title_compare, year)
    if tmdb_id is None:
        _apply_imdb_match(result, [], title, year)
        return result

    imdb_id = _resolve_imdb_from_tmdb(tmdb_id, api_key, http, result)
    if imdb_id is None:
        _apply_imdb_match(result, [], title, year)
        return result

    candidates = [{"imdb_id": imdb_id}]
    _apply_imdb_match(result, candidates, title, year)
    return result


# Strategy 3 — OMDb


def _validate_omdb_title(
    data: dict,
    title: str,
    year: str,
    movie_title_compare: str,
    result: ResultDict,
) -> str | None:
    """Validate the OMDb title response; return the OMDb title string if valid, else None.

    Calls _fail on *result* for invalid responses.
    """
    omdb_title = data.get("Title")
    omdb_norm = normalise_for_compare(omdb_title) if omdb_title else None
    if not omdb_title or not omdb_norm:
        omdb_error = data.get("Error") or ""
        if omdb_error and omdb_error != _OMDB_NOT_FOUND_ERROR:
            _fail(result, f"OMDb: API error for '{title}' ({year}): {omdb_error}")
        else:
            _fail(result, f"OMDb: no result for '{title}' ({year}).")
        return None
    if omdb_norm != movie_title_compare:
        _fail(result, f"OMDb: '{omdb_title}' does not match '{title}' ({year}).")
        return None
    return cast("str", omdb_title)


def _validate_omdb_year(data: dict, year: str, result: ResultDict) -> bool:
    """Validate the year in *data* matches *year*; calls _fail if invalid. Returns True if ok."""
    raw_year = re.sub(r"\D+", "", data.get("Year") or "")
    try:
        if int(raw_year) != int(year):
            _fail(result, f"OMDb: year '{raw_year}' != '{year}'.")
            return False
    except (ValueError, TypeError):
        _fail(result, f"OMDb: cannot parse year '{raw_year}'.")
        return False
    return True


def _search_omdb(result: ResultDict, config: Config) -> ResultDict:  # noqa: PLR0911
    api_key = config.credentials.omdb.api_key
    if not api_key:
        _fail(result, "OMDb: no API key configured.")
        return result

    title = result.get("movie_title") or ""
    year = result.get("movie_title_year") or ""
    movie_title_compare = result.get("movie_title_compare") or ""

    encoded_title = urllib.parse.quote(title)
    url = f"https://www.omdbapi.com/?apikey={api_key}&t={encoded_title}&y={year}"
    http = HttpClient()
    try:
        resp = http.get(url)
        data = json.loads(resp.content)
    except Exception as exc:  # noqa: BLE001
        _fail(result, f"OMDb search request failed: {exc}")
        return result

    if _validate_omdb_title(data, title, year, movie_title_compare, result) is None:
        return result

    if not _validate_omdb_year(data, year, result):
        return result

    imdb_id = data.get("imdbID")
    if not imdb_id:
        _fail(result, "OMDb: no imdbID in response.")
        return result

    _pass(result, imdb_id, f"Found via OMDb for '{title}'.")
    return result


# Strategy 4 — DuckDuckGo (last resort; more reliable than Google HTML scraping)


_TRANSLATE_MAP = str.maketrans(
    {
        "ß": "ss",
        "Ø": "O",
        "ø": "o",
        "Æ": "AE",
        "æ": "ae",
        "Œ": "OE",
        "œ": "oe",
        "Ð": "D",
        "ð": "d",
        "Þ": "Th",
        "þ": "th",
        "Ł": "L",
        "ł": "l",
        "Đ": "D",
        "đ": "d",
        "ı": "i",
        "İ": "I",
    }
)


def _strip_accents(text: str) -> str:
    """Remove accents from Unicode characters, leaving ASCII equivalents.

    Decomposes combined characters (e.g. ``é`` → ``e`` + combining accent),
    then strips remaining non-ASCII codepoints.  Also maps a handful of
    common non-decomposable Latin characters (``ß``, ``ø``, ``æ``, etc.)
    to their ASCII equivalents so they are not silently dropped.
    """
    decomposed = unicodedata.normalize("NFD", text)
    decomposed = decomposed.translate(_TRANSLATE_MAP)
    return "".join(c for c in decomposed if ord(c) < _ASCII_LIMIT)


def _should_skip_ddg_hit_by_year(hit: dict, year: str, movie_title: str) -> bool:
    """Return True if *hit* should be skipped due to a mismatched year in its title."""
    title_years = _YEAR_RE.findall(hit.get("title") or "")
    if not title_years:
        return False
    title_years = [y for y in title_years if y not in movie_title]
    return bool(title_years) and year not in title_years


def _extract_imdb_id_from_ddg_hit(
    hit: dict,
    movie_title_compare: str,
    movie_title_and_year_compare: str,
) -> str | None:
    """Return the IMDb ID from *hit* if its title matches either compare form, else None."""
    d_title: str = hit.get("title") or ""
    d_url: str = hit.get("href") or ""
    for d_title_variant in (d_title, _strip_accents(d_title)):
        san = sanitise(d_title_variant)
        if not san:
            continue
        norm = normalise_for_compare(san)
        if not norm or norm not in (movie_title_compare, movie_title_and_year_compare):
            continue
        match = _IMDB_ID_RE.search(d_url)
        if not match:
            continue
        return match.group()
    return None


def _get_str(result: ResultDict, key: str) -> str:
    """Return ``result[key]`` as a non-None string, defaulting to ``''``."""
    v: Any = result.get(key)
    return v if v else ""


def _search_duckduckgo(result: ResultDict, _config: Config) -> ResultDict:
    """Search IMDb ID via DuckDuckGo web search.

    Uses ``duckduckgo-search`` (a maintained, scraper-friendly alternative
    to the fragile Google HTML scraper).  Returns the first IMDb URL whose
    normalised title matches ``movie_title_compare`` or ``movie_title_and_year_compare``.
    """
    search_term = result.get("movie_title_and_year_search", "")
    movie_title_compare = _get_str(result, "movie_title_compare")
    movie_title_and_year_compare = _get_str(result, "movie_title_and_year_compare")
    year = _get_str(result, "movie_title_year")
    movie_title = _get_str(result, "movie_title")

    try:
        if _DDGS is None:
            raise ImportError("ddgs not available")
        with _DDGS() as ddgs:
            hits = list(ddgs.text(f"imdb {search_term}", max_results=10))
    except Exception as exc:  # noqa: BLE001
        _fail(result, f"DuckDuckGo error: {exc}")
        return result

    candidates: list[dict] = []
    for hit in hits:
        if year and _should_skip_ddg_hit_by_year(hit, year, movie_title):
            continue
        imdb_id = _extract_imdb_id_from_ddg_hit(hit, movie_title_compare, movie_title_and_year_compare)
        if imdb_id:
            candidates.append({"imdb_id": imdb_id})
            break

    _apply_imdb_match(result, candidates, search_term, year if year else None)
    return result


def _apply_imdb_match(
    result: ResultDict,
    candidates: list[dict],
    title: str,
    year: str | None,
) -> bool:
    """Match candidates against title/year, mutate result on success. Return True if matched."""
    for candidate in candidates:
        imdb_id = candidate.get("imdb_id")
        if imdb_id:
            _pass(result, imdb_id, f"Found for '{title}'.")
            return True
    _fail(result, f"No IMDb match for '{title}' ({year}).")
    return False


# Helpers


def _fail(result: ResultDict, message: str) -> None:
    _logger.warning(message)
    details: list[str] = result.get("result_details") or []
    details.append(f"Failed: {message}")
    result["result"] = "Failed"
    result["result_details"] = details


def _pass(result: ResultDict, imdb_id: str, message: str) -> None:
    _logger.info("IMDb ID '{}' — {}", imdb_id, message)
    details: list[str] = result.get("result_details") or []
    details.append(f"Passed: {message}")
    result["imdb_id"] = imdb_id
    result["result"] = "Passed"
    result["result_details"] = details
