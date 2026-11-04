"""SQLite history database using SQLAlchemy for movarr."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from loguru import logger as _logger
from sqlalchemy import Column, Integer, String, create_engine, func, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from movarr.config import DB_FILENAME
from movarr.utils import redact_secrets

if TYPE_CHECKING:
    from movarr.models import ResultDict

__all__ = ["Database", "HistoryRecord", "KvRecord"]

_DB_VERSION = 13
_SCHEMA_V7_CERT_FIELDS = 7
_SCHEMA_V8_INDEX_TITLE_IDX = 8
_SCHEMA_V9_STALLED_AT = 9
_SCHEMA_V10_CREATED_AT = 10
_SCHEMA_V11_KV_STORE = 11
_SCHEMA_V12_KV_KEY_RENAME = 12
_SCHEMA_V13_DUAL_TITLE = 13


def _encode_field(value: object) -> str | None:
    """Serialise a pipeline field for storage: lists → JSON, None → None, else str."""
    if value is None:
        return None
    if isinstance(value, list):
        return json.dumps(value)
    return str(value)


def _decode_field(value: object) -> Any:
    """Deserialise a stored field: JSON strings → object, None → None, else passthrough."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    return value


def _add_column_if_absent(conn: Any, column: str, col_type: str = "TEXT") -> None:
    """Add *column* of *col_type* to the history table if it does not already exist."""
    cursor = conn.execute(text("PRAGMA table_info(history)"))
    existing_cols = {row[1] for row in cursor.fetchall()}
    if column not in existing_cols:
        conn.execute(text(f"ALTER TABLE history ADD COLUMN {column} {col_type}"))


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""


class HistoryRecord(Base):
    """ORM mapping for the ``history`` table."""

    __tablename__ = "history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    index_title = Column(String, index=True)
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
    imdb_original_title = Column(String)
    imdb_alternate_titles = Column(String)
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
    stalled_at = Column(String)
    created_at = Column(String)


class KvRecord(Base):
    """A single key-value entry in the persistent kv_store table."""

    __tablename__ = "kv_store"

    key: Any = Column(String, primary_key=True)
    value: Any = Column(String, nullable=True)
    updated_at: Any = Column(String, nullable=True)


