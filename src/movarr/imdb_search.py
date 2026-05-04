"""IMDb ID search using multiple provider strategies.

Search order: IMDbPie → TMDb → OMDb → Google.
Each strategy updates result['imdb_id'] and result['result'] = 'Passed' on success.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime
from typing import TYPE_CHECKING

from loguru import logger as _logger

from movarr.downloader import HttpClient, HttpError
from movarr.parsing import normalise_for_compare, sanitise

if TYPE_CHECKING:
    from movarr.config import Config
    from movarr.models import ResultDict

__all__ = ["search_for_imdb_id"]

_IMDB_ID_RE = re.compile(r"tt\d+")
_OMDB_NOT_FOUND_ERROR = "Movie not found!"
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")


def search_for_imdb_id(result: ResultDict, config: Config) -> ResultDict:
    """Search for the IMDb ID using all available strategies.

    Tries each strategy in order; returns as soon as one succeeds.

    Args:
        result: Pipeline dict with ``movie_title_and_year_search``,
                ``movie_title``, ``index_title_compare``, ``movie_title_year``.
        config: Application configuration.

    Returns:
        Updated *result* dict with ``imdb_id`` set and
        ``result == 'Passed'`` on success.
    """
    for strategy in (_search_imdbpie, _search_tmdb, _search_omdb, _search_google):
        result = strategy(result, config)
        if result.get("result") == "Passed":
            return result
    _logger.warning("All IMDb ID search strategies exhausted for '{}'.", result.get("movie_title_and_year_search"))
    return result


# Strategy 1 — IMDbPie


def _search_imdbpie(result: ResultDict, _config: Config) -> ResultDict:
    search_term = result.get("movie_title_and_year_search", "")
    title_compare = result.get("index_title_compare") or ""
    year = result.get("movie_title_year") or ""

    try:
        import imdbpie

        client = imdbpie.Imdb()
        hits = client.search_for_title(search_term)
    except Exception as exc:  # noqa: BLE001
        _fail(result, f"IMDbPie search error for '{search_term}': {exc}")
        return result

    if not hits:
        _fail(result, f"IMDbPie returned no hits for '{search_term}'.")
        return result

    for hit in hits:
        imdb_title = hit.get("title")
        if not imdb_title:
            continue
        norm = normalise_for_compare(imdb_title)
        if not norm or norm not in title_compare:
            continue
        hit_year = hit.get("year")
        if hit_year is None:
            continue
        try:
            if int(hit_year) != int(year):
                continue
        except (ValueError, TypeError):
            continue

        imdb_id = hit.get("imdb_id")
        if not imdb_id:
            continue

        _pass(result, imdb_id, f"Found via IMDbPie for '{search_term}'.")
        return result

    _fail(result, f"IMDbPie: no match for '{search_term}'.")
    return result


# Strategy 2 — TMDb


def _search_tmdb(result: ResultDict, config: Config) -> ResultDict:
    api_key = config.credentials.tmdb.api_key
    if not api_key:
        _fail(result, "TMDb: no API key configured.")
        return result

    title = result.get("movie_title") or ""
    year = result.get("movie_title_year") or ""
    index_compare = result.get("index_title_compare") or ""

    encoded_title = urllib.parse.quote(title)
    url = f"https://api.themoviedb.org/3/search/movie?query={encoded_title}&year={year}&api_key={api_key}"
    http = HttpClient()
    try:
        resp = http.get(url)
        data = json.loads(resp.content)
    except (HttpError, json.JSONDecodeError, Exception) as exc:  # noqa: BLE001
        _fail(result, f"TMDb search request failed: {exc}")
        return result

    for hit in data.get("results", []):
        for field in ("title", "original_title"):
            candidate = hit.get(field, "")
            if candidate:
                norm = normalise_for_compare(candidate)
                if norm and norm in index_compare:
                    break
        else:
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

        # Second request to resolve the IMDb tt number from the TMDb ID.
        detail_url = f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={api_key}"
        try:
            resp2 = http.get(detail_url)
            detail = json.loads(resp2.content)
        except (HttpError, json.JSONDecodeError, Exception) as exc:  # noqa: BLE001
            _fail(result, f"TMDb detail request failed: {exc}")
            return result

        imdb_id = detail.get("imdb_id")
        if imdb_id:
            _pass(result, imdb_id, f"Found via TMDb for '{title}'.")
            return result

    _fail(result, f"TMDb: no match for '{title}' ({year}).")
    return result


# Strategy 3 — OMDb


def _search_omdb(result: ResultDict, config: Config) -> ResultDict:
    api_key = config.credentials.omdb.api_key
    if not api_key:
        _fail(result, "OMDb: no API key configured.")
        return result

    title = result.get("movie_title") or ""
    year = result.get("movie_title_year") or ""
    index_compare = result.get("index_title_compare") or ""

    encoded_title = urllib.parse.quote(title)
    url = f"http://www.omdbapi.com/?apikey={api_key}&t={encoded_title}&y={year}"
    http = HttpClient()
    try:
        resp = http.get(url)
        data = json.loads(resp.content)
    except (HttpError, json.JSONDecodeError, Exception) as exc:  # noqa: BLE001
        _fail(result, f"OMDb search request failed: {exc}")
        return result

    omdb_title = data.get("Title")
    omdb_norm = normalise_for_compare(omdb_title) if omdb_title else None
    if not omdb_title or not omdb_norm:
        omdb_error = data.get("Error") or ""
        if omdb_error and omdb_error != _OMDB_NOT_FOUND_ERROR:
            _fail(result, f"OMDb: API error for '{title}' ({year}): {omdb_error}")
        else:
            _fail(result, f"OMDb: no result for '{title}' ({year}).")
        return result
    if omdb_norm not in index_compare:
        _fail(result, f"OMDb: '{omdb_title}' does not match '{title}' ({year}).")
        return result

    raw_year = re.sub(r"\D+", "", data.get("Year", ""))
    try:
        if int(raw_year) != int(year):
            _fail(result, f"OMDb: year '{raw_year}' != '{year}'.")
            return result
    except (ValueError, TypeError):
        _fail(result, f"OMDb: cannot parse year '{raw_year}'.")
        return result

    imdb_id = data.get("imdbID")
    if not imdb_id:
        _fail(result, "OMDb: no imdbID in response.")
        return result

    _pass(result, imdb_id, f"Found via OMDb for '{title}'.")
    return result


# Strategy 4 — Google (last resort; may be slow / unreliable)


def _search_google(result: ResultDict, _config: Config) -> ResultDict:
    search_term = result.get("movie_title_and_year_search", "")
    index_compare = result.get("index_title_compare") or ""
    year = result.get("movie_title_year") or ""

    try:
        import googlesearch

        gen = googlesearch.search(
            f"imdb {search_term}",
            advanced=True,
            sleep_interval=5,
            num_results=10,
            timeout=10,
        )
        hits = list(gen)
    except Exception as exc:  # noqa: BLE001
        _fail(result, f"Google search error: {exc}")
        return result

    for hit in hits:
        g_title: str = hit.title or ""
        g_url: str = hit.url or ""

        # If the Google title contains a year that does NOT match the
        # expected year, skip this hit to avoid returning the wrong film.
        # Ignore year-like numbers that are literally the movie title
        # (e.g. "1917", "2012") so we don't false-negative those hits.
        if year:
            title_years = _YEAR_RE.findall(g_title)
            if title_years:
                movie_title = result.get("movie_title") or ""
                title_years = [y for y in title_years if y not in movie_title]
                if title_years and year not in title_years:
                    continue

        san = sanitise(g_title)
        if not san:
            continue
        norm = normalise_for_compare(san)
        if not norm or norm not in index_compare:
            continue

        match = _IMDB_ID_RE.search(g_url)
        if not match:
            continue

        imdb_id = match.group()
        _pass(result, imdb_id, f"Found via Google for '{search_term}'.")
        return result

    _fail(result, f"Google: no results for '{search_term}'.")
    return result


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
