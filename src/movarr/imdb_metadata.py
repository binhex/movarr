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
import types
from datetime import date
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

import pycountry
from loguru import logger as _logger

from movarr.notifications import _strip_poster_resolution

if TYPE_CHECKING:
    from movarr.config import Config
    from movarr.models import ResultDict

__all__ = ["fetch_metadata"]

_MAX_CREDITS = 20
_TRAILER_VI_RE = re.compile(r"vi\d+")
_re_imdb_id = re.compile(r"tt\d{7,}")


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


# IMDbPie strategy


def _resolve_imdbpie_redirect(client: Any, imdb_id: str) -> str:
    """Return the canonical IMDb ID, following any API-level redirect.

    IMDbPie calls the same endpoint internally but only returns True/False.
    We replicate its logic to extract the canonical ``tt`` ID so we can retry
    with it instead of failing immediately.
    """
    try:
        from imdbpie.constants import BASE_URI  # noqa: PLC0415

        path = "/template/imdb-ios-writable/title-auxiliary-v31.jstl/render"
        resource = client._get(
            url=urljoin(BASE_URI, path),
            params={
                "tconst": imdb_id,
                "today": date.today().strftime("%Y-%m-%d"),
                "region": client.region,
            },
        )
        if resource:
            returned_id = resource.get("id", "")
            if returned_id:
                match = _re_imdb_id.search(returned_id)
                if match:
                    return match.group()
    except Exception as e:  # noqa: BLE001
        _logger.debug("IMDb redirect lookup failed for %s: %s", imdb_id, e, exc_info=True)
    return imdb_id


def _patch_imdbpie_redirect_check(client: Any) -> None:
    """Fix IMDbPie's broken 8-digit IMDb ID handling.

    IMDbPie's ``is_redirection_title`` uses ``re.search(r'tt\\d{7}', ...)``
    which only matches exactly 7 digits.  IDs with 8+ digits (e.g. tt31193180)
    are therefore incorrectly classified as redirects and raise LookupError
    before any data is fetched.  We replace the method with a corrected version
    that uses ``tt\\d{7,}`` (7 or more digits).
    """

    def _is_redirection_title(self: Any, imdb_id: str) -> bool:  # noqa: ANN001
        self.validate_imdb_id(imdb_id)
        try:
            if imdb_id.startswith("nm"):
                resource = self._get_resource(f"/name/{imdb_id}/fulldetails")
                returned_id = resource["base"].get("id", "")
                if returned_id:
                    match = re.search(r"nm\d{7,}", returned_id)
                    if match:
                        return match.group() != imdb_id
            else:
                from imdbpie.constants import BASE_URI  # noqa: PLC0415

                path = "/template/imdb-ios-writable/title-auxiliary-v31.jstl/render"
                resource = self._get(
                    url=urljoin(BASE_URI, path),
                    params={
                        "tconst": imdb_id,
                        "today": date.today().strftime("%Y-%m-%d"),
                        "region": self.region,
                    },
                )
                returned_id = (resource or {}).get("id", "")
                if returned_id:
                    match = re.search(r"tt\d{7,}", returned_id)
                    if match:
                        return match.group() != imdb_id
            return False
        except (LookupError, ImportError):
            return False

    client.is_redirection_title = types.MethodType(_is_redirection_title, client)


def _build_imdbpie_payload(client: Any, imdb_id: str) -> dict[str, Any] | None:
    """Fetch all IMDbPie data for *imdb_id* and return as a flat dict.

    Returns ``None`` if any API call fails (caller logs and handles).
    """
    try:
        title_data = client.get_title(imdb_id)
        genres_data = client.get_title_genres(imdb_id)
        credits_data = client.get_title_credits(imdb_id)
        aux_data = client.get_title_auxiliary(imdb_id)
    except Exception:  # noqa: BLE001
        return None
    return {
        "title": _safe_str(title_data, "base", "title"),
        "year": _safe_val(title_data, "base", "year"),
        "title_type": _safe_str(title_data, "base", "titleType"),
        "runtime": _safe_val(title_data, "base", "runningTimeInMinutes"),
        "rating": _safe_val(title_data, "ratings", "rating"),
        "votes": _safe_val(title_data, "ratings", "ratingCount"),
        "poster": _safe_str(title_data, "base", "image", "url"),
        "plot_summary": _safe_str(title_data, "plot", "summaries", 0, "text"),
        "plot_outline": _safe_str(title_data, "plot", "outline", "text"),
        "trailer_url": _extract_trailer(aux_data),
        "genres": _extract_list_or_none(genres_data, "genres"),
        "cert": _extract_cert_imdbpie(aux_data),
        # Convert spoken language names to ISO 639-1 codes so they match the
        # allow-list format documented in the README (e.g. ["en"], not ["English"]).
        # _convert_languages and _convert_countries accept comma-separated strings.
        "languages": _convert_languages(", ".join(_extract_list_or_none(aux_data, "spokenLanguages") or [])) or None,
        "countries": _convert_countries(", ".join(_extract_list_or_none(aux_data, "origins") or [])) or None,
        "directors": _credits_names(credits_data, "director"),
        "writers": _credits_names(credits_data, "writer"),
        "cast": _credits_names(credits_data, "cast"),
        "characters": _credits_characters(credits_data),
    }


