"""Title parsing and text manipulation utilities for movarr."""

from __future__ import annotations

import re

from loguru import logger as _logger

__all__ = [
    "sanitise",
    "extract_movie_title",
    "extract_year",
    "extract_resolution",
    "extract_group",
    "extract_after_year",
    "normalise_for_compare",
    "build_sqlite_pattern",
    "all_criteria_present",
    "is_tv_content",
    "keyword_search",
    "bad_keyword_search",
    "quality_score",
]

# Compiled patterns

_RE_FILE_EXTENSION = re.compile(r"\.[a-z0-9]{3}$")
_RE_NON_ASCII = re.compile(r"[\.\s\-\_]?(\s?\[?[^\x00-\x7F]{2,}).*([^\x00-\x7F]{2,}\]?\s?)[\.\s\-\_]?")
_RE_BRACKETS_START = re.compile(r"^([\s\.\-\_]+)?\[.+?\]")
_RE_BRACKETS_END = re.compile(r"\[[^\[]+\]([\s\.\-\_]+)?$")
_RE_BRACES_START = re.compile(r"^([\s\.\-\_]+)?\{.+?\}")
_RE_BRACES_END = re.compile(r"\{[^\{]+\}([\s\.\-\_]+)?$")
_RE_END_TAGS = re.compile(r"[\s\.\-\_]\[[a-zA-Z]+\]$|@[a-zA-Z0-9]+$")
_RE_ROUND_SQUARE_BRACKETS = re.compile(r"[\[\]\(\)]+")
_RE_INVALID_WIN_CHARS = re.compile(r'[<>:"/\\|?*]+')
_RE_WEBSITE = re.compile(r"(?i)www[\s\.\-\_][a-zA-Z0-9]+[\s\.\-\_][a-zA-Z0-9]{3,}")
_RE_START_DATE = re.compile(r"^(\d{2,4}\s){3}")
_RE_TT_NUMBER = re.compile(r"(?i)tt\d{7,}")
_RE_AT_END = re.compile(r"@.+?$")
_RE_SEPARATOR = re.compile(r"[\.\-\_,\s]+")
_RE_SEPARATOR_BRACKETS = re.compile(r"[\.\-\_,\s\[\(\]\)]+")
_RE_COMPARE = re.compile(r"[\s\.\-\_\:\+\'\"\!\,\@\#]+")
_RE_SQLITE = re.compile(r"\.|_|-|\s|&")
_RE_RESOLUTION = re.compile(r"\d{3,4}(?=p)")
_RE_TV_EPISODE = re.compile(r"(?i)s[\d]{2,3}(e[\d]{2,3})|s[\d]{2,3}|ep[\d]{2,3}")
_RE_MOVIE_TITLE = re.compile(r"^(.*?)(?=[\s\.\-\_]\d{4})")
_RE_YEAR = re.compile(r"(?<=[\(\s\.\-\_])\d{4}(?=[\s\.\-\_\)]|$)")
_RE_GROUP = re.compile(r"[a-zA-Z0-9]+$")
_RE_TITLE_YEAR_SPLIT = re.compile(r"^(.+?\d{4}[\s\.\-\+,]?)(.*)")

_WORD_TO_INT: dict[str, str] = {
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
}
_ROMAN_TO_INT: dict[str, str] = {
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
    "x": "10",
}

# Pre-compiled patterns for word/numeral replacement — avoids recompiling on every call.
_NUMERAL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"(?<=[\s.\-_])({word})(?=[\s.\-_]|$)", re.IGNORECASE), digit)
    for mapping in (_WORD_TO_INT, _ROMAN_TO_INT)
    for word, digit in mapping.items()
]


# Internal helpers


def _replace_words_with_ints(text: str) -> str:
    """Replace written/Roman numerals with digit strings (word-boundary aware)."""
    for pattern, digit in _NUMERAL_PATTERNS:
        text = pattern.sub(digit, text)
    return text


