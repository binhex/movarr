"""Command-line interface for movarr."""

from __future__ import annotations

import contextlib
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any, cast

import click
from loguru import logger as _logger

from movarr.logger import create_logger
from movarr.parsing import CONTROL_CHAR_TRANSLATION

if TYPE_CHECKING:
    from movarr.config import Config

try:
    _VERSION = version("movarr")
except PackageNotFoundError:
    _VERSION = "unknown"

# Characters loguru reads as format (braces) or colour (angle brackets) syntax, plus
# every C0 control character (newlines forge log lines; ESC injects terminal control
# sequences).  The tracker name comes from the indexer, so none of it may reach the
# format string raw.
_LOG_UNSAFE_CHARS = {
    **CONTROL_CHAR_TRANSLATION,
    ord("{"): "(",
    ord("}"): ")",
    ord("<"): "(",
    ord(">"): ")",
}


def _start_load_capture() -> tuple[list[tuple[str, str]], int]:
    """Replace the default sink with a recorder for config-load diagnostics.

    Migration, unknown-key and timezone warnings are emitted while the config is
    loaded, before the operator's log path and level are known.  Loguru's default
    sink prints them to stderr immediately, but ``create_logger`` then removes
    every sink, so they would never reach the configured file.  Recording them
    here lets :func:`_finish_load_capture` replay them into the configured console
    and file sinks.

    Replayed lines go through the configured levels, so a level set above the
    record's severity suppresses it -- exactly as it would for a line logged
    after startup.

    Returns:
        The list that receives ``(level, message)`` pairs and its sink id.
    """
    captured: list[tuple[str, str]] = []

    def _record(message: Any) -> None:
        captured.append((message.record["level"].name, message.record["message"]))

    _logger.remove()
    return captured, _logger.add(_record, level="DEBUG")


def _finish_load_capture(
    captured: list[tuple[str, str]],
    handler_id: int,
    *,
    to_default_sink: bool = False,
) -> None:
    """Drop the recorder and re-emit the captured lines through the active sinks.

    Each line keeps its recorded severity, so the configured console and file
    levels apply to it exactly as they would to a line logged after startup.

    Args:
        captured: Pairs recorded by :func:`_start_load_capture`.
        handler_id: Id of the recorder sink, removed before replaying.
        to_default_sink: Restore a plain stderr sink first.  Used when no
            configured logger is available: a failed load, or a logger that
            could not be created.
    """
    # create_logger() removes every sink, so the recorder may already be gone.
    with contextlib.suppress(ValueError):
        _logger.remove(handler_id)
    if to_default_sink:
        # No configured sink survived: replace whatever is left with plain stderr.
        _logger.remove()
        _logger.add(sink=sys.stderr, level="DEBUG")
    for level, message in captured:
        _logger.log(level, message)


def _apply_general_overrides(config: Config, overrides: dict[str, object]) -> None:
    if overrides.get("db_path") is not None:
        config.general.db_path = str(overrides["db_path"])
    if overrides.get("pid_path") is not None:
        config.general.pid_path = str(overrides["pid_path"])
    if overrides.get("library_path_list") is not None:
        config.general.library_path_list = [
            p.strip() for p in str(overrides["library_path_list"]).split(",") if p.strip()
        ]


def _apply_qbt_overrides(config: Config, overrides: dict[str, object]) -> None:
    if overrides.get("qbt_host") is not None:
        config.torrent_client.qbittorrent.host = str(overrides["qbt_host"])
    if overrides.get("qbt_port") is not None:
        config.torrent_client.qbittorrent.port = cast("int", overrides["qbt_port"])
    if overrides.get("qbt_username") is not None:
        config.torrent_client.qbittorrent.username = str(overrides["qbt_username"])
    if overrides.get("qbt_password") is not None:
        config.torrent_client.qbittorrent.password = str(overrides["qbt_password"])


