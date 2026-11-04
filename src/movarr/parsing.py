"""Title parsing and text manipulation utilities for movarr."""

from __future__ import annotations

import re
import unicodedata

__all__ = [
    "sanitise",
    "extract_movie_title",
    "extract_year",
    "extract_resolution",
    "extract_group",
    "extract_after_year",
    "normalise_for_compare",
    "is_tv_content",
    "bad_keyword_search",
    "dedup_quality_score",
    "quality_score",
    "normalise_imdb_id",
    "is_extras_filename",
]

# Compiled patterns — each strips or captures a specific noise artifact from raw index titles.

_RE_FILE_EXTENSION = re.compile(r"\.[a-z0-9]{3}$")  # Strips trailing file extension (e.g. ".mkv").
_RE_NON_ASCII = re.compile(
    r"[\[\{][^\]\}]*[^\x00-\x7F][^\]\}]*[\]\}]"
)  # Strips a *bracketed* non-ASCII language annotation (e.g. "[CHINESE]").
# An unbracketed non-ASCII title is real content ("Иван Васильевич...") and is
# preserved; accent folding happens later, in normalise_for_compare.
_RE_BRACKETS_START = re.compile(
    r"^([\s\.\-\_]+)?\[.+?\]"
)  # Strips leading bracketed tag (e.g. "[YTS]" at start of title).
_RE_BRACKETS_END = re.compile(r"\[[^\[]+\]([\s\.\-\_]+)?$")
_RE_BRACES_START = re.compile(
    r"^([\s\.\-\_]+)?\{.+?\}"
)  # Strips leading brace-enclosed tag (e.g. "{group}" at start of title).
_RE_BRACES_END = re.compile(r"\{[^\{]+\}([\s\.\-\_]+)?$")
_RE_END_TAGS = re.compile(
    r"[\s\.\-\_]\[[a-zA-Z]+\]$|@[a-zA-Z0-9]+$"
)  # Strips trailing bracket tags (e.g. "[GROUP]") or @handle suffixes.
_RE_ROUND_SQUARE_BRACKETS = re.compile(r"[\[\]\(\)]+")
_RE_INVALID_WIN_CHARS = re.compile(r'[<>:"/\\|?*]+')
_RE_WEBSITE = re.compile(r"(?i)www[\s\.\-\_][a-zA-Z0-9]+[\s\.\-\_][a-zA-Z0-9]{3,}")
_RE_START_DATE = re.compile(r"^(\d{2,4}\s){3}")
_RE_TT_NUMBER = re.compile(r"(?i)tt\d{7,}")
_RE_DIGITS = re.compile(r"\d{7,}")
_RE_AT_END = re.compile(r"(?:\s+|^)@[A-Za-z0-9_]+$")
_RE_SEPARATOR = re.compile(r"[\.\-\_,\s]+")
_RE_COMPARE = re.compile(r"[\s\.\-\_\:\+\'\"\!\,\@\#\u2018\u2019\u02bc\u02bb]+")
# Strips possessive 's after an s-ending word (e.g. "Jones's" → "Jones").
# Matches ASCII and Unicode apostrophes with optional trailing s at a word boundary.
# The Unicode branches are technically dead after the NFKD step (all text is ASCII),
# but retained as belt-and-suspenders in case the pipeline order ever changes.
_RE_POSSESSIVE_S = re.compile(r"(?<=s)['\u2018\u2019\u02bc\u02bb]s?\b", re.IGNORECASE)
_UNICODE_APOSTROPHES = re.compile(r"[\u2018\u2019\u02bc\u02bb]")
_RE_RESOLUTION = re.compile(r"\d{3,4}(?=p)")
_RE_RESOLUTION_WORD = re.compile(r"(?i)(?:^|[\W_])(?:4k|uhd)(?:$|[\W_])")
# Anchored on token boundaries so "dts51"/"m2ts05" are not read as an episode
# marker, and SxxExx is its own alternative (the Sxx branch does not subsume it).
_RE_TV_EPISODE = re.compile(r"(?i)(?:^|[\W_])(?:s\d{2,3}e\d{2,3}|s\d{2,3}|ep\d{2,3})(?:[\W_]|$)")
# Plausible release years only: a bare "1080"/"2160" is a resolution token, and
# treating it as the year truncated the title and hid the resolution.
_RE_YEAR = re.compile(r"(?<=[\(\s\.\-\_])(?:18|19|20)\d{2}(?=[\s\.\-\_\)]|$)")
_RE_GROUP = re.compile(r"[a-zA-Z0-9]+$")

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
    """Return the substring of *text* that follows the release-year token.

    Uses the *last* year-shaped token (see :func:`_last_year_span`) so a
    resolution like ``1080`` is never mistaken for the year — with the previous
    first-4-digit split, ``"Show S01E02 1080p"`` yielded ``"p"`` and the episode
    marker was invisible.
    """
    span = _last_year_span(text)
    if span is None:
        return None
    return text[span[1] :].strip(" .-+,").lower()


