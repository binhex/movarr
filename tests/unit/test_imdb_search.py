"""Unit tests for movarr.imdb_search."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

    from movarr.models import ResultDict

from movarr.config import Config
from movarr.imdb_search import (
    _OMDB_NOT_FOUND_ERROR,
    _fail,
    _pass,
    _search_google,
    _search_imdbpie,
    _search_omdb,
    _search_tmdb,
    search_for_imdb_id,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(**overrides: Any) -> ResultDict:
    """Build a minimal pipeline result dict for search tests."""
    base: ResultDict = {
        "movie_title": "The Matrix",
        "movie_title_year": "1999",
        "movie_title_and_year_search": "The Matrix 1999",
        "index_title_compare": "thematrix 1999",
        "result": "Passed",
        "result_details": [],
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


# ---------------------------------------------------------------------------
# _fail and _pass helpers
# ---------------------------------------------------------------------------


class TestFailHelper:
    """Unit tests for the _fail() result helper."""

    def test_sets_result_to_failed(self) -> None:
        result: ResultDict = {"result": "Passed", "result_details": []}
        _fail(result, "something went wrong")
        assert result["result"] == "Failed"

    def test_appends_failed_message_to_details(self) -> None:
        result: ResultDict = {"result": "Passed", "result_details": []}
        _fail(result, "bad thing")
        assert any("bad thing" in d for d in result["result_details"])

    def test_initialises_result_details_if_absent(self) -> None:
        result: ResultDict = {"result": "Passed"}
        _fail(result, "error")
        assert "result_details" in result
        assert len(result["result_details"]) == 1


class TestPassHelper:
    """Unit tests for the _pass() result helper."""

    def test_sets_result_to_passed(self) -> None:
        result: ResultDict = {"result": "Failed", "result_details": []}
        _pass(result, "tt0133093", "found it")
        assert result["result"] == "Passed"

    def test_sets_imdb_id(self) -> None:
        result: ResultDict = {"result": "Failed", "result_details": []}
        _pass(result, "tt0133093", "found it")
        assert result["imdb_id"] == "tt0133093"

    def test_appends_passed_message_to_details(self) -> None:
        result: ResultDict = {"result": "Failed", "result_details": []}
        _pass(result, "tt0133093", "found it")
        assert any("found it" in d for d in result["result_details"])

    def test_initialises_result_details_if_absent(self) -> None:
        result: ResultDict = {"result": "Failed"}
        _pass(result, "tt0133093", "ok")
        assert "result_details" in result


# ---------------------------------------------------------------------------
# _search_imdbpie
# ---------------------------------------------------------------------------


class TestSearchImdbpie:
    """Tests for the IMDbPie search strategy."""

    def test_match_found_sets_imdb_id_and_passes(self, mocker: MockerFixture) -> None:
        mock_imdbpie = mocker.MagicMock()
        mock_client = mocker.MagicMock()
        mock_imdbpie.Imdb.return_value = mock_client
        mock_client.search_for_title.return_value = [
            {"title": "The Matrix", "year": "1999", "imdb_id": "tt0133093"},
        ]
        mocker.patch.dict("sys.modules", {"imdbpie": mock_imdbpie})
        cfg = Config()
        result = _make_result()
        out = _search_imdbpie(result, cfg)
        assert out["result"] == "Passed"
        assert out["imdb_id"] == "tt0133093"

    def test_no_hits_sets_failed(self, mocker: MockerFixture) -> None:
        mock_imdbpie = mocker.MagicMock()
        mock_client = mocker.MagicMock()
        mock_imdbpie.Imdb.return_value = mock_client
        mock_client.search_for_title.return_value = []
        mocker.patch.dict("sys.modules", {"imdbpie": mock_imdbpie})
        cfg = Config()
        result = _make_result()
        out = _search_imdbpie(result, cfg)
        assert out["result"] == "Failed"

    def test_year_mismatch_sets_failed(self, mocker: MockerFixture) -> None:
        mock_imdbpie = mocker.MagicMock()
        mock_client = mocker.MagicMock()
        mock_imdbpie.Imdb.return_value = mock_client
        mock_client.search_for_title.return_value = [
            {"title": "The Matrix", "year": "2003", "imdb_id": "tt0234215"},
        ]
        mocker.patch.dict("sys.modules", {"imdbpie": mock_imdbpie})
        cfg = Config()
        result = _make_result()
        out = _search_imdbpie(result, cfg)
        assert out["result"] == "Failed"

    def test_title_mismatch_sets_failed(self, mocker: MockerFixture) -> None:
        mock_imdbpie = mocker.MagicMock()
        mock_client = mocker.MagicMock()
        mock_imdbpie.Imdb.return_value = mock_client
        mock_client.search_for_title.return_value = [
            {"title": "Matrix Reloaded", "year": "1999", "imdb_id": "tt0234215"},
        ]
        mocker.patch.dict("sys.modules", {"imdbpie": mock_imdbpie})
        cfg = Config()
        result = _make_result()
        out = _search_imdbpie(result, cfg)
        assert out["result"] == "Failed"

    def test_import_error_sets_failed(self, mocker: MockerFixture) -> None:
        mocker.patch.dict("sys.modules", {"imdbpie": None})
        cfg = Config()
        result = _make_result()
        out = _search_imdbpie(result, cfg)
        assert out["result"] == "Failed"

    def test_hit_missing_imdb_id_skipped(self, mocker: MockerFixture) -> None:
        mock_imdbpie = mocker.MagicMock()
        mock_client = mocker.MagicMock()
        mock_imdbpie.Imdb.return_value = mock_client
        mock_client.search_for_title.return_value = [
            {"title": "The Matrix", "year": "1999"},  # no imdb_id
        ]
        mocker.patch.dict("sys.modules", {"imdbpie": mock_imdbpie})
        cfg = Config()
        result = _make_result()
        out = _search_imdbpie(result, cfg)
        assert out["result"] == "Failed"


# ---------------------------------------------------------------------------
# _search_tmdb
# ---------------------------------------------------------------------------


class TestSearchTmdb:
    """Tests for the TMDb search strategy."""

    def test_no_api_key_sets_failed(self) -> None:
        cfg = Config()  # api_key = "" by default
        result = _make_result()
        out = _search_tmdb(result, cfg)
        assert out["result"] == "Failed"

    def test_successful_match_sets_imdb_id_and_passes(self, mocker: MockerFixture) -> None:
        cfg = Config()
        cfg.credentials.tmdb.api_key = "test_key"
        mock_http = mocker.MagicMock()
        mocker.patch("movarr.imdb_search.HttpClient", return_value=mock_http)
        search_resp = mocker.MagicMock()
        search_resp.content = json.dumps(
            {"results": [{"title": "The Matrix", "release_date": "1999-03-31", "id": 603}]}
        )
        detail_resp = mocker.MagicMock()
        detail_resp.content = json.dumps({"imdb_id": "tt0133093"})
        mock_http.get.side_effect = [search_resp, detail_resp]
        result = _make_result()
        out = _search_tmdb(result, cfg)
        assert out["result"] == "Passed"
        assert out["imdb_id"] == "tt0133093"

    def test_year_mismatch_sets_failed(self, mocker: MockerFixture) -> None:
        cfg = Config()
        cfg.credentials.tmdb.api_key = "test_key"
        mock_http = mocker.MagicMock()
        mocker.patch("movarr.imdb_search.HttpClient", return_value=mock_http)
        search_resp = mocker.MagicMock()
        search_resp.content = json.dumps(
            {"results": [{"title": "The Matrix", "release_date": "2003-05-15", "id": 604}]}
        )
        mock_http.get.return_value = search_resp
        result = _make_result()
        out = _search_tmdb(result, cfg)
        assert out["result"] == "Failed"

    def test_title_not_in_index_compare_sets_failed(self, mocker: MockerFixture) -> None:
        cfg = Config()
        cfg.credentials.tmdb.api_key = "test_key"
        mock_http = mocker.MagicMock()
        mocker.patch("movarr.imdb_search.HttpClient", return_value=mock_http)
        search_resp = mocker.MagicMock()
        search_resp.content = json.dumps(
            {"results": [{"title": "Something Completely Different", "release_date": "1999-01-01", "id": 999}]}
        )
        mock_http.get.return_value = search_resp
        result = _make_result()
        out = _search_tmdb(result, cfg)
        assert out["result"] == "Failed"

    def test_http_error_sets_failed(self, mocker: MockerFixture) -> None:
        from movarr.downloader import HttpError

        cfg = Config()
        cfg.credentials.tmdb.api_key = "test_key"
        mock_http = mocker.MagicMock()
        mocker.patch("movarr.imdb_search.HttpClient", return_value=mock_http)
        mock_http.get.side_effect = HttpError("connection refused")
        result = _make_result()
        out = _search_tmdb(result, cfg)
        assert out["result"] == "Failed"

    def test_empty_results_sets_failed(self, mocker: MockerFixture) -> None:
        cfg = Config()
        cfg.credentials.tmdb.api_key = "test_key"
        mock_http = mocker.MagicMock()
        mocker.patch("movarr.imdb_search.HttpClient", return_value=mock_http)
        search_resp = mocker.MagicMock()
        search_resp.content = json.dumps({"results": []})
        mock_http.get.return_value = search_resp
        result = _make_result()
        out = _search_tmdb(result, cfg)
        assert out["result"] == "Failed"


# ---------------------------------------------------------------------------
# _search_omdb
# ---------------------------------------------------------------------------


class TestSearchOmdb:
    """Tests for the OMDb search strategy."""

    def test_no_api_key_sets_failed(self) -> None:
        cfg = Config()  # api_key = "" by default
        result = _make_result()
        out = _search_omdb(result, cfg)
        assert out["result"] == "Failed"

    def test_successful_match_sets_imdb_id_and_passes(self, mocker: MockerFixture) -> None:
        cfg = Config()
        cfg.credentials.omdb.api_key = "test_key"
        mock_http = mocker.MagicMock()
        mocker.patch("movarr.imdb_search.HttpClient", return_value=mock_http)
        resp = mocker.MagicMock()
        resp.content = json.dumps({"Title": "The Matrix", "Year": "1999", "imdbID": "tt0133093"})
        mock_http.get.return_value = resp
        result = _make_result()
        out = _search_omdb(result, cfg)
        assert out["result"] == "Passed"
        assert out["imdb_id"] == "tt0133093"

    def test_title_mismatch_sets_failed(self, mocker: MockerFixture) -> None:
        cfg = Config()
        cfg.credentials.omdb.api_key = "test_key"
        mock_http = mocker.MagicMock()
        mocker.patch("movarr.imdb_search.HttpClient", return_value=mock_http)
        resp = mocker.MagicMock()
        resp.content = json.dumps({"Title": "Something Else", "Year": "1999", "imdbID": "tt0099999"})
        mock_http.get.return_value = resp
        result = _make_result()
        out = _search_omdb(result, cfg)
        assert out["result"] == "Failed"

    def test_year_mismatch_sets_failed(self, mocker: MockerFixture) -> None:
        cfg = Config()
        cfg.credentials.omdb.api_key = "test_key"
        mock_http = mocker.MagicMock()
        mocker.patch("movarr.imdb_search.HttpClient", return_value=mock_http)
        resp = mocker.MagicMock()
        resp.content = json.dumps({"Title": "The Matrix", "Year": "2003", "imdbID": "tt0133093"})
        mock_http.get.return_value = resp
        result = _make_result()
        out = _search_omdb(result, cfg)
        assert out["result"] == "Failed"

    def test_http_error_sets_failed(self, mocker: MockerFixture) -> None:
        from movarr.downloader import HttpError

        cfg = Config()
        cfg.credentials.omdb.api_key = "test_key"
        mock_http = mocker.MagicMock()
        mocker.patch("movarr.imdb_search.HttpClient", return_value=mock_http)
        mock_http.get.side_effect = HttpError("timeout")
        result = _make_result()
        out = _search_omdb(result, cfg)
        assert out["result"] == "Failed"

    def test_missing_imdb_id_in_response_sets_failed(self, mocker: MockerFixture) -> None:
        cfg = Config()
        cfg.credentials.omdb.api_key = "test_key"
        mock_http = mocker.MagicMock()
        mocker.patch("movarr.imdb_search.HttpClient", return_value=mock_http)
        resp = mocker.MagicMock()
        resp.content = json.dumps({"Title": "The Matrix", "Year": "1999"})  # no imdbID
        mock_http.get.return_value = resp
        result = _make_result()
        out = _search_omdb(result, cfg)
        assert out["result"] == "Failed"


# ---------------------------------------------------------------------------
# _search_google
# ---------------------------------------------------------------------------


class TestSearchGoogle:
    """Tests for the Google search strategy (last resort)."""

    def test_imdb_id_extracted_from_url_passes(self, mocker: MockerFixture) -> None:
        mock_gs = mocker.MagicMock()
        mock_hit = mocker.MagicMock()
        mock_hit.title = "The Matrix 1999"
        mock_hit.url = "https://www.imdb.com/title/tt0133093/"
        mock_gs.search.return_value = iter([mock_hit])
        mocker.patch.dict("sys.modules", {"googlesearch": mock_gs})
        # "thematrix1999" is normalised form; set index_title_compare to contain it
        result = _make_result(index_title_compare="thematrix1999")
        cfg = Config()
        out = _search_google(result, cfg)
        assert out["result"] == "Passed"
        assert out["imdb_id"] == "tt0133093"

    def test_no_results_stop_iteration_sets_failed(self, mocker: MockerFixture) -> None:
        mock_gs = mocker.MagicMock()
        mock_gs.search.return_value = iter([])
        mocker.patch.dict("sys.modules", {"googlesearch": mock_gs})
        result = _make_result()
        cfg = Config()
        out = _search_google(result, cfg)
        assert out["result"] == "Failed"

    def test_no_imdb_id_in_url_sets_failed(self, mocker: MockerFixture) -> None:
        mock_gs = mocker.MagicMock()
        mock_hit = mocker.MagicMock()
        mock_hit.title = "The Matrix 1999"
        mock_hit.url = "https://www.somesite.com/the-matrix"
        mock_gs.search.return_value = iter([mock_hit])
        mocker.patch.dict("sys.modules", {"googlesearch": mock_gs})
        result = _make_result(index_title_compare="thematrix1999")
        cfg = Config()
        out = _search_google(result, cfg)
        assert out["result"] == "Failed"

    def test_year_not_in_title_still_passes(self, mocker: MockerFixture) -> None:
        """Google snippet titles often omit the year; IMDb URL is authoritative enough."""
        mock_gs = mocker.MagicMock()
        mock_hit = mocker.MagicMock()
        mock_hit.title = "The Matrix IMDb"  # no year
        mock_hit.url = "https://www.imdb.com/title/tt0133093/"
        mock_gs.search.return_value = iter([mock_hit])
        mocker.patch.dict("sys.modules", {"googlesearch": mock_gs})
        result = _make_result(index_title_compare="thematrix")
        cfg = Config()
        out = _search_google(result, cfg)
        assert out["result"] == "Passed"

    def test_import_error_sets_failed(self, mocker: MockerFixture) -> None:
        mocker.patch.dict("sys.modules", {"googlesearch": None})
        result = _make_result()
        cfg = Config()
        out = _search_google(result, cfg)
        assert out["result"] == "Failed"

    def test_requests_ten_results(self, mocker: MockerFixture) -> None:
        """Google search must request up to 10 results so we can skip non-IMDb hits."""
        mock_gs = mocker.MagicMock()
        mock_hit = mocker.MagicMock()
        mock_hit.title = "The Matrix 1999"
        mock_hit.url = "https://www.imdb.com/title/tt0133093/"
        mock_gs.search.return_value = iter([mock_hit])
        mocker.patch.dict("sys.modules", {"googlesearch": mock_gs})
        result = _make_result(index_title_compare="thematrix1999")
        _search_google(result, Config())

        call_kwargs = mock_gs.search.call_args.kwargs
        assert call_kwargs.get("num_results") == 10

    def test_skips_non_imdb_results_and_finds_match(self, mocker: MockerFixture) -> None:
        """First result is Wikipedia, second result is IMDb — must skip to the IMDb hit."""
        mock_gs = mocker.MagicMock()
        hit1 = mocker.MagicMock()
        hit1.title = "The Matrix - Wikipedia"
        hit1.url = "https://en.wikipedia.org/wiki/The_Matrix"
        hit2 = mocker.MagicMock()
        hit2.title = "The Matrix (1999) - IMDb"
        hit2.url = "https://www.imdb.com/title/tt0133093/"
        mock_gs.search.return_value = iter([hit1, hit2])
        mocker.patch.dict("sys.modules", {"googlesearch": mock_gs})
        result = _make_result(index_title_compare="thematrix1999")
        out = _search_google(result, Config())
        assert out["result"] == "Passed"
        assert out["imdb_id"] == "tt0133093"

    def test_year_not_required_in_google_title(self, mocker: MockerFixture) -> None:
        """Google result titles often omit the year (e.g. 'Title - IMDb').

        We should accept the result if the title matches and the URL contains
        an IMDb tt-number, without requiring the year in the snippet title.
        """
        mock_gs = mocker.MagicMock()
        mock_hit = mocker.MagicMock()
        mock_hit.title = "Little Amelie or the Character of Rain - IMDb"
        mock_hit.url = "https://www.imdb.com/title/tt1234567/"
        mock_gs.search.return_value = iter([mock_hit])
        mocker.patch.dict("sys.modules", {"googlesearch": mock_gs})
        result = _make_result(
            movie_title="Little Amelie or the Character of Rain",
            movie_title_year="2025",
            movie_title_and_year_search="Little Amelie or the Character of Rain 2025",
            index_title_compare="littleamelieorthecharacterofrain2025",
        )
        out = _search_google(result, Config())
        assert out["result"] == "Passed"
        assert out["imdb_id"] == "tt1234567"

    def test_skips_hit_with_wrong_year_in_title(self, mocker: MockerFixture) -> None:
        """If Google title contains a year that does NOT match, skip that hit."""
        mock_gs = mocker.MagicMock()
        hit1 = mocker.MagicMock()
        hit1.title = "The Matrix (1999) - IMDb"  # wrong year
        hit1.url = "https://www.imdb.com/title/tt0133093/"
        hit2 = mocker.MagicMock()
        hit2.title = "The Matrix Resurrections - IMDb"  # no year, accepted
        hit2.url = "https://www.imdb.com/title/tt10838180/"
        mock_gs.search.return_value = iter([hit1, hit2])
        mocker.patch.dict("sys.modules", {"googlesearch": mock_gs})
        result = _make_result(
            movie_title="The Matrix Resurrections",
            movie_title_year="2021",
            movie_title_and_year_search="The Matrix Resurrections 2021",
            index_title_compare="thematrixresurrections2021",
        )
        out = _search_google(result, Config())
        assert out["result"] == "Passed"
        assert out["imdb_id"] == "tt10838180"

    def test_year_in_movie_title_not_rejected(self, mocker: MockerFixture) -> None:
        """A year-like movie title (e.g. '1917') must not be treated as a wrong-year hit."""
        mock_gs = mocker.MagicMock()
        mock_hit = mocker.MagicMock()
        mock_hit.title = "1917 - IMDb"
        mock_hit.url = "https://www.imdb.com/title/tt8579674/"
        mock_gs.search.return_value = iter([mock_hit])
        mocker.patch.dict("sys.modules", {"googlesearch": mock_gs})
        result = _make_result(
            movie_title="1917",
            movie_title_year="2019",
            movie_title_and_year_search="1917 2019",
            index_title_compare="19172019",
        )
        out = _search_google(result, Config())
        assert out["result"] == "Passed"
        assert out["imdb_id"] == "tt8579674"


# ---------------------------------------------------------------------------
# search_for_imdb_id (orchestrator)
# ---------------------------------------------------------------------------


class TestSearchForImdbId:
    """Tests for the top-level search_for_imdb_id() orchestrator."""

    def test_returns_on_first_successful_strategy(self, mocker: MockerFixture) -> None:
        mocker.patch(
            "movarr.imdb_search._search_imdbpie",
            side_effect=lambda r, c: {**r, "result": "Passed", "imdb_id": "tt0133093"},
        )
        spy_tmdb = mocker.patch("movarr.imdb_search._search_tmdb")
        spy_omdb = mocker.patch("movarr.imdb_search._search_omdb")
        spy_google = mocker.patch("movarr.imdb_search._search_google")
        result = _make_result()
        cfg = Config()
        out = search_for_imdb_id(result, cfg)
        assert out["result"] == "Passed"
        assert out["imdb_id"] == "tt0133093"
        spy_tmdb.assert_not_called()
        spy_omdb.assert_not_called()
        spy_google.assert_not_called()

    def test_tries_next_strategy_when_first_fails(self, mocker: MockerFixture) -> None:
        mocker.patch(
            "movarr.imdb_search._search_imdbpie",
            side_effect=lambda r, c: {**r, "result": "Failed"},
        )
        mocker.patch(
            "movarr.imdb_search._search_tmdb",
            side_effect=lambda r, c: {**r, "result": "Passed", "imdb_id": "tt0133093"},
        )
        spy_omdb = mocker.patch("movarr.imdb_search._search_omdb")
        result = _make_result()
        cfg = Config()
        out = search_for_imdb_id(result, cfg)
        assert out["result"] == "Passed"
        spy_omdb.assert_not_called()

    def test_returns_failed_when_all_strategies_exhausted(self, mocker: MockerFixture) -> None:
        for strategy in ("_search_imdbpie", "_search_tmdb", "_search_omdb", "_search_google"):
            mocker.patch(
                f"movarr.imdb_search.{strategy}",
                side_effect=lambda r, c: {**r, "result": "Failed"},
            )
        result = _make_result()
        cfg = Config()
        out = search_for_imdb_id(result, cfg)
        assert out["result"] == "Failed"

    def test_google_used_as_last_resort(self, mocker: MockerFixture) -> None:
        for strategy in ("_search_imdbpie", "_search_tmdb", "_search_omdb"):
            mocker.patch(
                f"movarr.imdb_search.{strategy}",
                side_effect=lambda r, c: {**r, "result": "Failed"},
            )
        mocker.patch(
            "movarr.imdb_search._search_google",
            side_effect=lambda r, c: {**r, "result": "Passed", "imdb_id": "tt0133093"},
        )
        result = _make_result()
        cfg = Config()
        out = search_for_imdb_id(result, cfg)
        assert out["result"] == "Passed"
        assert out["imdb_id"] == "tt0133093"


# ---------------------------------------------------------------------------
# _search_imdbpie — additional edge cases
# ---------------------------------------------------------------------------


class TestSearchImdbpieEdgeCases:
    """Additional edge cases for _search_imdbpie."""

    def _mock_imdbpie(self, mocker: MockerFixture, hits: list) -> None:
        mock_imdbpie = mocker.MagicMock()
        mock_client = mocker.MagicMock()
        mock_imdbpie.Imdb.return_value = mock_client
        mock_client.search_for_title.return_value = hits
        mocker.patch.dict("sys.modules", {"imdbpie": mock_imdbpie})

    def test_hit_with_no_title_is_skipped(self, mocker: MockerFixture) -> None:
        """Hits without a 'title' key are skipped and the result fails."""
        self._mock_imdbpie(mocker, [{"year": "1999", "imdb_id": "tt0133093"}])
        out = _search_imdbpie(_make_result(), Config())
        assert out["result"] == "Failed"

    def test_hit_with_none_year_is_skipped(self, mocker: MockerFixture) -> None:
        """Hits with year=None are skipped and the result fails."""
        self._mock_imdbpie(mocker, [{"title": "The Matrix", "year": None, "imdb_id": "tt0133093"}])
        out = _search_imdbpie(_make_result(), Config())
        assert out["result"] == "Failed"

    def test_hit_with_unparseable_year_is_skipped(self, mocker: MockerFixture) -> None:
        """Hits where int(year) raises ValueError are skipped."""
        self._mock_imdbpie(mocker, [{"title": "The Matrix", "year": "n/a", "imdb_id": "tt0133093"}])
        out = _search_imdbpie(_make_result(), Config())
        assert out["result"] == "Failed"


# ---------------------------------------------------------------------------
# _search_tmdb — additional edge cases
# ---------------------------------------------------------------------------


class TestSearchTmdbEdgeCases:
    """Additional edge cases for _search_tmdb."""

    def _cfg(self) -> Config:
        cfg = Config()
        cfg.credentials.tmdb.api_key = "test_key"
        return cfg

    def _mock_http(self, mocker: MockerFixture, search_hits: list) -> Any:
        mock_http = mocker.MagicMock()
        mocker.patch("movarr.imdb_search.HttpClient", return_value=mock_http)
        search_resp = mocker.MagicMock()
        search_resp.content = json.dumps({"results": search_hits})
        mock_http.get.return_value = search_resp
        return mock_http

    def test_invalid_release_date_format_skips_hit(self, mocker: MockerFixture) -> None:
        """Hit with malformed release_date is skipped via ValueError."""
        self._mock_http(mocker, [{"title": "The Matrix", "release_date": "not-a-date", "id": 603}])
        out = _search_tmdb(_make_result(), self._cfg())
        assert out["result"] == "Failed"

    def test_none_tmdb_id_skips_hit(self, mocker: MockerFixture) -> None:
        """Hit where 'id' is absent/None is skipped."""
        self._mock_http(mocker, [{"title": "The Matrix", "release_date": "1999-03-31"}])
        out = _search_tmdb(_make_result(), self._cfg())
        assert out["result"] == "Failed"

    def test_detail_request_failure_sets_failed(self, mocker: MockerFixture) -> None:
        """When the second (detail) HTTP request fails, result is Failed."""
        from movarr.downloader import HttpError

        mock_http = mocker.MagicMock()
        mocker.patch("movarr.imdb_search.HttpClient", return_value=mock_http)
        search_resp = mocker.MagicMock()
        search_resp.content = json.dumps(
            {"results": [{"title": "The Matrix", "release_date": "1999-03-31", "id": 603}]}
        )
        mock_http.get.side_effect = [search_resp, HttpError("detail failed")]
        out = _search_tmdb(_make_result(), self._cfg())
        assert out["result"] == "Failed"


# ---------------------------------------------------------------------------
# _search_omdb — unparseable year edge case
# ---------------------------------------------------------------------------


class TestSearchOmdbEdgeCases:
    """Additional edge cases for _search_omdb."""

    def test_unparseable_year_sets_failed(self, mocker: MockerFixture) -> None:
        """OMDb response with Year containing no digits hits the ValueError except."""
        cfg = Config()
        cfg.credentials.omdb.api_key = "test_key"
        mock_http = mocker.MagicMock()
        mocker.patch("movarr.imdb_search.HttpClient", return_value=mock_http)
        resp = mocker.MagicMock()
        # Year stripped of non-digits becomes "" → int("") raises ValueError
        resp.content = json.dumps({"Title": "The Matrix", "Year": "N/A", "imdbID": "tt0133093"})
        mock_http.get.return_value = resp
        out = _search_omdb(_make_result(), cfg)
        assert out["result"] == "Failed"

    def test_no_title_in_response_message_shows_search_query(self, mocker: MockerFixture) -> None:
        """When OMDb returns no Title the failure message names the search query, not the slug."""
        cfg = Config()
        cfg.credentials.omdb.api_key = "test_key"
        mock_http = mocker.MagicMock()
        mocker.patch("movarr.imdb_search.HttpClient", return_value=mock_http)
        resp = mocker.MagicMock()
        resp.content = json.dumps({"Response": "False", "Error": "Movie not found!"})
        mock_http.get.return_value = resp
        out = _search_omdb(_make_result(), cfg)
        assert out["result"] == "Failed"
        assert any("The Matrix" in d and "1999" in d for d in out["result_details"])
        assert not any("index_title_compare" in d or "thematrix" in d for d in out["result_details"])

    def test_no_title_in_response_message_shows_omdb_error(self, mocker: MockerFixture) -> None:
        """When OMDb returns an Error field, the failure message includes it."""
        cfg = Config()
        cfg.credentials.omdb.api_key = "test_key"
        mock_http = mocker.MagicMock()
        mocker.patch("movarr.imdb_search.HttpClient", return_value=mock_http)
        resp = mocker.MagicMock()
        resp.content = json.dumps({"Response": "False", "Error": "Invalid API key!"})
        mock_http.get.return_value = resp
        out = _search_omdb(_make_result(), cfg)
        assert out["result"] == "Failed"
        details = " ".join(out["result_details"])
        assert "Invalid API key!" in details
        assert "The Matrix" in details
        assert "1999" in details

    def test_no_title_no_error_field_shows_no_result_message(self, mocker: MockerFixture) -> None:
        """When OMDb returns no Title and no Error field, the message says 'no result'."""
        cfg = Config()
        cfg.credentials.omdb.api_key = "test_key"
        mock_http = mocker.MagicMock()
        mocker.patch("movarr.imdb_search.HttpClient", return_value=mock_http)
        resp = mocker.MagicMock()
        resp.content = json.dumps({})
        mock_http.get.return_value = resp
        out = _search_omdb(_make_result(), cfg)
        assert out["result"] == "Failed"
        details = " ".join(out["result_details"])
        assert "no result" in details
        assert "The Matrix" in details
        assert "1999" in details

    def test_movie_not_found_error_shows_no_result_message(self, mocker: MockerFixture) -> None:
        """OMDb 'Movie not found!' is treated as no result, not as an API error."""
        cfg = Config()
        cfg.credentials.omdb.api_key = "test_key"
        mock_http = mocker.MagicMock()
        mocker.patch("movarr.imdb_search.HttpClient", return_value=mock_http)
        resp = mocker.MagicMock()
        resp.content = json.dumps({"Response": "False", "Error": _OMDB_NOT_FOUND_ERROR})
        mock_http.get.return_value = resp
        out = _search_omdb(_make_result(), cfg)
        assert out["result"] == "Failed"
        details = " ".join(out["result_details"])
        assert "no result" in details
        assert "API error" not in details

    def test_title_mismatch_message_shows_both_titles_not_slug(self, mocker: MockerFixture) -> None:
        """When OMDb returns a non-matching title the message shows both titles, not the slug."""
        cfg = Config()
        cfg.credentials.omdb.api_key = "test_key"
        mock_http = mocker.MagicMock()
        mocker.patch("movarr.imdb_search.HttpClient", return_value=mock_http)
        resp = mocker.MagicMock()
        resp.content = json.dumps({"Title": "Something Else", "Year": "1999", "imdbID": "tt0099999"})
        mock_http.get.return_value = resp
        out = _search_omdb(_make_result(), cfg)
        assert out["result"] == "Failed"
        details = " ".join(out["result_details"])
        assert "Something Else" in details
        assert "The Matrix" in details


# ---------------------------------------------------------------------------
# _search_google — sanitise-returns-None and title-mismatch edge cases
# ---------------------------------------------------------------------------


class TestSearchGoogleEdgeCases:
    """Additional edge cases for _search_google."""

    def test_empty_title_cannot_be_sanitised_sets_failed(self, mocker: MockerFixture) -> None:
        """Google hit with an empty title results in sanitise returning falsy."""
        mock_gs = mocker.MagicMock()
        mock_hit = mocker.MagicMock()
        mock_hit.title = ""
        mock_hit.url = "https://www.imdb.com/title/tt0133093/"
        mock_gs.search.return_value = iter([mock_hit])
        mocker.patch.dict("sys.modules", {"googlesearch": mock_gs})
        out = _search_google(_make_result(), Config())
        assert out["result"] == "Failed"

    def test_title_not_in_index_compare_sets_failed(self, mocker: MockerFixture) -> None:
        """Google hit whose normalised title is not in index_title_compare fails."""
        mock_gs = mocker.MagicMock()
        mock_hit = mocker.MagicMock()
        mock_hit.title = "Something Completely Different 1999"
        mock_hit.url = "https://www.imdb.com/title/tt0133093/"
        mock_gs.search.return_value = iter([mock_hit])
        mocker.patch.dict("sys.modules", {"googlesearch": mock_gs})
        # index_title_compare does NOT contain "somethingcompletelydifferent"
        result = _make_result(index_title_compare="thematrix1999")
        out = _search_google(result, Config())
        assert out["result"] == "Failed"
