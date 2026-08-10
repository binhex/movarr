# Torrent Status Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record completed/stalled torrent outcomes in the history DB and automatically expire stalled titles after a configurable number of days so they can be retried.

**Architecture:** Add a `stalled_at` column to `history`, two new DB methods (`mark_stalled`, `mark_completed`), an `expire_stalled` cleanup call at search-start, wire the queue manager to call `mark_stalled` after deletions, and replace `set_verified` in the post-processor with `mark_completed`. Config gets a new `database.stalled_expiry_days` key (config v2.0.0 → 2.1.0, DB v8 → v9).

**Tech Stack:** Python 3.12, SQLAlchemy (SQLite), Pydantic v2, pytest + pytest-mock, loguru

---

## Files

| Action | File | Change |
|--------|------|--------|
| Modify | `src/movarr/database.py` | Add `stalled_at` column; bump `_DB_VERSION` to 9; add migration; add `mark_stalled`, `mark_completed`, `expire_stalled`; update `has_passed` |
| Modify | `src/movarr/config.py` | Add `DatabaseConfig`; add `database` field to `Config`; add `_migrate_v2_to_v21`; bump `_CONFIG_VERSION` to `"2.1.0"` |
| Modify | `src/movarr/queue_manager.py` | Add `db: Database` param; call `db.mark_stalled(tag)` after each deletion |
| Modify | `src/movarr/post_processor.py` | Replace `db.set_verified(tag)` with `db.mark_completed(tag)` |
| Modify | `src/movarr/scheduler.py` | Pass `db` to queue management; call `db.expire_stalled(...)` before search |
| Modify | `tests/unit/test_database.py` | Tests for `mark_stalled`, `mark_completed`, `expire_stalled`, updated `has_passed`, migration v8→v9 |
| Modify | `tests/unit/test_queue_manager.py` | Tests for `mark_stalled` called on deletion |
| Modify | `tests/unit/test_post_processor.py` | Assert `mark_completed` called, not `set_verified` |
| Modify | `tests/unit/test_config.py` | Tests for `DatabaseConfig` defaults and v2.0.0→2.1.0 migration |

---

## Task 1: DB schema — `stalled_at` column, migration v8→v9

**Files:**
- Modify: `src/movarr/database.py`
- Test: `tests/unit/test_database.py`

- [ ] **Step 1: Write failing migration test**

Add inside `TestDatabaseUpgrade` in `tests/unit/test_database.py`:

```python
def test_upgrade_from_version_8_adds_stalled_at_column(self, tmp_path: Path) -> None:
    import sqlite3

    old_path = str(tmp_path / "v8.db")
    con = sqlite3.connect(old_path)
    con.execute(
        """
        CREATE TABLE history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_title TEXT,
            result TEXT,
            result_details TEXT,
            stalled_at TEXT
        )
        """
    )
    con.execute("PRAGMA user_version = 8")
    con.commit()
    con.close()

    db = Database(old_path)
    con2 = sqlite3.connect(old_path)
    cursor = con2.execute("PRAGMA table_info(history)")
    columns = [row[1] for row in cursor.fetchall()]
    con2.close()
    assert "stalled_at" in columns
    del db
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /data/movarr && uv run pytest tests/unit/test_database.py::TestDatabaseUpgrade::test_upgrade_from_version_8_adds_stalled_at_column -v --no-cov
```

