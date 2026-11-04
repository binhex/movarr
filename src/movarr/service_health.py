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
        is_healthy=qbt.is_reachable(),
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
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from movarr.notifications import send_service_alert

_SECONDS_PER_HOUR: float = 3600.0

if TYPE_CHECKING:
    from movarr.config import Config
    from movarr.database import Database


@dataclass
class _ServiceHealthCtx:
    service_name: str
    kv_prefix: str
    alert_hours: float
    db: Database
    config: Config


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
            _on_unhealthy(_ServiceHealthCtx(service_name, kv_prefix, alert_hours, db, config))
    except Exception:
        logger.exception(
            "service_health.check_service_health failed unexpectedly for '{}'.",
            service_name,
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _threshold_reached(ctx: _ServiceHealthCtx, elapsed_hours: float) -> bool:
    """Return True when the streak has passed the configured alert hours."""
    if elapsed_hours >= ctx.alert_hours:
        return True
    logger.debug(
        "{} unavailability streak: {:.1f}h elapsed, threshold {:.1f}h not reached.",
        ctx.service_name,
        elapsed_hours,
        ctx.alert_hours,
    )
    return False


def _record_suppressed_alert(ctx: _ServiceHealthCtx, key_alert: str, elapsed_hours: float) -> None:
    """Note that the threshold was hit but no Apprise URL is configured."""
    if ctx.db.kv_get(key_alert) == "suppressed":
        logger.debug("{} unavailability streak continues; alerting is disabled.", ctx.service_name)
        return
    logger.warning(
        "{} unavailability streak {:.1f}h exceeded threshold — no apprise URLs configured, "
        "cannot send alert (further occurrences are logged at debug).",
        ctx.service_name,
        elapsed_hours,
    )
    ctx.db.kv_set(key_alert, "suppressed")


def _reset_streak(kv_prefix: str, db: Database) -> None:
    """Clear streak state — called when the service is healthy."""
    db.kv_delete(f"{kv_prefix}.unavailable_since")
    db.kv_delete(f"{kv_prefix}.alert_sent")


def _parse_streak_start(ctx: _ServiceHealthCtx, since_raw: str, now: datetime.datetime) -> datetime.datetime | None:
    """Parse the streak-start timestamp; return the datetime or None to abort (corrupt)."""
    try:
        since = datetime.datetime.fromisoformat(since_raw)
        if since.tzinfo is None:
            raise ValueError(f"timezone-naive timestamp: {since_raw!r}")
        if since > now:
            # A future start point (clock skew, restored backup) would freeze the
            # streak below the alert threshold forever; treat it as corrupt and
            # restart the window, matching the circuit breaker's behaviour.
            raise ValueError(f"future timestamp: {since_raw!r}")
        return since
    except ValueError:
        key_since = f"{ctx.kv_prefix}.unavailable_since"
        logger.warning(
            "Corrupt {} value '{}'; resetting streak for '{}'.",
            key_since,
            since_raw,
            ctx.service_name,
        )
        ctx.db.kv_set(key_since, now.isoformat())
        # The new streak must be able to alert on its own merits.
        ctx.db.kv_delete(f"{ctx.kv_prefix}.alert_sent")
        return None


def _claim_alert(ctx: _ServiceHealthCtx, key_alert: str) -> bool:
    """Atomically claim the alert slot for this streak.

    Returns True when this caller won the race and should send the alert.
    """
    if ctx.db.kv_get(key_alert) == "suppressed":
        # Alerting was disabled earlier in this streak; URLs have since been
        # configured, so clear the marker and allow the alert through.
        ctx.db.kv_delete(key_alert)
    try:
        return bool(ctx.db.kv_set_if_absent(key_alert, "1"))
    except Exception:  # noqa: BLE001
        logger.debug("Could not claim the alert slot; continuing without suppression.")
        return True


def _on_unhealthy(ctx: _ServiceHealthCtx) -> None:
    """Record or advance an unavailability streak; fire alert when threshold met."""
    if ctx.alert_hours <= 0:
        # Alerting is disabled: drop any state from a previous enablement so a
        # re-enable cannot fire an immediately inflated "unavailable for Nh".
        ctx.db.kv_delete(f"{ctx.kv_prefix}.unavailable_since")
        ctx.db.kv_delete(f"{ctx.kv_prefix}.alert_sent")
        return

    key_since = f"{ctx.kv_prefix}.unavailable_since"
    key_alert = f"{ctx.kv_prefix}.alert_sent"
    since_raw = ctx.db.kv_get(key_since)
    now = datetime.datetime.now(datetime.UTC)

    if since_raw is None:
        ctx.db.kv_set(key_since, now.isoformat())
        logger.warning(
            "{} is unavailable — streak started at {}.",
            ctx.service_name,
            now.isoformat(),
        )
        return

    since = _parse_streak_start(ctx, since_raw, now)
    if since is None:
        return

    elapsed_hours = (now - since).total_seconds() / _SECONDS_PER_HOUR
    if not _threshold_reached(ctx, elapsed_hours) or _already_alerted(ctx, key_alert, elapsed_hours):
        return
    if not ctx.config.notification.apprise_urls:
        _record_suppressed_alert(ctx, key_alert, elapsed_hours)
        return
    _send_claimed_alert(ctx, key_alert, elapsed_hours)


def _already_alerted(ctx: _ServiceHealthCtx, key_alert: str, elapsed_hours: float) -> bool:
    """Return True when this streak already delivered its alert."""
    if ctx.db.kv_get(key_alert) != "1":
        return False
    logger.debug(
        "{} unavailability streak {:.1f}h: alert already sent; suppressing duplicate.",
        ctx.service_name,
        elapsed_hours,
    )
    return True


def _send_claimed_alert(ctx: _ServiceHealthCtx, key_alert: str, elapsed_hours: float) -> None:
    """Claim the alert slot, send the alert, and release the claim on failure."""
    # Claim before sending: queue-management and post-processing run on the same
    # 5-minute wall clock, so both threads can reach this point in the same
    # second and each send an identical outage notification.
    if not _claim_alert(ctx, key_alert):
        logger.debug("{} alert already claimed by another task; suppressing.", ctx.service_name)
        return
    try:
        sent = send_service_alert(
            service_name=ctx.service_name,
            hours_elapsed=elapsed_hours,
            config=ctx.config,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Sending the {} alert raised; releasing the claim.", ctx.service_name)
        sent = False
    if not sent:
        # Release the claim so the next cycle can retry the delivery.
        ctx.db.kv_delete(key_alert)
