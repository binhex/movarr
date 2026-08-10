# Torrent Client Health Alert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Alert via apprise when the configured torrent client (currently qBittorrent; future-proofed for others) has been unreachable for longer than a user-defined threshold.

**Architecture:** Extract the existing streak-tracking logic from `index_proxy_health.py` into a generic `service_health.py` engine parametrised by `kv_prefix` and `alert_hours`. Both `index_proxy_health.py` and the new `torrent_client_health.py` become thin wrappers (~5 lines each) over that engine. Future torrent clients (or any other monitored service) require only a new wrapper module and one config field — the streak logic is written and tested once. The notification function is also generalised so the same apprise machinery covers all services.

**Tech Stack:** Python 3.12, SQLAlchemy (SQLite), apprise, pydantic, loguru — all already in the project.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `src/movarr/service_health.py` | Generic streak-tracking engine |
| Modify | `src/movarr/index_proxy_health.py` | Thin wrapper over `service_health` |
| Modify | `src/movarr/notifications.py` | Add generic `send_service_alert()`; `send_index_proxy_alert()` delegates to it |
| Modify | `src/movarr/database.py` | DB migration v11→v12: rename orphaned `index_proxy.zero_results_since` key |
| Modify | `src/movarr/config.py` | Add `torrent_client_alert_hours`; migrate config v2.9.0→v2.10.0 |
| Create | `src/movarr/torrent_client_health.py` | Thin wrapper over `service_health` for torrent client |
| Modify | `src/movarr/search.py` | Wire torrent client health check alongside `qbt.is_connected()` |
| Create | `tests/unit/test_service_health.py` | Full tests for the generic engine |
| Modify | `tests/unit/test_index_proxy_health.py` | Slim down to wrapper-only coverage |
| Modify | `tests/unit/test_notifications.py` | Tests for `send_service_alert()` |
| Modify | `tests/unit/test_database.py` | Test for v12 key-rename migration |
| Modify | `tests/unit/test_config.py` | New field + v2.9→v2.10 migration tests |
| Create | `tests/unit/test_torrent_client_health.py` | Wrapper smoke tests |
| Modify | `tests/unit/test_search.py` | Torrent client health check call site tests |
| Modify | `README.md` | Document `torrent_client_alert_hours` |

---

## Key Design Decisions

### Generic KV key names

`service_health.py` derives its KV keys from `kv_prefix`:

- `{kv_prefix}.unavailable_since` — ISO 8601 UTC start of the current unhealthy streak
- `{kv_prefix}.alert_sent` — `"1"` once an alert has been fired for this streak

For index proxy: `index_proxy.unavailable_since`, `index_proxy.alert_sent`
For torrent client: `torrent_client.unavailable_since`, `torrent_client.alert_sent`

The old `index_proxy.zero_results_since` key (written by the previous implementation) is renamed by a DB migration (v12) to preserve streak continuity across upgrades. Without migration the streak would simply reset silently — the migration is clean-up, not a correctness fix.

### Generic notification

`send_service_alert(service_name, hours_elapsed, config)` replaces the bespoke
`send_index_proxy_alert()` internals. `send_index_proxy_alert()` is kept as a one-line
delegate so existing call sites remain valid.

### Torrent client display name

`config.torrent_client.selected` yields `"qbittorrent"` (lowercase). A small
`_CLIENT_DISPLAY_NAMES` dict in `torrent_client_health.py` maps this to `"qBittorrent"`.
Future clients add one entry to that dict.

### Where the health check lives

qBittorrent reachability is checked in `run_search()` (early return when `not qbt.is_connected()`).
The health check is wired there — the same place the index proxy health check lives.
`run_queue_management()` and `run_post_processing()` also check `qbt.is_connected()` but the
search task (default 30 min interval) provides sufficient detection frequency for an hours-scale alert.

---

## Task 1: Generalise `send_service_alert()` in `notifications.py`

**Files:**
- Modify: `src/movarr/notifications.py`
- Modify: `tests/unit/test_notifications.py`