Expected: FAIL — column is already in the table (we haven't bumped version yet, so migration never runs). Actually expected: test will pass trivially because the table in the test already has `stalled_at`. The real test is that `Database()` leaves it in place. Let's instead verify the migration adds it when absent:

Replace the test body — create the v8 table WITHOUT `stalled_at`:

```python
def test_upgrade_from_version_8_adds_stalled_at_column(self, tmp_path: Path) -> None:
    import sqlite3

    old_path = str(tmp_path / "v8.db")
    con = sqlite3.connect(old_path)
    con.execute(
        """
        CREATE TABLE history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_title TEXT,
            result TEXT,
            result_details TEXT
        )
        """
    )
    con.execute("PRAGMA user_version = 8")
    con.commit()
    con.close()

    db = Database(old_path)
    con2 = sqlite3.connect(old_path)
    cursor = con2.execute("PRAGMA table_info(history)")
    columns = [row[1] for row in cursor.fetchall()]
    con2.close()
    assert "stalled_at" in columns
    del db
```

Run:
```bash
cd /data/movarr && uv run pytest tests/unit/test_database.py::TestDatabaseUpgrade::test_upgrade_from_version_8_adds_stalled_at_column -v --no-cov
```

Expected: FAIL — `stalled_at` not in columns because migration doesn't exist yet.

- [ ] **Step 3: Add `stalled_at` column to `HistoryRecord` and bump DB version**

In `src/movarr/database.py`:

Change `_DB_VERSION = 8` → `_DB_VERSION = 9`

Add column to `HistoryRecord` after `imdb_cert_source`:
```python
stalled_at = Column(String)
```

Add migration branch in `_upgrade()` after the `< 8` block:
```python
if from_version < 9:
    cursor = conn.execute(text("PRAGMA table_info(history)"))
    existing_cols = {row[1] for row in cursor.fetchall()}
    if "stalled_at" not in existing_cols:
        conn.execute(text("ALTER TABLE history ADD COLUMN stalled_at TEXT"))
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /data/movarr && uv run pytest tests/unit/test_database.py::TestDatabaseUpgrade -v --no-cov
```

Expected: all upgrade tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /data/movarr && git add src/movarr/database.py tests/unit/test_database.py
git commit -m "feat(db): add stalled_at column, migrate to schema v9"
```

---

## Task 2: `mark_stalled`, `mark_completed`, `expire_stalled` DB methods

**Files:**
- Modify: `src/movarr/database.py`
- Test: `tests/unit/test_database.py`

- [ ] **Step 1: Write failing tests**

Add three new test classes to `tests/unit/test_database.py`:

```python
class TestMarkStalled:
    """Database.mark_stalled() sets result='Stalled' and stalled_at."""

    def test_sets_result_and_stalled_at(self, db: Database, tmp_path: Path) -> None:
        db.write(_minimal_result(result="Passed", torrent_tag="movarr-abc"))
        db.mark_stalled("movarr-abc")
        record = db.find_by_tag("movarr-abc")
        assert record is not None
        assert record.result == "Stalled"
        assert record.stalled_at is not None

    def test_unknown_tag_is_noop(self, db: Database) -> None:
        db.mark_stalled("movarr-unknown")  # must not raise


class TestMarkCompleted:
    """Database.mark_completed() sets result='Completed' and verified='true'."""

    def test_sets_result_and_verified(self, db: Database) -> None:
        db.write(_minimal_result(result="Passed", torrent_tag="movarr-xyz"))
        db.mark_completed("movarr-xyz")
        record = db.find_by_tag("movarr-xyz")
        assert record is not None
        assert record.result == "Completed"
        assert record.verified == "true"

    def test_unknown_tag_is_noop(self, db: Database) -> None:
        db.mark_completed("movarr-unknown")  # must not raise


class TestExpireStalled:
    """Database.expire_stalled() deletes rows older than N days."""

    def test_deletes_old_stalled_rows(self, db: Database) -> None:
        import datetime

        db.write(_minimal_result(result="Passed", torrent_tag="movarr-old"))
        # Force stalled_at to 10 days ago
        old_ts = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=10)).isoformat()
        db.mark_stalled("movarr-old")
        # Manually backdate stalled_at
        from sqlalchemy.orm import Session as _Session
        from movarr.database import HistoryRecord
        with _Session(db._engine) as s:
            s.query(HistoryRecord).filter_by(torrent_tag="movarr-old").update({"stalled_at": old_ts})
            s.commit()

        count = db.expire_stalled(days=7)
        assert count == 1
        assert db.find_by_tag("movarr-old") is None

    def test_retains_recent_stalled_rows(self, db: Database) -> None:
        db.write(_minimal_result(result="Passed", torrent_tag="movarr-new"))
        db.mark_stalled("movarr-new")
        count = db.expire_stalled(days=7)
        assert count == 0
        assert db.find_by_tag("movarr-new") is not None

    def test_zero_days_is_noop(self, db: Database) -> None:
        db.write(_minimal_result(result="Passed", torrent_tag="movarr-any"))
        db.mark_stalled("movarr-any")
        count = db.expire_stalled(days=0)
        assert count == 0
        assert db.find_by_tag("movarr-any") is not None

    def test_returns_count_deleted(self, db: Database) -> None:
        import datetime
        from sqlalchemy.orm import Session as _Session
        from movarr.database import HistoryRecord

        for tag in ["movarr-a", "movarr-b"]:
            db.write(_minimal_result(result="Passed", torrent_tag=tag))
            db.mark_stalled(tag)
            old_ts = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=10)).isoformat()
            with _Session(db._engine) as s:
                s.query(HistoryRecord).filter_by(torrent_tag=tag).update({"stalled_at": old_ts})
                s.commit()

        count = db.expire_stalled(days=7)
        assert count == 2
