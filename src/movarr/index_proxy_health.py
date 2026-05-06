"""Index proxy health monitoring for movarr.

Tracks a zero-results streak in the persistent kv_store and sends a single
alert notification once the streak duration exceeds the configured threshold.
The streak resets as soon as the proxy returns results again, allowing
re-alerting on future outages.

KV store keys used:
  ``"index_proxy.zero_results_since"`` -- ISO 8601 UTC start of the current
      zero-results streak.  Absent means the proxy is healthy.
  ``"index_proxy.alert_sent"``         -- ``"1"`` once an alert has been sent
      for the current streak.  Absent means not yet sent.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from loguru import logger

from movarr.notifications import send_index_proxy_alert

if TYPE_CHECKING:
    from movarr.config import Config
    from movarr.database import Database

__all__ = ["check_and_notify"]

_KEY_SINCE = "index_proxy.zero_results_since"
_KEY_ALERT_SENT = "index_proxy.alert_sent"


def check_and_notify(
    has_results: bool,
    proxy_name: str,
    db: Database,
    config: Config,
) -> None:
    """Update the health streak and fire an alert if the threshold is exceeded.

    Call this once per search run, after all criteria tiers have been
    processed.  Safe to call from any thread; never raises.

    Args:
        has_results: ``True`` if the proxy yielded at least one raw result
            across all criteria tiers; ``False`` if every tier returned
            nothing (including unreachable / HTTP-error cases).
        proxy_name: Human-readable proxy name for log and notification
            messages (e.g. ``"Prowlarr"`` or ``"Jackett"``).
        db: Open database instance with kv_store support.
        config: Application configuration.
    """
    try:
        if has_results:
            _reset_streak(db)
        else:
            _on_zero_results(proxy_name, db, config)
    except Exception:
        logger.exception("index_proxy_health.check_and_notify failed unexpectedly.")


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _reset_streak(db: Database) -> None:
    """Clear all streak state — called when the proxy returns results."""
    db.kv_delete(_KEY_SINCE)
    db.kv_delete(_KEY_ALERT_SENT)


def _on_zero_results(proxy_name: str, db: Database, config: Config) -> None:
    """Record or advance a zero-results streak; fire alert when due."""
    alert_hours = config.notification.index_proxy_alert_hours
    if alert_hours <= 0:
        return

    since_raw = db.kv_get(_KEY_SINCE)
    now = datetime.datetime.now(datetime.UTC)

    if since_raw is None:
        # Start of a new streak.
        db.kv_set(_KEY_SINCE, now.isoformat())
        logger.warning(
            "{} returned no results — zero-results streak started at {}.",
            proxy_name,
            now.isoformat(),
        )
        return

    # Streak already in progress — check whether threshold is exceeded.
    try:
        since = datetime.datetime.fromisoformat(since_raw)
    except ValueError:
        logger.warning(
            "Corrupt {} value '{}'; resetting streak.",
            _KEY_SINCE,
            since_raw,
        )
        db.kv_set(_KEY_SINCE, now.isoformat())
        return

    elapsed_seconds = (now - since).total_seconds()
    elapsed_hours = elapsed_seconds / 3600.0
    threshold_seconds = alert_hours * 3600.0

    if elapsed_seconds < threshold_seconds:
        logger.debug(
            "{} zero-results streak: {:.1f}h elapsed, threshold {:.1f}h not reached.",
            proxy_name,
            elapsed_hours,
            alert_hours,
        )
        return

    # Threshold exceeded — alert if not already sent and URLs are configured.
    if db.kv_get(_KEY_ALERT_SENT) == "1":
        logger.debug(
            "{} zero-results streak {:.1f}h: alert already sent; suppressing duplicate.",
            proxy_name,
            elapsed_hours,
        )
        return

    if not config.notification.apprise_urls:
        logger.warning(
            "{} zero-results streak {:.1f}h exceeded threshold — "
            "no apprise URLs configured, cannot send alert.",
            proxy_name,
            elapsed_hours,
        )
        return

    sent = send_index_proxy_alert(
        proxy_name=proxy_name,
        hours_elapsed=elapsed_hours,
        config=config,
    )
    if sent:
        db.kv_set(_KEY_ALERT_SENT, "1")