### Context

Add `send_service_alert(service_name, hours_elapsed, config)` as the generic alert function.
Update `send_index_proxy_alert()` to delegate to it (no behaviour change; its public API and
tests remain valid). `service_health.py` (Task 2) will call `send_service_alert()` directly.

---

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_notifications.py`:

```python
class TestSendServiceAlert:
    """Tests for the generic send_service_alert() function."""

    def _make_config(self, urls: list[str]) -> Config:
        from movarr.config import NotificationConfig
        config = Config()
        return config.model_copy(update={"notification": NotificationConfig(apprise_urls=urls)})

    def test_returns_false_when_no_urls_configured(self) -> None:
        """Returns False immediately when apprise_urls is empty."""
        from movarr.notifications import send_service_alert
        config = self._make_config([])
        assert send_service_alert(service_name="qBittorrent", hours_elapsed=3.0, config=config) is False

    def test_calls_apprise_when_urls_configured(self) -> None:
        """Calls apprise.Apprise.notify() and returns True on success."""
        from unittest.mock import MagicMock, patch
        from movarr.notifications import send_service_alert
        config = self._make_config(["ntfy://t"])
        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            assert send_service_alert(service_name="qBittorrent", hours_elapsed=2.0, config=config) is True
        mock_ap.notify.assert_called_once()

    def test_subject_contains_service_name_and_hours(self) -> None:
        """Subject line includes service name and elapsed hours."""
        from unittest.mock import MagicMock, patch
        from movarr.notifications import send_service_alert
        config = self._make_config(["ntfy://t"])
        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            send_service_alert(service_name="qBittorrent", hours_elapsed=4.0, config=config)
        _, kwargs = mock_ap.notify.call_args
        assert "qBittorrent" in kwargs["title"]
        assert "4" in kwargs["title"]

    def test_returns_false_when_apprise_returns_false(self) -> None:
        """Returns False when apprise.notify() returns False."""
        from unittest.mock import MagicMock, patch
        from movarr.notifications import send_service_alert
        config = self._make_config(["ntfy://t"])
        mock_ap = MagicMock()
        mock_ap.notify.return_value = False
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            assert send_service_alert(service_name="qBittorrent", hours_elapsed=1.0, config=config) is False

    def test_returns_false_when_apprise_raises(self) -> None:
        """Returns False and does not propagate when apprise raises."""
        from unittest.mock import MagicMock, patch
        from movarr.notifications import send_service_alert
        config = self._make_config(["ntfy://t"])
        mock_ap = MagicMock()
        mock_ap.notify.side_effect = RuntimeError("boom")
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            assert send_service_alert(service_name="qBittorrent", hours_elapsed=1.0, config=config) is False


class TestSendIndexProxyAlertDelegates:
    """send_index_proxy_alert() must delegate to send_service_alert()."""

    def test_delegates_to_send_service_alert(self) -> None:
        """send_index_proxy_alert() calls send_service_alert() with the same args."""
        from unittest.mock import patch
        from movarr.notifications import send_index_proxy_alert
        config = Config()
        with patch("movarr.notifications.send_service_alert", return_value=True) as mock_generic:
            result = send_index_proxy_alert(proxy_name="Prowlarr", hours_elapsed=3.0, config=config)
        mock_generic.assert_called_once_with(
            service_name="Prowlarr", hours_elapsed=3.0, config=config
        )
        assert result is True
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_notifications.py::TestSendServiceAlert tests/unit/test_notifications.py::TestSendIndexProxyAlertDelegates -v 2>&1 | tail -15
```

Expected: all FAILED (`send_service_alert` not yet defined; delegation not yet wired)

- [ ] **Step 3: Implement changes in `notifications.py`**

Add `send_service_alert` to `__all__` and add the function **before** `send_index_proxy_alert`:

```python
__all__ = ["send_index_proxy_alert", "send_queued_notification", "send_service_alert"]


