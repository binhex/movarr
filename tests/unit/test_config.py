"""Unit tests for movarr.config — loading, validation, and defaults."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml
from pydantic import ValidationError

from movarr.config import (
    Config,
    DatabaseConfig,
    GeneralConfig,
    QueueManagementConfig,
    load_config,
)

if TYPE_CHECKING:
    from pathlib import Path

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
        with pytest.raises(ValidationError):
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

    def test_loads_empty_file_with_defaults(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("{}\n")
        cfg = load_config(str(cfg_file))
        assert isinstance(cfg, Config)

    def test_partial_override_preserves_defaults(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("general:\n  log_level_console: debug\n")
        cfg = load_config(str(cfg_file))
        assert cfg.general.log_level_console == "debug"
        assert cfg.general.daemon_mode == "foreground"  # default unchanged

    def test_creates_file_if_absent(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.yml"
        cfg = load_config(str(cfg_file))
        assert cfg_file.exists()
        assert isinstance(cfg, Config)

    def test_invalid_daemon_mode_in_file_raises(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("general:\n  daemon_mode: bad_value\n")
        with pytest.raises(ValidationError):
            load_config(str(cfg_file))


# ---------------------------------------------------------------------------
# Config migration
# ---------------------------------------------------------------------------


class TestConfigMigration:
    """load_config must auto-migrate older config schemas on startup."""

    def _v1_config(self, tmp_path: Path) -> Path:
        """Write a v1.0.0 config (with notification.email block) to tmp_path."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(
            "general:\n"
            "  config_version: '1.0.0'\n"
            "notification:\n"
            "  email:\n"
            "    enabled: false\n"
            "    host: smtp.example.com\n"
            "    port: 587\n"
        )
        return cfg_file

    def test_v1_config_is_migrated_to_v2(self, tmp_path: Path) -> None:
        """A v1.0.0 config with notification.email is migrated through all versions."""
        cfg_file = self._v1_config(tmp_path)
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.1.0"
        assert cfg.notification.apprise_urls == []

    def test_v1_migration_removes_email_from_disk(self, tmp_path: Path) -> None:
        """After migration the on-disk YAML no longer contains notification.email."""
        cfg_file = self._v1_config(tmp_path)
        load_config(str(cfg_file))
        raw = yaml.safe_load(cfg_file.read_text())
        assert "email" not in raw.get("notification", {})
        assert "apprise_urls" in raw.get("notification", {})

    def test_v1_migration_creates_backup(self, tmp_path: Path) -> None:
        """A backup file config.yml.bak.1.0.0 is created before migration."""
        cfg_file = self._v1_config(tmp_path)
        load_config(str(cfg_file))
        backup = tmp_path / "config.yml.bak.1.0.0"
        assert backup.exists()
        raw = yaml.safe_load(backup.read_text())
        assert "email" in raw.get("notification", {})

    def test_no_version_key_treated_as_v1(self, tmp_path: Path) -> None:
        """A config without general.config_version is treated as v1.0.0 and migrated."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("notification:\n  email:\n    enabled: false\n")
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.1.0"

    def test_v2_config_needs_no_migration(self, tmp_path: Path) -> None:
        """A v2.0.0 config is migrated to v2.1.0 (next version)."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("general:\n  config_version: '2.0.0'\nnotification:\n  apprise_urls: []\n")
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.1.0"

    def test_v2_config_migrated_to_v21(self, tmp_path: Path) -> None:
        """A v2.0.0 config is migrated to v2.1.0, adding database.stalled_expiry_days."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("general:\n  config_version: '2.0.0'\nnotification:\n  apprise_urls: []\n")
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.1.0"
        assert cfg.database.stalled_expiry_days == 7

    def test_v2_migration_creates_backup(self, tmp_path: Path) -> None:
        """A backup file config.yml.bak.2.0.0 is created before v2→v2.1 migration."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("general:\n  config_version: '2.0.0'\nnotification:\n  apprise_urls: []\n")
        load_config(str(cfg_file))
        backup = tmp_path / "config.yml.bak.2.0.0"
        assert backup.exists()


# ---------------------------------------------------------------------------
# DatabaseConfig defaults
# ---------------------------------------------------------------------------


class TestDatabaseConfigDefaults:
    """DatabaseConfig must have sane defaults."""

    def test_stalled_expiry_days_defaults_to_7(self) -> None:
        """stalled_expiry_days defaults to 7 when not specified."""
        cfg = DatabaseConfig()
        assert cfg.stalled_expiry_days == 7

    def test_stalled_expiry_days_zero_allowed(self) -> None:
        """stalled_expiry_days of 0 (disable expiry) is valid."""
        cfg = DatabaseConfig(stalled_expiry_days=0)
        assert cfg.stalled_expiry_days == 0

    def test_config_database_field_present(self) -> None:
        """Top-level Config has a database field with DatabaseConfig defaults."""
        cfg = Config()
        assert cfg.database.stalled_expiry_days == 7
