# Index Proxy Outage Alert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send a notification when the configured index proxy (Jackett or Prowlarr) returns zero raw results or is unreachable for longer than a user-defined threshold, so the user is alerted when the proxy goes down or starts misbehaving.

**Architecture:** A lightweight `index_proxy_health` module tracks a zero-results streak start timestamp and an alert-sent flag in a new `kv_store` database table. `run_search()` records whether the selected proxy returned any raw results; `check_and_notify()` fires an apprise notification once the streak exceeds the configured threshold. The streak resets as soon as results return, enabling re-alerting on future outages.

**Tech Stack:** Python 3.12, SQLAlchemy (SQLite), apprise, pydantic, loguru — all already in the project.

---

## File Map

| Action   | Path                                              | Responsibility                                   |
|----------|---------------------------------------------------|--------------------------------------------------|
| Modify   | `src/movarr/database.py`                          | Add `kv_store` table + `kv_get/set/delete` methods; bump DB version to 11 |
| Modify   | `src/movarr/config.py`                            | Add `index_proxy_alert_hours` to `NotificationConfig`; add v2.8→v2.9 migration |
| Modify   | `src/movarr/notifications.py`                     | Add `send_index_proxy_alert()` function          |
| Create   | `src/movarr/index_proxy_health.py`                | `check_and_notify()` — streak tracking logic     |
| Modify   | `src/movarr/search.py`                            | Count raw indexer results; call health check     |
| Modify   | `tests/unit/test_database.py`                     | Tests for `kv_get/set/delete` + schema v11       |
| Modify   | `tests/unit/test_config.py`                       | Tests for new config field + v2.8→v2.9 migration |
| Modify   | `tests/unit/test_notifications.py`                | Tests for `send_index_proxy_alert()`             |
| Create   | `tests/unit/test_index_proxy_health.py`           | Full unit tests for health monitor               |
| Modify   | `tests/unit/test_search.py`                       | Tests for health-monitor call sites in `run_search()` |

---

## Task 1: `kv_store` table in Database

**Files:**
- Modify: `src/movarr/database.py`
- Modify: `tests/unit/test_database.py`

### Context
The health monitor needs to persist two pieces of state between search runs:
- When the zero-results streak started (ISO datetime string)
- Whether an alert has already been sent for the current streak

These are stored as rows in a new `kv_store` table. Existing DB version is `10`; this adds version `11`.

---

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_database.py`:

```python
class TestKvStore:
    """Tests for the kv_get / kv_set / kv_delete methods."""

    def test_kv_get_missing_key_returns_none(self, tmp_path: Path) -> None:
        """Getting a non-existent key returns None."""
        db = Database(tmp_path / "test.db")
        assert db.kv_get("missing.key") is None

    def test_kv_set_and_get_roundtrip(self, tmp_path: Path) -> None:
        """A value written with kv_set is readable via kv_get."""
        db = Database(tmp_path / "test.db")
        db.kv_set("some.key", "hello")
        assert db.kv_get("some.key") == "hello"

    def test_kv_set_overwrites_existing_value(self, tmp_path: Path) -> None:
        """A second kv_set on the same key replaces the previous value."""
        db = Database(tmp_path / "test.db")
        db.kv_set("k", "v1")
        db.kv_set("k", "v2")
        assert db.kv_get("k") == "v2"

    def test_kv_delete_removes_key(self, tmp_path: Path) -> None:
        """kv_delete removes the key; subsequent kv_get returns None."""
        db = Database(tmp_path / "test.db")
        db.kv_set("k", "v")
        db.kv_delete("k")
        assert db.kv_get("k") is None

    def test_kv_delete_missing_key_is_noop(self, tmp_path: Path) -> None:
        """kv_delete on a non-existent key raises no error."""
        db = Database(tmp_path / "test.db")
        db.kv_delete("never.existed")  # must not raise

    def test_kv_store_table_created_on_new_db(self, tmp_path: Path) -> None:
        """kv_store table exists after Database init."""
        db = Database(tmp_path / "test.db")
        with db._engine.connect() as conn:
            result = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='kv_store'")
            )
            assert result.fetchone() is not None

    def test_db_version_is_11(self, tmp_path: Path) -> None:
        """New database is created at schema version 11."""
        db = Database(tmp_path / "test.db")
        assert db._get_user_version() == 11
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_database.py::TestKvStore -v 2>&1 | tail -20
```

Expected: 7 FAILED (kv_get/set/delete do not exist yet, table does not exist yet)

- [ ] **Step 3: Implement `kv_store` in `database.py`**

At the top of `database.py`, update the version constants:

```python
_DB_VERSION = 11
_SCHEMA_V11_KV_STORE = 11
```

Add the ORM model after the existing `HistoryRecord` class (look for `class HistoryRecord`):

```python
class KvRecord(Base):
    """A single key-value entry in the persistent store."""

    __tablename__ = "kv_store"

    key: Any = Column(String, primary_key=True)
    value: Any = Column(String, nullable=True)
    updated_at: Any = Column(String, nullable=True)
