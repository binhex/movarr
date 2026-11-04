"""Unit tests for movarr.database — CRUD and deduplication."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from movarr.database import Database

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture

    from movarr.models import ResultDict

# Fixtures


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    """In-memory SQLite database for each test."""
    db_file = str(tmp_path / "test.db")
    return Database(db_file)


def _minimal_result(**overrides: object) -> ResultDict:
    """Return a minimal ResultDict suitable for writing to the DB."""
    base: ResultDict = {
        "index_title": "The Dark Knight 2008 1080p BluRay",
        "torrent_hash": "abc123def456",
        "torrent_tag": "uuid-test-001",
        "result": "Passed",
        "result_details": [],
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


# write + is_duplicate


class TestDatabaseInit:
    """Database.__init__ accepts directory paths (no extension suffix)."""

    def test_directory_path_creates_movarr_db(self, tmp_path: Path) -> None:
        """When db_path has no suffix, movarr.db is created inside the directory."""
        db_dir = tmp_path / "mydb"
        database = Database(str(db_dir))
        assert database._db_path == db_dir / "movarr.db"
        assert database._db_path.parent.exists()


class TestWrite:
    """Database.write() must persist a ResultDict."""

    def test_write_and_exact_duplicate_detected(self, db: Database) -> None:
        result = _minimal_result()
        db.write(result)
        assert db.is_duplicate_exact("The Dark Knight 2008 1080p BluRay") is True

    def test_unknown_title_not_duplicate(self, db: Database) -> None:
        assert db.is_duplicate_exact("Unknown Movie 2099") is False


# read_by_tag / find_by_tag


class TestFindByTag:
    """Database.find_by_tag() must return the correct record or None."""

    def test_returns_record_for_known_tag(self, db: Database) -> None:
        result = _minimal_result(torrent_tag="unique-tag-001")
        db.write(result)
        record = db.find_by_tag("unique-tag-001")
        assert record is not None
        assert record.torrent_tag == "unique-tag-001"

    def test_returns_none_for_unknown_tag(self, db: Database) -> None:
        assert db.find_by_tag("no-such-tag") is None


# IMDb metadata cache


class TestFindImdbMetadata:
    """Database.find_imdb_metadata() must return cached fields by imdb_id."""

    def test_cache_hit_returns_metadata_dict(self, db: Database) -> None:
        db.write(
            _minimal_result(
                torrent_tag="cache-hit-001",
                imdb_id="tt0468569",
                imdb_title="The Dark Knight",
                imdb_year="2008",
                imdb_rating="9.0",
                imdb_votes="2500000",
                imdb_genres_list=["Action", "Crime", "Drama"],
                imdb_certification="PG-13",
                imdb_cert_source="imdbpie",
            )
        )
        cached = db.find_imdb_metadata("tt0468569")
        assert cached is not None
        assert cached["imdb_title"] == "The Dark Knight"
        assert cached["imdb_year"] == "2008"
        assert cached["imdb_rating"] == "9.0"
        assert cached["imdb_genres_list"] == ["Action", "Crime", "Drama"]
        assert cached["imdb_certification"] == "PG-13"
        assert cached["imdb_cert_source"] == "imdbpie"

    def test_cache_miss_returns_none(self, db: Database) -> None:
        assert db.find_imdb_metadata("tt0000000") is None

    def test_cache_ignores_rows_without_imdb_title(self, db: Database) -> None:
        """Rows where the metadata fetch never succeeded (imdb_title is None) are ignored."""
        db.write(_minimal_result(torrent_tag="no-title-001", imdb_id="tt9999999"))
        assert db.find_imdb_metadata("tt9999999") is None

    def test_cache_prefers_most_recent_row(self, db: Database) -> None:
        """When multiple rows exist, the one with the highest id is returned."""
        db.write(
            _minimal_result(
                torrent_tag="old-001",
                imdb_id="tt0468569",
                imdb_title="Old Title",
                imdb_year="2007",
            )
        )
        db.write(
            _minimal_result(
                torrent_tag="new-001",
                imdb_id="tt0468569",
                imdb_title="The Dark Knight",
                imdb_year="2008",
            )
        )
        cached = db.find_imdb_metadata("tt0468569")
        assert cached is not None
        assert cached["imdb_title"] == "The Dark Knight"
        assert cached["imdb_year"] == "2008"

    def test_cache_decodes_json_fields(self, db: Database) -> None:
        db.write(
            _minimal_result(
                torrent_tag="json-001",
                imdb_id="tt1234567",
                imdb_title="Test",
                imdb_language_list=["en", "fr"],
                imdb_country_list=["us", "gb"],
                imdb_credits_director_list=["Christopher Nolan"],
                imdb_credits_writer_list=["Jonathan Nolan"],
                imdb_credits_cast_list=[["Christian Bale", "Bruce Wayne"]],
                imdb_credits_character_list=["Bruce Wayne"],
            )
        )
        cached = db.find_imdb_metadata("tt1234567")
        assert cached is not None
        assert cached["imdb_language_list"] == ["en", "fr"]
        assert cached["imdb_country_list"] == ["us", "gb"]
        assert cached["imdb_credits_director_list"] == ["Christopher Nolan"]
        assert cached["imdb_credits_writer_list"] == ["Jonathan Nolan"]
        assert cached["imdb_credits_cast_list"] == [["Christian Bale", "Bruce Wayne"]]
        assert cached["imdb_credits_character_list"] == ["Bruce Wayne"]

    def test_cache_decode_returns_non_string_value_unchanged(self, db: Database, tmp_path: Path) -> None:
        """If a column somehow stores a non-string value, _decode_field passes it through."""
        import sqlite3

        db.write(_minimal_result(torrent_tag="non-str-001", imdb_id="tt5555555", imdb_title="Test"))
        raw_path = str(tmp_path / "test.db")
        con = sqlite3.connect(raw_path)
        # Update the row directly to bypass ORM encoding.
        con.execute(
            "UPDATE history SET imdb_genres_list = 42 WHERE torrent_tag = ?",
            ("non-str-001",),
        )
        con.commit()
        con.close()
        db2 = Database(raw_path)
        cached = db2.find_imdb_metadata("tt5555555")
        assert cached is not None
        assert cached["imdb_genres_list"] == 42

    def test_decode_field_function(self) -> None:
        """_decode_field must handle strings, None, and non-strings."""
        from movarr.database import _decode_field

        assert _decode_field(None) is None
        assert _decode_field('["Action"]') == ["Action"]
        assert _decode_field("plain string") == "plain string"
        assert _decode_field(42) == 42
        assert _decode_field(["already", "a", "list"]) == ["already", "a", "list"]


# Database upgrade path


class TestDatabaseUpgrade:
    """Database._ensure_schema() must run _upgrade() on an older schema."""

    def test_upgrade_from_version_5_adds_cert_columns(self, tmp_path: Path) -> None:
        import sqlite3

        old_path = str(tmp_path / "old.db")
        con = sqlite3.connect(old_path)
        con.execute(
            """
            CREATE TABLE history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                index_title TEXT,
                torrent_hash TEXT,
                torrent_tag TEXT UNIQUE,
                result TEXT,
                result_details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        con.execute("PRAGMA user_version = 5")
        con.commit()
        con.close()

        db = Database(old_path)
        con2 = sqlite3.connect(old_path)
        cursor = con2.execute("PRAGMA table_info(history)")
        columns = [row[1] for row in cursor.fetchall()]
        con2.close()
        assert "imdb_certification" in columns
        assert "imdb_cert_source" in columns
        del db  # ensure object is not garbage collected before assertion

    def test_unversioned_legacy_db_is_upgraded(self, tmp_path: Path) -> None:
        """A pre-versioning DB (user_version=0, history table present) is fully upgraded."""
        import sqlite3

        old_path = str(tmp_path / "legacy.db")
        con = sqlite3.connect(old_path)
        con.execute("CREATE TABLE history (id INTEGER PRIMARY KEY AUTOINCREMENT, index_title TEXT)")
        con.commit()
        con.close()

        db = Database(old_path)
        version = db._get_user_version()
        del db

        assert version == 13

    def test_upgrade_from_version_7_creates_index_title_index(self, tmp_path: Path) -> None:
        import sqlite3

        old_path = str(tmp_path / "v7.db")
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
        con.execute("PRAGMA user_version = 7")
        con.commit()
        con.close()

        db = Database(old_path)
        con2 = sqlite3.connect(old_path)
        cursor = con2.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='history'")
        index_names = [row[0] for row in cursor.fetchall()]
        con2.close()
        assert "ix_history_index_title" in index_names
        del db


