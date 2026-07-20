"""Notification via apprise for movarr.

Sends a Markdown summary when a torrent is queued.  Any apprise-supported
service URL can be specified in ``config.notification.apprise_urls``.

Fixes siphonator bug #2: ``", ".join(None)`` crashes — all list fields
are null-guarded here.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import TYPE_CHECKING

import apprise
from loguru import logger

_AMAZON_POSTER_RES_RE = re.compile(r"_[SU][XWYL]\d+")
_AMAZON_POSTER_V1_RE = re.compile(r"\._V1_[^.]*\.")


def _strip_poster_resolution(url: str) -> str:
    """Strip any resolution/quality modifier from an Amazon poster URL.

    Handles _SX<width>, _SY<height>, _SW<width>, _UX<max-width>,
    _UY<max-height>, and _SL<size-limit> suffixes.
    """
    return _AMAZON_POSTER_RES_RE.sub("_", url)


def _poster_url_with_width(url: str, width: int) -> str:
    """Return the poster URL constrained to *width* pixels (width <= 0 returns largest/original).

    Handles all known Amazon modifier variants (``_SX``, ``_SY``, ``_SW``,
    ``_UX``, ``_UY``) using regex-based replacement. Works with both ``.jpg``
    and ``.png`` extensions. If the URL lacks the ``_V1`` segment (unexpected
    non-Amazon format), the URL is returned unchanged.
    """
    if width <= 0:
        return _strip_poster_resolution(url)
    stripped = _strip_poster_resolution(url)
    if "_V1_" not in stripped:
        return url
    # Inject _SX<width> before the file extension, handling any _V1 modifiers
    return _AMAZON_POSTER_V1_RE.sub(f"._V1_SX{width}.", stripped)


# Markdown-capable Apprise service schemes
# These services can render **bold**, [links](url), and other Markdown formatting.
# Services not in this set receive a plain-text body to avoid garbled output.
_MARKDOWN_SCHEMES = frozenset({"ntfy", "ntfys", "discord", "slack", "tgram", "tg", "matrix", "matrixs"})


if TYPE_CHECKING:
    from movarr.config import Config
    from movarr.models import ResultDict

__all__ = ["send_index_proxy_alert", "send_queued_notification", "send_service_alert"]


def send_service_alert(service_name: str, hours_elapsed: float, config: Config) -> bool:
    """Send an alert notification when a monitored service has been unavailable.

    Generic function used by all service health monitors (index proxy,
    torrent client, etc.).

    Args:
        service_name: Human-readable service name, e.g. ``"Prowlarr"`` or ``"qBittorrent"``.
        hours_elapsed: How many hours the unavailability streak has lasted.
        config: Application configuration.

    Returns:
        ``True`` if the notification was delivered, ``False`` otherwise (including
        when no apprise URLs are configured).
    """
    urls = config.notification.apprise_urls
    if not urls:
        logger.debug("No apprise URLs configured; skipping service alert.")
        return False

    hours_str = f"{hours_elapsed:.1f}"
    subject = f"movarr: {service_name} has been unavailable for {hours_str}h \u2014 possible outage"
    safe_service = _escape_markdown_text(service_name)
    body_md = (
        f"**movarr service health alert**\n\n"
        f"**Service:** {safe_service}\n"
        f"**Duration:** Unavailable for {hours_str} hours.\n\n"
        f"movarr will keep retrying every cycle. "
        f"Check that {safe_service} is running and accessible."
    )
    body_text = (
        f"movarr service health alert\n\n"
        f"Service: {safe_service}\n"
        f"Duration: Unavailable for {hours_str} hours.\n\n"
        f"movarr will keep retrying every cycle. "
        f"Check that {safe_service} is running and accessible."
    )

    if not _dispatch_apprise(subject, list(urls), body_markdown=body_md, body_text=body_text):
        return False

    logger.warning("Service alert sent: {}", subject)
    return True


def send_index_proxy_alert(proxy_name: str, hours_elapsed: float, config: Config) -> bool:
    """Send an alert for an index proxy outage.

    Delegates to :func:`send_service_alert`. Kept for backwards compatibility.

    Args:
        proxy_name: Human-readable proxy name, e.g. ``"Prowlarr"`` or ``"Jackett"``.
        hours_elapsed: How many hours the zero-results streak has lasted.
        config: Application configuration.

    Returns:
        ``True`` if the notification was delivered, ``False`` otherwise.
    """
    return send_service_alert(service_name=proxy_name, hours_elapsed=hours_elapsed, config=config)


def send_queued_notification(result: ResultDict, config: Config) -> bool:
    """Send a notification for a newly queued torrent via all configured apprise URLs.

    Args:
        result: Fully-populated pipeline dict (index + IMDb metadata).
        config: Application configuration.

    Returns:
        ``True`` if the notification was sent without error, ``False`` otherwise
        (including when no URLs are configured).
    """
    urls = config.notification.apprise_urls
    if not urls:
        logger.debug("No apprise URLs configured; skipping notification.")
        return False

    fields = _extract_body_fields(result, config)
    body_md = _build_markdown_body(fields)
    body_text = _build_text_body(fields)
    subject = _build_subject(result)

    if not _dispatch_apprise(subject, list(urls), body_markdown=body_md, body_text=body_text):
        return False

    logger.info("Notification sent: {}", subject)
    return True


def _is_markdown_service(url: str) -> bool:
    """Return True if *url* uses a scheme that supports markdown formatting."""
    try:
        scheme = url.split("://", 1)[0].lower()
    except (ValueError, AttributeError):
        return False
    return scheme in _MARKDOWN_SCHEMES


def _send_apprise_group(
    title: str,
    urls: list[str],
    body: str | None,
    body_format: str,
    *,
    group_label: str = "",
    ensure_ntfy_url: bool = False,
) -> int:
    """Send *body* to a group of *urls* and return 1 on success, else 0."""
    body = body or ""
    if not urls or not body:
        return 0
    return _try_apprise_notify(title, urls, body, body_format, ensure_ntfy_url, group_label)


def _try_apprise_notify(
    title: str,
    urls: list[str],
    body: str,
    body_format: str,
    ensure_ntfy_url: bool,
    group_label: str,
) -> int:
    """Build an Apprise instance and attempt notification; return 1 or 0."""
    ap = apprise.Apprise()
    for url in urls:
        ap.add(_ensure_ntfy_markdown(url) if ensure_ntfy_url else url)
    try:
        if ap.notify(title=title, body=body, body_format=body_format):
            return 1
    except Exception:  # noqa: BLE001
        label = f" ({group_label})" if group_label else ""
        logger.warning("Apprise notification failed{}.", label)
    return 0


def _dispatch_apprise(
    subject: str,
    urls: list[str],
    *,
    body_markdown: str | None = None,
    body_text: str | None = None,
) -> bool:
    """Send notification to markdown-capable and text-only targets separately.

    URLs are split by scheme: markdown-capable services (ntfy, discord, etc.)
    receive *body_markdown* with ``NotifyFormat.MARKDOWN``; all others receive
    *body_text* with ``NotifyFormat.TEXT``.

    Returns True if at least one notification was sent successfully.
    """
    if not urls:
        return False
    if not subject:
        return False
    if not body_markdown and not body_text:
        return False

    md_urls: list[str] = []
    text_urls: list[str] = []
    for u in urls:
        if _is_markdown_service(u):
            md_urls.append(u)
        else:
            text_urls.append(u)

    total = _send_apprise_group(
        subject, md_urls, body_markdown, apprise.NotifyFormat.MARKDOWN, group_label="markdown", ensure_ntfy_url=True
    )
    total += _send_apprise_group(
        subject, text_urls, body_text, apprise.NotifyFormat.TEXT, group_label="text", ensure_ntfy_url=False
    )

    if total == 0:
        logger.warning("Apprise notification was not sent (no valid targets or all failed).")
        return False
    return True


# Internal helpers


def _ensure_ntfy_markdown(url: str) -> str:
    """Append ``?format=markdown`` to an Apprise ntfy URL if not already present.

    Ntfy's Apprise plugin defaults to ``NotifyFormat.TEXT`` unless the
    URL carries a ``?format=markdown`` query parameter.  Without it,
    Markdown formatting (bold text, links, etc.) is lost.  Automatically
    adding the parameter ensures Markdown rendering without requiring
    the user to remember to append it to every ntfy URL.
    """
    if not url.startswith(("ntfy://", "ntfys://")):
        return url
    if "format=markdown" in url:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}format=markdown"


def _build_subject(result: ResultDict) -> str:
    """Build the plain-text notification subject line.

    The subject is used as the Apprise `title=` field (plain text),
    so we must NOT HTML-escape it — doing so produces visible entities
    like ``&amp;`` in delivered notifications.
    """
    title = result.get("imdb_title") or "Unknown"
    year = result.get("imdb_year") or ""
    year_str = f" ({year})" if year else ""
    return f"movarr: {title}{year_str}"


def _safe_url(raw: str) -> str:
    """Return a sanitised URL for Markdown link contexts, or '#' if invalid/empty.

    Accepts only http and https schemes.  Any parse error also falls back to '#'.

    Unlike HTML contexts, Markdown ``[text](url)`` expects raw URLs --
    HTML-entity encoding would produce literal ``&amp;`` in the rendered
    link.  We return the raw URL; the caller wraps it in
    ``<url>`` angle-bracket syntax for safety against ``)`` and spaces.
    """
    if not raw:
        return "#"
    try:
        parsed = urllib.parse.urlparse(raw)
        if parsed.scheme not in ("http", "https"):
            return "#"
    except Exception:  # noqa: BLE001
        return "#"
    return raw


def _extract_imdb_fields(result: ResultDict) -> dict[str, str]:
    """Extract and format IMDb identity fields."""
    title = _escape_markdown_text(result.get("imdb_title") or "Unknown")
    year = _escape_markdown_text(str(result.get("imdb_year") or ""))
    imdb_id = result.get("imdb_id") or ""
    rating = _escape_markdown_text(str(result.get("imdb_rating") or "?"))
    votes = _escape_markdown_text(str(result.get("imdb_votes") or "?"))
    return {"title": title, "year": year, "imdb_id": imdb_id, "rating": rating, "votes": votes}


def _escape_markdown_text(text: str) -> str:
    """Escape Markdown metacharacters so *text* is never interpreted as formatting.

    Escapes ``\\``, ``\\` ``, ``*``, ``_``, ``[``, ``]``, ``>``, ``\u003c``, and ``&``.
    These cover all Markdown inline formatting triggers plus HTML tag/entity
    openers, providing parity with ``html.escape()`` for Markdown contexts.
    """
    meta = "\\`*_[]>\u003c&"
    return "".join("\\" + c if c in meta else c for c in text)


