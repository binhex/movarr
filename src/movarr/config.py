"""Configuration loading, validation, and default creation for movarr."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

__all__ = ["Config", "load_config"]

_CONFIG_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Pydantic sub-models
# ---------------------------------------------------------------------------


class GeneralConfig(BaseModel):
    """Top-level general settings."""

    config_version: str = _CONFIG_VERSION
    daemon_mode: str = "foreground"
    log_level_console: str = "info"
    log_level_file: str = "info"
    library_path_list: list[str] = Field(default_factory=list)
    db_path: str = "db/movarr.db"
    ffprobe_path: str = "/usr/bin/ffprobe"

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
    schedule_time_mins: int = 30


class ScheduleConfig(BaseModel):
    """Schedule intervals for the three background tasks."""

    acquisition: ScheduleTaskConfig = Field(default_factory=lambda: ScheduleTaskConfig(schedule_time_mins=30))
    queue_management: ScheduleTaskConfig = Field(default_factory=lambda: ScheduleTaskConfig(schedule_time_mins=5))
    post_processing: ScheduleTaskConfig = Field(default_factory=lambda: ScheduleTaskConfig(schedule_time_mins=5))


class OverrideGenreConfig(BaseModel):
    """Relaxed thresholds for a specific genre."""

    minimum_rating: float = 0.0
    minimum_votes: int = 0


class FiltersConfig(BaseModel):
    """Torrent and IMDb filtering criteria."""

    minimum_year: int = 1970
    minimum_runtime_mins: int = 60
    minimum_rating: float = 7.0
    minimum_votes: int = 5000
    override_genre: dict[str, OverrideGenreConfig] = Field(default_factory=dict)
    good_imdb_title_type_list: list[str] = Field(default_factory=lambda: ["movie", "video", "tvmovie"])
    good_country_list: list[str] = Field(default_factory=list)
    good_language_list: list[str] = Field(default_factory=list)
    bad_index_title_list: list[str] = Field(default_factory=list)
    bad_genre_list: list[str] = Field(default_factory=list)
    bad_movie_title_list: list[str] = Field(default_factory=list)
    override_cast_list: list[str] = Field(default_factory=list)
    override_writer_list: list[str] = Field(default_factory=list)
    override_director_list: list[str] = Field(default_factory=list)
    override_movie_title_list: list[str] = Field(default_factory=list)
    override_character_list: list[str] = Field(default_factory=list)
    preferred_index_quality_list: list[str] = Field(default_factory=list)
    preferred_index_group_list: list[str] = Field(default_factory=list)


class QbittorrentConfig(BaseModel):
    """qBittorrent connection settings."""

    host: str = "localhost"
    port: int = 8080
    username: str = "admin"
    password: str = "adminadmin"
    add_paused: bool = True
    category: str = "movies-movarr"


class TorrentClientConfig(BaseModel):
    """Torrent client selection and settings."""

    selected: str = "qbittorrent"
    qbittorrent: QbittorrentConfig = Field(default_factory=QbittorrentConfig)


class EmailConfig(BaseModel):
    """SMTP email notification settings."""

    enabled: bool = False
    host: str = ""
    port: int = 587
    enable_tls: bool = True
    enable_ssl: bool = False
    username: str = ""
    password: str = ""
    from_address: str = ""
    to_address: str = ""


class NotificationConfig(BaseModel):
    """Notification settings."""

    email: EmailConfig = Field(default_factory=EmailConfig)


class JackettConfig(BaseModel):
    """Jackett indexer proxy settings."""

    host: str = "localhost"
    port: int = 9117
    api_key: str = ""
    read_timeout: float = 60.0
    limit: int = 500
    offset: int = 0


class IndexProxyConfig(BaseModel):
    """Index proxy selection and settings."""

    selected: str = "jackett"
    jackett: JackettConfig = Field(default_factory=JackettConfig)


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
    ignore_list: list[str] = Field(default_factory=list)
    search: list[SearchCriteriaConfig] = Field(
        default_factory=lambda: [
            SearchCriteriaConfig(criteria="1080p", minimum_size_mb=3000, maximum_size_mb=20000, minimum_bitrate_mb=50),
            SearchCriteriaConfig(
                criteria="2160p", minimum_size_mb=7000, maximum_size_mb=170000, minimum_bitrate_mb=115
            ),
        ]
    )
    override_search: dict[str, dict[str, str]] = Field(default_factory=dict)


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


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*, returning a new dict."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


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
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    merged = _deep_merge(_default_config_dict(), raw)
    return Config.model_validate(merged)
