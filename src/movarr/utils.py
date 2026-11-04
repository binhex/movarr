"""Miscellaneous project utilities for movarr."""

import re

# Query parameters that carry credentials for the services movarr talks to
# (TMDb, OMDb and Jackett all authenticate via the query string).
_SECRET_QUERY_RE = re.compile(r"(?i)\b(api[_-]?key|apikey|token|secret|password|passwd)=([^&\s'\"]+)")


def redact_secrets(value: object) -> str:
    """Return *value* as a string with secret-bearing query parameters masked.

    Exception messages and log records frequently embed the request URL.
    Because TMDb, OMDb and Jackett pass their API key in the query string, an
    unredacted URL leaks the user's credential into the log file, the history
    database and queued notification bodies.

    Args:
        value: Any value; converted with ``str()`` before redaction.

    Returns:
        The string with every secret query parameter value replaced by
        ``REDACTED``.
    """
    return _SECRET_QUERY_RE.sub(r"\1=REDACTED", str(value))


def bytes_to_mb(size_bytes: object) -> str:
    """Convert a raw byte count to a decimal megabyte string.

    Truncates fractional MB.  Returns ``"0"`` on invalid input.
    """
    try:
        # Use int() directly (not float) to avoid losing precision for values
        # larger than 2^53 bytes (~9 PB), though current torrent files never
        # approach that size.
        value = int(str(size_bytes).split(".")[0])
        if value < 0:
            return "0"
        return str(value // 1_000_000)
    except (ValueError, TypeError, OverflowError):
        return "0"


def parse_size_bytes(value: object) -> int | None:
    """Return *value* as an integer byte count, tolerating float-formatted strings.

    Indexer JSON occasionally reports sizes as ``4294967296.0``; ``int()`` rejects
    that while the rest of the pipeline (``bytes_to_mb``) accepts it.

    Args:
        value: Raw size value from an indexer feed.

    Returns:
        The integer byte count, or ``None`` when *value* is not numeric.
    """
    try:
        return int(str(value).split(".")[0])
    except (ValueError, TypeError, OverflowError):
        return None