def _apply_index_proxy_overrides(config: Config, overrides: dict[str, object]) -> None:
    if overrides.get("index_proxy") is not None:
        config.index_proxy.selected = str(overrides["index_proxy"])
    if overrides.get("jackett_host") is not None:
        config.index_proxy.jackett.host = str(overrides["jackett_host"])
    if overrides.get("jackett_port") is not None:
        config.index_proxy.jackett.port = cast("int", overrides["jackett_port"])
    if overrides.get("jackett_api_key") is not None:
        config.index_proxy.jackett.api_key = str(overrides["jackett_api_key"])
    if overrides.get("prowlarr_host") is not None:
        config.index_proxy.prowlarr.host = str(overrides["prowlarr_host"])
    if overrides.get("prowlarr_port") is not None:
        config.index_proxy.prowlarr.port = cast("int", overrides["prowlarr_port"])
    if overrides.get("prowlarr_api_key") is not None:
        config.index_proxy.prowlarr.api_key = str(overrides["prowlarr_api_key"])


def _apply_cli_overrides(config: Config, **overrides: object) -> None:
    """Apply non-None CLI override values onto *config* in-place.

    Any kwarg whose value is ``None`` is skipped (user did not supply it).
    """
    _apply_general_overrides(config, overrides)
    _apply_qbt_overrides(config, overrides)
    _apply_index_proxy_overrides(config, overrides)


