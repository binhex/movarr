"""Unit tests for movarr.prowlarr — Prowlarr JSON REST API client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from movarr.config import Config
from movarr.downloader import HttpError
from movarr.prowlarr import ProwlarrClient
from movarr.utils import bytes_to_mb

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def _make_client(mocker: MockerFixture) -> tuple[ProwlarrClient, Any]:
    """Return a ProwlarrClient with a mocked HttpClient."""
    mock_http_cls = mocker.patch("movarr.prowlarr.HttpClient")
    cfg = Config()
    client = ProwlarrClient(cfg)
    return client, mock_http_cls.return_value


class TestToMb:
    """Tests for bytes_to_mb shared helper (was ProwlarrClient._to_mb)."""

    def test_converts_bytes_to_mb(self) -> None:
        assert bytes_to_mb(8_589_934_592) == "8589"

    def test_truncates_remainder(self) -> None:
        assert bytes_to_mb(1_000_001) == "1"

    def test_zero_returns_zero(self) -> None:
        assert bytes_to_mb(0) == "0"

    def test_non_numeric_returns_zero(self) -> None:
        assert bytes_to_mb("bad") == "0"


class TestResolveIndexerId:
    """Tests for ProwlarrClient._resolve_indexer_id."""

    def test_all_returns_none(self, mocker: MockerFixture) -> None:
        """'all' returns None, meaning omit indexerIds from the request."""
        client, _ = _make_client(mocker)
        assert client._resolve_indexer_id("all") is None

    def test_numeric_string_returns_int(self, mocker: MockerFixture) -> None:
        client, _ = _make_client(mocker)
        assert client._resolve_indexer_id("7") == 7

    def test_non_numeric_returns_false_and_warns(self, mocker: MockerFixture) -> None:
        client, _ = _make_client(mocker)
        mock_warn = mocker.patch("movarr.prowlarr._logger.warning")
        result = client._resolve_indexer_id("notanumber")
        assert result is False
        mock_warn.assert_called_once()


class TestParseResult:
    """Tests for ProwlarrClient._parse_result."""

    def _item(self, **kwargs: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "title": "Movie 2023 1080p BluRay",
            "indexer": "MyTracker",
            "publishDate": "2023-01-01",
            "infoUrl": "http://example.com/info",
            "seeders": 42,
            "leechers": 10,
            "size": 8_589_934_592,
            "downloadUrl": "http://example.com/dl.torrent",
            "magnetUrl": "magnet:?xt=urn:btih:abc123",
        }
        base.update(kwargs)
        return base

    def test_returns_result_dict_with_all_fields(self, mocker: MockerFixture) -> None:
        client, _ = _make_client(mocker)
        result = client._parse_result(self._item())
        assert result is not None
        assert result["index_title"] == "Movie 2023 1080p BluRay"
        assert result["index_tracker"] == "MyTracker"
        assert result["index_seeders"] == "42"
        assert result["index_peers"] == "10"
        assert result["index_size"] == "8589934592"
        assert result["index_size_mb"] == "8589"
        assert result["torrent_url"] == "http://example.com/dl.torrent"
        assert result["magnet_url"] == "magnet:?xt=urn:btih:abc123"
        assert result["result"] == "Passed"

    def test_returns_none_for_missing_title(self, mocker: MockerFixture) -> None:
        client, _ = _make_client(mocker)
        assert client._parse_result({"indexer": "x"}) is None

    def test_imdb_id_formatted_with_tt_prefix(self, mocker: MockerFixture) -> None:
        """Prowlarr imdbId integer 113627 → 'tt0113627'."""
        client, _ = _make_client(mocker)
        result = client._parse_result(self._item(imdbId=113627))
        assert result is not None
        assert result.get("imdb_id") == "tt0113627"

    def test_imdb_id_zero_padded_to_seven_digits(self, mocker: MockerFixture) -> None:
        """Prowlarr imdbId integer 7 → 'tt0000007'."""
        client, _ = _make_client(mocker)
        result = client._parse_result(self._item(imdbId=7))
        assert result is not None
        assert result.get("imdb_id") == "tt0000007"

    def test_imdb_id_absent_when_not_in_item(self, mocker: MockerFixture) -> None:
        """imdb_id key is not added when imdbId is absent."""
        client, _ = _make_client(mocker)
        result = client._parse_result(self._item())
        assert result is not None
        assert "imdb_id" not in result

    def test_imdb_id_silently_skipped_when_unconvertible(self, mocker: MockerFixture) -> None:
        """If imdbId cannot be cast to int, the field is silently skipped."""
        client, _ = _make_client(mocker)
        result = client._parse_result(self._item(imdbId="not-a-number"))
        assert result is not None
        assert "imdb_id" not in result

    def test_missing_optional_fields_default_to_empty(self, mocker: MockerFixture) -> None:
        """Optional fields that are absent or null default to '' or '0'."""
        client, _ = _make_client(mocker)
        result = client._parse_result({"title": "Movie 2023 1080p"})
        assert result is not None
        assert result["index_tracker"] == ""
        assert result["torrent_url"] == ""
        assert result["magnet_url"] == ""
        assert result["index_size"] == "0"
        assert result["index_size_mb"] == "0"


class TestSearch:
    """Tests for ProwlarrClient.search."""

    def test_yields_results_on_success(self, mocker: MockerFixture) -> None:
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = [
            {"title": "Movie 2023 1080p", "seeders": 5, "size": 1_000_000_000},
        ]
        mock_http.get.return_value = mock_resp
        results = list(client.search("all", "1080p", "2000"))
        assert len(results) == 1
        assert results[0]["index_title"] == "Movie 2023 1080p"

    def test_empty_list_yields_nothing(self, mocker: MockerFixture) -> None:
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = []
        mock_http.get.return_value = mock_resp
        assert list(client.search("all", "1080p", "2000")) == []

    def test_http_error_yields_nothing_and_warns(self, mocker: MockerFixture) -> None:
        client, mock_http = _make_client(mocker)
        mock_http.get.side_effect = HttpError("503 Service Unavailable")
        mock_warn = mocker.patch("movarr.prowlarr._logger.warning")
        assert list(client.search("all", "1080p", "2000")) == []
        mock_warn.assert_called()

    def test_json_parse_error_yields_nothing_and_warns(self, mocker: MockerFixture) -> None:
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.json.side_effect = ValueError("not JSON")
        mock_http.get.return_value = mock_resp
        mock_warn = mocker.patch("movarr.prowlarr._logger.warning")
        assert list(client.search("all", "1080p", "2000")) == []
        mock_warn.assert_called()

    def test_non_numeric_indexer_yields_nothing_and_warns(self, mocker: MockerFixture) -> None:
        client, _ = _make_client(mocker)
        mock_warn = mocker.patch("movarr.prowlarr._logger.warning")
        assert list(client.search("not-a-number", "1080p", "2000")) == []
        mock_warn.assert_called()

    def test_usenet_results_are_filtered_out(self, mocker: MockerFixture) -> None:
        """Protocol 'usenet' results must not enter the torrent pipeline."""
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = [
            {"title": "Movie 2023 1080p", "protocol": "usenet", "size": 1_000_000_000},
            {"title": "Movie 2023 1080p", "protocol": "torrent", "size": 1_000_000_000},
        ]
        mock_http.get.return_value = mock_resp
        results = list(client.search("all", "1080p", "2000"))
        assert len(results) == 1
        assert results[0]["index_title"] == "Movie 2023 1080p"

    def test_non_list_response_yields_nothing_and_warns(self, mocker: MockerFixture) -> None:
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = {"error": "bad"}
        mock_http.get.return_value = mock_resp
        mock_warn = mocker.patch("movarr.prowlarr._logger.warning")
        assert list(client.search("all", "1080p", "2000")) == []
        mock_warn.assert_called()

    def test_url_omits_indexer_ids_for_all(self, mocker: MockerFixture) -> None:
        """When index_site='all', indexerIds must NOT appear in the URL."""
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = []
        mock_http.get.return_value = mock_resp
        list(client.search("all", "1080p", "2000"))
        url = mock_http.get.call_args[0][0]
        assert "indexerIds" not in url

    def test_api_key_not_in_url(self, mocker: MockerFixture) -> None:
        """API key must be sent as a header, not embedded in the URL."""
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = []
        mock_http.get.return_value = mock_resp
        list(client.search("all", "1080p", "2000"))
        url = mock_http.get.call_args[0][0]
        assert "apiKey" not in url
        headers = mock_http.get.call_args.kwargs.get("headers", {})
        assert "X-Api-Key" in headers

    def test_url_uses_numeric_id_for_specific_indexer(self, mocker: MockerFixture) -> None:
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = []
        mock_http.get.return_value = mock_resp
        list(client.search("7", "1080p", "2000"))
        url = mock_http.get.call_args[0][0]
        assert "indexerIds=7" in url

    def test_skips_items_with_no_title(self, mocker: MockerFixture) -> None:
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = [{"indexer": "x", "size": 1000}]
        mock_http.get.return_value = mock_resp
        assert list(client.search("all", "1080p", "2000")) == []

    def test_generic_exception_yields_nothing_and_warns(self, mocker: MockerFixture) -> None:
        client, mock_http = _make_client(mocker)
        mock_http.get.side_effect = OSError("connection reset")
        mock_warn = mocker.patch("movarr.prowlarr._logger.warning")
        assert list(client.search("all", "1080p", "2000")) == []
        mock_warn.assert_called()


class TestIsReachable:
    """Tests for ProwlarrClient.is_reachable."""

    def test_returns_true_on_successful_get(self, mocker: MockerFixture) -> None:
        client, mock_http = _make_client(mocker)
        mock_http.get.return_value = mocker.MagicMock()
        assert client.is_reachable() is True

    def test_returns_false_on_http_error(self, mocker: MockerFixture) -> None:
        client, mock_http = _make_client(mocker)
        mock_http.get.side_effect = HttpError("connection refused")
        assert client.is_reachable() is False

    def test_returns_false_on_generic_exception(self, mocker: MockerFixture) -> None:
        client, mock_http = _make_client(mocker)
        mock_http.get.side_effect = OSError("network unreachable")
        assert client.is_reachable() is False
