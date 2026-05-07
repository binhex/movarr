"""Command-line interface for movarr."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

import click

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

    _apply_cli_overrides(config, db_path=db_path, library_path_list=library_path_list)

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
        log_path=effective_log_path,
    )

    if test:
        click.echo("Configuration loaded successfully. Test mode — exiting.")
        return

    from movarr.scheduler import run  # noqa: PLC0415

    run(config)


if __name__ == "__main__":
    cli()
