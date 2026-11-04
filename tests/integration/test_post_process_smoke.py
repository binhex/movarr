"""Integration smoke test for the post-processing pipeline.

Drives :func:`movarr.post_processor.run_post_processing` end to end against a real
temporary filesystem, with qBittorrent and the history database stubbed at their
boundaries.  It is the integration tier declared in ``pyproject.toml``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from movarr.config import Config
from movarr.post_processor import run_post_processing

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture

pytestmark = pytest.mark.integration


def _make_config(library_dir: Path) -> Config:
    """Return a config that copies completed torrents into *library_dir*."""
    cfg = Config()
    cfg.post_process.post_process_enabled = True
    cfg.post_process.copy_completed = True
    cfg.post_process.remove_completed = False
    cfg.post_process.delete_lower_quality = False
    cfg.post_process.exclude_file_min_kb = 0
    cfg.post_process.default_copy_library.hd_path = str(library_dir)
    cfg.post_process.default_copy_library.uhd_path = ""
    return cfg


def _make_record(mocker: MockerFixture) -> Any:
    """Return a history record for the stubbed database."""
    record = mocker.MagicMock()
    record.imdb_title = "The Matrix"
    record.imdb_year = "1999"
    record.imdb_genres_list = "[]"
    record.imdb_certification = ""
    record.imdb_cert_source = ""
    record.imdb_poster_url = None
    record.index_title = "The Matrix 1999 1080p BluRay"
    return record


def test_completed_torrent_copied_to_library(tmp_path: Path, mocker: MockerFixture) -> None:
    """A completed torrent's video file lands in the per-movie library folder."""
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    library = tmp_path / "library"
    library.mkdir()
    source = downloads / "The Matrix 1999 1080p BluRay.mkv"
    source.write_bytes(b"movie-bytes")

    cfg = _make_config(library)

    db = mocker.MagicMock()
    db.find_by_tag.return_value = _make_record(mocker)

    qbt = mocker.MagicMock()
    qbt.is_reachable.return_value = True
    qbt.list_completed.return_value = [
        {
            "name": "The Matrix 1999 1080p BluRay",
            "torrent_tag": "movarr-abc123",
            "torrent_hash": "abc123",
            "torrent_save_path": str(downloads),
            "torrent_file_list": [
                {"file_name": "The Matrix 1999 1080p BluRay.mkv", "file_size": 11},
            ],
        }
    ]

    run_post_processing(cfg, qbt, db)

    copied = library / "The Matrix (1999)" / "The Matrix 1999 1080p BluRay.mkv"
    assert copied.is_file()
    assert copied.read_bytes() == b"movie-bytes"
    db.mark_completed.assert_called_once_with("movarr-abc123")


def test_torrent_without_history_record_is_skipped(tmp_path: Path, mocker: MockerFixture) -> None:
    """A torrent with no history row is left alone rather than copied blindly."""
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    library = tmp_path / "library"
    library.mkdir()
    (downloads / "Unknown 2021 1080p.mkv").write_bytes(b"x")

    cfg = _make_config(library)

    db = mocker.MagicMock()
    db.find_by_tag.return_value = None

    qbt = mocker.MagicMock()
    qbt.is_reachable.return_value = True
    qbt.list_completed.return_value = [
        {
            "name": "Unknown 2021 1080p",
            "torrent_tag": "movarr-unknown",
            "torrent_hash": "def456",
            "torrent_save_path": str(downloads),
            "torrent_file_list": [{"file_name": "Unknown 2021 1080p.mkv", "file_size": 1}],
        }
    ]

    run_post_processing(cfg, qbt, db)

    assert list(library.iterdir()) == []
    db.mark_completed.assert_not_called()