def _extract_plot(result: ResultDict) -> str:
    """Extract the plot/outline field with fallback."""
    raw = result.get("imdb_plot_outline") or result.get("imdb_plot_summary")
    return _escape_markdown_text(raw or "\u2014")


def _extract_content_fields(result: ResultDict) -> dict[str, str]:
    """Extract and format cast, genre, and plot fields."""
    cast_list: list[str] = result.get("imdb_credits_cast_list") or []
    directors: list[str] = result.get("imdb_credits_director_list") or []
    genres: list[str] = result.get("imdb_genres_list") or []
    actors_str = _escape_markdown_text(", ".join(cast_list[:10]) or "\u2014")
    directors_str = _escape_markdown_text(", ".join(directors) or "\u2014")
    genres_str = _escape_markdown_text(", ".join(genres) or "\u2014")
    plot = _extract_plot(result)
    return {"actors_str": actors_str, "directors_str": directors_str, "genres_str": genres_str, "plot": plot}


def _extract_index_fields(result: ResultDict) -> dict[str, str]:
    """Extract and format index/torrent release fields."""
    index_title = _escape_markdown_text(result.get("index_title") or "")
    index_size_mb = _escape_markdown_text(str(result.get("index_size_mb") or "?"))
    index_details = _safe_url(result.get("index_details") or "")
    return {"index_title": index_title, "index_size_mb": index_size_mb, "index_details": index_details}