```

Also check `_minimal_result` fixture supports `torrent_tag` — add it if missing:
```python
def _minimal_result(**overrides: object) -> ResultDict:
    base: ResultDict = {
        "index_title": "The Dark Knight 2008 1080p BluRay",
        "result": "Passed",
        "torrent_tag": "movarr-test-tag",
        ...
    }
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/movarr && uv run pytest tests/unit/test_database.py::TestMarkStalled tests/unit/test_database.py::TestMarkCompleted tests/unit/test_database.py::TestExpireStalled -v --no-cov
```

Expected: FAIL — methods don't exist yet.

- [ ] **Step 3: Implement the three methods in `database.py`**

Add after `set_verified`:

```python
def mark_stalled(self, torrent_tag: str) -> None:
    """Mark a torrent as stalled (deleted by queue manager with no seeds/peers).

    Sets ``result="Stalled"`` and records the UTC timestamp of detection.

    Args:
        torrent_tag: The UUID tag that identifies the torrent.
    """
    import datetime

    now = datetime.datetime.now(tz=datetime.UTC).isoformat()
    with Session(self._engine) as session:
        session.query(HistoryRecord).filter_by(torrent_tag=torrent_tag).update(
            {"result": "Stalled", "stalled_at": now}
        )
        session.commit()

def mark_completed(self, torrent_tag: str) -> None:
    """Mark a torrent as completed after successful post-processing.

    Sets ``result="Completed"`` and ``verified="true"``.

    Args:
        torrent_tag: The UUID tag that identifies the torrent.
    """
    with Session(self._engine) as session:
        session.query(HistoryRecord).filter_by(torrent_tag=torrent_tag).update(
            {"result": "Completed", "verified": "true"}
        )
        session.commit()

def expire_stalled(self, days: int) -> int:
    """Delete stalled history records older than *days* days.

    Called at the start of each search run to allow retry of titles
    whose stalled record has expired.  A *days* value of 0 disables
    expiry (no records deleted).

    Args:
        days: Retention window in days.  0 = no expiry.

    Returns:
        Number of records deleted.
    """
    if days <= 0:
        return 0

    import datetime

    cutoff = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=days)).isoformat()
    with Session(self._engine) as session:
        deleted = (
            session.query(HistoryRecord)
            .filter(
                HistoryRecord.result == "Stalled",
                HistoryRecord.stalled_at.isnot(None),
                HistoryRecord.stalled_at < cutoff,
            )
            .delete(synchronize_session=False)
        )
        session.commit()
    return int(deleted)
```

Move `import datetime` to the top of the file (alongside other stdlib imports).

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /data/movarr && uv run pytest tests/unit/test_database.py::TestMarkStalled tests/unit/test_database.py::TestMarkCompleted tests/unit/test_database.py::TestExpireStalled -v --no-cov
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /data/movarr && git add src/movarr/database.py tests/unit/test_database.py
git commit -m "feat(db): add mark_stalled, mark_completed, expire_stalled methods"
```

