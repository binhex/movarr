"""Generic service health monitoring for movarr.

Tracks an unavailability streak for any named service in the persistent
kv_store and fires a single apprise alert once the streak duration exceeds
the configured threshold.  The streak resets when the service becomes
healthy again, allowing re-alerting on future outages.

This is the shared engine.  Callers supply the KV key prefix and the alert
threshold — the streak logic itself is written and tested once here.

Usage (from a thin wrapper module)::

    from movarr.service_health import check_service_health

    check_service_health(
        is_healthy=qbt.is_connected(),
        service_name="qBittorrent",
        kv_prefix="torrent_client",
        alert_hours=config.notification.torrent_client_alert_hours,
        db=db,
        config=config,
    )

KV keys used (keyed by *kv_prefix*):
  ``"{kv_prefix}.unavailable_since"`` -- ISO 8601 UTC start of current streak.
  ``"{kv_prefix}.alert_sent"``        -- ``"1"`` once alert fired for streak.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from loguru import logger

from movarr.notifications import send_service_alert

if TYPE_CHECKING:
    from movarr.config import Config
    from movarr.database import Database

__all__ = ["check_service_health"]


def check_service_health(
    is_healthy: bool,
    service_name: str,
    kv_prefix: str,
    alert_hours: float,
    db: Database,
    config: Config,
) -> None:
    """Update a service's health streak and fire an alert if threshold is exceeded.

    Safe to call from any thread; never raises.

    Args:
        is_healthy: ``True`` if the service responded normally this cycle;
            ``False`` if unreachable or otherwise misbehaving.
        service_name: Human-readable name for logs/notifications
            (e.g. ``"Prowlarr"``, ``"qBittorrent"``).
        kv_prefix: Dot-prefix for the KV store keys used by this service
            (e.g. ``"index_proxy"``, ``"torrent_client"``).
        alert_hours: Streak duration in hours before alerting.
            ``0`` or negative disables the feature for this service.
        db: Open database instance with kv_store support.
        config: Application configuration (used for apprise URLs).
    """
    try:
        if is_healthy:
            _reset_streak(kv_prefix, db)
        else:
            _on_unhealthy(service_name, kv_prefix, alert_hours, db, config)
    except Exception:
        logger.exception(
            "service_health.check_service_health failed unexpectedly for '{}'.",
            service_name,
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _reset_streak(kv_prefix: str, db: Database) -> None:
    """Clear streak state — called when the service is healthy."""
    db.kv_delete(f"{kv_prefix}.unavailable_since")
    db.kv_delete(f"{kv_prefix}.alert_sent")


def _on_unhealthy(
    service_name: str,
    kv_prefix: str,
    alert_hours: float,
    db: Database,
    config: Config,
) -> None:
    """Record or advance an unavailability streak; fire alert when threshold met."""
    if alert_hours <= 0:
        return

    key_since = f"{kv_prefix}.unavailable_since"
    key_alert = f"{kv_prefix}.alert_sent"
    since_raw = db.kv_get(key_since)
    now = datetime.datetime.now(datetime.UTC)

    if since_raw is None:
        db.kv_set(key_since, now.isoformat())
        logger.warning(
            "{} is unavailable — streak started at {}.",
            service_name,
            now.isoformat(),
        )
        return

    try:
        since = datetime.datetime.fromisoformat(since_raw)
    except ValueError:
        logger.warning(
            "Corrupt {} value '{}'; resetting streak for '{}'.",
            key_since,
            since_raw,
            service_name,
        )
        db.kv_set(key_since, now.isoformat())
        return

    elapsed_seconds = (now - since).total_seconds()
    elapsed_hours = elapsed_seconds / 3600.0

    if elapsed_seconds < alert_hours * 3600.0:
        logger.debug(
            "{} unavailability streak: {:.1f}h elapsed, threshold {:.1f}h not reached.",
            service_name,
            elapsed_hours,
            alert_hours,
        )
        return

    if db.kv_get(key_alert) == "1":
        logger.debug(
            "{} unavailability streak {:.1f}h: alert already sent; suppressing duplicate.",
            service_name,
            elapsed_hours,
        )
        return

    if not config.notification.apprise_urls:
        logger.warning(
            "{} unavailability streak {:.1f}h exceeded threshold — "
            "no apprise URLs configured, cannot send alert.",
            service_name,
            elapsed_hours,
        )
        return

    sent = send_service_alert(
        service_name=service_name,
        hours_elapsed=elapsed_hours,
        config=config,
    )
    if sent:
        db.kv_set(key_alert, "1")
