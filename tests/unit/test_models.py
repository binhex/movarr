"""Unit tests for movarr.models — ResultDict TypedDict construction and access."""

from __future__ import annotations

from movarr.models import ResultDict, build_result_dict  # noqa: TCH001 — runtime import needed


class TestResultDictConstruction:
    """ResultDict can be constructed with various field combinations."""

    def test_empty_dict_is_valid(self) -> None:
        """An empty ResultDict is valid because total=False makes all fields optional."""
        result: ResultDict = {}
        assert result == {}

    def test_index_metadata_fields(self) -> None:
        """Index metadata fields can be set and read back."""
        result: ResultDict = {
            "index_title": "The Matrix (1999)",
            "index_size": "1.5 GB",
            "index_size_mb": "1500",
            "index_seeders": "100",
            "index_peers": "20",
            "index_pubdate": "2023-01-01",
            "index_details": "some details",
        }
        assert result["index_title"] == "The Matrix (1999)"
        assert result["index_size_mb"] == "1500"
        assert result["index_seeders"] == "100"

    def test_parsed_index_fields(self) -> None:
        """Parsed index fields can be set and read back."""
        result: ResultDict = {
            "index_title_sanitised": "The Matrix",
            "index_title_group": "YIFY",
            "index_title_resolution": "1080p",
            "index_title_after_year_to_end": "BluRay x264",
        }
        assert result["index_title_sanitised"] == "The Matrix"
        assert result["index_title_resolution"] == "1080p"
        assert result["index_title_group"] == "YIFY"

    def test_movie_metadata_fields(self) -> None:
        """Movie metadata fields can be set and read back."""
        result: ResultDict = {
            "movie_title": "The Matrix",
            "movie_title_year": "1999",
            "movie_title_and_year_search": "The Matrix 1999",
            "movie_title_compare": "the matrix",
            "movie_title_and_year_compare": "the matrix 1999",
        }
        assert result["movie_title"] == "The Matrix"
        assert result["movie_title_year"] == "1999"
        assert result["movie_title_and_year_search"] == "The Matrix 1999"

    def test_torrent_fields(self) -> None:
        """Torrent-specific fields can be set and read back."""
        result: ResultDict = {
            "torrent_url": "https://example.com/file.torrent",
            "magnet_url": "magnet:?xt=urn:btih:abc123",
            "torrent_hash": "abc123def456",
            "category": "movies",
            "torrent_tag": "hd",
        }
        assert result["torrent_url"] == "https://example.com/file.torrent"
        assert result["category"] == "movies"
        assert result["torrent_hash"] == "abc123def456"

    def test_filter_bitrate_field(self) -> None:
        """Private-prefixed bitrate field can be set and read back."""
        result: ResultDict = {"_filter_minimum_bitrate_mb": 5000}
        assert result["_filter_minimum_bitrate_mb"] == 5000

    def test_imdb_string_fields(self) -> None:
        """IMDb scalar string fields can be set and read back."""
        result: ResultDict = {
            "imdb_id": "tt0133093",
            "imdb_title": "The Matrix",
            "imdb_year": 1999,
            "imdb_rating": 8.7,
            "imdb_votes": 2_000_000,
            "imdb_title_type": "movie",
            "imdb_running_time_in_minutes": 136,
        }
        assert result["imdb_id"] == "tt0133093"
        assert result["imdb_rating"] == 8.7
        assert result["imdb_running_time_in_minutes"] == 136

    def test_imdb_list_fields(self) -> None:
        """IMDb list fields can be set and read back."""
        result: ResultDict = {
            "imdb_genres_list": ["Action", "Sci-Fi"],
            "imdb_credits_cast_list": ["Keanu Reeves", "Laurence Fishburne"],
            "imdb_credits_director_list": ["Lana Wachowski"],
            "imdb_credits_writer_list": ["Lilly Wachowski"],
            "imdb_credits_character_list": ["Neo", "Morpheus"],
            "imdb_language_list": ["English"],
            "imdb_country_list": ["USA"],
        }
        assert result["imdb_genres_list"] == ["Action", "Sci-Fi"]
        assert result["imdb_credits_cast_list"] == ["Keanu Reeves", "Laurence Fishburne"]
        assert result["imdb_country_list"] == ["USA"]

    def test_imdb_nullable_string_fields_set_to_none(self) -> None:
        """IMDb nullable fields can be explicitly set to None."""
        result: ResultDict = {
            "imdb_title": None,
            "imdb_year": None,
            "imdb_rating": None,
            "imdb_votes": None,
            "imdb_title_type": None,
            "imdb_running_time_in_minutes": None,
            "imdb_certification": None,
            "imdb_cert_source": None,
            "imdb_poster_url": None,
            "imdb_trailer_url": None,
            "imdb_plot_summary": None,
            "imdb_plot_outline": None,
        }
        assert result["imdb_title"] is None
        assert result["imdb_rating"] is None
        assert result["imdb_cert_source"] is None

    def test_imdb_nullable_list_fields_set_to_none(self) -> None:
        """IMDb nullable list fields can be explicitly set to None."""
        result: ResultDict = {
            "imdb_genres_list": None,
            "imdb_credits_cast_list": None,
            "imdb_credits_director_list": None,
            "imdb_credits_writer_list": None,
            "imdb_credits_character_list": None,
            "imdb_language_list": None,
            "imdb_country_list": None,
        }
        assert result["imdb_genres_list"] is None
        assert result["imdb_language_list"] is None

    def test_pipeline_outcome_fields(self) -> None:
        """Pipeline outcome fields can be set and read back."""
        result: ResultDict = {
            "result": "Passed",
            "result_details": ["Filter A: passed", "Filter B: passed"],
            "verified": "true",
        }
        assert result["result"] == "Passed"
        assert result["result_details"] == ["Filter A: passed", "Filter B: passed"]
        assert result["verified"] == "true"

    def test_pipeline_failed_result(self) -> None:
        """Pipeline outcome 'Failed' can be stored."""
        result: ResultDict = {
            "result": "Failed",
            "result_details": ["Filter A: passed", "Filter B: failed — rating too low"],
        }
        assert result["result"] == "Failed"
        assert len(result["result_details"]) == 2


