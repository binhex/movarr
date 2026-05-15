"""Configuration loading, validation, and default creation for movarr."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

import yaml
from loguru import logger
from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["Config", "ProwlarrConfig", "load_config"]

_CONFIG_VERSION = "2.20.0"
_INITIAL_CONFIG_VERSION = "1.0.0"

# Hardcoded filenames constructed from directory paths at runtime.
_LOG_FILENAME = "movarr.log"
_DB_FILENAME = "movarr.db"
_PID_FILENAME = "movarr.pid"
_CONFIG_FILENAME = "movarr.yml"


def _migrate_v1_to_v2(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v1.0.0 → v2.0.0: replace notification.email with apprise_urls."""
    notification = raw.setdefault("notification", {})
    notification.pop("email", None)
    notification.setdefault("apprise_urls", [])
    raw.setdefault("general", {})["config_version"] = "2.0.0"
    return raw


# ---------------------------------------------------------------------------
# Data-driven simple migrations (pure setdefault + version bump)
# ---------------------------------------------------------------------------

_MIGRATION_TABLE: list[tuple[str, str, list[tuple[tuple[str, ...], Any]]]] = [
    (
        "2.0.0",
        "2.1.0",
        [
            (("database", "stalled_expiry_days"), 7),
        ],
    ),
    (
        "2.1.0",
        "2.2.0",
        [
            (("database", "failed_expiry_days"), 7),
        ],
    ),
    (
        "2.2.0",
        "2.3.0",
        [
            (("database", "passed_expiry_days"), 30),
        ],
    ),
    (
        "2.4.0",
        "2.5.0",
        [
            (("index_proxy", "prowlarr"), {"host": "localhost", "port": 9696, "api_key": "", "read_timeout": 60.0}),
            (("index_site", "prowlarr_indexer"), "all"),
        ],
    ),
    (
        "2.8.0",
        "2.9.0",
        [
            (("notification", "index_proxy_alert_hours"), 0),
        ],
    ),
    (
        "2.9.0",
        "2.10.0",
        [
            (("notification", "torrent_client_alert_hours"), 0),
        ],
    ),
    (
        "2.10.0",
        "2.11.0",
        [
            (("general", "log_path"), ""),
            (("general", "pid_path"), ""),
        ],
    ),
    (
        "2.12.0",
        "2.13.0",
        [
            (("post_process", "delete_lower_quality"), False),
        ],
    ),
    (
        "2.13.0",
        "2.14.0",
        [
            (("post_process", "hooks"), {"post_copy": "", "pre_delete": "", "post_delete": ""}),
        ],
    ),
    (
        "2.14.0",
        "2.15.0",
        [
            (("post_process", "hooks", "pre_copy"), ""),
        ],
    ),
    (
        "2.16.0",
        "2.17.0",
        [
            (("post_process", "hooks", "hook_timeout_mins"), 5.0),
        ],
    ),
    (
        "2.18.0",
        "2.19.0",
        [],  # hand-written migration: strips filenames from path fields
    ),
    (
        "2.19.0",
        "2.20.0",
        [
            (("filters", "reject_genre_exclusive_list"), []),
        ],
    ),
]


