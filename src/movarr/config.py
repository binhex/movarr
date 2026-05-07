"""Configuration loading, validation, and default creation for movarr."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from loguru import logger
from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["Config", "ProwlarrConfig", "load_config"]

_CONFIG_VERSION = "2.11.0"


def _migrate_v1_to_v2(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v1.0.0 → v2.0.0: replace notification.email with apprise_urls."""
    notification = raw.setdefault("notification", {})
    notification.pop("email", None)
    notification.setdefault("apprise_urls", [])
    raw.setdefault("general", {})["config_version"] = "2.0.0"
    return raw


def _migrate_v2_to_v21(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.0.0 → v2.1.0: add database.stalled_expiry_days."""
    raw.setdefault("database", {}).setdefault("stalled_expiry_days", 7)
    raw.setdefault("general", {})["config_version"] = "2.1.0"
    return raw


def _migrate_v21_to_v22(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.1.0 → v2.2.0: add database.failed_expiry_days."""
    raw.setdefault("database", {}).setdefault("failed_expiry_days", 7)
    raw.setdefault("general", {})["config_version"] = "2.2.0"
    return raw


def _migrate_v22_to_v23(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.2.0 → v2.3.0: add database.passed_expiry_days."""
    raw.setdefault("database", {}).setdefault("passed_expiry_days", 30)
    raw.setdefault("general", {})["config_version"] = "2.3.0"
    return raw


def _migrate_v23_to_v24(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.3.0 → v2.4.0: add run_on_start: true to all schedule tasks."""
    schedule = raw.setdefault("schedule", {})
    for task in ("acquisition", "queue_management", "post_processing"):
        schedule.setdefault(task, {}).setdefault("run_on_start", True)
    raw.setdefault("general", {})["config_version"] = "2.4.0"
    return raw


def _migrate_v24_to_v25(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.4.0 → v2.5.0: add Prowlarr config block and prowlarr_indexer."""
    raw.setdefault("index_proxy", {}).setdefault(
        "prowlarr",
        {"host": "localhost", "port": 9696, "api_key": "", "read_timeout": 60.0},
    )
    raw.setdefault("index_site", {}).setdefault("prowlarr_indexer", "all")
    raw.setdefault("general", {})["config_version"] = "2.5.0"
    return raw


def _migrate_v25_to_v26(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.5.0 → v2.6.0: remove deprecated ffprobe_path setting."""
    raw.setdefault("general", {}).pop("ffprobe_path", None)
    raw.setdefault("general", {})["config_version"] = "2.6.0"
    return raw


def _migrate_v26_to_v27(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.6.0 → v2.7.0: rename good_* fields to allow_* in filters."""
    filters = raw.setdefault("filters", {})
    for old, new in (
        ("good_imdb_title_type_list", "allow_imdb_title_type_list"),
        ("good_country_list", "allow_country_list"),
        ("good_language_list", "allow_language_list"),
    ):
        if old in filters:
            filters[new] = filters.pop(old)
    raw.setdefault("general", {})["config_version"] = "2.7.0"
    return raw


def _migrate_v27_to_v28(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.7.0 → v2.8.0: rename bad_* fields to reject_* in filters."""
    filters = raw.setdefault("filters", {})
    for old, new in (
        ("bad_index_title_list", "reject_index_title_list"),
        ("bad_genre_list", "reject_genre_list"),
        ("bad_movie_title_list", "reject_movie_title_list"),
    ):
        if old in filters:
            filters[new] = filters.pop(old)
    raw.setdefault("general", {})["config_version"] = "2.8.0"
    return raw


def _migrate_v28_to_v29(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.8.0 -> v2.9.0: add notification.index_proxy_alert_hours (default 0 = disabled)."""
    notification = raw.setdefault("notification", {})
    notification.setdefault("index_proxy_alert_hours", 0)
    raw.setdefault("general", {})["config_version"] = "2.9.0"
    return raw


def _migrate_v29_to_v210(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.9.0 -> v2.10.0: add notification.torrent_client_alert_hours (default 0)."""
    raw.setdefault("notification", {}).setdefault("torrent_client_alert_hours", 0)
    raw.setdefault("general", {})["config_version"] = "2.10.0"
    return raw


def _migrate_v210_to_v211(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.10.0 -> v2.11.0: add general.log_path and general.pid_path.

    These settings previously only existed as CLI flags.  They are now part of
    the YAML config so the daemon can be configured entirely from movarr.yml.
    """
    raw.setdefault("general", {}).setdefault("log_path", "")
    raw.setdefault("general", {}).setdefault("pid_path", "")
    raw["general"]["config_version"] = "2.11.0"
    return raw


MIGRATIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "1.0.0": _migrate_v1_to_v2,
    "2.0.0": _migrate_v2_to_v21,
    "2.1.0": _migrate_v21_to_v22,
    "2.2.0": _migrate_v22_to_v23,
    "2.3.0": _migrate_v23_to_v24,
    "2.4.0": _migrate_v24_to_v25,
    "2.5.0": _migrate_v25_to_v26,
    "2.6.0": _migrate_v26_to_v27,
    "2.7.0": _migrate_v27_to_v28,
    "2.8.0": _migrate_v28_to_v29,
    "2.9.0": _migrate_v29_to_v210,
    "2.10.0": _migrate_v210_to_v211,
}


# Pydantic sub-models


class GeneralConfig(BaseModel):
    """Top-level general settings."""

    config_version: str = _CONFIG_VERSION
    daemon_mode: str = "foreground"
    log_level_console: str = "info"
    log_level_file: str = "info"
    log_path: str = ""
    library_path_list: list[str] = Field(default_factory=list)
    db_path: str = "db/movarr.db"
    pid_path: str = ""

    @field_validator("daemon_mode")
    @classmethod
    def validate_daemon_mode(cls, value: str) -> str:
        """Ensure daemon_mode is one of the allowed values."""
        allowed = {"foreground", "background"}
        if value not in allowed:
            raise ValueError(f"daemon_mode must be one of {allowed}")
        return value


class ScheduleTaskConfig(BaseModel):
    """A single scheduled task configuration."""

    enabled: bool = True
    schedule_time_units: str = "minutes"
    schedule_time_mins: int = Field(default=30, gt=0, description="Interval in minutes (must be > 0).")
    run_on_start: bool = True


class ScheduleConfig(BaseModel):
    """Schedule intervals for the three background tasks."""

    acquisition: ScheduleTaskConfig = Field(default_factory=lambda: ScheduleTaskConfig(schedule_time_mins=30))
    queue_management: ScheduleTaskConfig = Field(default_factory=lambda: ScheduleTaskConfig(schedule_time_mins=5))
    post_processing: ScheduleTaskConfig = Field(default_factory=lambda: ScheduleTaskConfig(schedule_time_mins=5))


class OverrideGenreConfig(BaseModel):
    """Relaxed thresholds for a specific genre.

    None means "inherit the global threshold"; 0 means "no minimum".
    """

    minimum_rating: float | None = None
    minimum_votes: int | None = None


class FiltersConfig(BaseModel):
    """Torrent and IMDb filtering criteria."""

    minimum_year: int = 1970
    minimum_runtime_mins: int = 60
    minimum_rating: float = 7.0
    minimum_votes: int = 5000
    override_genre: dict[str, OverrideGenreConfig] = Field(default_factory=dict)
    allow_imdb_title_type_list: list[str] = Field(default_factory=lambda: ["movie", "video", "tvmovie"])
    allow_country_list: list[str] = Field(default_factory=list)
    allow_language_list: list[str] = Field(default_factory=list)
    reject_index_title_list: list[str] = Field(default_factory=list)
    reject_genre_list: list[str] = Field(default_factory=list)
    reject_movie_title_list: list[str] = Field(default_factory=list)
    reject_index_group_list: list[str] = Field(default_factory=list)
    override_cast_list: list[str] = Field(default_factory=list)
    override_writer_list: list[str] = Field(default_factory=list)
    override_director_list: list[str] = Field(default_factory=list)
    override_movie_title_list: list[str] = Field(default_factory=list)
    override_character_list: list[str] = Field(default_factory=list)
    preferred_index_quality_list: list[str] = Field(default_factory=list)
    preferred_index_group_list: list[str] = Field(default_factory=list)

    @field_validator("override_genre", mode="before")
    @classmethod
    def _normalise_genre_keys(cls, v: object) -> object:
        """Normalise override_genre keys to lowercase at config load time.

        IMDb genre names are title-cased ("Action", "Sci-Fi"), but the
        filter pipeline looks up by lowercase genre to avoid case mismatches.
        Normalising keys here avoids a silent no-op when users capitalise
        their YAML keys.
        """
        if not isinstance(v, dict):
            return v
        return {k.lower(): val for k, val in v.items()}


class QbittorrentConfig(BaseModel):
    """qBittorrent connection settings."""

    host: str = "localhost"
    port: int = 8080
    username: str = "admin"
    password: str = "adminadmin"
    add_paused: bool = False
    category: str = "movies-movarr"


class TorrentClientConfig(BaseModel):
    """Torrent client selection and settings."""

    selected: str = "qbittorrent"
    qbittorrent: QbittorrentConfig = Field(default_factory=QbittorrentConfig)

    @field_validator("selected")
    @classmethod
    def _validate_selected(cls, value: str) -> str:
        """Only qBittorrent is currently supported."""
        allowed = ("qbittorrent",)
        if value not in allowed:
            raise ValueError(f"torrent_client.selected must be one of {allowed!r}, got {value!r}")
        return value


class NotificationConfig(BaseModel):
    """Notification settings.

    Specify one or more `apprise <https://github.com/caronc/apprise>`_ service URLs.
    An empty list disables notifications.  Any apprise-supported service works:
    ``ntfy://topic``, ``discord://id/token``, ``mailtos://user:pass@host:587/``, etc.

    ``index_proxy_alert_hours``: send an alert after the index proxy returns no results
    (or is unreachable) for this many hours.  Requires ``apprise_urls`` to be non-empty.
    ``0`` disables the feature.
    """

    apprise_urls: list[str] = Field(default_factory=list)
    index_proxy_alert_hours: float = 0
    torrent_client_alert_hours: float = 0


class JackettConfig(BaseModel):
    """Jackett indexer proxy settings."""

    host: str = "localhost"
    port: int = 9117
    api_key: str = ""
    read_timeout: float = 60.0
    limit: int = 500
    offset: int = 0


class ProwlarrConfig(BaseModel):
    """Prowlarr indexer proxy settings."""

    host: str = "localhost"
    port: int = 9696
    api_key: str = ""
    read_timeout: float = 60.0


class IndexProxyConfig(BaseModel):
    """Index proxy selection and settings."""

    selected: str = "jackett"
    jackett: JackettConfig = Field(default_factory=JackettConfig)
    prowlarr: ProwlarrConfig = Field(default_factory=ProwlarrConfig)

    @field_validator("selected")
    @classmethod
    def validate_selected(cls, value: str) -> str:
        """Ensure selected is a supported index proxy name."""
        allowed = {"jackett", "prowlarr"}
        if value not in allowed:
            raise ValueError(f"index_proxy.selected must be one of {allowed!r}, got {value!r}")
        return value


class CredentialSetConfig(BaseModel):
    """API key for a single external service."""

    api_key: str = ""


class CredentialsConfig(BaseModel):
    """External API credentials."""

    tmdb: CredentialSetConfig = Field(default_factory=CredentialSetConfig)
    omdb: CredentialSetConfig = Field(default_factory=CredentialSetConfig)


class SearchCriteriaConfig(BaseModel):
    """Per-quality-tier search parameters."""

    criteria: str
    category: str = "2000,5000"
    minimum_size_mb: int = 3000
    maximum_size_mb: int = 20000
    minimum_bitrate_mb: int = 50


class IndexSiteConfig(BaseModel):
    """Per-indexer search configuration."""

    jackett_indexer: str = "all"
    prowlarr_indexer: str = "all"
    ignore_list: list[str] = Field(default_factory=list)
    search: list[SearchCriteriaConfig] = Field(
        default_factory=lambda: [
            SearchCriteriaConfig(
                criteria="1080p",
                category="2000,5000",
                minimum_size_mb=3000,
                maximum_size_mb=20000,
                minimum_bitrate_mb=50,
            ),
        ]
    )
    override_search: dict[str, dict[str, str]] = Field(default_factory=dict)

    @field_validator("prowlarr_indexer")
    @classmethod
    def validate_prowlarr_indexer(cls, value: str) -> str:
        """Ensure prowlarr_indexer is 'all' or a numeric indexer ID string."""
        if value == "all":
            return value
        try:
            int(value)
        except ValueError as exc:
            raise ValueError(f"index_site.prowlarr_indexer must be 'all' or a numeric ID, got {value!r}") from exc
        return value


class QueueManagementConfig(BaseModel):
    """Stalled torrent monitoring settings."""

    queue_management_enabled: bool = True
    metadata_monitor_enabled: bool = True
    stalled_monitor_enabled: bool = True
    stalled_delete_torrent_data: bool = False
    metadata_delete_torrent_data: bool = False
    stalled_delete_torrent_max_mins: int = 120
    metadata_delete_torrent_max_mins: int = 30
    connection_down_grace_mins: int = 30


class CopyLibraryRuleConfig(BaseModel):
    """A genre/certification routing rule for post-processing."""

    name: str = "default"
    genres: list[str] = Field(default_factory=list)
    max_certification: str | None = None
    hd_path: str = ""
    uhd_path: str = ""


class DefaultCopyLibraryConfig(BaseModel):
    """Fallback destination paths when no routing rule matches."""

    hd_path: str = ""
    uhd_path: str | None = None


class PathRemappingConfig(BaseModel):
    """A single remote→local path prefix replacement.

    Useful when qBittorrent runs in a container and reports paths that differ
    from those visible to movarr (e.g. '/downloads' inside qbt container
    maps to '/mnt/storage/downloads' from movarr's perspective).
    """

    from_path: str = ""
    to_path: str = ""


class PostProcessConfig(BaseModel):
    """Post-processing settings for copying completed downloads."""

    post_process_enabled: bool = True
    copy_completed: bool = True
    remove_completed: bool = True
    exclude_file_min_kb: int = 1_500_000
    exclude_file_regex_list: list[str] = Field(default_factory=list)
    exclude_folder_regex_list: list[str] = Field(default_factory=list)
    copy_library_rules: list[CopyLibraryRuleConfig] = Field(default_factory=list)
    default_copy_library: DefaultCopyLibraryConfig = Field(default_factory=DefaultCopyLibraryConfig)
    path_remapping: list[PathRemappingConfig] = Field(default_factory=list)


class DatabaseConfig(BaseModel):
    """Database settings."""

    stalled_expiry_days: int = 7
    failed_expiry_days: int = 7
    passed_expiry_days: int = 30


class Config(BaseModel):
    """Root configuration model for movarr."""

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    filters: FiltersConfig = Field(default_factory=FiltersConfig)
    torrent_client: TorrentClientConfig = Field(default_factory=TorrentClientConfig)
    notification: NotificationConfig = Field(default_factory=NotificationConfig)
    index_proxy: IndexProxyConfig = Field(default_factory=IndexProxyConfig)
    credentials: CredentialsConfig = Field(default_factory=CredentialsConfig)
    index_site: IndexSiteConfig = Field(default_factory=IndexSiteConfig)
    queue_management: QueueManagementConfig = Field(default_factory=QueueManagementConfig)
    post_process: PostProcessConfig = Field(default_factory=PostProcessConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)


# Public helpers


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*, returning a new dict."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _run_migrations(raw: dict[str, Any], config_path: Path) -> dict[str, Any]:
    """Apply any pending schema migrations to *raw*, rewriting the file on disk.

    A single backup of the original file is created before the first migration
    step.  Migrations are applied sequentially until the config reaches
    ``_CONFIG_VERSION``.

    Args:
        raw: The raw config dict loaded from YAML.
        config_path: Path to the config file (used for backup and overwrite).

    Returns:
        The migrated raw dict (may be unchanged if already up to date).
    """
    current = raw.get("general", {}).get("config_version", "1.0.0")
    if current not in MIGRATIONS:
        return raw

    backup_path = config_path.with_suffix(f".yml.bak.{current}")
    try:
        shutil.copy2(config_path, backup_path)
        logger.info("Config backup created at {}", backup_path)
    except OSError:
        logger.warning(
            "Could not create config backup at {}; proceeding without backup.",
            backup_path,
        )

    while current in MIGRATIONS:
        previous = current
        raw = MIGRATIONS[current](raw)
        current = raw.get("general", {}).get("config_version", current)
        logger.info("Config migrated from v{} to v{}", previous, current)
        if current == previous:
            logger.error("Migration loop detected at version {}; aborting.", current)
            break

    with config_path.open("w", encoding="utf-8") as fh:
        yaml.dump(raw, fh, default_flow_style=False, sort_keys=False)

    return raw


def _default_config_dict() -> dict[str, Any]:
    """Return the default config as a plain dict suitable for YAML serialisation."""
    return Config().model_dump()


def create_default_config(config_path: str | Path) -> None:
    """Write a default config.yml to *config_path* if it does not already exist."""
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(_default_config_dict(), fh, default_flow_style=False, sort_keys=False)


def load_config(config_path: str | Path) -> Config:
    """Load, validate, and return the application config.

    Creates a default config file if none exists. Merges the on-disk file
    with defaults so that missing optional keys are always present.

    Args:
        config_path: Path to ``config.yml``.

    Returns:
        A fully validated :class:`Config` instance.

    Raises:
        ValueError: If the config file contains invalid values.
    """
    path = Path(config_path)

    if not path.exists():
        create_default_config(path)

    with path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)

    # yaml.safe_load returns None for an empty file; normalise to empty dict.
    if loaded is None:
        raw: dict[str, Any] = {}
    elif not isinstance(loaded, dict):
        raise ValueError(f"Config file '{path}' must be a YAML mapping (got {type(loaded).__name__}).")
    else:
        raw = loaded

    raw = _run_migrations(raw, path)

    merged = _deep_merge(_default_config_dict(), raw)
    return Config.model_validate(merged)