---

## Task 3: Update `has_passed` to include Stalled and Completed

**Files:**
- Modify: `src/movarr/database.py`
- Test: `tests/unit/test_database.py`

- [ ] **Step 1: Write failing tests**

Add to `TestHasPassed` in `tests/unit/test_database.py`:

```python
def test_returns_true_when_completed_row_exists(self, db: Database) -> None:
    db.write(_minimal_result(result="Passed", torrent_tag="movarr-comp"))
    db.mark_completed("movarr-comp")
    assert db.has_passed("The Dark Knight 2008 1080p BluRay") is True

def test_returns_true_when_stalled_row_exists(self, db: Database) -> None:
    db.write(_minimal_result(result="Passed", torrent_tag="movarr-stall"))
    db.mark_stalled("movarr-stall")
    assert db.has_passed("The Dark Knight 2008 1080p BluRay") is True

def test_returns_false_after_stalled_row_expired(self, db: Database) -> None:
    import datetime
    from sqlalchemy.orm import Session as _Session
    from movarr.database import HistoryRecord

    db.write(_minimal_result(result="Passed", torrent_tag="movarr-exp"))
    db.mark_stalled("movarr-exp")
    old_ts = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=10)).isoformat()
    with _Session(db._engine) as s:
        s.query(HistoryRecord).filter_by(torrent_tag="movarr-exp").update({"stalled_at": old_ts})
        s.commit()
    db.expire_stalled(days=7)
    assert db.has_passed("The Dark Knight 2008 1080p BluRay") is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/movarr && uv run pytest tests/unit/test_database.py::TestHasPassed::test_returns_true_when_completed_row_exists tests/unit/test_database.py::TestHasPassed::test_returns_true_when_stalled_row_exists -v --no-cov
```

Expected: FAIL — `has_passed` only checks `result="Passed"`.

- [ ] **Step 3: Update `has_passed` in `database.py`**

Replace the current `has_passed` body:

```python
def has_passed(self, index_title: str) -> bool:
    """Return True if *index_title* should be skipped by the search pipeline.

    Skips titles with result ``Passed`` (submitted, awaiting outcome),
    ``Completed`` (successfully downloaded), or ``Stalled`` (pending expiry).
    Expired stalled rows are removed by :meth:`expire_stalled` before this
    is called, so no timestamp check is needed here.

    Args:
        index_title: The raw index torrent title.
    """
    with Session(self._engine) as session:
        return (
            session.query(HistoryRecord)
            .filter(
                HistoryRecord.index_title == index_title,
                HistoryRecord.result.in_(["Passed", "Completed", "Stalled"]),
            )
            .first()
            is not None
        )
```

- [ ] **Step 4: Run all database tests**

```bash
cd /data/movarr && uv run pytest tests/unit/test_database.py -v --no-cov
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /data/movarr && git add src/movarr/database.py tests/unit/test_database.py
git commit -m "feat(db): update has_passed to block Completed and Stalled results"
```

---

## Task 4: Config — `DatabaseConfig` and migration v2.0.0→2.1.0

**Files:**
- Modify: `src/movarr/config.py`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write failing tests**

Find the config test file. Add:

```python
def test_database_config_defaults() -> None:
    from movarr.config import Config
    cfg = Config()
    assert cfg.database.stalled_expiry_days == 7


def test_migrate_v2_to_v21_adds_database_section() -> None:
    from movarr.config import migrate_config

    raw = {
        "general": {"config_version": "2.0.0"},
    }
    # Write to tmp file and migrate
    import tempfile, yaml
    from pathlib import Path
    with tempfile.NamedTemporaryFile(suffix=".yml", mode="w", delete=False) as f:
        yaml.dump(raw, f)
        tmp = Path(f.name)
    result = migrate_config(raw, tmp)
    assert result["general"]["config_version"] == "2.1.0"
    assert result["database"]["stalled_expiry_days"] == 7
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/movarr && uv run pytest tests/unit/test_config.py -k "database" -v --no-cov
```

