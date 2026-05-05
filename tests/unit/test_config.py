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
    ProwlarrConfig,
    QueueManagementConfig,
    ScheduleTaskConfig,
    load_config,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture

# GeneralConfig defaults


class TestGeneralConfigDefaults:
    """GeneralConfig must have sane defaults."""

    def test_default_daemon_mode_is_foreground(self) -> None:
        cfg = GeneralConfig()
        assert cfg.daemon_mode == "foreground"

    def test_default_db_path_is_set(self) -> None:
        cfg = GeneralConfig()
        assert cfg.db_path

    def test_invalid_daemon_mode_raises(self) -> None:
        with pytest.raises(ValidationError):
            GeneralConfig(daemon_mode="invalid")


# QueueManagementConfig fields


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


# Config construction


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


# load_config from file


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


# Config migration


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
        assert cfg.general.config_version == "2.5.0"
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
        assert cfg.general.config_version == "2.5.0"

    def test_v2_config_needs_no_migration(self, tmp_path: Path) -> None:
        """A v2.0.0 config is migrated through to the latest version."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("general:\n  config_version: '2.0.0'\nnotification:\n  apprise_urls: []\n")
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.5.0"

    def test_v2_config_migrated_to_v21(self, tmp_path: Path) -> None:
        """A v2.0.0 config is migrated to v2.3.0, adding database expiry fields."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("general:\n  config_version: '2.0.0'\nnotification:\n  apprise_urls: []\n")
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.5.0"
        assert cfg.database.stalled_expiry_days == 7

    def test_v2_migration_creates_backup(self, tmp_path: Path) -> None:
        """A backup file config.yml.bak.2.0.0 is created before v2→v2.1 migration."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("general:\n  config_version: '2.0.0'\nnotification:\n  apprise_urls: []\n")
        load_config(str(cfg_file))
        backup = tmp_path / "config.yml.bak.2.0.0"
        assert backup.exists()

    def test_v21_config_migrated_to_v22(self, tmp_path: Path) -> None:
        """A v2.1.0 config is migrated to v2.2.0, adding database.failed_expiry_days."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(
            "general:\n  config_version: '2.1.0'\nnotification:\n  apprise_urls: []\n"
            "database:\n  stalled_expiry_days: 7\n"
        )
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.5.0"
        assert cfg.database.failed_expiry_days == 7

    def test_v21_migration_creates_backup(self, tmp_path: Path) -> None:
        """A backup file config.yml.bak.2.1.0 is created before v2.1→v2.2 migration."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(
            "general:\n  config_version: '2.1.0'\nnotification:\n  apprise_urls: []\n"
            "database:\n  stalled_expiry_days: 7\n"
        )
        load_config(str(cfg_file))
        backup = tmp_path / "config.yml.bak.2.1.0"
        assert backup.exists()

    def test_v22_config_migrated_to_v23(self, tmp_path: Path) -> None:
        """A v2.2.0 config is migrated all the way to the latest version."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(
            "general:\n  config_version: '2.2.0'\nnotification:\n  apprise_urls: []\n"
            "database:\n  stalled_expiry_days: 7\n  failed_expiry_days: 7\n"
        )
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.5.0"
        assert cfg.database.passed_expiry_days == 30

    def test_v22_migration_creates_backup(self, tmp_path: Path) -> None:
        """A backup file config.yml.bak.2.2.0 is created before v2.2→v2.3 migration."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(
            "general:\n  config_version: '2.2.0'\nnotification:\n  apprise_urls: []\n"
            "database:\n  stalled_expiry_days: 7\n  failed_expiry_days: 7\n"
        )
        load_config(str(cfg_file))
        backup = tmp_path / "config.yml.bak.2.2.0"
        assert backup.exists()

    def test_v23_config_migrated_to_v24(self, tmp_path: Path) -> None:
        """A v2.3.0 config is migrated to v2.4.0, adding run_on_start: true to all tasks."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(
            "general:\n  config_version: '2.3.0'\nnotification:\n  apprise_urls: []\n"
            "database:\n  stalled_expiry_days: 7\n  failed_expiry_days: 7\n  passed_expiry_days: 30\n"
        )
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.5.0"
        assert cfg.schedule.acquisition.run_on_start is True
        assert cfg.schedule.queue_management.run_on_start is True
        assert cfg.schedule.post_processing.run_on_start is True

    def test_v23_migration_creates_backup(self, tmp_path: Path) -> None:
        """A backup file config.yml.bak.2.3.0 is created before v2.3→v2.4 migration."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("general:\n  config_version: '2.3.0'\nnotification:\n  apprise_urls: []\n")
        load_config(str(cfg_file))
        backup = tmp_path / "config.yml.bak.2.3.0"
        assert backup.exists()

    def test_v23_migration_writes_run_on_start_to_disk(self, tmp_path: Path) -> None:
        """After migration the on-disk YAML contains run_on_start: true for all tasks."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("general:\n  config_version: '2.3.0'\nnotification:\n  apprise_urls: []\n")
        load_config(str(cfg_file))
        raw = yaml.safe_load(cfg_file.read_text())
        schedule = raw.get("schedule", {})
        for task in ("acquisition", "queue_management", "post_processing"):
            assert schedule.get(task, {}).get("run_on_start") is True