# mark_stalled


class TestMarkStalled:
    """Database.mark_stalled() sets result='Stalled' and stalled_at."""

    def test_sets_result_and_stalled_at(self, db: Database) -> None:
        db.write(_minimal_result(torrent_tag="movarr-abc"))
        db.mark_stalled("movarr-abc")
        record = db.find_by_tag("movarr-abc")
        assert record is not None
        assert record.result == "Stalled"
        assert record.stalled_at is not None

    def test_unknown_tag_is_noop(self, db: Database) -> None:
        db.mark_stalled("movarr-unknown")  # must not raise
        assert db.find_by_tag("movarr-unknown") is None


# mark_completed


class TestMarkCompleted:
    """Database.mark_completed() sets result='Completed' and verified='true'."""

    def test_sets_result_and_verified(self, db: Database) -> None:
        db.write(_minimal_result(torrent_tag="movarr-xyz"))
        db.mark_completed("movarr-xyz")
        record = db.find_by_tag("movarr-xyz")
        assert record is not None
        assert record.result == "Completed"
        assert record.verified == "true"

    def test_unknown_tag_is_noop(self, db: Database) -> None:
        db.mark_completed("movarr-unknown")  # must not raise
        assert db.find_by_tag("movarr-unknown") is None


# expire_stalled