def _queue_status_str(config: Config) -> str:
    """Return human-readable queue status based on add_paused setting."""
    add_paused = config.torrent_client.qbittorrent.add_paused
    return "Paused" if add_paused is True else ("Started" if add_paused is False else "Unknown")


def _extract_body_fields(result: ResultDict, config: Config) -> dict[str, str]:
    """Extract and format all fields needed to render the notification body."""
    fields: dict[str, str] = {}
    fields.update(_extract_imdb_fields(result))
    fields.update(_extract_content_fields(result))
    fields.update(_extract_index_fields(result))
    fields["queue_status"] = _queue_status_str(config)
    details = result.get("result_details") or []
    fields["result_details_md"] = _format_result_details(details)
    fields["result_details_text"] = _format_result_details_text(details)
    return fields


def _build_links_section(f: dict[str, str], *, use_markdown: bool) -> str:
    """Build the 'Links:' section for the notification body.

    Returns an empty string when no IMDb link is available.
    Torrent index URLs are intentionally excluded because Jackett/Prowlarr
    proxy URLs (e.g. ``localhost:9696/api/...``) are not externally useful.
    """
    if not f.get("imdb_id"):
        return ""

    imdb_url = f"https://imdb.com/title/{f['imdb_id']}"
    if use_markdown:
        return f"**Links:** [IMDb]({imdb_url})"
    return f"Links: {imdb_url}"