# Public API


def sanitise(title: str) -> str | None:
    """Clean a raw index title string, removing noise artefacts.

    Strips file extensions, bracketed non-ASCII annotations, brackets, invalid
    filename characters, website strings, timestamps, and IMDb tt-numbers.
    Collapses separators and trailing/leading whitespace.

    Args:
        title: Raw indexer title string.

    Returns:
        Sanitised string, or ``None`` if the result is empty.
    """
    if not title:
        return None

    result = _RE_FILE_EXTENSION.sub("", title)
    result = _RE_NON_ASCII.sub("", result)
    result = _strip_leading_bracket(result)
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


def _strip_leading_bracket(text: str) -> str:
    """Strip a leading ``[TAG]`` only when a real title remains behind it.

    ``[YTS] Movie 2020 1080p`` should lose the group tag, but ``[REC] 2007
    1080p`` must keep its title -- stripping there leaves a year-only string
    that would parse as the title ``"2007"``.  A candidate is rejected when it
    loses the year or its title portion is purely numeric.
    """
    candidate = _RE_BRACKETS_START.sub("", text)
    if candidate == text:
        return text
    span = _last_year_span(candidate)
    if span is None:
        return text
    title = candidate[: span[0]].strip(" .-_[]()")
    if not title or title.isdigit():
        return text
    return candidate


def extract_movie_title(sanitised: str) -> str | None:
    """Extract the movie title (everything before the year) from a sanitised title.

    Args:
        sanitised: A sanitised index title string.
    """
    if not sanitised:
        return None
    span = _last_year_span(sanitised)
    if span is None:
        return None
    return sanitised[: span[0]].rstrip(" .-")


def _last_year_span(sanitised: str) -> tuple[int, int] | None:
    """Return the (start, end) span of the *last* year token in *sanitised*.

    Scene naming puts the release year last among year-shaped tokens, so
    ``Blade Runner 2049 2017 2160p`` resolves to 2017 and keeps ``2049`` in the
    title.  Taking the first match instead mis-parses every title that contains
    a year-like number.
    """
    spans = [m.span() for m in _RE_YEAR.finditer(sanitised)]
    return spans[-1] if spans else None


def extract_year(sanitised: str) -> str | None:
    """Extract the 4-digit year from a sanitised title string.

    Args:
        sanitised: A sanitised index title string.
    """
    if not sanitised:
        return None
    span = _last_year_span(sanitised)
    if span is None:
        return None
    return sanitised[span[0] : span[1]]


# Bonus/extras markers, matched only in the post-year segment so a real title
# such as "Interview with the Vampire" is never misclassified.
_EXTRAS_MARKERS_RE = re.compile(
    r"\b(?:behind[\s_.\-]+the[\s_.\-]+scenes|making[\s_.\-]+of|featurettes?"
    r"|deleted[\s_.\-]+scenes?|interviews?|short[\s_.\-]+films?"
    r"|theatrical[\s_.\-]+trailer|trailers?|samples?"
    r"|bonus(?:es)?|extras|special[\s_.\-]+features?|specials)\b",
    re.IGNORECASE,
)


def is_extras_filename(sanitised: str) -> bool:
    """Return True when *sanitised* looks like bonus/extras content, not the feature.

    Only the segment after the release year is inspected, so titles that happen
    to contain a marker word (e.g. ``Interview with the Vampire 1994``) are not
    treated as extras.

    Args:
        sanitised: A sanitised filename or index title.
    """
    if not sanitised:
        return False
    remainder = _after_year(sanitised)
    return bool(remainder and _EXTRAS_MARKERS_RE.search(remainder))


def extract_resolution(sanitised: str) -> str | None:
    """Extract the resolution token (e.g. ``"1080"``) from a sanitised title.

    Only looks in the portion of the title after the year.  ``4K`` and ``UHD``
    are normalised to ``"2160"``.

    Args:
        sanitised: A sanitised index title string.
    """
    if not sanitised:
        return None
    after = _after_year(sanitised)
    if not after:
        return None
    match = _RE_RESOLUTION.search(after)
    if match:
        return match.group(0)
    if _RE_RESOLUTION_WORD.search(after):
        # "4K"/"UHD" means the same thing as 2160p for routing and library dedup.
        return "2160"
    return None


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


