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
    _migrate_v28_to_v29,
    _migrate_v29_to_v210,
    _migrate_v210_to_v211,
    _migrate_v211_to_v212,
    _migrate_v212_to_v213,
    _migrate_v213_to_v214,
    _migrate_v214_to_v215,
    _migrate_v215_to_v216,
    _migrate_v216_to_v217,
    _migrate_v217_to_v218,
    _migrate_v218_to_v219,
    _strip_path_basename,
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

    def test_log_level_file_valid_accepted(self) -> None:
        cfg = GeneralConfig.model_validate({"log_level_file": "debug"})
        assert cfg.log_level_file == "DEBUG"

    def test_log_level_file_invalid_raises(self) -> None:
        with pytest.raises(ValidationError):
            GeneralConfig.model_validate({"log_level_file": "verbose"})


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

    def test_default_delete_data_is_true(self) -> None:
        """Stalled and metaDL torrents must delete their data by default.

        Incomplete partial downloads have no value; leaving them on disk wastes
        space.  The correct default is True so movarr instructs qBittorrent to
        remove the files when it removes the torrent from the queue.
        """
        cfg = QueueManagementConfig()
        assert cfg.stalled_delete_torrent_data is True
        assert cfg.metadata_delete_torrent_data is True


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
        assert cfg.general.log_level_console == "DEBUG"
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
        assert cfg.general.config_version == "2.19.0"
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
        assert cfg.general.config_version == "2.19.0"

    def test_v2_config_migrated_to_v21(self, tmp_path: Path) -> None:
        """A v2.0.0 config is migrated to v2.3.0, adding database expiry fields."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("general:\n  config_version: '2.0.0'\nnotification:\n  apprise_urls: []\n")
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.19.0"
        assert cfg.database.stalled_expiry_days == 7

    def test_v21_config_migrated_to_v22(self, tmp_path: Path) -> None:
        """A v2.1.0 config is migrated to v2.2.0, adding database.failed_expiry_days."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(
            "general:\n  config_version: '2.1.0'\nnotification:\n  apprise_urls: []\n"
            "database:\n  stalled_expiry_days: 7\n"
        )
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.19.0"
        assert cfg.database.failed_expiry_days == 7

    def test_v22_config_migrated_to_v23(self, tmp_path: Path) -> None:
        """A v2.2.0 config is migrated all the way to the latest version."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(
            "general:\n  config_version: '2.2.0'\nnotification:\n  apprise_urls: []\n"
            "database:\n  stalled_expiry_days: 7\n  failed_expiry_days: 7\n"
        )
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.19.0"
        assert cfg.database.passed_expiry_days == 30

    def test_v23_config_migrated_to_v24(self, tmp_path: Path) -> None:
        """A v2.3.0 config is migrated to v2.4.0, adding run_on_start: true to all tasks."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(
            "general:\n  config_version: '2.3.0'\nnotification:\n  apprise_urls: []\n"
            "database:\n  stalled_expiry_days: 7\n  failed_expiry_days: 7\n  passed_expiry_days: 30\n"
        )
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.19.0"
        assert cfg.schedule.acquisition.run_on_start is True
        assert cfg.schedule.queue_management.run_on_start is True
        assert cfg.schedule.post_processing.run_on_start is True

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
        assert cfg.general.config_version == "2.19.0"
        assert cfg.index_proxy.prowlarr.host == "localhost"
        assert cfg.index_proxy.prowlarr.port == 9696
        assert cfg.index_site.prowlarr_indexer == "all"

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

    def test_existing_config_at_v25_is_migrated_to_v26(self, tmp_path: Path) -> None:
        """A config at v2.5.0 is migrated to v2.6.0 (ffprobe_path removal)."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("general:\n  config_version: '2.5.0'\n")
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.19.0"
        backup = tmp_path / "config.yml.bak.2.5.0"
        assert backup.exists()


# Config migration v2.5.0 → v2.6.0


class TestMigrationV25toV26:
    """Migration v2.5.0 → v2.6.0 removes deprecated ffprobe_path."""

    def _v25_config(self, tmp_path: Path) -> Path:
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("general:\n  config_version: '2.5.0'\n  ffprobe_path: /usr/bin/ffprobe\n")
        return cfg_file

    def test_v25_config_migrated_to_v26(self, tmp_path: Path) -> None:
        """A v2.5.0 config with ffprobe_path is migrated to v2.6.0 and the field is removed."""
        cfg_file = self._v25_config(tmp_path)
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.19.0"
        raw = yaml.safe_load(cfg_file.read_text())
        assert "ffprobe_path" not in raw.get("general", {})

    def test_existing_config_at_v26_is_migrated_to_v28(self, tmp_path: Path) -> None:
        """A config at v2.6.0 is migrated through v2.7.0 to v2.8.0."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(
            "general:\n  config_version: '2.6.0'\n"
            "filters:\n"
            "  good_country_list: [us]\n"
            "  good_language_list: [en]\n"
            "  good_imdb_title_type_list: [movie]\n"
        )
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.19.0"
        assert cfg.filters.allow_country_list == ["us"]
        assert cfg.filters.allow_language_list == ["en"]
        assert cfg.filters.allow_imdb_title_type_list == ["movie"]
        backup = tmp_path / "config.yml.bak.2.6.0"
        assert backup.exists()

    def test_existing_config_at_v27_is_migrated_to_v28(self, tmp_path: Path) -> None:
        """A config at v2.7.0 is migrated to v2.8.0 (bad_ → reject_ rename)."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(
            "general:\n  config_version: '2.7.0'\n"
            "filters:\n"
            "  bad_index_title_list: [xvid]\n"
            "  bad_genre_list: [horror]\n"
            "  bad_movie_title_list: [Bad Movie]\n"
        )
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.19.0"
        assert cfg.filters.reject_index_title_list == ["xvid"]
        assert cfg.filters.reject_genre_list == ["horror"]
        assert cfg.filters.reject_movie_title_list == ["Bad Movie"]
        backup = tmp_path / "config.yml.bak.2.7.0"
        assert backup.exists()

    def test_existing_config_at_v28_is_migrated_to_v29(self, tmp_path: Path) -> None:
        """A config at v2.8.0 is migrated to v2.9.0, adding index_proxy_alert_hours."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("general:\n  config_version: '2.8.0'\n")
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.19.0"
        assert cfg.notification.index_proxy_alert_hours == 0
        backup = tmp_path / "config.yml.bak.2.8.0"
        assert backup.exists()


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