class TestResultDictGetMethod:
    """ResultDict supports dict .get() for safe access of optional fields."""

    def test_get_missing_key_returns_none(self) -> None:
        """Missing optional fields return None via .get()."""
        result: ResultDict = {}
        assert result.get("result") is None

    def test_get_missing_key_with_default(self) -> None:
        """Missing optional fields return the supplied default via .get()."""
        result: ResultDict = {}
        assert result.get("imdb_title", "unknown") == "unknown"

    def test_get_present_key_returns_value(self) -> None:
        """Present fields return their value via .get()."""
        result: ResultDict = {"result": "Passed"}
        assert result.get("result") == "Passed"

    def test_get_present_key_ignores_default(self) -> None:
        """When a key is present .get() returns the stored value, not the default."""
        result: ResultDict = {"imdb_rating": 9.0}
        assert result.get("imdb_rating", 0.0) == 9.0

    def test_get_none_value_not_confused_with_missing(self) -> None:
        """A field set to None should be distinguishable from a missing field."""
        result: ResultDict = {"imdb_title": None}
        assert "imdb_title" in result
        assert result.get("imdb_title") is None


class TestResultDictFullPayload:
    """ResultDict can hold all fields simultaneously."""

    def test_full_result_dict_roundtrip(self) -> None:
        """All defined fields can coexist in a single ResultDict."""
        result: ResultDict = {
            "index_title": "Inception (2010) 1080p BluRay",
            "index_size": "8.0 GB",
            "index_size_mb": "8192",
            "index_seeders": "500",
            "index_peers": "50",
            "index_pubdate": "2023-06-15",
            "index_details": "http://example.com/details",
            "index_title_sanitised": "Inception",
            "index_title_group": "SPARKS",
            "index_title_resolution": "1080p",
            "index_title_after_year_to_end": "BluRay x264-SPARKS",
            "movie_title": "Inception",
            "movie_title_year": "2010",
            "movie_title_and_year_search": "Inception 2010",
            "movie_title_compare": "inception",
            "movie_title_and_year_compare": "inception 2010",
            "torrent_url": "https://example.com/t.torrent",
            "magnet_url": "magnet:?xt=urn:btih:xyz",
            "torrent_hash": "xyzxyz",
            "category": "movies",
            "_filter_minimum_bitrate_mb": 6000,
            "torrent_tag": "hd",
            "imdb_id": "tt1375666",
            "imdb_title": "Inception",
            "imdb_year": 2010,
            "imdb_rating": 8.8,
            "imdb_votes": 2_300_000,
            "imdb_title_type": "movie",
            "imdb_running_time_in_minutes": 148,
            "imdb_genres_list": ["Action", "Adventure", "Sci-Fi"],
            "imdb_credits_cast_list": ["Leonardo DiCaprio"],
            "imdb_credits_director_list": ["Christopher Nolan"],
            "imdb_credits_writer_list": ["Christopher Nolan"],
            "imdb_credits_character_list": ["Cobb"],
            "imdb_language_list": ["English", "Japanese", "French"],
            "imdb_country_list": ["USA", "UK"],
            "imdb_certification": "PG-13",
            "imdb_cert_source": "mpaa",
            "imdb_poster_url": "https://example.com/poster.jpg",
            "imdb_trailer_url": "https://example.com/trailer",
            "imdb_plot_summary": "A thief who steals corporate secrets.",
            "imdb_plot_outline": "Dreams within dreams.",
            "result": "Passed",
            "result_details": ["all filters passed"],
            "verified": "true",
        }
        assert result["imdb_id"] == "tt1375666"
        assert len(result["imdb_genres_list"]) == 3  # type: ignore[arg-type]
        assert result["result"] == "Passed"
        assert result["verified"] == "true"


