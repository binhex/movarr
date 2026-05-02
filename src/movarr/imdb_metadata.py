"""IMDb metadata fetching using IMDbPie (primary) with OMDb fallback.

Bug fix: null-guard every field access so `.lower()` on None never crashes
(siphonator bug #1).

Bug fix: track cert source ('imdbpie' vs 'omdb') via `imdb_cert_source` so
the post-processor can correctly skip MPAA certs when routing by BBFC
(siphonator bug #8).
"""

from __future__ import annotations

import contextlib
import re
from typing import TYPE_CHECKING, Any

import pycountry
from loguru import logger as _logger

if TYPE_CHECKING:
    from movarr.config import Config
    from movarr.models import ResultDict

__all__ = ["fetch_metadata"]

_TRAILER_VI_RE = re.compile(r"vi\d+")


def fetch_metadata(result: ResultDict, config: Config) -> ResultDict:
    """Fetch detailed IMDb metadata for the ID stored in *result*.

    Tries IMDbPie first; falls back to OMDb if IMDbPie fails.

    Args:
        result: Pipeline dict with ``imdb_id`` set.
        config: Application configuration.

    Returns:
        Updated *result* with all IMDb fields populated and
        ``result == 'Passed'`` on success.
    """
    imdb_id = result.get("imdb_id")
    if not imdb_id:
        _logger.warning("fetch_metadata called without imdb_id in result dict.")
        result["result"] = "Failed"
        return result

    _logger.info("Fetching IMDb metadata for '{}'.", imdb_id)
    result = _fetch_imdbpie(result)
    if result.get("result") == "Passed":
        return result

    _logger.info("IMDbPie failed for '{}'; trying OMDb fallback.", imdb_id)
    result = _fetch_omdb(result, config)
    return result


# ---------------------------------------------------------------------------
# IMDbPie strategy
# ---------------------------------------------------------------------------


def _fetch_imdbpie(result: ResultDict) -> ResultDict:
    imdb_id = result.get("imdb_id", "")
    details: list[str] = result.get("result_details") or []

    try:
        import imdbpie

        client = imdbpie.Imdb()
    except Exception as exc:  # noqa: BLE001
        msg = f"Cannot connect to IMDb via IMDbPie: {exc}"
        _logger.warning(msg)
        details.append(f"Failed: {msg}")
        result["result"] = "Failed"
        result["result_details"] = details
        return result

    try:
        title_data = client.get_title(imdb_id)
        genres_data = client.get_title_genres(imdb_id)
        credits_data = client.get_title_credits(imdb_id)
        aux_data = client.get_title_auxiliary(imdb_id)
    except Exception as exc:  # noqa: BLE001
        msg = f"IMDbPie failed to fetch data for '{imdb_id}': {exc}"
        _logger.warning(msg)
        details.append(f"Failed: {msg}")
        result["result"] = "Failed"
        result["result_details"] = details
        with contextlib.suppress(Exception):
            client.session.close()
        return result

    directors = _credits_names(credits_data, "director")
    writers = _credits_names(credits_data, "writer")
    cast = _credits_names(credits_data, "cast")
    characters = _credits_characters(credits_data)
    languages = _get(aux_data, "spokenLanguages")
    countries = _get(aux_data, "origins")
    genres = _get(genres_data, "genres")
    cert = _extract_cert_imdbpie(aux_data)
    title = _safe_str(title_data, "base", "title")
    year = _safe_val(title_data, "base", "year")
    title_type = _safe_str(title_data, "base", "titleType")
    runtime = _safe_val(title_data, "base", "runningTimeInMinutes")
    rating = _safe_val(title_data, "ratings", "rating")
    votes = _safe_val(title_data, "ratings", "ratingCount")
    poster = _safe_str(title_data, "base", "image", "url")
    plot_summary = _safe_str(title_data, "plot", "summaries", 0, "text")
    plot_outline = _safe_str(title_data, "plot", "outline", "text")
    trailer_url = _extract_trailer(aux_data)

    result.update(
        {
            "imdb_title": title,
            "imdb_year": year,
            "imdb_poster_url": poster,
            "imdb_trailer_url": trailer_url,
            "imdb_plot_summary": plot_summary,
            "imdb_plot_outline": plot_outline,
            "imdb_rating": rating,
            "imdb_votes": votes,
            "imdb_title_type": title_type,
            "imdb_running_time_in_minutes": runtime,
            "imdb_genres_list": genres,
            "imdb_certification": cert,
            "imdb_cert_source": "imdbpie" if cert else None,
            "imdb_credits_character_list": characters,
            "imdb_credits_director_list": directors,
            "imdb_credits_writer_list": writers,
            "imdb_credits_cast_list": cast,
            "imdb_language_list": languages,
            "imdb_country_list": countries,
        }
    )

    msg = f"Identified IMDb metadata for '{imdb_id}' using IMDbPie."
    _logger.info(msg)
    details.append(f"Passed: {msg}")
    result["result"] = "Passed"
    result["result_details"] = details

    with contextlib.suppress(Exception):
        client.session.close()
    return result


# ---------------------------------------------------------------------------
# OMDb fallback strategy
# ---------------------------------------------------------------------------