# NotificationConfig — index_proxy_alert_hours field


class TestIndexProxyAlertHoursConfig:
    """Tests for notification.index_proxy_alert_hours config field."""

    def test_default_is_zero(self) -> None:
        """index_proxy_alert_hours defaults to 0 (feature disabled)."""
        config = Config()
        assert config.notification.index_proxy_alert_hours == 0

    def test_parses_float_value(self) -> None:
        """A float value is parsed and stored correctly."""
        config = Config.model_validate({"notification": {"apprise_urls": [], "index_proxy_alert_hours": 2.5}})
        assert config.notification.index_proxy_alert_hours == 2.5  # noqa: PLR2004

    def test_parses_integer_as_float(self) -> None:
        """An integer value (e.g. 2) is accepted and stored as float."""
        config = Config.model_validate({"notification": {"apprise_urls": [], "index_proxy_alert_hours": 2}})
        assert config.notification.index_proxy_alert_hours == 2.0  # noqa: PLR2004

    def test_parses_zero_disables_feature(self) -> None:
        """Explicitly setting 0 keeps the feature disabled."""
        config = Config.model_validate({"notification": {"apprise_urls": [], "index_proxy_alert_hours": 0}})
        assert config.notification.index_proxy_alert_hours == 0


# Migration v2.8.0 -> v2.9.0


class TestMigrationV28ToV29:
    """Tests for the v2.8.0 -> v2.9.0 config migration."""

    def test_adds_index_proxy_alert_hours_zero(self) -> None:
        """Migration inserts index_proxy_alert_hours: 0 into notification block."""
        raw = {"general": {"config_version": "2.8.0"}, "notification": {"apprise_urls": []}}
        result = _migrate_v28_to_v29(raw)
        assert result["notification"]["index_proxy_alert_hours"] == 0

    def test_bumps_version_to_v29(self) -> None:
        """Migration sets config_version to 2.9.0."""
        raw = {"general": {"config_version": "2.8.0"}}
        result = _migrate_v28_to_v29(raw)
        assert result["general"]["config_version"] == "2.9.0"

    def test_does_not_overwrite_existing_value(self) -> None:
        """Migration does not clobber an existing index_proxy_alert_hours value."""
        raw = {
            "general": {"config_version": "2.8.0"},
            "notification": {"apprise_urls": [], "index_proxy_alert_hours": 4.0},
        }
        result = _migrate_v28_to_v29(raw)
        assert result["notification"]["index_proxy_alert_hours"] == 4.0  # noqa: PLR2004


class TestTorrentClientAlertHoursConfig:
    """Tests for notification.torrent_client_alert_hours config field."""

    def test_default_is_zero(self) -> None:
        """torrent_client_alert_hours defaults to 0 (feature disabled)."""
        assert Config().notification.torrent_client_alert_hours == 0

    def test_parses_float_value(self) -> None:
        """A float value is parsed and stored correctly."""
        config = Config.model_validate({"notification": {"apprise_urls": [], "torrent_client_alert_hours": 3.5}})
        assert config.notification.torrent_client_alert_hours == 3.5  # noqa: PLR2004

    def test_parses_zero_disables(self) -> None:
        """Zero keeps the feature disabled."""
        config = Config.model_validate({"notification": {"apprise_urls": [], "torrent_client_alert_hours": 0}})
        assert config.notification.torrent_client_alert_hours == 0


class TestMigrationV29ToV210:
    """Tests for the v2.9.0 -> v2.10.0 config migration."""

    def test_adds_torrent_client_alert_hours_zero(self) -> None:
        """Migration inserts torrent_client_alert_hours: 0 into notification block."""
        raw = {"general": {"config_version": "2.9.0"}, "notification": {"apprise_urls": []}}
        result = _migrate_v29_to_v210(raw)
        assert result["notification"]["torrent_client_alert_hours"] == 0

    def test_bumps_version_to_v210(self) -> None:
        """Migration sets config_version to 2.10.0."""
        raw = {"general": {"config_version": "2.9.0"}}
        assert _migrate_v29_to_v210(raw)["general"]["config_version"] == "2.10.0"

    def test_does_not_overwrite_existing_value(self) -> None:
        """Migration does not clobber a pre-existing torrent_client_alert_hours value."""
        raw = {
            "general": {"config_version": "2.9.0"},
            "notification": {"apprise_urls": [], "torrent_client_alert_hours": 4.0},
        }
        assert _migrate_v29_to_v210(raw)["notification"]["torrent_client_alert_hours"] == 4.0  # noqa: PLR2004


class TestMigrationV210ToV211:
    """Tests for the v2.10.0 -> v2.11.0 config migration."""

    def test_adds_log_path(self) -> None:
        """Migration inserts general.log_path with empty-string default."""
        raw: dict = {"general": {"config_version": "2.10.0"}}
        result = _migrate_v210_to_v211(raw)
        assert result["general"]["log_path"] == ""

    def test_adds_pid_path(self) -> None:
        """Migration inserts general.pid_path with empty-string default."""
        raw: dict = {"general": {"config_version": "2.10.0"}}
        result = _migrate_v210_to_v211(raw)
        assert result["general"]["pid_path"] == ""

    def test_bumps_version_to_v211(self) -> None:
        raw: dict = {"general": {"config_version": "2.10.0"}}
        assert _migrate_v210_to_v211(raw)["general"]["config_version"] == "2.11.0"

    def test_does_not_overwrite_existing_log_path(self) -> None:
        """Migration does not clobber a pre-existing log_path value."""
        raw: dict = {"general": {"config_version": "2.10.0", "log_path": "/var/log/movarr.log"}}
        assert _migrate_v210_to_v211(raw)["general"]["log_path"] == "/var/log/movarr.log"

    def test_does_not_overwrite_existing_pid_path(self) -> None:
        raw: dict = {"general": {"config_version": "2.10.0", "pid_path": "/run/movarr.pid"}}
        assert _migrate_v210_to_v211(raw)["general"]["pid_path"] == "/run/movarr.pid"


