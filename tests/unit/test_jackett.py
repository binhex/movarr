"""Unit tests for movarr.jackett — Torznab feed client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from movarr.config import Config
from movarr.downloader import HttpError
from movarr.jackett import JackettClient

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

# Torznab namespace key used by xmltodict with process_namespaces=True
_NS = "http://torznab.com/schemas/2015/feed"
_NS_ATTR = f"{_NS}:attr"

# ---------------------------------------------------------------------------
# Sample XML fixtures
# ---------------------------------------------------------------------------

_SINGLE_ITEM_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <item>
      <title>Movie 2023 1080p BluRay</title>
      <link>http://example.com/dl.torrent</link>
      <torznab:attr name="size" value="8589934592"/>
      <torznab:attr name="seeders" value="42"/>
      <torznab:attr name="magneturl" value="magnet:?xt=urn:btih:abc123"/>
    </item>
  </channel>
</rss>"""

_MULTI_ITEM_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <item>
      <title>Movie 2023 1080p BluRay</title>
      <link>http://example.com/dl1.torrent</link>
      <torznab:attr name="size" value="8589934592"/>
      <torznab:attr name="seeders" value="42"/>
      <torznab:attr name="magneturl" value="magnet:?xt=urn:btih:abc123"/>
    </item>
    <item>
      <title>AnotherMovie 2022 1080p</title>
      <link>http://example.com/dl2.torrent</link>
      <torznab:attr name="size" value="4294967296"/>
      <torznab:attr name="seeders" value="10"/>
      <torznab:attr name="magneturl" value="magnet:?xt=urn:btih:def456"/>
    </item>
  </channel>
</rss>"""

_IMDB_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <item>
      <title>Inception 2010 1080p</title>
      <link>http://example.com/inception.torrent</link>
      <torznab:attr name="size" value="5368709120"/>
      <torznab:attr name="seeders" value="100"/>
      <torznab:attr name="magneturl" value="magnet:?xt=urn:btih:xyz789"/>
      <torznab:attr name="imdbid" value="tt1375666"/>
    </item>
  </channel>
</rss>"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(mocker: MockerFixture) -> tuple[JackettClient, Any]:
    """Return a JackettClient with a mocked HttpClient."""
    mock_http_cls = mocker.patch("movarr.jackett.HttpClient")
    cfg = Config()
    client = JackettClient(cfg)
    return client, mock_http_cls.return_value


def _make_item(title: str, attrs: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Build a minimal Torznab item dict as xmltodict would produce."""
    item: dict[str, Any] = {"title": title, "link": "http://example.com/dl.torrent"}
    if attrs is not None:
        item[_NS_ATTR] = attrs
    return item


# ---------------------------------------------------------------------------
# _attr (static method)
# ---------------------------------------------------------------------------


class TestAttr:
    """Tests for JackettClient._attr static helper."""

    def test_returns_value_for_matching_name(self) -> None:
        """Extracts the value for a known attribute name."""
        item = _make_item("Movie", [{"@name": "seeders", "@value": "42"}, {"@name": "size", "@value": "999"}])
        assert JackettClient._attr(item, "seeders") == "42"
        assert JackettClient._attr(item, "size") == "999"

    def test_returns_empty_string_for_missing_name(self) -> None:
        """Returns '' when the attribute name is not present."""
        item = _make_item("Movie", [{"@name": "seeders", "@value": "42"}])
        assert JackettClient._attr(item, "peers") == ""

    def test_handles_single_dict_attr(self) -> None:
        """xmltodict returns a dict (not list) for a single attr element."""
        item = _make_item("Movie")
        item[_NS_ATTR] = {"@name": "seeders", "@value": "7"}
        assert JackettClient._attr(item, "seeders") == "7"

    def test_returns_empty_when_no_attrs_key(self) -> None:
        """Returns '' when the torznab:attr key is entirely absent."""
        item = {"title": "Movie"}
        assert JackettClient._attr(item, "seeders") == ""


# ---------------------------------------------------------------------------
# _to_mb (static method)
# ---------------------------------------------------------------------------


