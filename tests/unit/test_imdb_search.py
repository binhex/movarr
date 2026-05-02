"""Unit tests for movarr.imdb_search."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

    from movarr.models import ResultDict

from movarr.config import Config
from movarr.imdb_search import (
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

    def test_year_not_in_title_sets_failed(self, mocker: MockerFixture) -> None:
        mock_gs = mocker.MagicMock()
        mock_hit = mocker.MagicMock()
        mock_hit.title = "The Matrix IMDb"  # no year
        mock_hit.url = "https://www.imdb.com/title/tt0133093/"
        mock_gs.search.return_value = iter([mock_hit])
        mocker.patch.dict("sys.modules", {"googlesearch": mock_gs})
        result = _make_result(index_title_compare="thematrix")
        cfg = Config()
        out = _search_google(result, cfg)
        assert out["result"] == "Failed"

    def test_import_error_sets_failed(self, mocker: MockerFixture) -> None:
        mocker.patch.dict("sys.modules", {"googlesearch": None})
        result = _make_result()
        cfg = Config()
        out = _search_google(result, cfg)
        assert out["result"] == "Failed"


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