def _make_migration(
    from_v: str,
    to_v: str,
    additions: list[tuple[tuple[str, ...], Any]],
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Return a migration that applies *additions* as setdefault entries and bumps config_version."""

    def _migrate(raw: dict[str, Any]) -> dict[str, Any]:
        for keypath, default in additions:
            node = raw
            for k in keypath[:-1]:
                if not isinstance(node.get(k), dict):
                    if k in node:
                        logger.warning("Replacing non-dict value at config key %r during migration.", k)
                    node[k] = {}
                node = node[k]
            node.setdefault(keypath[-1], copy.deepcopy(default))
        raw.setdefault("general", {})["config_version"] = to_v
        return raw

    _migrate.__name__ = f"_migrate_v{from_v.replace('.', '')}_to_v{to_v.replace('.', '')}"
    return _migrate


# ---------------------------------------------------------------------------
# Custom migrations with logic beyond simple setdefault
# ---------------------------------------------------------------------------


def _migrate_v23_to_v24(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.3.0 → v2.4.0: add run_on_start: true to all schedule tasks."""
    schedule = raw.setdefault("schedule", {})
    for task in ("acquisition", "queue_management", "post_processing"):
        schedule.setdefault(task, {}).setdefault("run_on_start", True)
    raw.setdefault("general", {})["config_version"] = "2.4.0"
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


def _migrate_v211_to_v212(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.11.0 -> v2.12.0: fix stalled/metadata delete_data defaults.

    The old default was ``False`` (leave partial files on disk when deleting a
    stalled or metaDL torrent).  The correct behaviour is to remove the
    incomplete data via qBittorrent when movarr deletes the torrent, so both
    fields are updated to ``True``.

    This migration explicitly writes ``True`` so that existing configs that
    relied on the old ``False`` default are also corrected.
    """
    raw.setdefault("queue_management", {})["stalled_delete_torrent_data"] = True
    raw.setdefault("queue_management", {})["metadata_delete_torrent_data"] = True
    raw.setdefault("general", {})["config_version"] = "2.12.0"
    return raw


def _migrate_v215_to_v216(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.15.0 -> v2.16.0: move index_site.ignore_list to index_proxy.jackett.ignore_list.

    The ignore_list was previously on IndexSiteConfig but only ever applied to
    Jackett all-indexer searches.  It now lives on JackettConfig so that each
    proxy owns its own ignore list.
    """
    existing = raw.get("index_site", {}).pop("ignore_list", [])
    if existing:
        raw.setdefault("index_proxy", {}).setdefault("jackett", {}).setdefault("ignore_list", existing)
    raw.setdefault("general", {})["config_version"] = "2.16.0"
    return raw


def _migrate_v217_to_v218(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.17.0 -> v2.18.0: rename hook_timeout_secs -> hook_timeout_mins (seconds to minutes).

    Converts any existing ``hook_timeout_secs`` value from seconds to minutes
    (rounding to 1 decimal) and stores it under ``hook_timeout_mins``.
    If neither key exists, defaults to 5.0 minutes.
    """
    if not isinstance(raw.get("post_process"), dict):
        raw["post_process"] = {}
    hooks = raw.setdefault("post_process", {}).setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        raw["post_process"]["hooks"] = hooks
    if "hook_timeout_secs" in hooks:
        old_secs = hooks.pop("hook_timeout_secs")
        if isinstance(old_secs, (int, float)):
            converted = round(old_secs / 60.0, 1)
        else:
            try:
                converted = round(float(old_secs) / 60.0, 1)
            except (TypeError, ValueError):
                logger.warning(
                    "Cannot convert hook_timeout_secs value {!r} to minutes; using default 5.0.",
                    old_secs,
                )
                converted = None
        if converted is not None and converted > 0:
            hooks.setdefault("hook_timeout_mins", converted)
        elif converted is not None:
            logger.warning(
                "hook_timeout_secs value of {} s is non-positive; using default 5.0.",
                old_secs,
            )
    hooks.setdefault("hook_timeout_mins", 5.0)
    raw.setdefault("general", {})["config_version"] = "2.18.0"
    return raw


def _strip_sep_suffix(normalised: str, basename: str) -> str:
    """Strip a separator-prefixed *basename* suffix from *normalised*.

    Returns *normalised* unchanged if no separator-prefixed match is found.
    """
    for sep in ("/", "\\"):
        if normalised.endswith(sep + basename):
            return normalised[: -len(sep) - len(basename)].rstrip("/\\")
    return normalised


def _strip_path_basename(value: str, basename: str) -> str:
    """Strip *basename* from *value* if it is a complete path-component suffix.

    Handles both Unix and Windows separators.  Returns the stripped value.

    Examples:
        _strip_path_basename("logs/movarr.log", "movarr.log") -> "logs"
        _strip_path_basename("/movarr.log", "movarr.log")     -> "/"
        _strip_path_basename("movarr.log", "movarr.log")      -> ""
        _strip_path_basename("logs", "movarr.log")            -> "logs"
    """
    normalised = value.rstrip("/\\")
    stripped = _strip_sep_suffix(normalised, basename)

    if stripped != normalised:
        # Root path (empty, "/", "\\", or "C:") — preserve the separator.
        if not stripped or (len(stripped) == 2 and stripped[1] == ":"):
            return normalised[: -len(basename)]
        return stripped

    if normalised.endswith(basename):
        prefix = normalised[: -len(basename)]
        if not prefix or prefix[-1] in ("/", "\\"):
            return prefix.rstrip("/\\")
    return value


def _migrate_v218_to_v219(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.18.0 -> v2.19.0: strip hardcoded filenames from path fields.

    log_path, db_path, and pid_path become directory-only; the filename portion
    (movarr.log, movarr.db, movarr.pid) is now hardcoded in the application.
    """
    general = raw.setdefault("general", {})
    for key, basename in [
        ("log_path", "movarr.log"),
        ("db_path", "movarr.db"),
        ("pid_path", "movarr.pid"),
    ]:
        value = general.get(key)
        if isinstance(value, str):
            general[key] = _strip_path_basename(value, basename)
    general["config_version"] = "2.19.0"
    return raw


# ---------------------------------------------------------------------------
# Bind table-generated functions to module-level names (import compatibility)
# ---------------------------------------------------------------------------

_table_fns: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    from_v: _make_migration(from_v, to_v, additions) for from_v, to_v, additions in _MIGRATION_TABLE
}

_migrate_v2_to_v21 = _table_fns["2.0.0"]
_migrate_v21_to_v22 = _table_fns["2.1.0"]
_migrate_v22_to_v23 = _table_fns["2.2.0"]
_migrate_v24_to_v25 = _table_fns["2.4.0"]
_migrate_v28_to_v29 = _table_fns["2.8.0"]
_migrate_v29_to_v210 = _table_fns["2.9.0"]
_migrate_v210_to_v211 = _table_fns["2.10.0"]
_migrate_v212_to_v213 = _table_fns["2.12.0"]
_migrate_v213_to_v214 = _table_fns["2.13.0"]
_migrate_v214_to_v215 = _table_fns["2.14.0"]
_migrate_v216_to_v217 = _table_fns["2.16.0"]
_migrate_v219_to_v220 = _table_fns["2.19.0"]


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
    "2.11.0": _migrate_v211_to_v212,
    "2.12.0": _migrate_v212_to_v213,
    "2.13.0": _migrate_v213_to_v214,
    "2.14.0": _migrate_v214_to_v215,
    "2.15.0": _migrate_v215_to_v216,
    "2.16.0": _migrate_v216_to_v217,
    "2.17.0": _migrate_v217_to_v218,
    "2.18.0": _migrate_v218_to_v219,
    "2.19.0": _migrate_v219_to_v220,
}

_VALID_LOG_LEVELS = frozenset({"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"})


# Pydantic sub-models


class GeneralConfig(BaseModel):
    """Top-level general settings."""

    config_version: str = _CONFIG_VERSION
    daemon_mode: str = "foreground"
    log_level_console: str = "info"
    log_level_file: str = "info"
    log_path: str = "logs"
    library_path_list: list[str] = Field(default_factory=list)
    db_path: str = "db"
    pid_path: str = "pids"

    @field_validator("daemon_mode")
    @classmethod
    def validate_daemon_mode(cls, value: str) -> str:
        """Ensure daemon_mode is one of the allowed values."""
        allowed = {"foreground", "background"}
        if value not in allowed:
            raise ValueError(f"daemon_mode must be one of {allowed}")
        return value

    @field_validator("log_level_console", "log_level_file", mode="before")
    @classmethod
    def validate_log_level(cls, value: object) -> str:
        """Ensure log level is a known Loguru level."""
        if not isinstance(value, str):
            raise ValueError(f"Log level must be a string, got {type(value).__name__}")
        upper = value.upper()
        if upper not in _VALID_LOG_LEVELS:
            raise ValueError(f"Invalid log level {value!r}. Must be one of: {', '.join(sorted(_VALID_LOG_LEVELS))}")
        return upper


class ScheduleTaskConfig(BaseModel):
    """A single scheduled task configuration."""

    enabled: bool = True
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
    reject_genre_exclusive_list: list[str] = Field(default_factory=list)
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
    ignore_list: list[str] = Field(default_factory=list)


class ProwlarrConfig(BaseModel):
    """Prowlarr indexer proxy settings."""

    host: str = "localhost"
    port: int = 9696
    api_key: str = ""
    read_timeout: float = 60.0
    ignore_list: list[str] = Field(default_factory=list)


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
    stalled_delete_torrent_data: bool = True
    metadata_delete_torrent_data: bool = True
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


class PostProcessHooksConfig(BaseModel):
    """Shell commands to run at defined points in the post-processing pipeline.

    Each field is a command template. Leave empty (the default) to disable.
    The placeholder ``{dir}`` is substituted with the resolved absolute path of
    the destination directory before the command is executed.
    ``{leaf}`` is substituted with the last path component (e.g. the movie
    folder name).

    ``shell=True`` is used so that glob patterns such as ``chattr -R -i {dir}``
    are expanded by the shell. Commands come from the user's own config file,
    so the trust boundary is identical to the rest of the configuration.

    Important:
        Hooks **must not rename or move** the target files. The ``post_copy``
        hook fires before library supersession; if it renames the newly copied
        primary file, supersession will skip deletion (the primary is no longer
        found). The ``pre_delete`` hook fires before the deletion loop; if it
        renames a library candidate, the loop will report a false-positive
        deletion count. Use hooks only for in-place operations (e.g. ``chattr
        -i``, ``trimarr``).
    """

    pre_copy: str = ""
    post_copy: str = ""
    pre_delete: str = ""
    post_delete: str = ""
    hook_timeout_mins: float = 5.0


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
    delete_lower_quality: bool = False
    hooks: PostProcessHooksConfig = Field(default_factory=PostProcessHooksConfig)


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
    """Recursively merge *override* into *base*, returning a new dict.

    ``None`` values in *override* are treated as "not provided" and skipped,
    so the base value is preserved.  This handles YAML config files where an
    empty scalar (e.g. ``key:``) parses as Python ``None``.
    """
    result = dict(base)
    for key, value in override.items():
        if value is None:
            # NOTE: safe only while every `T | None` field has default=None.
            # If a nullable field ever gains a non-None default, explicit
            # ``null`` from the user's YAML will silently lose to that default.
            continue
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _strip_none_values(d: dict[str, Any]) -> None:
    """Recursively remove keys whose value is ``None`` from *d* (mutates in place).

    Also descends into lists to clean dicts nested inside list entries.
    """
    for key in list(d.keys()):
        if d[key] is None:
            del d[key]
        elif isinstance(d[key], dict):
            _strip_none_values(d[key])
        elif isinstance(d[key], list):
            for item in d[key]:
                if isinstance(item, dict):
                    _strip_none_values(item)


def _has_none_values(d: dict[str, Any]) -> bool:
    """Return True if *d* contains any None-valued key at any depth."""
    for value in d.values():
        if value is None:
            return True
        if isinstance(value, dict):
            if _has_none_values(value):
                return True
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and _has_none_values(item):
                    return True
    return False


def _strip_and_dump(raw: dict[str, Any], fh: IO[str]) -> None:
    """Write *raw* to *fh* (a file handle) after stripping None-valued keys."""
    _clean = copy.deepcopy(raw)
    _strip_none_values(_clean)
    yaml.dump(_clean, fh, default_flow_style=False, sort_keys=False)


def _strip_and_write(raw: dict[str, Any], config_path: Path) -> None:
    """Open *config_path* for writing and dump *raw* with None keys stripped."""
    with config_path.open("w", encoding="utf-8") as fh:
        _strip_and_dump(raw, fh)


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
    if not isinstance(raw.get("general"), dict):
        raw["general"] = {}
    current = raw.get("general", {}).get("config_version", _INITIAL_CONFIG_VERSION)
    if current not in MIGRATIONS:
        if _has_none_values(raw):
            _strip_and_write(raw, config_path)
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
        _strip_and_dump(raw, fh)

    return raw


def _default_config_dict() -> dict[str, Any]:
    """Return the default config as a plain dict suitable for YAML serialisation."""
    return Config().model_dump()


def create_default_config(config_path: str | Path) -> None:
    """Write a default ``movarr.yml`` to *config_path* if it does not already exist.

    If *config_path* has a file extension it is used as-is.  Otherwise it is
    treated as a directory and ``movarr.yml`` is created inside.
    """
    path = Path(config_path)
    if not path.suffix:  # directory — construct filename
        path = path / _CONFIG_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(_default_config_dict(), fh, default_flow_style=False, sort_keys=False)


def load_config(config_path: str | Path) -> Config:
    """Load, validate, and return the application config.

    If *config_path* is a directory, ``movarr.yml`` is created inside it.
    Creates a default config file if none exists. Merges the on-disk file
    with defaults so that missing optional keys are always present.

    Args:
        config_path: Path to the config directory or file.  If a directory,
            ``movarr.yml`` is used inside it.

    Returns:
        A fully validated :class:`Config` instance.

    Raises:
        ValueError: If the config file contains invalid values.
    """
    path = Path(config_path)
    if not path.suffix:  # directory — construct filename
        path = path / _CONFIG_FILENAME

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

    _known_top_level = set(Config.model_fields.keys())
    _unknown_keys = set(raw.keys()) - _known_top_level
    if _unknown_keys:
        logger.warning(
            "Unknown config keys (will be ignored): {}. Check for typos.",
            ", ".join(sorted(_unknown_keys)),
        )

    merged = _deep_merge(_default_config_dict(), raw)
    return Config.model_validate(merged)