def _build_markdown_body(f: dict[str, str]) -> str:
    """Build the Markdown notification body with a Links section at the bottom.

    Args:
        f: Extracted body fields from :func:`_extract_body_fields`.
    """
    lines = [
        f"**Status:** {f['queue_status']}",
        "",
        f"**Score:** {f['rating']} from {f['votes']} users",
        "",
        f"**Plot:** {f['plot']}",
        "",
        f"**Actors:** {f['actors_str']}",
        "",
        f"**Directors:** {f['directors_str']}",
        "",
        f"**Genres:** {f['genres_str']}",
        "",
        f"**Release:** {f['index_title']}",
        "",
        f"**Size:** {f['index_size_mb']} MB",
    ]

    links = _build_links_section(f, use_markdown=True)
    if links:
        lines.append("")
        lines.append(links)

    lines.append("")
    lines.append(f["result_details_md"])

    return "\n".join(lines)


def _build_text_body(f: dict[str, str]) -> str:
    """Build the plain-text notification body (no Markdown formatting).

    Args:
        f: Extracted body fields from :func:`_extract_body_fields`.
    """
    lines = [
        f"Status: {f['queue_status']}",
        "",
        f"Score: {f['rating']} from {f['votes']} users",
        "",
        f"Plot: {f['plot']}",
        "",
        f"Actors: {f['actors_str']}",
        "",
        f"Directors: {f['directors_str']}",
        "",
        f"Genres: {f['genres_str']}",
        "",
        f"Release: {f['index_title']}",
        "",
        f"Size: {f['index_size_mb']} MB",
    ]

    links = _build_links_section(f, use_markdown=False)
    if links:
        lines.append("")
        lines.append(links)

    lines.append("")
    lines.append(f["result_details_text"])

    return "\n".join(lines)


def _count_result_pf(details: list[str]) -> tuple[int, int]:
    """Return (passed, failed) counts from detail item prefixes."""
    passed = sum(1 for d in details if d.startswith("Passed"))
    failed = sum(1 for d in details if d.startswith("Failed"))
    return passed, failed


def _result_count_label(passed: int, failed: int, total: int) -> str:
    """Build the pass/fail count label for result details."""
    if not total:
        return "0 checks"
    if passed + failed == 0:
        return f"{total} items"
    if not failed:
        return f"{passed} checks passed"
    return f"{passed} passed, {failed} failed"


def _format_detail_md(item: str) -> str:
    """Format a single result detail item for Markdown output."""
    if item.startswith("Passed"):
        _, sep, rest = item.partition(": ")
        if sep:
            return f"- **Passed:** {_escape_markdown_text(rest)}"
        return f"- {_escape_markdown_text(item)}"
    if item.startswith("Failed"):
        _, sep, rest = item.partition(": ")
        if sep:
            return f"- **Failed:** {_escape_markdown_text(rest)}"
        return f"- {_escape_markdown_text(item)}"
    return f"- {_escape_markdown_text(item)}"


def _format_result_details(details: list[str]) -> str:
    """Format pipeline result_details as a Markdown list with bold label and inline count."""
    passed, failed = _count_result_pf(details)
    count_str = _result_count_label(passed, failed, len(details))
    items_parts = [_format_detail_md(item) for item in details]
    items = "\n".join(items_parts)
    if items_parts:
        items += "\n"
    sep = "\n\n" if items_parts else ""
    return f"**Result Details:** {count_str}{sep}{items}"


def _format_result_details_text(details: list[str]) -> str:
    """Format pipeline result_details as plain text (no markdown)."""
    passed, failed = _count_result_pf(details)
    count_str = _result_count_label(passed, failed, len(details))
    items = "\n".join(f"  - {item}" for item in details)
    sep = "\n\n" if details else ""
    return f"Result Details: {count_str}{sep}{items}"