def send_service_alert(service_name: str, hours_elapsed: float, config: Config) -> bool:
    """Send an alert notification when a monitored service has been unavailable.

    Generic function used by all service health monitors (index proxy,
    torrent client, etc.).

    Args:
        service_name: Human-readable service name, e.g. ``"Prowlarr"`` or ``"qBittorrent"``.
        hours_elapsed: How many hours the unavailability streak has lasted.
        config: Application configuration.

    Returns:
        ``True`` if the notification was delivered, ``False`` otherwise (including
        when no apprise URLs are configured).
    """
    urls = config.notification.apprise_urls
    if not urls:
        logger.debug("No apprise URLs configured; skipping service alert.")
        return False

    hours_str = f"{hours_elapsed:.1f}"
    subject = f"movarr: {service_name} has been unavailable for {hours_str}h — possible outage"
    body = (
        f"<p><strong>movarr service health alert</strong></p>"
        f"<p><strong>Service:</strong> {service_name}</p>"
        f"<p><strong>Duration:</strong> Unavailable for {hours_str} hours.</p>"
        f"<p>movarr will keep retrying every cycle. "
        f"Check that {service_name} is running and accessible.</p>"
    )

    ap = apprise.Apprise()
    for url in urls:
        ap.add(url)

    try:
        sent = ap.notify(title=subject, body=body, body_format=apprise.NotifyFormat.HTML)
    except Exception:
        logger.exception("Service alert send failed.")
        return False

    if not sent:
        logger.warning("Service alert was not delivered (apprise returned False).")
        return False

    logger.warning("Service alert sent: {}", subject)
    return True
```

Update `send_index_proxy_alert()` to delegate:

```python
def send_index_proxy_alert(proxy_name: str, hours_elapsed: float, config: Config) -> bool:
    """Send an alert for an index proxy outage.

    Delegates to :func:`send_service_alert`.  Kept for backwards compatibility.

    Args:
        proxy_name: Human-readable proxy name, e.g. ``"Prowlarr"`` or ``"Jackett"``.
        hours_elapsed: How many hours the zero-results streak has lasted.
        config: Application configuration.

    Returns:
        ``True`` if the notification was delivered, ``False`` otherwise.
    """
    return send_service_alert(service_name=proxy_name, hours_elapsed=hours_elapsed, config=config)
```

- [ ] **Step 4: Run new tests — must all pass**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_notifications.py::TestSendServiceAlert tests/unit/test_notifications.py::TestSendIndexProxyAlertDelegates -v 2>&1 | tail -15
```

- [ ] **Step 5: Run full notifications suite — no regressions**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_notifications.py -v 2>&1 | tail -15
```

- [ ] **Step 6: Run linters**

```bash
cd /data/movarr && source .venv/bin/activate && uv run ruff check src/movarr/notifications.py tests/unit/test_notifications.py && uv run mypy src/movarr/notifications.py 2>&1 | tail -5
```

- [ ] **Step 7: Commit**

```bash
cd /data/movarr && git add src/movarr/notifications.py tests/unit/test_notifications.py
git commit -m "refactor(notifications): add generic send_service_alert(); send_index_proxy_alert() delegates to it"
```

---

## Task 2: Extract generic `service_health.py` + slim down `index_proxy_health.py`

**Files:**
- Create: `src/movarr/service_health.py`
- Modify: `src/movarr/index_proxy_health.py`
- Modify: `src/movarr/database.py` (DB migration v11→v12)
- Create: `tests/unit/test_service_health.py`
- Modify: `tests/unit/test_index_proxy_health.py`
- Modify: `tests/unit/test_database.py`

### Context

Move all streak logic from `index_proxy_health.py` into `service_health.py`.
`index_proxy_health.check_and_notify()` becomes a 1-call wrapper — its public API
and existing call site in `search.py` remain unchanged.

The old KV key `index_proxy.zero_results_since` (written by the previous implementation)
is renamed to `index_proxy.unavailable_since` by a DB migration so any in-flight streak
survives the upgrade. DB version bumps 11→12.

`test_service_health.py` carries the comprehensive branch tests. `test_index_proxy_health.py`
is trimmed to ~4 tests that verify the wrapper calls the engine correctly.

---

- [ ] **Step 1: Write failing tests for `service_health.py`**

Create `tests/unit/test_service_health.py`:

```python
"""Tests for the generic service_health streak engine."""
from __future__ import annotations

