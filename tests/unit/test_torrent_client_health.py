"""Tests for the torrent_client_health wrapper module."""

from __future__ import annotations

import datetime
from pathlib import Path  # noqa: TC003
from unittest.mock import patch

from movarr.config import Config, NotificationConfig, QbittorrentConfig, TorrentClientConfig
from movarr.database import Database
from movarr.torrent_client_health import check_and_notify


def _db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


def _config(alert_hours: float, selected: str = "qbittorrent", urls: list[str] | None = None) -> Config:
    config = Config()
    return config.model_copy(
        update={
            "notification": NotificationConfig(
                apprise_urls=urls or [],
                torrent_client_alert_hours=alert_hours,
            ),
            "torrent_client": TorrentClientConfig(
                selected=selected,
                qbittorrent=QbittorrentConfig(),
            ),
        }
    )


class TestTorrentClientHealthWrapper:
    """check_and_notify() delegates to check_service_health() with correct arguments."""

    def test_delegates_unreachable_call(self, tmp_path: Path) -> None:
        """is_reachable=False delegates with is_healthy=False and torrent_client prefix."""
        db = _db(tmp_path)
        config = _config(2.0, urls=["ntfy://t"])
        with patch("movarr.torrent_client_health.check_service_health") as mock_engine:
            check_and_notify(is_reachable=False, db=db, config=config)
        mock_engine.assert_called_once_with(
            is_healthy=False,
            service_name="qBittorrent",
            kv_prefix="torrent_client",
            alert_hours=2.0,
            db=db,
            config=config,
        )

    def test_delegates_reachable_call(self, tmp_path: Path) -> None:
        """is_reachable=True delegates with is_healthy=True."""
        db = _db(tmp_path)
        config = _config(2.0, urls=["ntfy://t"])
        with patch("movarr.torrent_client_health.check_service_health") as mock_engine:
            check_and_notify(is_reachable=True, db=db, config=config)
        mock_engine.assert_called_once_with(
            is_healthy=True,
            service_name="qBittorrent",
            kv_prefix="torrent_client",
            alert_hours=2.0,
            db=db,
            config=config,
        )

    def test_display_name_qbittorrent(self, tmp_path: Path) -> None:
        """qbittorrent selected maps to display name 'qBittorrent'."""
        db = _db(tmp_path)
        config = _config(2.0, selected="qbittorrent")
        with patch("movarr.torrent_client_health.check_service_health") as mock_engine:
            check_and_notify(is_reachable=False, db=db, config=config)
        assert mock_engine.call_args.kwargs["service_name"] == "qBittorrent"

    def test_end_to_end_alert_fires(self, tmp_path: Path) -> None:
        """Integration: unreachable streak exceeds threshold -> apprise called."""
        db = _db(tmp_path)
        config = _config(2.0, urls=["ntfy://t"])
        past = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=3)).isoformat()
        db.kv_set("torrent_client.unavailable_since", past)
        with patch("movarr.service_health.send_service_alert", return_value=True) as mock_alert:
            check_and_notify(is_reachable=False, db=db, config=config)
        mock_alert.assert_called_once()
        assert db.kv_get("torrent_client.alert_sent") == "1"
