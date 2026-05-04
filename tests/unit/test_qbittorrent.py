"""Unit tests for movarr.qbittorrent — qBittorrent WebUI client wrapper."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any

import pytest
import qbittorrentapi

from movarr.config import Config
from movarr.qbittorrent import QBittorrentClient

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

    from movarr.models import ResultDict

# Helpers


def _make_client(mocker: MockerFixture) -> tuple[QBittorrentClient, Any]:
    """Return a QBittorrentClient with a mocked qbittorrentapi.Client."""
    mock_api_cls = mocker.patch("movarr.qbittorrent.qbittorrentapi.Client")
    cfg = Config()
    client = QBittorrentClient(cfg)
    return client, mock_api_cls.return_value


def _old_ts(mins: int) -> int:
    """Return a Unix timestamp that is *mins* minutes in the past."""
    return int((datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(minutes=mins)).timestamp())


def _make_torrent(mocker: MockerFixture, *, tag: str = "movarr-abc123", amount_left: int = 0) -> Any:
    """Build a minimal mock torrent object for list_completed tests."""
    torrent = mocker.MagicMock()
    torrent.tags = tag
    torrent.amount_left = amount_left
    torrent.hash = "abc123"
    torrent.name = "Test Movie"
    return torrent


# is_connected


class TestIsConnected:
    """Tests for QBittorrentClient.is_connected."""

    def test_returns_true_when_status_is_connected(self, mocker: MockerFixture) -> None:
        """Returns True when server_state reports 'connected' status."""
        client, mock_api = _make_client(mocker)
        mock_api.sync_maindata.return_value.server_state.connection_status = "connected"
        assert client.is_connected() is True

    def test_returns_true_when_status_is_firewalled(self, mocker: MockerFixture) -> None:
        """Returns True when firewalled — internet is up, just behind NAT."""
        client, mock_api = _make_client(mocker)
        mock_api.sync_maindata.return_value.server_state.connection_status = "firewalled"
        assert client.is_connected() is True

    def test_returns_false_when_status_is_disconnected(self, mocker: MockerFixture) -> None:
        """Returns False when disconnected — internet is down, skip queue management."""
        client, mock_api = _make_client(mocker)
        mock_api.sync_maindata.return_value.server_state.connection_status = "disconnected"
        assert client.is_connected() is False

    def test_returns_false_on_api_error(self, mocker: MockerFixture) -> None:
        """Returns False when the API call raises APIError."""
        client, mock_api = _make_client(mocker)
        mock_api.sync_maindata.side_effect = qbittorrentapi.APIError("connection failed")
        assert client.is_connected() is False


# add_torrent


class TestAddTorrent:
    """Tests for QBittorrentClient.add_torrent."""

    def test_adds_magnet_url_and_sets_tag(self, mocker: MockerFixture) -> None:
        """Adds torrent via magnet_url and stamps result with a movarr- tag."""
        client, mock_api = _make_client(mocker)
        result: ResultDict = {
            "index_title": "Test Movie",
            "magnet_url": "magnet:?xt=urn:btih:abc123",
            "torrent_url": "",
        }
        updated = client.add_torrent(result)

        assert updated is not None
        assert "torrent_tag" in updated
        assert updated["torrent_tag"].startswith("movarr-")
        mock_api.torrents_add.assert_called_once()
        mock_api.torrents_reannounce.assert_called_once()

    def test_falls_back_to_torrent_url_when_no_magnet(self, mocker: MockerFixture) -> None:
        """Falls back to torrent_url when magnet_url is absent."""
        client, mock_api = _make_client(mocker)
        result: ResultDict = {
            "index_title": "Test Movie",
            "magnet_url": "",
            "torrent_url": "http://example.com/movie.torrent",
        }
        updated = client.add_torrent(result)

        assert updated is not None
        assert "torrent_tag" in updated
        mock_api.torrents_add.assert_called_once()

    def test_returns_none_when_no_url(self, mocker: MockerFixture) -> None:
        """Returns None and skips API call when neither URL is set."""
        client, mock_api = _make_client(mocker)
        result: ResultDict = {"index_title": "Test Movie", "magnet_url": "", "torrent_url": ""}

        assert client.add_torrent(result) is None
        mock_api.torrents_add.assert_not_called()

    def test_returns_none_on_api_error(self, mocker: MockerFixture) -> None:
        """Returns None when torrents_add raises APIError."""
        client, mock_api = _make_client(mocker)
        mock_api.torrents_add.side_effect = qbittorrentapi.APIError("quota exceeded")
        result: ResultDict = {
            "index_title": "Test Movie",
            "magnet_url": "magnet:?xt=urn:btih:abc123",
        }

        assert client.add_torrent(result) is None

    def test_tag_matches_uuid_format(self, mocker: MockerFixture) -> None:
        """The generated tag follows 'movarr-<uuid>' pattern."""
        import re

        client, _ = _make_client(mocker)
        result: ResultDict = {"index_title": "Movie", "magnet_url": "magnet:?xt=urn:btih:xyz"}
        updated = client.add_torrent(result)

        assert updated is not None
        tag = updated["torrent_tag"]
        assert re.match(r"^movarr-[0-9a-f-]{36}$", tag)


# list_completed


class TestListCompleted:
    """Tests for QBittorrentClient.list_completed."""

    def test_returns_completed_movarr_tagged_torrents(self, mocker: MockerFixture) -> None:
        """Returns structured dicts for stopped 100%-complete movarr torrents."""
        client, mock_api = _make_client(mocker)
        torrent = _make_torrent(mocker, tag="movarr-abc123, other-tag")

        mock_file = mocker.MagicMock()
        mock_file.name = "movie.mkv"
        mock_file.size = 1_000_000

        mock_props = mocker.MagicMock()
        mock_props.save_path = "/downloads/movies"

        mock_api.torrents_info.return_value = [torrent]
        mock_api.torrents_files.return_value = [mock_file]
        mock_api.torrents_properties.return_value = mock_props

        results = client.list_completed()

        assert len(results) == 1
        entry = results[0]
        assert entry["torrent_hash"] == "abc123"
        assert entry["torrent_name"] == "Test Movie"
        assert entry["torrent_tag"] == "movarr-abc123"
        assert entry["torrent_save_path"] == "/downloads/movies"
        assert entry["torrent_file_list"] == [{"file_name": "movie.mkv", "file_size": 1_000_000}]

    def test_skips_torrents_without_movarr_tag(self, mocker: MockerFixture) -> None:
        """Torrents whose tags contain no 'movarr-' prefix are excluded."""
        client, mock_api = _make_client(mocker)
        torrent = _make_torrent(mocker, tag="other-tag, another-tag")
        mock_api.torrents_info.return_value = [torrent]

        assert client.list_completed() == []

    def test_skips_torrents_with_nonzero_amount_left(self, mocker: MockerFixture) -> None:
        """Torrents that are not 100% complete are excluded."""
        client, mock_api = _make_client(mocker)
        torrent = _make_torrent(mocker, tag="movarr-abc123", amount_left=1024)
        mock_api.torrents_info.return_value = [torrent]

        assert client.list_completed() == []

    def test_returns_empty_list_on_api_error(self, mocker: MockerFixture) -> None:
        """Returns [] when torrents_info raises APIError."""
        client, mock_api = _make_client(mocker)
        mock_api.torrents_info.side_effect = qbittorrentapi.APIError("service unavailable")

        assert client.list_completed() == []

    def test_skips_torrent_when_metadata_fetch_fails(self, mocker: MockerFixture) -> None:
        """Torrent is skipped (not raised) when files/props API call fails."""
        client, mock_api = _make_client(mocker)
        torrent = _make_torrent(mocker, tag="movarr-abc123")
        mock_api.torrents_info.return_value = [torrent]
        mock_api.torrents_files.side_effect = qbittorrentapi.APIError("hash not found")

        assert client.list_completed() == []


# identify_for_deletion


class TestIdentifyForDeletion:
    """Tests for QBittorrentClient.identify_for_deletion."""

    def test_includes_torrent_exceeding_max_mins_last_activity(self, mocker: MockerFixture) -> None:
        """Torrent with last_activity older than max_mins is returned."""
        client, _ = _make_client(mocker)
        torrent_map = {
            "hash1": {"state": "stalledDL", "name": "Stalled Movie", "last_activity": _old_ts(200)},
        }
        result = client.identify_for_deletion(torrent_map, "stalledDL", 120, "last_activity")

        assert "hash1" in result
        assert result["hash1"]["name"] == "Stalled Movie"
        assert result["hash1"]["age_mins"] > 120

    def test_excludes_torrent_within_max_mins(self, mocker: MockerFixture) -> None:
        """Torrent with last_activity within max_mins is excluded."""
        client, _ = _make_client(mocker)
        torrent_map = {
            "hash1": {"state": "stalledDL", "name": "Recent Movie", "last_activity": _old_ts(30)},
        }
        result = client.identify_for_deletion(torrent_map, "stalledDL", 120, "last_activity")

        assert "hash1" not in result

    def test_skips_torrent_in_different_state(self, mocker: MockerFixture) -> None:
        """Torrent in a state other than the target is ignored."""
        client, _ = _make_client(mocker)
        torrent_map = {
            "hash1": {"state": "downloading", "name": "Active Movie", "last_activity": _old_ts(200)},
        }
        result = client.identify_for_deletion(torrent_map, "stalledDL", 120, "last_activity")

        assert "hash1" not in result

    def test_skips_torrent_with_zero_timestamp(self, mocker: MockerFixture) -> None:
        """Torrent with ts=0 (never had network activity) is skipped."""
        client, _ = _make_client(mocker)
        torrent_map = {
            "hash1": {"state": "metaDL", "name": "Never Active", "added_on": 0},
        }
        result = client.identify_for_deletion(torrent_map, "metaDL", 30, "added_on")

        assert "hash1" not in result

    def test_skips_torrent_with_missing_name(self, mocker: MockerFixture) -> None:
        """Torrent without a 'name' key is ignored regardless of state."""
        client, _ = _make_client(mocker)
        torrent_map = {
            "hash1": {"state": "stalledDL", "last_activity": _old_ts(200)},
        }
        result = client.identify_for_deletion(torrent_map, "stalledDL", 120, "last_activity")

        assert "hash1" not in result

    def test_uses_added_on_for_meta_dl(self, mocker: MockerFixture) -> None:
        """filter_type='added_on' reads the added_on field, not last_activity."""
        client, _ = _make_client(mocker)
        torrent_map = {
            "hash1": {"state": "metaDL", "name": "Metadata Stuck", "added_on": _old_ts(60)},
        }
        result = client.identify_for_deletion(torrent_map, "metaDL", 30, "added_on")

        assert "hash1" in result

    def test_raises_value_error_for_invalid_filter_type(self, mocker: MockerFixture) -> None:
        """Raises ValueError when filter_type is not a recognised field."""
        client, _ = _make_client(mocker)
        with pytest.raises(ValueError, match="filter_type"):
            client.identify_for_deletion({}, "stalledDL", 120, "invalid_field")

    def test_returns_empty_dict_when_no_candidates(self, mocker: MockerFixture) -> None:
        """Returns an empty dict when torrent_map is empty."""
        client, _ = _make_client(mocker)
        result = client.identify_for_deletion({}, "stalledDL", 120, "last_activity")

        assert result == {}


# delete_torrent


class TestDeleteTorrent:
    """Tests for QBittorrentClient.delete_torrent."""

    def test_returns_true_and_calls_api_on_success(self, mocker: MockerFixture) -> None:
        """Returns True and calls torrents_delete with correct args."""
        client, mock_api = _make_client(mocker)

        assert client.delete_torrent("hash1", delete_data=False, state="stalledDL") is True
        mock_api.torrents_delete.assert_called_once_with(delete_files=False, torrent_hashes="hash1")

    def test_returns_true_with_delete_data_true(self, mocker: MockerFixture) -> None:
        """delete_data=True is forwarded to torrents_delete."""
        client, mock_api = _make_client(mocker)

        assert client.delete_torrent("hash2", delete_data=True, state="metaDL") is True
        mock_api.torrents_delete.assert_called_once_with(delete_files=True, torrent_hashes="hash2")

    def test_returns_false_on_api_error(self, mocker: MockerFixture) -> None:
        """Returns False when torrents_delete raises APIError."""
        client, mock_api = _make_client(mocker)
        mock_api.torrents_delete.side_effect = qbittorrentapi.APIError("hash not found")

        assert client.delete_torrent("hash1", delete_data=False, state="stalledDL") is False


# delete_stalled


class TestDeleteStalled:
    """Tests for QBittorrentClient.delete_stalled."""

    def test_calls_delete_torrent_for_each_entry(self, mocker: MockerFixture) -> None:
        """Calls torrents_delete once per entry in stalled_map."""
        client, mock_api = _make_client(mocker)
        stalled_map = {
            "hash1": {"name": "Movie 1", "age_mins": 200, "state": "stalledDL"},
            "hash2": {"name": "Movie 2", "age_mins": 300, "state": "stalledDL"},
        }
        client.delete_stalled(stalled_map, state="stalledDL", delete_data=False)

        assert mock_api.torrents_delete.call_count == 2

    def test_empty_map_makes_no_api_calls(self, mocker: MockerFixture) -> None:
        """No API calls are made when stalled_map is empty."""
        client, mock_api = _make_client(mocker)
        client.delete_stalled({}, state="stalledDL", delete_data=False)

        mock_api.torrents_delete.assert_not_called()

    def test_forwards_delete_data_flag(self, mocker: MockerFixture) -> None:
        """delete_data=True is forwarded to each torrents_delete call."""
        client, mock_api = _make_client(mocker)
        stalled_map = {"hash1": {"name": "Movie", "age_mins": 200, "state": "stalledDL"}}
        client.delete_stalled(stalled_map, state="stalledDL", delete_data=True)

        mock_api.torrents_delete.assert_called_once_with(delete_files=True, torrent_hashes="hash1")


# list_by_category


class TestListByCategory:
    """Tests for QBittorrentClient.list_by_category."""

    def test_returns_dict_keyed_by_hash(self, mocker: MockerFixture) -> None:
        """list_by_category returns torrents keyed by their hash string."""
        client, mock_api = _make_client(mocker)
        mock_api.torrents_info.return_value = [
            {"hash": "abc123", "name": "Movie A"},
            {"hash": "def456", "name": "Movie B"},
        ]
        result = client.list_by_category()
        assert result == {
            "abc123": {"hash": "abc123", "name": "Movie A"},
            "def456": {"hash": "def456", "name": "Movie B"},
        }

    def test_empty_category_returns_empty_dict(self, mocker: MockerFixture) -> None:
        """list_by_category returns an empty dict when no torrents found."""
        client, mock_api = _make_client(mocker)
        mock_api.torrents_info.return_value = []
        result = client.list_by_category()
        assert result == {}


# delete_stalled — warning when delete_torrent returns False


class TestDeleteStalledWarning:
    """delete_stalled logs a warning when delete_torrent returns False."""

    def test_logs_warning_when_delete_fails(self, mocker: MockerFixture) -> None:
        client, _mock_api = _make_client(mocker)
        mocker.patch.object(client, "delete_torrent", return_value=False)
        mock_warning = mocker.patch("movarr.qbittorrent._logger.warning")
        stalled_map = {"hash1": {"name": "Stuck Movie", "age_mins": 500, "state": "stalledDL"}}
        client.delete_stalled(stalled_map, state="stalledDL", delete_data=False)
        mock_warning.assert_called_once()
