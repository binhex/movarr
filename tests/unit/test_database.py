"""Unit tests for movarr.database — CRUD and deduplication."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from movarr.database import Database

if TYPE_CHECKING:
    from pathlib import Path

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


class TestWrite:
    """Database.write() must persist a ResultDict."""

    def test_write_and_exact_duplicate_detected(self, db: Database) -> None:
        result = _minimal_result()
        db.write(result)
        assert db.is_duplicate_exact("The Dark Knight 2008 1080p BluRay") is True

    def test_unknown_title_not_duplicate(self, db: Database) -> None:
        assert db.is_duplicate_exact("Unknown Movie 2099") is False

    def test_fuzzy_duplicate_detected(self, db: Database) -> None:
        result = _minimal_result()
        db.write(result)
        assert db.is_duplicate_fuzzy("%Dark Knight%") is True

    def test_fuzzy_no_match_returns_false(self, db: Database) -> None:
        db.write(_minimal_result())
        assert db.is_duplicate_fuzzy("%UnknownXYZ%") is False


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


# set_verified / is_verified


class TestVerification:
    """Verification state round-trips correctly."""

    def test_newly_written_record_is_not_verified(self, db: Database) -> None:
        db.write(_minimal_result(torrent_tag="v-tag-001"))
        assert db.is_verified("v-tag-001") is False

    def test_set_verified_marks_record(self, db: Database) -> None:
        db.write(_minimal_result(torrent_tag="v-tag-002"))
        db.set_verified("v-tag-002")
        assert db.is_verified("v-tag-002") is True

    def test_set_verified_unknown_tag_is_noop(self, db: Database) -> None:
        db.set_verified("no-such-tag")  # must not raise


# genres_for_tag — JSON and legacy Python-repr formats


class TestGenresForTag:
    """genres_for_tag() handles both JSON and Python-repr encoded lists."""

    def test_json_encoded_genres(self, db: Database) -> None:
        result = _minimal_result(
            torrent_tag="genre-tag-json",
            imdb_genres_list='["Action", "Drama"]',
        )
        db.write(result)
        genres = db.genres_for_tag("genre-tag-json")
        assert "Action" in genres
        assert "Drama" in genres

    def test_python_repr_encoded_genres(self, db: Database) -> None:
        result = _minimal_result(
            torrent_tag="genre-tag-repr",
            imdb_genres_list="['Comedy', 'Thriller']",
        )
        db.write(result)
        genres = db.genres_for_tag("genre-tag-repr")
        assert "Comedy" in genres

    def test_no_genres_returns_empty_list(self, db: Database) -> None:
        db.write(_minimal_result(torrent_tag="no-genre-tag"))
        assert db.genres_for_tag("no-genre-tag") == []

    def test_missing_tag_returns_empty_list(self, db: Database) -> None:
        assert db.genres_for_tag("absent-tag") == []

    def test_invalid_genres_string_returns_empty_list(self, db: Database, tmp_path: Path) -> None:
        """Genres that fail both json.loads and ast.literal_eval return []."""
        import sqlite3

        db.write(_minimal_result(torrent_tag="bad-genre-tag"))
        raw_path = str(tmp_path / "test.db")
        con = sqlite3.connect(raw_path)
        con.execute(
            "UPDATE history SET imdb_genres_list = ? WHERE torrent_tag = ?",
            ("not-valid-at-all", "bad-genre-tag"),
        )
        con.commit()
        con.close()
        # Re-open via Database — read genres
        db2 = Database(raw_path)
        assert db2.genres_for_tag("bad-genre-tag") == []


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


# Database vacuum


class TestDatabaseVacuum:
    """Database.vacuum() must not raise."""

    def test_vacuum_runs_without_error(self, db: Database) -> None:
        assert db.vacuum() is None


# has_passed


class TestHasPassed:
    """Database.has_passed() returns True only when a Passed/Completed/Stalled row exists."""

    def test_returns_true_when_passed_row_exists(self, db: Database) -> None:
        db.write(_minimal_result(result="Passed"))
        assert db.has_passed("The Dark Knight 2008 1080p BluRay") is True

    def test_returns_false_when_only_failed_row_exists(self, db: Database) -> None:
        db.write(_minimal_result(result="Failed"))
        assert db.has_passed("The Dark Knight 2008 1080p BluRay") is False

    def test_returns_false_when_no_row_exists(self, db: Database) -> None:
        assert db.has_passed("Unknown Movie 2099") is False

    def test_returns_true_when_completed_row_exists(self, db: Database) -> None:
        db.write(_minimal_result(torrent_tag="movarr-comp-hp"))
        db.mark_completed("movarr-comp-hp")
        assert db.has_passed("The Dark Knight 2008 1080p BluRay") is True

    def test_returns_true_when_stalled_row_exists(self, db: Database) -> None:
        db.write(_minimal_result(torrent_tag="movarr-stall-hp"))
        db.mark_stalled("movarr-stall-hp")
        assert db.has_passed("The Dark Knight 2008 1080p BluRay") is True

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

    def test_unversioned_legacy_db_gets_index_created(self, tmp_path: Path) -> None:
        """A pre-versioning DB (user_version=0, history table exists) must get the index."""
        import sqlite3

        old_path = str(tmp_path / "legacy.db")
        con = sqlite3.connect(old_path)
        con.execute(
            """
            CREATE TABLE history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                index_title TEXT,
                result TEXT,
                result_details TEXT,
                imdb_certification TEXT,
                imdb_cert_source TEXT
            )
            """
        )
        # user_version stays 0 — simulates a DB created before schema versioning
        con.commit()
        con.close()

        db = Database(old_path)
        con2 = sqlite3.connect(old_path)
        cursor = con2.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='history'")
        index_names = [row[0] for row in cursor.fetchall()]
        con2.close()
        assert "ix_history_index_title" in index_names
        del db

    def test_upgrade_from_version_8_adds_stalled_at_column(self, tmp_path: Path) -> None:
        """Migrating a v8 DB adds the stalled_at column."""
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

    def test_kv_store_table_created_on_new_db(self, tmp_path: Path) -> None:
        """kv_store table exists after Database init."""
        db = Database(tmp_path / "test.db")
        with db._engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='kv_store'"))
            assert result.fetchone() is not None

    def test_db_version_is_12(self, tmp_path: Path) -> None:
        """New database is created at schema version 12."""
        db = Database(tmp_path / "test.db")
        assert db._get_user_version() == 12

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
        assert db._get_user_version() == 12

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
        assert db.kv_get("index_proxy.zero_results_since") is None
        assert db.kv_get("index_proxy.unavailable_since") == "2026-01-01T00:00:00+00:00"
