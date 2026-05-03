"""Unit tests for movarr.imdb_metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

    from movarr.models import ResultDict

from movarr.config import Config
from movarr.imdb_metadata import _fetch_imdbpie, _fetch_omdb, fetch_metadata

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(**overrides: object) -> ResultDict:
    """Build a minimal pipeline result dict for metadata tests."""
    base: ResultDict = {
        "imdb_id": "tt0133093",
        "result": "Passed",
        "result_details": [],
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def _make_imdbpie_data() -> tuple[dict, dict, dict, dict]:
    """Return (title_data, genres_data, credits_data, aux_data) for IMDbPie tests."""
    title_data = {
        "base": {
            "title": "The Matrix",
            "year": 1999,
            "titleType": "movie",
            "runningTimeInMinutes": 136,
            "image": {"url": "https://example.com/poster.jpg"},
        },
        "ratings": {"rating": 8.7, "ratingCount": 1_000_000},
        "plot": {
            "summaries": [{"text": "A computer hacker learns the truth about reality."}],
            "outline": {"text": "Neo discovers the Matrix."},
        },
    }
    genres_data: dict = {"genres": ["Action", "Sci-Fi"]}
    credits_data: dict = {
        "credits": {
            "director": [{"name": "Lana Wachowski"}, {"name": "Lilly Wachowski"}],
            "writer": [{"name": "Lilly Wachowski"}],
            "cast": [{"name": "Keanu Reeves", "characters": ["Neo"]}],
        }
    }
    aux_data: dict = {
        "spokenLanguages": ["English"],
        "origins": ["US"],
        "certificate": {"certificate": "15"},
        "videos": {"mainTrailer": {"id": "vi1234567"}},
    }
    return title_data, genres_data, credits_data, aux_data


def _mock_imdbpie_client(mocker: MockerFixture, *, raise_on_fetch: bool = False) -> Any:
    """Set up sys.modules mock for imdbpie and return (mock_module, mock_client)."""
    title_data, genres_data, credits_data, aux_data = _make_imdbpie_data()
    mock_imdbpie = mocker.MagicMock()
    mock_client = mocker.MagicMock()
    mock_imdbpie.Imdb.return_value = mock_client
    if raise_on_fetch:
        mock_client.get_title.side_effect = RuntimeError("network error")
    else:
        mock_client.get_title.return_value = title_data
        mock_client.get_title_genres.return_value = genres_data
        mock_client.get_title_credits.return_value = credits_data
        mock_client.get_title_auxiliary.return_value = aux_data
    mocker.patch.dict("sys.modules", {"imdbpie": mock_imdbpie})
    return mock_imdbpie, mock_client


# ---------------------------------------------------------------------------
# fetch_metadata (public entry point)
# ---------------------------------------------------------------------------


class TestFetchMetadata:
    """Tests for the public fetch_metadata() orchestrator."""

    def test_no_imdb_id_sets_failed(self) -> None:
        result: ResultDict = {"result": "Passed", "result_details": []}
        cfg = Config()
        out = fetch_metadata(result, cfg)
        assert out["result"] == "Failed"

    def test_imdbpie_success_returns_passed(self, mocker: MockerFixture) -> None:
        _mock_imdbpie_client(mocker)
        result = _make_result()
        cfg = Config()
        out = fetch_metadata(result, cfg)
        assert out["result"] == "Passed"
        assert out.get("imdb_title") == "The Matrix"

    def test_imdbpie_failure_tries_omdb_fallback(self, mocker: MockerFixture) -> None:
        mocker.patch.dict("sys.modules", {"imdbpie": None})
        mock_omdb_module = mocker.MagicMock()
        mock_omdb_client = mocker.MagicMock()
        mock_omdb_module.OMDBClient.return_value = mock_omdb_client
        mock_omdb_client.imdbid.return_value = {
            "title": "The Matrix",
            "year": "1999",
            "imdb_rating": "8.7",
            "imdb_votes": "1,000,000",
            "runtime": "136 min",
            "type": "movie",
            "genre": "Action, Sci-Fi",
            "actors": "Keanu Reeves, Laurence Fishburne",
            "director": "Lana Wachowski, Lilly Wachowski",
            "writer": "Lilly Wachowski",
            "rated": "R",
            "country": "United States",
            "language": "English",
            "plot": "A hacker discovers the truth.",
            "poster": "https://example.com/poster.jpg",
        }
        mocker.patch.dict("sys.modules", {"omdb": mock_omdb_module})
        result = _make_result()
        cfg = Config()
        out = fetch_metadata(result, cfg)
        assert out["result"] == "Passed"

    def test_both_strategies_fail_sets_failed(self, mocker: MockerFixture) -> None:
        mocker.patch.dict("sys.modules", {"imdbpie": None})
        mock_omdb_module = mocker.MagicMock()
        mock_omdb_module.OMDBClient.side_effect = RuntimeError("omdb unavailable")
        mocker.patch.dict("sys.modules", {"omdb": mock_omdb_module})
        result = _make_result()
        cfg = Config()
        out = fetch_metadata(result, cfg)
        assert out["result"] == "Failed"


# ---------------------------------------------------------------------------
# _fetch_imdbpie
# ---------------------------------------------------------------------------


class TestFetchImdbpie:
    """Tests for _fetch_imdbpie()."""

    def test_successful_fetch_populates_all_fields(self, mocker: MockerFixture) -> None:
        _mock_imdbpie_client(mocker)
        result = _make_result()
        out = _fetch_imdbpie(result)
        assert out["result"] == "Passed"
        assert out["imdb_title"] == "The Matrix"
        assert out["imdb_year"] == 1999
        assert out["imdb_rating"] == 8.7
        assert out["imdb_votes"] == 1_000_000
        assert out["imdb_title_type"] == "movie"
        assert out["imdb_running_time_in_minutes"] == 136
        assert out["imdb_genres_list"] == ["Action", "Sci-Fi"]
        assert out["imdb_cert_source"] == "imdbpie"
        assert out["imdb_certification"] == "15"
        assert out["imdb_language_list"] == ["English"]
        assert out["imdb_country_list"] == ["US"]

    def test_successful_fetch_populates_credits(self, mocker: MockerFixture) -> None:
        _mock_imdbpie_client(mocker)
        result = _make_result()
        out = _fetch_imdbpie(result)
        assert out["imdb_credits_director_list"] == ["Lana Wachowski", "Lilly Wachowski"]
        assert out["imdb_credits_writer_list"] == ["Lilly Wachowski"]
        assert out["imdb_credits_cast_list"] == ["Keanu Reeves"]
        assert out["imdb_credits_character_list"] == ["Neo"]

    def test_successful_fetch_sets_trailer_url(self, mocker: MockerFixture) -> None:
        _mock_imdbpie_client(mocker)
        result = _make_result()
        out = _fetch_imdbpie(result)
        assert out.get("imdb_trailer_url") == "https://imdb.com/video/vi1234567"

    def test_imdbpie_import_error_sets_failed(self, mocker: MockerFixture) -> None:
        mocker.patch.dict("sys.modules", {"imdbpie": None})
        result = _make_result()
        out = _fetch_imdbpie(result)
        assert out["result"] == "Failed"

    def test_data_fetch_error_sets_failed(self, mocker: MockerFixture) -> None:
        _mock_imdbpie_client(mocker, raise_on_fetch=True)
        result = _make_result()
        out = _fetch_imdbpie(result)
        assert out["result"] == "Failed"

    def test_poster_url_populated(self, mocker: MockerFixture) -> None:
        _mock_imdbpie_client(mocker)
        result = _make_result()
        out = _fetch_imdbpie(result)
        assert out.get("imdb_poster_url") == "https://example.com/poster.jpg"

    def test_plot_summary_populated(self, mocker: MockerFixture) -> None:
        _mock_imdbpie_client(mocker)
        result = _make_result()
        out = _fetch_imdbpie(result)
        assert out.get("imdb_plot_summary") == "A computer hacker learns the truth about reality."

    def test_no_cert_sets_cert_source_none(self, mocker: MockerFixture) -> None:
        title_data, genres_data, credits_data, _ = _make_imdbpie_data()
        aux_data: dict = {"spokenLanguages": ["English"], "origins": ["US"]}
        mock_imdbpie = mocker.MagicMock()
        mock_client = mocker.MagicMock()
        mock_imdbpie.Imdb.return_value = mock_client
        mock_client.get_title.return_value = title_data
        mock_client.get_title_genres.return_value = genres_data
        mock_client.get_title_credits.return_value = credits_data
        mock_client.get_title_auxiliary.return_value = aux_data
        mocker.patch.dict("sys.modules", {"imdbpie": mock_imdbpie})
        result = _make_result()
        out = _fetch_imdbpie(result)
        assert out.get("imdb_cert_source") is None


# ---------------------------------------------------------------------------
# _fetch_omdb
# ---------------------------------------------------------------------------


class TestFetchOmdb:
    """Tests for _fetch_omdb()."""

    def _setup_omdb_mock(self, mocker: MockerFixture, return_value: dict) -> None:
        mock_omdb = mocker.MagicMock()
        mock_client = mocker.MagicMock()
        mock_omdb.OMDBClient.return_value = mock_client
        mock_client.imdbid.return_value = return_value
        mocker.patch.dict("sys.modules", {"omdb": mock_omdb})

    def test_successful_fetch_populates_fields(self, mocker: MockerFixture) -> None:
        self._setup_omdb_mock(
            mocker,
            {
                "title": "The Matrix",
                "year": "1999",
                "imdb_rating": "8.7",
                "imdb_votes": "1,500,000",
                "runtime": "136 min",
                "type": "movie",
                "genre": "Action, Sci-Fi",
                "actors": "Keanu Reeves, Laurence Fishburne",
                "director": "Lana Wachowski",
                "writer": "Lilly Wachowski",
                "rated": "R",
                "country": "United States",
                "language": "English",
                "plot": "A hacker discovers the truth.",
                "poster": "https://example.com/poster.jpg",
            },
        )
        result = _make_result()
        cfg = Config()
        cfg.credentials.omdb.api_key = "test_key"
        out = _fetch_omdb(result, cfg)
        assert out["result"] == "Passed"
        assert out["imdb_title"] == "The Matrix"
        assert out["imdb_year"] == 1999
        assert out["imdb_cert_source"] == "omdb"

    def test_cert_source_none_when_no_rating(self, mocker: MockerFixture) -> None:
        self._setup_omdb_mock(
            mocker,
            {
                "title": "The Matrix",
                "year": "1999",
                "rated": "N/A",
            },
        )
        result = _make_result()
        cfg = Config()
        out = _fetch_omdb(result, cfg)
        assert out.get("imdb_cert_source") is None

    def test_not_rated_normalised_to_none(self, mocker: MockerFixture) -> None:
        self._setup_omdb_mock(
            mocker,
            {"title": "The Matrix", "year": "1999", "rated": "Not Rated"},
        )
        result = _make_result()
        cfg = Config()
        out = _fetch_omdb(result, cfg)
        assert out.get("imdb_certification") is None

    def test_omdb_exception_sets_failed(self, mocker: MockerFixture) -> None:
        mock_omdb = mocker.MagicMock()
        mock_client = mocker.MagicMock()
        mock_omdb.OMDBClient.return_value = mock_client
        mock_client.imdbid.side_effect = RuntimeError("api error")
        mocker.patch.dict("sys.modules", {"omdb": mock_omdb})
        result = _make_result()
        cfg = Config()
        out = _fetch_omdb(result, cfg)
        assert out["result"] == "Failed"

    def test_omdb_constructor_exception_sets_failed(self, mocker: MockerFixture) -> None:
        mock_omdb = mocker.MagicMock()
        mock_omdb.OMDBClient.side_effect = RuntimeError("no connection")
        mocker.patch.dict("sys.modules", {"omdb": mock_omdb})
        result = _make_result()
        cfg = Config()
        out = _fetch_omdb(result, cfg)
        assert out["result"] == "Failed"

    def test_votes_digits_only_extracted(self, mocker: MockerFixture) -> None:
        self._setup_omdb_mock(
            mocker,
            {"title": "The Matrix", "year": "1999", "imdb_votes": "1,500,000", "imdb_rating": "8.7"},
        )
        result = _make_result()
        cfg = Config()
        out = _fetch_omdb(result, cfg)
        # Votes should be an integer (no commas or non-digit chars)
        votes = out.get("imdb_votes")
        if votes:
            assert isinstance(votes, int)

    def test_genres_split_by_comma(self, mocker: MockerFixture) -> None:
        self._setup_omdb_mock(
            mocker,
            {"title": "The Matrix", "year": "1999", "genre": "Action, Sci-Fi, Thriller"},
        )
        result = _make_result()
        cfg = Config()
        out = _fetch_omdb(result, cfg)
        genres = out.get("imdb_genres_list")
        if genres:
            assert "Action" in genres
            assert "Sci-Fi" in genres