import datetime
from pathlib import Path
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
        update={"notification": NotificationConfig(apprise_urls=urls or [], index_proxy_alert_hours=alert_hours)}
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
        """First unhealthy call does not fire alert (threshold not reached)."""
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
        with patch("movarr.service_health.send_service_alert", side_effect=RuntimeError("boom")):
            db.kv_set(f"{_PREFIX}.unavailable_since", _past_iso(5.0))
            check_service_health(False, "SVC", _PREFIX, 2.0, db, _config(2.0, ["ntfy://t"]))
```

Also add a test for the DB key migration in `tests/unit/test_database.py`:

```python
def test_kv_rename_migration_v11_to_v12(self, tmp_path: Path) -> None:
    """v11->v12 migration renames index_proxy.zero_results_since to index_proxy.unavailable_since."""
    import sqlite3

    db_path = tmp_path / "legacy_v11.db"
    raw = sqlite3.connect(str(db_path))
    raw.execute("CREATE TABLE history (id INTEGER PRIMARY KEY, index_title TEXT)")
    raw.execute("CREATE TABLE kv_store (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    raw.execute(
        "INSERT INTO kv_store (key, value) VALUES ('index_proxy.zero_results_since', '2026-01-01T00:00:00+00:00')"
    )
    raw.execute("PRAGMA user_version = 11")
    raw.commit()
    raw.close()

    db = Database(db_path)
    assert db._get_user_version() == 12
    # Old key gone; new key carries the value forward.
    assert db.kv_get("index_proxy.zero_results_since") is None
    assert db.kv_get("index_proxy.unavailable_since") == "2026-01-01T00:00:00+00:00"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_service_health.py tests/unit/test_database.py::TestKvStore::test_kv_rename_migration_v11_to_v12 -v 2>&1 | tail -20
```

Expected: all FAILED (module does not exist yet, migration not yet added)

- [ ] **Step 3: Implement `service_health.py`**

```python
"""Generic service health monitoring for movarr.

Tracks an unavailability streak for any named service in the persistent
kv_store and fires a single apprise alert once the streak duration exceeds
the configured threshold.  The streak resets when the service becomes
healthy again, allowing re-alerting on future outages.

This is a shared engine.  Callers supply the KV key prefix and the alert
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
```

- [ ] **Step 4: Add DB migration v11→v12 in `database.py`**

Update version constants:

```python
_DB_VERSION = 12
_SCHEMA_V12_KV_KEY_RENAME = 12
```

Add migration block in `_upgrade()` after the `_SCHEMA_V11_KV_STORE` block:

```python
            if from_version < _SCHEMA_V12_KV_KEY_RENAME:
                conn.execute(
                    text(
                        "UPDATE kv_store SET key = 'index_proxy.unavailable_since' "
                        "WHERE key = 'index_proxy.zero_results_since'"
                    )
                )
                conn.commit()
```

- [ ] **Step 5: Refactor `index_proxy_health.py`** to a thin wrapper

```python
"""Index proxy health monitoring for movarr.

Thin wrapper around :mod:`movarr.service_health`.  All streak logic lives
in the generic engine; this module provides the stable public API used by
:mod:`movarr.search`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from movarr.service_health import check_service_health

if TYPE_CHECKING:
    from movarr.config import Config
    from movarr.database import Database

__all__ = ["check_and_notify"]