class TestDefaultSearchConfig:
    """Auto-generated config has a single 1080p search tier."""

    def test_default_search_has_one_tier(self) -> None:
        cfg = Config()
        assert len(cfg.index_site.search) == 1

    def test_default_search_criteria_is_1080p(self) -> None:
        cfg = Config()
        assert cfg.index_site.search[0].criteria == "1080p"

    def test_default_search_category_is_2000_5000(self) -> None:
        cfg = Config()
        assert cfg.index_site.search[0].category == "2000,5000"


class TestMigrationV211ToV212:
    """Tests for the v2.11.0 -> v2.12.0 config migration."""

    def test_sets_stalled_delete_torrent_data_true(self) -> None:
        """Migration corrects the old False default for stalled torrents."""
        raw: dict = {"general": {"config_version": "2.11.0"}}
        result = _migrate_v211_to_v212(raw)
        assert result["queue_management"]["stalled_delete_torrent_data"] is True

    def test_sets_metadata_delete_torrent_data_true(self) -> None:
        """Migration corrects the old False default for metaDL torrents."""
        raw: dict = {"general": {"config_version": "2.11.0"}}
        result = _migrate_v211_to_v212(raw)
        assert result["queue_management"]["metadata_delete_torrent_data"] is True

    def test_overwrites_explicit_false_value(self) -> None:
        """Migration explicitly sets True even if the config had False."""
        raw: dict = {
            "general": {"config_version": "2.11.0"},
            "queue_management": {
                "stalled_delete_torrent_data": False,
                "metadata_delete_torrent_data": False,
            },
        }
        result = _migrate_v211_to_v212(raw)
        assert result["queue_management"]["stalled_delete_torrent_data"] is True
        assert result["queue_management"]["metadata_delete_torrent_data"] is True

    def test_bumps_version_to_v212(self) -> None:
        raw: dict = {"general": {"config_version": "2.11.0"}}
        assert _migrate_v211_to_v212(raw)["general"]["config_version"] == "2.12.0"


class TestPostProcessConfigDefaults:
    """PostProcessConfig.delete_lower_quality must default to False."""

    def test_delete_lower_quality_defaults_to_false(self) -> None:
        from movarr.config import PostProcessConfig

        cfg = PostProcessConfig()
        assert cfg.delete_lower_quality is False

    def test_hook_timeout_mins_defaults_to_5(self) -> None:
        """hook_timeout_mins replaces hook_timeout_secs with minutes unit."""
        from movarr.config import PostProcessHooksConfig

        cfg = PostProcessHooksConfig()
        assert cfg.hook_timeout_mins == 5.0
        assert not hasattr(cfg, "hook_timeout_secs")

    def test_delete_lower_quality_can_be_enabled(self) -> None:
        from movarr.config import PostProcessConfig

        cfg = PostProcessConfig(delete_lower_quality=True)
        assert cfg.delete_lower_quality is True


class TestMigrationV212ToV213:
    """Tests for the v2.12.0 -> v2.13.0 config migration."""

    def test_adds_delete_lower_quality_false(self) -> None:
        """Migration inserts post_process.delete_lower_quality: False."""
        raw: dict = {"general": {"config_version": "2.12.0"}}
        result = _migrate_v212_to_v213(raw)
        assert result["post_process"]["delete_lower_quality"] is False

    def test_does_not_overwrite_existing_value(self) -> None:
        """Migration does not clobber a pre-existing delete_lower_quality value."""
        raw: dict = {
            "general": {"config_version": "2.12.0"},
            "post_process": {"delete_lower_quality": True},
        }
        result = _migrate_v212_to_v213(raw)
        assert result["post_process"]["delete_lower_quality"] is True

    def test_bumps_version_to_v213(self) -> None:
        raw: dict = {"general": {"config_version": "2.12.0"}}
        assert _migrate_v212_to_v213(raw)["general"]["config_version"] == "2.13.0"

    def test_preserves_existing_post_process_keys(self) -> None:
        """Migration does not drop existing post_process settings."""
        raw: dict = {
            "general": {"config_version": "2.12.0"},
            "post_process": {"remove_completed": False},
        }
        result = _migrate_v212_to_v213(raw)
        assert result["post_process"]["remove_completed"] is False
        assert "delete_lower_quality" in result["post_process"]


class TestMigrationV213ToV214:
    """Tests for the v2.13.0 -> v2.14.0 config migration."""

    def test_adds_hooks_block(self) -> None:
        """Migration inserts post_process.hooks with all three subfields empty."""
        raw: dict = {"general": {"config_version": "2.13.0"}}
        result = _migrate_v213_to_v214(raw)
        assert result["post_process"]["hooks"] == {"post_copy": "", "pre_delete": "", "post_delete": ""}

    def test_does_not_overwrite_existing_hooks(self) -> None:
        """Migration does not clobber a pre-existing hooks block."""
        raw: dict = {
            "general": {"config_version": "2.13.0"},
            "post_process": {"hooks": {"pre_delete": "chattr -i {dir}/*"}},
        }
        result = _migrate_v213_to_v214(raw)
        assert result["post_process"]["hooks"]["pre_delete"] == "chattr -i {dir}/*"

    def test_bumps_version_to_v214(self) -> None:
        raw: dict = {"general": {"config_version": "2.13.0"}}
        assert _migrate_v213_to_v214(raw)["general"]["config_version"] == "2.14.0"

    def test_preserves_existing_post_process_keys(self) -> None:
        """Migration does not drop existing post_process settings."""
        raw: dict = {
            "general": {"config_version": "2.13.0"},
            "post_process": {"delete_lower_quality": True},
        }
        result = _migrate_v213_to_v214(raw)
        assert result["post_process"]["delete_lower_quality"] is True
        assert "hooks" in result["post_process"]


