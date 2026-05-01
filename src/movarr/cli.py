"""Command-line interface for movarr."""

from importlib.metadata import PackageNotFoundError, version

import click

from movarr.logger import create_logger
from movarr.utils import get_project_root

try:
    _VERSION = version("movarr")
except PackageNotFoundError:
    _VERSION = "unknown"

# Compute default database path (project_root/db/movarr.db)
_PROJECT_ROOT = get_project_root()
_DEFAULT_DB_PATH = f"{_PROJECT_ROOT}/db/movarr.db"
_DEFAULT_LOGS_PATH = f"{_PROJECT_ROOT}/logs/movarr.log"


@click.command()
@click.option(
    "--database-path",
    type=click.Path(file_okay=True, dir_okay=False, resolve_path=True),
    required=False,
    default=_DEFAULT_DB_PATH,
    show_default=True,
    metavar="<path>",
    help="Path to SQLite database file for tracking processed files.",
)
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR"], case_sensitive=False),
    metavar="<level>",
    show_default=True,
    help="Logging level for console output",
)
@click.option(
    "--log-path",
    type=click.Path(file_okay=True, dir_okay=False, resolve_path=True),
    required=False,
    default=_DEFAULT_LOGS_PATH,
    show_default=True,
    metavar="<path>",
    help="Path to log file for tracking application events.",
)

@click.version_option()
@click.version_option(version=_VERSION, prog_name="movarr")
def cli(
    database_path: str | None,
    log_level: str,
    log_path: str,
) -> None:
    """movarr - Short description.

    Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna
    aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.
    Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur
    sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.
    """

    # Logger format for consistent output styling
    log_format = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>"

    logger = create_logger(log_format=log_format, log_level=log_level, log_path=log_path)

    logger.info("WIP: CLI logic not yet implemented.")

if __name__ == "__main__":
    cli()
