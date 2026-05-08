"""Unit tests for movarr.filters — index and IMDb filter chains."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger as _loguru_logger

from movarr.config import Config
from movarr.filters import filter_by_imdb, filter_by_index

if TYPE_CHECKING:
    from movarr.models import ResultDict

# Helpers


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
        "imdb_year": 2008,
        "imdb_rating": 9.0,
        "imdb_votes": 2_500_000,
        "imdb_running_time_in_minutes": 152,
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


# filter_by_index: size checks


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


# filter_by_index: TV detection


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


# filter_by_index: reject group


class TestFilterByIndexRejectGroup:
    """Release group rejection filter."""

    def test_matching_group_fails(self) -> None:
        cfg = _make_config(reject_index_group_list=["FGT"])
        result = _index_result(index_title="Movie 2020 1080p BluRay FGT")
        out = filter_by_index(result, _default_site_dict(), cfg)
        assert out["result"] == "Failed"
        details = " ".join(out.get("result_details", []))
        assert "fgt" in details.lower()

    def test_non_matching_group_passes(self) -> None:
        cfg = _make_config(reject_index_group_list=["FGT"])
        result = _index_result(index_title="Movie 2020 1080p BluRay SPARKS")
        out = filter_by_index(result, _default_site_dict(), cfg)
        assert out["result"] == "Passed"

    def test_empty_list_skips_check(self) -> None:
        cfg = Config()  # reject_index_group_list = []
        result = _index_result(index_title="Movie 2020 1080p BluRay FGT")
        out = filter_by_index(result, _default_site_dict(), cfg)
        assert out["result"] == "Passed"

    def test_case_insensitive_match(self) -> None:
        cfg = _make_config(reject_index_group_list=["fgt"])
        result = _index_result(index_title="Movie 2020 1080p BluRay FGT")
        out = filter_by_index(result, _default_site_dict(), cfg)
        assert out["result"] == "Failed"

    def test_non_matching_quality_token_passes(self) -> None:
        """extract_group returns the last token (e.g. 'bluray') when no group is present."""
        cfg = _make_config(reject_index_group_list=["FGT"])
        result = _index_result(index_title="Movie 2020 1080p BluRay")
        out = filter_by_index(result, _default_site_dict(), cfg)
        assert out["result"] == "Passed"
        assert "bluray" in " ".join(out.get("result_details", [])).lower()

    def test_no_group_detected_no_year_passes(self) -> None:
        """Titles without a year have no after-year segment → no group detected."""
        cfg = _make_config(reject_index_group_list=["FGT"])
        result = _index_result(index_title="Movie")
        out = filter_by_index(result, _default_site_dict(criteria=""), cfg)
        assert out["result"] == "Passed"


# filter_by_index: reject keywords


class TestFilterByIndexRejectKeywords:
    """Titles containing rejected keywords must be rejected."""

    def test_reject_keyword_rejects_result(self) -> None:
        cfg = Config()
        cfg.filters.reject_index_title_list = ["CAM"]
        result = _index_result(index_title="Movie 2020 CAM 1080p BluRay")
        out = filter_by_index(result, _default_site_dict(), cfg)
        assert out["result"] != "Passed"

    def test_no_reject_keywords_passes(self) -> None:
        cfg = Config()  # default — empty reject_index_title_list
        result = _index_result()
        out = filter_by_index(result, _default_site_dict(), cfg)
        assert out["result"] == "Passed"


# filter_by_imdb: title type gate


class TestFilterByImdbTitleType:
    """Only configured title types should pass."""

    def test_movie_type_passes(self) -> None:
        cfg = Config()
        result = _imdb_result(imdb_title_type="movie")
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_tv_series_type_rejected(self) -> None:
        cfg = Config()
        result = _imdb_result(imdb_title_type="tvSeries")
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"

    def test_tvmovie_passes(self) -> None:
        cfg = Config()
        result = _imdb_result(imdb_title_type="tvMovie")
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"


# filter_by_imdb: rating / votes gate


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


# filter_by_imdb: year gate


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


# filter_by_imdb: runtime gate


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


# filter_by_imdb: bitrate gate


class TestFilterByIndexBitrate:
    """Minimum bitrate check (_filter_minimum_bitrate_mb in result dict)."""

    def test_passes_when_bitrate_meets_minimum(self) -> None:
        # 8000 MB / 120 min = ~66 MB/min, min = 50
        result = _imdb_result(
            _filter_minimum_bitrate_mb=50,
            index_size=str(8_000_000_000),
            imdb_running_time_in_minutes="120",
        )
        cfg = Config()
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_fails_when_bitrate_below_minimum(self) -> None:
        # 1000 MB / 120 min = ~8 MB/min, min = 50
        result = _imdb_result(
            _filter_minimum_bitrate_mb=50,
            index_size=str(1_000_000_000),
            imdb_running_time_in_minutes="120",
        )
        cfg = Config()
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"

    def test_skips_when_no_minimum_bitrate_set(self) -> None:
        result = _imdb_result()  # no _filter_minimum_bitrate_mb
        cfg = Config()
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_fails_when_no_index_size_available(self) -> None:
        result = _imdb_result(
            _filter_minimum_bitrate_mb=50,
            imdb_running_time_in_minutes="120",
        )
        result.pop("index_size", None)
        cfg = Config()
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"


# filter_by_index: library dedup (index-level)


class TestFilterByIndexLibraryDedup:
    """Library dedup check at index stage using file-name comparison."""

    def test_passes_when_library_paths_empty(self) -> None:
        cfg = Config()  # library_path_list defaults to []
        result = _index_result()
        out = filter_by_index(result, _default_site_dict(), cfg, library_walk=[])
        assert out["result"] == "Passed"

    def test_passes_when_movie_not_in_library(self) -> None:
        cfg = Config()
        cfg.general.library_path_list = ["/library"]
        result = _index_result(
            movie_title_compare="thedarkknight",
            movie_title_year="2008",
            index_title_resolution="1080",
        )
        # Library has an unrelated movie
        library_walk: list[tuple[str, list[str], list[str]]] = [("/library", [], ["Inception 2010 1080p BluRay.mkv"])]
        out = filter_by_index(result, _default_site_dict(), cfg, library_walk=library_walk)
        assert out["result"] == "Passed"

    def test_passes_when_index_is_higher_resolution_than_library(self) -> None:
        cfg = Config()
        cfg.general.library_path_list = ["/library"]
        result = _index_result(
            index_title="The Dark Knight 2008 1080p BluRay",
            index_title_sanitised="The Dark Knight 2008 1080p BluRay",
            index_title_resolution="1080",
            movie_title="The Dark Knight",
            movie_title_year="2008",
            movie_title_compare="thedarkknight",
        )
        library_walk: list[tuple[str, list[str], list[str]]] = [
            ("/library", [], ["The Dark Knight 2008 720p BluRay.mkv"])
        ]
        out = filter_by_index(result, _default_site_dict(criteria="1080p"), cfg, library_walk=library_walk)
        assert out["result"] == "Passed"

    def test_fails_when_library_has_same_resolution_and_quality(self) -> None:
        cfg = Config()
        cfg.general.library_path_list = ["/library"]
        result = _index_result(
            index_title="The Dark Knight 2008 1080p BluRay",
            index_title_sanitised="The Dark Knight 2008 1080p BluRay",
            index_title_resolution="1080",
            movie_title="The Dark Knight",
            movie_title_year="2008",
            movie_title_compare="thedarkknight",
        )
        library_walk: list[tuple[str, list[str], list[str]]] = [
            ("/library", [], ["The Dark Knight 2008 1080p BluRay.mkv"])
        ]
        out = filter_by_index(result, _default_site_dict(criteria="1080p"), cfg, library_walk=library_walk)
        assert out["result"] != "Passed"

    def test_passes_when_no_resolution_in_index_result(self) -> None:
        """No resolution token skips the library check (passes) rather than failing.

        A torrent without a resolution token should not be permanently rejected
        by the library check; other filters should still evaluate it.
        """
        cfg = Config()
        cfg.general.library_path_list = ["/library"]
        result = _index_result(index_title_resolution="")
        library_walk: list[tuple[str, list[str], list[str]]] = [
            ("/library", [], ["The Dark Knight 2008 1080p BluRay.mkv"])
        ]
        out = filter_by_index(result, _default_site_dict(), cfg, library_walk=library_walk)
        assert out["result"] == "Passed"


# filter_by_imdb: genre gate


class TestFilterByImdbRejectGenre:
    """Genre exclusion filter (reject_genre_list)."""

    def test_excluded_genre_rejects_result(self) -> None:
        cfg = _make_config(reject_genre_list=["Horror"])
        result = _imdb_result(imdb_genres_list=["Horror", "Thriller"])
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"

    def test_no_excluded_genres_passes(self) -> None:
        cfg = _make_config(reject_genre_list=["Horror"])
        result = _imdb_result(imdb_genres_list=["Action", "Drama"])
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_empty_reject_genre_list_always_passes(self) -> None:
        cfg = Config()  # empty reject_genre_list
        result = _imdb_result(imdb_genres_list=["Horror", "Gore"])
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_case_insensitive_genre_match(self) -> None:
        cfg = _make_config(reject_genre_list=["horror"])
        result = _imdb_result(imdb_genres_list=["Horror"])
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"


# filter_by_imdb: language gate


class TestFilterByImdbLanguage:
    """Language allowlist filter (allow_language_list)."""

    def test_matching_language_passes(self) -> None:
        cfg = _make_config(allow_language_list=["english"])
        result = _imdb_result(imdb_language_list=["English"])
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_non_matching_language_fails(self) -> None:
        cfg = _make_config(allow_language_list=["english"])
        result = _imdb_result(imdb_language_list=["French"])
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"

    def test_empty_language_list_skips_check(self) -> None:
        cfg = Config()  # allow_language_list = []
        result = _imdb_result(imdb_language_list=["Klingon"])
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_no_imdb_language_data_fails_closed(self) -> None:
        """When allow_language_list is configured but metadata is missing, fail closed.

        Previously this passed ("assuming OK"), which silently defeated the
        allow-list on bad data. The correct behaviour is to fail closed.
        """
        cfg = _make_config(allow_language_list=["english"])
        result = _imdb_result(imdb_language_list=None)
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"


# filter_by_imdb: country gate


class TestFilterByImdbCountry:
    """Country allowlist filter (allow_country_list)."""

    def test_matching_country_passes(self) -> None:
        cfg = _make_config(allow_country_list=["us"])
        result = _imdb_result(imdb_country_list=["US"])
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_non_matching_country_fails(self) -> None:
        cfg = _make_config(allow_country_list=["us"])
        result = _imdb_result(imdb_country_list=["FR"])
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"

    def test_empty_country_list_skips_check(self) -> None:
        cfg = Config()  # allow_country_list = []
        result = _imdb_result(imdb_country_list=["ZW"])
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_no_imdb_country_data_fails_closed(self) -> None:
        """When allow_country_list is configured but metadata is missing, fail closed.

        Previously this passed ("assuming OK"), which silently defeated the
        allow-list on bad data. The correct behaviour is to fail closed.
        """
        cfg = _make_config(allow_country_list=["us"])
        result = _imdb_result(imdb_country_list=None)
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"


# filter_by_imdb: certification (not filtered; stored only)


class TestFilterByImdbCertification:
    """Certification is stored in result but not filtered — any cert passes."""

    def test_any_certification_passes_filter(self) -> None:
        cfg = Config()
        result = _imdb_result(imdb_certification="18")
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_no_certification_passes_filter(self) -> None:
        cfg = Config()
        result = _imdb_result(imdb_certification=None)
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_certification_value_preserved_in_result(self) -> None:
        cfg = Config()
        result = _imdb_result(imdb_certification="15")
        out = filter_by_imdb(result, cfg)
        assert out.get("imdb_certification") == "15"


# filter_by_imdb: library dedup (IMDb canonical title)


class TestFilterByImdbLibraryDedup:
    """Library dedup using the canonical IMDb title (post-IMDb stage)."""

    def test_passes_when_library_walk_is_none(self) -> None:
        cfg = Config()
        result = _imdb_result()
        out = filter_by_imdb(result, cfg, library_walk=None)
        assert out["result"] == "Passed"

    def test_passes_when_movie_not_in_library(self) -> None:
        cfg = Config()
        result = _imdb_result(
            imdb_title="The Dark Knight",
            imdb_year="2008",
            index_title_resolution="1080",
        )
        library_walk: list[tuple[str, list[str], list[str]]] = [("/library", [], ["Inception 2010 1080p BluRay.mkv"])]
        out = filter_by_imdb(result, cfg, library_walk=library_walk)
        assert out["result"] == "Passed"

    def test_fails_when_same_movie_already_in_library_at_same_quality(self) -> None:
        cfg = Config()
        result = _imdb_result(
            index_title="The Dark Knight 2008 1080p BluRay",
            index_title_sanitised="The Dark Knight 2008 1080p BluRay",
            imdb_title="The Dark Knight",
            imdb_year="2008",
            index_title_resolution="1080",
        )
        library_walk: list[tuple[str, list[str], list[str]]] = [
            ("/library", [], ["The Dark Knight 2008 1080p BluRay.mkv"])
        ]
        out = filter_by_imdb(result, cfg, library_walk=library_walk)
        assert out["result"] != "Passed"

    def test_passes_when_index_is_upgrade_over_library(self) -> None:
        cfg = Config()
        result = _imdb_result(
            index_title="The Dark Knight 2008 2160p BluRay",
            index_title_sanitised="The Dark Knight 2008 2160p BluRay",
            imdb_title="The Dark Knight",
            imdb_year="2008",
            index_title_resolution="2160",
        )
        library_walk: list[tuple[str, list[str], list[str]]] = [
            ("/library", [], ["The Dark Knight 2008 1080p BluRay.mkv"])
        ]
        out = filter_by_imdb(result, cfg, library_walk=library_walk)
        assert out["result"] == "Passed"

    def test_passes_when_imdb_title_missing(self) -> None:
        cfg = Config()
        result = _imdb_result(imdb_title=None, imdb_year="2008", index_title_resolution="1080")
        library_walk: list[tuple[str, list[str], list[str]]] = [
            ("/library", [], ["The Dark Knight 2008 1080p BluRay.mkv"])
        ]
        out = filter_by_imdb(result, cfg, library_walk=library_walk)
        assert out["result"] == "Passed"


# filter_by_imdb: overrides


class TestFilterByImdbOverrides:
    """Override chain: character/director/writer/cast/title = hard pass; genre = relaxed."""

    def test_director_override_bypasses_rating_check(self) -> None:
        cfg = _make_config(
            minimum_rating=7.0,
            minimum_votes=5000,
            override_director_list=["Christopher Nolan"],
        )
        result = _imdb_result(
            imdb_credits_director_list=["Christopher Nolan"],
            imdb_rating="3.0",
            imdb_votes="100",
        )
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_cast_override_bypasses_rating_check(self) -> None:
        cfg = _make_config(
            minimum_rating=7.0,
            minimum_votes=5000,
            override_cast_list=["Keanu Reeves"],
        )
        result = _imdb_result(
            imdb_credits_cast_list=["Keanu Reeves"],
            imdb_rating="3.0",
            imdb_votes="100",
        )
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_writer_override_bypasses_rating_check(self) -> None:
        cfg = _make_config(
            minimum_rating=7.0,
            minimum_votes=5000,
            override_writer_list=["Lilly Wachowski"],
        )
        result = _imdb_result(
            imdb_credits_writer_list=["Lilly Wachowski"],
            imdb_rating="3.0",
            imdb_votes="100",
        )
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_character_override_bypasses_rating_check(self) -> None:
        cfg = _make_config(
            minimum_rating=7.0,
            minimum_votes=5000,
            override_character_list=["Neo"],
        )
        result = _imdb_result(
            imdb_credits_character_list=["Neo"],
            imdb_rating="3.0",
            imdb_votes="100",
        )
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_movie_title_override_bypasses_rating_check(self) -> None:
        cfg = _make_config(
            minimum_rating=7.0,
            minimum_votes=5000,
            override_movie_title_list=["The Dark Knight"],
        )
        result = _imdb_result(
            movie_title_compare="thedarkknight",
            movie_title_and_year_compare="thedarkknight2008",
            imdb_rating="3.0",
            imdb_votes="100",
        )
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_genre_override_relaxes_rating_threshold(self) -> None:
        from movarr.config import OverrideGenreConfig

        cfg = Config()
        cfg.filters.minimum_rating = 7.0
        cfg.filters.minimum_votes = 5000
        cfg.filters.override_genre = {"action": OverrideGenreConfig(minimum_rating=3.0, minimum_votes=100)}
        result = _imdb_result(
            imdb_genres_list=["Action"],
            imdb_rating="4.0",
            imdb_votes="500",
        )
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_no_override_match_applies_default_thresholds(self) -> None:
        cfg = _make_config(minimum_rating=7.0, minimum_votes=5000)
        result = _imdb_result(imdb_rating="3.0", imdb_votes="100")
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"

    def test_override_only_on_matching_person_in_list(self) -> None:
        cfg = _make_config(
            minimum_rating=7.0,
            minimum_votes=5000,
            override_director_list=["Steven Spielberg"],
        )
        # Director is someone else
        result = _imdb_result(
            imdb_credits_director_list=["Michael Bay"],
            imdb_rating="3.0",
            imdb_votes="100",
        )
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"


# filter_by_index: search criteria token miss


class TestFilterByIndexCriteria:
    """Search criteria token must appear in the index title."""

    def test_criteria_token_missing_in_title_fails(self) -> None:
        site = _default_site_dict(criteria="exclusivetoken")
        result = _index_result(index_title="The Dark Knight 2008 1080p BluRay")
        out = filter_by_index(result, site, Config())
        assert out["result"] != "Passed"


# filter_by_index: size edge cases


class TestFilterByIndexSizeEdgeCases:
    """Size threshold skipped when zero; absent raw_size fails with threshold."""

    def test_passes_when_no_size_threshold_set(self) -> None:
        site = _default_site_dict(minimum_size_mb=0, maximum_size_mb=0)
        result = _index_result(index_size=str(100_000))
        out = filter_by_index(result, site, Config())
        assert out["result"] == "Passed"

    def test_fails_when_no_index_size_but_minimum_set(self) -> None:
        site = _default_site_dict(minimum_size_mb=1000)
        result = _index_result()
        result.pop("index_size", None)
        out = filter_by_index(result, site, Config())
        assert out["result"] != "Passed"


# filter_by_index: reject keyword no-match path


class TestFilterByIndexRejectKeywordNoMatch:
    """Non-empty reject keyword list with no match must still pass."""

    def test_non_empty_reject_keyword_list_no_match_passes(self) -> None:
        cfg = _make_config(reject_index_title_list=["CAM"])
        result = _index_result()
        out = filter_by_index(result, _default_site_dict(), cfg)
        assert out["result"] == "Passed"


# filter_by_index: reject movie title list


class TestFilterByIndexRejectMovieTitles:
    """Reject movie title list rejects matched titles and passes others."""

    def test_reject_movie_title_exact_title_match_fails(self) -> None:
        """An entry matching the exact normalised title (no year) is rejected."""
        cfg = _make_config(reject_movie_title_list=["thedarkknight"])
        result = _index_result(
            movie_title_compare="thedarkknight",
            movie_title_and_year_compare="thedarkknight2008",
        )
        out = filter_by_index(result, _default_site_dict(), cfg)
        assert out["result"] != "Passed"

    def test_reject_movie_title_with_year_match_fails(self) -> None:
        """An entry matching the normalised title+year string is rejected."""
        cfg = _make_config(reject_movie_title_list=["The Dark Knight 2008"])
        result = _index_result(
            movie_title_compare="thedarkknight",
            movie_title_and_year_compare="thedarkknight2008",
        )
        out = filter_by_index(result, _default_site_dict(), cfg)
        assert out["result"] != "Passed"

    def test_reject_movie_title_no_match_passes(self) -> None:
        cfg = _make_config(reject_movie_title_list=["terminator"])
        result = _index_result(movie_title_and_year_compare="thedarkknight2008")
        out = filter_by_index(result, _default_site_dict(), cfg)
        assert out["result"] == "Passed"


# filter_by_imdb: empty allow_imdb_title_type_list


class TestFilterByImdbTitleTypeEmptyList:
    """An empty allow_imdb_title_type_list skips the title-type gate."""

    def test_any_type_passes_when_list_empty(self) -> None:
        cfg = _make_config(allow_imdb_title_type_list=[])
        result = _imdb_result(imdb_title_type="videoGame")
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"


# filter_by_imdb: bitrate check needs runtime


class TestFilterByImdbBitrateNoRuntime:
    """Bitrate check fails when runtime is absent."""

    def test_fails_when_minimum_bitrate_set_but_no_runtime(self) -> None:
        result = _imdb_result(_filter_minimum_bitrate_mb=50)
        result.pop("imdb_running_time_in_minutes", None)
        out = filter_by_imdb(result, Config())
        assert out["result"] != "Passed"


# filter_by_imdb: year edge cases


class TestFilterByImdbYearEdgeCases:
    """Zero minimum_year skips the gate; absent year fails it."""

    def test_passes_when_minimum_year_is_zero(self) -> None:
        cfg = _make_config(minimum_year=0)
        result = _imdb_result(movie_title_year="1940")
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_fails_when_minimum_year_set_and_year_absent(self) -> None:
        """When minimum_year is set but imdb_year is missing, the check must fail."""
        cfg = _make_config(minimum_year=2000)
        result = _imdb_result()
        result.pop("imdb_year", None)
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"

    def test_fails_when_year_is_invalid_string(self) -> None:
        """Non-numeric year string cannot be parsed and should fail the gate."""
        cfg = _make_config(minimum_year=2000)
        result = _imdb_result(imdb_year="abc")
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"


# filter_by_imdb: runtime edge cases


class TestFilterByImdbRuntimeEdgeCases:
    """Zero minimum_runtime_mins skips the gate; absent runtime fails it."""

    def test_passes_when_minimum_runtime_is_zero(self) -> None:
        cfg = _make_config(minimum_runtime_mins=0)
        result = _imdb_result(imdb_running_time_in_minutes="5")
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_fails_when_minimum_runtime_set_and_runtime_absent(self) -> None:
        cfg = _make_config(minimum_runtime_mins=60)
        result = _imdb_result()
        result.pop("imdb_running_time_in_minutes", None)
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"

    def test_fails_when_runtime_is_empty_string(self) -> None:
        """Empty-string runtime cannot be parsed and should fail the gate."""
        cfg = _make_config(minimum_runtime_mins=60)
        result = _imdb_result(imdb_running_time_in_minutes="")
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"


# filter_by_imdb: rating edge cases


class TestFilterByImdbRatingEdgeCases:
    """Zero/absent/exceeds-ten minimum_rating branches."""

    def test_passes_when_minimum_rating_is_zero(self) -> None:
        cfg = _make_config(minimum_rating=0, minimum_votes=0)
        result = _imdb_result(imdb_rating="1.0", imdb_votes="10")
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_fails_when_minimum_rating_set_and_rating_absent(self) -> None:
        cfg = _make_config(minimum_rating=7.0, minimum_votes=0)
        result = _imdb_result()
        result.pop("imdb_rating", None)
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"

    def test_passes_when_minimum_rating_above_ten(self) -> None:
        cfg = _make_config(minimum_rating=11.0, minimum_votes=0)
        result = _imdb_result(imdb_rating="5.0")
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_fails_when_rating_is_invalid_string(self) -> None:
        """Non-numeric rating string cannot be parsed and should fail the gate."""
        cfg = _make_config(minimum_rating=7.0, minimum_votes=0)
        result = _imdb_result(imdb_rating="abc")
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"


# filter_by_imdb: votes edge cases


class TestFilterByImdbVotesEdgeCases:
    """Zero minimum_votes skips the gate; absent votes fails it."""

    def test_passes_when_minimum_votes_is_zero(self) -> None:
        cfg = _make_config(minimum_rating=0, minimum_votes=0)
        result = _imdb_result(imdb_votes="5")
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_fails_when_minimum_votes_set_and_votes_absent(self) -> None:
        cfg = _make_config(minimum_rating=0, minimum_votes=5000)
        result = _imdb_result()
        result.pop("imdb_votes", None)
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"

    def test_fails_when_votes_is_empty_string(self) -> None:
        """Empty-string votes cannot be parsed and should fail the gate."""
        cfg = _make_config(minimum_rating=0, minimum_votes=5000)
        result = _imdb_result(imdb_votes="")
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"


# filter_by_imdb: override returns False, falls through to rating/votes


class TestFilterByImdbOverrideFallthrough:
    """Override returning False does NOT hard-pass; rating/votes gates still apply."""

    def test_director_override_no_match_in_credits_still_fails_rating(self) -> None:
        cfg = _make_config(
            override_director_list=["Christopher Nolan"],
            minimum_rating=7.0,
            minimum_votes=5000,
        )
        result = _imdb_result(imdb_rating="3.0", imdb_votes="100")
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"

    def test_movie_title_override_no_match_still_fails_rating(self) -> None:
        cfg = _make_config(
            override_movie_title_list=["BatmanBegins"],
            minimum_rating=7.0,
            minimum_votes=5000,
        )
        result = _imdb_result(
            movie_title_and_year_compare="thedarkknight2008",
            imdb_rating="3.0",
            imdb_votes="100",
        )
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"


# filter_by_imdb: canonical library walk edge cases


class TestFilterByImdbCanonicalLibraryEdgeCases:
    """Canonical library dedup correctly skips un-normalisable/year-mismatch files."""

    def test_passes_when_imdb_title_cannot_be_normalised(self) -> None:
        result = _imdb_result(imdb_title="!!!", imdb_year="2008", index_title_resolution="1080")
        library_walk: list[tuple[str, list[str], list[str]]] = [("/lib", [], ["The Dark Knight 2008 1080p BluRay.mkv"])]
        out = filter_by_imdb(result, Config(), library_walk=library_walk)
        assert out["result"] == "Passed"

    def test_non_video_file_skipped_at_imdb_canonical_stage(self) -> None:
        result = _imdb_result(imdb_title="The Dark Knight", imdb_year="2008", index_title_resolution="1080")
        library_walk: list[tuple[str, list[str], list[str]]] = [("/lib", [], ["readme.txt"])]
        out = filter_by_imdb(result, Config(), library_walk=library_walk)
        assert out["result"] == "Passed"

    def test_sanitise_none_file_skipped_at_imdb_canonical_stage(self) -> None:
        result = _imdb_result(imdb_title="The Dark Knight", imdb_year="2008", index_title_resolution="1080")
        library_walk: list[tuple[str, list[str], list[str]]] = [("/lib", [], ["---...---.mkv"])]
        out = filter_by_imdb(result, Config(), library_walk=library_walk)
        assert out["result"] == "Passed"

    def test_library_file_without_year_skipped_at_imdb_canonical_stage(self) -> None:
        result = _imdb_result(imdb_title="The Dark Knight", imdb_year="2008", index_title_resolution="1080")
        library_walk: list[tuple[str, list[str], list[str]]] = [("/lib", [], ["TheMovieNoYear.mkv"])]
        out = filter_by_imdb(result, Config(), library_walk=library_walk)
        assert out["result"] == "Passed"

    def test_library_file_year_mismatch_skipped_at_imdb_canonical_stage(self) -> None:
        result = _imdb_result(imdb_title="The Dark Knight", imdb_year="2008", index_title_resolution="1080")
        library_walk: list[tuple[str, list[str], list[str]]] = [("/lib", [], ["The Dark Knight 2099 1080p BluRay.mkv"])]
        out = filter_by_imdb(result, Config(), library_walk=library_walk)
        assert out["result"] == "Passed"


# filter_by_index: library walk edge cases


class TestFilterByIndexLibraryWalkEdgeCases:
    """Library walk skips non-video, un-sanitisable, no-year, and year-mismatch files."""

    def test_non_video_file_skipped_at_index_stage(self) -> None:
        cfg = Config()
        cfg.general.library_path_list = ["/library"]
        result = _index_result(
            index_title_resolution="1080",
            movie_title_compare="thedarkknight",
            movie_title_year="2008",
        )
        library_walk: list[tuple[str, list[str], list[str]]] = [("/library", [], ["readme.txt"])]
        out = filter_by_index(result, _default_site_dict(criteria="1080p"), cfg, library_walk=library_walk)
        assert out["result"] == "Passed"

    def test_sanitise_none_file_skipped_at_index_stage(self) -> None:
        cfg = Config()
        cfg.general.library_path_list = ["/library"]
        result = _index_result(
            index_title_resolution="1080",
            movie_title_compare="thedarkknight",
            movie_title_year="2008",
        )
        library_walk: list[tuple[str, list[str], list[str]]] = [("/library", [], ["---...---.mkv"])]
        out = filter_by_index(result, _default_site_dict(criteria="1080p"), cfg, library_walk=library_walk)
        assert out["result"] == "Passed"

    def test_library_file_without_year_skipped_at_index_stage(self) -> None:
        cfg = Config()
        cfg.general.library_path_list = ["/library"]
        result = _index_result(
            index_title_resolution="1080",
            movie_title_compare="thedarkknight",
            movie_title_year="2008",
        )
        library_walk: list[tuple[str, list[str], list[str]]] = [("/library", [], ["TheMovieNoYear.mkv"])]
        out = filter_by_index(result, _default_site_dict(criteria="1080p"), cfg, library_walk=library_walk)
        assert out["result"] == "Passed"

    def test_library_file_year_mismatch_skipped_at_index_stage(self) -> None:
        cfg = Config()
        cfg.general.library_path_list = ["/library"]
        result = _index_result(
            index_title_resolution="1080",
            movie_title_compare="thedarkknight",
            movie_title_year="2008",
        )
        library_walk: list[tuple[str, list[str], list[str]]] = [
            ("/library", [], ["The Dark Knight 2099 1080p BluRay.mkv"])
        ]
        out = filter_by_index(result, _default_site_dict(criteria="1080p"), cfg, library_walk=library_walk)
        assert out["result"] == "Passed"

    def test_library_file_normalise_returns_none_skipped(self) -> None:
        """File whose title normalises to None is skipped (covers _match_library_file line 496)."""
        cfg = Config()
        cfg.general.library_path_list = ["/library"]
        result = _index_result(
            index_title_resolution="1080",
            movie_title_compare="thedarkknight",
            movie_title_year="2008",
        )
        library_walk: list[tuple[str, list[str], list[str]]] = [("/library", [], ["imdb 2008 1080p.mkv"])]
        out = filter_by_index(result, _default_site_dict(criteria="1080p"), cfg, library_walk=library_walk)
        assert out["result"] == "Passed"


# _evaluate_library_files: resolution edge cases


class TestEvaluateLibraryFilesEdgeCases:
    """_evaluate_library_files handles missing/unparseable/higher resolutions."""

    def test_fails_when_library_file_has_no_parseable_resolution(self) -> None:
        from movarr.filters import _evaluate_library_files

        result = _imdb_result(
            index_title="Test Film 2008",
            index_title_sanitised="Test Film 2008",
            index_title_resolution="1080",
        )
        out = _evaluate_library_files(result, ["/lib/Test Film 2008.mkv"], "1080", Config())
        assert out["result"] != "Passed"

    def test_skips_file_when_resolution_not_castable_to_int(self) -> None:
        from movarr.filters import _evaluate_library_files

        result = _imdb_result(
            index_title="Test Film 2008 1080p",
            index_title_sanitised="Test Film 2008 1080p",
            index_title_resolution="1080",
        )
        out = _evaluate_library_files(result, ["/lib/Test Film 2008 1080p BluRay.mkv"], "notanint", Config())
        assert out["result"] == "Passed"

    def test_fails_when_library_has_higher_resolution(self) -> None:
        from movarr.filters import _evaluate_library_files

        result = _imdb_result(
            index_title="Test Film 2008 1080p",
            index_title_sanitised="Test Film 2008 1080p",
            index_title_resolution="1080",
        )
        out = _evaluate_library_files(result, ["/lib/Test Film 2008 2160p BluRay.mkv"], "1080", Config())
        assert out["result"] != "Passed"

    def test_passes_when_index_scores_higher_at_same_resolution(self) -> None:
        """Index with a REMUX and UNRATED bonus should beat a plain BluRay at same resolution."""
        from movarr.filters import _evaluate_library_files

        # Index has REMUX + UNRATED (special edition bonus) — will outscore plain BluRay
        result = _imdb_result(
            index_title="Test Film 2008 1080p BluRay REMUX UNRATED",
            index_title_sanitised="Test Film 2008 1080p BluRay REMUX UNRATED",
            index_title_resolution="1080",
        )
        out = _evaluate_library_files(result, ["/lib/Test Film 2008 1080p BluRay.mkv"], "1080", Config())
        assert out["result"] == "Passed"
        assert "lib score:" in (out.get("result_details") or [""])[-1]

    def test_passes_with_reason_when_index_is_higher_resolution(self) -> None:
        """Pass message includes reason when index outresolves the library file."""
        from movarr.filters import _evaluate_library_files

        result = _imdb_result(
            index_title="Test Film 2008 2160p BluRay",
            index_title_sanitised="Test Film 2008 2160p BluRay",
            index_title_resolution="2160",
        )
        out = _evaluate_library_files(result, ["/lib/Test Film 2008 1080p BluRay.mkv"], "2160", Config())
        assert out["result"] == "Passed"
        assert "lower resolution" in (out.get("result_details") or [""])[-1]


# _group_bonus and _special_edition_bonus


class TestGroupAndEditionBonus:
    """Preferred-group and special-edition internal helpers."""

    def test_group_bonus_returns_ten_for_preferred_group(self) -> None:
        from movarr.filters import _group_bonus

        cfg = _make_config(preferred_index_group_list=["FGT"])
        bonus = _group_bonus(
            "The Dark Knight 2008 1080p BluRay FGT",
            "The Dark Knight 2008 1080p BluRay YIFY",
            cfg,
        )
        assert bonus == 10

    def test_group_bonus_returns_zero_when_candidate_not_preferred(self) -> None:
        from movarr.filters import _group_bonus

        cfg = _make_config(preferred_index_group_list=["FGT"])
        bonus = _group_bonus(
            "The Dark Knight 2008 1080p BluRay YIFY",
            "The Dark Knight 2008 1080p BluRay FGT",
            cfg,
        )
        assert bonus == 0

    def test_special_edition_bonus_returns_ten_for_candidate_with_edition(self) -> None:
        from movarr.filters import _special_edition_bonus

        bonus = _special_edition_bonus(
            "The Dark Knight Extended 2008 1080p BluRay",
            "The Dark Knight 2008 1080p BluRay",
        )
        assert bonus == 10

    def test_special_edition_bonus_returns_zero_when_both_have_edition(self) -> None:
        from movarr.filters import _special_edition_bonus

        bonus = _special_edition_bonus(
            "The Dark Knight Extended 2008 1080p BluRay",
            "The Dark Knight Extended 2008 720p BluRay",
        )
        assert bonus == 0


# Log-level behaviour


class TestLogLevels:
    """_fail() must log at WARNING; _pass() must log at INFO."""

    def _capture_records(self, fn: Any) -> list[Any]:
        """Run fn() while capturing loguru records; return the list."""
        records: list[Any] = []

        def sink(msg: Any) -> None:
            records.append(msg.record)

        sink_id = _loguru_logger.add(sink, level=0)
        try:
            fn()
        finally:
            _loguru_logger.remove(sink_id)
        return records

    def test_fail_logs_at_warning_level(self) -> None:
        from movarr.filters import _fail

        records = self._capture_records(lambda: _fail({"result": "Passed", "result_details": []}, "bad thing"))
        assert any(r["level"].name == "WARNING" for r in records), "_fail() must log at WARNING, not INFO"

    def test_pass_logs_at_info_level(self) -> None:
        from movarr.filters import _pass

        records = self._capture_records(lambda: _pass({"result": "Passed", "result_details": []}, "good thing"))
        assert any(r["level"].name == "INFO" for r in records), "_pass() must log at INFO"


# _check_size — ValueError/TypeError path


class TestCheckMinimumSizeEdgeCases:
    """Edge cases for _check_minimum_size — unparseable index_size."""

    def test_unparseable_size_sets_failed(self) -> None:
        from movarr.filters import _check_minimum_size

        result = _index_result(index_size="not-a-number")
        site = _default_site_dict(minimum_size_mb=100)
        out = _check_minimum_size(result, site)
        assert out["result"] == "Failed"


# _check_bitrate — ZeroDivisionError path


class TestCheckBitrateEdgeCases:
    """Edge cases for _check_bitrate — zero runtime (ZeroDivisionError)."""

    def test_zero_runtime_sets_failed(self) -> None:
        from movarr.filters import _check_bitrate

        result = _imdb_result(imdb_running_time_in_minutes="0")
        # _check_bitrate reads _filter_minimum_bitrate_mb directly from the result dict
        result["_filter_minimum_bitrate_mb"] = 50
        result["index_size"] = "8000000000"
        out = _check_bitrate(result)
        assert out["result"] == "Failed"

    def test_empty_string_runtime_sets_failed(self) -> None:
        from movarr.filters import _check_bitrate

        result = _imdb_result(imdb_running_time_in_minutes="")
        result["_filter_minimum_bitrate_mb"] = 50
        result["index_size"] = "8000000000"
        out = _check_bitrate(result)
        assert out["result"] == "Failed"


class TestSpecialEditionToken:
    """Tests for the public special_edition_token helper."""

    def test_returns_empty_string_for_no_token(self) -> None:
        from movarr.filters import special_edition_token

        assert special_edition_token("The Matrix 1999 1080p BluRay") == ""

    def test_returns_extended(self) -> None:
        from movarr.filters import special_edition_token

        assert special_edition_token("The Matrix 1999 Extended 1080p BluRay") == "extended"

    def test_returns_theatrical(self) -> None:
        from movarr.filters import special_edition_token

        assert special_edition_token("The Matrix 1999 Theatrical 1080p BluRay") == "theatrical"

    def test_case_insensitive(self) -> None:
        from movarr.filters import special_edition_token

        assert special_edition_token("The Matrix 1999 EXTENDED 1080p BluRay") == "extended"
