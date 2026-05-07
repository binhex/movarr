"""Tests for the generic service_health streak engine."""

from __future__ import annotations

import datetime
from pathlib import Path  # noqa: TC003
from unittest.mock import patch

from movarr.config import Config, NotificationConfig
from movarr.database import Database
from movarr.service_health import check_service_health

_PREFIX = "test_svc"


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


def _past_iso(hours_ago: float) -> str:
    dt = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=hours_ago)
    return dt.isoformat()


class TestFeatureDisabled:
    def test_zero_alert_hours_no_results_no_db_write(self, tmp_path: Path) -> None:
        """alert_hours=0 means no KV writes ever."""
        db = _db(tmp_path)
        check_service_health(False, "SVC", _PREFIX, 0, db, _config(0))
        assert db.kv_get(f"{_PREFIX}.unavailable_since") is None

    def test_zero_alert_hours_healthy_no_db_write(self, tmp_path: Path) -> None:
        """alert_hours=0: healthy call writes nothing."""
        db = _db(tmp_path)
        check_service_health(True, "SVC", _PREFIX, 0, db, _config(0))
        assert db.kv_get(f"{_PREFIX}.unavailable_since") is None


class TestStreakReset:
    def test_healthy_clears_streak_and_alert_sent(self, tmp_path: Path) -> None:
        """is_healthy=True removes both KV keys."""
        db = _db(tmp_path)
        db.kv_set(f"{_PREFIX}.unavailable_since", "2026-01-01T00:00:00+00:00")
        db.kv_set(f"{_PREFIX}.alert_sent", "1")
        check_service_health(True, "SVC", _PREFIX, 2.0, db, _config(2.0, ["ntfy://t"]))
        assert db.kv_get(f"{_PREFIX}.unavailable_since") is None
        assert db.kv_get(f"{_PREFIX}.alert_sent") is None

    def test_healthy_with_no_existing_streak_is_noop(self, tmp_path: Path) -> None:
        """is_healthy=True with no streak writes nothing."""
        db = _db(tmp_path)
        check_service_health(True, "SVC", _PREFIX, 2.0, db, _config(2.0, ["ntfy://t"]))
        assert db.kv_get(f"{_PREFIX}.unavailable_since") is None


class TestStreakStart:
    def test_first_unhealthy_call_writes_timestamp(self, tmp_path: Path) -> None:
        """First unhealthy call records unavailable_since."""
        db = _db(tmp_path)
        check_service_health(False, "SVC", _PREFIX, 2.0, db, _config(2.0, ["ntfy://t"]))
        assert db.kv_get(f"{_PREFIX}.unavailable_since") is not None

    def test_first_unhealthy_call_does_not_alert(self, tmp_path: Path) -> None:
        """First unhealthy call does not fire alert — threshold not reached."""
        db = _db(tmp_path)
        with patch("movarr.service_health.send_service_alert") as mock_alert:
            check_service_health(False, "SVC", _PREFIX, 2.0, db, _config(2.0, ["ntfy://t"]))
        mock_alert.assert_not_called()

    def test_subsequent_unhealthy_does_not_reset_start_time(self, tmp_path: Path) -> None:
        """Subsequent unhealthy calls do not overwrite the original timestamp."""
        db = _db(tmp_path)
        check_service_health(False, "SVC", _PREFIX, 2.0, db, _config(2.0, ["ntfy://t"]))
        first_ts = db.kv_get(f"{_PREFIX}.unavailable_since")
        check_service_health(False, "SVC", _PREFIX, 2.0, db, _config(2.0, ["ntfy://t"]))
        assert db.kv_get(f"{_PREFIX}.unavailable_since") == first_ts

    def test_corrupt_timestamp_resets_streak(self, tmp_path: Path) -> None:
        """Corrupt unavailable_since is replaced with a fresh timestamp; no alert fires."""
        db = _db(tmp_path)
        db.kv_set(f"{_PREFIX}.unavailable_since", "not-a-timestamp")
        with patch("movarr.service_health.send_service_alert") as mock_alert:
            check_service_health(False, "SVC", _PREFIX, 2.0, db, _config(2.0, ["ntfy://t"]))
        ts = db.kv_get(f"{_PREFIX}.unavailable_since")
        assert ts is not None
        assert ts != "not-a-timestamp"
        mock_alert.assert_not_called()