Expected: FAIL — `Config` has no `database` field.

- [ ] **Step 3: Add `DatabaseConfig` to `config.py`**

Add after `PostProcessConfig`:

```python
class DatabaseConfig(BaseModel):
    """Database settings."""

    stalled_expiry_days: int = 7
```

Add `database` field to `Config`:

```python
database: DatabaseConfig = Field(default_factory=DatabaseConfig)
```

Add migration function and register it:

```python
_CONFIG_VERSION = "2.1.0"


def _migrate_v2_to_v21(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.0.0 → v2.1.0: add database.stalled_expiry_days."""
    raw.setdefault("database", {}).setdefault("stalled_expiry_days", 7)
    raw.setdefault("general", {})["config_version"] = "2.1.0"
    return raw


MIGRATIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "1.0.0": _migrate_v1_to_v2,
    "2.0.0": _migrate_v2_to_v21,
}
```

Also update the line that stamps new configs:
```python
raw.setdefault("general", {})["config_version"] = "2.1.0"
```

- [ ] **Step 4: Run config tests**

```bash
cd /data/movarr && uv run pytest tests/unit/test_config.py -v --no-cov
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /data/movarr && git add src/movarr/config.py tests/unit/test_config.py
git commit -m "feat(config): add DatabaseConfig with stalled_expiry_days, config v2.1.0"
```

---

## Task 5: Queue manager — call `mark_stalled` after deletions

**Files:**
- Modify: `src/movarr/queue_manager.py`
- Test: `tests/unit/test_queue_manager.py`

- [ ] **Step 1: Write failing tests**

Open `tests/unit/test_queue_manager.py` and add a test class:

```python
class TestMarkStalledOnDeletion:
    """Queue manager must call db.mark_stalled for each deleted torrent."""

    def test_mark_stalled_called_for_stalled_torrents(self, mocker: MockerFixture) -> None:
        from movarr.queue_manager import run_queue_management
        from movarr.config import Config

        config = Config()
        config.queue_management.queue_management_enabled = True
        config.queue_management.stalled_monitor_enabled = True
        config.queue_management.metadata_monitor_enabled = False

        mock_qbt = mocker.MagicMock()
        mock_db = mocker.MagicMock()

        # list_by_category returns one stalledDL torrent tagged with movarr tag
        mock_qbt.is_connected.return_value = True
        mock_qbt.list_by_category.return_value = {
            "abc123": {
                "state": "stalledDL",
                "name": "Some Movie 2024",
                "tags": "movarr-uuid-stalled",
                "last_activity": 0,
                "added_on": 0,
            }
        }
        mock_qbt.identify_for_deletion.return_value = {
            "abc123": {"name": "Some Movie 2024", "age_mins": 999, "state": "stalledDL"}
        }

        run_queue_management(config, mock_qbt, mock_db)

        mock_db.mark_stalled.assert_called_once_with("movarr-uuid-stalled")

    def test_mark_stalled_called_for_metadata_torrents(self, mocker: MockerFixture) -> None:
        from movarr.queue_manager import run_queue_management
        from movarr.config import Config

        config = Config()
        config.queue_management.queue_management_enabled = True
        config.queue_management.stalled_monitor_enabled = False
        config.queue_management.metadata_monitor_enabled = True

        mock_qbt = mocker.MagicMock()
        mock_db = mocker.MagicMock()

        mock_qbt.is_connected.return_value = True
        mock_qbt.list_by_category.return_value = {
            "def456": {
                "state": "metaDL",
                "name": "Other Movie 2023",
                "tags": "movarr-uuid-meta",
                "last_activity": 0,
                "added_on": 0,
            }
        }
        mock_qbt.identify_for_deletion.return_value = {
            "def456": {"name": "Other Movie 2023", "age_mins": 999, "state": "metaDL"}
        }

        run_queue_management(config, mock_qbt, mock_db)

        mock_db.mark_stalled.assert_called_once_with("movarr-uuid-meta")

    def test_no_mark_stalled_when_nothing_deleted(self, mocker: MockerFixture) -> None:
        from movarr.queue_manager import run_queue_management
        from movarr.config import Config

        config = Config()
        mock_qbt = mocker.MagicMock()
        mock_db = mocker.MagicMock()

        mock_qbt.is_connected.return_value = True
        mock_qbt.list_by_category.return_value = {}
        mock_qbt.identify_for_deletion.return_value = {}

        run_queue_management(config, mock_qbt, mock_db)

        mock_db.mark_stalled.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/movarr && uv run pytest tests/unit/test_queue_manager.py::TestMarkStalledOnDeletion -v --no-cov
```

