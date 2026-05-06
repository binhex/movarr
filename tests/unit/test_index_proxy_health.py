"""Tests for src/movarr/index_proxy_health.py."""
from __future__ import annotations

import datetime
from typing import TYPE_CHECKING
from unittest.mock import patch

from movarr.config import Config, NotificationConfig
from movarr.database import Database
from movarr.index_proxy_health import check_and_notify

if TYPE_CHECKING:
    from pathlib import Path


def _db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


def _config(alert_hours: float | None, urls: list[str] | None = None) -> Config:
    config = Config()
    return config.model_copy(
        update={
            "notification": NotificationConfig(
                apprise_urls=urls or [],
                index_proxy_alert_hours=alert_hours,
            )
        }
    )


class TestCheckAndNotifyFeatureDisabled:
    """When the feature is disabled, nothing is written to the DB."""

    def test_no_alert_hours_configured_has_results(self, tmp_path: Path) -> None:
        """No DB writes when alert_hours is None and proxy has results."""
        db = _db(tmp_path)
        config = _config(alert_hours=None)
        check_and_notify(has_results=True, proxy_name="Prowlarr", db=db, config=config)
        assert db.kv_get("index_proxy.zero_results_since") is None

    def test_no_alert_hours_configured_no_results(self, tmp_path: Path) -> None:
        """No DB writes when alert_hours is None and proxy has no results."""
        db = _db(tmp_path)
        config = _config(alert_hours=None)
        check_and_notify(has_results=False, proxy_name="Prowlarr", db=db, config=config)
        assert db.kv_get("index_proxy.zero_results_since") is None

    def test_no_apprise_urls_no_results(self, tmp_path: Path) -> None:
        """When apprise_urls is empty the streak is still tracked but no notification sent."""
        db = _db(tmp_path)
        config = _config(alert_hours=2.0, urls=[])
        check_and_notify(has_results=False, proxy_name="Prowlarr", db=db, config=config)
        # Streak IS recorded even with no URLs (timer starts); alert just won't fire
        assert db.kv_get("index_proxy.zero_results_since") is not None


class TestCheckAndNotifyStreakReset:
    """When results are found the streak is cleared."""

    def test_results_found_clears_streak_and_alert_sent(self, tmp_path: Path) -> None:
        """has_results=True deletes zero_results_since and alert_sent keys."""
        db = _db(tmp_path)
        db.kv_set("index_proxy.zero_results_since", "2026-01-01T00:00:00+00:00")
        db.kv_set("index_proxy.alert_sent", "1")
        config = _config(alert_hours=2.0, urls=["ntfy://t"])
        check_and_notify(has_results=True, proxy_name="Prowlarr", db=db, config=config)
        assert db.kv_get("index_proxy.zero_results_since") is None
        assert db.kv_get("index_proxy.alert_sent") is None

    def test_results_found_with_no_existing_streak_is_noop(self, tmp_path: Path) -> None:
        """has_results=True with no existing streak does not write anything."""
        db = _db(tmp_path)
        config = _config(alert_hours=2.0, urls=["ntfy://t"])
        check_and_notify(has_results=True, proxy_name="Prowlarr", db=db, config=config)
        assert db.kv_get("index_proxy.zero_results_since") is None


class TestCheckAndNotifyStreakStart:
    """When zero results are first detected the streak start time is recorded."""

    def test_no_results_sets_zero_results_since(self, tmp_path: Path) -> None:
        """First zero-results call writes zero_results_since to DB."""
        db = _db(tmp_path)
        config = _config(alert_hours=2.0, urls=["ntfy://t"])
        check_and_notify(has_results=False, proxy_name="Prowlarr", db=db, config=config)
        assert db.kv_get("index_proxy.zero_results_since") is not None

    def test_no_results_does_not_alert_immediately(self, tmp_path: Path) -> None:
        """First zero-results call does NOT send an alert — threshold not reached."""
        db = _db(tmp_path)
        config = _config(alert_hours=2.0, urls=["ntfy://t"])
        with patch("movarr.index_proxy_health.send_index_proxy_alert") as mock_alert:
            check_and_notify(has_results=False, proxy_name="Prowlarr", db=db, config=config)
        mock_alert.assert_not_called()

    def test_subsequent_zero_results_does_not_reset_streak_start(self, tmp_path: Path) -> None:
        """Subsequent zero-results calls do not overwrite the original streak start time."""
        db = _db(tmp_path)
        config = _config(alert_hours=2.0, urls=["ntfy://t"])
        check_and_notify(has_results=False, proxy_name="Prowlarr", db=db, config=config)
        first_ts = db.kv_get("index_proxy.zero_results_since")
        check_and_notify(has_results=False, proxy_name="Prowlarr", db=db, config=config)
        assert db.kv_get("index_proxy.zero_results_since") == first_ts


