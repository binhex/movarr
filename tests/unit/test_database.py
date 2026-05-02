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


class TestReadByTag:
    """Database.read_by_tag() must return the correct record or None."""

    def test_returns_record_for_known_tag(self, db: Database) -> None:
        result = _minimal_result(torrent_tag="unique-tag-001")
        db.write(result)
        record = db.read_by_tag("unique-tag-001")
        assert record is not None
        assert record.torrent_tag == "unique-tag-001"

    def test_returns_none_for_unknown_tag(self, db: Database) -> None:
        assert db.read_by_tag("no-such-tag") is None

    def test_find_by_tag_same_result(self, db: Database) -> None:
        result = _minimal_result(torrent_tag="shared-tag")
        db.write(result)
        rec1 = db.find_by_tag("shared-tag")
        rec2 = db.read_by_tag("shared-tag")
        assert rec1 is not None and rec2 is not None
        assert rec1.torrent_tag == rec2.torrent_tag == "shared-tag"


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