```

In `_upgrade()`, add this block after the existing `_SCHEMA_V10_CREATED_AT` block:

```python
            if from_version < _SCHEMA_V11_KV_STORE:
                conn.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS kv_store "
                        "(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)"
                    )
                )
                conn.commit()
```

Add three new public methods anywhere after `vacuum()`:

```python
    def kv_get(self, key: str) -> str | None:
        """Return the stored string for *key*, or ``None`` if absent.

        Args:
            key: Dot-namespaced key string (e.g. ``"index_proxy.zero_results_since"``).

        Returns:
            The stored value, or ``None`` if the key does not exist.
        """
        with Session(self._engine) as session:
            record = session.get(KvRecord, key)
            return record.value if record is not None else None

    def kv_set(self, key: str, value: str) -> None:
        """Upsert *value* for *key* in the persistent kv store.

        Args:
            key: Dot-namespaced key string.
            value: String value to store.
        """
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with Session(self._engine) as session:
            record = session.get(KvRecord, key)
            if record is None:
                session.add(KvRecord(key=key, value=value, updated_at=now))
            else:
                record.value = value
                record.updated_at = now
            session.commit()

    def kv_delete(self, key: str) -> None:
        """Delete the entry for *key* if it exists (no-op if absent).

        Args:
            key: Dot-namespaced key string.
        """
        with Session(self._engine) as session:
            record = session.get(KvRecord, key)
            if record is not None:
                session.delete(record)
                session.commit()
```

Also add `KvRecord` to `__all__`:

```python
__all__ = ["Database", "HistoryRecord", "KvRecord"]
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_database.py::TestKvStore -v 2>&1 | tail -20
```

Expected: 7 PASSED

- [ ] **Step 5: Run full DB test suite to confirm no regressions**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_database.py -v 2>&1 | tail -20
```

Expected: all PASSED

- [ ] **Step 6: Commit**

```bash
cd /data/movarr && git add src/movarr/database.py tests/unit/test_database.py
git commit -m "feat(db): add kv_store table for persistent key-value state (schema v11)"
```

---

## Task 2: Config field + migration

**Files:**
- Modify: `src/movarr/config.py`
- Modify: `tests/unit/test_config.py`

### Context
The user sets `notification.index_proxy_alert_hours` to a float (e.g. `2.0`) to enable alerts. `0` means the feature is disabled (consistent with all other numeric config fields). A config migration bumps the schema from `2.8.0` to `2.9.0`, inserting `0` for all existing users (opt-in by default).

