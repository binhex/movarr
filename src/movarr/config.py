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

_CONFIG_VERSION = "2.24.0"
_INITIAL_CONFIG_VERSION = "1.0.0"

# Filenames constructed from directory paths at runtime.  Consumers import these
# rather than hardcoding the literal, so the name is defined in exactly one place.
LOG_FILENAME = "movarr.log"
DB_FILENAME = "movarr.db"
PID_FILENAME = "movarr.pid"
_CONFIG_FILENAME = "movarr.yml"


def _section(parent: dict[str, Any], key: str) -> dict[str, Any]:
    """Return *parent*[*key*] as a mutable dict, replacing a null/scalar value.

    ``raw.setdefault(key, {})`` returns the existing value untouched, so a YAML
    section written as ``filters:`` (null) or ``filters: nope`` would make every
    hand-written migration raise ``AttributeError``/``TypeError`` at startup.
    """
    value = parent.get(key)
    if not isinstance(value, dict):
        value = {}
        parent[key] = value
    return value


def _migrate_v1_to_v2(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v1.0.0 → v2.0.0: replace notification.email with apprise_urls."""
    notification = _section(raw, "notification")
    notification.pop("email", None)
    notification.setdefault("apprise_urls", [])
    _section(raw, "general")["config_version"] = "2.0.0"
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
    # NOTE: these defaults (empty = disabled) differ from the current model
    # defaults ("logs"/"pids").  Reaching 2.11.0 *disables* file logging and the
    # PID file for upgraded configs, which is intended continuity with 2.10
    # behaviour; fresh installs get the enabled defaults.
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
        [],  # version bump only: key introduced by the hand-written 2.17.0 -> 2.18.0 step
    ),
    (
        "2.19.0",
        "2.20.0",
        [
            (("filters", "reject_genre_exclusive_list"), []),
        ],
    ),
    (
        "2.20.0",
        "2.21.0",
        [
            (("notification", "poster_embed_enabled"), True),
            (("notification", "poster_embed_width"), 500),
            (("post_process", "poster_art"), {"filename": "", "download_width": 0}),
        ],
    ),
    (
        "2.21.0",
        "2.22.0",
        [
            (("queue_management", "supersede_enabled"), False),
        ],
    ),
    (
        "2.22.0",
        "2.23.0",
        [
            (("index_proxy", "circuit_open_minutes"), 30),
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
        _section(raw, "general")["config_version"] = to_v
        return raw

    _migrate.__name__ = f"_migrate_v{from_v.replace('.', '')}_to_v{to_v.replace('.', '')}"
    return _migrate


# ---------------------------------------------------------------------------
# Custom migrations with logic beyond simple setdefault
# ---------------------------------------------------------------------------


def _migrate_v23_to_v24(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.3.0 → v2.4.0: add run_on_start: true to all schedule tasks."""
    schedule = _section(raw, "schedule")
    for task in ("acquisition", "queue_management", "post_processing"):
        _section(schedule, task).setdefault("run_on_start", True)
    _section(raw, "general")["config_version"] = "2.4.0"
    return raw


def _migrate_v25_to_v26(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.5.0 → v2.6.0: remove deprecated ffprobe_path setting."""
    _section(raw, "general").pop("ffprobe_path", None)
    _section(raw, "general")["config_version"] = "2.6.0"
    return raw


def _migrate_v26_to_v27(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.6.0 → v2.7.0: rename good_* fields to allow_* in filters."""
    filters = _section(raw, "filters")
    for old, new in (
        ("good_imdb_title_type_list", "allow_imdb_title_type_list"),
        ("good_country_list", "allow_country_list"),
        ("good_language_list", "allow_language_list"),
    ):
        if old in filters:
            filters[new] = filters.pop(old)
    _section(raw, "general")["config_version"] = "2.7.0"
    return raw


def _migrate_v27_to_v28(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.7.0 → v2.8.0: rename bad_* fields to reject_* in filters."""
    filters = _section(raw, "filters")
    for old, new in (
        ("bad_index_title_list", "reject_index_title_list"),
        ("bad_genre_list", "reject_genre_list"),
        ("bad_movie_title_list", "reject_movie_title_list"),
    ):
        if old in filters:
            filters[new] = filters.pop(old)
    _section(raw, "general")["config_version"] = "2.8.0"
    return raw


def _migrate_v211_to_v212(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.11.0 -> v2.12.0: fix stalled/metadata delete_data defaults.

    The old default was ``False`` (leave partial files on disk when deleting a
    stalled or metaDL torrent).  The correct behaviour is to remove the
    incomplete data via qBittorrent when movarr deletes the torrent.

    The values are written unconditionally: the old *default* was ``false``, so
    a ``false`` in a 2.11.0 config is indistinguishable from the bug this step
    exists to fix.  (An earlier revision preserved ``false``, which left
    upgraded installs keeping the old behaviour.)
    """
    queue_management = _section(raw, "queue_management")
    queue_management["stalled_delete_torrent_data"] = True
    queue_management["metadata_delete_torrent_data"] = True
    _section(raw, "general")["config_version"] = "2.12.0"
    return raw


def _migrate_v215_to_v216(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.15.0 -> v2.16.0: move index_site.ignore_list to index_proxy.jackett.ignore_list.

    The ignore_list was previously on IndexSiteConfig but only ever applied to
    Jackett all-indexer searches.  It now lives on JackettConfig so that each
    proxy owns its own ignore list.
    """
    existing = _section(raw, "index_site").pop("ignore_list", [])
    if existing:
        _section(_section(raw, "index_proxy"), "jackett").setdefault("ignore_list", existing)
    _section(raw, "general")["config_version"] = "2.16.0"
    return raw


def _ensure_hooks_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """Ensure raw["post_process"]["hooks"] is a mutable dict; return it."""
    if not isinstance(raw.get("post_process"), dict):
        raw["post_process"] = {}
    return _section(_section(raw, "post_process"), "hooks")


def _migrate_v217_to_v218(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.17.0 -> v2.18.0: rename hook_timeout_secs -> hook_timeout_mins (seconds to minutes).

    Converts any existing ``hook_timeout_secs`` value from seconds to minutes
    (rounding to 1 decimal) and stores it under ``hook_timeout_mins``, unless a
    ``hook_timeout_mins`` value is already present, which takes precedence.
    If neither key exists, defaults to 5.0 minutes.
    """
    hooks = _ensure_hooks_dict(raw)
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
        # Only assign when the newer key is absent: a config that already carries
        # hook_timeout_mins is expressing current intent, so the stale seconds
        # value must not overwrite it.
        if converted is not None and converted > 0:
            if "hook_timeout_mins" in hooks:
                logger.info(
                    "Both hook_timeout_secs ({} s) and hook_timeout_mins are set; keeping "
                    "hook_timeout_mins={} and dropping the legacy seconds key.",
                    old_secs,
                    hooks["hook_timeout_mins"],
                )
            else:
                hooks["hook_timeout_mins"] = converted
        elif converted is not None:
            logger.warning(
                "hook_timeout_secs value of {} s is non-positive; using default 5.0.",
                old_secs,
            )
    hooks.setdefault("hook_timeout_mins", 5.0)
    _section(raw, "general")["config_version"] = "2.18.0"
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
    general = _section(raw, "general")
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


def _migrate_v223_to_v224(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.23.0 -> v2.24.0: drop the dead ``poster_embed_*`` settings.

    Poster embedding was removed by the Markdown notification redesign, so the
    two settings were written into every config without any effect.
    """
    notification = _section(raw, "notification")
    for key in ("poster_embed_enabled", "poster_embed_width"):
        notification.pop(key, None)
    _section(raw, "general")["config_version"] = "2.24.0"
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
_migrate_v220_to_v221 = _table_fns["2.20.0"]
_migrate_v221_to_v222 = _table_fns["2.21.0"]
_migrate_v222_to_v223 = _table_fns["2.22.0"]


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
    "2.20.0": _migrate_v220_to_v221,
    "2.21.0": _migrate_v221_to_v222,
    "2.22.0": _migrate_v222_to_v223,
    "2.23.0": _migrate_v223_to_v224,
}

_VALID_LOG_LEVELS = frozenset({"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"})


# Pydantic sub-models


class GeneralConfig(BaseModel):
    """Top-level general settings."""

    config_version: str = _CONFIG_VERSION
    log_level_console: str = "info"
    log_level_file: str = "info"
    log_path: str = "logs"
    library_path_list: list[str] = Field(default_factory=list)
    db_path: str = "db"
    pid_path: str = "pids"

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
        # YAML may parse a bare key ("2020:", "Sci_Fi:") as a non-string.
        return {str(k).lower(): val for k, val in v.items()}


class QbittorrentConfig(BaseModel):
    """qBittorrent connection settings."""

    host: str = "localhost"
    # Certificate verification for an HTTPS WebUI.  Defaults to False for
    # compatibility with self-signed LAN certificates; enable it when the WebUI
    # is reachable from anywhere but a trusted network.
    verify_webui_certificate: bool = False
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
    # gt=0: an indexer cannot return a page of zero results, so a zero limit
    # would turn every request into an empty "last page" and silently stop.
    limit: int = Field(default=500, gt=0)
    # ge=0: a negative offset makes the pagination loop body never execute.
    offset: int = Field(default=0, ge=0)
    ignore_list: list[str] = Field(default_factory=list)


class ProwlarrConfig(BaseModel):
    """Prowlarr indexer proxy settings."""

    host: str = "localhost"
    port: int = 9696
    api_key: str = ""
    read_timeout: float = 60.0
    # gt=0: a zero page size would return nothing and look like "no results".
    limit: int = Field(default=500, gt=0)
    ignore_list: list[str] = Field(default_factory=list)


class IndexProxyConfig(BaseModel):
    """Index proxy selection and settings."""

    selected: str = "jackett"
    jackett: JackettConfig = Field(default_factory=JackettConfig)
    prowlarr: ProwlarrConfig = Field(default_factory=ProwlarrConfig)
    circuit_open_minutes: int = Field(default=30, ge=0)

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
            if int(value) <= 0:
                raise ValueError(f"indexer ID must be positive, got {value!r}")
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
    # ge=1: a non-positive threshold would delete every matching torrent on the
    # next cycle (including its partial data when *_data is enabled).
    stalled_delete_torrent_max_mins: int = Field(default=120, ge=1)
    metadata_delete_torrent_max_mins: int = Field(default=30, ge=1)
    supersede_enabled: bool = False


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
    uhd_path: str | None = ""


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
    hook_timeout_mins: float = Field(default=5.0, ge=0)


class PosterArtConfig(BaseModel):
    """Poster art download settings for post-processing.

    ``filename``: target filename (e.g. ``"poster.jpg"`` or ``"folder.jpg"``).
    Empty string disables poster download. Extension is forced to ``.jpg`` at
    save time (the user can omit or specify any extension).

    ``download_width``: pixel width for the poster. ``0`` means largest available.
    """

    filename: str = ""
    download_width: int = 0


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
    poster_art: PosterArtConfig = Field(default_factory=PosterArtConfig)


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


def _has_none_in_list(items: list[object]) -> bool:
    """Return True if any dict in *items* contains None-valued keys."""
    return any(isinstance(item, dict) and _has_none_values(item) for item in items)


def _has_none_values(d: dict[str, Any]) -> bool:
    """Return True if *d* contains any None-valued key at any depth."""
    for value in d.values():
        if value is None:
            return True
        if isinstance(value, dict) and _has_none_values(value):
            return True
        if isinstance(value, list) and _has_none_in_list(value):
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


def _normalise_version(value: object) -> str:
    """Return *value* as a canonical ``X.Y.Z`` version string.

    A hand-edited ``config_version: '2.3'`` (or an unquoted ``2.3`` parsed as a
    float) must still match its migration entry rather than silently skipping
    the whole chain.
    """
    if value is None:
        return _INITIAL_CONFIG_VERSION
    if isinstance(value, float):
        logger.warning(
            "config_version was read as the number {}; quote it in YAML (e.g. '2.10.0') — an "
            "unquoted value loses its trailing zero and can re-run applied migrations.",
            value,
        )
    text = str(value).strip()
    key = _version_key(text)
    padded = (key + (0, 0, 0))[:3]
    return ".".join(str(part) for part in padded)


def _version_key(version: str) -> tuple[int, ...]:
    """Return a comparable tuple of the numeric components of *version*."""
    parts: list[int] = []
    for chunk in str(version).split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


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
    current = _prepare_config_version(raw)
    if current not in MIGRATIONS:
        _warn_unknown_version(current)
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

    seen: set[str] = set()
    while current in MIGRATIONS:
        if current in seen:
            logger.error("Migration cycle detected at version {}; aborting.", current)
            break
        seen.add(current)
        previous = current
        raw = MIGRATIONS[current](raw)
        current = _normalise_version(raw.get("general", {}).get("config_version", current))
        logger.info("Config migrated from v{} to v{}", previous, current)
        if current == previous:
            logger.error("Migration loop detected at version {}; aborting.", current)
            break

    merged = _deep_merge(_default_config_dict(), raw)
    with config_path.open("w", encoding="utf-8") as fh:
        _strip_and_dump(merged, fh)

    return raw


def _is_mapping(annotation: Any) -> bool:
    """Return True when *annotation* is a ``dict[...]``-style mapping."""
    origin = getattr(annotation, "__origin__", None)
    return origin is dict or annotation is dict


def _nested_model(annotation: Any) -> type[BaseModel] | None:
    """Return the ``BaseModel`` subclass carried by *annotation*, if any."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in getattr(annotation, "__args__", ()):
        found = _nested_model(arg)
        if found is not None:
            return found
    return None


def _unknown_key_paths(model: type[BaseModel], data: dict[str, Any], prefix: str = "") -> list[str]:
    """Return dotted paths of keys in *data* that *model* (or its sub-models) lack.

    Pydantic silently drops unknown keys, so a typo such as
    ``filters.reject_genres_list`` or ``post_process.delete_lower_qualityy``
    would otherwise switch a feature off with no diagnostic.  Lists of
    sub-models (e.g. ``index_site.search``) are inspected element-wise.
    """
    unknown: list[str] = []
    fields = model.model_fields
    for key, value in data.items():
        path = f"{prefix}{key}"
        if key not in fields:
            unknown.append(path)
            continue
        unknown.extend(_unknown_key_paths_in_value(fields[key].annotation, value, path))
    return unknown


def _unknown_key_paths_in_value(annotation: Any, value: Any, path: str) -> list[str]:
    """Return unknown key paths inside *value* for a field annotated as *annotation*."""
    nested = _nested_model(annotation)
    if nested is None:
        return []
    if _is_mapping(annotation):
        # dict[str, Model]: the mapping keys are user data (e.g. genre names in
        # filters.override_genre), so only the mapping values are inspected.
        return _unknown_key_paths_in_mapping_values(nested, value, path)
    if isinstance(value, dict):
        return _unknown_key_paths(nested, value, f"{path}.")
    if isinstance(value, list):
        return _unknown_key_paths_in_list(nested, value, f"{path}[].")
    return []


def _unknown_key_paths_in_mapping_values(nested: type[BaseModel], value: Any, path: str) -> list[str]:
    """Return unknown key paths for each dict value of a ``dict[str, Model]`` field."""
    if not isinstance(value, dict):
        return []
    found: list[str] = []
    for item in value.values():
        if isinstance(item, dict):
            found.extend(_unknown_key_paths(nested, item, f"{path}."))
    return found


def _unknown_key_paths_in_list(nested: type[BaseModel], value: list[Any], prefix: str) -> list[str]:
    """Return unknown key paths for each dict element of a list-of-models field."""
    found: list[str] = []
    for item in value:
        if isinstance(item, dict):
            found.extend(_unknown_key_paths(nested, item, prefix))
    return found


def _prepare_config_version(raw: dict[str, Any]) -> str:
    """Normalise ``general.config_version`` in place and warn when it is newer.

    GeneralConfig validates ``config_version`` as a str, so an unquoted ``2.24``
    (parsed as a float) would otherwise fail validation on every load.
    """
    current = _normalise_version(raw.get("general", {}).get("config_version"))
    if isinstance(current, str) and _version_key(current) > _version_key(_CONFIG_VERSION):
        logger.warning(
            "Config version {} is newer than this movarr build ({}); no migration is applied and "
            "unrecognised keys are ignored.",
            current,
            _CONFIG_VERSION,
        )
    raw.setdefault("general", {})["config_version"] = current
    return current


def _warn_unknown_version(current: str) -> None:
    """Warn when *current* is older than this build but has no migration entry."""
    if isinstance(current, str) and _version_key(current) < _version_key(_CONFIG_VERSION):
        logger.warning(
            "Unknown config_version '{}' (this build expects {}); no migration was applied. "
            "Old keys are ignored, so verify the config after upgrading.",
            current,
            _CONFIG_VERSION,
        )


def _default_config_dict() -> dict[str, Any]:
    """Return the default config as a plain dict suitable for YAML serialisation."""
    return Config().model_dump()


def create_default_config(config_path: str | Path) -> None:
    """Write a default ``movarr.yml`` to *config_path* if it does not already exist.

    If *config_path* has a file extension it is used as-is.  Otherwise it is
    treated as a directory and ``movarr.yml`` is created inside.
    """
    path = Path(config_path)
    if path.is_dir() or not path.suffix:  # directory — construct filename
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
    if path.is_dir() or not path.suffix:  # directory — construct filename
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

    unknown_keys = _unknown_key_paths(Config, raw)
    if unknown_keys:
        logger.warning(
            "Unknown config keys (will be ignored): {}. Check for typos.",
            ", ".join(sorted(unknown_keys)),
        )

    merged = _deep_merge(_default_config_dict(), raw)
    return Config.model_validate(merged)