def _after_year(text: str) -> str | None:
    """Return the substring of *text* that follows the year token."""
    match = _RE_TITLE_YEAR_SPLIT.search(text)
    if match:
        return match.group(2).lower()
    return None


# Public API


def sanitise(title: str) -> str | None:
    """Clean a raw index title string, removing noise artefacts.

    Strips file extensions, non-ASCII clusters, brackets, invalid filename
    characters, website strings, timestamps, and IMDb tt-numbers. Collapses
    separators and trailing/leading whitespace.

    Args:
        title: Raw indexer title string.

    Returns:
        Sanitised string, or ``None`` if the result is empty.
    """
    if not title:
        return None

    result = _RE_FILE_EXTENSION.sub("", title)
    result = _RE_NON_ASCII.sub("", result)
    result = _RE_BRACKETS_START.sub("", result)
    result = _RE_BRACKETS_END.sub("", result)
    result = _RE_BRACES_START.sub("", result)
    result = _RE_BRACES_END.sub("", result)
    result = _RE_INVALID_WIN_CHARS.sub("", result)
    result = _RE_END_TAGS.sub("", result)
    result = _RE_ROUND_SQUARE_BRACKETS.sub(" ", result)
    result = _RE_SEPARATOR.sub(" ", result)
    result = _RE_WEBSITE.sub("", result)
    result = _RE_START_DATE.sub("", result)
    result = _RE_TT_NUMBER.sub("", result)
    result = _RE_AT_END.sub("", result)
    result = " ".join(result.split())

    return result or None


def extract_movie_title(sanitised: str) -> str | None:
    """Extract the movie title (everything before the year) from a sanitised title.

    Args:
        sanitised: A sanitised index title string.
    """
    if not sanitised:
        return None
    match = _RE_MOVIE_TITLE.search(sanitised)
    return match.group(0) if match else None


def extract_year(sanitised: str) -> str | None:
    """Extract the 4-digit year from a sanitised title string.

    Args:
        sanitised: A sanitised index title string.
    """
    if not sanitised:
        return None
    match = _RE_YEAR.search(sanitised)
    return match.group(0) if match else None


def extract_resolution(sanitised: str) -> str | None:
    """Extract the resolution token (e.g. ``"1080"``) from a sanitised title.

    Only looks in the portion of the title after the year.

    Args:
        sanitised: A sanitised index title string.
    """
    if not sanitised:
        return None
    after = _after_year(sanitised)
    if not after:
        return None
    match = _RE_RESOLUTION.search(after)
    return match.group(0) if match else None


def extract_group(sanitised: str) -> str | None:
    """Extract the release group (trailing word after year) from a sanitised title.

    Args:
        sanitised: A sanitised index title string.
    """
    if not sanitised:
        return None
    after = _after_year(sanitised)
    if not after:
        return None
    match = _RE_GROUP.search(after)
    return match.group(0) if match else None


def extract_after_year(sanitised: str) -> str | None:
    """Return the lowercase portion of *sanitised* that follows the year token.

    Args:
        sanitised: A sanitised index title string.
    """
    if not sanitised:
        return None
    return _after_year(sanitised)


def normalise_for_compare(text: str) -> str | None:
    """Normalise a title string for fuzzy comparison.

    Lowercases, replaces ``&`` with ``and``, strips ``imdb``, converts
    written/Roman numerals to digits, and removes all separator characters.

    Args:
        text: Input string (should be sanitised first).
    """
    if not text:
        return None

    result = text.lower()
    result = result.replace("&", "and")
    result = result.replace("imdb", "")
    result = _replace_words_with_ints(result)
    result = _RE_COMPARE.sub("", result)
    return result or None


def build_sqlite_pattern(sanitised: str) -> str | None:
    """Build a ``LIKE``-style SQLite pattern for fuzzy duplicate detection.

    Replaces common separator characters with ``%`` and wraps in ``%%…%%``.

    Args:
        sanitised: A sanitised index title string (movie title portion).
    """
    if not sanitised:
        return None
    title = extract_movie_title(sanitised)
    if not title:
        return None
    result = _RE_SQLITE.sub("%", title)
    _logger.debug("build_sqlite_pattern: input=%r output=%r", title, result)
    return f"%%{result}%%"


