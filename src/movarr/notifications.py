"""Notification via apprise for movarr.

Sends an HTML summary when a torrent is queued.  Any apprise-supported
service URL can be specified in ``config.notification.apprise_urls``.

Fixes siphonator bug #2: ``", ".join(None)`` crashes — all list fields
are null-guarded here.
"""

from __future__ import annotations

import html
import urllib.parse
from typing import TYPE_CHECKING

import apprise
from loguru import logger

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
    safe_service = html.escape(service_name)
    safe_hours = html.escape(hours_str)
    subject = f"movarr: {service_name} has been unavailable for {hours_str}h \u2014 possible outage"
    body = (
        f"<p><strong>movarr service health alert</strong></p>"
        f"<p><strong>Service:</strong> {safe_service}</p>"
        f"<p><strong>Duration:</strong> Unavailable for {safe_hours} hours.</p>"
        f"<p>movarr will keep retrying every cycle. "
        f"Check that {safe_service} is running and accessible.</p>"
    )

    ap = apprise.Apprise()
    for url in urls:
        ap.add(url)

    try:
        sent = ap.notify(title=subject, body=body, body_format=apprise.NotifyFormat.HTML)
    except Exception:
        logger.exception("Service alert send failed.")
        return False

    if not sent:
        logger.warning("Service alert was not delivered (apprise returned False).")
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

    body = _build_body(result, config)
    subject = _build_subject(result)

    ap = apprise.Apprise()
    for url in urls:
        ap.add(url)

    try:
        sent = ap.notify(title=subject, body=body, body_format=apprise.NotifyFormat.HTML)
    except Exception:
        logger.exception("Notification send failed.")
        return False

    if not sent:
        logger.warning("Notification was not delivered (apprise returned False).")
        return False

    logger.info("Notification sent: {}", subject)
    return True


# Internal helpers


def _build_subject(result: ResultDict) -> str:
    """Build the plain-text notification subject line.

    The subject is used as the Apprise `title=` field (plain text),
    so we must NOT HTML-escape it — doing so produces visible entities
    like ``&amp;`` in delivered notifications.
    """
    title = result.get("imdb_title") or "Unknown"
    year = result.get("imdb_year") or ""
    rating = result.get("imdb_rating") or "?"
    year_str = f" ({year})" if year else ""
    return f"movarr: {title}{year_str} \u2014 IMDb {rating} \u2014 Queued"


def _build_body(result: ResultDict, config: Config) -> str:
    title = html.escape(result.get("imdb_title") or "Unknown")
    year = html.escape(str(result.get("imdb_year") or ""))
    imdb_id = result.get("imdb_id") or ""
    rating = html.escape(str(result.get("imdb_rating") or "?"))
    votes = html.escape(str(result.get("imdb_votes") or "?"))

    # Bug #2 fix: null-guard all list -> string conversions.
    cast_list: list[str] = result.get("imdb_credits_cast_list") or []
    directors: list[str] = result.get("imdb_credits_director_list") or []
    genres: list[str] = result.get("imdb_genres_list") or []

    actors_str = html.escape(", ".join(cast_list[:10]) or "\u2014")
    directors_str = html.escape(", ".join(directors) or "\u2014")
    genres_str = html.escape(", ".join(genres) or "\u2014")

    plot = html.escape(result.get("imdb_plot_outline") or result.get("imdb_plot_summary") or "\u2014")

    index_title = html.escape(result.get("index_title") or "")
    index_size_mb = html.escape(str(result.get("index_size_mb") or "?"))

    # Validate and sanitise URL-like fields.
    # 1. Accept only http/https scheme.
    # 2. HTML-escape the URL (with quote=True) so that quote characters in
    #    the raw URL cannot break out of the href attribute and inject
    #    arbitrary HTML attributes.
    def _safe_url(raw: str) -> str:
        if not raw:
            return "#"
        try:
            parsed = urllib.parse.urlparse(raw)
            if parsed.scheme not in ("http", "https"):
                return "#"
        except Exception:  # noqa: BLE001
            return "#"
        return html.escape(raw, quote=True)

    index_details = _safe_url(result.get("index_details") or "")

    add_paused = config.torrent_client.qbittorrent.add_paused
    queue_status = "Paused" if add_paused is True else ("Started" if add_paused is False else "Unknown")

    result_details_html = _format_result_details(result.get("result_details") or [])

    # Validate IMDb URL.
    imdb_url = f"https://imdb.com/title/{html.escape(imdb_id)}" if imdb_id else "#"

    return f"""
<p><strong>Title:</strong> <a href="{imdb_url}">{title} ({year})</a> \u2014 {rating} from {votes} users</p>
<p><strong>Plot:</strong> {plot}</p>
<p><strong>Actors:</strong> {actors_str}</p>
<p><strong>Directors:</strong> {directors_str}</p>
<p><strong>Genres:</strong> {genres_str}</p>
<p><strong>Queue Status:</strong> {queue_status}</p>
<p><strong>Release:</strong> <a href="{index_details}">{index_title}</a></p>
<p><strong>Size:</strong> {index_size_mb} MB</p>
<p><strong>Result Details:</strong></p>
{result_details_html}
"""


def _format_result_details(details: list[str]) -> str:
    """Format pipeline result_details as an HTML unordered list.

    Each entry is ``"Passed: msg"`` or ``"Failed: msg"`` (exactly one ``": "``
    separator), so they are always rendered as simple ``<li>`` items.
    """
    items = ""
    for item in details:
        items += f"<li>{html.escape(item)}</li>"
    return f"<ul>{items}</ul>"