_KV_PREFIX = "index_proxy"


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
```

- [ ] **Step 6: Slim down `tests/unit/test_index_proxy_health.py`**

Replace the file contents with a focused wrapper test that verifies the wrapper
delegates correctly, without re-testing every branch of the engine:

```python
"""Tests for the index_proxy_health wrapper module."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import call, patch

from movarr.config import Config, NotificationConfig
from movarr.database import Database
from movarr.index_proxy_health import check_and_notify


def _db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


def _config(alert_hours: float, urls: list[str] | None = None) -> Config:
    config = Config()
    return config.model_copy(
        update={"notification": NotificationConfig(apprise_urls=urls or [], index_proxy_alert_hours=alert_hours)}
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
        """Integration: streak exceeds threshold -> apprise called (no mocking of engine)."""
        import datetime
        db = _db(tmp_path)
        config = _config(2.0, ["ntfy://t"])
        past = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=3)).isoformat()
        db.kv_set("index_proxy.unavailable_since", past)
        with patch("movarr.service_health.send_service_alert", return_value=True) as mock_alert:
            check_and_notify(has_results=False, proxy_name="Prowlarr", db=db, config=config)
        mock_alert.assert_called_once()
        assert db.kv_get("index_proxy.alert_sent") == "1"
```

- [ ] **Step 7: Run all new and updated tests**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_service_health.py tests/unit/test_index_proxy_health.py tests/unit/test_database.py -v 2>&1 | tail -25
```

Expected: all PASSED

- [ ] **Step 8: Run full suite — no regressions**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/ -q 2>&1 | tail -5
```

- [ ] **Step 9: Run linters**

```bash
cd /data/movarr && source .venv/bin/activate && uv run ruff check src/movarr/service_health.py src/movarr/index_proxy_health.py src/movarr/database.py && uv run mypy src/movarr/service_health.py src/movarr/index_proxy_health.py 2>&1 | tail -5
```

- [ ] **Step 10: Commit**

```bash
cd /data/movarr && git add src/movarr/service_health.py src/movarr/index_proxy_health.py src/movarr/database.py tests/unit/test_service_health.py tests/unit/test_index_proxy_health.py tests/unit/test_database.py
git commit -m "refactor(health): extract generic service_health engine; index_proxy_health becomes thin wrapper; DB v12 key rename"
```

---

## Task 3: Config field + migration (v2.9.0 → v2.10.0)

**Files:**
- Modify: `src/movarr/config.py`
- Modify: `tests/unit/test_config.py`

### Context

Add `torrent_client_alert_hours: float = 0` to `NotificationConfig` alongside the existing
`index_proxy_alert_hours`. A migration inserts `0` for all existing users (opt-in).

---

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_config.py`:

```python
class TestTorrentClientAlertHoursConfig:
    """Tests for notification.torrent_client_alert_hours config field."""

    def test_default_is_zero(self) -> None:
        """torrent_client_alert_hours defaults to 0 (feature disabled)."""
        assert Config().notification.torrent_client_alert_hours == 0

    def test_parses_float_value(self) -> None:
        """A float value is parsed and stored correctly."""
        config = Config.model_validate(
            {"notification": {"apprise_urls": [], "torrent_client_alert_hours": 3.5}}
        )
        assert config.notification.torrent_client_alert_hours == 3.5  # noqa: PLR2004

    def test_parses_zero_disables(self) -> None:
        """Zero keeps the feature disabled."""
        config = Config.model_validate(
            {"notification": {"apprise_urls": [], "torrent_client_alert_hours": 0}}
        )
        assert config.notification.torrent_client_alert_hours == 0


class TestMigrationV29ToV210:
    """Tests for the v2.9.0 -> v2.10.0 config migration."""

    def test_adds_torrent_client_alert_hours_zero(self) -> None:
        """Migration inserts torrent_client_alert_hours: 0 into notification block."""
        raw = {"general": {"config_version": "2.9.0"}, "notification": {"apprise_urls": []}}
        result = _migrate_v29_to_v210(raw)
        assert result["notification"]["torrent_client_alert_hours"] == 0

    def test_bumps_version_to_v210(self) -> None:
        """Migration sets config_version to 2.10.0."""
        raw = {"general": {"config_version": "2.9.0"}}
        assert _migrate_v29_to_v210(raw)["general"]["config_version"] == "2.10.0"

    def test_does_not_overwrite_existing_value(self) -> None:
        """Migration does not clobber a pre-existing torrent_client_alert_hours value."""
        raw = {
            "general": {"config_version": "2.9.0"},
            "notification": {"apprise_urls": [], "torrent_client_alert_hours": 4.0},
        }
        assert _migrate_v29_to_v210(raw)["notification"]["torrent_client_alert_hours"] == 4.0  # noqa: PLR2004
```

Add `_migrate_v29_to_v210` to the imports from `movarr.config`.

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_config.py::TestTorrentClientAlertHoursConfig tests/unit/test_config.py::TestMigrationV29ToV210 -v 2>&1 | tail -15
```

- [ ] **Step 3: Implement changes in `config.py`**

Add field to `NotificationConfig`:

```python
    apprise_urls: list[str] = Field(default_factory=list)
    index_proxy_alert_hours: float = 0
    torrent_client_alert_hours: float = 0
```

Update docstring to mention the new field.

Add migration function after `_migrate_v28_to_v29`:

```python
def _migrate_v29_to_v210(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.9.0 -> v2.10.0: add notification.torrent_client_alert_hours (default 0)."""
    raw.setdefault("notification", {}).setdefault("torrent_client_alert_hours", 0)
    raw.setdefault("general", {})["config_version"] = "2.10.0"
    return raw
