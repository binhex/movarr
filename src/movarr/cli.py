"""Command-line interface for movarr."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, cast

import click
from loguru import logger as _logger

from movarr.logger import create_logger

if TYPE_CHECKING:
    from movarr.config import Config

try:
    _VERSION = version("movarr")
except PackageNotFoundError:
    _VERSION = "unknown"


def _apply_cli_overrides(config: Config, **overrides: object) -> None:
    """Apply non-None CLI override values onto *config* in-place.

    Any kwarg whose value is ``None`` is skipped (user did not supply it).
    """
    if overrides.get("db_path") is not None:
        config.general.db_path = str(overrides["db_path"])
    if overrides.get("library_path_list") is not None:
        config.general.library_path_list = [
            p.strip() for p in str(overrides["library_path_list"]).split(",") if p.strip()
        ]
    if overrides.get("qbt_host") is not None:
        config.torrent_client.qbittorrent.host = str(overrides["qbt_host"])
    if overrides.get("qbt_port") is not None:
        config.torrent_client.qbittorrent.port = cast("int", overrides["qbt_port"])
    if overrides.get("qbt_username") is not None:
        config.torrent_client.qbittorrent.username = str(overrides["qbt_username"])
    if overrides.get("qbt_password") is not None:
        config.torrent_client.qbittorrent.password = str(overrides["qbt_password"])
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


@click.command()
@click.option(
    "--config-path",
    type=click.Path(file_okay=True, dir_okay=False, resolve_path=True),
    default="configs/movarr.yml",
    show_default=True,
    metavar="<path>",
    help="Path to YAML configuration file.",
)
@click.option(
    "--log-path",
    type=click.Path(file_okay=True, dir_okay=False, resolve_path=True),
    default=None,
    show_default=False,
    metavar="<path>",
    help="Override the log file path from config.",
)
@click.option(
    "--log-level",
    default=None,
    type=click.Choice(["DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR"], case_sensitive=False),
    show_default=False,
    metavar="<level>",
    help="Override the log level from config (useful for debugging).",
)
@click.option(
    "--db-path",
    type=click.Path(file_okay=True, dir_okay=False, resolve_path=True),
    default=None,
    show_default=False,
    metavar="<path>",
    help="Override the database file path from config.",
)
@click.option(
    "--library-path-list",
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
    "--daemon",
    is_flag=True,
    default=False,
    help="Run in daemon (background) mode; otherwise single-pass foreground.",
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
    daemon: bool,
    test: bool,
) -> None:
    """movarr — torrent acquisition daemon.

    Polls Jackett/Prowlarr for movie torrents, filters by IMDb metadata, adds
    passing torrents to qBittorrent, post-processes completed downloads, and
    sends notifications.

    All paths (database, log file, PID file) and log levels are configured in
    the YAML file pointed to by --config-path.  Use --log-level to override
    the console log level at runtime without editing the config file.
    """
    from movarr.config import load_config  # noqa: PLC0415

    config = load_config(config_path)

    # --daemon flag overrides general.daemon_mode in config.
    if daemon:
        config.general.daemon_mode = "background"

    _apply_cli_overrides(
        config,
        db_path=db_path,
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
        prefix = f"[{tracker}] " if tracker else ""
        return (
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            f"<level>{prefix}{{message}}</level>\n"
        )

    # --log-level / --log-path override config values when supplied.
    effective_log_level = log_level.upper() if log_level else config.general.log_level_console
    effective_log_path = log_path if log_path is not None else (config.general.log_path or None)

    create_logger(
        log_format=_log_format,
        log_level=effective_log_level,
        log_level_file=config.general.log_level_file.upper(),
        log_path=effective_log_path,
    )
    _logger.info("movarr v{}", _VERSION)

    if test:
        click.echo("Configuration loaded successfully. Test mode — exiting.")
        return

    from movarr.scheduler import run  # noqa: PLC0415

    run(config)


if __name__ == "__main__":
    cli()
