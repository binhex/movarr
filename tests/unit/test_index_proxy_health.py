"""Tests for the index_proxy_health wrapper module."""

from __future__ import annotations

import datetime
from pathlib import Path  # noqa: TC003
from unittest.mock import patch

from movarr.config import Config, NotificationConfig
from movarr.database import Database
from movarr.index_proxy_health import (
    _KV_SEARCH_FAILED_AT,
    check_and_notify,
    is_search_circuit_open,
    record_search_failure,
    record_search_success,
)


def _db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


def _config(alert_hours: float, urls: list[str] | None = None) -> Config:
    config = Config()
    return config.model_copy(
        update={
            "notification": NotificationConfig(
                apprise_urls=urls or [],
                index_proxy_alert_hours=alert_hours,
            )
        }
    )


class TestIndexProxyHealthWrapper:
    """check_and_notify() delegates to check_service_health() with correct arguments."""

    def test_delegates_unhealthy_call(self, tmp_path: Path) -> None:
        """has_results=False delegates with is_healthy=False and index_proxy prefix."""
        db = _db(tmp_path)
        config = _config(2.0, ["ntfy://t"])
        with patch("movarr.index_proxy_health.check_service_health") as mock_engine:
            check_and_notify(has_results=False, proxy_name="Prowlarr", db=db, config=config)
        mock_engine.assert_called_once_with(
            is_healthy=False,
            service_name="Prowlarr",
            kv_prefix="index_proxy",
            alert_hours=2.0,
            db=db,
            config=config,
        )

    def test_delegates_healthy_call(self, tmp_path: Path) -> None:
        """has_results=True delegates with is_healthy=True."""
        db = _db(tmp_path)
        config = _config(2.0, ["ntfy://t"])
        with patch("movarr.index_proxy_health.check_service_health") as mock_engine:
            check_and_notify(has_results=True, proxy_name="Jackett", db=db, config=config)
        mock_engine.assert_called_once_with(
            is_healthy=True,
            service_name="Jackett",
            kv_prefix="index_proxy",
            alert_hours=2.0,
            db=db,
            config=config,
        )

    def test_uses_config_alert_hours(self, tmp_path: Path) -> None:
        """alert_hours is read from config.notification.index_proxy_alert_hours."""
        db = _db(tmp_path)
        config = _config(alert_hours=5.0)
        with patch("movarr.index_proxy_health.check_service_health") as mock_engine:
            check_and_notify(has_results=False, proxy_name="Prowlarr", db=db, config=config)
        assert mock_engine.call_args.kwargs["alert_hours"] == 5.0  # noqa: PLR2004

    def test_end_to_end_alert_fires(self, tmp_path: Path) -> None:
        """Integration: streak exceeds threshold -> apprise called (engine not mocked)."""
        db = _db(tmp_path)
        config = _config(2.0, ["ntfy://t"])
        past = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=3)).isoformat()
        db.kv_set("index_proxy.unavailable_since", past)
        with patch("movarr.service_health.send_service_alert", return_value=True) as mock_alert:
            check_and_notify(has_results=False, proxy_name="Prowlarr", db=db, config=config)
        mock_alert.assert_called_once()
        assert db.kv_get("index_proxy.alert_sent") == "1"


class TestSearchCircuitBreaker:
    """Tests for the search circuit breaker (record/is_open helpers)."""

    def test_is_open_false_when_no_failure_recorded(self, tmp_path: Path) -> None:
        """No failure timestamp -> circuit is closed."""
        db = _db(tmp_path)
        config = _config(0.0)
        assert is_search_circuit_open(db, config) is False

    def test_is_open_false_when_failure_older_than_window(self, tmp_path: Path) -> None:
        """A failure older than circuit_open_minutes -> circuit closed (retry allowed)."""
        db = _db(tmp_path)
        config = Config()
        old = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=60)).isoformat()
        db.kv_set(_KV_SEARCH_FAILED_AT, old)
        assert is_search_circuit_open(db, config) is False

    def test_is_open_true_within_window(self, tmp_path: Path) -> None:
        """A recent failure -> circuit is open (fast-fail)."""
        db = _db(tmp_path)
        config = Config()
        recent = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1)).isoformat()
        db.kv_set(_KV_SEARCH_FAILED_AT, recent)
        assert is_search_circuit_open(db, config) is True

    def test_is_open_uses_configured_window(self, tmp_path: Path) -> None:
        """circuit_open_minutes config controls the open window."""
        db = _db(tmp_path)
        config = Config()
        config.index_proxy.circuit_open_minutes = 10
        recent = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)).isoformat()
        db.kv_set(_KV_SEARCH_FAILED_AT, recent)
        assert is_search_circuit_open(db, config) is True

        config.index_proxy.circuit_open_minutes = 1
        assert is_search_circuit_open(db, config) is False

    def test_is_open_ignores_corrupt_timestamp(self, tmp_path: Path) -> None:
        """A corrupt timestamp cannot keep the circuit open forever."""
        db = _db(tmp_path)
        config = Config()
        db.kv_set(_KV_SEARCH_FAILED_AT, "not-a-timestamp")
        assert is_search_circuit_open(db, config) is False

    def test_is_open_ignores_timezone_naive_timestamp(self, tmp_path: Path) -> None:
        """A timezone-naive ISO timestamp cannot keep the circuit open forever."""
        db = _db(tmp_path)
        config = Config()
        db.kv_set(_KV_SEARCH_FAILED_AT, "2025-01-01T12:00:00")
        assert is_search_circuit_open(db, config) is False
        assert db.kv_get(_KV_SEARCH_FAILED_AT) is None

    def test_is_open_ignores_future_timestamp(self, tmp_path: Path) -> None:
        """A future timestamp (clock skew) cannot keep the circuit open forever."""
        db = _db(tmp_path)
        config = Config()
        future = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)).isoformat()
        db.kv_set(_KV_SEARCH_FAILED_AT, future)
        assert is_search_circuit_open(db, config) is False
        assert db.kv_get(_KV_SEARCH_FAILED_AT) is None

    def test_record_failure_sets_timestamp(self, tmp_path: Path) -> None:
        """record_search_failure stores a parseable ISO timestamp."""
        db = _db(tmp_path)
        record_search_failure(db)
        raw = db.kv_get(_KV_SEARCH_FAILED_AT)
        assert raw is not None
        datetime.datetime.fromisoformat(raw)  # must parse
        assert is_search_circuit_open(db, Config()) is True

    def test_record_success_clears_timestamp(self, tmp_path: Path) -> None:
        """record_search_success removes the failure timestamp."""
        db = _db(tmp_path)
        record_search_failure(db)
        record_search_success(db)
        assert db.kv_get(_KV_SEARCH_FAILED_AT) is None

    def test_record_success_on_empty_db_is_noop(self, tmp_path: Path) -> None:
        """Clearing an already-clear circuit must not raise."""
        db = _db(tmp_path)
        record_search_success(db)
        assert db.kv_get(_KV_SEARCH_FAILED_AT) is None