def _fetch_imdbpie(result: ResultDict) -> ResultDict:
    imdb_id = result.get("imdb_id", "")
    details: list[str] = result.get("result_details") or []

    try:
        import imdbpie  # noqa: PLC0415

        client = imdbpie.Imdb()
        _patch_imdbpie_redirect_check(client)
    except Exception as exc:  # noqa: BLE001
        msg = f"Cannot connect to IMDb via IMDbPie: {exc}"
        _logger.warning(msg)
        details.append(f"Failed: {msg}")
        result["result"] = "Failed"
        result["result_details"] = details
        return result

    # Resolve redirect IDs up-front so get_title() doesn't immediately fail.
    # IMDbPie raises LookupError for redirect IDs; the same API endpoint
    # returns the canonical ID in the 'id' field of the response resource.
    canonical_id = _resolve_imdbpie_redirect(client, imdb_id)
    if canonical_id != imdb_id:
        _logger.info("Resolved IMDb redirect: '{}' → '{}'.", imdb_id, canonical_id)
        result["imdb_id"] = canonical_id
        imdb_id = canonical_id

    payload = _build_imdbpie_payload(client, imdb_id)
    if payload is None:
        msg = f"IMDbPie failed to fetch data for '{imdb_id}'."
        _logger.warning(msg)
        details.append(f"Failed: {msg}")
        result["result"] = "Failed"
        result["result_details"] = details
        with contextlib.suppress(Exception):
            client.session.close()
        return result

    canonical: dict[str, Any] = {
        **payload,
        "cert_source": "imdbpie" if payload["cert"] else None,
    }
    _apply_metadata(result, canonical)

    msg = f"Identified IMDb metadata for '{imdb_id}' using IMDbPie."
    _logger.info(msg)
    details.append(f"Passed: {msg}")
    result["result"] = "Passed"
    result["result_details"] = details

    with contextlib.suppress(Exception):
        client.session.close()
    return result


# OMDb fallback strategy


def _omit_na(data: dict, key: str) -> str | None:
    """Return None if *data[key]* is absent, None, or 'N/A'; else return as str."""
    val = data.get(key)
    if val in (None, "N/A", ""):
        return None
    return str(val)


def _split_csv_field(data: dict, key: str) -> list[str] | None:
    """Return a non-empty list of stripped tokens from a comma-separated OMDb field, or None."""
    val = _omit_na(data, key) or ""
    items = [x.strip() for x in val.split(",") if x.strip()]
    return items or None


def _parse_digits_to_int(raw: str | None) -> int | None:
    """Extract all digit characters from *raw* and return as int, or None if *raw* is falsy."""
    return int("".join(re.findall(r"\d+", raw))) if raw else None