class TestBuildResultDict:
    """build_result_dict() constructs a base ResultDict from shared index fields."""

    _DEFAULTS = {
        "index_title": "Some Title",
        "index_tracker": "tracker-x",
        "index_pubdate": "2024-01-01",
        "index_details": "https://example.com/info",
        "index_seeders": "42",
        "index_peers": "10",
        "index_size": "1073741824",
        "index_size_mb": "1024.00",
        "torrent_url": "https://example.com/file.torrent",
        "magnet_url": "magnet:?xt=urn:btih:abc",
        "category": "2000",
    }

    def test_returns_result_dict_type(self) -> None:
        """Return value is a dict (TypedDict is a dict at runtime)."""
        result = build_result_dict(**self._DEFAULTS)
        assert isinstance(result, dict)

    def test_all_supplied_fields_present(self) -> None:
        """Every keyword argument is stored under the matching key."""
        result = build_result_dict(**self._DEFAULTS)
        result_as_dict = dict(result)
        for key, value in self._DEFAULTS.items():
            assert result_as_dict[key] == value, f"Mismatch on key '{key}'"

    def test_result_preset_to_passed(self) -> None:
        """The 'result' field is always initialised to 'Passed'."""
        result = build_result_dict(**self._DEFAULTS)
        assert result["result"] == "Passed"

    def test_result_details_preset_to_empty_list(self) -> None:
        """The 'result_details' field is always initialised to an empty list."""
        result = build_result_dict(**self._DEFAULTS)
        assert result["result_details"] == []

    def test_result_details_is_independent_per_call(self) -> None:
        """Each call returns a fresh list so mutations don't bleed between results."""
        r1 = build_result_dict(**self._DEFAULTS)
        r2 = build_result_dict(**self._DEFAULTS)
        r1["result_details"].append("x")
        assert r2["result_details"] == []

    def test_empty_strings_allowed(self) -> None:
        """Fields like magnet_url and category may be empty strings."""
        result = build_result_dict(**{**self._DEFAULTS, "magnet_url": "", "category": ""})
        assert result["magnet_url"] == ""
        assert result["category"] == ""