@click.command()
@click.option(
    "--config-path",
    type=click.Path(file_okay=False, dir_okay=True, resolve_path=True),
    default="configs",
    show_default=True,
    metavar="<dir>",
    help="Directory containing movarr.yml configuration file.",
)
@click.option(
    "--log-path",
    type=click.Path(file_okay=False, dir_okay=True, resolve_path=True),
    default=None,
    show_default=False,
    metavar="<dir>",
    help="Override the log directory from config.",
)
@click.option(
    "--log-level",
    default=None,
    type=click.Choice(["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False),
    show_default=False,
    metavar="<level>",
    help="Override the log level from config (useful for debugging).",
)
@click.option(
    "--db-path",
    type=click.Path(file_okay=False, dir_okay=True, resolve_path=True),
    default=None,
    show_default=False,
    metavar="<dir>",
    help="Override the database directory from config.",
)
@click.option(
    "--pid-path",
    type=click.Path(file_okay=False, dir_okay=True, resolve_path=True),
    default=None,
    show_default=False,
    metavar="<dir>",
    help="Override the PID file directory from config.",
)
@click.option(
    "--library-path",
    "library_path_list",
    default=None,
    show_default=False,
    metavar="<path[,path...]>",
    help="Comma-separated library paths, overrides config (e.g. /media/movies,/media/4k).",
)
@click.option(
    "--qbt-host",
    default=None,
    show_default=False,
    metavar="<host>",
    help="Override qBittorrent host from config.",
)
@click.option(
    "--qbt-port",
    type=int,
    default=None,
    show_default=False,
    metavar="<port>",
    help="Override qBittorrent WebUI port from config.",
)
@click.option(
    "--qbt-username",
    default=None,
    show_default=False,
    metavar="<user>",
    help="Override qBittorrent username from config.",
)
@click.option(
    "--qbt-password",
    default=None,
    show_default=False,
    metavar="<pass>",
    help="Override qBittorrent password from config.",
)
@click.option(
    "--index-proxy",
    type=click.Choice(["jackett", "prowlarr"], case_sensitive=False),
    default=None,
    show_default=False,
    metavar="<proxy>",
    help="Override index proxy selection from config (jackett or prowlarr).",
)
@click.option(
    "--jackett-host",
    default=None,
    show_default=False,
    metavar="<host>",
    help="Override Jackett host from config.",
)
@click.option(
    "--jackett-port",
    type=int,
    default=None,
    show_default=False,
    metavar="<port>",
    help="Override Jackett port from config.",
)
@click.option(
    "--jackett-api-key",
    default=None,
    show_default=False,
    metavar="<key>",
    help="Override Jackett API key from config.",
)
@click.option(
    "--prowlarr-host",
    default=None,
    show_default=False,
    metavar="<host>",
    help="Override Prowlarr host from config.",
)
@click.option(
    "--prowlarr-port",
    type=int,
    default=None,
    show_default=False,
    metavar="<port>",
    help="Override Prowlarr port from config.",
)
@click.option(
    "--prowlarr-api-key",
    default=None,
    show_default=False,
    metavar="<key>",
    help="Override Prowlarr API key from config.",
)
@click.option(
    "--once",
    is_flag=True,
    default=False,
    help="Run each enabled scheduler task once, then exit (foreground mode).",
)
@click.option(
    "--test",
    is_flag=True,
    default=False,
    help="Validate configuration and exit without running any tasks.",
)
@click.version_option(version=_VERSION, prog_name="movarr")
def cli(
    config_path: str,
    log_path: str | None,
    log_level: str | None,
    db_path: str | None,
    pid_path: str | None,
    library_path_list: str | None,
    qbt_host: str | None,
    qbt_port: int | None,
    qbt_username: str | None,
    qbt_password: str | None,
    index_proxy: str | None,
    jackett_host: str | None,
    jackett_port: int | None,
    jackett_api_key: str | None,
    prowlarr_host: str | None,
    prowlarr_port: int | None,
    prowlarr_api_key: str | None,
    once: bool,
    test: bool,
) -> None:
    """movarr — torrent acquisition daemon.

    Polls Jackett/Prowlarr for movie torrents, filters by IMDb metadata, adds
    passing torrents to qBittorrent, post-processes completed downloads, and
    sends notifications.

    All paths (database, log, PID) and log levels are configured in
    the YAML file inside --config-path.  Use --log-level to override
    the console log level at runtime without editing the config file.
    """
    from movarr.config import load_config  # noqa: PLC0415

    captured, capture_id = _start_load_capture()
    try:
        config = load_config(config_path)
    except Exception:
        _finish_load_capture(captured, capture_id, to_default_sink=True)
        raise

    _apply_cli_overrides(
        config,
        db_path=db_path,
        pid_path=pid_path,
        library_path_list=library_path_list,
        qbt_host=qbt_host,
        qbt_port=qbt_port,
        qbt_username=qbt_username,
        qbt_password=qbt_password,
        index_proxy=index_proxy,
        jackett_host=jackett_host,
        jackett_port=jackett_port,
        jackett_api_key=jackett_api_key,
        prowlarr_host=prowlarr_host,
        prowlarr_port=prowlarr_port,
        prowlarr_api_key=prowlarr_api_key,
    )

    def _log_format(record: dict) -> str:
        tracker = record["extra"].get("tracker", "")
        # Braces are format syntax and angle brackets are colour syntax to
        # loguru: an indexer-controlled tracker name must not be able to corrupt
        # (or break) every log record.
        safe_tracker = str(tracker).translate(_LOG_UNSAFE_CHARS)
        prefix = f"[{safe_tracker}] " if tracker else ""
        return (
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            f"<level>{prefix}{{message}}</level>\n"
        )

    # --log-level / --log-path override config values when supplied.
    effective_log_level = log_level.upper() if log_level else config.general.log_level_console
    effective_log_path = log_path if log_path is not None else (config.general.log_path or None)

    create_logger_ok = False
    try:
        create_logger(
            log_format=_log_format,
            log_level=effective_log_level,
            log_level_file=config.general.log_level_file.upper(),
            log_path=effective_log_path,
        )
        create_logger_ok = True
    finally:
        # A logger that cannot be created (unopenable log path) must not swallow the
        # diagnostics captured while the config was loaded.
        _finish_load_capture(captured, capture_id, to_default_sink=not create_logger_ok)
    _logger.info("movarr v{}", _VERSION)

    if test:
        click.echo("Configuration loaded successfully. Test mode — exiting.")
        return

    if once:
        from movarr.scheduler import run_once  # noqa: PLC0415

        run_once(config)
        return

    from movarr.scheduler import run  # noqa: PLC0415

    run(config)


if __name__ == "__main__":
    cli()