Expected: FAIL — `run_queue_management` doesn't accept `db`.

- [ ] **Step 3: Update `queue_manager.py`**

```python
from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from movarr.config import Config
    from movarr.database import Database
    from movarr.qbittorrent import QBittorrentClient

__all__ = ["run_queue_management"]

_TAG_PREFIX = "movarr-"


def run_queue_management(config: Config, qbt: QBittorrentClient, db: Database) -> None:
    """Check for stuck torrents and delete them.

    Args:
        config: Application configuration.
        qbt: An already-connected ``QBittorrentClient`` instance.
        db: History database (used to mark deleted torrents as stalled).
    """
    qm_cfg = config.queue_management
    if not qm_cfg.queue_management_enabled:
        logger.debug("Queue management disabled; skipping.")
        return

    if not qbt.is_connected():
        logger.warning("qBittorrent is unreachable; skipping queue management.")
        return

    if qm_cfg.metadata_monitor_enabled:
        _delete_stuck(
            qbt=qbt,
            db=db,
            state="metaDL",
            filter_type="added_on",
            max_mins=qm_cfg.metadata_delete_torrent_max_mins,
            label="metadata",
            delete_data=qm_cfg.metadata_delete_torrent_data,
        )

    if qm_cfg.stalled_monitor_enabled:
        _delete_stuck(
            qbt=qbt,
            db=db,
            state="stalledDL",
            filter_type="last_activity",
            max_mins=qm_cfg.stalled_delete_torrent_max_mins,
            label="stalled",
            delete_data=qm_cfg.stalled_delete_torrent_data,
        )


def _delete_stuck(
    qbt: QBittorrentClient,
    db: Database,
    state: str,
    filter_type: str,
    max_mins: int,
    label: str,
    delete_data: bool,
) -> None:
    torrent_map = qbt.list_by_category()
    if not torrent_map:
        return

    to_delete = qbt.identify_for_deletion(
        torrent_map=torrent_map,
        state=state,
        delay_max_mins=max_mins,
        filter_type=filter_type,
    )

    if not to_delete:
        logger.debug("No {} torrents to delete.", label)
        return

    logger.info("Deleting {} {} torrent(s) in state '{}'.", len(to_delete), label, state)
    qbt.delete_stalled(to_delete, state=state, delete_data=delete_data)

    for torrent_hash in to_delete:
        torrent_info = torrent_map.get(torrent_hash, {})
        raw_tags: str = torrent_info.get("tags", "") or ""
        tag = next(
            (t.strip() for t in raw_tags.split(",") if t.strip().startswith(_TAG_PREFIX)),
            None,
        )
        if tag:
            db.mark_stalled(tag)
            logger.debug("Marked torrent '{}' (tag='{}') as Stalled in DB.", torrent_hash, tag)
        else:
            logger.debug("No movarr tag found for deleted torrent '{}'; skipping DB update.", torrent_hash)
```

- [ ] **Step 4: Run all queue manager tests**

```bash
cd /data/movarr && uv run pytest tests/unit/test_queue_manager.py -v --no-cov
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /data/movarr && git add src/movarr/queue_manager.py tests/unit/test_queue_manager.py
git commit -m "feat(queue): mark deleted torrents as Stalled in DB"
```

---

## Task 6: Post-processor — replace `set_verified` with `mark_completed`

