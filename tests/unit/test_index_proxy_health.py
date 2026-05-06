"""Tests for the index_proxy_health wrapper module."""
from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import patch

from movarr.config import Config, NotificationConfig
from movarr.database import Database
from movarr.index_proxy_health import check_and_notify


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
