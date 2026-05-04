"""Unit tests for movarr.queue_manager — stuck-torrent cleanup task."""

from __future__ import annotations

from typing import TYPE_CHECKING

from movarr.config import Config
from movarr.qbittorrent import QBittorrentClient
from movarr.queue_manager import _delete_stuck, _StuckConfig, run_queue_management

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

# Helpers


def _enabled_config() -> Config:
    """Return a Config with queue management fully enabled."""
    cfg = Config()
    cfg.queue_management.queue_management_enabled = True
    cfg.queue_management.metadata_monitor_enabled = True
    cfg.queue_management.stalled_monitor_enabled = True
    return cfg


# run_queue_management


class TestRunQueueManagement:
    """Tests for the run_queue_management public function."""

    def test_returns_early_when_disabled(self, mocker: MockerFixture) -> None:
        """Does nothing when queue_management_enabled is False."""
        cfg = Config()
        cfg.queue_management.queue_management_enabled = False
        qbt = mocker.MagicMock(spec=QBittorrentClient)
        db = mocker.MagicMock()

        run_queue_management(cfg, qbt, db)

        qbt.is_connected.assert_not_called()
        qbt.list_by_category.assert_not_called()

    def test_returns_early_when_not_connected(self, mocker: MockerFixture) -> None:
        """Skips all torrent operations when qBittorrent is unreachable."""
        cfg = _enabled_config()
        qbt = mocker.MagicMock(spec=QBittorrentClient)
        qbt.is_connected.return_value = False
        db = mocker.MagicMock()

        run_queue_management(cfg, qbt, db)

        qbt.list_by_category.assert_not_called()

    def test_calls_delete_stuck_for_meta_dl_when_enabled(self, mocker: MockerFixture) -> None:
        """_delete_stuck is called for metaDL when metadata_monitor_enabled=True."""
        cfg = _enabled_config()
        cfg.queue_management.stalled_monitor_enabled = False
        qbt = mocker.MagicMock(spec=QBittorrentClient)
        qbt.is_connected.return_value = True
        qbt.list_by_category.return_value = {}
        db = mocker.MagicMock()

        run_queue_management(cfg, qbt, db)

        qbt.list_by_category.assert_called_once()

    def test_calls_delete_stuck_for_stalled_dl_when_enabled(self, mocker: MockerFixture) -> None:
        """_delete_stuck is called for stalledDL when stalled_monitor_enabled=True."""
        cfg = _enabled_config()
        cfg.queue_management.metadata_monitor_enabled = False
        qbt = mocker.MagicMock(spec=QBittorrentClient)
        qbt.is_connected.return_value = True
        qbt.list_by_category.return_value = {}
        db = mocker.MagicMock()

        run_queue_management(cfg, qbt, db)

        qbt.list_by_category.assert_called_once()

    def test_calls_delete_stuck_twice_when_both_monitors_enabled(self, mocker: MockerFixture) -> None:
        """list_by_category is called once per enabled monitor (2 total)."""
        cfg = _enabled_config()
        qbt = mocker.MagicMock(spec=QBittorrentClient)
        qbt.is_connected.return_value = True
        qbt.list_by_category.return_value = {}
        db = mocker.MagicMock()

        run_queue_management(cfg, qbt, db)

        assert qbt.list_by_category.call_count == 2

    def test_skips_meta_dl_when_metadata_monitor_disabled(self, mocker: MockerFixture) -> None:
        """metaDL processing is skipped when metadata_monitor_enabled=False."""
        cfg = _enabled_config()
        cfg.queue_management.metadata_monitor_enabled = False
        cfg.queue_management.stalled_monitor_enabled = False
        qbt = mocker.MagicMock(spec=QBittorrentClient)
        qbt.is_connected.return_value = True
        db = mocker.MagicMock()

        run_queue_management(cfg, qbt, db)

        qbt.list_by_category.assert_not_called()


# _delete_stuck (internal, tested directly for full branch coverage)


