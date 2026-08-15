"""Index proxy health monitoring for movarr.

Thin wrapper around :mod:`movarr.service_health`.  All streak logic lives
in the generic engine; this module provides the stable public API used by
:mod:`movarr.search`.

Also provides the search circuit breaker: when a search request errors
(e.g. Prowlarr read timeout), the circuit opens for ``circuit_open_minutes``
so subsequent search cycles fast-fail instead of burning the full retry
budget.  The circuit reopens automatically once the window expires, so
movarr resumes as soon as the proxy recovers — it never gives up or exits.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from loguru import logger

from movarr.service_health import check_service_health

if TYPE_CHECKING:
    from movarr.config import Config
    from movarr.database import Database

__all__ = ["check_and_notify", "record_search_failure", "record_search_success", "is_search_circuit_open"]

_KV_PREFIX = "index_proxy"
_KV_SEARCH_FAILED_AT = f"{_KV_PREFIX}.search_failed_at"


def check_and_notify(
    has_results: bool,
    proxy_name: str,
    db: Database,
    config: Config,
) -> None:
    """Update index proxy health streak and alert if threshold exceeded.

    Args:
        has_results: ``True`` if the proxy yielded any raw results this run;
            ``False`` if unreachable or returned nothing.
        proxy_name: Human-readable proxy name (e.g. ``"Prowlarr"``).
        db: Open database instance.
        config: Application configuration.
    """
    check_service_health(
        is_healthy=has_results,
        service_name=proxy_name,
        kv_prefix=_KV_PREFIX,
        alert_hours=config.notification.index_proxy_alert_hours,
        db=db,
        config=config,
    )


def record_search_failure(db: Database) -> None:
    """Open the search circuit by recording the current time as the failure point.

    Safe to call from any thread; never raises.

    Args:
        db: Open database instance with kv_store support.
    """
    try:
        db.kv_set(_KV_SEARCH_FAILED_AT, datetime.datetime.now(datetime.UTC).isoformat())
    except Exception:
        logger.exception("Failed to record search failure in KV store.")


def record_search_success(db: Database) -> None:
    """Close the search circuit by clearing the recorded failure timestamp.

    Safe to call from any thread; never raises.

    Args:
        db: Open database instance with kv_store support.
    """
    try:
        db.kv_delete(_KV_SEARCH_FAILED_AT)
    except Exception:
        logger.exception("Failed to clear search circuit in KV store.")


def is_search_circuit_open(db: Database, config: Config) -> bool:
    """Return True if a recent search failure should cause fast-fail.

    The circuit is open while the recorded failure timestamp is younger than
    ``config.index_proxy.circuit_open_minutes``.  A corrupt, missing, or
    future timestamp never keeps the circuit open.

    Safe to call from any thread; never raises.

    Args:
        db: Open database instance with kv_store support.
        config: Application configuration.
    """
    try:
        raw = db.kv_get(_KV_SEARCH_FAILED_AT)
        if not isinstance(raw, str):
            if raw is not None:
                db.kv_delete(_KV_SEARCH_FAILED_AT)
            return False
        try:
            failed_at = datetime.datetime.fromisoformat(raw)
            if failed_at.tzinfo is None:
                raise ValueError(f"timezone-naive timestamp: {raw!r}")
        except ValueError:
            db.kv_delete(_KV_SEARCH_FAILED_AT)
            return False
        now = datetime.datetime.now(datetime.UTC)
        if failed_at > now:
            db.kv_delete(_KV_SEARCH_FAILED_AT)
            return False
        window = datetime.timedelta(minutes=config.index_proxy.circuit_open_minutes)
        return now - failed_at < window
    except Exception:
        logger.exception("Failed to check search circuit state in KV store.")
        return False
