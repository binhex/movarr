"""Unit tests for movarr.qbittorrent — qBittorrent WebUI client wrapper."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any

import pytest
import qbittorrentapi

from movarr.config import Config
from movarr.qbittorrent import QBittorrentClient, _build_supersede_tag, parse_imdb_id_from_tags

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

    def test_returns_false_on_api_connection_error(self, mocker: MockerFixture) -> None:
        """Returns False when qBittorrent is unreachable (APIConnectionError).

        ``qbittorrentapi.exceptions.APIConnectionError`` subclasses ``APIError``,
        so an unreachable host takes the same non-raising path as an API error.
        This regression test pins that relationship: the outage alert depends on
        ``is_connected()`` returning False rather than propagating.
        """
        client, mock_api = _make_client(mocker)
        mock_api.sync_maindata.side_effect = qbittorrentapi.exceptions.APIConnectionError("unreachable")
        assert client.is_connected() is False


# add_torrent


class TestAddTorrent:
    """Tests for QBittorrentClient.add_torrent."""

    def test_adds_magnet_url_and_sets_tag(self, mocker: MockerFixture) -> None:
        """Adds torrent via magnet_url and stamps result with a movarr- tag."""
        client, mock_api = _make_client(mocker)
        mock_quality = mocker.patch("movarr.parsing.quality_score", return_value=55)
        result: ResultDict = {
            "index_title": "Test Movie",
            "imdb_id": "tt1234567",
            "index_title_sanitised": "Test Movie 2024 1080p WEB-DL",
            "magnet_url": "magnet:?xt=urn:btih:abc123",
            "torrent_url": "",
        }
        updated = client.add_torrent(result)

        assert updated is not None
        assert "torrent_tag" in updated
        tag = updated["torrent_tag"]
        assert tag.startswith("movarr-")
        assert "-imdb-tt1234567-score-55" in tag
        mock_quality.assert_called_once()
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
        """The generated tag follows 'movarr-<8hex>' pattern when no imdb_id."""
        import re

        client, _ = _make_client(mocker)
        result: ResultDict = {"index_title": "Movie", "magnet_url": "magnet:?xt=urn:btih:xyz"}
        updated = client.add_torrent(result)

        assert updated is not None
        tag = updated["torrent_tag"]
        assert re.match(r"^movarr-[0-9a-f]{8}$", tag)


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

    def test_zero_last_activity_falls_back_to_added_on(self, mocker: MockerFixture) -> None:
        """Torrent with last_activity=0 falls back to added_on for age calculation.

        Previously this treated age_mins as infinite, immediately deleting
        brand-new torrents that had never connected to a peer. The fix uses
        added_on as a fallback so newly queued torrents get a grace period.
        """
        import datetime

        client, _ = _make_client(mocker)
        # added_on set to 2 hours ago so the torrent is eligible after the 30 min threshold.
        two_hours_ago = int((datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=2)).timestamp())
        torrent_map = {
            "hash1": {
                "state": "stalledDL",
                "name": "Never Active",
                "last_activity": 0,
                "added_on": two_hours_ago,
            },
        }
        result = client.identify_for_deletion(torrent_map, "stalledDL", 30, "last_activity")

        assert "hash1" in result
        # Age should be roughly 120 minutes, not infinity.
        assert result["hash1"]["age_mins"] < float("inf")
        assert result["hash1"]["age_mins"] > 30

    def test_zero_last_activity_zero_added_on_skipped(self, mocker: MockerFixture) -> None:
        """Torrent with both last_activity=0 and added_on=0 is skipped (age unknowable)."""
        client, _ = _make_client(mocker)
        torrent_map = {
            "hash1": {
                "state": "stalledDL",
                "name": "No Timestamps",
                "last_activity": 0,
                "added_on": 0,
            },
        }
        result = client.identify_for_deletion(torrent_map, "stalledDL", 30, "last_activity")

        assert "hash1" not in result

    def test_none_last_activity_skipped(self, mocker: MockerFixture) -> None:
        """Torrent with last_activity=None is skipped."""
        client, _ = _make_client(mocker)
        torrent_map = {
            "hash1": {"state": "stalledDL", "name": "No Activity Key", "last_activity": None},
        }
        result = client.identify_for_deletion(torrent_map, "stalledDL", 30, "last_activity")

        assert "hash1" not in result

    def test_zero_added_on_skipped(self, mocker: MockerFixture) -> None:
        """Torrent with added_on=0 is skipped (None vs 0 distinction)."""
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

    def test_log_message_includes_name_when_provided(self, mocker: MockerFixture) -> None:
        """The INFO log includes the torrent name when passed."""
        client, mock_api = _make_client(mocker)
        mock_info = mocker.patch("movarr.qbittorrent._logger.info")

        client.delete_torrent("hash1", delete_data=False, state="stalledDL", name="Movie.Title.2024")

        # The name should appear as a positional argument to the log call
        log_args = mock_info.call_args.args
        assert "Movie.Title.2024" in log_args


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
        mock_dt = mocker.patch.object(client, "delete_torrent", wraps=client.delete_torrent)
        client.delete_stalled(stalled_map, state="stalledDL", delete_data=False)

        assert mock_api.torrents_delete.call_count == 2
        mock_dt.assert_any_call("hash1", False, "stalledDL", name="Movie 1")
        mock_dt.assert_any_call("hash2", False, "stalledDL", name="Movie 2")

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

    def test_returns_seeding_torrents_not_just_stopped(self, mocker: MockerFixture) -> None:
        """list_completed must include 100%-complete torrents in any state.

        With add_paused=False (the default), a finished torrent will often be
        in an uploading/seeding state rather than 'stopped'.  The old
        implementation used status_filter='stopped' and silently missed them.
        The fix queries all torrents in the movarr category and filters by
        amount_left == 0 regardless of state.
        """
        client, mock_api = _make_client(mocker)
        # Simulate an uploading torrent (100% done, add_paused was False).
        torrent = _make_torrent(mocker, tag="movarr-abc123", amount_left=0)

        mock_file = mocker.MagicMock()
        mock_file.name = "movie.mkv"
        mock_file.size = 5_000_000_000

        mock_props = mocker.MagicMock()
        mock_props.save_path = "/downloads/movies"

        mock_api.torrents_info.return_value = [torrent]
        mock_api.torrents_files.return_value = [mock_file]
        mock_api.torrents_properties.return_value = mock_props

        results = client.list_completed()

        # Must be returned regardless of the 'stopped' state filter.
        assert len(results) == 1
        assert results[0]["torrent_hash"] == "abc123"

        # Confirm torrents_info was NOT called with status_filter='stopped'.
        call_kwargs = mock_api.torrents_info.call_args
        assert call_kwargs.kwargs.get("status_filter") != "stopped"


# add_torrent — empty torrents_info (line 118) and APIError on reannounce (lines 119-120)


class TestAddTorrentReannounce:
    """Tests for the reannounce branch in add_torrent."""

    def test_skips_reannounce_when_torrents_info_empty(self, mocker: MockerFixture) -> None:
        """When torrents_info returns [], no hash is found and reannounce is skipped."""
        client, mock_api = _make_client(mocker)
        mock_api.torrents_info.return_value = []
        mock_debug = mocker.patch("movarr.qbittorrent._logger.debug")
        result: ResultDict = {
            "index_title": "Test Movie",
            "magnet_url": "magnet:?xt=urn:btih:abc123",
            "torrent_url": "",
        }

        updated = client.add_torrent(result)

        assert updated is not None
        assert "torrent_tag" in updated
        mock_api.torrents_reannounce.assert_not_called()
        # Debug log for the "no hash" branch must have been called.
        assert any("reannounce" in str(call) for call in mock_debug.call_args_list)

    def test_warns_and_continues_when_reannounce_raises_api_error(self, mocker: MockerFixture) -> None:
        """When torrents_reannounce raises APIError a warning is logged and result is returned."""
        client, mock_api = _make_client(mocker)
        torrent = mocker.MagicMock()
        torrent.hash = "deadbeef"
        mock_api.torrents_info.return_value = [torrent]
        mock_api.torrents_reannounce.side_effect = qbittorrentapi.APIError("tracker error")
        mock_warning = mocker.patch("movarr.qbittorrent._logger.warning")
        result: ResultDict = {
            "index_title": "Test Movie",
            "magnet_url": "magnet:?xt=urn:btih:abc123",
            "torrent_url": "",
        }

        updated = client.add_torrent(result)

        assert updated is not None
        mock_warning.assert_called()
        assert any("Reannounce" in str(call) for call in mock_warning.call_args_list)


# _build_supersede_tag


class TestBuildSupersedeTag:
    """Tests for _build_supersede_tag — structured movarr torrent tag builder."""

    def test_builds_tag_with_imdb_id_and_score(self) -> None:
        """Tag contains movarr prefix, 8-char hex, imdb id, and score."""
        tag = _build_supersede_tag("tt1234567", 45)
        assert tag.startswith("movarr-")
        assert "-imdb-tt1234567-score-45" in tag

    def test_tag_format(self) -> None:
        """Tag matches the expected structured format."""
        import re

        tag = _build_supersede_tag("tt0000001", 10)
        assert re.match(r"^movarr-[0-9a-f]{8}-imdb-tt\d+-score-\d+$", tag)

    def test_uuid_part_is_8_hex_chars(self) -> None:
        """The UUID segment is exactly 8 lowercase hex characters."""
        tag = _build_supersede_tag("tt9999999", 99)
        parts = tag.split("-")
        assert len(parts[1]) == 8
        int(parts[1], 16)  # raises ValueError if not hex

    def test_different_inputs_produce_different_tags(self) -> None:
        """Different imdb_id/score combos produce different tags."""
        tag1 = _build_supersede_tag("tt1234567", 45)
        tag2 = _build_supersede_tag("tt7654321", 30)
        assert tag1 != tag2


# parse_imdb_id_from_tags


class TestParseImdbIdFromTags:
    """Tests for parse_imdb_id_from_tags - IMDb ID extraction from tag strings."""

    def test_returns_imdb_id_when_present(self) -> None:
        """Extracts tt-id from a complete movarr tag."""
        assert parse_imdb_id_from_tags("movarr-abc123-imdb-tt1234567-score-45") == "tt1234567"

    def test_returns_imdb_id_from_comma_separated_tags(self) -> None:
        """Finds imdb segment even when tag is part of a comma-separated list."""
        assert parse_imdb_id_from_tags("tag1, movarr-abc123-imdb-tt1234567-score-45, tag2") == "tt1234567"

    def test_returns_none_when_no_imdb_segment(self) -> None:
        """Returns None when tag lacks the imdb-ttN segment."""
        assert parse_imdb_id_from_tags("movarr-abc123-score-45") is None

    def test_returns_none_for_empty_string(self) -> None:
        """Returns None for empty input."""
        assert parse_imdb_id_from_tags("") is None

    def test_returns_none_for_unrelated_tags(self) -> None:
        """Returns None when no movarr tag is present."""
        assert parse_imdb_id_from_tags("some-random-tag") is None

    def test_handles_8_digit_imdb_id(self) -> None:
        """Supports 8-digit IMDb IDs (tt12345678)."""
        assert parse_imdb_id_from_tags("movarr-abc123-imdb-tt12345678-score-45") == "tt12345678"


class TestParseImdbIdDigitWidth:
    """parse_imdb_id_from_tags must accept future 9-digit IMDb IDs."""

    def test_handles_9_digit_imdb_id(self) -> None:
        """A 9-digit IMDb ID must still match the supersession regex."""
        assert parse_imdb_id_from_tags("movarr-abc123-imdb-tt123456789-score-45") == "tt123456789"


class TestAddTorrentRejection:
    """A qBittorrent 'Fails.' response must not be recorded as a queued torrent."""

    def _result(self) -> ResultDict:
        return {"index_title": "Test Movie", "magnet_url": "magnet:?xt=urn:btih:abc123", "torrent_url": ""}

    def test_returns_none_when_qbt_reports_fails(self, mocker: MockerFixture) -> None:
        """A rejected add returns None so the pipeline records a failure, not Passed."""
        client, mock_api = _make_client(mocker)
        mock_api.torrents_add.return_value = "Fails."
        result = self._result()

        assert client.add_torrent(result) is None
        assert "torrent_tag" not in result
        mock_api.torrents_reannounce.assert_not_called()

    def test_returns_result_when_qbt_reports_ok(self, mocker: MockerFixture) -> None:
        """A successful add still returns the tagged result."""
        client, mock_api = _make_client(mocker)
        mock_api.torrents_add.return_value = "Ok."
        result = self._result()

        updated = client.add_torrent(result)
        assert updated is not None
        assert result["torrent_tag"].startswith("movarr-")


class TestBuildTagFromResultNormalisation:
    """Indexer-supplied IMDb IDs must be normalised before entering a tag."""

    def _client(self, mocker: MockerFixture) -> QBittorrentClient:
        client, _ = _make_client(mocker)
        return client

    def test_malformed_imdb_id_falls_back_to_random_tag(self, mocker: MockerFixture) -> None:
        """A malformed ID produces no imdb segment at all."""
        tag = self._client(mocker)._build_tag_from_result(
            {"imdb_id": "tt1)](https://evil.example", "index_title_sanitised": "Movie 2020 1080p"}
        )
        assert "imdb-" not in tag
        assert tag.startswith("movarr-")

    def test_bare_digit_imdb_id_is_normalised(self, mocker: MockerFixture) -> None:
        """A bare-digit ID gains the tt prefix."""
        tag = self._client(mocker)._build_tag_from_result(
            {"imdb_id": "1234567", "index_title_sanitised": "Movie 2020 1080p"}
        )
        assert "imdb-tt1234567" in tag

    def test_comma_in_imdb_id_cannot_inject_extra_tags(self, mocker: MockerFixture) -> None:
        """A comma-bearing ID cannot smuggle a second tag into the tag list."""
        tag = self._client(mocker)._build_tag_from_result(
            {"imdb_id": "tt1234567,tag-injected", "index_title_sanitised": "Movie 2020 1080p"}
        )
        assert "tag-injected" not in tag


class TestIsReachable:
    """is_reachable() is a pure API probe, independent of internet status."""

    def test_true_when_api_answers(self, mocker: MockerFixture) -> None:
        client, mock_api = _make_client(mocker)
        mock_api.app_version.return_value = "v4.6.0"
        assert client.is_reachable() is True

    def test_false_when_api_raises(self, mocker: MockerFixture) -> None:
        client, mock_api = _make_client(mocker)
        mock_api.app_version.side_effect = qbittorrentapi.APIConnectionError("down")
        assert client.is_reachable() is False


class TestReachableButOffline:
    """A reachable qBittorrent without internet is not a service outage."""

    def test_is_connected_false_while_reachable(self, mocker: MockerFixture) -> None:
        client, mock_api = _make_client(mocker)
        mock_api.app_version.return_value = "v4.6.0"
        mock_api.sync_maindata.return_value.server_state.connection_status = "disconnected"

        assert client.is_reachable() is True
        assert client.is_connected() is False


class TestSupersedeTagRequiresMovarrShape:
    """Only a complete movarr supersede tag identifies one of our torrents."""

    def test_foreign_imdb_tag_is_not_matched(self) -> None:
        """A user/other-tool tag must never be treated as a movarr tag."""
        assert parse_imdb_id_from_tags("imdb-tt0133093") is None
        assert parse_imdb_id_from_tags("movies, imdb-tt0133093, archive") is None

    def test_complete_movarr_tag_is_matched(self) -> None:
        assert parse_imdb_id_from_tags("movarr-abc12345-imdb-tt0133093-score-70") == "tt0133093"


class TestAddTorrentTagBeforeReannounce:
    """The tag is stamped before the optional reannounce can fail."""

    def test_tag_survives_a_reannounce_failure(self, mocker: MockerFixture) -> None:
        client, mock_api = _make_client(mocker)
        mock_api.torrents_info.side_effect = RuntimeError("unexpected")
        result: ResultDict = {
            "index_title": "Test Movie",
            "imdb_id": "tt1234567",
            "index_title_sanitised": "Test Movie 2024 1080p",
            "magnet_url": "magnet:?xt=urn:btih:abc123",
            "torrent_url": "",
        }

        updated = client.add_torrent(result)

        assert updated is not None
        assert updated["torrent_tag"].startswith("movarr-")


class TestMetadataStuckForcedState:
    """forcedMetaDL is the same stuck state as metaDL."""

    def test_forced_metadl_is_matched(self, mocker: MockerFixture) -> None:
        client, _ = _make_client(mocker)
        candidates = client.identify_for_deletion(
            {"h1": {"name": "Stuck Magnet", "state": "forcedMetaDL", "added_on": 1, "size": 0}},
            "metaDL",
            30,
            "added_on",
        )

        assert "h1" in candidates


class TestWebuiTlsVerification:
    """The qBittorrent WebUI certificate check is configurable."""

    def test_verification_flag_follows_config(self, mocker: MockerFixture) -> None:
        client_cls = mocker.patch("movarr.qbittorrent.qbittorrentapi.Client")
        cfg = Config()
        cfg.torrent_client.qbittorrent.verify_webui_certificate = True

        QBittorrentClient(cfg)

        assert client_cls.call_args.kwargs["VERIFY_WEBUI_CERTIFICATE"] is True

    def test_defaults_to_disabled_for_self_signed_lan_certs(self, mocker: MockerFixture) -> None:
        client_cls = mocker.patch("movarr.qbittorrent.qbittorrentapi.Client")

        QBittorrentClient(Config())

        assert client_cls.call_args.kwargs["VERIFY_WEBUI_CERTIFICATE"] is False
