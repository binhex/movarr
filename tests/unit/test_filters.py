"""Unit tests for movarr.filters — index and IMDb filter chains."""

from __future__ import annotations

from typing import Any

from movarr.config import Config
from movarr.filters import filter_by_imdb, filter_by_index
from movarr.models import ResultDict

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**filter_overrides: Any) -> Config:
    """Build a Config with overridden filters fields."""
    cfg = Config()
    for key, val in filter_overrides.items():
        setattr(cfg.filters, key, val)
    return cfg


def _index_result(**overrides: Any) -> ResultDict:
    """Minimal ResultDict for index-level filter testing."""
    base: ResultDict = {
        "index_title": "The Dark Knight 2008 1080p BluRay",
        "index_title_sanitised": "The Dark Knight 2008 1080p BluRay",
        "index_size": str(8_000_000_000),  # 8 GB in bytes
        "movie_title": "The Dark Knight",
        "movie_title_year": "2008",
        "index_title_resolution": "1080p",
        "result": "Passed",
        "result_details": [],
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def _imdb_result(**overrides: Any) -> ResultDict:
    """Minimal ResultDict for IMDb-level filter testing."""
    base: ResultDict = {
        "index_title": "The Dark Knight 2008 1080p BluRay",
        "index_size": str(8_000_000_000),
        "movie_title": "The Dark Knight",
        "movie_title_year": "2008",
        "imdb_id": "tt0468569",
        "imdb_title": "The Dark Knight",
        "imdb_title_type": "movie",
        "imdb_year": "2008",
        "imdb_rating": "9.0",
        "imdb_votes": "2500000",
        "imdb_running_time_in_minutes": "152",
        "imdb_language_list": ["English"],
        "imdb_country_list": ["US"],
        "imdb_genres_list": ["Action", "Crime", "Drama"],
        "index_title_resolution": "1080p",
        "result": "Passed",
        "result_details": [],
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def _default_site_dict(
    criteria: str = "1080p",
    minimum_size_mb: int = 1000,
    maximum_size_mb: int = 50000,
    minimum_bitrate_mb: int = 50,
    category: str = "2000,5000",
) -> dict:
    return {
        "criteria": criteria,
        "minimum_size_mb": minimum_size_mb,
        "maximum_size_mb": maximum_size_mb,
        "minimum_bitrate_mb": minimum_bitrate_mb,
        "category": category,
    }


# ---------------------------------------------------------------------------
# filter_by_index: size checks
# ---------------------------------------------------------------------------


class TestFilterByIndexSize:
    """Size threshold checks."""

    def test_passes_when_size_within_bounds(self) -> None:
        result = _index_result()  # index_size=8 GB default, within 1–20 GB
        site = _default_site_dict(minimum_size_mb=1000, maximum_size_mb=20000)
        cfg = Config()
        out = filter_by_index(result, site, cfg)
        assert out["result"] == "Passed"

    def test_fails_when_size_too_small(self) -> None:
        result = _index_result(index_size=str(500_000_000))  # 500 MB
        site = _default_site_dict(minimum_size_mb=1000)
        cfg = Config()
        out = filter_by_index(result, site, cfg)
        assert out["result"] != "Passed"

    def test_fails_when_size_too_large(self) -> None:
        result = _index_result(index_size=str(99_000_000_000))  # 99 GB
        site = _default_site_dict(maximum_size_mb=20000)
        cfg = Config()
        out = filter_by_index(result, site, cfg)
        assert out["result"] != "Passed"


# ---------------------------------------------------------------------------
# filter_by_index: TV detection
# ---------------------------------------------------------------------------


class TestFilterByIndexTv:
    """TV content must be rejected."""

    def test_episode_notation_rejected(self) -> None:
        result = _index_result(
            index_title="Breaking Bad 2008 S01E05 1080p BluRay",
            index_title_sanitised="Breaking Bad 2008 S01E05 1080p BluRay",
        )
        site = _default_site_dict(criteria="1080p")
        cfg = Config()
        out = filter_by_index(result, site, cfg)
        assert out["result"] != "Passed"


# ---------------------------------------------------------------------------
# filter_by_index: bad keywords
# ---------------------------------------------------------------------------


class TestFilterByIndexBadKeywords:
    """Titles containing bad keywords must be rejected."""

    def test_bad_keyword_rejects_result(self) -> None:
        cfg = Config()
        cfg.filters.bad_index_title_list = ["CAM"]
        result = _index_result(index_title="Movie 2020 CAM 1080p BluRay")
        out = filter_by_index(result, _default_site_dict(), cfg)
        assert out["result"] != "Passed"

    def test_no_bad_keywords_passes(self) -> None:
        cfg = Config()  # default — empty bad_index_title_list
        result = _index_result()
        out = filter_by_index(result, _default_site_dict(), cfg)
        assert out["result"] == "Passed"


# ---------------------------------------------------------------------------
# filter_by_imdb: title type gate
# ---------------------------------------------------------------------------


class TestFilterByImdbTitleType:
    """Only configured title types should pass."""

    def test_movie_type_passes(self) -> None:
        cfg = Config()
        result = _imdb_result(imdb_title_type="movie")
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_tvSeries_type_rejected(self) -> None:
        cfg = Config()
        result = _imdb_result(imdb_title_type="tvSeries")
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"

    def test_tvmovie_passes(self) -> None:
        cfg = Config()
        result = _imdb_result(imdb_title_type="tvMovie")
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"


# ---------------------------------------------------------------------------
# filter_by_imdb: rating / votes gate
# ---------------------------------------------------------------------------


class TestFilterByImdbRatingVotes:
    """Minimum rating and votes thresholds."""

    def test_low_rating_rejected(self) -> None:
        cfg = _make_config(minimum_rating=7.0, minimum_votes=1000)
        result = _imdb_result(imdb_rating="3.5", imdb_votes="500000")
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"

    def test_low_votes_rejected(self) -> None:
        cfg = _make_config(minimum_rating=7.0, minimum_votes=10000)
        result = _imdb_result(imdb_rating="8.5", imdb_votes="100")
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"

    def test_high_rating_and_votes_passes(self) -> None:
        cfg = _make_config(minimum_rating=7.0, minimum_votes=1000)
        result = _imdb_result(imdb_rating="8.5", imdb_votes="500000")
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"


# ---------------------------------------------------------------------------
# filter_by_imdb: year gate
# ---------------------------------------------------------------------------


class TestFilterByImdbYear:
    """Minimum year threshold."""

    def test_old_film_rejected(self) -> None:
        cfg = _make_config(minimum_year=2000)
        result = _imdb_result(imdb_year="1965", movie_title_year="1965")
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"

    def test_new_film_passes(self) -> None:
        cfg = _make_config(minimum_year=2000)
        result = _imdb_result(imdb_year="2010")
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"


# ---------------------------------------------------------------------------
# filter_by_imdb: runtime gate
# ---------------------------------------------------------------------------


class TestFilterByImdbRuntime:
    """Minimum runtime threshold."""

    def test_short_runtime_rejected(self) -> None:
        cfg = _make_config(minimum_runtime_mins=60)
        result = _imdb_result(imdb_running_time_in_minutes="20")
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"

    def test_normal_runtime_passes(self) -> None:
        cfg = _make_config(minimum_runtime_mins=60)
        result = _imdb_result(imdb_running_time_in_minutes="120")
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"