**Files:**
- Modify: `src/movarr/post_processor.py`
- Test: `tests/unit/test_post_processor.py`

- [ ] **Step 1: Write failing test**

In `tests/unit/test_post_processor.py`, find the test that asserts `db.set_verified` is called and add/modify:

```python
def test_mark_completed_called_after_successful_copy(mocker: MockerFixture, tmp_path: Path) -> None:
    """Post-processor calls db.mark_completed, not set_verified."""
    from movarr.post_processor import _process_one
    from movarr.config import Config
    from movarr.database import HistoryRecord

    config = Config()
    config.post_process.post_process_enabled = True
    config.post_process.copy_completed = True
    config.post_process.remove_completed = False

    mock_qbt = mocker.MagicMock()
    mock_db = mocker.MagicMock()

    record = mocker.MagicMock(spec=HistoryRecord)
    record.imdb_title = "The Dark Knight"
    record.imdb_year = "2008"
    record.imdb_genres_list = '["action"]'
    record.imdb_certification = None

    mock_db.find_by_tag.return_value = record

    src = tmp_path / "movie.mkv"
    src.write_bytes(b"x" * 2_000_000_000)

    torrent = {
        "torrent_tag": "movarr-test",
        "torrent_hash": "abc123",
        "torrent_save_path": str(tmp_path),
        "torrent_file_list": [{"file_name": "movie.mkv", "file_size": 2_000_000_000}],
    }

    mocker.patch("movarr.post_processor.copy_with_verify", return_value=True)
    mocker.patch("movarr.post_processor.make_directory", return_value=True)

    _process_one(torrent, config, mock_qbt, mock_db)

    mock_db.mark_completed.assert_called_once_with("movarr-test")
    mock_db.set_verified.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /data/movarr && uv run pytest tests/unit/test_post_processor.py::test_mark_completed_called_after_successful_copy -v --no-cov
```

Expected: FAIL — `set_verified` is still called.

- [ ] **Step 3: Update `post_processor.py`**

In `_process_one`, replace:
```python
if all_ok:
    db.set_verified(tag)
    logger.info("Marked tag '{}' as verified.", tag)
```
with:
```python
if all_ok:
    db.mark_completed(tag)
    logger.info("Marked tag '{}' as completed.", tag)
```

- [ ] **Step 4: Run all post-processor tests**

```bash
cd /data/movarr && uv run pytest tests/unit/test_post_processor.py -v --no-cov
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /data/movarr && git add src/movarr/post_processor.py tests/unit/test_post_processor.py
git commit -m "feat(post_processor): replace set_verified with mark_completed"
```

---

## Task 7: Scheduler — wire `db` to queue management, call `expire_stalled`

**Files:**
- Modify: `src/movarr/scheduler.py`
- Test: `tests/unit/test_scheduler.py`

- [ ] **Step 1: Write failing tests**

In `tests/unit/test_scheduler.py` add:

```python
def test_expire_stalled_called_before_search(mocker: MockerFixture) -> None:
    """run_once must call db.expire_stalled before run_search."""
    from movarr.scheduler import run_once
    from movarr.config import Config

    config = Config()
    config.database.stalled_expiry_days = 5

    mock_db = mocker.MagicMock()
    mock_qbt = mocker.MagicMock()
    mock_qbt.is_connected.return_value = True

    mocker.patch("movarr.scheduler.Database", return_value=mock_db)
    mocker.patch("movarr.scheduler._connect_qbt", return_value=mock_qbt)
    mocker.patch("movarr.scheduler.run_search")
    mocker.patch("movarr.scheduler.run_queue_management")
    mocker.patch("movarr.scheduler.run_post_processing")

    run_once(config)

    mock_db.expire_stalled.assert_called_once_with(5)


def test_queue_management_receives_db(mocker: MockerFixture) -> None:
    """run_once must pass db to run_queue_management."""
    from movarr.scheduler import run_once
    from movarr.config import Config

    config = Config()
    mock_db = mocker.MagicMock()
    mock_qbt = mocker.MagicMock()

    mocker.patch("movarr.scheduler.Database", return_value=mock_db)
    mocker.patch("movarr.scheduler._connect_qbt", return_value=mock_qbt)
    mocker.patch("movarr.scheduler.run_search")
    mock_qm = mocker.patch("movarr.scheduler.run_queue_management")
    mocker.patch("movarr.scheduler.run_post_processing")

    run_once(config)

    mock_qm.assert_called_once_with(config, mock_qbt, mock_db)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/movarr && uv run pytest tests/unit/test_scheduler.py::test_expire_stalled_called_before_search tests/unit/test_scheduler.py::test_queue_management_receives_db -v --no-cov
```