# DatabaseConfig defaults


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

    def test_failed_expiry_days_defaults_to_7(self) -> None:
        """failed_expiry_days defaults to 7 when not specified."""
        cfg = DatabaseConfig()
        assert cfg.failed_expiry_days == 7

    def test_failed_expiry_days_zero_allowed(self) -> None:
        """failed_expiry_days of 0 (disable expiry) is valid."""
        cfg = DatabaseConfig(failed_expiry_days=0)
        assert cfg.failed_expiry_days == 0

    def test_passed_expiry_days_defaults_to_30(self) -> None:
        """passed_expiry_days defaults to 30 when not specified."""
        cfg = DatabaseConfig()
        assert cfg.passed_expiry_days == 30

    def test_passed_expiry_days_zero_allowed(self) -> None:
        """passed_expiry_days of 0 (disable expiry) is valid."""
        cfg = DatabaseConfig(passed_expiry_days=0)
        assert cfg.passed_expiry_days == 0

    def test_config_database_field_present(self) -> None:
        """Top-level Config has a database field with DatabaseConfig defaults."""
        cfg = Config()
        assert cfg.database.stalled_expiry_days == 7
        assert cfg.database.failed_expiry_days == 7
        assert cfg.database.passed_expiry_days == 30


# _migrate_config — OSError during backup


class TestMigrateConfigBackupOsError:
    """_run_migrations continues without backup when shutil.copy2 raises OSError."""

    def test_migration_proceeds_when_backup_fails(self, mocker: MockerFixture, tmp_path: Path) -> None:
        from movarr.config import MIGRATIONS, _run_migrations

        config_path = tmp_path / "config.yml"
        # Use a known migration key ("1.0.0")
        first_key = next(iter(MIGRATIONS))
        raw: dict = {"general": {"config_version": first_key}}
        import yaml as _yaml

        config_path.write_text(_yaml.dump(raw))
        mocker.patch("movarr.config.shutil.copy2", side_effect=OSError("no space"))
        mock_warning = mocker.patch("movarr.config.logger.warning")
        result = _run_migrations(raw, config_path)
        assert result is not None
        mock_warning.assert_called_once()


# create_default_config — early return when file already exists


class TestCreateDefaultConfigExists:
    """create_default_config does nothing when the file already exists."""

    def test_no_write_when_file_exists(self, tmp_path: Path) -> None:
        from movarr.config import create_default_config

        config_path = tmp_path / "config.yml"
        original = "# existing config\n"
        config_path.write_text(original)
        create_default_config(config_path)
        assert config_path.read_text() == original


# ScheduleTaskConfig.run_on_start


class TestScheduleTaskConfigRunOnStart:
    """ScheduleTaskConfig must expose a run_on_start boolean defaulting to False."""

    def test_run_on_start_defaults_to_true(self) -> None:
        """run_on_start must default to True so tasks fire immediately on first start."""
        cfg = ScheduleTaskConfig()
        assert cfg.run_on_start is True

    def test_run_on_start_can_be_set_false(self) -> None:
        """run_on_start can be set to False to wait for the first interval."""
        cfg = ScheduleTaskConfig(run_on_start=False)
        assert cfg.run_on_start is False

    def test_all_three_schedule_tasks_default_true(self) -> None:
        """All three tasks inside ScheduleConfig default to run_on_start=True."""
        cfg = Config()
        assert cfg.schedule.acquisition.run_on_start is True
        assert cfg.schedule.queue_management.run_on_start is True
        assert cfg.schedule.post_processing.run_on_start is True


# ProwlarrConfig defaults