class TestToMb:
    """Tests for JackettClient._to_mb static helper."""

    def test_converts_bytes_to_mb(self) -> None:
        """8 GiB in bytes converts to 8192 MiB."""
        assert JackettClient._to_mb("8589934592") == "8192"

    def test_truncates_remainder(self) -> None:
        """Conversion truncates (integer division), does not round."""
        assert JackettClient._to_mb("1048577") == "1"  # just over 1 MiB

    def test_zero_bytes(self) -> None:
        """Zero bytes converts to '0'."""
        assert JackettClient._to_mb("0") == "0"

    def test_invalid_string_returns_zero(self) -> None:
        """Non-numeric input returns '0'."""
        assert JackettClient._to_mb("not-a-number") == "0"

    def test_empty_string_returns_zero(self) -> None:
        """Empty string returns '0'."""
        assert JackettClient._to_mb("") == "0"

    def test_none_returns_zero(self) -> None:
        """None input returns '0'."""
        assert JackettClient._to_mb(None) == "0"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _parse_item
# ---------------------------------------------------------------------------


class TestParseItem:
    """Tests for JackettClient._parse_item."""

    def test_returns_result_dict_with_correct_fields(self, mocker: MockerFixture) -> None:
        """parse_item extracts all expected ResultDict fields."""
        client, _ = _make_client(mocker)
        item = _make_item(
            "Movie 2023 1080p BluRay",
            [
                {"@name": "size", "@value": "8589934592"},
                {"@name": "seeders", "@value": "42"},
                {"@name": "magneturl", "@value": "magnet:?xt=urn:btih:abc123"},
            ],
        )
        result = client._parse_item(item)

        assert result is not None
        assert result["index_title"] == "Movie 2023 1080p BluRay"
        assert result["index_seeders"] == "42"
        assert result["index_size_mb"] == "8192"
        assert result["magnet_url"] == "magnet:?xt=urn:btih:abc123"
        assert result["result"] == "Passed"

    def test_returns_none_for_missing_title(self, mocker: MockerFixture) -> None:
        """Returns None when item has no title."""
        client, _ = _make_client(mocker)
        item: dict[str, Any] = {"link": "http://example.com/dl.torrent"}
        assert client._parse_item(item) is None

    def test_includes_imdb_id_when_present(self, mocker: MockerFixture) -> None:
        """imdb_id is added to result when torznab attr imdbid is present."""
        client, _ = _make_client(mocker)
        item = _make_item(
            "Inception 2010 1080p",
            [
                {"@name": "size", "@value": "5368709120"},
                {"@name": "seeders", "@value": "100"},
                {"@name": "imdbid", "@value": "tt1375666"},
            ],
        )
        result = client._parse_item(item)

        assert result is not None
        assert result.get("imdb_id") == "tt1375666"

    def test_imdb_id_absent_when_not_in_attrs(self, mocker: MockerFixture) -> None:
        """imdb_id key is not added when the imdbid attr is missing."""
        client, _ = _make_client(mocker)
        item = _make_item("Movie", [{"@name": "seeders", "@value": "5"}])
        result = client._parse_item(item)

        assert result is not None
        assert "imdb_id" not in result


# ---------------------------------------------------------------------------
# _fetch_page
# ---------------------------------------------------------------------------


class TestFetchPage:
    """Tests for JackettClient._fetch_page."""

    def test_parses_valid_xml_response(self, mocker: MockerFixture) -> None:
        """Returns a list of item dicts for a valid Torznab XML response."""
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.content = _SINGLE_ITEM_XML
        mock_http.get.return_value = mock_resp

        items = client._fetch_page("http://example.com", "test-indexer")

        assert items is not None
        assert isinstance(items, list)
        assert len(items) == 1
        assert items[0]["title"] == "Movie 2023 1080p BluRay"

    def test_single_item_xml_wraps_to_list(self, mocker: MockerFixture) -> None:
        """A single-item feed (dict from xmltodict) is wrapped into a list."""
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.content = _SINGLE_ITEM_XML
        mock_http.get.return_value = mock_resp

        items = client._fetch_page("http://example.com", "test-indexer")

        # xmltodict returns a dict for single items; _fetch_page must return a list
        assert isinstance(items, list)

    def test_multi_item_xml_returns_list(self, mocker: MockerFixture) -> None:
        """A multi-item feed returns a list with all items."""
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.content = _MULTI_ITEM_XML
        mock_http.get.return_value = mock_resp

        items = client._fetch_page("http://example.com", "test-indexer")

        assert items is not None
        assert len(items) == 2

    def test_returns_none_on_http_error(self, mocker: MockerFixture) -> None:
        """Returns None when the HTTP call raises HttpError."""
        client, mock_http = _make_client(mocker)
        mock_http.get.side_effect = HttpError("404 Not Found")

        assert client._fetch_page("http://example.com", "test-indexer") is None

    def test_returns_none_on_generic_exception(self, mocker: MockerFixture) -> None:
        """Returns None for any unexpected exception during the request."""
        client, mock_http = _make_client(mocker)
        mock_http.get.side_effect = ConnectionError("refused")

        assert client._fetch_page("http://example.com", "test-indexer") is None

    def test_returns_none_on_invalid_xml(self, mocker: MockerFixture) -> None:
        """Returns None when the response body cannot be parsed as Torznab."""
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.content = b"<not-a-valid-torznab/>"
        mock_http.get.return_value = mock_resp

        assert client._fetch_page("http://example.com", "test-indexer") is None