class TestCheckAndNotifyAlertFiring:
    """Alert fires exactly once when threshold is exceeded."""

    def _past_iso(self, hours_ago: float) -> str:
        """Return an ISO UTC timestamp *hours_ago* hours in the past."""
        dt = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=hours_ago)
        return dt.isoformat()

    def test_alert_fires_when_threshold_exceeded(self, tmp_path: Path) -> None:
        """Alert is sent when streak exceeds alert_hours and alert_sent is not set."""
        db = _db(tmp_path)
        db.kv_set("index_proxy.zero_results_since", self._past_iso(3.0))
        config = _config(alert_hours=2.0, urls=["ntfy://t"])
        with patch("movarr.index_proxy_health.send_index_proxy_alert", return_value=True) as mock_alert:
            check_and_notify(has_results=False, proxy_name="Prowlarr", db=db, config=config)
        mock_alert.assert_called_once()
        _, kwargs = mock_alert.call_args
        assert kwargs["proxy_name"] == "Prowlarr"

    def test_alert_sets_alert_sent_flag(self, tmp_path: Path) -> None:
        """After firing, alert_sent key is written to DB."""
        db = _db(tmp_path)
        db.kv_set("index_proxy.zero_results_since", self._past_iso(3.0))
        config = _config(alert_hours=2.0, urls=["ntfy://t"])
        with patch("movarr.index_proxy_health.send_index_proxy_alert", return_value=True):
            check_and_notify(has_results=False, proxy_name="Prowlarr", db=db, config=config)
        assert db.kv_get("index_proxy.alert_sent") == "1"

    def test_alert_not_repeated_once_sent(self, tmp_path: Path) -> None:
        """Alert is NOT resent when alert_sent is already set."""
        db = _db(tmp_path)
        db.kv_set("index_proxy.zero_results_since", self._past_iso(5.0))
        db.kv_set("index_proxy.alert_sent", "1")
        config = _config(alert_hours=2.0, urls=["ntfy://t"])
        with patch("movarr.index_proxy_health.send_index_proxy_alert") as mock_alert:
            check_and_notify(has_results=False, proxy_name="Prowlarr", db=db, config=config)
        mock_alert.assert_not_called()

    def test_alert_not_fired_when_threshold_not_reached(self, tmp_path: Path) -> None:
        """Alert is NOT sent when elapsed time is below alert_hours."""
        db = _db(tmp_path)
        db.kv_set("index_proxy.zero_results_since", self._past_iso(0.5))
        config = _config(alert_hours=2.0, urls=["ntfy://t"])
        with patch("movarr.index_proxy_health.send_index_proxy_alert") as mock_alert:
            check_and_notify(has_results=False, proxy_name="Prowlarr", db=db, config=config)
        mock_alert.assert_not_called()

    def test_alert_not_fired_when_apprise_urls_empty(self, tmp_path: Path) -> None:
        """Alert is NOT sent when apprise_urls is empty, even after threshold exceeded."""
        db = _db(tmp_path)
        db.kv_set("index_proxy.zero_results_since", self._past_iso(5.0))
        config = _config(alert_hours=2.0, urls=[])
        with patch("movarr.index_proxy_health.send_index_proxy_alert") as mock_alert:
            check_and_notify(has_results=False, proxy_name="Prowlarr", db=db, config=config)
        mock_alert.assert_not_called()

    def test_new_outage_alerts_after_streak_reset(self, tmp_path: Path) -> None:
        """After a streak reset, a new outage can trigger a fresh alert."""
        db = _db(tmp_path)
        config = _config(alert_hours=2.0, urls=["ntfy://t"])
        # First outage: alert fires
        db.kv_set("index_proxy.zero_results_since", self._past_iso(3.0))
        with patch("movarr.index_proxy_health.send_index_proxy_alert", return_value=True) as mock_alert:
            check_and_notify(has_results=False, proxy_name="Prowlarr", db=db, config=config)
        mock_alert.assert_called_once()
        # Results return: streak reset
        check_and_notify(has_results=True, proxy_name="Prowlarr", db=db, config=config)
        assert db.kv_get("index_proxy.alert_sent") is None
        # Second outage: alert can fire again
        db.kv_set("index_proxy.zero_results_since", self._past_iso(3.0))
        with patch("movarr.index_proxy_health.send_index_proxy_alert", return_value=True) as mock_alert2:
            check_and_notify(has_results=False, proxy_name="Prowlarr", db=db, config=config)
        mock_alert2.assert_called_once()