class TestMigrationV214ToV215:
    """Tests for the v2.14.0 -> v2.15.0 config migration."""

    def test_adds_pre_copy_to_existing_hooks(self) -> None:
        """Migration inserts pre_copy into an existing hooks block."""
        raw: dict = {
            "general": {"config_version": "2.14.0"},
            "post_process": {"hooks": {"post_copy": "", "pre_delete": "", "post_delete": ""}},
        }
        result = _migrate_v214_to_v215(raw)
        assert result["post_process"]["hooks"]["pre_copy"] == ""

    def test_does_not_overwrite_configured_pre_copy(self) -> None:
        """Migration does not clobber a pre-existing pre_copy value."""
        raw: dict = {
            "general": {"config_version": "2.14.0"},
            "post_process": {"hooks": {"pre_copy": "chattr -R -i {dir}"}},
        }
        result = _migrate_v214_to_v215(raw)
        assert result["post_process"]["hooks"]["pre_copy"] == "chattr -R -i {dir}"

    def test_creates_hooks_block_if_absent(self) -> None:
        """Migration creates the hooks block with pre_copy if hooks is missing entirely."""
        raw: dict = {"general": {"config_version": "2.14.0"}}
        result = _migrate_v214_to_v215(raw)
        assert result["post_process"]["hooks"]["pre_copy"] == ""

    def test_bumps_version_to_v215(self) -> None:
        raw: dict = {"general": {"config_version": "2.14.0"}}
        assert _migrate_v214_to_v215(raw)["general"]["config_version"] == "2.15.0"


class TestMigrationV215ToV216:
    """Tests for the v2.15.0 -> v2.16.0 config migration."""

    def test_moves_ignore_list_to_jackett(self) -> None:
        """Existing index_site.ignore_list is moved to index_proxy.jackett.ignore_list."""
        raw: dict = {
            "general": {"config_version": "2.15.0"},
            "index_site": {"ignore_list": ["tracker-a", "tracker-b"]},
        }
        result = _migrate_v215_to_v216(raw)
        assert result["index_proxy"]["jackett"]["ignore_list"] == ["tracker-a", "tracker-b"]
        assert "ignore_list" not in result.get("index_site", {})

    def test_empty_ignore_list_not_written(self) -> None:
        """An empty index_site.ignore_list does not create index_proxy.jackett.ignore_list."""
        raw: dict = {
            "general": {"config_version": "2.15.0"},
            "index_site": {"ignore_list": []},
        }
        result = _migrate_v215_to_v216(raw)
        assert result.get("index_proxy", {}).get("jackett", {}).get("ignore_list") is None

    def test_absent_ignore_list_is_noop(self) -> None:
        """No index_site.ignore_list key leaves index_proxy untouched."""
        raw: dict = {"general": {"config_version": "2.15.0"}}
        result = _migrate_v215_to_v216(raw)
        assert result.get("index_proxy", {}).get("jackett", {}).get("ignore_list") is None

    def test_does_not_overwrite_existing_jackett_ignore_list(self) -> None:
        """Pre-existing index_proxy.jackett.ignore_list is not clobbered."""
        raw: dict = {
            "general": {"config_version": "2.15.0"},
            "index_site": {"ignore_list": ["old-tracker"]},
            "index_proxy": {"jackett": {"ignore_list": ["already-set"]}},
        }
        result = _migrate_v215_to_v216(raw)
        assert result["index_proxy"]["jackett"]["ignore_list"] == ["already-set"]

    def test_bumps_version_to_v216(self) -> None:
        raw: dict = {"general": {"config_version": "2.15.0"}}
        assert _migrate_v215_to_v216(raw)["general"]["config_version"] == "2.16.0"


class TestMigrationV216ToV217:
    """Tests for the v2.16.0 -> v2.17.0 config migration (hook_timeout_mins)."""

    def test_migrates_v216_to_v217(self, tmp_path: Path) -> None:
        """A v2.16.0 config is migrated to v2.17.0 with hook_timeout_mins on disk."""
        p = tmp_path / "config.yml"
        p.write_text(
            "general:\n"
            "  config_version: 2.16.0\n"
            "post_process:\n"
            "  hooks:\n"
            "    pre_copy:\n"
            "    post_copy: echo ok\n"
            "    pre_delete:\n"
            "    post_delete:\n"
        )
        config = load_config(p)
        # Runtime version bumped
        assert config.general.config_version == "2.19.0"
        # hook_timeout_mins added
        assert config.post_process.hooks.hook_timeout_mins == 5.0
        # Migration persisted to disk
        on_disk = yaml.safe_load(p.read_text())
        assert on_disk["general"]["config_version"] == "2.19.0"
        assert on_disk["post_process"]["hooks"].get("hook_timeout_mins") == 5.0