class TestAlertFiring:
    def test_alert_fires_when_threshold_exceeded(self, tmp_path: Path) -> None:
        """Alert is sent when streak >= alert_hours and alert_sent not set."""
        db = _db(tmp_path)
        db.kv_set(f"{_PREFIX}.unavailable_since", _past_iso(3.0))
        with patch("movarr.service_health.send_service_alert", return_value=True) as mock_alert:
            check_service_health(False, "SVC", _PREFIX, 2.0, db, _config(2.0, ["ntfy://t"]))
        mock_alert.assert_called_once()
        assert mock_alert.call_args.kwargs["service_name"] == "SVC"

    def test_alert_sets_alert_sent_flag(self, tmp_path: Path) -> None:
        """After firing, alert_sent is written to DB."""
        db = _db(tmp_path)
        db.kv_set(f"{_PREFIX}.unavailable_since", _past_iso(3.0))
        with patch("movarr.service_health.send_service_alert", return_value=True):
            check_service_health(False, "SVC", _PREFIX, 2.0, db, _config(2.0, ["ntfy://t"]))
        assert db.kv_get(f"{_PREFIX}.alert_sent") == "1"

    def test_alert_not_repeated_once_sent(self, tmp_path: Path) -> None:
        """Alert is suppressed when alert_sent is already set."""
        db = _db(tmp_path)
        db.kv_set(f"{_PREFIX}.unavailable_since", _past_iso(5.0))
        db.kv_set(f"{_PREFIX}.alert_sent", "1")
        with patch("movarr.service_health.send_service_alert") as mock_alert:
            check_service_health(False, "SVC", _PREFIX, 2.0, db, _config(2.0, ["ntfy://t"]))
        mock_alert.assert_not_called()

    def test_alert_not_fired_below_threshold(self, tmp_path: Path) -> None:
        """Alert is not sent when elapsed time is below alert_hours."""
        db = _db(tmp_path)
        db.kv_set(f"{_PREFIX}.unavailable_since", _past_iso(0.5))
        with patch("movarr.service_health.send_service_alert") as mock_alert:
            check_service_health(False, "SVC", _PREFIX, 2.0, db, _config(2.0, ["ntfy://t"]))
        mock_alert.assert_not_called()

    def test_alert_not_fired_when_no_urls(self, tmp_path: Path) -> None:
        """Alert is not sent when apprise_urls is empty."""
        db = _db(tmp_path)
        db.kv_set(f"{_PREFIX}.unavailable_since", _past_iso(5.0))
        with patch("movarr.service_health.send_service_alert") as mock_alert:
            check_service_health(False, "SVC", _PREFIX, 2.0, db, _config(2.0, urls=[]))
        mock_alert.assert_not_called()

    def test_failed_alert_does_not_set_alert_sent(self, tmp_path: Path) -> None:
        """When send_service_alert returns False, alert_sent is not written (retry next cycle)."""
        db = _db(tmp_path)
        db.kv_set(f"{_PREFIX}.unavailable_since", _past_iso(3.0))
        with patch("movarr.service_health.send_service_alert", return_value=False):
            check_service_health(False, "SVC", _PREFIX, 2.0, db, _config(2.0, ["ntfy://t"]))
        assert db.kv_get(f"{_PREFIX}.alert_sent") is None

    def test_new_outage_alerts_after_streak_reset(self, tmp_path: Path) -> None:
        """After a streak reset, a fresh outage can trigger a new alert."""
        db = _db(tmp_path)
        cfg = _config(2.0, ["ntfy://t"])
        db.kv_set(f"{_PREFIX}.unavailable_since", _past_iso(3.0))
        with patch("movarr.service_health.send_service_alert", return_value=True) as m1:
            check_service_health(False, "SVC", _PREFIX, 2.0, db, cfg)
        m1.assert_called_once()
        check_service_health(True, "SVC", _PREFIX, 2.0, db, cfg)
        assert db.kv_get(f"{_PREFIX}.alert_sent") is None
        db.kv_set(f"{_PREFIX}.unavailable_since", _past_iso(3.0))
        with patch("movarr.service_health.send_service_alert", return_value=True) as m2:
            check_service_health(False, "SVC", _PREFIX, 2.0, db, cfg)
        m2.assert_called_once()

    def test_never_raises(self, tmp_path: Path) -> None:
        """check_service_health never raises even when internals fail."""
        db = _db(tmp_path)
        db.kv_set(f"{_PREFIX}.unavailable_since", _past_iso(5.0))
        with patch("movarr.service_health.send_service_alert", side_effect=RuntimeError("boom")):
            check_service_health(False, "SVC", _PREFIX, 2.0, db, _config(2.0, ["ntfy://t"]))
