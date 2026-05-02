"""SQLite history database using SQLAlchemy for movarr."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import Column, Integer, String, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from movarr.models import ResultDict

__all__ = ["Database", "HistoryRecord"]

_DB_VERSION = 7
_logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""


class HistoryRecord(Base):
    """ORM mapping for the ``history`` table."""

    __tablename__ = "history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    index_title = Column(String)
    result = Column(String)
    result_details = Column(String)
    index_details = Column(String)
    index_pubdate = Column(String)
    index_seeders = Column(String)
    index_peers = Column(String)
    index_size = Column(String)
    index_size_mb = Column(String)
    torrent_url = Column(String)
    torrent_tag = Column(String)
    magnet_url = Column(String)
    category = Column(String)
    verified = Column(String)
    imdb_id = Column(String)
    imdb_title = Column(String)
    imdb_year = Column(String)
    imdb_poster_url = Column(String)
    imdb_trailer_url = Column(String)
    imdb_plot_summary = Column(String)
    imdb_plot_outline = Column(String)
    imdb_rating = Column(String)
    imdb_votes = Column(String)
    imdb_title_type = Column(String)
    imdb_running_time_in_minutes = Column(String)
    imdb_genres_list = Column(String)
    imdb_credits_director_list = Column(String)
    imdb_credits_writer_list = Column(String)
    imdb_credits_cast_list = Column(String)
    imdb_credits_character_list = Column(String)
    imdb_language_list = Column(String)
    imdb_country_list = Column(String)
    imdb_certification = Column(String)
    imdb_cert_source = Column(String)


class Database:
    """SQLite history database wrapper.

    Each instance owns its own engine + session, satisfying SQLite's
    thread-safety requirement (one connection per thread).

    Args:
        db_path: Path to the ``.db`` file. Parent directories are created
            automatically.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_engine(f"sqlite:///{self._db_path}", echo=False)
        self._Session = sessionmaker(bind=self._engine)
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------

    def _get_user_version(self) -> int:
        """Return the SQLite ``user_version`` pragma value."""
        with self._engine.connect() as conn:
            result = conn.execute(text("PRAGMA user_version"))
            return int(result.scalar() or 0)

    def _set_user_version(self, version: int) -> None:
        """Set the SQLite ``user_version`` pragma."""
        with self._engine.connect() as conn:
            conn.execute(text(f"PRAGMA user_version = {version}"))
            conn.commit()

    def _ensure_schema(self) -> None:
        """Create tables if absent; run migrations if version mismatches."""
        Base.metadata.create_all(self._engine)
        current = self._get_user_version()
        if current == 0:
            _logger.info("New database — setting schema version to %d.", _DB_VERSION)
            self._set_user_version(_DB_VERSION)
        elif current < _DB_VERSION:
            _logger.info("Upgrading database from v%d to v%d.", current, _DB_VERSION)
            self._upgrade(current)

    def _upgrade(self, from_version: int) -> None:
        """Apply incremental schema migrations."""
        with self._engine.connect() as conn:
            if from_version < 7:
                conn.execute(text("ALTER TABLE history ADD COLUMN imdb_certification TEXT"))
                conn.execute(text("ALTER TABLE history ADD COLUMN imdb_cert_source TEXT"))
                conn.commit()
        self._set_user_version(_DB_VERSION)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write(self, result: ResultDict) -> None:
        """Insert a pipeline result into the history table.

        List-valued fields are JSON-encoded before storage.

        Args:
            result: The completed :class:`~movarr.models.ResultDict`.
        """

        def _enc(value: object) -> str | None:
            if value is None:
                return None
            if isinstance(value, list):
                return json.dumps(value)
            return str(value)

        record = HistoryRecord(
            index_title=result.get("index_title"),
            result=result.get("result"),
            result_details=_enc(result.get("result_details")),
            index_details=result.get("index_details"),
            index_pubdate=result.get("index_pubdate"),
            index_seeders=result.get("index_seeders"),
            index_peers=result.get("index_peers"),
            index_size=result.get("index_size"),
            index_size_mb=result.get("index_size_mb"),
            torrent_url=result.get("torrent_url"),
            torrent_tag=result.get("torrent_tag"),
            magnet_url=result.get("magnet_url"),
            category=result.get("category"),
            verified=result.get("verified"),
            imdb_id=result.get("imdb_id"),
            imdb_title=result.get("imdb_title"),
            imdb_year=result.get("imdb_year"),
            imdb_poster_url=result.get("imdb_poster_url"),
            imdb_trailer_url=result.get("imdb_trailer_url"),
            imdb_plot_summary=result.get("imdb_plot_summary"),
            imdb_plot_outline=result.get("imdb_plot_outline"),
            imdb_rating=result.get("imdb_rating"),
            imdb_votes=result.get("imdb_votes"),
            imdb_title_type=result.get("imdb_title_type"),
            imdb_running_time_in_minutes=result.get("imdb_running_time_in_minutes"),
            imdb_genres_list=_enc(result.get("imdb_genres_list")),
            imdb_credits_director_list=_enc(result.get("imdb_credits_director_list")),
            imdb_credits_writer_list=_enc(result.get("imdb_credits_writer_list")),
            imdb_credits_cast_list=_enc(result.get("imdb_credits_cast_list")),
            imdb_credits_character_list=_enc(result.get("imdb_credits_character_list")),
            imdb_language_list=_enc(result.get("imdb_language_list")),
            imdb_country_list=_enc(result.get("imdb_country_list")),
            imdb_certification=result.get("imdb_certification"),
            imdb_cert_source=result.get("imdb_cert_source"),
        )
        with Session(self._engine) as session:
            session.add(record)
            session.commit()

    def set_verified(self, torrent_tag: str) -> None:
        """Mark a history record as verified after a successful file copy.

        Args:
            torrent_tag: The UUID tag that identifies the torrent.
        """
        with Session(self._engine) as session:
            session.query(HistoryRecord).filter_by(torrent_tag=torrent_tag).update({"verified": "true"})
            session.commit()

    # ------------------------------------------------------------------
    # Read / deduplication
    # ------------------------------------------------------------------

    def is_duplicate_exact(self, index_title: str) -> bool:
        """Return True if *index_title* already exists in history (exact match).

        Args:
            index_title: The raw index torrent title.
        """
        with Session(self._engine) as session:
            return session.query(HistoryRecord).filter_by(index_title=index_title).first() is not None

    def is_duplicate_fuzzy(self, pattern: str) -> bool:
        """Return True if *pattern* matches any existing history title (LIKE match).

        *pattern* should already be in ``%…%`` form (produced by
        :func:`~movarr.parsing.build_sqlite_pattern`).

        Args:
            pattern: A ``LIKE``-style pattern string.
        """
        with Session(self._engine) as session:
            row = session.query(HistoryRecord).filter(HistoryRecord.index_title.like(pattern)).first()
            return row is not None

    def is_verified(self, torrent_tag: str) -> bool:
        """Return True if the torrent has already been post-processed.

        Args:
            torrent_tag: The UUID tag that identifies the torrent.
        """
        with Session(self._engine) as session:
            row = session.query(HistoryRecord).filter_by(torrent_tag=torrent_tag, verified="true").first()
            return row is not None

    def find_by_tag(self, torrent_tag: str) -> HistoryRecord | None:
        """Fetch a history record by its torrent tag.

        Args:
            torrent_tag: The UUID tag that identifies the torrent.
        """
        with Session(self._engine) as session:
            return session.query(HistoryRecord).filter_by(torrent_tag=torrent_tag).first()

    def read_by_tag(self, torrent_tag: str) -> HistoryRecord | None:
        """Alias for :meth:`find_by_tag` — returns a history record by tag."""
        return self.find_by_tag(torrent_tag)

    def genres_for_tag(self, torrent_tag: str) -> list[str]:
        """Return the genre list stored for *torrent_tag*, or an empty list.

        Handles both JSON-encoded and Python-repr encoded lists (legacy).

        Args:
            torrent_tag: The UUID tag that identifies the torrent.
        """
        record = self.find_by_tag(torrent_tag)
        if record is None or not record.imdb_genres_list:
            return []
        raw = record.imdb_genres_list.strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # Legacy: Python repr list e.g. "['Action', 'Comedy']"
        try:
            import ast

            value = ast.literal_eval(raw)
            if isinstance(value, list):
                return [str(g) for g in value]
        except (ValueError, SyntaxError):
            pass
        return []

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def vacuum(self) -> None:
        """Run VACUUM to reclaim disk space from deleted rows."""
        with self._engine.connect() as conn:
            conn.execute(text("VACUUM"))
            conn.commit()