class TestLoadConfigUnknownKeys:
    """load_config must warn when the config file contains unknown top-level keys."""

    def test_warns_on_unknown_top_level_key(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A typo'd top-level key (e.g. 'gneral') must produce a logger.warning."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("gneral: {}\n")
        mock_warning = mocker.patch("movarr.config.logger.warning")
        cfg = load_config(str(cfg_file))
        assert isinstance(cfg, Config)
        mock_warning.assert_called_once()
        call_args = mock_warning.call_args
        # First positional arg is the format string; second is the key list string.
        assert "gneral" in call_args.args[1]


# ---------------------------------------------------------------------------
# New coverage tests
# ---------------------------------------------------------------------------


class TestValidateLogLevelNonString:
    """validate_log_level raises when a non-string value is passed."""

    def test_log_level_non_string_raises(self) -> None:
        with pytest.raises(ValidationError):
            Config.model_validate({"general": {"log_level_console": 42}})


class TestNormaliseGenreKeysNonDict:
    """_normalise_genre_keys early-returns when override_genre is not a dict."""

    def test_override_genre_none_raises_after_validator_returns(self) -> None:
        """Validator early-returns None; Pydantic then rejects it as non-dict."""
        from movarr.config import FiltersConfig

        with pytest.raises(ValidationError):
            FiltersConfig.model_validate({"override_genre": None})

    def test_override_genre_list_raises_after_validator_returns(self) -> None:
        """Validator early-returns a list; Pydantic then rejects it as non-dict."""
        from movarr.config import FiltersConfig

        with pytest.raises(ValidationError):
            FiltersConfig.model_validate({"override_genre": ["action"]})


class TestTorrentClientUnsupported:
    """TorrentClientConfig._validate_selected raises for unsupported clients."""

    def test_unsupported_torrent_client_raises(self) -> None:
        from movarr.config import TorrentClientConfig

        with pytest.raises(ValidationError):
            TorrentClientConfig(selected="transmission")


class TestMigrationInfiniteLoopGuard:
    """_run_migrations breaks out when a migration does not increment config_version."""

    def test_no_op_migration_does_not_loop_forever(self, tmp_path: Path, mocker: MockerFixture) -> None:
        from movarr.config import _run_migrations

        sentinel_version = "__test_loop_guard__"

        def _no_op(raw: dict) -> dict:
            # Deliberately does NOT increment config_version.
            return raw

        patched: dict = {sentinel_version: _no_op}
        mocker.patch("movarr.config.MIGRATIONS", patched)

        config_path = tmp_path / "config.yml"
        raw: dict = {"general": {"config_version": sentinel_version}}
        import yaml as _yaml

        config_path.write_text(_yaml.dump(raw))

        mock_error = mocker.patch("movarr.config.logger.error")
        result = _run_migrations(raw, config_path)
        # Must return (not hang) and must have logged the loop-detection error.
        assert result is not None
        mock_error.assert_called_once()


class TestRunMigrationsAlreadyCurrentWithNulls:
    """_run_migrations when already at latest version with None values."""

    def test_already_current_with_none_values_strips_and_writes(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """When config is at latest version and has None values, _strip_and_write is called."""
        from typing import Any

        from movarr.config import _CONFIG_VERSION, _run_migrations

        config_path = tmp_path / "config.yml"
        raw: dict[str, Any] = {
            "general": {"config_version": _CONFIG_VERSION},
            "post_process": None,  # None value triggers _strip_and_write
        }
        mock_strip = mocker.patch("movarr.config._strip_and_write")
        result = _run_migrations(raw, config_path)
        assert result is not None
        mock_strip.assert_called_once_with(raw, config_path)

    def test_already_current_no_none_values_returns_without_strip(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """When config is at latest version with no None values, _strip_and_write is NOT called."""
        from typing import Any

        from movarr.config import _CONFIG_VERSION, _run_migrations

        config_path = tmp_path / "config.yml"
        raw: dict[str, Any] = {
            "general": {"config_version": _CONFIG_VERSION, "daemon_mode": "foreground"},
        }
        mock_strip = mocker.patch("movarr.config._strip_and_write")
        result = _run_migrations(raw, config_path)
        assert result is not None
        mock_strip.assert_not_called()

    def test_has_none_values_returns_false_for_clean_dict(self) -> None:
        """_has_none_values returns False when no None values at any depth."""
        from typing import Any

        from movarr.config import _has_none_values

        clean: dict[str, Any] = {"a": 1, "b": {"c": "hello"}, "d": [1, 2, 3]}
        assert _has_none_values(clean) is False


class TestCreateDefaultConfigDirectory:
    """create_default_config accepts directory path and creates movarr.yml inside."""

    def test_create_default_config_from_directory(self, tmp_path: Path) -> None:
        """When config_path is a directory, movarr.yml is created inside it."""
        from movarr.config import create_default_config

        config_dir = tmp_path / "my_configs"
        create_default_config(str(config_dir))
        config_file = config_dir / "movarr.yml"
        assert config_file.exists()
        raw = yaml.safe_load(config_file.read_text())
        assert raw["general"]["log_path"] == "logs"
        assert raw["general"]["db_path"] == "db"
        assert raw["general"]["pid_path"] == "pids"


class TestLoadConfigDirectoryPath:
    """load_config accepts a directory path and constructs movarr.yml inside."""

    def test_load_config_from_directory(self, tmp_path: Path) -> None:
        """When config_path is a directory, movarr.yml is created inside it."""
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        config = load_config(str(config_dir))
        assert isinstance(config, Config)
        assert (config_dir / "movarr.yml").exists()
        assert config.general.config_version == "2.19.0"
        assert config.general.log_path == "logs"
        assert config.general.db_path == "db"
        assert config.general.pid_path == "pids"

    def test_load_config_warns_on_unknown_keys(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """load_config logs a warning when the config contains unknown top-level keys."""
        p = tmp_path / "config.yml"
        p.write_text("general:\n  config_version: 2.19.0\nunknown_section:\n  foo: bar\n")
        mock_warning = mocker.patch("movarr.config.logger.warning")
        config = load_config(str(p))
        assert isinstance(config, Config)
        mock_warning.assert_called_once()


class TestLoadConfigEdgeCases:
    """Edge cases for load_config: empty file and non-dict YAML."""

    def test_load_config_empty_file_returns_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yml"
        p.write_text("")
        config = load_config(p)
        assert isinstance(config, Config)

    def test_load_config_scalar_yaml_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yml"
        p.write_text("42")
        with pytest.raises(ValueError, match="YAML mapping"):
            load_config(p)

    def test_null_hooks_fields_default_to_empty(self, tmp_path: Path) -> None:
        """YAML null values for hook fields fall back to empty string defaults."""
        p = tmp_path / "config.yml"
        p.write_text(
            "general:\n"
            "  config_version: 2.16.0\n"
            "post_process:\n"
            "  hooks:\n"
            "    pre_copy:\n"
            "    post_copy: chmod -R 755 {dir}\n"
            "    pre_delete:\n"
            "    post_delete:\n"
            "    hook_timeout_mins:\n"
        )
        config = load_config(p)
        assert config.post_process.hooks.pre_copy == ""
        assert config.post_process.hooks.post_copy == "chmod -R 755 {dir}"
        assert config.post_process.hooks.pre_delete == ""
        assert config.post_process.hooks.post_delete == ""
        assert config.post_process.hooks.hook_timeout_mins == 5.0

    def test_null_hooks_whole_block_preserves_defaults(self, tmp_path: Path) -> None:
        """Entire hooks block set to null preserves all default hook values."""
        p = tmp_path / "config.yml"
        p.write_text("general:\n  config_version: 2.16.0\npost_process:\n  hooks:\n")
        config = load_config(p)
        assert config.post_process.hooks.pre_copy == ""
        assert config.post_process.hooks.post_copy == ""
        assert config.post_process.hooks.pre_delete == ""
        assert config.post_process.hooks.post_delete == ""
        assert config.post_process.hooks.hook_timeout_mins == 5.0

    def test_null_typo_key_warns(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A null-valued typo'd top-level key still triggers the typo warning."""
        p = tmp_path / "config.yml"
        p.write_text("gneral:\n")
        mock_warning = mocker.patch("movarr.config.logger.warning")
        config = load_config(p)
        assert isinstance(config, Config)
        mock_warning.assert_called_once()
        assert "gneral" in mock_warning.call_args.args[1]


class TestDeepMerge:
    """_deep_merge edge cases exercised through load_config."""

    def test_override_list_not_dict(self, tmp_path: Path) -> None:
        """Non-dict override values replace the base value wholesale."""
        p = tmp_path / "config.yml"
        p.write_text("general:\n  config_version: 2.16.0\n  library_path_list:\n    - /custom/movies\n")
        config = load_config(p)
        assert config.general.library_path_list == ["/custom/movies"]

    def test_override_key_not_in_base(self, tmp_path: Path) -> None:
        """Keys in override that aren't in base trigger unknown-key warning but don't crash."""
        p = tmp_path / "config.yml"
        p.write_text("general:\n  config_version: 2.16.0\nnonexistent_key:\n  sub: value\n")
        config = load_config(p)
        assert isinstance(config, Config)

    def test_mixed_none_and_values_in_nested_override(self, tmp_path: Path) -> None:
        """Nested override with some None values and some real values merges correctly."""
        p = tmp_path / "config.yml"
        p.write_text("general:\n  config_version: 2.16.0\n  log_level_console: debug\n  library_path_list:\n")
        config = load_config(p)
        assert config.general.log_level_console == "DEBUG"
        assert config.general.library_path_list == []


class TestDeepMergeDirect:
    """Direct unit tests for _deep_merge covering edge-case branches."""

    def test_empty_override_returns_base_unchanged(self) -> None:
        from typing import Any

        from movarr.config import _deep_merge

        base: dict[str, Any] = {"a": 1, "b": {"c": 2}}
        result = _deep_merge(base, {})
        assert result == {"a": 1, "b": {"c": 2}}

    def test_none_value_in_override_is_skipped(self) -> None:
        from typing import Any

        from movarr.config import _deep_merge

        base: dict[str, Any] = {"a": 1, "b": 2}
        result = _deep_merge(base, {"b": None, "c": 3})
        assert result == {"a": 1, "b": 2, "c": 3}

    def test_none_in_nested_dict_is_skipped(self) -> None:
        from typing import Any

        from movarr.config import _deep_merge

        base: dict[str, Any] = {"outer": {"inner": "keep"}}
        result = _deep_merge(base, {"outer": {"inner": None, "new_key": "added"}})
        assert result == {"outer": {"inner": "keep", "new_key": "added"}}

    def test_non_dict_override_replaces_value(self) -> None:
        from typing import Any

        from movarr.config import _deep_merge

        base: dict[str, Any] = {"key": {"nested": "old"}}
        result = _deep_merge(base, {"key": "replaced"})
        assert result == {"key": "replaced"}

    def test_deeply_nested_merge(self) -> None:
        from typing import Any

        from movarr.config import _deep_merge

        base: dict[str, Any] = {"a": {"b": {"c": 1, "d": 2}}}
        override: dict[str, Any] = {"a": {"b": {"c": None, "e": 3}}}
        result = _deep_merge(base, override)
        assert result == {"a": {"b": {"c": 1, "d": 2, "e": 3}}}

    def test_base_has_non_dict_value_override_has_dict(self) -> None:
        """When base has a non-dict for a key but override provides a dict, base is replaced."""
        from typing import Any

        from movarr.config import _deep_merge

        base: dict[str, Any] = {"key": "string_value"}
        override: dict[str, Any] = {"key": {"nested": "new"}}
        result = _deep_merge(base, override)
        assert result == {"key": {"nested": "new"}}


class TestGeneralNull:
    """Null-valued general block must be normalised and not crash."""

    def test_general_null_loaded_without_crash(self, tmp_path: Path) -> None:
        """Config with general: null is normalised to {} during migration."""
        p = tmp_path / "config.yml"
        p.write_text("general:\n")
        config = load_config(p)
        assert isinstance(config, Config)
        assert config.general.config_version == "2.19.0"


class TestMigrationV216ToV217Isolated:
    """Direct unit tests for the v2.16.0 -> v2.17.0 migration function."""

    def test_adds_hook_timeout_mins(self) -> None:
        """Migration inserts hook_timeout_mins into hooks block."""
        raw: dict = {"general": {"config_version": "2.16.0"}}
        result = _migrate_v216_to_v217(raw)
        assert result["post_process"]["hooks"]["hook_timeout_mins"] == 5.0

    def test_does_not_overwrite_existing_value(self) -> None:
        """Migration preserves a pre-existing hook_timeout_mins value."""
        raw: dict = {
            "general": {"config_version": "2.16.0"},
            "post_process": {"hooks": {"hook_timeout_mins": 2.0}},
        }
        result = _migrate_v216_to_v217(raw)
        assert result["post_process"]["hooks"]["hook_timeout_mins"] == 2.0

    def test_bumps_version_to_v217(self) -> None:
        raw: dict = {"general": {"config_version": "2.16.0"}}
        assert _migrate_v216_to_v217(raw)["general"]["config_version"] == "2.17.0"


class TestMigrationV217ToV218:
    """Tests for the v2.17.0 -> v2.18.0 migration (hook_timeout_secs -> hook_timeout_mins)."""

    def test_renames_old_key_and_converts_value(self) -> None:
        """hook_timeout_secs: 300 is converted to hook_timeout_mins: 5.0."""
        raw: dict = {
            "general": {"config_version": "2.17.0"},
            "post_process": {"hooks": {"hook_timeout_secs": 300.0}},
        }
        result = _migrate_v217_to_v218(raw)
        assert "hook_timeout_secs" not in result["post_process"]["hooks"]
        assert result["post_process"]["hooks"]["hook_timeout_mins"] == 5.0

    def test_defaults_when_no_old_key(self) -> None:
        """When neither key exists, defaults to 5.0 minutes."""
        raw: dict = {"general": {"config_version": "2.17.0"}}
        result = _migrate_v217_to_v218(raw)
        assert result["post_process"]["hooks"]["hook_timeout_mins"] == 5.0

    def test_preserves_existing_mins_value(self) -> None:
        """Does not overwrite an existing hook_timeout_mins value."""
        raw: dict = {
            "general": {"config_version": "2.17.0"},
            "post_process": {"hooks": {"hook_timeout_mins": 2.0}},
        }
        result = _migrate_v217_to_v218(raw)
        assert result["post_process"]["hooks"]["hook_timeout_mins"] == 2.0

    def test_bumps_version_to_v218(self) -> None:
        raw: dict = {"general": {"config_version": "2.17.0"}}
        assert _migrate_v217_to_v218(raw)["general"]["config_version"] == "2.18.0"

    def test_post_process_null_does_not_crash(self) -> None:
        """Migration handles post_process: null without crashing."""
        raw: dict = {
            "general": {"config_version": "2.17.0"},
            "post_process": None,
        }
        result = _migrate_v217_to_v218(raw)
        assert result["post_process"]["hooks"]["hook_timeout_mins"] == 5.0

    def test_non_numeric_secs_value_warns(self, mocker: MockerFixture) -> None:
        """A string hook_timeout_secs value triggers a warning and defaults to 5.0."""
        raw: dict = {
            "general": {"config_version": "2.17.0"},
            "post_process": {"hooks": {"hook_timeout_secs": "five"}},
        }
        mock_warning = mocker.patch("movarr.config.logger.warning")
        result = _migrate_v217_to_v218(raw)
        assert result["post_process"]["hooks"]["hook_timeout_mins"] == 5.0
        assert "hook_timeout_secs" not in result["post_process"]["hooks"]
        mock_warning.assert_called_once()

    def test_small_seconds_rounds_to_zero_warns(self, mocker: MockerFixture) -> None:
        """A tiny seconds value that rounds to 0.0 triggers a warning and uses default."""
        raw: dict = {
            "general": {"config_version": "2.17.0"},
            "post_process": {"hooks": {"hook_timeout_secs": 1}},
        }
        mock_warning = mocker.patch("movarr.config.logger.warning")
        result = _migrate_v217_to_v218(raw)
        assert result["post_process"]["hooks"]["hook_timeout_mins"] == 5.0
        mock_warning.assert_called_once()

    def test_both_keys_present_mins_wins(self) -> None:
        """When both old and new keys exist, hook_timeout_mins is preserved."""
        raw: dict = {
            "general": {"config_version": "2.17.0"},
            "post_process": {"hooks": {"hook_timeout_secs": 600.0, "hook_timeout_mins": 3.0}},
        }
        result = _migrate_v217_to_v218(raw)
        assert result["post_process"]["hooks"]["hook_timeout_mins"] == 3.0
        assert "hook_timeout_secs" not in result["post_process"]["hooks"]

    def test_hooks_is_not_a_dict(self) -> None:
        """Migration handles hooks being a non-dict (e.g. bare string) by replacing it."""
        raw: dict = {
            "general": {"config_version": "2.17.0"},
            "post_process": {"hooks": "not-a-dict"},
        }
        result = _migrate_v217_to_v218(raw)
        assert isinstance(result["post_process"]["hooks"], dict)
        assert result["post_process"]["hooks"]["hook_timeout_mins"] == 5.0


class TestMigrationV218ToV219:
    """Tests for the v2.18.0 -> v2.19.0 migration (paths → directories)."""

    def test_strips_filenames_from_paths(self) -> None:
        """Migration strips movarr.log, movarr.db, movarr.pid suffixes from paths."""
        raw: dict = {
            "general": {
                "config_version": "2.18.0",
                "log_path": "logs/movarr.log",
                "db_path": "db/movarr.db",
                "pid_path": "configs/movarr.pid",
            },
        }
        result = _migrate_v218_to_v219(raw)
        assert result["general"]["log_path"] == "logs"
        assert result["general"]["db_path"] == "db"
        assert result["general"]["pid_path"] == "configs"
        assert result["general"]["config_version"] == "2.19.0"

    def test_handles_paths_without_suffix(self) -> None:
        """Paths already without the hardcoded suffix are left unchanged."""
        raw: dict = {
            "general": {
                "config_version": "2.18.0",
                "log_path": "custom_logs",
                "db_path": "/var/data",
                "pid_path": "run",
            },
        }
        result = _migrate_v218_to_v219(raw)
        assert result["general"]["log_path"] == "custom_logs"
        assert result["general"]["db_path"] == "/var/data"
        assert result["general"]["pid_path"] == "run"
        assert result["general"]["config_version"] == "2.19.0"

    def test_root_path_preserved(self) -> None:
        """/movarr.log migrates to /, not empty string."""
        raw: dict = {"general": {"config_version": "2.18.0", "log_path": "/movarr.log"}}
        result = _migrate_v218_to_v219(raw)
        assert result["general"]["log_path"] == "/"

    def test_bare_filename_strips_to_empty(self) -> None:
        """Bare 'movarr.log' (just a filename) strips to empty string."""
        raw: dict = {"general": {"config_version": "2.18.0", "log_path": "movarr.log"}}
        result = _migrate_v218_to_v219(raw)
        assert result["general"]["log_path"] == ""

    def test_substring_not_stripped(self) -> None:
        """A path like 'notmovarr.log' (substring match) is NOT stripped."""
        raw: dict = {"general": {"config_version": "2.18.0", "log_path": "logs/notmovarr.log"}}
        result = _migrate_v218_to_v219(raw)
        assert result["general"]["log_path"] == "logs/notmovarr.log"

    def test_trailing_slash_normalised(self) -> None:
        """'logs/movarr.log/' (trailing slash) has the basename stripped."""
        raw: dict = {"general": {"config_version": "2.18.0", "log_path": "logs/movarr.log/"}}
        result = _migrate_v218_to_v219(raw)
        assert result["general"]["log_path"] == "logs"


class TestStripPathBasename:
    """Tests for _strip_path_basename — the helper extracted from _migrate_v218_to_v219."""

    def test_strips_basename_with_separator(self) -> None:
        """'logs/movarr.log' strips to 'logs'."""
        assert _strip_path_basename("logs/movarr.log", "movarr.log") == "logs"

    def test_no_basename_unchanged(self) -> None:
        """'custom_logs' (no basename) returns unchanged."""
        assert _strip_path_basename("custom_logs", "movarr.log") == "custom_logs"

    def test_root_path_preserves_separator(self) -> None:
        """'/movarr.log' migrates to '/' not empty."""
        assert _strip_path_basename("/movarr.log", "movarr.log") == "/"

    def test_bare_filename_strips_to_empty(self) -> None:
        """Bare 'movarr.log' strips to ''."""
        assert _strip_path_basename("movarr.log", "movarr.log") == ""

    def test_substring_not_stripped(self) -> None:
        """'notmovarr.log' (substring) returns unchanged."""
        assert _strip_path_basename("notmovarr.log", "movarr.log") == "notmovarr.log"

    def test_trailing_slash_normalised(self) -> None:
        """'logs/movarr.log/' trailing slash stripped."""
        assert _strip_path_basename("logs/movarr.log/", "movarr.log") == "logs"

    def test_windows_backslash_path(self) -> None:
        """'logs\\movarr.log' strips to 'logs'."""
        assert _strip_path_basename("logs\\movarr.log", "movarr.log") == "logs"

    def test_windows_backslash_root(self) -> None:
        """'\\movarr.log' strips to '\\' (root separator preserved)."""
        result = _strip_path_basename("\\movarr.log", "movarr.log")
        assert result == "\\"

    def test_windows_drive_root_preserved(self) -> None:
        """'C:\\movarr.log' strips to 'C:\\' (drive root separator preserved)."""
        result = _strip_path_basename("C:\\movarr.log", "movarr.log")
        # stripped == '' after rstrip("/\\"), length 2 check fails, falls through to basename check
        assert result == "C:\\"

    def test_path_with_only_separator_prefix(self) -> None:
        """'/something/movarr.log' strips to '/something'."""
        assert _strip_path_basename("/something/movarr.log", "movarr.log") == "/something"

    def test_basename_at_end_without_separator_unchanged(self) -> None:
        """'somemovarr.log' (basename embedded, not component) unchanged."""
        assert _strip_path_basename("somemovarr.log", "movarr.log") == "somemovarr.log"


class TestStripNullValues:
    """Migration must not write YAML null values to disk."""

    def test_null_hook_values_stripped_from_disk(self, tmp_path: Path) -> None:
        """After migration, null-valued hook keys are omitted from on-disk YAML."""
        p = tmp_path / "config.yml"
        p.write_text(
            "general:\n"
            "  config_version: 2.16.0\n"
            "post_process:\n"
            "  hooks:\n"
            "    pre_copy:\n"
            "    post_copy: echo ok\n"
            "    pre_delete:\n"
            "    post_delete:\n"
        )
        config = load_config(p)
        assert isinstance(config, Config)
        on_disk = yaml.safe_load(p.read_text())
        hooks = on_disk["post_process"]["hooks"]
        # Null-valued keys must be absent from disk (not present at all)
        assert "pre_copy" not in hooks
        assert "pre_delete" not in hooks
        assert "post_delete" not in hooks
        # post_copy with a value must remain
        assert hooks["post_copy"] == "echo ok"

    def test_null_hooks_stripped_when_already_current_version(self, tmp_path: Path) -> None:
        """Early-return path also strips nulls from already-current configs."""
        p = tmp_path / "config.yml"
        p.write_text(
            "general:\n  config_version: '2.18.0'\npost_process:\n  hooks:\n    pre_copy:\n    post_copy: echo ok\n"
        )
        config = load_config(p)
        assert isinstance(config, Config)
        on_disk = yaml.safe_load(p.read_text())
        assert "pre_copy" not in on_disk["post_process"]["hooks"]

    def test_null_in_list_dict_item_stripped(self, tmp_path: Path) -> None:
        """List-of-dict entries with null values are also cleaned."""
        p = tmp_path / "config.yml"
        p.write_text(
            "general:\n"
            "  config_version: 2.16.0\n"
            "post_process:\n"
            "  copy_library_rules:\n"
            "    - name: test\n"
            "      max_certification:\n"
        )
        config = load_config(p)
        assert isinstance(config, Config)
        on_disk = yaml.safe_load(p.read_text())
        rule = on_disk["post_process"]["copy_library_rules"][0]
        # max_certification null must be stripped from the list-of-dicts entry
        assert "max_certification" not in rule