class TestExpireStalled:
    """Database.expire_stalled() deletes rows older than N days."""

    def _backdate(self, db: Database, torrent_tag: str, days_ago: int) -> None:
        """Force stalled_at to *days_ago* days in the past."""
        import datetime

        from sqlalchemy.orm import Session as _Session

        from movarr.database import HistoryRecord

        old_ts = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=days_ago)).isoformat()
        with _Session(db._engine) as s:
            s.query(HistoryRecord).filter_by(torrent_tag=torrent_tag).update({"stalled_at": old_ts})
            s.commit()

    def test_deletes_old_stalled_rows(self, db: Database) -> None:
        db.write(_minimal_result(torrent_tag="movarr-old"))
        db.mark_stalled("movarr-old")
        self._backdate(db, "movarr-old", days_ago=10)

        count = db.expire_stalled(days=7)

        assert count == 1
        assert db.find_by_tag("movarr-old") is None

    def test_retains_recent_stalled_rows(self, db: Database) -> None:
        db.write(_minimal_result(torrent_tag="movarr-new"))
        db.mark_stalled("movarr-new")

        count = db.expire_stalled(days=7)

        assert count == 0
        assert db.find_by_tag("movarr-new") is not None

    def test_zero_days_is_noop(self, db: Database) -> None:
        db.write(_minimal_result(torrent_tag="movarr-any"))
        db.mark_stalled("movarr-any")
        self._backdate(db, "movarr-any", days_ago=100)

        count = db.expire_stalled(days=0)

        assert count == 0
        assert db.find_by_tag("movarr-any") is not None

    def test_returns_count_deleted(self, db: Database) -> None:
        for tag in ["movarr-a", "movarr-b"]:
            db.write(_minimal_result(index_title=f"Movie {tag}", torrent_tag=tag))
            db.mark_stalled(tag)
            self._backdate(db, tag, days_ago=10)

        count = db.expire_stalled(days=7)

        assert count == 2

    def test_does_not_delete_completed_rows(self, db: Database) -> None:
        db.write(_minimal_result(torrent_tag="movarr-comp"))
        db.mark_completed("movarr-comp")

        count = db.expire_stalled(days=1)

        assert count == 0
        assert db.find_by_tag("movarr-comp") is not None