class TestDeleteStuck:
    """Tests for the _delete_stuck internal function.

    _delete_stuck is tested directly because its early-exit branches
    (empty torrent_map, empty to_delete) cannot be exercised without
    significant state setup through run_queue_management alone.
    """

    def test_skips_identify_when_list_by_category_empty(self, mocker: MockerFixture) -> None:
        """Returns immediately when list_by_category returns an empty dict."""
        qbt = mocker.MagicMock(spec=QBittorrentClient)
        qbt.list_by_category.return_value = {}
        db = mocker.MagicMock()

        _delete_stuck(
            qbt,
            db,
            _StuckConfig(
                state="stalledDL",
                filter_type="last_activity",
                max_mins=120,
                label="stalled",
                delete_data=False,
            ),
        )

        qbt.identify_for_deletion.assert_not_called()
        qbt.delete_stalled.assert_not_called()

    def test_skips_delete_when_identify_returns_empty(self, mocker: MockerFixture) -> None:
        """Returns immediately when identify_for_deletion finds nothing."""
        qbt = mocker.MagicMock(spec=QBittorrentClient)
        qbt.list_by_category.return_value = {"hash1": {"state": "stalledDL", "name": "Movie"}}
        qbt.identify_for_deletion.return_value = {}
        db = mocker.MagicMock()

        _delete_stuck(
            qbt,
            db,
            _StuckConfig(
                state="stalledDL",
                filter_type="last_activity",
                max_mins=120,
                label="stalled",
                delete_data=False,
            ),
        )

        qbt.delete_stalled.assert_not_called()

    def test_calls_delete_stalled_when_candidates_found(self, mocker: MockerFixture) -> None:
        """Calls delete_stalled with the correct map and flags when candidates exist."""
        qbt = mocker.MagicMock(spec=QBittorrentClient)
        torrent_map = {"hash1": {"state": "stalledDL", "name": "Movie", "tags": ""}}
        to_delete = {"hash1": {"name": "Movie", "age_mins": 200, "state": "stalledDL"}}
        db = mocker.MagicMock()

        qbt.list_by_category.return_value = torrent_map
        qbt.identify_for_deletion.return_value = to_delete

        _delete_stuck(
            qbt,
            db,
            _StuckConfig(
                state="stalledDL",
                filter_type="last_activity",
                max_mins=120,
                label="stalled",
                delete_data=False,
            ),
        )

        qbt.delete_stalled.assert_called_once_with(to_delete, state="stalledDL", delete_data=False)

    def test_passes_correct_state_and_filter_to_identify(self, mocker: MockerFixture) -> None:
        """Correct state, filter_type and max_mins are forwarded to identify_for_deletion."""
        qbt = mocker.MagicMock(spec=QBittorrentClient)
        torrent_map = {"hash1": {"state": "metaDL", "name": "Stuck", "tags": ""}}
        qbt.list_by_category.return_value = torrent_map
        qbt.identify_for_deletion.return_value = {}
        db = mocker.MagicMock()

        _delete_stuck(
            qbt,
            db,
            _StuckConfig(
                state="metaDL",
                filter_type="added_on",
                max_mins=30,
                label="metadata",
                delete_data=True,
            ),
        )

        qbt.identify_for_deletion.assert_called_once_with(
            torrent_map=torrent_map,
            state="metaDL",
            delay_max_mins=30,
            filter_type="added_on",
        )

    def test_delete_data_flag_forwarded_to_delete_stalled(self, mocker: MockerFixture) -> None:
        """The delete_data flag is forwarded to delete_stalled unchanged."""
        qbt = mocker.MagicMock(spec=QBittorrentClient)
        to_delete = {"hash1": {"name": "Movie", "age_mins": 200, "state": "metaDL"}}
        qbt.list_by_category.return_value = {"hash1": {"tags": ""}}
        qbt.identify_for_deletion.return_value = to_delete
        db = mocker.MagicMock()

        _delete_stuck(
            qbt,
            db,
            _StuckConfig(
                state="metaDL",
                filter_type="added_on",
                max_mins=30,
                label="metadata",
                delete_data=True,
            ),
        )

        qbt.delete_stalled.assert_called_once_with(to_delete, state="metaDL", delete_data=True)


# mark_stalled called on deletion


class TestMarkStalledOnDeletion:
    """Queue manager must call db.mark_stalled for each deleted torrent."""

    def test_mark_stalled_called_when_movarr_tag_present(self, mocker: MockerFixture) -> None:
        """mark_stalled is called with the movarr tag when a tagged torrent is deleted."""
        qbt = mocker.MagicMock(spec=QBittorrentClient)
        db = mocker.MagicMock()
        torrent_map = {"abc123": {"state": "stalledDL", "name": "Some Movie 2024", "tags": "movarr-uuid-stalled"}}
        to_delete = {"abc123": {"name": "Some Movie 2024", "age_mins": 999, "state": "stalledDL"}}
        qbt.list_by_category.return_value = torrent_map
        qbt.identify_for_deletion.return_value = to_delete

        _delete_stuck(
            qbt,
            db,
            _StuckConfig(
                state="stalledDL",
                filter_type="last_activity",
                max_mins=120,
                label="stalled",
                delete_data=False,
            ),
        )

        db.mark_stalled.assert_called_once_with("movarr-uuid-stalled")

    def test_mark_stalled_not_called_when_no_movarr_tag(self, mocker: MockerFixture) -> None:
        """mark_stalled is skipped when the deleted torrent has no movarr tag."""
        qbt = mocker.MagicMock(spec=QBittorrentClient)
        db = mocker.MagicMock()
        torrent_map = {"abc123": {"state": "stalledDL", "name": "Some Movie 2024", "tags": "other-tag"}}
        to_delete = {"abc123": {"name": "Some Movie 2024", "age_mins": 999, "state": "stalledDL"}}
        qbt.list_by_category.return_value = torrent_map
        qbt.identify_for_deletion.return_value = to_delete

        _delete_stuck(
            qbt,
            db,
            _StuckConfig(
                state="stalledDL",
                filter_type="last_activity",
                max_mins=120,
                label="stalled",
                delete_data=False,
            ),
        )

        db.mark_stalled.assert_not_called()

    def test_mark_stalled_called_for_meta_dl_torrent(self, mocker: MockerFixture) -> None:
        """mark_stalled is called for metaDL torrents as well."""
        qbt = mocker.MagicMock(spec=QBittorrentClient)
        db = mocker.MagicMock()
        torrent_map = {"def456": {"state": "metaDL", "name": "Other Movie 2023", "tags": "movarr-uuid-meta"}}
        to_delete = {"def456": {"name": "Other Movie 2023", "age_mins": 999, "state": "metaDL"}}
        qbt.list_by_category.return_value = torrent_map
        qbt.identify_for_deletion.return_value = to_delete

        _delete_stuck(
            qbt,
            db,
            _StuckConfig(
                state="metaDL",
                filter_type="added_on",
                max_mins=30,
                label="metadata",
                delete_data=False,
            ),
        )

        db.mark_stalled.assert_called_once_with("movarr-uuid-meta")