class Database:
    """SQLite history database wrapper.

    Each instance owns its own engine, and every operation opens a short-lived
    session, so SQLite's "one connection per thread" rule is honoured even
    though a single ``Database`` instance is shared by the scheduler's job
    threads (``scheduler._run_daemon``).  Concurrent writers can still collide
    on the database lock; SQLite serialises them and the default busy timeout
    applies.

    Args:
        db_path: Path to the database.  A directory, or a path without a
            suffix, gets ``movarr.db`` created inside it; any other path is
            used as the database file itself.  Parent directories are created
            automatically.
    """

    def __init__(self, db_path: str | Path) -> None:
        db_path_obj = Path(db_path)
        # Prefer the filesystem when it can answer the question: a directory
        # whose name contains a dot (e.g. ``/data/movarr-1.3``) is still a
        # directory.  Fall back to the suffix heuristic for paths that do not
        # exist yet.
        if db_path_obj.is_dir() or not db_path_obj.suffix:
            self._db_path = db_path_obj / DB_FILENAME
        else:
            self._db_path = db_path_obj
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_engine(
            f"sqlite:///{self._db_path}",
            echo=False,
            connect_args={"check_same_thread": False},
        )
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

    def _history_table_exists(self) -> bool:
        """Return True if the history table already exists in the database."""
        with self._engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='history'"))
            return result.fetchone() is not None

    def _ensure_schema(self) -> None:
        """Create tables if absent; migrate an older schema and warn on a newer one."""
        table_existed = self._history_table_exists()
        Base.metadata.create_all(self._engine)
        current = self._get_user_version()
        if current == 0:
            if table_existed:
                _logger.info("Unversioned legacy database — applying all migrations.")
                self._upgrade(0)
            else:
                _logger.info("New database — setting schema version to {}.", _DB_VERSION)
                self._set_user_version(_DB_VERSION)
        elif current < _DB_VERSION:
            _logger.info("Upgrading database from v{} to v{}.", current, _DB_VERSION)
            self._upgrade(current)
        elif current > _DB_VERSION:
            _logger.warning(
                "Database schema v{} is newer than this build (v{}); continuing read/write — "
                "downgrade the database or upgrade movarr if behaviour looks wrong.",
                current,
                _DB_VERSION,
            )

    def _repair_missing_columns(self, conn: Any) -> None:
        """Add any ORM-mapped column that a pre-versioning legacy table lacks.

        ``Base.metadata.create_all`` never alters an existing table, and the
        v7+ migrations only add the columns introduced after v7.  A v5-era
        table therefore reached the current version without ``torrent_tag`` or
        ``verified``, and every write then failed with "no such column".
        Column names and types come from this module's own ORM metadata, so no
        external input reaches the DDL.
        """
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(history)"))}
        for column in HistoryRecord.__table__.columns:
            if column.primary_key or column.name in existing:
                continue
            col_type = column.type.compile(dialect=self._engine.dialect)
            conn.execute(text(f"ALTER TABLE history ADD COLUMN {column.name} {col_type}"))
        conn.commit()

    def _upgrade(self, from_version: int) -> None:
        """Apply incremental schema migrations."""
        with self._engine.connect() as conn:
            if from_version < _SCHEMA_V7_CERT_FIELDS:
                self._repair_missing_columns(conn)
                self._upgrade_v7_cert_fields(conn)
            if from_version < _SCHEMA_V8_INDEX_TITLE_IDX:
                self._upgrade_v8_index_title_idx(conn)
            if from_version < _SCHEMA_V9_STALLED_AT:
                self._upgrade_v9_stalled_at(conn)
            if from_version < _SCHEMA_V10_CREATED_AT:
                self._upgrade_v10_created_at(conn)
            if from_version < _SCHEMA_V11_KV_STORE:
                self._upgrade_v11_kv_store(conn)
            if from_version < _SCHEMA_V12_KV_KEY_RENAME:
                self._upgrade_v12_kv_key_rename(conn)
            if from_version < _SCHEMA_V13_DUAL_TITLE:
                self._upgrade_v13_dual_title(conn)
        self._set_user_version(_DB_VERSION)

    def _upgrade_v7_cert_fields(self, conn: Any) -> None:
        """Add imdb_certification and imdb_cert_source columns to history."""
        _add_column_if_absent(conn, "imdb_certification")
        _add_column_if_absent(conn, "imdb_cert_source")
        conn.commit()

    def _upgrade_v8_index_title_idx(self, conn: Any) -> None:
        """Create index on history.index_title."""
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_history_index_title ON history (index_title)"))
        conn.commit()

    def _upgrade_v9_stalled_at(self, conn: Any) -> None:
        """Add stalled_at column to history."""
        _add_column_if_absent(conn, "stalled_at")
        conn.commit()

    def _upgrade_v10_created_at(self, conn: Any) -> None:
        """Add created_at column to history."""
        _add_column_if_absent(conn, "created_at")
        conn.commit()

    def _upgrade_v11_kv_store(self, conn: Any) -> None:
        """Create kv_store table."""
        conn.execute(text("CREATE TABLE IF NOT EXISTS kv_store (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)"))
        conn.commit()

    def _upgrade_v12_kv_key_rename(self, conn: Any) -> None:
        """Rename index_proxy.zero_results_since key to index_proxy.unavailable_since."""
        # Idempotent under a downgrade/restore where both keys exist: the UPDATE
        # alone would violate the primary key and abort startup.
        conn.execute(text("DELETE FROM kv_store WHERE key = 'index_proxy.unavailable_since'"))
        conn.execute(
            text(
                "UPDATE kv_store SET key = 'index_proxy.unavailable_since' WHERE key = 'index_proxy.zero_results_since'"
            )
        )
        conn.commit()

    def _upgrade_v13_dual_title(self, conn: Any) -> None:
        """Add imdb_original_title and imdb_alternate_titles columns to history."""
        _add_column_if_absent(conn, "imdb_original_title")
        _add_column_if_absent(conn, "imdb_alternate_titles")
        conn.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write(self, result: ResultDict) -> None:
        """Insert a pipeline result into the history table.

        List-valued fields are JSON-encoded before storage.

        Args:
            result: The completed :class:`~movarr.models.ResultDict`.
        """
        record = self._result_to_record(result)
        with Session(self._engine) as session:
            session.add(record)
            session.commit()

    @staticmethod
    def _result_to_record(result: ResultDict) -> HistoryRecord:
        """Build a :class:`HistoryRecord` from a pipeline *result*."""
        return HistoryRecord(
            index_title=result.get("index_title"),
            result=result.get("result"),
            result_details=_encode_field(result.get("result_details")),
            index_details=result.get("index_details"),
            index_pubdate=result.get("index_pubdate"),
            index_seeders=result.get("index_seeders"),
            index_peers=result.get("index_peers"),
            index_size=result.get("index_size"),
            index_size_mb=result.get("index_size_mb"),
            # Jackett/Prowlarr download URLs can carry the indexer API key.
            torrent_url=redact_secrets(result.get("torrent_url") or ""),
            torrent_tag=result.get("torrent_tag"),
            magnet_url=redact_secrets(result.get("magnet_url") or ""),
            category=result.get("category"),
            verified=result.get("verified"),
            imdb_id=result.get("imdb_id"),
            imdb_title=result.get("imdb_title"),
            imdb_original_title=result.get("imdb_original_title"),
            imdb_alternate_titles=_encode_field(result.get("imdb_alternate_titles")),
            imdb_year=result.get("imdb_year"),
            imdb_poster_url=result.get("imdb_poster_url"),
            imdb_trailer_url=result.get("imdb_trailer_url"),
            imdb_plot_summary=result.get("imdb_plot_summary"),
            imdb_plot_outline=result.get("imdb_plot_outline"),
            imdb_rating=result.get("imdb_rating"),
            imdb_votes=result.get("imdb_votes"),
            imdb_title_type=result.get("imdb_title_type"),
            imdb_running_time_in_minutes=result.get("imdb_running_time_in_minutes"),
            imdb_genres_list=_encode_field(result.get("imdb_genres_list")),
            imdb_credits_director_list=_encode_field(result.get("imdb_credits_director_list")),
            imdb_credits_writer_list=_encode_field(result.get("imdb_credits_writer_list")),
            imdb_credits_cast_list=_encode_field(result.get("imdb_credits_cast_list")),
            imdb_credits_character_list=_encode_field(result.get("imdb_credits_character_list")),
            imdb_language_list=_encode_field(result.get("imdb_language_list")),
            imdb_country_list=_encode_field(result.get("imdb_country_list")),
            imdb_certification=result.get("imdb_certification"),
            imdb_cert_source=result.get("imdb_cert_source"),
            created_at=datetime.datetime.now(tz=datetime.UTC).isoformat(),
        )

    def mark_stalled(self, torrent_tag: str) -> None:
        """Mark a torrent as stalled (deleted by queue manager with no seeds/peers).

        Sets ``result="Stalled"`` and records the UTC timestamp of detection.

        Args:
            torrent_tag: The UUID tag that identifies the torrent.
        """
        now = datetime.datetime.now(tz=datetime.UTC).isoformat()
        with Session(self._engine) as session:
            session.query(HistoryRecord).filter_by(torrent_tag=torrent_tag).update(
                {"result": "Stalled", "stalled_at": now}
            )
            session.commit()

    def mark_completed(self, torrent_tag: str) -> None:
        """Mark a torrent as completed after successful post-processing.

        Sets ``result="Completed"`` and ``verified="true"``.

        Args:
            torrent_tag: The UUID tag that identifies the torrent.
        """
        with Session(self._engine) as session:
            session.query(HistoryRecord).filter_by(torrent_tag=torrent_tag).update(
                {"result": "Completed", "verified": "true"}
            )
            session.commit()

    def _expire_by_result(self, result_value: str, timestamp_column: str, days: int) -> int:
        """Delete history rows with *result_value* older than *days* days.

        Uses *timestamp_column* as the age criterion.
        Returns the number of deleted rows.
        """
        if days <= 0:
            return 0
        cutoff = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=days)).isoformat()
        col_attr = getattr(HistoryRecord, timestamp_column)
        with Session(self._engine) as session:
            # Compare parsed timestamps: legacy rows written with SQLite's
            # CURRENT_TIMESTAMP use "YYYY-MM-DD HH:MM:SS", which sorts before any
            # ISO "T" string and would otherwise expire a day early.
            deleted = (
                session.query(HistoryRecord)
                .filter(
                    HistoryRecord.result == result_value,
                    col_attr.isnot(None),
                    func.datetime(col_attr) < func.datetime(cutoff),
                )
                .delete(synchronize_session=False)
            )
            session.commit()
        return int(deleted)

    def expire_stalled(self, days: int) -> int:
        """Delete stalled history records older than *days* days.

        Called at the start of each search run to allow retry of titles whose
        stalled record has expired.  A *days* value of 0 disables expiry (no
        records deleted).

        Args:
            days: Retention window in days.  0 = no expiry.

        Returns:
            Number of records deleted.
        """
        return self._expire_by_result("Stalled", "stalled_at", days)

    def expire_failed(self, days: int) -> int:
        """Delete failed history records older than *days* days.

        Called at the start of each search run to allow retry of titles whose
        failed record has expired (e.g. transient IMDb outage, changed criteria).
        A *days* value of 0 disables expiry (no records deleted).

        Args:
            days: Retention window in days.  0 = no expiry.

        Returns:
            Number of records deleted.
        """
        return self._expire_by_result("Failed", "created_at", days)

    def expire_passed(self, days: int) -> int:
        """Delete passed history records older than *days* days.

        Called at the start of each search run to allow retry of titles whose
        queued torrent was never completed (e.g. qBittorrent was reset externally).
        A *days* value of 0 disables expiry (no records deleted).

        .. warning::
            Only ``Passed`` rows with a ``created_at`` timestamp are eligible.
            Rows that transitioned to ``Completed`` or ``Stalled`` are excluded.
            Setting *days* too low risks expiring records for torrents that are
            still legitimately downloading (e.g. a very large file with few
            seeders).  The default of 30 days is intentionally conservative.

        Args:
            days: Retention window in days.  0 = no expiry.

        Returns:
            Number of records deleted.
        """
        return self._expire_by_result("Passed", "created_at", days)

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

    def find_by_tag(self, torrent_tag: str) -> HistoryRecord | None:
        """Fetch a history record by its torrent tag.

        The returned ``HistoryRecord`` is explicitly expunged from the session
        before it is returned.  This avoids ``DetachedInstanceError`` on any
        attribute access that would otherwise trigger a lazy load after the
        session has closed.  All scalar columns are loaded eagerly by the
        initial SELECT, so expunged instances are safe to read.

        Args:
            torrent_tag: The UUID tag that identifies the torrent.
        """
        with Session(self._engine) as session:
            row = session.query(HistoryRecord).filter_by(torrent_tag=torrent_tag).first()
            if row is not None:
                session.expunge(row)
            return row

    def find_imdb_metadata(self, imdb_id: str) -> dict[str, Any] | None:
        """Return cached IMDb metadata for *imdb_id* if available.

        Looks up the most recent history record with the given *imdb_id*
        that has ``imdb_title`` set (indicating a successful metadata fetch).
        Returns a plain dict suitable for merging into a pipeline result.

        Args:
            imdb_id: The IMDb identifier (e.g. ``"tt0095169"``).

        Returns:
            A dict of ``imdb_*`` fields, or ``None`` if no cache hit.
        """
        with Session(self._engine) as session:
            row = (
                session.query(HistoryRecord)
                .filter(
                    HistoryRecord.imdb_id == imdb_id,
                    HistoryRecord.imdb_title.isnot(None),
                )
                .order_by(HistoryRecord.id.desc())
                .first()
            )
            if row is not None:
                session.expunge(row)
        if row is None:
            return None

        return self._record_to_imdb_dict(row)

    @staticmethod
    def _record_to_imdb_dict(row: HistoryRecord) -> dict[str, Any]:
        """Convert a *row* into a plain dict of ``imdb_*`` fields."""
        return {
            "imdb_title": row.imdb_title,
            "imdb_original_title": row.imdb_original_title,
            "imdb_alternate_titles": _decode_field(row.imdb_alternate_titles),
            "imdb_year": row.imdb_year,
            "imdb_rating": row.imdb_rating,
            "imdb_votes": row.imdb_votes,
            "imdb_title_type": row.imdb_title_type,
            "imdb_running_time_in_minutes": row.imdb_running_time_in_minutes,
            "imdb_genres_list": _decode_field(row.imdb_genres_list),
            "imdb_plot_summary": row.imdb_plot_summary,
            "imdb_plot_outline": row.imdb_plot_outline,
            "imdb_poster_url": row.imdb_poster_url,
            "imdb_trailer_url": row.imdb_trailer_url,
            "imdb_language_list": _decode_field(row.imdb_language_list),
            "imdb_country_list": _decode_field(row.imdb_country_list),
            "imdb_certification": row.imdb_certification,
            "imdb_cert_source": row.imdb_cert_source,
            "imdb_credits_director_list": _decode_field(row.imdb_credits_director_list),
            "imdb_credits_writer_list": _decode_field(row.imdb_credits_writer_list),
            "imdb_credits_cast_list": _decode_field(row.imdb_credits_cast_list),
            "imdb_credits_character_list": _decode_field(row.imdb_credits_character_list),
        }

    # ------------------------------------------------------------------
    # Key-value store
    # ------------------------------------------------------------------

    def kv_get(self, key: str) -> str | None:
        """Return the stored string for *key*, or ``None`` if absent.

        Args:
            key: Dot-namespaced key string (e.g. ``"index_proxy.unavailable_since"``).

        Returns:
            The stored value, or ``None`` if the key does not exist.
        """
        with Session(self._engine) as session:
            record = session.get(KvRecord, key)
            return record.value if record is not None else None

    def kv_set(self, key: str, value: str) -> None:
        """Upsert *value* for *key* in the persistent kv store.

        Uses an atomic INSERT … ON CONFLICT DO UPDATE to be safe under
        concurrent access.  No row is loaded into the session, so two
        threads inserting the same key will not raise an IntegrityError.

        Args:
            key: Dot-namespaced key string.
            value: String value to store.
        """
        now = datetime.datetime.now(datetime.UTC).isoformat()
        with Session(self._engine) as session:
            stmt = (
                sqlite_insert(KvRecord)
                .values(key=key, value=value, updated_at=now)
                .on_conflict_do_update(
                    index_elements=[KvRecord.key],
                    set_={"value": value, "updated_at": now},
                )
            )
            session.execute(stmt)
            session.commit()

    def kv_set_if_absent(self, key: str, value: str) -> bool:
        """Insert *key* only when absent; return True when this call inserted it.

        Used to claim a one-shot action (e.g. an outage alert) across the
        scheduler's threads.  A plain read-then-write is racy: two jobs firing
        on the same wall clock can both observe "absent".
        """
        now = datetime.datetime.now(datetime.UTC).isoformat()
        stmt = (
            sqlite_insert(KvRecord)
            .values(key=key, value=value, updated_at=now)
            .on_conflict_do_nothing(index_elements=[KvRecord.key])
        )
        with Session(self._engine) as session:
            result = session.execute(stmt)
            session.commit()
            return bool(cast("Any", result).rowcount)

    def kv_delete(self, key: str) -> None:
        """Delete the entry for *key* if it exists (no-op if absent).

        Uses a bulk DELETE to avoid the ORM load-then-delete pattern.
        The old pattern loaded a row into the session before deleting it,
        which produced a ``SAWarning`` when two threads concurrently
        deleted the same key — the second thread saw a stale session
        object whose backing row had already been removed.  A bulk
        ``DELETE … WHERE key = ?`` is naturally idempotent and thread-safe.

        Args:
            key: Dot-namespaced key string.
        """
        with Session(self._engine) as session:
            session.query(KvRecord).filter_by(key=key).delete(synchronize_session=False)
            session.commit()