# ---------------------------------------------------------------------------
# search (public)
# ---------------------------------------------------------------------------


class TestSearch:
    """Tests for JackettClient.search."""

    def test_yields_results_from_single_page(self, mocker: MockerFixture) -> None:
        """Yields parsed ResultDicts from a single-page response."""
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.content = _SINGLE_ITEM_XML
        mock_http.get.return_value = mock_resp

        results = list(client.search("all", "1080p", "2000"))

        assert len(results) == 1
        assert results[0]["index_title"] == "Movie 2023 1080p BluRay"

    def test_stops_when_fetch_page_returns_none(self, mocker: MockerFixture) -> None:
        """Generator terminates when _fetch_page returns None (HTTP error)."""
        client, _ = _make_client(mocker)
        mocker.patch.object(client, "_fetch_page", return_value=None)

        assert list(client.search("all", "1080p", "2000")) == []

    def test_stops_on_empty_page(self, mocker: MockerFixture) -> None:
        """Generator terminates when _fetch_page returns an empty list."""
        client, _ = _make_client(mocker)
        # Override limit/offset so we enter the while loop twice
        client._cfg.offset = 1000

        fetch_mock = mocker.patch.object(
            client,
            "_fetch_page",
            side_effect=[
                [{"title": "Movie 1", _NS_ATTR: []}],
                [],  # empty second page → stop
            ],
        )

        results = list(client.search("all", "1080p", "2000"))

        assert len(results) == 1
        assert fetch_mock.call_count == 2

    def test_skips_items_with_no_title(self, mocker: MockerFixture) -> None:
        """Items whose title is missing are silently skipped."""
        client, _ = _make_client(mocker)
        mocker.patch.object(
            client,
            "_fetch_page",
            return_value=[{"link": "http://example.com/no-title.torrent"}],
        )

        assert list(client.search("all", "1080p", "2000")) == []

    def test_includes_imdb_id_in_yielded_results(self, mocker: MockerFixture) -> None:
        """imdb_id is propagated through search for items that carry it."""
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.content = _IMDB_XML
        mock_http.get.return_value = mock_resp

        results = list(client.search("all", "1080p", "2000"))

        assert len(results) == 1
        assert results[0].get("imdb_id") == "tt1375666"


# ---------------------------------------------------------------------------
# is_reachable (public)
# ---------------------------------------------------------------------------


class TestIsReachable:
    """Tests for JackettClient.is_reachable."""

    def test_returns_true_on_successful_get(self, mocker: MockerFixture) -> None:
        """Returns True when the health-check GET succeeds."""
        client, mock_http = _make_client(mocker)
        mock_http.get.return_value = mocker.MagicMock()

        assert client.is_reachable() is True

    def test_returns_false_on_http_error(self, mocker: MockerFixture) -> None:
        """Returns False when the GET raises HttpError."""
        client, mock_http = _make_client(mocker)
        mock_http.get.side_effect = HttpError("connection refused")

        assert client.is_reachable() is False

    def test_returns_false_on_generic_exception(self, mocker: MockerFixture) -> None:
        """Returns False for any unexpected exception during the health check."""
        client, mock_http = _make_client(mocker)
        mock_http.get.side_effect = OSError("network unreachable")

        assert client.is_reachable() is False