---

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_config.py`:

```python
class TestIndexProxyAlertHoursConfig:
    """Tests for notification.index_proxy_alert_hours config field."""

    def test_default_is_zero(self) -> None:
        """index_proxy_alert_hours defaults to 0 (feature disabled)."""
        config = Config()
        assert config.notification.index_proxy_alert_hours == 0

    def test_parses_float_value(self) -> None:
        """A float value is parsed and stored correctly."""
        config = Config.model_validate(
            {"notification": {"apprise_urls": [], "index_proxy_alert_hours": 2.5}}
        )
        assert config.notification.index_proxy_alert_hours == 2.5  # noqa: PLR2004

    def test_parses_integer_as_float(self) -> None:
        """An integer value (e.g. 2) is accepted and stored as float."""
        config = Config.model_validate(
            {"notification": {"apprise_urls": [], "index_proxy_alert_hours": 2}}
        )
        assert config.notification.index_proxy_alert_hours == 2.0  # noqa: PLR2004

    def test_parses_zero_disables_feature(self) -> None:
        """Explicitly setting 0 keeps the feature disabled."""
        config = Config.model_validate(
            {"notification": {"apprise_urls": [], "index_proxy_alert_hours": 0}}
        )
        assert config.notification.index_proxy_alert_hours == 0


class TestMigrationV28ToV29:
    """Tests for the v2.8.0 → v2.9.0 config migration."""

    def test_adds_index_proxy_alert_hours_zero(self) -> None:
        """Migration inserts index_proxy_alert_hours: 0 into notification block."""
        raw = {"general": {"config_version": "2.8.0"}, "notification": {"apprise_urls": []}}
        result = _migrate_v28_to_v29(raw)
        assert result["notification"]["index_proxy_alert_hours"] == 0

    def test_bumps_version_to_v29(self) -> None:
        """Migration sets config_version to 2.9.0."""
        raw = {"general": {"config_version": "2.8.0"}}
        result = _migrate_v28_to_v29(raw)
        assert result["general"]["config_version"] == "2.9.0"

    def test_does_not_overwrite_existing_value(self) -> None:
        """Migration does not clobber an existing index_proxy_alert_hours value."""
        raw = {
            "general": {"config_version": "2.8.0"},
            "notification": {"apprise_urls": [], "index_proxy_alert_hours": 4.0},
        }
        result = _migrate_v28_to_v29(raw)
        assert result["notification"]["index_proxy_alert_hours"] == 4.0  # noqa: PLR2004
```

Ensure `_migrate_v28_to_v29` is importable from `movarr.config` by adding it to the existing test imports:

```python
from movarr.config import (
    Config,
    _migrate_v28_to_v29,  # add this
    load_config,
)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_config.py::TestIndexProxyAlertHoursConfig tests/unit/test_config.py::TestMigrationV28ToV29 -v 2>&1 | tail -20
```

Expected: all FAILED (field and migration do not exist yet)

- [ ] **Step 3: Add field to `NotificationConfig`**

In `config.py`, update `NotificationConfig`:

```python
class NotificationConfig(BaseModel):
    """Notification settings.

    Specify one or more `apprise <https://github.com/caronc/apprise>`_ service URLs.
    An empty list disables notifications.  Any apprise-supported service works:
    ``ntfy://topic``, ``discord://id/token``, ``mailtos://user:pass@host:587/``, etc.

    ``index_proxy_alert_hours``: if set, send an alert after the index proxy returns
    no results (or is unreachable) for this many hours.  Requires ``apprise_urls``
    to be non-empty.  ``None`` disables the feature.
    """

    apprise_urls: list[str] = Field(default_factory=list)
    index_proxy_alert_hours: float = 0