Expected: FAIL.

- [ ] **Step 3: Update `scheduler.py`**

In `run_once`, add `expire_stalled` call and pass `db` to queue management:

```python
def run_once(config: Config) -> None:
    """Execute each task exactly once (foreground / test mode)."""
    logger.info("movarr running in single-pass foreground mode.")

    db = Database(config.general.db_path)
    qbt = _connect_qbt(config)

    _task_search(config, qbt, db)
    _task_queue_management(config, qbt, db)
    _task_post_processing(config, qbt, db)

    logger.info("Single-pass complete.")
```

Update `_task_search` to call `expire_stalled` first:

```python
def _task_search(config: Config, qbt: QBittorrentClient, db: Database) -> None:
    try:
        db.expire_stalled(config.database.stalled_expiry_days)
        run_search(config, qbt, db)
    except Exception:
        logger.exception("Search task failed.")
```

Update `_task_queue_management` signature and the daemon scheduler:

```python
def _task_queue_management(config: Config, qbt: QBittorrentClient, db: Database) -> None:
    try:
        run_queue_management(config, qbt, db)
    except Exception:
        logger.exception("Queue management task failed.")
```

In `_run_daemon`, update the queue management job to pass `db`:

```python
scheduler.add_job(
    _task_queue_management,
    trigger="interval",
    minutes=qm_mins,
    args=(config, qbt, db),
    id="queue_management",
    name="Queue management (delete stuck torrents)",
    max_instances=1,
    coalesce=True,
)
```

- [ ] **Step 4: Run all scheduler tests**

```bash
cd /data/movarr && uv run pytest tests/unit/test_scheduler.py -v --no-cov
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /data/movarr && git add src/movarr/scheduler.py tests/unit/test_scheduler.py
git commit -m "feat(scheduler): wire expire_stalled and db to queue management"
```

---

## Task 8: Full suite + pre-commit

- [ ] **Step 1: Run full test suite**

```bash
cd /data/movarr && uv run pytest --tb=short -q
```

Expected: all tests pass (620+ passing).

- [ ] **Step 2: Run pre-commit**

```bash
cd /data/movarr && uv run pre-commit run --all-files
```

Expected: all checks pass.

- [ ] **Step 3: Fix any issues found and re-run**

Address any type errors, formatting issues, or test failures before proceeding.

- [ ] **Step 4: Final commit if any cleanup needed**

```bash
cd /data/movarr && git add -A && git commit -m "chore: pre-commit fixes for torrent status tracking"
```

---

## Self-Review

**Spec coverage check:**
- ✅ `stalled_at` column + migration v8→v9 → Task 1
- ✅ `mark_stalled`, `mark_completed`, `expire_stalled` → Task 2
- ✅ `has_passed` includes Stalled/Completed → Task 3
- ✅ `database.stalled_expiry_days` config + migration v2.0.0→2.1.0 → Task 4
- ✅ Queue manager marks deleted torrents as Stalled → Task 5
- ✅ Post-processor uses `mark_completed` → Task 6
- ✅ Scheduler calls `expire_stalled` before search → Task 7
- ✅ `stalled_expiry_days: 0` disables expiry → covered in `expire_stalled` implementation

**Placeholder scan:** None found.

**Type consistency:** `torrent_tag: str` used consistently across all methods. `expire_stalled(days: int) -> int` signature consistent across implementation and tests.
