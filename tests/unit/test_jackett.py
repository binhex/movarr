"""Unit tests for movarr.jackett — Torznab feed client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from movarr.config import Config
from movarr.downloader import HttpError
from movarr.jackett import JackettClient
from movarr.utils import bytes_to_mb

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

# Torznab namespace key used by xmltodict with process_namespaces=True
_NS = "http://torznab.com/schemas/2015/feed"
_NS_ATTR = f"{_NS}:attr"

# Sample XML fixtures

_SINGLE_ITEM_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <item>
      <title>Movie 2023 1080p BluRay</title>
      <link>http://example.com/dl.torrent</link>
      <size>8589934592</size>
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
      <size>8589934592</size>
      <torznab:attr name="seeders" value="42"/>
      <torznab:attr name="magneturl" value="magnet:?xt=urn:btih:abc123"/>
    </item>
    <item>
      <title>AnotherMovie 2022 1080p</title>
      <link>http://example.com/dl2.torrent</link>
      <size>4294967296</size>
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
      <size>5368709120</size>
      <torznab:attr name="seeders" value="100"/>
      <torznab:attr name="magneturl" value="magnet:?xt=urn:btih:xyz789"/>
      <torznab:attr name="imdbid" value="tt1375666"/>
    </item>
  </channel>
</rss>"""

# Helpers


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


# _attr (static method)


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


# _to_mb (static method)


class TestToMb:
    """Tests for bytes_to_mb shared helper (was JackettClient._to_mb)."""

    def test_converts_bytes_to_mb(self) -> None:
        """8 GB (8,589,934,592 bytes) converts to 8589 decimal MB."""
        assert bytes_to_mb("8589934592") == "8589"

    def test_truncates_remainder(self) -> None:
        """Conversion truncates (integer division), does not round."""
        assert bytes_to_mb("1000001") == "1"  # just over 1 MB

    def test_zero_bytes(self) -> None:
        """Zero bytes converts to '0'."""
        assert bytes_to_mb("0") == "0"


# _parse_item


