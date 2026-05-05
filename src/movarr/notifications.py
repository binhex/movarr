"""Notification via apprise for movarr.

Sends an HTML summary when a torrent is queued.  Any apprise-supported
service URL can be specified in ``config.notification.apprise_urls``.

Fixes siphonator bug #2: ``", ".join(None)`` crashes — all list fields
are null-guarded here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import apprise
from loguru import logger

if TYPE_CHECKING:
    from movarr.config import Config
    from movarr.models import ResultDict

__all__ = ["send_queued_notification"]


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
    title = result.get("imdb_title") or "Unknown"
    year = result.get("imdb_year") or ""
    rating = result.get("imdb_rating") or "?"
    year_str = f" ({year})" if year else ""
    return f"movarr: {title}{year_str} — IMDb {rating} — Queued"


def _build_body(result: ResultDict, config: Config) -> str:
    title = result.get("imdb_title") or "Unknown"
    year = result.get("imdb_year") or ""
    imdb_id = result.get("imdb_id") or ""
    rating = result.get("imdb_rating") or "?"
    votes = result.get("imdb_votes") or "?"

    # Bug #2 fix: null-guard all list → string conversions.
    cast_list: list[str] = result.get("imdb_credits_cast_list") or []
    directors: list[str] = result.get("imdb_credits_director_list") or []
    genres: list[str] = result.get("imdb_genres_list") or []

    actors_str = ", ".join(cast_list[:10]) or "—"
    directors_str = ", ".join(directors) or "—"
    genres_str = ", ".join(genres) or "—"

    plot = result.get("imdb_plot_outline") or result.get("imdb_plot_summary") or "—"

    index_title = result.get("index_title") or ""
    index_details = result.get("index_details") or "#"
    index_size_mb = result.get("index_size_mb") or "?"

    add_paused = config.torrent_client.qbittorrent.add_paused
    queue_status = "Paused" if add_paused is True else ("Started" if add_paused is False else "Unknown")

    result_details_html = _format_result_details(result.get("result_details") or [])

    imdb_url = f"https://imdb.com/title/{imdb_id}" if imdb_id else "#"

    return f"""
<p><strong>Title:</strong> <a href="{imdb_url}">{title} ({year})</a> — {rating} from {votes} users</p>
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
    items = ""
    for item in details:
        parts = item.split(": ", 2)
        if len(parts) == 3:  # noqa: PLR2004
            main, sub, detail = parts
            items += f"<li>{main}: {sub}<ul><li>{detail}</li></ul></li>"
        else:
            items += f"<li>{item}</li>"
    return f"<ul>{items}</ul>"
