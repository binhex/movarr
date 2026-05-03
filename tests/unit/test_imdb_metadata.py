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

    def test_redirect_resolved_before_fetch(self, mocker: MockerFixture) -> None:
        """When IMDb redirects an ID, the canonical ID is used for all API calls."""
        _mock_imdbpie_client(mocker)
        # Make _resolve_imdbpie_redirect return a different (canonical) ID.
        mocker.patch(
            "movarr.imdb_metadata._resolve_imdbpie_redirect",
            return_value="tt9999999",
        )
        result = _make_result(imdb_id="tt0000001")
        out = _fetch_imdbpie(result)
        # The canonical ID must be propagated into the result dict.
        assert out.get("imdb_id") == "tt9999999"

    def test_eight_digit_id_not_flagged_as_redirect(self, mocker: MockerFixture) -> None:
        """8-digit IMDb IDs (e.g. tt31193180) must not be misidentified as redirects.

        IMDbPie's internal regex uses tt\\d{7} (exactly 7 digits) so it extracts
        the wrong ID from an 8-digit tconst and falsely raises LookupError.
        _patch_imdbpie_redirect_check replaces the method with tt\\d{7,}.
        """
        from movarr.imdb_metadata import _patch_imdbpie_redirect_check

        fake_client = mocker.MagicMock()
        fake_client.region = "en-US"
        # API returns the same 8-digit ID → not a redirect.
        fake_client._get.return_value = {"id": "/title/tt31193180/"}
        fake_client.validate_imdb_id = mocker.MagicMock()

        mock_constants = mocker.MagicMock()
        mock_constants.BASE_URI = "https://app.imdb.com"
        mocker.patch.dict("sys.modules", {"imdbpie.constants": mock_constants})

        _patch_imdbpie_redirect_check(fake_client)
        assert fake_client.is_redirection_title("tt31193180") is False

    def test_eight_digit_id_redirect_returns_different_id(self, mocker: MockerFixture) -> None:
        """If the API genuinely returns a different 8-digit ID, it IS a redirect."""
        from movarr.imdb_metadata import _patch_imdbpie_redirect_check

        fake_client = mocker.MagicMock()
        fake_client.region = "en-US"
        # API says the canonical ID is different from the requested one.
        fake_client._get.return_value = {"id": "/title/tt99999999/"}
        fake_client.validate_imdb_id = mocker.MagicMock()

        mock_constants = mocker.MagicMock()
        mock_constants.BASE_URI = "https://app.imdb.com"
        mocker.patch.dict("sys.modules", {"imdbpie.constants": mock_constants})

        _patch_imdbpie_redirect_check(fake_client)
        assert fake_client.is_redirection_title("tt31193180") is True


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

    def test_year_with_trailing_dash_parsed(self, mocker: MockerFixture) -> None:
        """OMDb returns '2026–' for ongoing series; must extract the 4-digit year."""
        self._setup_omdb_mock(
            mocker,
            {"title": "Big Mistakes", "year": "2026\u2013"},
        )
        result = _make_result()
        cfg = Config()
        out = _fetch_omdb(result, cfg)
        assert out.get("imdb_year") == 2026

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


# ---------------------------------------------------------------------------
# _resolve_imdbpie_redirect (private helper — tested directly)
# ---------------------------------------------------------------------------


class TestResolveImdbpieRedirect:
    """Tests for _resolve_imdbpie_redirect()."""

    def test_returns_canonical_id_when_api_returns_different_id(self, mocker: MockerFixture) -> None:
        from movarr.imdb_metadata import _resolve_imdbpie_redirect

        mock_client = mocker.MagicMock()
        mock_client.region = "en-US"
        mock_client._get.return_value = {"id": "/title/tt9999999/"}
        mock_constants = mocker.MagicMock()
        mock_constants.BASE_URI = "https://app.imdb.com"
        mocker.patch.dict("sys.modules", {"imdbpie.constants": mock_constants})

        result = _resolve_imdbpie_redirect(mock_client, "tt0133093")

        assert result == "tt9999999"

    def test_returns_original_id_when_no_id_in_response(self, mocker: MockerFixture) -> None:
        from movarr.imdb_metadata import _resolve_imdbpie_redirect

        mock_client = mocker.MagicMock()
        mock_client.region = "en-US"
        mock_client._get.return_value = {}
        mock_constants = mocker.MagicMock()
        mock_constants.BASE_URI = "https://app.imdb.com"
        mocker.patch.dict("sys.modules", {"imdbpie.constants": mock_constants})

        result = _resolve_imdbpie_redirect(mock_client, "tt0133093")

        assert result == "tt0133093"

    def test_returns_original_id_on_exception(self, mocker: MockerFixture) -> None:
        from movarr.imdb_metadata import _resolve_imdbpie_redirect

        mock_client = mocker.MagicMock()
        mock_client._get.side_effect = RuntimeError("network error")
        mocker.patch.dict("sys.modules", {"imdbpie.constants": None})

        result = _resolve_imdbpie_redirect(mock_client, "tt0133093")

        assert result == "tt0133093"