```

Add to `MIGRATIONS`:

```python
    "2.9.0": _migrate_v29_to_v210,
```

Update `_CONFIG_VERSION = "2.10.0"`.

Update all existing tests that assert on the final migrated version (`"2.9.0"` → `"2.10.0"`).

- [ ] **Step 4: Run tests — must all pass**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_config.py -v 2>&1 | tail -15
```

- [ ] **Step 5: Linters + commit**

```bash
cd /data/movarr && source .venv/bin/activate && uv run ruff check src/movarr/config.py && uv run mypy src/movarr/config.py 2>&1 | tail -5
git add src/movarr/config.py tests/unit/test_config.py
git commit -m "feat(config): add notification.torrent_client_alert_hours; migrate v2.9.0 -> v2.10.0"
```

---

## Task 4: New `torrent_client_health.py` wrapper

**Files:**
- Create: `src/movarr/torrent_client_health.py`
- Create: `tests/unit/test_torrent_client_health.py`

### Context

Thin wrapper over `check_service_health`. A `_CLIENT_DISPLAY_NAMES` dict maps
`config.torrent_client.selected` (`"qbittorrent"`) to the correct display string
(`"qBittorrent"`). Future clients add one entry to that dict.

---

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_torrent_client_health.py`:

```python
"""Tests for the torrent_client_health wrapper module."""
from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import patch