class TestExpireFailed:
    """Database.expire_failed() deletes Failed rows older than N days."""

    def _backdate_created(self, db: Database, index_title: str, days_ago: int) -> None:
        """Force created_at to *days_ago* days in the past."""
        import datetime

        from sqlalchemy.orm import Session as _Session

        from movarr.database import HistoryRecord

        old_ts = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=days_ago)).isoformat()
        with _Session(db._engine) as s:
            s.query(HistoryRecord).filter_by(index_title=index_title).update({"created_at": old_ts})
            s.commit()

    def test_deletes_old_failed_rows(self, db: Database) -> None:
        db.write({**_minimal_result(), "result": "Failed", "index_title": "Old Failed Movie"})
        self._backdate_created(db, "Old Failed Movie", days_ago=10)

        count = db.expire_failed(days=7)

        assert count == 1

    def test_retains_recent_failed_rows(self, db: Database) -> None:
        db.write({**_minimal_result(), "result": "Failed", "index_title": "Recent Failed Movie"})

        count = db.expire_failed(days=7)

        assert count == 0

    def test_zero_days_is_noop(self, db: Database) -> None:
        db.write({**_minimal_result(), "result": "Failed", "index_title": "Any Failed Movie"})
        self._backdate_created(db, "Any Failed Movie", days_ago=100)

        count = db.expire_failed(days=0)

        assert count == 0

    def test_does_not_delete_passed_rows(self, db: Database) -> None:
        db.write({**_minimal_result(), "result": "Passed", "index_title": "Passed Movie"})
        self._backdate_created(db, "Passed Movie", days_ago=10)

        count = db.expire_failed(days=7)

        assert count == 0

    def test_returns_count_deleted(self, db: Database) -> None:
        for title in ["Failed Alpha", "Failed Beta"]:
            db.write({**_minimal_result(), "result": "Failed", "index_title": title})
            self._backdate_created(db, title, days_ago=10)

        count = db.expire_failed(days=7)

        assert count == 2

    def test_created_at_set_on_write(self, db: Database) -> None:
        """write() should populate created_at with an ISO timestamp."""
        from sqlalchemy.orm import Session as _Session

        from movarr.database import HistoryRecord

        db.write({**_minimal_result(), "index_title": "TimestampTest"})
        with _Session(db._engine) as s:
            record = s.query(HistoryRecord).filter_by(index_title="TimestampTest").first()
        assert record is not None
        assert record.created_at is not None
        assert "T" in record.created_at  # ISO 8601 format


