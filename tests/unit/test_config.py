"""Unit tests for movarr.config — loading, validation, and defaults."""

from __future__ import annotations

import pytest

from movarr.config import (
    Config,
    GeneralConfig,
    QueueManagementConfig,
    load_config,
)

# ---------------------------------------------------------------------------
# GeneralConfig defaults
# ---------------------------------------------------------------------------


class TestGeneralConfigDefaults:
    """GeneralConfig must have sane defaults."""

    def test_default_daemon_mode_is_foreground(self) -> None:
        cfg = GeneralConfig()
        assert cfg.daemon_mode == "foreground"

    def test_default_db_path_is_set(self) -> None:
        cfg = GeneralConfig()
        assert cfg.db_path

    def test_default_ffprobe_path_is_set(self) -> None:
        cfg = GeneralConfig()
        assert cfg.ffprobe_path

    def test_invalid_daemon_mode_raises(self) -> None:
        with pytest.raises(Exception):
            GeneralConfig(daemon_mode="invalid")


# ---------------------------------------------------------------------------
# QueueManagementConfig fields
# ---------------------------------------------------------------------------


class TestQueueManagementConfig:
    """QueueManagementConfig must expose the required delete-data fields."""

    def test_has_metadata_delete_torrent_data(self) -> None:
        cfg = QueueManagementConfig()
        assert hasattr(cfg, "metadata_delete_torrent_data")
        assert isinstance(cfg.metadata_delete_torrent_data, bool)

    def test_has_stalled_delete_torrent_data(self) -> None:
        cfg = QueueManagementConfig()
        assert hasattr(cfg, "stalled_delete_torrent_data")
        assert isinstance(cfg.stalled_delete_torrent_data, bool)

    def test_default_delete_data_is_false(self) -> None:
        cfg = QueueManagementConfig()
        assert cfg.stalled_delete_torrent_data is False
        assert cfg.metadata_delete_torrent_data is False


# ---------------------------------------------------------------------------
# Config construction
# ---------------------------------------------------------------------------


class TestConfigConstruction:
    """Root Config must build cleanly with all sub-models."""

    def test_default_config_is_valid(self) -> None:
        cfg = Config()
        assert cfg.general is not None
        assert cfg.schedule is not None
        assert cfg.filters is not None
        assert cfg.index_site is not None
        assert cfg.queue_management is not None
        assert cfg.post_process is not None

    def test_schedule_has_three_tasks(self) -> None:
        cfg = Config()
        assert cfg.schedule.acquisition is not None
        assert cfg.schedule.queue_management is not None
        assert cfg.schedule.post_processing is not None

    def test_index_site_has_default_search_criteria(self) -> None:
        cfg = Config()
        assert len(cfg.index_site.search) > 0

    def test_index_site_default_jackett_indexer(self) -> None:
        cfg = Config()
        assert cfg.index_site.jackett_indexer == "all"


# ---------------------------------------------------------------------------
# load_config from file
# ---------------------------------------------------------------------------


class TestLoadConfig:
    """load_config must merge YAML with defaults and return a validated Config."""

    def test_loads_empty_file_with_defaults(self, tmp_path) -> None:
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("{}\n")
        cfg = load_config(str(cfg_file))
        assert isinstance(cfg, Config)

    def test_partial_override_preserves_defaults(self, tmp_path) -> None:
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("general:\n  log_level_console: debug\n")
        cfg = load_config(str(cfg_file))
        assert cfg.general.log_level_console == "debug"
        assert cfg.general.daemon_mode == "foreground"  # default unchanged

    def test_creates_file_if_absent(self, tmp_path) -> None:
        cfg_file = tmp_path / "config.yml"
        cfg = load_config(str(cfg_file))
        assert cfg_file.exists()
        assert isinstance(cfg, Config)

    def test_invalid_daemon_mode_in_file_raises(self, tmp_path) -> None:
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("general:\n  daemon_mode: bad_value\n")
        with pytest.raises(Exception):
            load_config(str(cfg_file))