class TestParseItem:
    """Tests for JackettClient._parse_item."""

    def test_returns_result_dict_with_correct_fields(self, mocker: MockerFixture) -> None:
        """parse_item extracts all expected ResultDict fields."""
        client, _ = _make_client(mocker)
        item: dict[str, Any] = {
            "title": "Movie 2023 1080p BluRay",
            "link": "http://example.com/dl.torrent",
            "size": "8589934592",  # standard RSS element
            _NS_ATTR: [
                {"@name": "seeders", "@value": "42"},
                {"@name": "magneturl", "@value": "magnet:?xt=urn:btih:abc123"},
            ],
        }
        result = client._parse_item(item)

        assert result is not None
        assert result["index_title"] == "Movie 2023 1080p BluRay"
        assert result["index_seeders"] == "42"
        assert result["index_size"] == "8589934592"
        assert result["index_size_mb"] == "8589"
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
        item: dict[str, Any] = {
            "title": "Inception 2010 1080p",
            "link": "http://example.com/inception.torrent",
            "size": "5368709120",  # standard RSS element
            _NS_ATTR: [
                {"@name": "seeders", "@value": "100"},
                {"@name": "imdbid", "@value": "tt1375666"},
            ],
        }
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

    def test_size_read_from_rss_element(self, mocker: MockerFixture) -> None:
        """index_size is populated from the top-level <size> RSS element, not torznab:attr."""
        client, _ = _make_client(mocker)
        # size as a standard RSS element (top-level dict key), no torznab:attr for size
        item: dict[str, Any] = {
            "title": "Big.Movie.2024.2160p.Remux",
            "link": "http://example.com/dl.torrent",
            "size": "10737418240",  # 10 GiB as RSS element
            _NS_ATTR: [{"@name": "seeders", "@value": "5"}],
        }
        result = client._parse_item(item)

        assert result is not None
        assert result["index_size"] == "10737418240"
        assert result["index_size_mb"] == "10737"

    def test_size_in_torznab_attr_only_is_ignored(self, mocker: MockerFixture) -> None:
        """size in torznab:attr only (no <size> element) gives empty index_size."""
        client, _ = _make_client(mocker)
        # size only in torznab:attr — this is the wrong place; should NOT be read
        item = _make_item("Movie", [{"@name": "size", "@value": "8589934592"}])
        result = client._parse_item(item)

        assert result is not None
        assert result["index_size"] == ""
        assert result["index_size_mb"] == "0"

    def test_torrent_url_prefers_enclosure_over_link(self, mocker: MockerFixture) -> None:
        """torrent_url uses enclosure[@url] when present (more reliable than link)."""
        client, _ = _make_client(mocker)
        item: dict[str, Any] = {
            "title": "Movie 2023",
            "link": "https://example.com/details/123",  # page URL
            "enclosure": {
                "@url": "https://example.com/dl/123.torrent",
                "@length": "1000",
                "@type": "application/x-bittorrent",
            },
            "size": "1000",
            _NS_ATTR: [],
        }
        result = client._parse_item(item)

        assert result is not None
        assert result["torrent_url"] == "https://example.com/dl/123.torrent"

    def test_torrent_url_falls_back_to_link_when_no_enclosure(self, mocker: MockerFixture) -> None:
        """torrent_url falls back to link when enclosure is absent."""
        client, _ = _make_client(mocker)
        item: dict[str, Any] = {
            "title": "Movie 2023",
            "link": "https://example.com/dl/123.torrent",
            "size": "1000",
            _NS_ATTR: [],
        }
        result = client._parse_item(item)

        assert result is not None
        assert result["torrent_url"] == "https://example.com/dl/123.torrent"

    def test_torrent_url_empty_when_enclosure_is_magnet(self, mocker: MockerFixture) -> None:
        """torrent_url is empty when enclosure url is a magnet link (not a torrent)."""
        client, _ = _make_client(mocker)
        item: dict[str, Any] = {
            "title": "Movie 2023",
            "link": "https://example.com/details/123",
            "enclosure": {"@url": "magnet:?xt=urn:btih:abc123", "@length": "0", "@type": "application/x-bittorrent"},
            "size": "1000",
            _NS_ATTR: [{"@name": "magneturl", "@value": "magnet:?xt=urn:btih:abc123"}],
        }
        result = client._parse_item(item)

        assert result is not None
        assert result["torrent_url"] == ""
        assert result["magnet_url"] == "magnet:?xt=urn:btih:abc123"

    def test_torrent_url_falls_back_to_link_when_enclosure_url_empty(self, mocker: MockerFixture) -> None:
        """torrent_url falls back to link when enclosure dict has empty @url (malformed feed)."""
        client, _ = _make_client(mocker)
        item: dict[str, Any] = {
            "title": "Movie 2023",
            "link": "https://example.com/dl/123.torrent",
            "enclosure": {"@length": "1000", "@type": "application/x-bittorrent"},  # no @url
            "size": "1000",
            _NS_ATTR: [],
        }
        result = client._parse_item(item)

        assert result is not None
        assert result["torrent_url"] == "https://example.com/dl/123.torrent"

    def test_magnet_url_falls_back_to_enclosure_when_torznab_attr_absent(self, mocker: MockerFixture) -> None:
        """magnet_url is populated from enclosure when torznab:attr magneturl is absent."""
        client, _ = _make_client(mocker)
        item: dict[str, Any] = {
            "title": "Movie 2023",
            "link": "https://example.com/details/123",
            "enclosure": {"@url": "magnet:?xt=urn:btih:def456", "@length": "0", "@type": "application/x-bittorrent"},
            "size": "1000",
            _NS_ATTR: [],  # no magneturl attr
        }
        result = client._parse_item(item)

        assert result is not None
        assert result["torrent_url"] == ""
        assert result["magnet_url"] == "magnet:?xt=urn:btih:def456"

    def test_tracker_plain_string_used_directly(self, mocker: MockerFixture) -> None:
        """jackettindexer as a dict uses the #text key as tracker name."""
        client, _ = _make_client(mocker)
        item: dict[str, Any] = {
            "title": "Movie 2023 1080p",
            "link": "https://example.com/dl/123.torrent",
            "jackettindexer": {"#text": "BitMagnet"},
            _NS_ATTR: [],
        }
        result = client._parse_item(item)

        assert result is not None
        assert result["index_tracker"] == "BitMagnet"

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

    def test_returns_none_on_malformed_xml(self, mocker: MockerFixture) -> None:
        """Returns None when the response body is malformed XML (parse error)."""
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.content = b"<unclosed tag"
        mock_http.get.return_value = mock_resp

        assert client._fetch_page("http://example.com", "test-indexer") is None

    def test_returns_empty_list_on_valid_xml_missing_items(self, mocker: MockerFixture) -> None:
        """Returns [] when the feed is valid XML but has no <item> elements.

        An empty feed (zero results from the indexer) is not an error condition.
        """
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.content = b"<not-a-valid-torznab/>"
        mock_http.get.return_value = mock_resp

        assert client._fetch_page("http://example.com", "test-indexer") == []


# search (public)


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


# is_reachable (public)


class TestIsReachable:
    """Tests for JackettClient.is_reachable.

    is_reachable() uses a direct requests.get() call (no HttpClient backoff)
    so tests patch 'requests.get' instead of the HttpClient mock.
    """

    def test_returns_true_on_successful_get(self, mocker: MockerFixture) -> None:
        """Returns True when the health-check GET returns a 2xx status."""
        client, _ = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.raise_for_status.return_value = None
        mocker.patch("movarr.jackett.requests.get", return_value=mock_resp)

        assert client.is_reachable() is True

    def test_returns_false_on_connection_error(self, mocker: MockerFixture) -> None:
        """Returns False immediately (no retry) when the host is unreachable."""
        import requests as _requests

        client, _ = _make_client(mocker)
        mocker.patch(
            "movarr.jackett.requests.get",
            side_effect=_requests.exceptions.ConnectionError("Connection refused"),
        )

        assert client.is_reachable() is False

    def test_returns_false_on_http_error_status(self, mocker: MockerFixture) -> None:
        """Returns False when the server returns a non-2xx status."""
        import requests as _requests

        client, _ = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.raise_for_status.side_effect = _requests.exceptions.HTTPError("401 Unauthorized")
        mocker.patch("movarr.jackett.requests.get", return_value=mock_resp)

        assert client.is_reachable() is False

    def test_logs_host_and_port_on_failure(self, mocker: MockerFixture) -> None:
        """The warning log includes the configured host and port."""
        import requests as _requests

        client, _ = _make_client(mocker)
        mocker.patch(
            "movarr.jackett.requests.get",
            side_effect=_requests.exceptions.ConnectionError("refused"),
        )
        mock_warn = mocker.patch("movarr.jackett._logger.warning")

        client.is_reachable()

        logged = " ".join(str(a) for a in mock_warn.call_args[0])
        assert "localhost" in logged
        assert "9117" in logged