class TestExpirePassed:
    """Database.expire_passed() deletes Passed rows older than N days."""

    def _backdate_created(self, db: Database, index_title: str, days_ago: int) -> None:
        """Force created_at to *days_ago* days in the past."""
        import datetime

        from sqlalchemy.orm import Session as _Session

        from movarr.database import HistoryRecord

        old_ts = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=days_ago)).isoformat()
        with _Session(db._engine) as s:
            s.query(HistoryRecord).filter_by(index_title=index_title).update({"created_at": old_ts})
            s.commit()

    def test_deletes_old_passed_rows(self, db: Database) -> None:
        db.write({**_minimal_result(), "result": "Passed", "index_title": "Old Passed Movie"})
        self._backdate_created(db, "Old Passed Movie", days_ago=35)

        count = db.expire_passed(days=30)

        assert count == 1

    def test_retains_recent_passed_rows(self, db: Database) -> None:
        db.write({**_minimal_result(), "result": "Passed", "index_title": "Recent Passed Movie"})

        count = db.expire_passed(days=30)

        assert count == 0

    def test_zero_days_is_noop(self, db: Database) -> None:
        db.write({**_minimal_result(), "result": "Passed", "index_title": "Any Passed Movie"})
        self._backdate_created(db, "Any Passed Movie", days_ago=100)

        count = db.expire_passed(days=0)

        assert count == 0

    def test_does_not_delete_completed_rows(self, db: Database) -> None:
        db.write({**_minimal_result(), "result": "Completed", "index_title": "Completed Movie"})
        self._backdate_created(db, "Completed Movie", days_ago=35)

        count = db.expire_passed(days=30)

        assert count == 0

    def test_does_not_delete_failed_rows(self, db: Database) -> None:
        db.write({**_minimal_result(), "result": "Failed", "index_title": "Failed Movie"})
        self._backdate_created(db, "Failed Movie", days_ago=35)

        count = db.expire_passed(days=30)

        assert count == 0

    def test_returns_count_deleted(self, db: Database) -> None:
        for title in ["Passed Alpha", "Passed Beta"]:
            db.write({**_minimal_result(), "result": "Passed", "index_title": title})
            self._backdate_created(db, title, days_ago=35)

        count = db.expire_passed(days=30)

        assert count == 2


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

    def test_kv_delete_concurrent_same_key_no_warning(self, tmp_path: Path) -> None:
        """Concurrent kv_delete on the same key must not produce SAWarning.

        Regression test: the old ORM load-then-delete pattern loaded a row
        into the session before deleting.  Two concurrent threads could both
        load the same row; the second commit would see a stale object and
        emit ``SAWarning: DELETE statement on table 'kv_store' expected to
        delete 1 row(s); 0 were matched``.

        The bulk ``DELETE … WHERE key = ?`` replacement is naturally
        idempotent — both threads succeed silently.
        """
        import threading
        import warnings

        db = Database(tmp_path / "test.db")
        db.kv_set("concurrent.key", "val")

        failures: list[str] = []
        barrier = threading.Barrier(2, timeout=5)

        def _delete() -> None:
            barrier.wait()
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                db.kv_delete("concurrent.key")
                for w in caught:
                    msg = str(w.message)
                    if "kv_store" in msg and "expected to delete" in msg:
                        failures.append(msg)

        t1 = threading.Thread(target=_delete)
        t2 = threading.Thread(target=_delete)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not failures, f"SAWarning raised during concurrent kv_delete: {failures}"
        assert db.kv_get("concurrent.key") is None

    def test_kv_set_concurrent_same_key_no_error(self, tmp_path: Path) -> None:
        """Concurrent kv_set on the same key must not raise IntegrityError.

        The old ORM load-then-decide pattern could cause a duplicate-key
        insert when two threads both saw the key as absent.  The atomic
        INSERT … ON CONFLICT DO UPDATE replacement avoids this.
        """
        import threading

        db = Database(tmp_path / "test.db")
        errors: list[Exception] = []
        barrier = threading.Barrier(2, timeout=5)

        def _upsert() -> None:
            barrier.wait()
            try:
                db.kv_set("concurrent.key", "val")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t1 = threading.Thread(target=_upsert)
        t2 = threading.Thread(target=_upsert)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Exception during concurrent kv_set: {errors}"
        assert db.kv_get("concurrent.key") == "val"

    def test_kv_store_table_created_on_new_db(self, tmp_path: Path) -> None:
        """kv_store table exists after Database init."""
        db = Database(tmp_path / "test.db")
        with db._engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='kv_store'"))
            assert result.fetchone() is not None

    def test_db_version_is_13(self, tmp_path: Path) -> None:
        """New database is created at schema version 13."""
        db = Database(tmp_path / "test.db")
        assert db._get_user_version() == 13

    def test_kv_store_migration_from_v10(self, tmp_path: Path) -> None:
        """kv_store table is created and usable when upgrading a v10 database."""
        import sqlite3

        db_path = tmp_path / "legacy.db"
        # Create a bare v10 DB (history table exists, no kv_store)
        raw = sqlite3.connect(str(db_path))
        raw.execute("CREATE TABLE history (id INTEGER PRIMARY KEY, index_title TEXT)")
        raw.execute("PRAGMA user_version = 10")
        raw.commit()
        raw.close()

        db = Database(db_path)
        assert db._get_user_version() == 13

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
        assert db._get_user_version() == 13
        assert db.kv_get("index_proxy.zero_results_since") is None
        assert db.kv_get("index_proxy.unavailable_since") == "2026-01-01T00:00:00+00:00"