def _parse_omdb_canonical(data: dict) -> dict:
    """Build a canonically-shaped metadata dict from a raw OMDb response *data*."""
    _year_digits = 4  # noqa: PLR2004

    votes = _parse_digits_to_int(_omit_na(data, "imdb_votes"))
    runtime = _parse_digits_to_int(_omit_na(data, "runtime"))

    cast = _split_csv_field(data, "actors")
    directors = _split_csv_field(data, "director")
    writers = _split_csv_field(data, "writer")
    genres = _split_csv_field(data, "genre")

    # Rated — skip MPAA-specific non-values.
    rated = _omit_na(data, "rated")
    if rated in ("Not Rated", "Unrated"):
        rated = None

    countries = _convert_countries(_omit_na(data, "country"))
    languages = _convert_languages(_omit_na(data, "language"))

    # Normalise year and rating to canonical numeric types.
    # OMDb may return '2026–' for ongoing series — extract leading 4-digit year.
    raw_year = _omit_na(data, "year")
    year_digits = "".join(re.findall(r"\d+", raw_year))[:_year_digits] if raw_year else ""
    year: int | None = int(year_digits) if len(year_digits) == _year_digits else None

    raw_rating = _omit_na(data, "imdb_rating")
    rating: float | None = float(raw_rating) if raw_rating else None

    return {
        "title": _omit_na(data, "title"),
        "year": year,
        "poster": _omit_na(data, "poster"),
        "trailer_url": None,
        "plot_summary": _omit_na(data, "plot"),
        "plot_outline": None,
        "rating": rating,
        "votes": votes,
        "title_type": _omit_na(data, "type"),
        "runtime": runtime,
        "genres": genres,
        "cert": rated,
        "cert_source": "omdb" if rated else None,
        "title_id": _omit_na(data, "imdb_id"),
        "characters": None,
        "directors": directors,
        "writers": writers,
        "cast": cast,
        "languages": languages,
        "countries": countries,
    }


def _fetch_omdb(result: ResultDict, config: Config) -> ResultDict:
    import omdb  # noqa: PLC0415

    api_key = config.credentials.omdb.api_key
    imdb_id = result.get("imdb_id", "")
    details: list[str] = result.get("result_details") or []

    if not api_key:
        msg = "OMDb API key not configured; cannot fetch OMDb metadata."
        _logger.warning(msg)
        details.append(f"Failed: {msg}")
        result["result"] = "Failed"
        result["result_details"] = details
        return result

    try:
        omdb_client = omdb.OMDBClient(apikey=api_key)
        data = omdb_client.imdbid(imdb_id, timeout=30)
    except Exception as exc:  # noqa: BLE001
        msg = f"OMDb fetch failed for '{imdb_id}': {exc}"
        _logger.warning(msg)
        details.append(f"Failed: {msg}")
        result["result"] = "Failed"
        result["result_details"] = details
        return result

    # The omdb library converts OMDb error responses (e.g., invalid API key,
    # not found) to an empty dict rather than raising.  Detect that here so
    # callers see a clean failure instead of a misleading "Passed" with all
    # metadata fields set to None.
    if not data or not (data.get("title") or data.get("imdb_id")):
        msg = f"OMDb returned no usable data for '{imdb_id}' (empty or error response)."
        _logger.warning(msg)
        details.append(f"Failed: {msg}")
        result["result"] = "Failed"
        result["result_details"] = details
        return result

    canonical = _parse_omdb_canonical(data)
    _apply_metadata(result, canonical)

    msg = f"Identified IMDb metadata for '{imdb_id}' using OMDb."
    _logger.info(msg)
    details.append(f"Passed: {msg}")
    result["result"] = "Passed"
    result["result_details"] = details
    return result


# Private helpers


def _apply_metadata(result: ResultDict, data: dict[str, Any]) -> None:
    """Apply a canonically-shaped metadata dict to *result* in-place."""
    poster_raw = data.get("poster")
    poster_url = _strip_poster_resolution(poster_raw) if poster_raw else None
    result.update(
        {
            "imdb_title": data.get("title"),
            "imdb_year": data.get("year"),
            "imdb_poster_url": poster_url,
            "imdb_trailer_url": data.get("trailer_url"),
            "imdb_plot_summary": data.get("plot_summary"),
            "imdb_plot_outline": data.get("plot_outline"),
            "imdb_rating": data.get("rating"),
            "imdb_votes": data.get("votes"),
            "imdb_title_type": data.get("title_type"),
            "imdb_running_time_in_minutes": data.get("runtime"),
            "imdb_genres_list": data.get("genres"),
            "imdb_certification": data.get("cert"),
            "imdb_cert_source": data.get("cert_source"),
            "imdb_id": data.get("title_id") or result.get("imdb_id") or "",
            "imdb_credits_character_list": data.get("characters"),
            "imdb_credits_director_list": data.get("directors"),
            "imdb_credits_writer_list": data.get("writers"),
            "imdb_credits_cast_list": data.get("cast"),
            "imdb_language_list": data.get("languages"),
            "imdb_country_list": data.get("countries"),
        }
    )