def _fetch_omdb(result: ResultDict, config: Config) -> ResultDict:
    import omdb

    api_key = config.credentials.omdb.api_key
    imdb_id = result.get("imdb_id", "")
    details: list[str] = result.get("result_details") or []

    try:
        omdb_client = omdb.OMDB(api_key=api_key, timeout=30.0)
        data = omdb_client.get_movie(imdbid=imdb_id)
    except Exception as exc:  # noqa: BLE001
        msg = f"OMDb fetch failed for '{imdb_id}': {exc}"
        _logger.warning(msg)
        details.append(f"Failed: {msg}")
        result["result"] = "Failed"
        result["result_details"] = details
        return result

    def _nona(key: str) -> str | None:
        """Return None if value is absent, None, or 'N/A'."""
        val = data.get(key)
        if val in (None, "N/A", ""):
            return None
        return str(val)

    # Strip non-digits from votes and runtime.
    raw_votes = _nona("imdb_votes")
    votes = "".join(re.findall(r"\d+", raw_votes)) if raw_votes else None

    raw_runtime = _nona("runtime")
    runtime = "".join(re.findall(r"\d+", raw_runtime)) if raw_runtime else None

    # Split comma-separated credits.
    cast = [x.strip() for x in (_nona("actors") or "").split(",") if x.strip()] or None
    directors = [x.strip() for x in (_nona("director") or "").split(",") if x.strip()] or None
    writers = [x.strip() for x in (_nona("writer") or "").split(",") if x.strip()] or None
    genres = [x.strip() for x in (_nona("genre") or "").split(",") if x.strip()] or None

    # Rated — skip MPAA-specific non-values but keep the value so callers can
    # set imdb_cert_source='omdb' and use it only where appropriate.
    rated = _nona("rated")
    if rated in ("Not Rated", "Unrated"):
        rated = None

    countries = _convert_countries(_nona("country"))
    languages = _convert_languages(_nona("language"))

    result.update(
        {
            "imdb_title": _nona("title"),
            "imdb_year": _nona("year"),
            "imdb_poster_url": _nona("poster"),
            "imdb_trailer_url": None,
            "imdb_plot_summary": _nona("plot"),
            "imdb_plot_outline": None,
            "imdb_rating": _nona("imdb_rating"),
            "imdb_votes": votes,
            "imdb_title_type": _nona("type"),
            "imdb_running_time_in_minutes": runtime,
            "imdb_genres_list": genres,
            "imdb_certification": rated,
            "imdb_cert_source": "omdb" if rated else None,
            "imdb_credits_character_list": None,
            "imdb_credits_director_list": directors,
            "imdb_credits_writer_list": writers,
            "imdb_credits_cast_list": cast,
            "imdb_language_list": languages,
            "imdb_country_list": countries,
        }
    )

    msg = f"Identified IMDb metadata for '{imdb_id}' using OMDb."
    _logger.info(msg)
    details.append(f"Passed: {msg}")
    result["result"] = "Passed"
    result["result_details"] = details
    return result


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _credits_names(credits: dict, role: str) -> list[str] | None:
    """Extract up to 20 names for a credit role."""
    names: list[str] = []
    try:
        for person in credits["credits"][role]:
            name = person.get("name")
            if name and name not in names and len(names) < 20:
                names.append(name)
    except (KeyError, TypeError):
        return None
    return names or None


def _credits_characters(credits: dict) -> list[str] | None:
    """Extract up to 20 character names from the cast."""
    chars: list[str] = []
    try:
        for person in credits["credits"]["cast"]:
            for char in person.get("characters", []):
                if char and char not in chars and len(chars) < 20:
                    chars.append(char)
    except (KeyError, TypeError):
        return None
    return chars or None


def _get(data: dict, key: str) -> list | None:
    try:
        val = data[key]
        return val if val else None
    except (KeyError, TypeError):
        return None


def _safe_val(data: dict, *keys: str | int) -> Any:
    """Traverse nested dict/list by *keys*; return value or None."""
    cur = data
    for k in keys:
        try:
            cur = cur[k]
        except (KeyError, IndexError, TypeError):
            return None
    return cur


def _safe_str(data: dict, *keys: str | int) -> str | None:
    val = _safe_val(data, *keys)
    return str(val) if val is not None else None


def _extract_cert_imdbpie(aux: dict) -> str | None:
    """Try multiple paths in the auxiliary data for a UK certificate."""
    try:
        val = aux["certificate"]["certificate"]
        return str(val)
    except (KeyError, TypeError):
        pass
    try:
        certs = aux.get("certificates") or []
        uk = next(
            (c["certificate"] for c in certs if c.get("country") in ("United Kingdom", "UK")),
            None,
        )
        return uk or (certs[0].get("certificate") if certs else None)
    except (KeyError, IndexError, TypeError):
        return None


def _extract_trailer(aux: dict) -> str | None:
    try:
        trailer_id = aux["videos"]["mainTrailer"]["id"]
        match = _TRAILER_VI_RE.search(trailer_id)
        if match:
            return f"https://imdb.com/video/{match.group()}"
    except (KeyError, TypeError):
        pass
    return None


def _convert_countries(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    result = []
    for name in raw.split(","):
        name = name.strip()
        country = pycountry.countries.get(name=name)
        if country:
            result.append(country.alpha_2.lower())
    return result or None


def _convert_languages(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    result = []
    for name in raw.split(","):
        name = name.strip()
        lang = pycountry.languages.get(name=name)
        if lang:
            with contextlib.suppress(AttributeError):
                result.append(lang.alpha_2.lower())
    return result or None
