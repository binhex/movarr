"""Command-line interface for movarr."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import click

from movarr.logger import create_logger
from movarr.utils import get_project_root

try:
    _VERSION = version("movarr")
except PackageNotFoundError:
    _VERSION = "unknown"

_PROJECT_ROOT = get_project_root()


@click.command()
@click.option(
    "--config-path",
    type=click.Path(file_okay=True, dir_okay=False, resolve_path=True),
    default=f"{_PROJECT_ROOT}/configs/movarr.yml",
    show_default=True,
    metavar="<path>",
    help="Path to YAML configuration file.",
)
@click.option(
    "--db-path",
    type=click.Path(file_okay=True, dir_okay=False, resolve_path=True),
    default=f"{_PROJECT_ROOT}/db/movarr.db",
    show_default=True,
    metavar="<path>",
    help="Path to SQLite database file.",
)
@click.option(
    "--log-path",
    type=click.Path(file_okay=True, dir_okay=False, resolve_path=True),
    default=f"{_PROJECT_ROOT}/logs/movarr.log",
    show_default=True,
    metavar="<path>",
    help="Path to log file.",
)
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR"], case_sensitive=False),
    show_default=True,
    metavar="<level>",
    help="Logging level.",
)
@click.option(
    "--pid-path",
    type=click.Path(file_okay=True, dir_okay=False, resolve_path=True),
    default=None,
    show_default=False,
    metavar="<path>",
    help="Path to PID file (daemon mode). Defaults to movarr.pid in the config directory.",
)
@click.option(
    "--ffprobe-path",
    type=click.Path(file_okay=True, dir_okay=False, resolve_path=True),
    default="/usr/bin/ffprobe",
    show_default=True,
    metavar="<path>",
    help="Path to ffprobe binary.",
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
    db_path: str,
    log_path: str,
    log_level: str,
    pid_path: str | None,
    ffprobe_path: str,
    daemon: bool,
    test: bool,
) -> None:
    """movarr — torrent acquisition daemon.

    Polls Jackett for movie torrents, filters by IMDb metadata, adds passing
    torrents to qBittorrent, post-processes completed downloads, and sends
    email notifications.
    """

    def _log_format(record: dict) -> str:  # type: ignore[type-arg]
        tracker = record["extra"].get("tracker", "")
        prefix = f"[{tracker}] " if tracker else ""
        return (
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            f"<level>{prefix}{{message}}</level>\n"
        )

    create_logger(log_format=_log_format, log_level=log_level, log_path=log_path)

    if pid_path is None:
        pid_path = str(Path(config_path).parent / "movarr.pid")

    from movarr.config import load_config

    config = load_config(config_path)

    # Override config paths with CLI values so they take precedence.
    config.general.db_path = db_path
    config.general.daemon_mode = "background" if daemon else "foreground"
    if ffprobe_path:
        config.general.ffprobe_path = ffprobe_path

    if test:
        click.echo("Configuration loaded successfully. Test mode — exiting.")
        return

    from movarr.scheduler import run

    run(config, pid_path=pid_path)


if __name__ == "__main__":
    cli()