class TestProwlarrConfigDefaults:
    """ProwlarrConfig must have sane defaults."""

    def test_default_host(self) -> None:
        cfg = ProwlarrConfig()
        assert cfg.host == "localhost"

    def test_default_port(self) -> None:
        cfg = ProwlarrConfig()
        assert cfg.port == 9696

    def test_default_api_key_is_empty(self) -> None:
        cfg = ProwlarrConfig()
        assert cfg.api_key == ""

    def test_default_read_timeout(self) -> None:
        cfg = ProwlarrConfig()
        assert cfg.read_timeout == 60.0


# Config migration v2.4.0 → v2.5.0


class TestMigrationV24toV25:
    """Migration v2.4.0 → v2.5.0 adds Prowlarr config and prowlarr_indexer."""

    def _v24_config(self, tmp_path: Path) -> Path:
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("general:\n  config_version: '2.4.0'\nnotification:\n  apprise_urls: []\n")
        return cfg_file

    def test_v24_config_migrated_to_v25(self, tmp_path: Path) -> None:
        """A v2.4.0 config is migrated to v2.5.0, adding Prowlarr fields."""
        cfg_file = self._v24_config(tmp_path)
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.5.0"
        assert cfg.index_proxy.prowlarr.host == "localhost"
        assert cfg.index_proxy.prowlarr.port == 9696
        assert cfg.index_site.prowlarr_indexer == "all"

    def test_v24_migration_creates_backup(self, tmp_path: Path) -> None:
        """A backup file config.yml.bak.2.4.0 is created before migration."""
        cfg_file = self._v24_config(tmp_path)
        load_config(str(cfg_file))
        assert (tmp_path / "config.yml.bak.2.4.0").exists()

    def test_v24_migration_writes_prowlarr_block_to_disk(self, tmp_path: Path) -> None:
        """After migration the on-disk YAML contains the prowlarr block."""
        cfg_file = self._v24_config(tmp_path)
        load_config(str(cfg_file))
        raw = yaml.safe_load(cfg_file.read_text())
        assert "prowlarr" in raw.get("index_proxy", {})
        assert raw["index_proxy"]["prowlarr"]["port"] == 9696

    def test_v24_migration_writes_prowlarr_indexer_to_disk(self, tmp_path: Path) -> None:
        """After migration the on-disk YAML contains prowlarr_indexer: all."""
        cfg_file = self._v24_config(tmp_path)
        load_config(str(cfg_file))
        raw = yaml.safe_load(cfg_file.read_text())
        assert raw.get("index_site", {}).get("prowlarr_indexer") == "all"

    def test_v24_migration_preserves_existing_jackett_config(self, tmp_path: Path) -> None:
        """Existing jackett config values are not overwritten by migration."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(
            "general:\n  config_version: '2.4.0'\n"
            "index_proxy:\n"
            "  selected: jackett\n"
            "  jackett:\n"
            "    host: myjackett\n"
            "    port: 9117\n"
        )
        cfg = load_config(str(cfg_file))
        assert cfg.index_proxy.jackett.host == "myjackett"

    def test_existing_config_at_v25_needs_no_migration(self, tmp_path: Path) -> None:
        """A config already at v2.5.0 is not re-migrated."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("general:\n  config_version: '2.5.0'\n")
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.5.0"
        backup = tmp_path / "config.yml.bak.2.5.0"
        assert not backup.exists()


# IndexSiteConfig — prowlarr_indexer default


class TestIndexSiteConfigProwlarrIndexer:
    """IndexSiteConfig must expose prowlarr_indexer defaulting to 'all'."""

    def test_default_prowlarr_indexer_is_all(self) -> None:
        cfg = Config()
        assert cfg.index_site.prowlarr_indexer == "all"

    def test_numeric_prowlarr_indexer_accepted(self) -> None:
        cfg = Config.model_validate({"index_site": {"prowlarr_indexer": "7"}})
        assert cfg.index_site.prowlarr_indexer == "7"

    def test_invalid_prowlarr_indexer_raises(self) -> None:
        """A non-numeric, non-'all' value raises ValidationError at config construction."""
        with pytest.raises(ValidationError, match="prowlarr_indexer"):
            Config.model_validate({"index_site": {"prowlarr_indexer": "my-tracker"}})


# IndexProxyConfig — selected validator


class TestIndexProxySelectedValidator:
    """index_proxy.selected must be validated at load time."""

    def test_valid_jackett_accepted(self) -> None:
        cfg = Config()
        cfg.index_proxy.selected = "jackett"
        assert cfg.index_proxy.selected == "jackett"

    def test_invalid_selected_raises_on_construction(self) -> None:
        """An unknown proxy name raises ValidationError at config construction time."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="index_proxy.selected"):
            Config.model_validate({"index_proxy": {"selected": "notaproxy"}})