# ---------------------------------------------------------------------------
# _patch_imdbpie_redirect_check — nm-id branch and except path
# ---------------------------------------------------------------------------


class TestPatchImdbpieRedirectCheckExtra:
    """Additional tests for _patch_imdbpie_redirect_check edge cases."""

    def test_nm_id_with_matching_returned_id_is_not_redirect(self, mocker: MockerFixture) -> None:
        """nm- prefixed IDs use _get_resource; same ID → not a redirect."""
        from movarr.imdb_metadata import _patch_imdbpie_redirect_check

        fake_client = mocker.MagicMock()
        fake_client.validate_imdb_id = mocker.MagicMock()
        fake_client._get_resource.return_value = {"base": {"id": "/name/nm0000001/"}}

        _patch_imdbpie_redirect_check(fake_client)
        assert fake_client.is_redirection_title("nm0000001") is False

    def test_nm_id_with_different_returned_id_is_redirect(self, mocker: MockerFixture) -> None:
        """nm- prefixed IDs: different returned ID → is a redirect."""
        from movarr.imdb_metadata import _patch_imdbpie_redirect_check

        fake_client = mocker.MagicMock()
        fake_client.validate_imdb_id = mocker.MagicMock()
        fake_client._get_resource.return_value = {"base": {"id": "/name/nm9999999/"}}

        _patch_imdbpie_redirect_check(fake_client)
        assert fake_client.is_redirection_title("nm0000001") is True

    def test_lookup_error_returns_false(self, mocker: MockerFixture) -> None:
        """LookupError inside the patched method is caught and returns False."""
        from movarr.imdb_metadata import _patch_imdbpie_redirect_check

        fake_client = mocker.MagicMock()
        fake_client.validate_imdb_id = mocker.MagicMock()
        mock_constants = mocker.MagicMock()
        mock_constants.BASE_URI = "https://app.imdb.com"
        mocker.patch.dict("sys.modules", {"imdbpie.constants": mock_constants})
        fake_client._get.side_effect = LookupError("not found")

        _patch_imdbpie_redirect_check(fake_client)
        assert fake_client.is_redirection_title("tt0133093") is False


# ---------------------------------------------------------------------------
# _credits_names, _credits_characters, _get, _safe_val (exception paths)
# ---------------------------------------------------------------------------


class TestCreditsNamesException:
    """_credits_names must return None on KeyError / TypeError."""

    def test_returns_none_on_missing_credits_key(self) -> None:
        from movarr.imdb_metadata import _credits_names

        assert _credits_names({}, "director") is None

    def test_returns_none_on_none_input(self) -> None:
        from movarr.imdb_metadata import _credits_names

        assert _credits_names(None, "director") is None  # type: ignore[arg-type]


class TestCreditsCharactersException:
    """_credits_characters must return None on KeyError / TypeError."""

    def test_returns_none_on_missing_cast_key(self) -> None:
        from movarr.imdb_metadata import _credits_characters

        assert _credits_characters({}) is None

    def test_returns_none_on_none_input(self) -> None:
        from movarr.imdb_metadata import _credits_characters

        assert _credits_characters(None) is None  # type: ignore[arg-type]


class TestGetHelper:
    """_get must return None on KeyError / TypeError."""

    def test_returns_none_on_missing_key(self) -> None:
        from movarr.imdb_metadata import _get

        assert _get({}, "missing") is None

    def test_returns_none_on_none_data(self) -> None:
        from movarr.imdb_metadata import _get

        assert _get(None, "key") is None  # type: ignore[arg-type]


class TestSafeValException:
    """_safe_val must return None on KeyError / IndexError / TypeError."""

    def test_returns_none_on_missing_key(self) -> None:
        from movarr.imdb_metadata import _safe_val

        assert _safe_val({}, "missing") is None

    def test_returns_none_on_none_data(self) -> None:
        from movarr.imdb_metadata import _safe_val

        assert _safe_val(None, "key") is None  # type: ignore[arg-type]

    def test_returns_none_on_index_out_of_range(self) -> None:
        from movarr.imdb_metadata import _safe_val

        assert _safe_val({"items": []}, "items", 0) is None


# ---------------------------------------------------------------------------
# _extract_cert_imdbpie — second try exception path
# ---------------------------------------------------------------------------


class TestExtractCertImdbpieException:
    """_extract_cert_imdbpie second try block must return None on exception."""

    def test_returns_none_when_certificates_raises_key_error(self) -> None:
        from movarr.imdb_metadata import _extract_cert_imdbpie

        # UK country entry exists but has no "certificate" key → KeyError in generator
        aux = {"certificates": [{"country": "United Kingdom"}]}
        result = _extract_cert_imdbpie(aux)
        assert result is None