def normalise_imdb_id(value: object) -> str | None:
    """Return *value* as a canonical ``ttNNNNNNN`` IMDb ID, or ``None`` if malformed.

    Indexers supply IMDb IDs either as ``tt1234567`` or as bare digits, and a
    malformed value must never reach the notification body, the qBittorrent tag
    or the IMDbPie request URL.  Anything that is not ``tt`` plus at least seven
    digits (or the bare-digit equivalent) is rejected.

    Args:
        value: Raw IMDb ID value from an indexer feed.

    Returns:
        The canonical ID, or ``None`` when *value* is not a usable IMDb ID.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        # Bare integers are zero-padded to the canonical seven digits
        # (Prowlarr reports ``imdbId`` as an integer, e.g. 113627 -> tt0113627).
        return f"tt{value:07d}" if value > 0 else None
    raw = str(value).strip()
    if _RE_TT_NUMBER.fullmatch(raw):
        # Canonical form is lowercase; the tag parser is case-sensitive.
        return raw.lower()
    if _RE_DIGITS.fullmatch(raw):
        return f"tt{raw}"
    return None


def normalise_for_compare(text: str) -> str | None:
    """Normalise a title string for fuzzy comparison.

    Lowercases, folds accented/decorated characters to ASCII equivalents
    (stripping remaining non-ASCII), replaces ``&`` with ``and``, strips
    ``imdb``, converts written/Roman numerals to digits, and removes all
    separator characters.

    Args:
        text: Input string (should be sanitised first).
    """
    if not text:
        return None

    result = text.lower()
    # Fold typographic apostrophes to ASCII *before* the ASCII-only step: after
    # encode("ascii", "ignore") a U+2019 is simply gone, which left
    # "Bridget Jones’s Diary" as "bridgetjonessdiary" and defeated the
    # possessive rule below.
    result = _UNICODE_APOSTROPHES.sub("'", result)
    # Convert accented/special characters to ASCII equivalents (NFKD decomposition).
    # Characters that do NOT decompose to ASCII (e.g. ß, æ, ø, ł) are silently dropped
    # — a known limitation.  Library filenames rarely contain these, and the prior
    # behaviour (preserving them) also failed to match their ASCII equivalents.
    result = unicodedata.normalize("NFKD", result).encode("ascii", "ignore").decode("ascii")
    result = result.replace("&", "and")
    result = result.replace("imdb", "")
    result = _replace_words_with_ints(result)
    # Strip possessive 's after s-ending words before general separator removal
    result = _RE_POSSESSIVE_S.sub("", result)
    result = _RE_COMPARE.sub("", result)
    return result or None


def is_tv_content(sanitised: str) -> bool:
    """Return True if the sanitised title looks like a TV episode or season pack.

    Args:
        sanitised: A sanitised index title string.
    """
    if not sanitised:
        return False
    after = _after_year(sanitised) or sanitised.lower()
    return bool(_RE_TV_EPISODE.search(after))


# Quality scoring weights: higher value = better quality.
# Scale is relative; resolution differences dominate source differences,
# which dominate audio differences, which dominate HDR differences.
_RESOLUTION_SCORES: dict[str, int] = {
    r"(480p?|540p?)": 10,
    r"(720p?)": 20,
    r"(1080p?)": 30,
    r"(2160p?)": 40,
    r"(4k|uhd)": 40,
    r"(4320p?)": 50,
}
_SOURCE_SCORES: dict[str, int] = {
    r"(remux|bdremux|uhdremux)": 80,
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


def bad_keyword_search(string: str, keyword: str) -> bool:
    """Return True if *keyword* appears as a token after the year in *string*.

    When *string* contains no year token the whole string is searched.
    Uses a wider separator set (includes brackets) so bracket-enclosed keywords match.

    Args:
        string: Raw or sanitised index title string.
        keyword: Keyword to search for (e.g. ``"xvid"``).
    """
    if not string:
        return False
    after = _after_year(string)
    if after is None:
        after = string.lower()
    sep = r"[\s.\-_\[\(\]\)]"
    pattern = rf"(?:^|{sep}){re.escape(keyword)}(?:{sep}|$)"
    return bool(re.search(pattern, after, re.IGNORECASE))


def dedup_quality_score(sanitised: str) -> int:
    """Score a sanitised title for library dedup comparison.

    Only includes resolution and source type — the most reliable signals.
    HDR and audio tokens are excluded as they are commonly embellished
    by indexers, causing false "upgrade" detections.

    Args:
        sanitised: A sanitised index title string.
    """
    after = _after_year(sanitised) or ""
    score = 0
    for table in (_RESOLUTION_SCORES, _SOURCE_SCORES):
        for pattern, value in table.items():
            if re.search(rf"(?:^|\s){pattern}(?:\s|$)", after, re.IGNORECASE):
                score += value
                break
    return score


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