def all_criteria_present(criteria: str, index_title: str) -> bool:
    """Return True if every space-separated token in *criteria* appears in *index_title*.

    The search is case-insensitive. E.g. criteria ``"2160p remux"`` requires
    both ``"2160p"`` AND ``"remux"`` to be present.

    Args:
        criteria: Space-separated search tokens (e.g. ``"1080p"`` or ``"2160p remux"``).
        index_title: The raw or sanitised index title to search within.
    """
    lower_title = index_title.lower()
    return all(token.lower() in lower_title for token in criteria.split())


def is_tv_content(sanitised: str) -> bool:
    """Return True if the sanitised title looks like a TV episode or season pack.

    Args:
        sanitised: A sanitised index title string.
    """
    if not sanitised:
        return False
    after = _after_year(sanitised) or sanitised.lower()
    return bool(_RE_TV_EPISODE.search(after))


# Quality scoring tables (resolution → source → audio, higher = better)
_RESOLUTION_SCORES: dict[str, int] = {
    r"(480p?|540p?)": 10,
    r"(720p?)": 20,
    r"(1080p?)": 30,
    r"(2160p?)": 40,
    r"(4320p?)": 50,
}
_SOURCE_SCORES: dict[str, int] = {
    r"(remux|bdremux|bd\sremux)": 80,
    r"(bd|bdrip|bluray|blu\sray)": 40,
    r"(webdl|web\sdl|hdrip)": 30,
    r"(hdtv)": 20,
    r"(dvdrip|webrip)": 10,
}
_AUDIO_SCORES: dict[str, int] = {
    r"(dtsx|dts\sx|atmos)": 30,
    r"(dtshd|dts\shd|truehd|true\shd|ddp)": 20,
    r"(dts)": 10,
}
_HDR_SCORES: dict[str, int] = {
    r"(dolby\svision|dv|do\.vi\.)": 15,
    r"(hdr10\+|hdr10plus|hdrplus)": 12,
    r"(hdr)": 10,
}


def keyword_search(sanitised: str, keyword: str) -> bool:
    """Return True if *keyword* appears as a word-token after the year in *sanitised*.

    Args:
        sanitised: A sanitised index title string.
        keyword: Keyword to search for (e.g. ``"remux"`` or ``"2160p"``).
    """
    if not sanitised:
        return False
    after = _after_year(sanitised)
    if not after:
        return False
    sep = r"[\s.\-_]"
    pattern = rf"(?:^|{sep}){re.escape(keyword)}(?:{sep}|$)"
    return bool(re.search(pattern, after, re.IGNORECASE))


def bad_keyword_search(string: str, keyword: str) -> bool:
    """Return True if *keyword* appears as a token after the year in *string*.

    Uses a wider separator set (includes brackets) so bracket-enclosed keywords match.

    Args:
        string: Raw or sanitised index title string.
        keyword: Keyword to search for (e.g. ``"xvid"``).
    """
    if not string:
        return False
    after = _after_year(string) or string.lower()
    sep = r"[\s.\-_\[\(\]\)]"
    pattern = rf"(?:^|{sep}){re.escape(keyword)}(?:{sep}|$)"
    return bool(re.search(pattern, after, re.IGNORECASE))


def quality_score(sanitised: str) -> int:
    """Return a composite quality score for a sanitised title.

    Scores resolution, source type, audio quality, and HDR format independently
    and sums them. Higher scores represent better-quality releases.

    Args:
        sanitised: A sanitised index title string.
    """
    after = _after_year(sanitised) or ""
    score = 0
    for table in (_RESOLUTION_SCORES, _SOURCE_SCORES, _AUDIO_SCORES, _HDR_SCORES):
        for pattern, value in table.items():
            if re.search(rf"(?:^|\s){pattern}(?:\s|$)", after, re.IGNORECASE):
                score += value
                break  # only one match per table
    return score