def _credits_names(credits: dict, role: str) -> list[str] | None:
    """Extract up to _MAX_CREDITS names for a credit role."""
    names: list[str] = []
    try:
        for person in credits["credits"][role]:
            name = person.get("name")
            if name and name not in names and len(names) < _MAX_CREDITS:
                names.append(name)
    except (KeyError, TypeError):
        return None
    return names or None


def _credits_characters(credits: dict) -> list[str] | None:
    """Extract up to _MAX_CREDITS character names from the cast."""
    chars: list[str] = []
    try:
        for person in credits["credits"]["cast"]:
            for char in person.get("characters", []):
                if char and char not in chars and len(chars) < _MAX_CREDITS:
                    chars.append(char)
    except (KeyError, TypeError):
        return None
    return chars or None


def _extract_list_or_none(data: dict, key: str) -> list | None:
    try:
        val = data[key]
        # Guard against non-list values (e.g. strings, dicts) which would
        # propagate a wrong type into fields that expect list[str].
        if not isinstance(val, list) or not val:
            return None
        return val
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
        # Guard against JSON null (Python None) — str(None) == "None" is wrong.
        if val is None:
            return None
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


# Common OMDb country name aliases that pycountry's official `name` field
# does not recognise.  Keys are OMDb values; values are ISO 3166-1 alpha-2 codes.
_COUNTRY_ALIASES: dict[str, str] = {
    "USA": "us",
    "UK": "gb",
    "Russia": "ru",
    "South Korea": "kr",
    "Iran": "ir",
    "Syria": "sy",
    "Taiwan": "tw",
    "Bolivia": "bo",
    "Tanzania": "tz",
    "Venezuela": "ve",
    "Vietnam": "vn",
    "Moldova": "md",
    "Macedonia": "mk",
    "Palestinian Territory": "ps",
    "Kosovo": "xk",
}


def _convert_countries(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    result = []
    for raw_name in raw.split(","):
        name = raw_name.strip()
        # 1. Try pycountry's official name (e.g. "United States", "United Kingdom").
        country = pycountry.countries.get(name=name)
        if country:
            result.append(country.alpha_2.lower())
            continue
        # 2. Try alpha_2 directly (e.g. "US", "GB").
        country = pycountry.countries.get(alpha_2=name.upper())
        if country:
            result.append(country.alpha_2.lower())
            continue
        # 3. Try alpha_3 (e.g. "USA").
        country = pycountry.countries.get(alpha_3=name.upper())
        if country:
            result.append(country.alpha_2.lower())
            continue
        # 4. Try common_name (e.g. "Iran", "South Korea").
        country = pycountry.countries.get(common_name=name)
        if country:
            result.append(country.alpha_2.lower())
            continue
        # 5. Fall back to manual alias table for values that pycountry doesn't cover.
        alias = _COUNTRY_ALIASES.get(name)
        if alias:
            result.append(alias)
    return result or None


def _lang_code_from(lang: object | None) -> str | None:
    """Return the best available ISO code (alpha-2 preferred, alpha-3 fallback) for *lang*, or None."""
    if lang is None:
        return None
    code = getattr(lang, "alpha_2", None) or getattr(lang, "alpha_3", None)
    return code.lower() if code else None


def _lookup_language_code(name: str) -> str | None:
    """Resolve one language token to an ISO 639-1 alpha-2 code using four lookup strategies.

    Strategies tried in order:
    1. ISO 639-1 alpha-2 code (e.g. "en", "de")
    2. ISO 639-2/3 alpha-3 code (e.g. "eng", "deu")
    3. ISO 639-2/B bibliographic alpha-3 code (e.g. "ger" for German)
    4. Official language name (e.g. "English", "German")

    Returns the resolved alpha-2 code (lower-case), or alpha-3 as fallback if no alpha-2 exists.
    Returns None if no strategy matches.
    """
    lower = name.lower()
    lang = (
        pycountry.languages.get(alpha_2=lower)
        or pycountry.languages.get(alpha_3=lower)
        or pycountry.languages.get(bibliographic=lower)
        or pycountry.languages.get(name=name)
    )
    return _lang_code_from(lang)


def _convert_languages(raw: str | None) -> list[str] | None:
    """Convert a comma-separated string of language names or codes to ISO 639-1 alpha-2 codes."""
    if not raw:
        return None
    result = []
    for raw_name in raw.split(","):
        name = raw_name.strip()
        if not name:
            continue
        code = _lookup_language_code(name)
        if code is not None:
            result.append(code)
    return result or None