```

- [ ] **Step 4: Add migration function and wire it up**

In `config.py`, add the migration function (place it after `_migrate_v27_to_v28`):

```python
def _migrate_v28_to_v29(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.8.0 -> v2.9.0: add notification.index_proxy_alert_hours (default 0 = disabled)."""
    notification = raw.setdefault("notification", {})
    notification.setdefault("index_proxy_alert_hours", 0)
    raw.setdefault("general", {})["config_version"] = "2.9.0"
    return raw
```

Update `MIGRATIONS` dict to include the new entry:

```python
MIGRATIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "1.0.0": _migrate_v1_to_v2,
    "2.0.0": _migrate_v2_to_v21,
    "2.1.0": _migrate_v21_to_v22,
    "2.2.0": _migrate_v22_to_v23,
    "2.3.0": _migrate_v23_to_v24,
    "2.4.0": _migrate_v24_to_v25,
    "2.5.0": _migrate_v25_to_v26,
    "2.6.0": _migrate_v26_to_v27,
    "2.7.0": _migrate_v27_to_v28,
    "2.8.0": _migrate_v28_to_v29,
}
```

Update `_CONFIG_VERSION` at the top of the file:

```python
_CONFIG_VERSION = "2.9.0"
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_config.py::TestIndexProxyAlertHoursConfig tests/unit/test_config.py::TestMigrationV28ToV29 -v 2>&1 | tail -20
```

Expected: all PASSED

- [ ] **Step 6: Run full config test suite to confirm no regressions**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_config.py -v 2>&1 | tail -20
```

Expected: all PASSED

- [ ] **Step 7: Commit**

```bash
cd /data/movarr && git add src/movarr/config.py tests/unit/test_config.py
git commit -m "feat(config): add notification.index_proxy_alert_hours; migrate v2.8.0 -> v2.9.0"
```

---

## Task 3: `send_index_proxy_alert()` in `notifications.py`

**Files:**
- Modify: `src/movarr/notifications.py`
- Modify: `tests/unit/test_notifications.py`

### Context
A dedicated notification function fires when the health monitor decides an alert is due. It uses the same apprise machinery as `send_queued_notification()` and returns a bool for testability. The proxy name (e.g. `"Prowlarr"` or `"Jackett"`) and elapsed hours are passed in so the message is informative.

---

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_notifications.py`:

```python
class TestSendIndexProxyAlert:
    """Tests for send_index_proxy_alert()."""

    def _make_config(self, urls: list[str]) -> Config:
        from movarr.config import Config, NotificationConfig
        config = Config()
        config = config.model_copy(update={"notification": NotificationConfig(apprise_urls=urls)})
        return config

    def test_returns_false_when_no_urls_configured(self) -> None:
        """Returns False immediately when apprise_urls is empty."""
        from movarr.notifications import send_index_proxy_alert
        config = self._make_config([])
        result = send_index_proxy_alert(proxy_name="Prowlarr", hours_elapsed=3.0, config=config)
        assert result is False

    def test_calls_apprise_when_urls_configured(self) -> None:
        """Calls apprise.Apprise.notify() when URLs are present."""
        from unittest.mock import MagicMock, patch
        from movarr.notifications import send_index_proxy_alert
        config = self._make_config(["ntfy://test-topic"])
        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            result = send_index_proxy_alert(proxy_name="Jackett", hours_elapsed=2.5, config=config)
        assert result is True
        mock_ap.notify.assert_called_once()

    def test_subject_contains_proxy_name_and_hours(self) -> None:
        """Notification subject includes the proxy name and elapsed hours."""
        from unittest.mock import MagicMock, call, patch
        from movarr.notifications import send_index_proxy_alert
        config = self._make_config(["ntfy://test-topic"])
        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            send_index_proxy_alert(proxy_name="Prowlarr", hours_elapsed=4.0, config=config)
        _, kwargs = mock_ap.notify.call_args
        assert "Prowlarr" in kwargs["title"]
        assert "4" in kwargs["title"]

    def test_returns_false_when_apprise_returns_false(self) -> None:
        """Returns False when apprise.notify() returns False."""
        from unittest.mock import MagicMock, patch
        from movarr.notifications import send_index_proxy_alert
        config = self._make_config(["ntfy://test-topic"])
        mock_ap = MagicMock()
        mock_ap.notify.return_value = False
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            result = send_index_proxy_alert(proxy_name="Prowlarr", hours_elapsed=1.0, config=config)
        assert result is False

    def test_returns_false_when_apprise_raises(self) -> None:
        """Returns False (does not propagate) when apprise.notify() raises."""
        from unittest.mock import MagicMock, patch
        from movarr.notifications import send_index_proxy_alert
        config = self._make_config(["ntfy://test-topic"])
        mock_ap = MagicMock()
        mock_ap.notify.side_effect = RuntimeError("boom")
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            result = send_index_proxy_alert(proxy_name="Prowlarr", hours_elapsed=1.0, config=config)
        assert result is False
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_notifications.py::TestSendIndexProxyAlert -v 2>&1 | tail -20
```

Expected: all FAILED (`send_index_proxy_alert` does not exist yet)

- [ ] **Step 3: Implement `send_index_proxy_alert()` in `notifications.py`**

Add the function and update `__all__`:

```python
__all__ = ["send_index_proxy_alert", "send_queued_notification"]


def send_index_proxy_alert(proxy_name: str, hours_elapsed: float, config: Config) -> bool:
    """Send an alert notification when the index proxy has returned no results for too long.

    Args:
        proxy_name: Human-readable proxy name, e.g. ``"Prowlarr"`` or ``"Jackett"``.
        hours_elapsed: How many hours the zero-results streak has lasted.
        config: Application configuration.

    Returns:
        ``True`` if the notification was delivered, ``False`` otherwise (including
        when no apprise URLs are configured).
    """
    urls = config.notification.apprise_urls
    if not urls:
        logger.debug("No apprise URLs configured; skipping index proxy alert.")
        return False

    hours_str = f"{hours_elapsed:.1f}"
    subject = f"movarr: {proxy_name} returned no results for {hours_str}h — possible outage"
    body = (
        f"<p><strong>movarr index proxy alert</strong></p>"
        f"<p><strong>Proxy:</strong> {proxy_name}</p>"
        f"<p><strong>Duration:</strong> No results returned for {hours_str} hours.</p>"
        f"<p>movarr will keep retrying every search cycle. "
        f"Check that {proxy_name} is running and its indexers are healthy.</p>"
    )

    ap = apprise.Apprise()
    for url in urls:
        ap.add(url)

    try:
        sent = ap.notify(title=subject, body=body, body_format=apprise.NotifyFormat.HTML)
    except Exception:
        logger.exception("Index proxy alert send failed.")
        return False

    if not sent:
        logger.warning("Index proxy alert was not delivered (apprise returned False).")
        return False

    logger.warning("Index proxy alert sent: {}", subject)
    return True
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_notifications.py::TestSendIndexProxyAlert -v 2>&1 | tail -20
```

Expected: all PASSED

- [ ] **Step 5: Run full notifications test suite**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_notifications.py -v 2>&1 | tail -20
```

Expected: all PASSED

- [ ] **Step 6: Commit**

```bash
cd /data/movarr && git add src/movarr/notifications.py tests/unit/test_notifications.py
git commit -m "feat(notifications): add send_index_proxy_alert() for index proxy outage alerts"
```

---

## Task 4: `index_proxy_health.py` module

**Files:**
- Create: `src/movarr/index_proxy_health.py`
- Create: `tests/unit/test_index_proxy_health.py`

### Context
This module owns the streak-tracking state machine. It reads/writes two KV store keys:
- `"index_proxy.zero_results_since"` — ISO 8601 UTC timestamp when the current streak began. Absent means the proxy is healthy.
- `"index_proxy.alert_sent"` — `"1"` when an alert has been sent for the current streak. Absent / any other value means not yet sent.

The public function `check_and_notify()` is called after every search run. It never raises; all errors are caught and logged.

**State machine:**
```
has_results=True  → delete both keys (reset streak)
has_results=False → if no streak key: set streak key to now; return
                  → if streak key set and threshold met and alert not sent:
                        send alert; set alert_sent key
                  → otherwise: no-op
```

`index_proxy_alert_hours` is None or apprise_urls is empty → all no-ops (feature disabled).

---

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_index_proxy_health.py`:

```python
"""Tests for src/movarr/index_proxy_health.py."""
from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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


class TestCheckAndNotifyFeatureDisabled:
    """When the feature is disabled, nothing is written to the DB."""

    def test_no_alert_hours_configured_has_results(self, tmp_path: Path) -> None:
        """No DB writes when alert_hours is None and proxy has results."""
        db = _db(tmp_path)
        config = _config(alert_hours=0)
        check_and_notify(has_results=True, proxy_name="Prowlarr", db=db, config=config)
        assert db.kv_get("index_proxy.zero_results_since") is None

    def test_no_alert_hours_configured_no_results(self, tmp_path: Path) -> None:
        """No DB writes when alert_hours is 0 (disabled) and proxy has no results."""
        db = _db(tmp_path)
        config = _config(alert_hours=0)
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
        dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours_ago)
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_index_proxy_health.py -v 2>&1 | tail -25
```

Expected: all FAILED (module does not exist yet)

- [ ] **Step 3: Implement `src/movarr/index_proxy_health.py`**

```python
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
    if alert_hours is None:
        return

    since_raw = db.kv_get(_KEY_SINCE)
    now = datetime.datetime.now(datetime.timezone.utc)

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
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_index_proxy_health.py -v 2>&1 | tail -30
```

Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
cd /data/movarr && git add src/movarr/index_proxy_health.py tests/unit/test_index_proxy_health.py
git commit -m "feat(health): add index_proxy_health module for zero-results outage detection"
```

---

## Task 5: Wire health check into `search.py`

**Files:**
- Modify: `src/movarr/search.py`
- Modify: `tests/unit/test_search.py`

### Context
`_process_criteria()` is changed to return an `int` — the count of raw results yielded by the indexer before any filtering. `run_search()` sums these counts across all criteria tiers and passes the result to `check_and_notify()`.

The two failure paths that both count as "no results" are:
1. `indexer_client.is_reachable()` returns `False` (early return before any search)
2. All criteria tiers yield zero raw results from the indexer

Both call `check_and_notify(has_results=False, ...)`. When results are found, `check_and_notify(has_results=True, ...)` resets the streak.

---

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_search.py`:

```python
class TestRunSearchHealthMonitor:
    """Tests that run_search() calls check_and_notify() correctly."""

    def _make_config(self) -> Config:
        from movarr.config import Config, NotificationConfig
        config = Config()
        return config.model_copy(
            update={
                "notification": NotificationConfig(
                    apprise_urls=["ntfy://t"],
                    index_proxy_alert_hours=2.0,
                )
            }
        )

    def test_check_and_notify_called_with_false_when_not_reachable(
        self, tmp_path: Path
    ) -> None:
        """When indexer is unreachable, check_and_notify called with has_results=False."""
        from unittest.mock import MagicMock, patch
        from movarr.search import run_search

        config = self._make_config()
        db = Database(tmp_path / "test.db")
        qbt = MagicMock()
        qbt.is_connected.return_value = True

        mock_indexer = MagicMock()
        mock_indexer.is_reachable.return_value = False

        with (
            patch("movarr.search.get_indexer_client", return_value=mock_indexer),
            patch("movarr.search.check_and_notify") as mock_health,
        ):
            run_search(config, qbt, db)

        mock_health.assert_called_once()
        call_kwargs = mock_health.call_args.kwargs
        assert call_kwargs["has_results"] is False

    def test_check_and_notify_called_with_true_when_results_returned(
        self, tmp_path: Path
    ) -> None:
        """When indexer returns results, check_and_notify called with has_results=True."""
        from unittest.mock import MagicMock, patch
        from movarr.search import run_search
        from movarr.models import ResultDict

        config = self._make_config()
        db = Database(tmp_path / "test.db")
        qbt = MagicMock()
        qbt.is_connected.return_value = True

        # Minimal ResultDict that won't pass filters (result stays Failed)
        # but IS yielded as a raw result from the indexer.
        raw_result: ResultDict = {
            "index_title": "Some.Movie.2020.1080p",
            "result": "Passed",
            "result_details": [],
        }

        mock_indexer = MagicMock()
        mock_indexer.is_reachable.return_value = True
        mock_indexer.search.return_value = iter([raw_result])

        with (
            patch("movarr.search.get_indexer_client", return_value=mock_indexer),
            patch("movarr.search.check_and_notify") as mock_health,
            patch("movarr.search.walk_library", return_value=[]),
        ):
            run_search(config, qbt, db)

        mock_health.assert_called_once()
        call_kwargs = mock_health.call_args.kwargs
        assert call_kwargs["has_results"] is True

    def test_check_and_notify_called_with_false_when_indexer_returns_empty(
        self, tmp_path: Path
    ) -> None:
        """When indexer returns zero raw results, check_and_notify called with has_results=False."""
        from unittest.mock import MagicMock, patch
        from movarr.search import run_search

        config = self._make_config()
        db = Database(tmp_path / "test.db")
        qbt = MagicMock()
        qbt.is_connected.return_value = True

        mock_indexer = MagicMock()
        mock_indexer.is_reachable.return_value = True
        mock_indexer.search.return_value = iter([])  # empty generator

        with (
            patch("movarr.search.get_indexer_client", return_value=mock_indexer),
            patch("movarr.search.check_and_notify") as mock_health,
            patch("movarr.search.walk_library", return_value=[]),
        ):
            run_search(config, qbt, db)

        mock_health.assert_called_once()
        call_kwargs = mock_health.call_args.kwargs
        assert call_kwargs["has_results"] is False

    def test_check_and_notify_not_called_when_qbt_unreachable(
        self, tmp_path: Path
    ) -> None:
        """When qBittorrent is unreachable, search exits early — check_and_notify not called."""
        from unittest.mock import MagicMock, patch
        from movarr.search import run_search

        config = self._make_config()
        db = Database(tmp_path / "test.db")
        qbt = MagicMock()
        qbt.is_connected.return_value = False

        with patch("movarr.search.check_and_notify") as mock_health:
            run_search(config, qbt, db)

        mock_health.assert_not_called()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_search.py::TestRunSearchHealthMonitor -v 2>&1 | tail -20
```

Expected: all FAILED (`check_and_notify` not imported or called in `search.py` yet)

- [ ] **Step 3: Modify `search.py`**

Add the import at the top of `search.py` (alongside other movarr imports):

```python
from movarr.index_proxy_health import check_and_notify
```

Change `_process_criteria()` signature to return `int`. Update the function signature and add a raw-results counter:

```python
def _process_criteria(  # noqa: PLR0912
    criteria_cfg: SearchCriteriaConfig,
    category: str,
    indexer: str,
    session: _SearchSession,
) -> int:
    """Fetch and process all indexer results for one criteria tier.

    Returns:
        The number of raw results yielded by the indexer (before any filtering).
    """
    site_dict = criteria_cfg.model_dump()
    raw_count = 0

    for raw_result in session.indexer.search(indexer, criteria_cfg.criteria, category):
        raw_count += 1
        result = _enrich_index_metadata(raw_result)
        # ... rest of existing body unchanged ...
    
    return raw_count
```

> **Note:** only the function signature, docstring, and the two lines `raw_count = 0` / `raw_count += 1` / `return raw_count` change — the entire inner body remains identical to the current implementation.

Modify `run_search()` to:
1. Call `check_and_notify(has_results=False, ...)` when not reachable.
2. Sum raw counts and call `check_and_notify` after processing all tiers.

Replace the `is_reachable` early-return block and the criteria loop in `run_search()`:

```python
    proxy_name = config.index_proxy.selected.capitalize()

    indexer_client = get_indexer_client(config)
    if not indexer_client.is_reachable():
        logger.warning(
            "{} is not reachable; skipping search.",
            proxy_name,
        )
        check_and_notify(has_results=False, proxy_name=proxy_name, db=db, config=config)
        return

    library_walk: list[tuple[str, list[str], list[str]]] | None = None
    if config.general.library_path_list:
        library_walk = list(walk_library(config.general.library_path_list))

    session = _SearchSession(
        config=config,
        indexer=indexer_client,
        qbt=qbt,
        db=db,
        library_walk=library_walk,
    )

    total_raw = 0
    for criteria_cfg in site_cfg.search:
        index_site = site_cfg.jackett_indexer if config.index_proxy.selected == "jackett" else site_cfg.prowlarr_indexer
        category = criteria_cfg.category
        if index_site in site_cfg.override_search:
            overrides = site_cfg.override_search[index_site]
            if "category" in overrides:
                category = overrides["category"]

        logger.info(
            "Searching indexer '{}' for '{}' (category '{}').",
            index_site,
            criteria_cfg.criteria,
            category,
        )
        total_raw += _process_criteria(criteria_cfg=criteria_cfg, category=category, indexer=index_site, session=session)

    check_and_notify(has_results=total_raw > 0, proxy_name=proxy_name, db=db, config=config)
```

- [ ] **Step 4: Run the new tests to confirm they pass**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_search.py::TestRunSearchHealthMonitor -v 2>&1 | tail -20
```

Expected: all PASSED

- [ ] **Step 5: Run the full search test suite to confirm no regressions**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/test_search.py -v 2>&1 | tail -20
```

Expected: all PASSED

- [ ] **Step 6: Commit**

```bash
cd /data/movarr && git add src/movarr/search.py tests/unit/test_search.py
git commit -m "feat(search): wire index proxy health check into run_search()"
```

---

## Task 6: Update example config + full test run

**Files:**
- Modify: `configs/movarr.yml`

### Context
The live config already has `config_version: 2.8.0`. Running the app will auto-migrate it to `2.9.0` and insert `index_proxy_alert_hours: 0`. To make the new field discoverable for users, add a comment in the `notification` block.

---

- [ ] **Step 1: Update `configs/movarr.yml` notification block**

Edit the `notification:` section of `configs/movarr.yml`:

```yaml
notification:
  apprise_urls:
  - ntfy://peccleston_lan_alerting
  # index_proxy_alert_hours: 2  # Alert if index proxy returns no results for this many hours (0 = disabled)
  index_proxy_alert_hours: 0
```

> The migration will write `0` on first run, keeping the feature opt-in.

- [ ] **Step 2: Run the full test suite**

```bash
cd /data/movarr && source .venv/bin/activate && python -m pytest tests/unit/ -v 2>&1 | tail -30
```

Expected: all PASSED

- [ ] **Step 3: Run linters**

```bash
cd /data/movarr && source .venv/bin/activate && uv run ruff check src/ tests/ && uv run mypy src/ 2>&1 | tail -20
```

Expected: no errors

- [ ] **Step 4: Commit**

```bash
cd /data/movarr && git add configs/movarr.yml
git commit -m "chore(config): document index_proxy_alert_hours in example config"
```

---

## Summary

| Task | Files Changed | What It Delivers |
|------|--------------|-----------------|
| 1    | `database.py`, `test_database.py` | `kv_store` table; persistent key-value state |
| 2    | `config.py`, `test_config.py` | `index_proxy_alert_hours` field; v2.9.0 migration |
| 3    | `notifications.py`, `test_notifications.py` | `send_index_proxy_alert()` function |
| 4    | `index_proxy_health.py` (new), `test_index_proxy_health.py` (new) | Streak tracker; fires alert once per outage |
| 5    | `search.py`, `test_search.py` | Raw result counting; health check called in both failure paths |
| 6    | `configs/movarr.yml` | Visible config knob for users |

**To enable the feature**, the user adds to `config.yml`:
```yaml
notification:
  apprise_urls:
  - ntfy://your-topic
  index_proxy_alert_hours: 2
```
