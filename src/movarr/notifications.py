"""Email notification via apprise for movarr.

Sends an HTML summary email when a torrent is queued.  Fixes siphonator bug #2:
``", ".join(None)`` crashes — all list fields are null-guarded here.
"""

from __future__ import annotations

from urllib.parse import quote

import apprise
from loguru import logger

from movarr.config import Config
from movarr.models import ResultDict

__all__ = ["send_queued_notification"]


def send_queued_notification(result: ResultDict, config: Config) -> bool:
    """Send an email notification for a newly queued torrent.

    Args:
        result: Fully-populated pipeline dict (index + IMDb metadata).
        config: Application configuration.

    Returns:
        ``True`` if the notification was sent without error, ``False`` otherwise.
    """
    email_cfg = config.notification.email
    if not email_cfg.enabled:
        logger.debug("Email notifications disabled; skipping.")
        return False

    body = _build_body(result, config)
    subject = _build_subject(result)

    url = _build_apprise_url(email_cfg)
    ap = apprise.Apprise()
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


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
    cast_list = result.get("imdb_credits_cast_list") or []
    directors = result.get("imdb_credits_director_list") or []
    genres = result.get("imdb_genres_list") or []

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
        if len(parts) == 3:
            main, sub, detail = parts
            items += f"<li>{main}: {sub}<ul><li>{detail}</li></ul></li>"
        else:
            items += f"<li>{item}</li>"
    return f"<ul>{items}</ul>"


def _build_apprise_url(email_cfg) -> str:
    """Build an apprise SMTP URL from the email config section."""
    scheme = "mailtos" if email_cfg.enable_tls or email_cfg.enable_ssl else "mailto"
    user = email_cfg.username or ""
    password = email_cfg.password or ""
    host = email_cfg.host
    port = email_cfg.port
    from_addr = email_cfg.from_address or ""
    to_addr = email_cfg.to_address or ""

    auth = ""
    if user and password:
        auth = f"{quote(user, safe='')}:{quote(password, safe='')}@"
    elif user:
        auth = f"{quote(user, safe='')}@"

    url = f"{scheme}://{auth}{host}:{port}/?from={quote(from_addr, safe='')}&to={quote(to_addr, safe='')}"
    return url