class TestDualTitleCacheColumns:
    """Schema v13 persists the dual-title fields the library matcher needs."""

    def test_v13_adds_dual_title_columns(self, tmp_path: Path) -> None:
        """Upgrading from v12 adds imdb_original_title and imdb_alternate_titles."""
        import sqlite3

        old_path = str(tmp_path / "v12.db")
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
        con.execute("PRAGMA user_version = 12")
        con.commit()
        con.close()

        db = Database(old_path)
        con2 = sqlite3.connect(old_path)
        columns = [row[1] for row in con2.execute("PRAGMA table_info(history)").fetchall()]
        version = con2.execute("PRAGMA user_version").fetchone()[0]
        con2.close()
        del db

        assert "imdb_original_title" in columns
        assert "imdb_alternate_titles" in columns
        assert version == 13

    def test_dual_title_fields_round_trip_through_cache(self, db: Database) -> None:
        """Cached metadata must return the original and alternate titles."""
        db.write(
            _minimal_result(
                torrent_tag="dual-title",
                imdb_id="tt0133093",
                imdb_title="The Matrix",
                imdb_original_title="Матрица",
                imdb_alternate_titles=["Matrix", "Die Matrix"],
            )
        )

        cached = db.find_imdb_metadata("tt0133093")

        assert cached is not None
        assert cached["imdb_original_title"] == "Матрица"
        assert cached["imdb_alternate_titles"] == ["Matrix", "Die Matrix"]


class TestDbPathResolution:
    """A directory whose name contains a dot is still a directory."""

    def test_directory_with_dot_in_name_gets_movarr_db(self, tmp_path: Path) -> None:
        """Database(path) treats an existing dotted directory as a directory."""
        dotted = tmp_path / "movarr-1.3"
        dotted.mkdir()

        db = Database(dotted)
        db.write(_minimal_result())

        assert (dotted / "movarr.db").is_file()
        assert not dotted.is_file()


class TestEncodeField:
    """_encode_field serialises lists as JSON and passes scalars through."""

    def test_list_is_json_encoded(self, db: Database) -> None:
        db.write(_minimal_result(imdb_genres_list=["Action", "Drama"]))
        record = db.find_by_tag("uuid-test-001")
        assert record is not None
        assert record.imdb_genres_list == '["Action", "Drama"]'

    def test_scalar_is_stringified(self, db: Database) -> None:
        """A scalar in a list-typed field is stored via str() rather than crashing."""
        db.write(_minimal_result(imdb_genres_list="Action"))
        record = db.find_by_tag("uuid-test-001")
        assert record is not None
        assert record.imdb_genres_list == "Action"


class TestLegacyColumnRepair:
    """An unversioned legacy table is repaired for columns added before v7."""

    def test_missing_pre_v7_columns_are_added(self, tmp_path: Path) -> None:
        """A v5-era table lacking torrent_tag/verified must still be writable."""
        import sqlite3

        old_path = str(tmp_path / "v5.db")
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
        con.commit()
        con.close()

        db = Database(old_path)
        db.write(_minimal_result(torrent_tag="legacy-tag"))
        record = db.find_by_tag("legacy-tag")

        assert record is not None
        assert record.torrent_tag == "legacy-tag"


class TestNewerDatabaseSchema:
    """A database written by a newer build is flagged, not silently used."""

    def test_newer_version_warns(self, tmp_path: Path, mocker: MockerFixture) -> None:
        import sqlite3

        path = str(tmp_path / "newer.db")
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE history (id INTEGER PRIMARY KEY, index_title TEXT)")
        con.execute("PRAGMA user_version = 99")
        con.commit()
        con.close()
        log = mocker.patch("movarr.database._logger")

        Database(path)

        assert any("newer" in str(c).lower() for c in log.warning.call_args_list)