from movarr.config import Config, NotificationConfig, TorrentClientConfig, QbittorrentConfig
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

    def test_unknown_client_falls_back_to_capitalize(self, tmp_path: Path) -> None:
        """Unknown client names fall back to .capitalize() for the display name."""
        db = _db(tmp_path)
        config = _config(2.0, selected="myclient")
        with patch("movarr.torrent_client_health.check_service_health") as mock_engine:
            check_and_notify(is_reachable=False, db=db, config=config)
        assert mock_engine.call_args.kwargs["service_name"] == "Myclient"

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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_torrent_client_health.py -v 2>&1 | tail -15
```

- [ ] **Step 3: Implement `src/movarr/torrent_client_health.py`**

```python
"""Torrent client health monitoring for movarr.

Thin wrapper around :mod:`movarr.service_health`.  Monitors whether the
configured torrent client (currently qBittorrent) is reachable and fires an
alert if it has been unavailable for longer than
``config.notification.torrent_client_alert_hours``.

Adding support for a future torrent client requires only:
  1. Adding a ``"client_key": "DisplayName"`` entry to ``_CLIENT_DISPLAY_NAMES``.
  2. Ensuring the client implements an ``is_connected()`` method.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from movarr.service_health import check_service_health

if TYPE_CHECKING:
    from movarr.config import Config
    from movarr.database import Database

__all__ = ["check_and_notify"]

_KV_PREFIX = "torrent_client"

# Maps config.torrent_client.selected values to human-readable display names.
# Add entries here when new torrent clients are introduced.
_CLIENT_DISPLAY_NAMES: dict[str, str] = {
    "qbittorrent": "qBittorrent",
}


def _display_name(selected: str) -> str:
    """Return the display name for *selected*, falling back to .capitalize()."""
    return _CLIENT_DISPLAY_NAMES.get(selected, selected.capitalize())


def check_and_notify(
    is_reachable: bool,
    db: Database,
    config: Config,
) -> None:
    """Update torrent client health streak and alert if threshold exceeded.

    Args:
        is_reachable: ``True`` if the client API responded this cycle;
            ``False`` if the connection attempt failed.
        db: Open database instance.
        config: Application configuration.
    """
    check_service_health(
        is_healthy=is_reachable,
        service_name=_display_name(config.torrent_client.selected),
        kv_prefix=_KV_PREFIX,
        alert_hours=config.notification.torrent_client_alert_hours,
        db=db,
        config=config,
    )
```

- [ ] **Step 4: Run tests — must all pass**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_torrent_client_health.py -v 2>&1 | tail -15
```

- [ ] **Step 5: Full suite + linters + commit**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/ -q 2>&1 | tail -5
uv run ruff check src/movarr/torrent_client_health.py && uv run mypy src/movarr/torrent_client_health.py 2>&1 | tail -5
git add src/movarr/torrent_client_health.py tests/unit/test_torrent_client_health.py
git commit -m "feat(health): add torrent_client_health wrapper; qBittorrent outage alerting"
```

---

## Task 5: Wire into `run_search()`

**Files:**
- Modify: `src/movarr/search.py`
- Modify: `tests/unit/test_search.py`

### Context

`run_search()` already has an early return when `qbt.is_connected()` is `False`.
The torrent client health check is called on **both** paths:
- Unreachable → `check_and_notify(is_reachable=False, ...)`
- Reachable → `check_and_notify(is_reachable=True, ...)` so the streak resets

The reachable call sits after the `is_connected()` guard — it runs unconditionally
when the search proceeds normally, ensuring streak is reset as soon as qBittorrent
becomes healthy again.

---

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_search.py`:

```python
class TestRunSearchTorrentClientHealthMonitor:
    """Tests that run_search() calls torrent_client_health.check_and_notify() correctly."""

    def _make_config(self) -> Config:
        from movarr.config import NotificationConfig
        config = Config()
        return config.model_copy(
            update={"notification": NotificationConfig(
                apprise_urls=["ntfy://t"],
                torrent_client_alert_hours=2.0,
            )}
        )

    def test_called_with_false_when_qbt_unreachable(self, tmp_path: Path) -> None:
        """When qBittorrent is unreachable, check_and_notify called with is_reachable=False."""
        from unittest.mock import MagicMock, patch
        from movarr.search import run_search

        config = self._make_config()
        db = Database(tmp_path / "test.db")
        qbt = MagicMock()
        qbt.is_connected.return_value = False

        with patch("movarr.search.torrent_client_health") as mock_health:
            run_search(config, qbt, db)

        mock_health.check_and_notify.assert_called_once_with(
            is_reachable=False, db=db, config=config
        )

    def test_called_with_true_when_qbt_reachable(self, tmp_path: Path) -> None:
        """When qBittorrent is reachable, check_and_notify called with is_reachable=True."""
        from unittest.mock import MagicMock, patch
        from movarr.search import run_search

        config = self._make_config()
        db = Database(tmp_path / "test.db")
        qbt = MagicMock()
        qbt.is_connected.return_value = True

        mock_indexer = MagicMock()
        mock_indexer.is_reachable.return_value = True
        mock_indexer.search.return_value = iter([])

        with (
            patch("movarr.search.get_indexer_client", return_value=mock_indexer),
            patch("movarr.search.check_and_notify"),
            patch("movarr.search.torrent_client_health") as mock_health,
            patch("movarr.search.walk_library", return_value=[]),
        ):
            run_search(config, qbt, db)

        mock_health.check_and_notify.assert_called_once_with(
            is_reachable=True, db=db, config=config
        )
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_search.py::TestRunSearchTorrentClientHealthMonitor -v 2>&1 | tail -15
```

- [ ] **Step 3: Modify `search.py`**

Add import (alongside existing health import):

```python
from movarr import torrent_client_health
```

In `run_search()`, update the `qbt.is_connected()` block:

```python
    if not qbt.is_connected():
        logger.warning("qBittorrent is unreachable; skipping search.")
        torrent_client_health.check_and_notify(is_reachable=False, db=db, config=config)
        return
    torrent_client_health.check_and_notify(is_reachable=True, db=db, config=config)
```

The `torrent_client_health.check_and_notify(is_reachable=True, ...)` line sits immediately
after the guard block, before `get_indexer_client()` — it runs every time the search
proceeds normally.

- [ ] **Step 4: Run new tests — must all pass**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_search.py::TestRunSearchTorrentClientHealthMonitor -v 2>&1 | tail -10
```

- [ ] **Step 5: Run full search suite — no regressions**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_search.py -v 2>&1 | tail -15
```

- [ ] **Step 6: Full suite + linters + commit**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/ -q 2>&1 | tail -5
uv run ruff check src/movarr/search.py tests/unit/test_search.py && uv run mypy src/movarr/search.py 2>&1 | tail -5
git add src/movarr/search.py tests/unit/test_search.py
git commit -m "feat(search): wire torrent client health check into run_search()"
```

---

## Task 6: README + config + final verification

**Files:**
- Modify: `README.md`
- Modify: `configs/movarr.yml` (on disk; gitignored)

- [ ] **Step 1: Update `README.md` notification table**

Add the new row beneath `index_proxy_alert_hours`:

```markdown
| `torrent_client_alert_hours` | Send an alert if the torrent client has been unreachable for this many consecutive hours. Set to `0` to disable. | `0` |
```

- [ ] **Step 2: Update `configs/movarr.yml` notification block**

```yaml
notification:
  apprise_urls:
  - ntfy://peccleston_lan_alerting
  # Alert if index proxy (Prowlarr/Jackett) returns no results or is unreachable for this many hours.
  # Remove or set to 0 to disable.
  index_proxy_alert_hours: 0
  # Alert if torrent client (qBittorrent) has been unreachable for this many hours.
  # Remove or set to 0 to disable.
  torrent_client_alert_hours: 0
```

- [ ] **Step 3: Run full test suite**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/ -q 2>&1 | tail -5
```

Expected: all PASSED

- [ ] **Step 4: Run full linter pass**

```bash
cd /data/movarr && source .venv/bin/activate && uv run ruff check src/ && uv run mypy src/ 2>&1 | tail -5
```

- [ ] **Step 5: Commit README**

```bash
cd /data/movarr && git add README.md
git commit -m "docs(readme): document torrent_client_alert_hours config field"
```

---

## Summary

| Task | What It Delivers |
|------|-----------------|
| 1 — `send_service_alert()` | Generic notification function; `send_index_proxy_alert()` becomes a delegate |
| 2 — `service_health.py` + DB v12 | Single streak engine reused by all services; old KV key renamed cleanly |
| 3 — Config v2.10.0 | `torrent_client_alert_hours` field + migration |
| 4 — `torrent_client_health.py` | Thin wrapper; `_CLIENT_DISPLAY_NAMES` dict for future clients |
| 5 — `run_search()` wiring | Health check on both reachable and unreachable paths |
| 6 — README + config | User-visible documentation |

**To add a future torrent client (e.g. Transmission):**
1. Add `"transmission": "Transmission"` to `_CLIENT_DISPLAY_NAMES` in `torrent_client_health.py`
2. Ensure the new client class implements `is_connected() -> bool`
3. No changes needed to `service_health.py`, `notifications.py`, `search.py`, or config
