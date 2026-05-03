"""Unit tests for movarr.database — CRUD and deduplication."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from movarr.database import Database

if TYPE_CHECKING:
    from pathlib import Path

    from movarr.models import ResultDict

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# write + is_duplicate
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# read_by_tag / find_by_tag
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# set_verified / is_verified
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# genres_for_tag — JSON and legacy Python-repr formats
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Database upgrade path
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Database vacuum
# ---------------------------------------------------------------------------


class TestDatabaseVacuum:
    """Database.vacuum() must not raise."""

    def test_vacuum_runs_without_error(self, db: Database) -> None:
        db.vacuum()


# ---------------------------------------------------------------------------
# has_passed
# ---------------------------------------------------------------------------


class TestHasPassed:
    """Database.has_passed() returns True only when a Passed row exists."""

    def test_returns_true_when_passed_row_exists(self, db: Database) -> None:
        db.write(_minimal_result(result="Passed"))
        assert db.has_passed("The Dark Knight 2008 1080p BluRay") is True

    def test_returns_false_when_only_failed_row_exists(self, db: Database) -> None:
        db.write(_minimal_result(result="Failed"))
        assert db.has_passed("The Dark Knight 2008 1080p BluRay") is False

    def test_returns_false_when_no_row_exists(self, db: Database) -> None:
        assert db.has_passed("Unknown Movie 2099") is False

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
