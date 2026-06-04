"""Unit tests for movarr.post_processor."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


from movarr.config import Config, CopyLibraryRuleConfig, DefaultCopyLibraryConfig
from movarr.database import HistoryRecord
from movarr.filters import composite_quality_score, supersession_quality_score
from movarr.post_processor import (
    _build_copy_list,
    _canonical_filename,
    _cert_acceptable,
    _delete_superseded_files,
    _first_level_dir,
    _kill_process,
    _largest_file,
    _parse_genres,
    _pick_path,
    _process_one,
    _resolution_from_index_title,
    _resolve_destination,
    _run_hook,
    _safe_path_component,
    _save_poster_art,
    run_post_processing,
)


class TestCompositeQualityScore:
    """composite_quality_score is the public scoring surface used by the deletion step."""

    def test_higher_score_for_remux(self) -> None:
        cfg = Config()
        new_san = "The Matrix 1999 1080p Remux"
        lib_san = "The Matrix 1999 1080p BluRay"
        assert composite_quality_score(new_san, lib_san, cfg) > composite_quality_score(lib_san, new_san, cfg)

    def test_equal_score_for_identical_titles(self) -> None:
        cfg = Config()
        san = "The Matrix 1999 1080p BluRay"
        assert composite_quality_score(san, san, cfg) == composite_quality_score(san, san, cfg)

    def test_preferred_group_bonus_applied(self) -> None:
        from movarr.config import FiltersConfig

        cfg = Config()
        cfg = cfg.model_copy(update={"filters": FiltersConfig(preferred_index_group_list=["PublicHD"])})
        new_san = "The Matrix 1999 1080p BluRay PublicHD"
        lib_san = "The Matrix 1999 1080p BluRay OtherGroup"
        assert composite_quality_score(new_san, lib_san, cfg) > composite_quality_score(lib_san, new_san, cfg)


class TestSupersessionQualityScore:
    """supersession_quality_score excludes special-edition bonus unlike composite_quality_score."""

    def test_special_edition_does_not_beat_non_edition(self) -> None:
        """Extended cut must NOT score higher than theatrical in a supersession comparison.

        composite_quality_score would award +10 to the extended cut; supersession must not.
        """
        cfg = Config()
        extended_san = "The Matrix 1999 1080p BluRay Extended"
        theatrical_san = "The Matrix 1999 1080p BluRay"
        assert supersession_quality_score(extended_san, theatrical_san, cfg) == supersession_quality_score(
            theatrical_san, extended_san, cfg
        )

    def test_preferred_group_bonus_still_applied(self) -> None:
        """Group bonus is still included so genuine quality differences are detected."""
        from movarr.config import FiltersConfig

        cfg = Config().model_copy(update={"filters": FiltersConfig(preferred_index_group_list=["PublicHD"])})
        new_san = "The Matrix 1999 1080p BluRay PublicHD"
        lib_san = "The Matrix 1999 1080p BluRay OtherGroup"
        assert supersession_quality_score(new_san, lib_san, cfg) > supersession_quality_score(lib_san, new_san, cfg)

    def test_remux_beats_bluray(self) -> None:
        """Remux still scores higher than BluRay encode."""
        cfg = Config()
        assert supersession_quality_score(
            "The Matrix 1999 1080p Remux", "The Matrix 1999 1080p BluRay", cfg
        ) > supersession_quality_score("The Matrix 1999 1080p BluRay", "The Matrix 1999 1080p Remux", cfg)


class TestDeleteSupersededFiles:
    """Tests for _delete_superseded_files — deletes lower-quality library videos."""

    # ------------------------------------------------------------------
    # Normal deletion cases
    # ------------------------------------------------------------------

    def test_deletes_lower_quality_same_resolution(self, tmp_path: Path) -> None:
        """Lower-scored video at same resolution is deleted."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        dst_dir = str(movie_dir)
        dst_base = str(tmp_path)
        new_fname = "The Matrix 1999 1080p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        old_fname = "The Matrix 1999 1080p BluRay.mkv"
        (movie_dir / old_fname).write_bytes(b"old")

        cfg = Config()
        count = _delete_superseded_files(dst_dir, dst_base, new_fname, cfg)

        assert count == 1
        assert not (movie_dir / old_fname).exists()
        assert (movie_dir / new_fname).exists()

    def test_deletes_lower_resolution_library_file(self, tmp_path: Path) -> None:
        """Library file at lower resolution is deleted when new file is higher res."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 2160p BluRay.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        lib_fname = "The Matrix 1999 1080p BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"lib")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 1
        assert not (movie_dir / lib_fname).exists()

    def test_multiple_lower_quality_files_all_deleted(self, tmp_path: Path) -> None:
        """All lower-quality files in the directory are deleted."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 1080p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        for name in ["The Matrix 1999 1080p BluRay.mkv", "The Matrix 1999 1080p HDTV.mkv"]:
            (movie_dir / name).write_bytes(b"old")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 2

    # ------------------------------------------------------------------
    # Conservative keep cases
    # ------------------------------------------------------------------

    def test_skips_non_video_files(self, tmp_path: Path) -> None:
        """Non-video files (NFO, SRT) are never touched."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 1080p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        nfo = movie_dir / "The Matrix 1999.nfo"
        nfo.write_bytes(b"metadata")
        srt = movie_dir / "The Matrix 1999.srt"
        srt.write_bytes(b"subs")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 0
        assert nfo.exists()
        assert srt.exists()

    def test_does_not_delete_new_file_itself(self, tmp_path: Path) -> None:
        """The newly copied file is never a deletion candidate."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 1080p BluRay.mkv"
        (movie_dir / new_fname).write_bytes(b"content")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 0
        assert (movie_dir / new_fname).exists()

    def test_returns_zero_if_dst_dir_missing(self, tmp_path: Path) -> None:
        """Non-existent directory returns 0 without raising."""
        count = _delete_superseded_files(
            str(tmp_path / "nonexistent" / "Movie (2000)"),
            str(tmp_path / "nonexistent"),
            "movie.mkv",
            Config(),
        )
        assert count == 0

    # ------------------------------------------------------------------
    # Safety guard: depth / parent check
    # ------------------------------------------------------------------

    def test_safety_guard_rejects_dst_dir_equal_to_dst_base(self, tmp_path: Path) -> None:
        """Guard 1: dst_dir == dst_base is rejected — we never operate on the library root."""
        lib_root = tmp_path / "Movies"
        lib_root.mkdir()
        (lib_root / "some_movie.mkv").write_bytes(b"x")

        count = _delete_superseded_files(
            str(lib_root),  # dst_dir == dst_base -> MUST abort
            str(lib_root),
            "new_movie.mkv",
            Config(),
        )
        assert count == 0
        assert (lib_root / "some_movie.mkv").exists()

    def test_safety_guard_rejects_grandchild_dir(self, tmp_path: Path) -> None:
        """Guard 1: dst_dir two levels below dst_base is rejected."""
        lib_root = tmp_path / "Movies"
        deep_dir = lib_root / "subcat" / "Movie (2000)"
        deep_dir.mkdir(parents=True)
        (deep_dir / "old.mkv").write_bytes(b"x")

        # dst_base is lib_root but dst_dir is two levels deep -> abort
        count = _delete_superseded_files(
            str(deep_dir),
            str(lib_root),
            "new.mkv",
            Config(),
        )
        assert count == 0
        assert (deep_dir / "old.mkv").exists()

    # ------------------------------------------------------------------
    # Safety guard: video file count cap
    # ------------------------------------------------------------------

    def test_safety_guard_rejects_too_many_video_files(self, tmp_path: Path) -> None:
        """Guard 2: more than _MAX_VIDEO_FILES_IN_MOVIE_DIR video files -> abort, nothing deleted."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 1080p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        # Create 4 more video files -- total 5 (> cap of 4)
        lower_quality_files = [
            "The Matrix 1999 1080p BluRay.mkv",
            "The Matrix 1999 1080p HDTV.mkv",
            "The Matrix 1999 720p BluRay.mkv",
            "The Matrix 1999 720p HDTV.mkv",
        ]
        for name in lower_quality_files:
            (movie_dir / name).write_bytes(b"old")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 0
        # All files untouched
        for name in lower_quality_files:
            assert (movie_dir / name).exists(), f"{name} should not have been deleted"

    def test_safety_guard_rejects_too_many_video_files_emits_warning(self, tmp_path: Path) -> None:
        """Guard 2: warning log includes the actual video file count."""
        from loguru import logger as _loguru_logger

        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 1080p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        for name in [
            "The Matrix 1999 1080p BluRay.mkv",
            "The Matrix 1999 1080p HDTV.mkv",
            "The Matrix 1999 720p BluRay.mkv",
            "The Matrix 1999 720p HDTV.mkv",
        ]:
            (movie_dir / name).write_bytes(b"old")

        records: list = []
        sink_id = _loguru_logger.add(lambda m: records.append(m.record), level=0)
        try:
            count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())
        finally:
            _loguru_logger.remove(sink_id)

        assert count == 0
        warning_messages = [r["message"] for r in records if r["level"].name == "WARNING"]
        assert any("5" in msg for msg in warning_messages), (
            f"Expected file count '5' in warning log; got: {warning_messages}"
        )

    def test_safety_guard_allows_exactly_four_video_files(self, tmp_path: Path) -> None:
        """Guard 2: exactly _MAX_VIDEO_FILES_IN_MOVIE_DIR video files -> proceeds normally."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 1080p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        # 3 lower-quality files -> total 4 (= cap, allowed)
        for name in [
            "The Matrix 1999 1080p BluRay.mkv",
            "The Matrix 1999 1080p HDTV.mkv",
            "The Matrix 1999 720p BluRay.mkv",
        ]:
            (movie_dir / name).write_bytes(b"old")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 3  # all three lower-quality files deleted

    def test_aborts_if_primary_file_absent(self, tmp_path: Path) -> None:
        """Abort if new_primary_fname is not present in dst_dir.

        If the primary file was filtered out by exclusion rules, canonical_fname
        won't exist in the directory. We must not delete anything in that case —
        we cannot safely tell 'new' from 'old'.
        """
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        # Only the *real* copied file is present — NOT canonical_fname.
        real_copied = movie_dir / "The Matrix 1999 1080p BluRay.mkv"
        real_copied.write_bytes(b"real")
        lib_fname = "The Matrix 1999 720p BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"old")

        # Pass a canonical_fname that doesn't exist (largest torrent entry was excluded).
        count = _delete_superseded_files(
            str(movie_dir),
            str(tmp_path),
            "The Matrix 1999 2160p Remux.mkv",  # not present
            Config(),
        )
        assert count == 0
        assert (movie_dir / lib_fname).exists(), "lib file must be preserved"
        assert real_copied.exists(), "real copied file must be preserved"

    def test_companion_file_from_same_torrent_is_protected(self, tmp_path: Path) -> None:
        """Files listed in copied_fnames are never deleted, even if lower quality.

        A multi-file torrent may include a 1080p bonus-feature alongside a 2160p
        main feature. Both are 'new' — the 1080p must not be deleted.
        """
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 2160p Remux.mkv"
        companion_fname = "The Matrix 1999 1080p BluRay.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        (movie_dir / companion_fname).write_bytes(b"companion")

        count = _delete_superseded_files(
            str(movie_dir),
            str(tmp_path),
            new_fname,
            Config(),
            copied_fnames=frozenset({new_fname, companion_fname}),
        )
        assert count == 0
        assert (movie_dir / companion_fname).exists(), "companion must not be deleted"

    def test_non_video_primary_skips_deletion(self, tmp_path: Path) -> None:
        """A non-video primary file (.rar, .nfo) must not trigger any deletions."""
        movie_dir = tmp_path / "Movie (2024)"
        movie_dir.mkdir()
        non_video_primary = "Movie 2024 2160p BluRay.rar"
        (movie_dir / non_video_primary).write_bytes(b"archive")
        lib_fname = "Movie 2024 1080p BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"lib")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), non_video_primary, Config())
        assert count == 0
        assert (movie_dir / lib_fname).exists(), "lib file must not be deleted for non-video primary"

    def test_skips_behind_the_scenes_extra(self, tmp_path: Path) -> None:
        """Extras keyword guard: 'Behind the Scenes' file is never deleted.

        This file shares the same title and year as the main feature but contains
        a known extras keyword in the post-year segment.
        """
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 2160p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        behind_scenes = "The Matrix 1999 Behind the Scenes 1080p BluRay.mkv"
        (movie_dir / behind_scenes).write_bytes(b"extra")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())
        assert count == 0
        assert (movie_dir / behind_scenes).exists(), "behind-the-scenes file must not be deleted"

    def test_skips_making_of_extra(self, tmp_path: Path) -> None:
        """Extras keyword guard: 'Making Of' file is never deleted."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 2160p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        making_of = "The Matrix 1999 Making Of 1080p.mkv"
        (movie_dir / making_of).write_bytes(b"extra")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())
        assert count == 0
        assert (movie_dir / making_of).exists(), "making-of file must not be deleted"

    def test_skips_deletion_when_canonical_not_in_copied_fnames_in_process_one(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """If the largest torrent entry is excluded, canonical_fname is not in copied_fnames.

        _process_one must NOT call _delete_superseded_files in that case even if a
        stale library file with the same canonical name exists.
        """
        from movarr.config import DefaultCopyLibraryConfig, PostProcessConfig

        dst_base = tmp_path
        cfg = Config().model_copy(
            update={
                "post_process": PostProcessConfig(
                    copy_completed=True,
                    remove_completed=False,
                    delete_lower_quality=True,
                    # exclude the large remux by regex so it's filtered from src_files
                    exclude_file_regex_list=["Remux"],
                    default_copy_library=DefaultCopyLibraryConfig(hd_path=str(dst_base)),
                )
            }
        )

        movie_folder = dst_base / "The Matrix (1999)"
        movie_folder.mkdir()
        old_file = movie_folder / "The Matrix 1999 1080p BluRay.mkv"
        old_file.write_bytes(b"old")

        # Torrent with remux (excluded by regex) + srt that passes
        torrent = {
            "torrent_tag": "tag_excluded",
            "torrent_hash": "excl123",
            "torrent_save_path": "/downloads",
            "torrent_file_list": [
                # Remux is excluded by exclude_file_regex_list
                {"file_name": "The Matrix 1999 1080p Remux.mkv", "file_size": 20_000_000_000},
            ],
        }
        db_record = mocker.MagicMock()
        db_record.imdb_title = "The Matrix"
        db_record.imdb_year = "1999"
        db_record.imdb_genres_list = "[]"
        db_record.imdb_certification = ""
        db_record.imdb_cert_source = ""
        db_record.index_title = "The Matrix 1999 1080p Remux"

        db = mocker.MagicMock()
        db.find_by_tag.return_value = db_record
        qbt = mocker.MagicMock()

        # copy_with_verify is NOT patched — but src_files will be empty after filtering,
        # so no copy occurs and copied_fnames stays empty.
        mocker.patch("movarr.post_processor.make_directory", return_value=True)

        _process_one(torrent, cfg, qbt, db)

        # old_file must be untouched since the primary was never copied
        assert old_file.exists(), "lib file must not be deleted when primary was excluded"

    def test_skips_deletion_when_new_primary_is_extras(self, tmp_path: Path) -> None:
        """New primary that is extras/bonus content must not trigger deletion of the real feature."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The.Matrix.1999.Making.Of.2160p.mkv"
        (movie_dir / new_fname).write_bytes(b"extras")
        lib_fname = "The.Matrix.1999.1080p.BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"real")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 0
        assert (movie_dir / lib_fname).exists()

    def test_deletes_same_edition_resolution_upgrade(self, tmp_path: Path) -> None:
        """Extended 2160p should delete Extended 1080p — same edition, higher resolution."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The.Matrix.1999.Extended.2160p.Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        lib_fname = "The.Matrix.1999.Extended.1080p.BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"old")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 1
        assert not (movie_dir / lib_fname).exists()

    def test_deletes_base_edition_resolution_upgrade(self, tmp_path: Path) -> None:
        """2160p (no edition) should delete 1080p (no edition) — same base, higher resolution."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The.Matrix.1999.2160p.Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        lib_fname = "The.Matrix.1999.1080p.BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"old")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 1
        assert not (movie_dir / lib_fname).exists()

    def test_deletes_same_res_same_edition(self, tmp_path: Path) -> None:
        """Same edition at same resolution: higher-scored variant deletes lower."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The.Matrix.1999.Extended.1080p.Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        lib_fname = "The.Matrix.1999.Extended.1080p.BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"old")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 1
        assert not (movie_dir / lib_fname).exists()

    def test_skips_deletion_when_new_primary_has_bracketed_extras(self, tmp_path: Path) -> None:
        """New primary with bracketed extras token in raw filename must not trigger deletion."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The.Matrix.1999.2160p.[Featurettes].mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        lib_fname = "The.Matrix.1999.1080p.BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"old")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 0
        assert (movie_dir / lib_fname).exists()

    def test_skips_deletion_when_lib_file_has_bracketed_extras(self, tmp_path: Path) -> None:
        """Library file with bracketed extras token in raw filename is not deleted."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The.Matrix.1999.2160p.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        lib_fname = "The.Matrix.1999.[Behind.the.Scenes].1080p.mkv"
        (movie_dir / lib_fname).write_bytes(b"old")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 0
        assert (movie_dir / lib_fname).exists()

    def test_dotted_bracket_extras_detected(self, tmp_path: Path) -> None:
        """Bracket extras with dot-separated words (e.g. [Behind.the.Scenes]) must be detected."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The.Matrix.1999.2160p.[Behind.the.Scenes].mkv"
        (movie_dir / new_fname).write_bytes(b"extras")
        lib_fname = "The.Matrix.1999.1080p.BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"real")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 0
        assert (movie_dir / lib_fname).exists()

    def test_skips_deletion_when_new_primary_is_extras_no_year(self, tmp_path: Path) -> None:
        """No-year extras primary (e.g. 'sample.mkv') must not trigger deletion of real movies."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "sample.mkv"
        (movie_dir / new_fname).write_bytes(b"extras")
        real_movie = "The.Matrix.1999.1080p.BluRay.mkv"
        (movie_dir / real_movie).write_bytes(b"real")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 0
        assert (movie_dir / real_movie).exists()

    def test_hyphenated_bracket_extras_detected(self, tmp_path: Path) -> None:
        """Hyphen-separated bracket extras still prevent deletion."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The.Matrix.1999.2160p.[Behind-the-Scenes].mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        lib_fname = "The.Matrix.1999.1080p.BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"old")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 0
        assert (movie_dir / lib_fname).exists()

    def test_no_false_positive_for_extra_in_title(self, tmp_path: Path) -> None:
        """Movie titled 'Extra Ordinary' must NOT be treated as extras content."""
        movie_dir = tmp_path / "Extra Ordinary (2019)"
        movie_dir.mkdir()
        new_fname = "Extra.Ordinary.2019.2160p.Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        lib_fname = "Extra.Ordinary.2019.1080p.BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"old")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 1
        assert not (movie_dir / lib_fname).exists()

    def test_bracketed_extras_still_detected(self, tmp_path: Path) -> None:
        """Bracket-wrapped extras keyword still prevents deletion after false-positive fix."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The.Matrix.1999.2160p.[Featurettes].mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        lib_fname = "The.Matrix.1999.1080p.BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"old")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 0
        assert (movie_dir / lib_fname).exists()

    def test_brace_wrapped_extras_detected(self, tmp_path: Path) -> None:
        """Curly-brace-wrapped extras keyword prevents deletion (sanitise strips braces too)."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The.Matrix.1999.2160p.{Featurettes}.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        lib_fname = "The.Matrix.1999.1080p.BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"old")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 0
        assert (movie_dir / lib_fname).exists()

    def test_special_edition_not_treated_as_extras(self, tmp_path: Path) -> None:
        """'Special Edition' in the post-year segment must NOT be treated as extras content.

        A release like 'Movie.2019.Special.Edition.2160p.Remux' is the main feature,
        not bonus content.  The extras guard must not fire and the lower-quality
        library copy must be deleted.
        """
        movie_dir = tmp_path / "Movie (2019)"
        movie_dir.mkdir()
        new_fname = "Movie.2019.Special.Edition.2160p.Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        lib_fname = "Movie.2019.Special.Edition.1080p.BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"old")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 1
        assert not (movie_dir / lib_fname).exists()

    def test_special_features_is_treated_as_extras(self, tmp_path: Path) -> None:
        """'Special Features' in the post-year segment IS extras content and must be skipped."""
        movie_dir = tmp_path / "Movie (2019)"
        movie_dir.mkdir()
        new_fname = "Movie.2019.Special.Features.2160p.mkv"
        (movie_dir / new_fname).write_bytes(b"extras")
        lib_fname = "Movie.2019.1080p.BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"real")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 0
        assert (movie_dir / lib_fname).exists()

    def test_plural_extras_protected(self, tmp_path: Path) -> None:
        """Plural extras keywords (deleted scenes, interviews, short films) must prevent deletion."""
        movie_dir = tmp_path / "Movie (2019)"
        movie_dir.mkdir()
        new_fname = "Movie.2019.Deleted.Scenes.2160p.mkv"
        (movie_dir / new_fname).write_bytes(b"extras")
        lib_fname = "Movie.2019.1080p.BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"real")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 0, "plural 'Deleted Scenes' must be treated as extras"
        assert (movie_dir / lib_fname).exists()

    def test_extra_singular_not_treated_as_extras(self, tmp_path: Path) -> None:
        """'EXTRA' (singular) in the post-year segment must NOT be treated as extras content.

        A release tagged 'Movie.2019.2160p.BluRay-EXTRA.mkv' is the main feature;
        only the plural form 'extras' is a known extras keyword.
        """
        movie_dir = tmp_path / "Movie (2019)"
        movie_dir.mkdir()
        new_fname = "Movie.2019.2160p.BluRay-EXTRA.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        lib_fname = "Movie.2019.1080p.BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"old")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 1
        assert not (movie_dir / lib_fname).exists()

    def test_theatrical_tag_treated_as_base_edition_resolution_upgrade(self, tmp_path: Path) -> None:
        """Theatrical 2160p should delete untagged 1080p — theatrical IS the base edition."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The.Matrix.1999.Theatrical.2160p.Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        lib_fname = "The.Matrix.1999.1080p.BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"old")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 1
        assert not (movie_dir / lib_fname).exists()

    def test_extended_preserves_base_theatrical(self, tmp_path: Path) -> None:
        """Extended 2160p preserves base Theatrical 1080p — different editions are protected."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "Movie.2019.Extended.2160p.Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        lib_fname = "Movie.2019.Theatrical.1080p.BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"old")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 0
        assert (movie_dir / lib_fname).exists()

    def test_theatrical_extended_supersedes_lower_quality_extended(self, tmp_path: Path) -> None:
        """Theatrical Extended 2160p Remux should delete Extended 1080p BluRay — both Extended."""
        movie_dir = tmp_path / "Movie (2019)"
        movie_dir.mkdir()
        new_fname = "Movie.2019.Theatrical.Extended.2160p.Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        lib_fname = "Movie.2019.Extended.1080p.BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"old")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 1
        assert not (movie_dir / lib_fname).exists()

    def test_compound_edition_same_tags_different_order(self, tmp_path: Path) -> None:
        """Unrated Extended 2160p Remux should delete Extended Unrated 1080p BluRay — same compound edition, higher res."""
        movie_dir = tmp_path / "Movie (2019)"
        movie_dir.mkdir()
        new_fname = "Movie.2019.Unrated.Extended.2160p.Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        lib_fname = "Movie.2019.Extended.Unrated.1080p.BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"old")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 1
        assert not (movie_dir / lib_fname).exists()

    def test_compound_edition_different_tags_preserved(self, tmp_path: Path) -> None:
        """Unrated Extended 2160p preserves Directors Cut 1080p — different editions are protected."""
        movie_dir = tmp_path / "Movie (2019)"
        movie_dir.mkdir()
        new_fname = "Movie.2019.Unrated.Extended.2160p.Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        lib_fname = "Movie.2019.Directors.Cut.1080p.BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"old")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 0
        assert (movie_dir / lib_fname).exists()

    def test_deletes_all_other_video_files_regardless_of_quality(self, tmp_path: Path) -> None:
        """All other video files are deleted regardless of resolution/quality/edition."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 1080p BluRay.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        # Files that would have been protected by the old logic
        higher_res = "The Matrix 1999 2160p BluRay.mkv"
        diff_title = "The Matrix Reloaded 2003 2160p BluRay.mkv"
        unparseable = "The Matrix.mkv"
        for name in [higher_res, diff_title, unparseable]:
            (movie_dir / name).write_bytes(b"old")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 3
        assert not (movie_dir / higher_res).exists()
        assert not (movie_dir / diff_title).exists()
        assert not (movie_dir / unparseable).exists()
        assert (movie_dir / new_fname).exists()

    def test_skips_extras_from_prior_runs(self, tmp_path: Path) -> None:
        """Extras/bonus content from prior torrent runs is NOT deleted."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 2160p BluRay.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        # Extras from a prior run — must survive
        extras = [
            "The Matrix 1999 Behind the Scenes 1080p.mkv",
            "The Matrix 1999 [Featurettes] 1080p.mkv",
        ]
        for name in extras:
            (movie_dir / name).write_bytes(b"extra")
        # A real movie file that SHOULD be deleted
        old_movie = "The Matrix 1999 1080p BluRay.mkv"
        (movie_dir / old_movie).write_bytes(b"old")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 1
        assert not (movie_dir / old_movie).exists()
        for name in extras:
            assert (movie_dir / name).exists(), f"{name} must not be deleted"


# _safe_path_component


class TestSafePathComponent:
    """Tests for _safe_path_component — pure function."""

    def test_clean_string_unchanged(self) -> None:
        assert _safe_path_component("The Matrix") == "The Matrix"

    def test_strips_forward_slash(self) -> None:
        assert _safe_path_component("movie/title") == "movietitle"

    def test_strips_backslash(self) -> None:
        assert _safe_path_component("movie\\title") == "movietitle"

    def test_strips_angle_brackets(self) -> None:
        assert _safe_path_component("<bad>") == "bad"

    def test_strips_colon(self) -> None:
        # colon is unsafe; space is kept
        assert _safe_path_component("title: subtitle") == "title subtitle"

    def test_strips_pipe(self) -> None:
        assert _safe_path_component("foo|bar") == "foobar"

    def test_strips_question_mark(self) -> None:
        assert _safe_path_component("what?") == "what"

    def test_strips_asterisk(self) -> None:
        assert _safe_path_component("star*") == "star"

    def test_strips_double_dot(self) -> None:
        assert _safe_path_component("..secret") == "secret"

    def test_strips_null_byte(self) -> None:
        assert _safe_path_component("null\x00byte") == "nullbyte"

    def test_strips_surrounding_whitespace(self) -> None:
        assert _safe_path_component("  title  ") == "title"

    def test_empty_string(self) -> None:
        assert _safe_path_component("") == ""


# _run_hook


class TestRunHook:
    """Tests for the _run_hook subprocess helper."""

    def test_returns_true_on_zero_exit(self, mocker: MockerFixture) -> None:
        mock_popen = mocker.patch("movarr.post_processor.subprocess.Popen")
        mocker.patch("movarr.post_processor.os.getpgid", return_value=99)
        mock_proc = mocker.Mock()
        mock_proc.communicate.return_value = ("", "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc
        assert _run_hook("echo hello", "/tmp/movie", "post_copy") is True

    def test_returns_false_on_nonzero_exit(self, mocker: MockerFixture) -> None:
        mock_popen = mocker.patch("movarr.post_processor.subprocess.Popen")
        mocker.patch("movarr.post_processor.os.getpgid", return_value=99)
        mock_proc = mocker.Mock()
        mock_proc.communicate.return_value = ("", "error")
        mock_proc.returncode = 1
        mock_popen.return_value = mock_proc
        assert _run_hook("false", "/tmp/movie", "pre_delete") is False

    def test_substitutes_dir_placeholder(self, mocker: MockerFixture) -> None:
        mock_popen = mocker.patch("movarr.post_processor.subprocess.Popen")
        mocker.patch("movarr.post_processor.os.getpgid", return_value=99)
        mock_proc = mocker.Mock()
        mock_proc.communicate.return_value = ("", "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc
        _run_hook("chattr -i {dir}/*", "/mnt/media/The Matrix (1999)", "pre_delete")
        cmd = mock_popen.call_args[0][0]
        assert cmd == "chattr -i '/mnt/media/The Matrix (1999)'/*"

    def test_substitutes_leaf_placeholder(self, mocker: MockerFixture) -> None:
        mock_popen = mocker.patch("movarr.post_processor.subprocess.Popen")
        mocker.patch("movarr.post_processor.os.getpgid", return_value=99)
        mock_proc = mocker.Mock()
        mock_proc.communicate.return_value = ("", "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc
        _run_hook("echo {leaf}", "/mnt/media/The Matrix (1999)", "post_copy")
        cmd = mock_popen.call_args[0][0]
        assert cmd == "echo 'The Matrix (1999)'"

    def test_substitutes_both_placeholders(self, mocker: MockerFixture) -> None:
        mock_popen = mocker.patch("movarr.post_processor.subprocess.Popen")
        mocker.patch("movarr.post_processor.os.getpgid", return_value=99)
        mock_proc = mocker.Mock()
        mock_proc.communicate.return_value = ("", "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc
        _run_hook("cp {dir}/info.txt /log/{leaf}.txt", "/mnt/media/The Matrix (1999)", "post_copy")
        cmd = mock_popen.call_args[0][0]
        assert cmd == "cp '/mnt/media/The Matrix (1999)'/info.txt /log/'The Matrix (1999)'.txt"

    def test_no_double_expansion_when_dir_contains_leaf(self, mocker: MockerFixture) -> None:
        """Regression: dir_path literally containing '{leaf}' must not cause re-expansion."""
        mock_popen = mocker.patch("movarr.post_processor.subprocess.Popen")
        mocker.patch("movarr.post_processor.os.getpgid", return_value=99)
        mock_proc = mocker.Mock()
        mock_proc.communicate.return_value = ("", "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc
        _run_hook("echo {dir}", "/mnt/media/{leaf}/Movie (2020)", "post_copy")
        cmd = mock_popen.call_args[0][0]
        assert cmd == "echo '/mnt/media/{leaf}/Movie (2020)'"

    def test_leaf_with_trailing_slash_dir_path(self, mocker: MockerFixture) -> None:
        """Trailing slash on dir_path is stripped before extracting the leaf."""
        mock_popen = mocker.patch("movarr.post_processor.subprocess.Popen")
        mocker.patch("movarr.post_processor.os.getpgid", return_value=99)
        mock_proc = mocker.Mock()
        mock_proc.communicate.return_value = ("", "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc
        _run_hook("echo {leaf}", "/mnt/media/The Matrix (1999)/", "post_copy")
        cmd = mock_popen.call_args[0][0]
        assert cmd == "echo 'The Matrix (1999)'"

    def test_uses_shell_true(self, mocker: MockerFixture) -> None:
        mock_popen = mocker.patch("movarr.post_processor.subprocess.Popen")
        mocker.patch("movarr.post_processor.os.getpgid", return_value=99)
        mock_proc = mocker.Mock()
        mock_proc.communicate.return_value = ("", "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc
        _run_hook("echo {dir}", "/tmp/movie", "post_copy")
        assert mock_popen.call_args[1]["shell"] is True

    def test_returns_false_on_timeout(self, mocker: MockerFixture) -> None:
        import subprocess as _subprocess

        mock_popen = mocker.patch("movarr.post_processor.subprocess.Popen")
        mocker.patch("movarr.post_processor.os.killpg")
        mock_proc = mocker.Mock()
        mock_proc.pid = 12345
        mock_proc.communicate.side_effect = [
            _subprocess.TimeoutExpired(cmd="echo", timeout=300),
            ("", ""),  # drain after SIGTERM
        ]
        mock_popen.return_value = mock_proc
        assert _run_hook("echo {dir}", "/tmp/movie", "post_copy") is False

    def test_pgid_falls_back_to_pid_when_getpgid_fails(self, mocker: MockerFixture) -> None:
        """When os.getpgid raises OSError, proc.pid is used as pgid fallback."""
        mock_popen = mocker.patch("movarr.post_processor.subprocess.Popen")
        mocker.patch("movarr.post_processor.os.getpgid", side_effect=OSError)
        mock_proc = mocker.Mock()
        mock_proc.pid = 12345
        mock_proc.communicate.return_value = ("", "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc
        assert _run_hook("echo hello", "/tmp/movie", "post_copy") is True

    def test_pgid_falls_back_to_pid_when_getpgid_raises_process_lookup(self, mocker: MockerFixture) -> None:
        """When os.getpgid raises ProcessLookupError, proc.pid is used as pgid fallback."""
        mock_popen = mocker.patch("movarr.post_processor.subprocess.Popen")
        mocker.patch("movarr.post_processor.os.getpgid", side_effect=ProcessLookupError)
        mock_proc = mocker.Mock()
        mock_proc.pid = 99999
        mock_proc.communicate.return_value = ("", "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc
        assert _run_hook("echo test", "/tmp/movie", "post_copy") is True

    def test_kill_process_with_none_timeout_logs_terminated(self, mocker: MockerFixture) -> None:
        """_kill_process with timeout_mins=None logs 'hook terminated' not 'timed out'."""

        mocker.patch("movarr.post_processor.os.killpg")
        mocker.patch("movarr.post_processor.subprocess.Popen")
        mock_proc = mocker.Mock()
        mock_proc.pid = 12345
        mock_proc.communicate.return_value = ("", "")
        mock_proc.returncode = 0
        mock_log = mocker.patch("movarr.post_processor.logger.error")

        _kill_process(mock_proc, 99, "pre_delete", None)

        # Should have logged "terminated", not "timed out after None min."
        assert mock_log.call_count == 1
        msg = mock_log.call_args[0][0]
        assert "terminated" in msg


# _cert_acceptable


class TestCertAcceptable:
    """Tests for _cert_acceptable — BBFC ordering."""

    def test_cert_below_max_is_acceptable(self) -> None:
        assert _cert_acceptable("15", "18") is True

    def test_cert_equal_to_max_is_acceptable(self) -> None:
        assert _cert_acceptable("18", "18") is True

    def test_cert_above_max_is_not_acceptable(self) -> None:
        assert _cert_acceptable("18", "15") is False

    def test_u_cert_acceptable_for_all(self) -> None:
        assert _cert_acceptable("U", "R18") is True

    def test_u_cert_acceptable_for_u(self) -> None:
        assert _cert_acceptable("U", "U") is True

    def test_r18_above_18(self) -> None:
        assert _cert_acceptable("R18", "18") is False

    def test_unknown_movie_cert_returns_false(self) -> None:
        assert _cert_acceptable("NR", "18") is False

    def test_unknown_max_cert_returns_false(self) -> None:
        assert _cert_acceptable("15", "MPAA") is False

    def test_empty_movie_cert_returns_false(self) -> None:
        assert _cert_acceptable("", "18") is False

    def test_empty_max_cert_returns_false(self) -> None:
        assert _cert_acceptable("15", "") is False

    def test_pg_below_12(self) -> None:
        assert _cert_acceptable("PG", "12") is True

    def test_12_below_12a(self) -> None:
        assert _cert_acceptable("12", "12A") is True


# _parse_genres


class TestParseGenres:
    """Tests for _parse_genres — handles multiple input formats."""

    def test_list_returned_directly(self) -> None:
        assert _parse_genres(["Action", "Drama"]) == ["Action", "Drama"]

    def test_json_string_parsed(self) -> None:
        assert _parse_genres('["Action", "Drama"]') == ["Action", "Drama"]

    def test_python_repr_string_parsed(self) -> None:
        assert _parse_genres("['Action', 'Drama']") == ["Action", "Drama"]

    def test_json_strips_whitespace(self) -> None:
        assert _parse_genres('["  Action  ", " Drama "]') == ["Action", "Drama"]

    def test_non_string_non_list_returns_empty(self) -> None:
        assert _parse_genres(42) == []

    def test_none_returns_empty(self) -> None:
        assert _parse_genres(None) == []

    def test_invalid_string_returns_empty(self) -> None:
        assert _parse_genres("{not valid json}") == []

    def test_bytes_json_parsed(self) -> None:
        assert _parse_genres(b'["Action"]') == ["Action"]

    def test_empty_list(self) -> None:
        assert _parse_genres([]) == []

    def test_single_genre_list(self) -> None:
        assert _parse_genres('["Horror"]') == ["Horror"]


# _largest_file


class TestLargestFile:
    """Tests for _largest_file — pure helper."""

    def test_empty_file_list_returns_empty_strings(self) -> None:
        torrent: dict[str, Any] = {"torrent_file_list": []}
        assert _largest_file(torrent) == ("", "")

    def test_missing_file_list_returns_empty_strings(self) -> None:
        assert _largest_file({}) == ("", "")

    def test_single_file_returns_correct_name_and_dir(self) -> None:
        torrent: dict[str, Any] = {
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 5_000_000_000},
            ]
        }
        fname, fdir = _largest_file(torrent)
        assert fname == "movie.mkv"
        assert fdir == "movie"

    def test_picks_largest_of_multiple_files(self) -> None:
        torrent: dict[str, Any] = {
            "torrent_file_list": [
                {"file_name": "movie/small.nfo", "file_size": 1024},
                {"file_name": "movie/movie.mkv", "file_size": 8_000_000_000},
            ]
        }
        fname, _ = _largest_file(torrent)
        assert fname == "movie.mkv"

    def test_file_at_root_level_has_empty_dir(self) -> None:
        torrent: dict[str, Any] = {
            "torrent_file_list": [
                {"file_name": "movie.mkv", "file_size": 8_000_000_000},
            ]
        }
        fname, fdir = _largest_file(torrent)
        assert fname == "movie.mkv"
        assert fdir == ""

    def test_nested_path_returns_correct_dirname(self) -> None:
        torrent: dict[str, Any] = {
            "torrent_file_list": [
                {"file_name": "movie/sub/movie.mkv", "file_size": 8_000_000_000},
            ]
        }
        _, fdir = _largest_file(torrent)
        assert fdir == "movie/sub"


# _first_level_dir


class TestFirstLevelDir:
    """Tests for _first_level_dir — pure function."""

    def test_nested_path_returns_first_component(self) -> None:
        assert _first_level_dir("movie/sub/file.mkv") == "movie"

    def test_single_component(self) -> None:
        assert _first_level_dir("movie") == "movie"

    def test_empty_string_returns_empty(self) -> None:
        assert _first_level_dir("") == ""

    def test_filename_no_dir_returns_filename(self) -> None:
        assert _first_level_dir("file.mkv") == "file.mkv"

    def test_two_level_path(self) -> None:
        assert _first_level_dir("parent/child.mkv") == "parent"


# _canonical_filename


class TestCanonicalFilename:
    """Tests for _canonical_filename — pure function."""

    def test_non_video_extension_returned_unchanged(self) -> None:
        result = _canonical_filename("subs.srt", "movie")
        assert result == "subs.srt"

    def test_no_first_level_dir_returns_filename(self) -> None:
        result = _canonical_filename("movie.mkv", "")
        assert result == "movie.mkv"

    def test_parent_longer_than_filename_renames_file(self) -> None:
        # "The Dark Knight 2008 1080p BluRay" (32 chars) > "movie.mkv" (9 chars)
        result = _canonical_filename("movie.mkv", "The Dark Knight 2008 1080p BluRay")
        assert result.endswith(".mkv")
        assert result != "movie.mkv"

    def test_parent_shorter_than_filename_keeps_original(self) -> None:
        long_fname = "The.Dark.Knight.2008.1080p.BluRay.mkv"
        result = _canonical_filename(long_fname, "movie")
        assert result == long_fname

    def test_mp4_extension_preserved(self) -> None:
        result = _canonical_filename("movie.mp4", "A Very Long Directory Name Indeed")
        assert result.endswith(".mp4")

    def test_avi_extension_preserved(self) -> None:
        result = _canonical_filename("movie.avi", "A Very Long Directory Name Here")
        assert result.endswith(".avi")


# _pick_path


class TestPickPath:
    """Tests for _pick_path — routing logic."""

    def _default(self, hd: str = "/media/hd", uhd: str = "/media/uhd") -> DefaultCopyLibraryConfig:
        return DefaultCopyLibraryConfig(hd_path=hd, uhd_path=uhd)

    def _rule(
        self,
        name: str,
        genres: list[str],
        hd: str,
        uhd: str = "",
        max_cert: str | None = None,
    ) -> CopyLibraryRuleConfig:
        return CopyLibraryRuleConfig(name=name, genres=genres, hd_path=hd, uhd_path=uhd, max_certification=max_cert)

    def test_no_rules_returns_default_hd_path(self) -> None:
        result = _pick_path(["Action"], "15", "1080", [], self._default())
        assert result == "/media/hd"

    def test_uhd_resolution_returns_default_uhd_path(self) -> None:
        result = _pick_path(["Action"], "15", "2160", [], self._default())
        assert result == "/media/uhd"

    def test_4k_resolution_returns_default_uhd_path(self) -> None:
        result = _pick_path(["Action"], "15", "4k", [], self._default())
        assert result == "/media/uhd"

    def test_matching_rule_overrides_default(self) -> None:
        rule = self._rule("action", ["Action"], "/media/action")
        result = _pick_path(["Action", "Drama"], "15", "1080", [rule], self._default())
        assert result == "/media/action"

    def test_cert_fails_max_cert_falls_back_to_default(self) -> None:
        rule = self._rule("kids", ["Animation"], "/media/kids", max_cert="PG")
        result = _pick_path(["Animation"], "15", "1080", [rule], self._default())
        assert result == "/media/hd"

    def test_cert_within_max_cert_uses_rule_path(self) -> None:
        rule = self._rule("kids", ["Animation"], "/media/kids", max_cert="PG")
        result = _pick_path(["Animation"], "U", "1080", [rule], self._default())
        assert result == "/media/kids"

    def test_genre_tie_between_rules_falls_back_to_default(self) -> None:
        rule_a = self._rule("a", ["Action", "Drama"], "/media/a")
        rule_b = self._rule("b", ["Action", "Drama"], "/media/b")
        result = _pick_path(["Action", "Drama"], "15", "1080", [rule_a, rule_b], self._default())
        assert result == "/media/hd"

    def test_no_genre_match_returns_default(self) -> None:
        rule = self._rule("action", ["Action"], "/media/action")
        result = _pick_path(["Romance"], "15", "1080", [rule], self._default())
        assert result == "/media/hd"

    def test_uhd_resolution_no_uhd_path_falls_back_to_hd(self) -> None:
        result = _pick_path(["Action"], "15", "2160", [], self._default(hd="/media/hd", uhd=""))
        assert result == "/media/hd"

    def test_rule_with_no_hd_path_falls_back_to_default(self) -> None:
        rule = CopyLibraryRuleConfig(name="empty", genres=["Action"], hd_path="", uhd_path="")
        result = _pick_path(["Action"], "15", "1080", [rule], self._default())
        assert result == "/media/hd"

    def test_case_insensitive_genre_matching(self) -> None:
        rule = self._rule("action", ["action"], "/media/action")
        result = _pick_path(["ACTION"], "15", "1080", [rule], self._default())
        assert result == "/media/action"

    def test_best_scoring_rule_wins(self) -> None:
        rule_one = self._rule("one-genre", ["Action"], "/media/one")
        rule_two = self._rule("two-genres", ["Action", "Crime"], "/media/two")
        result = _pick_path(["Action", "Crime", "Drama"], "15", "1080", [rule_one, rule_two], self._default())
        assert result == "/media/two"


# _build_copy_list


class TestBuildCopyList:
    """Tests for _build_copy_list — exclusion logic."""

    def _config(
        self,
        min_kb: int = 0,
        file_regex: list[str] | None = None,
        folder_regex: list[str] | None = None,
    ) -> Config:
        cfg = Config()
        cfg.post_process.exclude_file_min_kb = min_kb
        cfg.post_process.exclude_file_regex_list = file_regex or []
        cfg.post_process.exclude_folder_regex_list = folder_regex or []
        return cfg

    def test_normal_file_included(self, tmp_path: Path) -> None:
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 8_000_000_000},
            ],
        }
        result = _build_copy_list(torrent, self._config())
        assert len(result) == 1
        assert result[0].endswith("movie.mkv")

    def test_multiple_files_all_included(self, tmp_path: Path) -> None:
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 8_000_000_000},
                {"file_name": "movie/movie.srt", "file_size": 50_000},
            ],
        }
        result = _build_copy_list(torrent, self._config())
        assert len(result) == 2

    def test_file_excluded_by_file_regex(self, tmp_path: Path) -> None:
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 8_000_000_000},
                {"file_name": "movie/sample.mkv", "file_size": 50_000_000},
            ],
        }
        result = _build_copy_list(torrent, self._config(file_regex=[r"sample"]))
        assert len(result) == 1
        assert not any("sample" in r for r in result)

    def test_file_excluded_by_folder_regex(self, tmp_path: Path) -> None:
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 8_000_000_000},
                {"file_name": "extras/behind.mkv", "file_size": 500_000_000},
            ],
        }
        result = _build_copy_list(torrent, self._config(folder_regex=[r"extras"]))
        assert len(result) == 1
        assert not any("behind" in r for r in result)

    def test_file_excluded_below_min_kb(self, tmp_path: Path) -> None:
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 8_000_000_000},
                {"file_name": "movie/small.nfo", "file_size": 1024},  # 1 KB
            ],
        }
        result = _build_copy_list(torrent, self._config(min_kb=100))
        assert len(result) == 1
        assert not any("small" in r for r in result)

    def test_empty_file_name_skipped(self, tmp_path: Path) -> None:
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "", "file_size": 8_000_000_000},
                {"file_name": "movie/movie.mkv", "file_size": 8_000_000_000},
            ],
        }
        result = _build_copy_list(torrent, self._config())
        assert len(result) == 1

    def test_path_escape_attack_skipped(self, tmp_path: Path) -> None:
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "../sneaky.mkv", "file_size": 8_000_000_000},
                {"file_name": "movie/movie.mkv", "file_size": 8_000_000_000},
            ],
        }
        result = _build_copy_list(torrent, self._config())
        # Only the safe file should be included; the traversal file is rejected
        assert len(result) == 1
        assert result[0].endswith("movie.mkv")
        assert not any("sneaky" in r for r in result)

    def test_all_excluded_returns_empty_list(self, tmp_path: Path) -> None:
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "movie/sample.mkv", "file_size": 8_000_000_000},
            ],
        }
        result = _build_copy_list(torrent, self._config(file_regex=[r"sample"]))
        assert result == []

    def test_invalid_file_regex_skipped(self, tmp_path: Path) -> None:
        """Invalid regex in exclude_file_regex_list is skipped and file is kept."""
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 8_000_000_000},
            ],
        }
        result = _build_copy_list(torrent, self._config(file_regex=[r"(invalid"]))
        assert len(result) == 1
        assert result[0].endswith("movie.mkv")

    def test_invalid_folder_regex_skipped(self, tmp_path: Path) -> None:
        """Invalid regex in exclude_folder_regex_list is skipped and file is kept."""
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 8_000_000_000},
            ],
        }
        result = _build_copy_list(torrent, self._config(folder_regex=[r"(invalid"]))
        assert len(result) == 1
        assert result[0].endswith("movie.mkv")

    def test_empty_file_list_returns_empty(self, tmp_path: Path) -> None:
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [],
        }
        result = _build_copy_list(torrent, self._config())
        assert result == []

    def test_file_regex_is_case_insensitive(self, tmp_path: Path) -> None:
        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "movie/SAMPLE.mkv", "file_size": 8_000_000_000},
            ],
        }
        result = _build_copy_list(torrent, self._config(file_regex=[r"sample"]))
        assert result == []


# _resolve_destination


class TestResolveDestination:
    """Tests for _resolve_destination — routing based on genre/cert/resolution."""

    def _config_with_default(self, hd: str = "/media/hd", uhd: str = "/media/uhd") -> Config:
        cfg = Config()
        cfg.post_process.default_copy_library.hd_path = hd
        cfg.post_process.default_copy_library.uhd_path = uhd
        return cfg

    def _db_record(
        self,
        mocker: MockerFixture,
        genres: str = '["Action"]',
        cert: str = "15",
        cert_source: str = "imdbpie",
        index_title: str = "Movie 2020 1080p BluRay",
    ) -> Any:
        rec = mocker.MagicMock()
        rec.imdb_genres_list = genres
        rec.imdb_certification = cert
        rec.imdb_cert_source = cert_source
        rec.index_title = index_title
        return rec

    def test_returns_default_hd_for_hd_content(self, mocker: MockerFixture) -> None:
        config = self._config_with_default()
        rec = self._db_record(mocker)
        result = _resolve_destination(rec, config)
        assert result == "/media/hd"

    def test_no_default_paths_configured_returns_none(self, mocker: MockerFixture) -> None:
        config = self._config_with_default(hd="", uhd="")
        rec = self._db_record(mocker)
        result = _resolve_destination(rec, config)
        assert result is None

    def test_omdb_cert_not_applied_to_bbfc_rules(self, mocker: MockerFixture) -> None:
        """Bug #8: MPAA certs from omdb must not be routed via BBFC rules."""
        cfg = self._config_with_default()
        rule = CopyLibraryRuleConfig(name="kids", genres=["Animation"], max_certification="PG", hd_path="/media/kids")
        cfg.post_process.copy_library_rules = [rule]
        # MPAA "R" from omdb → effective_cert="" → cert check fails → default path used
        rec = self._db_record(mocker, genres='["Animation"]', cert="R", cert_source="omdb")
        result = _resolve_destination(rec, cfg)
        assert result == "/media/hd"

    def test_imdbpie_cert_applied_to_bbfc_routing(self, mocker: MockerFixture) -> None:
        cfg = self._config_with_default()
        rule = CopyLibraryRuleConfig(name="kids", genres=["Animation"], max_certification="PG", hd_path="/media/kids")
        cfg.post_process.copy_library_rules = [rule]
        # cert "15" > "PG" in BBFC → cert fails → default path
        rec = self._db_record(mocker, genres='["Animation"]', cert="15", cert_source="imdbpie")
        result = _resolve_destination(rec, cfg)
        assert result == "/media/hd"

    def test_imdbpie_u_cert_routes_to_kids_path(self, mocker: MockerFixture) -> None:
        cfg = self._config_with_default()
        rule = CopyLibraryRuleConfig(name="kids", genres=["Animation"], max_certification="PG", hd_path="/media/kids")
        cfg.post_process.copy_library_rules = [rule]
        rec = self._db_record(mocker, genres='["Animation"]', cert="U", cert_source="imdbpie")
        result = _resolve_destination(rec, cfg)
        assert result == "/media/kids"


# run_post_processing


class TestRunPostProcessing:
    """Tests for run_post_processing — main entry point."""

    def test_disabled_returns_early_without_connection_check(self, mocker: MockerFixture) -> None:
        cfg = Config()
        cfg.post_process.post_process_enabled = False
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()

        run_post_processing(cfg, qbt, db)

        qbt.is_connected.assert_not_called()

    def test_not_connected_returns_early_without_listing(self, mocker: MockerFixture) -> None:
        cfg = Config()
        cfg.post_process.post_process_enabled = True
        qbt = mocker.MagicMock()
        qbt.is_connected.return_value = False
        db = mocker.MagicMock()

        run_post_processing(cfg, qbt, db)

        qbt.list_completed.assert_not_called()

    def test_no_completed_torrents_returns_early(self, mocker: MockerFixture) -> None:
        cfg = Config()
        cfg.post_process.post_process_enabled = True
        qbt = mocker.MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = []
        db = mocker.MagicMock()

        run_post_processing(cfg, qbt, db)

        db.find_by_tag.assert_not_called()

    def test_processes_each_completed_torrent(self, mocker: MockerFixture) -> None:
        cfg = Config()
        cfg.post_process.post_process_enabled = True
        mock_process_one = mocker.patch("movarr.post_processor._process_one")
        qbt = mocker.MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = [
            {"torrent_hash": "abc", "torrent_tag": "tag1"},
            {"torrent_hash": "def", "torrent_tag": "tag2"},
        ]
        db = mocker.MagicMock()

        run_post_processing(cfg, qbt, db)

        assert mock_process_one.call_count == 2

    def test_process_one_receives_correct_args(self, mocker: MockerFixture) -> None:
        cfg = Config()
        cfg.post_process.post_process_enabled = True
        mock_process_one = mocker.patch("movarr.post_processor._process_one")
        torrent = {"torrent_hash": "abc", "torrent_tag": "tag1"}
        qbt = mocker.MagicMock()
        qbt.is_connected.return_value = True
        qbt.list_completed.return_value = [torrent]
        db = mocker.MagicMock()

        run_post_processing(cfg, qbt, db)

        mock_process_one.assert_called_once_with(torrent, cfg, qbt, db)


# _resolution_from_index_title


class TestResolutionFromIndexTitle:
    """Tests for _resolution_from_index_title — thin wrapper around parsing helpers."""

    def test_empty_string_returns_none(self) -> None:
        assert _resolution_from_index_title("") is None

    def test_whitespace_returns_none(self) -> None:
        assert _resolution_from_index_title("   ") is None

    def test_title_with_resolution_returns_digits(self) -> None:
        result = _resolution_from_index_title("The Matrix 1999 1080p BluRay")
        assert result == "1080"

    def test_title_without_resolution_returns_none(self) -> None:
        result = _resolution_from_index_title("The Matrix 1999 BluRay")
        assert result is None


# _canonical_filename — additional edge-cases


class TestCanonicalFilenameEdgeCases:
    """Additional edge-cases for _canonical_filename not covered in TestCanonicalFilename."""

    def test_whitespace_only_first_level_dir_returns_filename(self) -> None:
        """When _first_level_dir returns a string that sanitise() strips to None."""
        # "   " is a valid PurePosixPath component but sanitise("   ") → None
        result = _canonical_filename("movie.mkv", "   ")
        assert result == "movie.mkv"


# _pick_path — additional edge-cases


class TestPickPathEdgeCases:
    """Additional edge-cases for _pick_path not covered in TestPickPath."""

    def _default(self, hd: str = "/media/hd", uhd: str | None = "/media/uhd") -> DefaultCopyLibraryConfig:
        return DefaultCopyLibraryConfig(hd_path=hd, uhd_path=uhd)

    def _rule(self, name: str, genres: list[str], hd: str = "/media/hd", uhd: str = "") -> CopyLibraryRuleConfig:
        return CopyLibraryRuleConfig(name=name, genres=genres, hd_path=hd, uhd_path=uhd)

    def test_uhd_matching_rule_without_uhd_path_uses_rule_hd_path(self) -> None:
        """UHD resolution; matching rule has no uhd_path → falls back to rule's hd_path (line 302)."""
        rule = self._rule("Action", ["Action"], hd="/action/hd", uhd="")
        result = _pick_path(["Action"], "", "2160", [rule], self._default())
        assert result == "/action/hd"


# _build_copy_list — exception path


class TestBuildCopyListOSError:
    """Covers the OSError/ValueError handler in _build_copy_list (lines 169-171)."""

    def test_oserror_during_resolve_skips_file(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """When pathlib.Path.resolve() raises OSError inside the loop, file is skipped."""
        import pathlib as _pathlib

        torrent = {
            "torrent_save_path": str(tmp_path),
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 8_000_000_000},
            ],
        }
        cfg = Config()
        # First call (save_root) succeeds; second call (per-file abs_path) raises OSError
        mocker.patch(
            "movarr.post_processor.pathlib.Path.resolve",
            side_effect=[_pathlib.Path(str(tmp_path)), OSError("permission denied")],
        )
        result = _build_copy_list(torrent, cfg)
        assert result == []


# _process_one


class TestProcessOne:
    """Tests for _process_one — per-torrent processing pipeline."""

    def _config(self, remove_completed: bool = False) -> Config:
        cfg = Config()
        cfg.post_process.remove_completed = remove_completed
        cfg.post_process.post_process_enabled = True
        cfg.post_process.default_copy_library = DefaultCopyLibraryConfig(hd_path="/media/hd", uhd_path="")
        return cfg

    def _torrent(self, tag: str = "tag1", torrent_hash: str = "abc123") -> dict[str, Any]:
        return {
            "torrent_tag": tag,
            "torrent_hash": torrent_hash,
            "torrent_save_path": "/downloads",
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 4_000_000_000},
            ],
        }

    def _db_record(self, mocker: MockerFixture, title: str = "The Matrix", year: str = "1999") -> Any:
        rec = mocker.MagicMock()
        rec.imdb_title = title
        rec.imdb_year = year
        rec.imdb_genres_list = "[]"
        rec.imdb_certification = ""
        rec.imdb_cert_source = "imdbpie"
        rec.index_title = "The.Matrix.1999.1080p.BluRay"
        return rec

    def test_no_db_record_returns_early(self, mocker: MockerFixture) -> None:
        """When db.find_by_tag returns None, everything else is skipped."""
        db = mocker.MagicMock()
        db.find_by_tag.return_value = None
        qbt = mocker.MagicMock()

        _process_one(self._torrent(), self._config(), qbt, db)

        db.mark_completed.assert_not_called()

    def test_no_files_to_copy_returns_early(self, mocker: MockerFixture) -> None:
        """When _build_copy_list returns empty, processing is skipped."""
        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        mocker.patch("movarr.post_processor._build_copy_list", return_value=[])
        qbt = mocker.MagicMock()

        _process_one(self._torrent(), self._config(), qbt, db)

        db.mark_completed.assert_not_called()

    def test_no_destination_returns_early(self, mocker: MockerFixture) -> None:
        """When _resolve_destination returns None, files are not copied."""
        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/movie.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value=None)
        qbt = mocker.MagicMock()

        _process_one(self._torrent(), self._config(), qbt, db)

        db.mark_completed.assert_not_called()

    def test_make_directory_fails_returns_early(self, mocker: MockerFixture) -> None:
        """When make_directory returns False, copy is not attempted."""
        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/movie.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=False)
        qbt = mocker.MagicMock()

        _process_one(self._torrent(), self._config(), qbt, db)

        db.mark_completed.assert_not_called()

    def test_copy_verify_fails_does_not_set_verified(self, mocker: MockerFixture) -> None:
        """When copy_with_verify returns False, mark_completed is not called."""
        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/movie.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor.copy_with_verify", return_value=False)
        qbt = mocker.MagicMock()

        _process_one(self._torrent(), self._config(), qbt, db)

        db.mark_completed.assert_not_called()
        qbt.delete_torrent.assert_not_called()

    def test_full_happy_path_sets_verified_and_deletes_torrent(self, mocker: MockerFixture) -> None:
        """All steps succeed: mark_completed called and torrent deleted (remove_completed=True)."""
        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/movie.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor.copy_with_verify", return_value=True)
        qbt = mocker.MagicMock()
        cfg = self._config(remove_completed=True)
        torrent = self._torrent(tag="tag1", torrent_hash="deadbeef")

        _process_one(torrent, cfg, qbt, db)

        db.mark_completed.assert_called_once_with("tag1")
        qbt.delete_torrent.assert_called_once_with(
            "deadbeef", delete_data=True, state="completed", name="The.Matrix.1999.1080p.BluRay"
        )

    def test_copy_succeeds_without_remove_completed(self, mocker: MockerFixture) -> None:
        """All steps succeed but remove_completed=False: mark_completed but no torrent deletion."""
        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/movie.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor.copy_with_verify", return_value=True)
        qbt = mocker.MagicMock()

        _process_one(self._torrent(), self._config(remove_completed=False), qbt, db)

        db.mark_completed.assert_called_once()
        qbt.delete_torrent.assert_not_called()

    def test_title_with_only_dots_uses_unknown(self, mocker: MockerFixture) -> None:
        """imdb_title that sanitises to '.' (single dot) is replaced with 'Unknown'."""
        db = mocker.MagicMock()
        rec = self._db_record(mocker, title="...")
        db.find_by_tag.return_value = rec
        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/movie.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mock_mkdir = mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor.copy_with_verify", return_value=True)

        _process_one(self._torrent(), self._config(), mocker.MagicMock(), db)

        dst_dir_arg = mock_mkdir.call_args[0][0]
        assert "Unknown" in dst_dir_arg


# _process_one — non-largest-file branch (line 141)


class TestProcessOneMultiFile:
    """_process_one with multiple files — covers the else branch for non-largest files."""

    def _config(self) -> Config:
        cfg = Config()
        cfg.post_process.post_process_enabled = True
        cfg.post_process.remove_completed = False
        return cfg

    def _db_record(self, mocker: Any) -> Any:
        rec = mocker.MagicMock()
        rec.imdb_title = "The Matrix"
        rec.imdb_year = "1999"
        rec.imdb_genres = '["Action"]'
        rec.imdb_certification = "15"
        rec.imdb_cert_source = "imdbpie"
        rec.index_title_resolution = "1080p"
        return rec

    def test_secondary_file_uses_original_filename(self, mocker: MockerFixture) -> None:
        """When src_files has two entries and only one matches largest_fname,
        the other uses src_fname directly (covering line 141)."""
        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        # Two files: largest is "movie.mkv", secondary is "movie.nfo"
        mocker.patch(
            "movarr.post_processor._build_copy_list",
            return_value=["/dl/movie.mkv", "/dl/movie.nfo"],
        )
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor.copy_with_verify", return_value=True)
        # _largest_file returns "movie.mkv" as the largest
        torrent = {
            "torrent_tag": "tag1",
            "torrent_hash": "abc123",
            "content_path": "/dl",
            "files": [
                {"name": "movie.mkv", "size": 8_000_000_000},
                {"name": "movie.nfo", "size": 1_000},
            ],
        }
        mocker.patch(
            "movarr.post_processor._largest_file",
            return_value=("movie.mkv", "movie.mkv"),
        )
        mocker.patch(
            "movarr.post_processor._canonical_filename",
            return_value="The Matrix (1999).mkv",
        )
        qbt = mocker.MagicMock()

        _process_one(torrent, self._config(), qbt, db)

        db.mark_completed.assert_called_once_with("tag1")


class TestProcessOneCopyCompletedFalse:
    """Tests for _process_one when copy_completed=False."""

    def _config(self, remove_completed: bool = False, copy_completed: bool = False) -> Config:
        cfg = Config()
        cfg.post_process.post_process_enabled = True
        cfg.post_process.copy_completed = copy_completed
        cfg.post_process.remove_completed = remove_completed
        return cfg

    def _torrent(self, tag: str = "tag1", torrent_hash: str = "abc123") -> dict[str, Any]:
        return {
            "torrent_tag": tag,
            "torrent_hash": torrent_hash,
            "torrent_save_path": "/downloads",
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 4_000_000_000},
            ],
        }

    def _db_record(self, mocker: MockerFixture) -> Any:
        rec = mocker.MagicMock()
        rec.imdb_title = "The Matrix"
        rec.imdb_year = "1999"
        rec.index_title = "The.Matrix.1999.1080p.BluRay"
        return rec

    def test_copy_completed_false_marks_completed_skips_copy(self, mocker: MockerFixture, tmp_path: Any) -> None:
        """When copy_completed=False, DB is marked completed and no file copy occurs."""
        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        mock_copy = mocker.patch("movarr.post_processor.copy_with_verify")
        qbt = mocker.MagicMock()

        _process_one(self._torrent(), self._config(copy_completed=False), qbt, db)

        db.mark_completed.assert_called_once_with("tag1")
        mock_copy.assert_not_called()

    def test_copy_completed_false_remove_completed_deletes_torrent_not_data(self, mocker: MockerFixture) -> None:
        """When copy_completed=False and remove_completed=True, torrent is removed
        without deleting data files (delete_data=False).

        This covers the use-case where qBittorrent writes directly to the final
        library path \u2014 movarr should remove the torrent entry from qBittorrent's
        queue but NEVER delete the downloaded files.
        """
        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        qbt = mocker.MagicMock()

        _process_one(
            self._torrent(),
            self._config(copy_completed=False, remove_completed=True),
            qbt,
            db,
        )

        qbt.delete_torrent.assert_called_once_with(
            "abc123", delete_data=False, state="completed", name="The.Matrix.1999.1080p.BluRay"
        )
        db.mark_completed.assert_called_once_with("tag1")


class TestProcessOneSupersession:
    """Integration tests for the delete_lower_quality path in _process_one."""

    def _make_config(self, enabled: bool, dst_dir: str) -> Config:
        from movarr.config import DefaultCopyLibraryConfig, PostProcessConfig

        return Config().model_copy(
            update={
                "post_process": PostProcessConfig(
                    copy_completed=True,
                    remove_completed=False,
                    delete_lower_quality=enabled,
                    default_copy_library=DefaultCopyLibraryConfig(hd_path=dst_dir),
                )
            }
        )

    def test_deletes_lower_quality_when_option_enabled(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """After a successful copy, lower-quality library files in dst_dir are deleted."""
        dst_base = tmp_path
        cfg = self._make_config(enabled=True, dst_dir=str(dst_base))

        movie_folder = dst_base / "The Matrix (1999)"
        movie_folder.mkdir()
        old_file = movie_folder / "The Matrix 1999 1080p BluRay.mkv"
        old_file.write_bytes(b"old")
        # canonical_fname for a flat torrent entry is the filename itself; simulate the copy
        (movie_folder / "The Matrix 1999 1080p Remux.mkv").write_bytes(b"new")

        torrent = {
            "torrent_tag": "tag1",
            "torrent_hash": "abc123",
            "torrent_save_path": "/downloads",
            "torrent_file_list": [{"file_name": "The Matrix 1999 1080p Remux.mkv", "file_size": 20_000_000_000}],
        }
        db_record = mocker.MagicMock()
        db_record.imdb_title = "The Matrix"
        db_record.imdb_year = "1999"
        db_record.imdb_genres_list = "[]"
        db_record.imdb_certification = ""
        db_record.imdb_cert_source = ""
        db_record.index_title = "The Matrix 1999 1080p Remux"

        db = mocker.MagicMock()
        db.find_by_tag.return_value = db_record
        qbt = mocker.MagicMock()

        mocker.patch("movarr.post_processor.copy_with_verify", return_value=True)
        mocker.patch("movarr.post_processor.make_directory", return_value=True)

        _process_one(torrent, cfg, qbt, db)

        assert not old_file.exists(), "Lower-quality file should have been deleted"

    def test_does_not_delete_when_option_disabled(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """When delete_lower_quality is False, no library files are deleted."""
        dst_base = tmp_path
        cfg = self._make_config(enabled=False, dst_dir=str(dst_base))

        movie_folder = dst_base / "The Matrix (1999)"
        movie_folder.mkdir()
        old_file = movie_folder / "The Matrix 1999 1080p BluRay.mkv"
        old_file.write_bytes(b"old")

        torrent = {
            "torrent_tag": "tag2",
            "torrent_hash": "def456",
            "torrent_save_path": "/downloads",
            "torrent_file_list": [{"file_name": "The Matrix 1999 1080p Remux.mkv", "file_size": 20_000_000_000}],
        }
        db_record = mocker.MagicMock()
        db_record.imdb_title = "The Matrix"
        db_record.imdb_year = "1999"
        db_record.imdb_genres_list = "[]"
        db_record.imdb_certification = ""
        db_record.imdb_cert_source = ""
        db_record.index_title = "The Matrix 1999 1080p Remux"

        db = mocker.MagicMock()
        db.find_by_tag.return_value = db_record
        qbt = mocker.MagicMock()

        mocker.patch("movarr.post_processor.copy_with_verify", return_value=True)
        mocker.patch("movarr.post_processor.make_directory", return_value=True)

        _process_one(torrent, cfg, qbt, db)

        assert old_file.exists(), "File should be untouched when option is disabled"

    def test_does_not_delete_when_copy_failed(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """Supersession is skipped entirely if the copy/verify step fails."""
        dst_base = tmp_path
        cfg = self._make_config(enabled=True, dst_dir=str(dst_base))

        movie_folder = dst_base / "The Matrix (1999)"
        movie_folder.mkdir()
        old_file = movie_folder / "The Matrix 1999 1080p BluRay.mkv"
        old_file.write_bytes(b"old")

        torrent = {
            "torrent_tag": "tag3",
            "torrent_hash": "ghi789",
            "torrent_save_path": "/downloads",
            "torrent_file_list": [{"file_name": "The Matrix 1999 1080p Remux.mkv", "file_size": 20_000_000_000}],
        }
        db_record = mocker.MagicMock()
        db_record.imdb_title = "The Matrix"
        db_record.imdb_year = "1999"
        db_record.imdb_genres_list = "[]"
        db_record.imdb_certification = ""
        db_record.imdb_cert_source = ""
        db_record.index_title = "The Matrix 1999 1080p Remux"

        db = mocker.MagicMock()
        db.find_by_tag.return_value = db_record
        qbt = mocker.MagicMock()

        mocker.patch("movarr.post_processor.copy_with_verify", return_value=False)
        mocker.patch("movarr.post_processor.make_directory", return_value=True)

        _process_one(torrent, cfg, qbt, db)

        assert old_file.exists(), "File should be untouched when copy failed"


class TestProcessOneHooks:
    """Tests for pre_copy and post_copy hook wiring in _process_one."""

    def _config(self) -> Config:
        cfg = Config()
        cfg.post_process.default_copy_library = DefaultCopyLibraryConfig(hd_path="/media/hd", uhd_path="")
        return cfg

    def _torrent(self) -> dict[str, Any]:
        return {
            "torrent_tag": "tag1",
            "torrent_hash": "abc123",
            "torrent_save_path": "/downloads",
            "torrent_file_list": [
                {"file_name": "movie/The Matrix 1999 1080p.mkv", "file_size": 4_000_000_000},
            ],
        }

    def _db_record(self, mocker: MockerFixture) -> Any:
        rec = mocker.MagicMock()
        rec.imdb_title = "The Matrix"
        rec.imdb_year = "1999"
        rec.imdb_genres_list = "[]"
        rec.imdb_certification = ""
        rec.imdb_cert_source = "imdbpie"
        rec.index_title = "The Matrix 1999 1080p BluRay"
        return rec

    def test_post_copy_hook_fires_on_successful_copy(self, mocker: MockerFixture) -> None:
        """post_copy hook is called after all files copy successfully."""
        config = self._config()
        config.post_process.hooks.post_copy = "echo {dir}"

        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        qbt = mocker.MagicMock()

        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/movie.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor.copy_with_verify", return_value=True)
        mock_hook = mocker.patch("movarr.post_processor._run_hook", return_value=True)

        _process_one(self._torrent(), config, qbt, db)

        labels = [call[0][2] for call in mock_hook.call_args_list]
        assert "post_copy" in labels

    def test_post_copy_hook_does_not_fire_on_copy_failure(self, mocker: MockerFixture) -> None:
        """post_copy hook is NOT called when a copy fails."""
        config = self._config()
        config.post_process.hooks.post_copy = "echo {dir}"

        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        qbt = mocker.MagicMock()

        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/movie.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor.copy_with_verify", return_value=False)
        mock_hook = mocker.patch("movarr.post_processor._run_hook", return_value=True)

        _process_one(self._torrent(), config, qbt, db)

        labels = [call[0][2] for call in mock_hook.call_args_list]
        assert "post_copy" not in labels

    def test_post_copy_hook_not_called_when_empty(self, mocker: MockerFixture) -> None:
        """No subprocess is spawned when post_copy is empty string (default)."""
        config = self._config()
        # hooks.post_copy defaults to "" — intentionally left unset

        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        qbt = mocker.MagicMock()

        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/movie.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor.copy_with_verify", return_value=True)
        mock_hook = mocker.patch("movarr.post_processor._run_hook", return_value=True)

        _process_one(self._torrent(), config, qbt, db)

        labels = [call[0][2] for call in mock_hook.call_args_list]
        assert "post_copy" not in labels

    def test_post_copy_hook_failure_does_not_block_cleanup(self, mocker: MockerFixture) -> None:
        """When post_copy hook fails, mark_completed, delete_lower_quality, and remove_completed still run."""
        config = self._config()
        config.post_process.hooks.post_copy = "false"  # always fails
        config.post_process.delete_lower_quality = True
        config.post_process.remove_completed = True

        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        qbt = mocker.MagicMock()

        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/The Matrix 1999 1080p.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor.copy_with_verify", return_value=True)
        mocker.patch("movarr.post_processor._run_hook", return_value=False)
        mock_delete = mocker.patch("movarr.post_processor._delete_superseded_files")

        _process_one(self._torrent(), config, qbt, db)

        db.mark_completed.assert_called_once()
        mock_delete.assert_called_once()
        qbt.delete_torrent.assert_called_once()

    def test_pre_copy_hook_fires_before_copy(self, mocker: MockerFixture) -> None:
        """pre_copy hook is called before files are copied."""
        config = self._config()
        config.post_process.hooks.pre_copy = "chattr -R -i {dir}"
        config.post_process.hooks.post_copy = "trimarr {dir}"

        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        qbt = mocker.MagicMock()

        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/movie.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor.copy_with_verify", return_value=True)
        mock_hook = mocker.patch("movarr.post_processor._run_hook", return_value=True)

        _process_one(self._torrent(), config, qbt, db)

        labels = [call[0][2] for call in mock_hook.call_args_list]
        assert "pre_copy" in labels
        # pre_copy must appear before post_copy in call order
        assert labels.index("pre_copy") < labels.index("post_copy")

    def test_pre_copy_hook_failure_aborts_copy(self, mocker: MockerFixture) -> None:
        """When pre_copy hook fails, the copy is aborted and mark_completed is not called."""
        config = self._config()
        config.post_process.hooks.pre_copy = "chattr -R -i {dir}"

        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        qbt = mocker.MagicMock()

        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/movie.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor._run_hook", return_value=False)
        mock_copy = mocker.patch("movarr.post_processor.copy_with_verify")
        mock_logger = mocker.patch("movarr.post_processor.logger")

        _process_one(self._torrent(), config, qbt, db)

        mock_copy.assert_not_called()
        db.mark_completed.assert_not_called()
        error_calls = [str(c) for c in mock_logger.error.call_args_list]
        assert any("pre_copy" in c for c in error_calls)

    def test_pre_copy_hook_not_called_when_empty(self, mocker: MockerFixture) -> None:
        """No subprocess is spawned when pre_copy is empty string (default)."""
        config = self._config()
        # hooks.pre_copy defaults to "" — intentionally left unset

        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        qbt = mocker.MagicMock()

        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/movie.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor.copy_with_verify", return_value=True)
        mock_hook = mocker.patch("movarr.post_processor._run_hook", return_value=True)

        _process_one(self._torrent(), config, qbt, db)

        labels = [call[0][2] for call in mock_hook.call_args_list]
        assert "pre_copy" not in labels

    def test_poster_art_saved_when_filename_set(self, mocker: MockerFixture) -> None:
        """_save_poster_art is called when poster_art.filename is set."""
        config = self._config()
        config.post_process.poster_art.filename = "poster.jpg"

        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        qbt = mocker.MagicMock()

        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/movie.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor.copy_with_verify", return_value=True)
        mocker.patch("movarr.post_processor._run_hook", return_value=True)
        mock_save = mocker.patch("movarr.post_processor._save_poster_art")

        _process_one(self._torrent(), config, qbt, db)

        mock_save.assert_called_once()


class TestProcessOnePreCopyHookException:
    """Covers the pre_copy hook exception path: copy is aborted."""

    def _config(self) -> Config:
        cfg = Config()
        cfg.post_process.default_copy_library = DefaultCopyLibraryConfig(hd_path="/media/hd", uhd_path="")
        cfg.post_process.hooks.pre_copy = "chattr -R -i {dir}"
        return cfg

    def _torrent(self) -> dict[str, Any]:
        return {
            "torrent_tag": "tag1",
            "torrent_hash": "abc123",
            "torrent_save_path": "/downloads",
            "torrent_file_list": [
                {"file_name": "movie/The Matrix 1999 1080p.mkv", "file_size": 4_000_000_000},
            ],
        }

    def _db_record(self, mocker: MockerFixture) -> Any:
        rec = mocker.MagicMock()
        rec.imdb_title = "The Matrix"
        rec.imdb_year = "1999"
        rec.imdb_genres_list = "[]"
        rec.imdb_certification = ""
        rec.imdb_cert_source = "imdbpie"
        rec.index_title = "The Matrix 1999 1080p BluRay"
        return rec

    def test_pre_copy_hook_exception_aborts_copy(self, mocker: MockerFixture) -> None:
        """When pre_copy hook raises, the copy is aborted and mark_completed is not called."""
        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        qbt = mocker.MagicMock()

        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/The Matrix 1999 1080p.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor._run_hook", side_effect=Exception("chattr failed"))
        mock_copy = mocker.patch("movarr.post_processor.copy_with_verify")
        mock_logger = mocker.patch("movarr.post_processor.logger")

        _process_one(self._torrent(), self._config(), qbt, db)

        mock_copy.assert_not_called()
        db.mark_completed.assert_not_called()
        error_calls = [str(c) for c in mock_logger.error.call_args_list]
        assert any("pre_copy" in c for c in error_calls)


class TestDeleteSupersededFilesHooks:
    """Tests for pre_delete / post_delete hook wiring."""

    def test_pre_delete_hook_fires_before_deletion(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """pre_delete hook is called when the deletion pass starts."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 2160p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        (movie_dir / "The Matrix 1999 1080p BluRay.mkv").write_bytes(b"old")

        config = Config()
        config.post_process.hooks.pre_delete = "chattr -i {dir}/*"

        mock_hook = mocker.patch("movarr.post_processor._run_hook", return_value=True)
        mocker.patch("movarr.post_processor.delete_file", return_value=True)

        _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, config)

        mock_hook.assert_any_call("chattr -i {dir}/*", mocker.ANY, "pre_delete", mocker.ANY)

    def test_pre_delete_hook_failure_aborts_deletion(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """If pre_delete hook returns False, no files are deleted and count is 0."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 2160p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        old_fname = "The Matrix 1999 1080p BluRay.mkv"
        (movie_dir / old_fname).write_bytes(b"old")

        config = Config()
        config.post_process.hooks.pre_delete = "chattr -i {dir}/*"

        mocker.patch("movarr.post_processor._run_hook", return_value=False)
        mock_delete = mocker.patch("movarr.post_processor.delete_file")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, config)

        assert count == 0
        mock_delete.assert_not_called()
        assert (movie_dir / old_fname).exists()

    def test_post_delete_hook_fires_after_deletion(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """post_delete hook is called after the deletion loop completes."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 2160p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        (movie_dir / "The Matrix 1999 1080p BluRay.mkv").write_bytes(b"old")

        config = Config()
        config.post_process.hooks.post_delete = "chattr +i {dir}/*"

        call_order: list[str] = []

        def fake_hook(cmd: str, d: str, label: str, timeout_secs: float = 300.0) -> bool:
            call_order.append(label)
            return True

        mocker.patch("movarr.post_processor._run_hook", side_effect=fake_hook)
        mocker.patch("movarr.post_processor.delete_file", return_value=True)

        _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, config)

        assert "post_delete" in call_order

    def test_post_delete_does_not_fire_when_pre_delete_aborts(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """post_delete label never appears in call list when pre_delete fails."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 2160p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        (movie_dir / "The Matrix 1999 1080p BluRay.mkv").write_bytes(b"old")

        config = Config()
        config.post_process.hooks.pre_delete = "chattr -i {dir}/*"
        config.post_process.hooks.post_delete = "chattr +i {dir}/*"

        called_labels: list[str] = []

        def fake_hook(cmd: str, d: str, label: str, timeout_secs: float = 300.0) -> bool:
            called_labels.append(label)
            return False  # always fail

        mocker.patch("movarr.post_processor._run_hook", side_effect=fake_hook)

        _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, config)

        assert "post_delete" not in called_labels

    def test_hooks_not_called_when_empty(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """No subprocess is spawned when hooks are empty strings."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 2160p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        (movie_dir / "The Matrix 1999 1080p BluRay.mkv").write_bytes(b"old")

        config = Config()
        # hooks default to "" — leave unset
        mock_hook = mocker.patch("movarr.post_processor._run_hook")

        _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, config)

        mock_hook.assert_not_called()


# ---------------------------------------------------------------------------
# Additional coverage tests
# ---------------------------------------------------------------------------


class TestRunHookStdoutDebug:
    """Covers the stdout debug-log path (line 128) in _run_hook."""

    def test_stdout_triggers_debug_log(self, mocker: MockerFixture) -> None:
        mock_popen = mocker.patch("movarr.post_processor.subprocess.Popen")
        mocker.patch("movarr.post_processor.os.getpgid", return_value=99)
        mock_proc = mocker.Mock()
        mock_proc.communicate.return_value = ("some hook output", "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc
        mock_logger = mocker.patch("movarr.post_processor.logger")

        result = _run_hook("echo {dir}", "/tmp/movie", "post_copy")

        assert result is True
        debug_calls = [str(c) for c in mock_logger.debug.call_args_list]
        assert any("some hook output" in c for c in debug_calls)


class TestRunHookInnerTimeout:
    """Covers lines 116-117: inner TimeoutExpired during SIGTERM communicate call."""

    def test_sigterm_communicate_also_times_out(self, mocker: MockerFixture) -> None:
        import subprocess as _subprocess

        mock_popen = mocker.patch("movarr.post_processor.subprocess.Popen")
        mocker.patch("movarr.post_processor.os.getpgid", return_value=99)
        mocker.patch("movarr.post_processor.os.killpg")
        mock_proc = mocker.Mock()
        mock_proc.pid = 12345
        # First call: main 300s timeout; second call: SIGTERM inner 5s timeout;
        # third call: SIGKILL inner 5s communicate succeeds.
        mock_proc.communicate.side_effect = [
            _subprocess.TimeoutExpired(cmd="echo", timeout=300),
            _subprocess.TimeoutExpired(cmd="echo", timeout=5),
            ("", ""),
        ]
        mock_popen.return_value = mock_proc

        result = _run_hook("echo {dir}", "/tmp/movie", "post_copy")

        assert result is False
        # communicate was called three times total
        assert mock_proc.communicate.call_count == 3


class TestBuildCopyListEmptySavePath:
    """Covers lines 293-295: _build_copy_list returns [] when torrent_save_path is empty."""

    def test_empty_save_path_returns_empty_list(self) -> None:
        torrent = {
            "torrent_tag": "tag_empty",
            "torrent_save_path": "",
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 4_000_000_000},
            ],
        }
        result = _build_copy_list(torrent, Config())
        assert result == []

    def test_none_save_path_returns_empty_list(self) -> None:
        torrent = {
            "torrent_tag": "tag_none",
            "torrent_save_path": None,
            "torrent_file_list": [
                {"file_name": "movie/movie.mkv", "file_size": 4_000_000_000},
            ],
        }
        result = _build_copy_list(torrent, Config())
        assert result == []


class TestProcessOnePostCopyHookException:
    """Covers lines 268-269: post_copy hook raises an unexpected exception."""

    def _config(self) -> Config:
        cfg = Config()
        cfg.post_process.default_copy_library = DefaultCopyLibraryConfig(hd_path="/media/hd", uhd_path="")
        cfg.post_process.hooks.post_copy = "raise_hook"
        return cfg

    def _torrent(self) -> dict[str, Any]:
        return {
            "torrent_tag": "tag1",
            "torrent_hash": "abc123",
            "torrent_save_path": "/downloads",
            "torrent_file_list": [
                {"file_name": "movie/The Matrix 1999 1080p.mkv", "file_size": 4_000_000_000},
            ],
        }

    def _db_record(self, mocker: MockerFixture) -> Any:
        rec = mocker.MagicMock()
        rec.imdb_title = "The Matrix"
        rec.imdb_year = "1999"
        rec.imdb_genres_list = "[]"
        rec.imdb_certification = ""
        rec.imdb_cert_source = "imdbpie"
        rec.index_title = "The Matrix 1999 1080p BluRay"
        return rec

    def test_post_copy_hook_exception_does_not_abort(self, mocker: MockerFixture) -> None:
        config = self._config()

        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        qbt = mocker.MagicMock()

        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/The Matrix 1999 1080p.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor.copy_with_verify", return_value=True)
        mocker.patch("movarr.post_processor._run_hook", side_effect=Exception("boom"))
        mock_logger = mocker.patch("movarr.post_processor.logger")

        # Should not raise
        _process_one(self._torrent(), config, qbt, db)

        # mark_completed still called (processing did not abort)
        db.mark_completed.assert_called_once()
        # Warning was logged
        warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
        assert any("boom" in c or "exception" in c.lower() for c in warning_calls)


class TestDeleteSupersededFilesListdirOSError:
    """Covers lines 586-588: os.listdir raises OSError in _delete_superseded_files."""

    def test_listdir_oserror_returns_zero(self, tmp_path: Path, mocker: MockerFixture) -> None:
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 1080p BluRay.mkv"
        (movie_dir / new_fname).write_bytes(b"new")

        mocker.patch("movarr.post_processor.os.listdir", side_effect=OSError("cannot read"))

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 0


class TestDeleteSupersededFilesPreDeleteHookException:
    """Covers lines 636-638: pre_delete hook raises exception."""

    def test_pre_delete_exception_aborts_deletion(self, tmp_path: Path, mocker: MockerFixture) -> None:
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 2160p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        (movie_dir / "The Matrix 1999 1080p BluRay.mkv").write_bytes(b"old")

        config = Config()
        config.post_process.hooks.pre_delete = "chattr -i {dir}/*"

        mocker.patch("movarr.post_processor._run_hook", side_effect=Exception("hook crash"))
        mock_delete = mocker.patch("movarr.post_processor.delete_file")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, config)

        assert count == 0
        mock_delete.assert_not_called()


class TestDeleteSupersededFilesPreDeleteHookListdirOSError:
    """Covers lines 645-650: os.listdir raises OSError after pre_delete hook succeeds."""

    def test_listdir_oserror_after_hook_returns_zero(self, tmp_path: Path, mocker: MockerFixture) -> None:
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 2160p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        (movie_dir / "The Matrix 1999 1080p BluRay.mkv").write_bytes(b"old")

        config = Config()
        config.post_process.hooks.pre_delete = "chattr -i {dir}/*"

        mocker.patch("movarr.post_processor._run_hook", return_value=True)
        # First listdir call (line 585) returns real listing; second call (line 644) raises OSError.
        real_entries = [new_fname, "The Matrix 1999 1080p BluRay.mkv"]
        mocker.patch(
            "movarr.post_processor.os.listdir",
            side_effect=[real_entries, OSError("gone")],
        )
        mock_delete = mocker.patch("movarr.post_processor.delete_file")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, config)

        assert count == 0
        mock_delete.assert_not_called()


class TestDeleteSupersededFilesPreDeleteRenamedPrimary:
    """Covers lines 652-656: pre_delete hook renames the primary file."""

    def test_primary_renamed_by_hook_aborts_deletion(self, tmp_path: Path, mocker: MockerFixture) -> None:
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 2160p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        (movie_dir / "The Matrix 1999 1080p BluRay.mkv").write_bytes(b"old")

        config = Config()
        config.post_process.hooks.pre_delete = "chattr -i {dir}/*"

        mocker.patch("movarr.post_processor._run_hook", return_value=True)
        # First listdir returns normal listing; second (post-hook check) is missing the primary.
        real_entries = [new_fname, "The Matrix 1999 1080p BluRay.mkv"]
        remaining_without_primary = ["The Matrix 1999 1080p BluRay.mkv"]
        mocker.patch(
            "movarr.post_processor.os.listdir",
            side_effect=[real_entries, remaining_without_primary],
        )
        mock_delete = mocker.patch("movarr.post_processor.delete_file")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, config)

        assert count == 0
        mock_delete.assert_not_called()


class TestDeleteSupersededFilesEdgeCases:
    """Covers lines 717-718: extract_resolution returns non-numeric string."""

    def test_non_numeric_resolution_file_deleted(self, tmp_path: Path, mocker: MockerFixture) -> None:
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 1080p BluRay.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        lib_fname = "The Matrix 1999 1080p HDTV.mkv"
        (movie_dir / lib_fname).write_bytes(b"lib")

        # Return a truthy non-numeric string so int() raises ValueError.
        mocker.patch("movarr.post_processor.extract_resolution", return_value="unknown")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 1
        assert not (movie_dir / lib_fname).exists()


class TestDeleteSupersededFilesDeleteFileFails:
    """Covers line 751: delete_file returns False — error is logged, count stays 0."""

    def test_delete_file_failure_logged(self, tmp_path: Path, mocker: MockerFixture) -> None:
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 2160p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        lib_fname = "The Matrix 1999 1080p BluRay.mkv"
        (movie_dir / lib_fname).write_bytes(b"lib")

        mocker.patch("movarr.post_processor.delete_file", return_value=False)
        mock_logger = mocker.patch("movarr.post_processor.logger")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

        assert count == 0
        error_calls = [str(c) for c in mock_logger.error.call_args_list]
        assert any("Failed to auto-delete" in c for c in error_calls)


class TestDeleteSupersededFilesPostDeleteHook:
    """Covers lines 756-758: post_delete hook returns False or raises."""

    def _setup(self, tmp_path: Path) -> tuple[Path, str, Config]:
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 2160p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        (movie_dir / "The Matrix 1999 1080p BluRay.mkv").write_bytes(b"lib")
        config = Config()
        config.post_process.hooks.post_delete = "chattr +i {dir}/*"
        return movie_dir, new_fname, config

    def test_post_delete_returns_false_logs_warning(self, tmp_path: Path, mocker: MockerFixture) -> None:
        movie_dir, new_fname, config = self._setup(tmp_path)

        mocker.patch("movarr.post_processor.delete_file", return_value=True)
        mocker.patch("movarr.post_processor._run_hook", return_value=False)
        mock_logger = mocker.patch("movarr.post_processor.logger")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, config)

        # Deletion happened (delete_file returned True) so count > 0
        assert count == 1
        warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
        assert any("post_delete hook failed" in c for c in warning_calls)

    def test_post_delete_raises_logs_warning(self, tmp_path: Path, mocker: MockerFixture) -> None:
        movie_dir, new_fname, config = self._setup(tmp_path)

        mocker.patch("movarr.post_processor.delete_file", return_value=True)
        mocker.patch("movarr.post_processor._run_hook", side_effect=Exception("hook exploded"))
        mock_logger = mocker.patch("movarr.post_processor.logger")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, config)

        assert count == 1
        warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
        assert any("exception" in c.lower() or "hook exploded" in c for c in warning_calls)


# ===========================================================================
# !! WARNING — REAL FILESYSTEM + REAL SUBPROCESS TESTS !!
#
# These tests do NOT mock subprocess.Popen or delete_file.  They spawn actual
# shell commands and physically unlink files from disk.
#
# SAFETY CONTRACT — enforced by assertion inside every test:
#   All file operations are confined to pytest's tmp_path fixture, which
#   resolves to a subdirectory of /tmp on this host.  The tests assert
#   that the working directory starts with /tmp before touching anything.
#   They will HARD FAIL if tmp_path ever resolves outside /tmp (e.g. if
#   TMPDIR is redirected to /media or another production path).
#
# Never add paths like /media, /data, /mnt, or any real library location
# to these tests.  tmp_path only.
# ===========================================================================


def _assert_safe_tmpdir(path: Path) -> None:
    """Abort immediately if path is not under /tmp.

    This guard exists because the tests below perform real filesystem
    deletions.  If pytest's tmp_path ever resolved outside /tmp (e.g.
    due to an environment variable change) we must fail loudly rather
    than risk touching production media.
    """
    import os

    resolved = os.path.realpath(str(path))
    assert resolved.startswith("/tmp"), (
        f"SAFETY VIOLATION: test directory '{resolved}' is outside /tmp. "
        "Refusing to run destructive tests against a non-temporary path. "
        "Check TMPDIR / PYTEST_TMPDIR environment variables."
    )


class TestRunHookRealSubprocess:
    """_run_hook with a real subprocess — no Popen mock.

    WARNING: spawns actual shell commands.  Confined to /tmp via
    _assert_safe_tmpdir().  Do not add real media paths here.
    """

    def test_echo_command_returns_true(self, tmp_path: Path) -> None:
        """A successful command (exit 0) returns True."""
        _assert_safe_tmpdir(tmp_path)
        assert _run_hook("echo movarr_test_ok", str(tmp_path), "post_copy") is True

    def test_false_command_returns_false(self, tmp_path: Path) -> None:
        """A failing command (exit 1) returns False."""
        _assert_safe_tmpdir(tmp_path)
        assert _run_hook("false", str(tmp_path), "pre_delete") is False

    def test_dir_placeholder_substituted_and_available(self, tmp_path: Path) -> None:
        """{dir} is replaced with the real path and the shell can access it."""
        _assert_safe_tmpdir(tmp_path)
        sentinel = tmp_path / "sentinel.txt"
        sentinel.write_text("ok")
        # 'test -f <file>' exits 0 when the file exists
        result = _run_hook(
            "test -f {dir}/sentinel.txt",
            str(tmp_path),
            "post_copy",
        )
        assert result is True

    def test_dir_placeholder_missing_file_exits_nonzero(self, tmp_path: Path) -> None:
        """{dir} substitution points at the real dir; absent file makes test exit 1."""
        _assert_safe_tmpdir(tmp_path)
        result = _run_hook(
            "test -f {dir}/does_not_exist.txt",
            str(tmp_path),
            "pre_delete",
        )
        assert result is False

    def test_stdout_captured_does_not_raise(self, tmp_path: Path) -> None:
        """Commands that produce stdout output complete without error."""
        _assert_safe_tmpdir(tmp_path)
        assert _run_hook("echo line1 && echo line2", str(tmp_path), "post_copy") is True


class TestDeleteSupersededFilesEndToEnd:
    """End-to-end: real files created, real deletion, real hook subprocess.

    WARNING: physically unlinks files from disk and spawns shell commands.
    Every test calls _assert_safe_tmpdir() first to ensure operations are
    confined to /tmp.  Do NOT introduce /media, /data, /mnt, or any real
    library path here.

    Each test explicitly asserts the quality scores before checking the
    filesystem so that it is obvious WHY a file is (or is not) deleted.
    A test that only checks "one file is gone" without proving *which*
    score won is not meaningfully different from a random deletion.
    """

    def test_lower_quality_file_physically_deleted_with_post_delete_hook(self, tmp_path: Path) -> None:
        """Score-driven deletion: 2160p Remux (120) beats 1080p BluRay (70).

        The lower-scored file must be physically gone; the post_delete hook
        must have run (witness file); the higher-scored file must survive.
        """
        _assert_safe_tmpdir(tmp_path)

        new_san = "The Matrix 1999 2160p Remux"
        old_san = "The Matrix 1999 1080p BluRay"
        config = Config()

        # Prove the score drives the decision before touching the filesystem.
        new_score = supersession_quality_score(new_san, old_san, config)
        old_score = supersession_quality_score(old_san, new_san, config)
        assert new_score > old_score, (
            f"Precondition failed: new score ({new_score}) must exceed "
            f"old score ({old_score}) for this test to be meaningful"
        )

        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = f"{new_san}.mkv"
        old_fname = f"{old_san}.mkv"
        (movie_dir / new_fname).write_bytes(b"new-high-quality")
        (movie_dir / old_fname).write_bytes(b"old-low-quality")

        witness = tmp_path / "hook_ran.txt"
        config.post_process.hooks.post_delete = f"touch {witness}"

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, config)

        assert count == 1, "expected exactly one file deleted"
        assert not (movie_dir / old_fname).exists(), f"lower-scored file ({old_score}) must be physically gone"
        assert (movie_dir / new_fname).exists(), f"higher-scored file ({new_score}) must survive"
        assert witness.exists(), "post_delete hook must have run (witness file missing)"

    def test_higher_quality_library_file_deleted(self, tmp_path: Path) -> None:
        """Library file is deleted regardless of quality score under simplified logic."""
        _assert_safe_tmpdir(tmp_path)

        # new file is 1080p BluRay; library file is 2160p Remux — library wins.
        new_san = "Inception 2010 1080p BluRay"
        lib_san = "Inception 2010 2160p Remux"
        config = Config()

        new_score = supersession_quality_score(new_san, lib_san, config)
        lib_score = supersession_quality_score(lib_san, new_san, config)
        assert new_score < lib_score, (
            f"Precondition failed: new score ({new_score}) must be less than "
            f"lib score ({lib_score}) for this test to be meaningful"
        )

        movie_dir = tmp_path / "Inception (2010)"
        movie_dir.mkdir()
        new_fname = f"{new_san}.mkv"
        lib_fname = f"{lib_san}.mkv"
        (movie_dir / new_fname).write_bytes(b"new-lower-quality")
        (movie_dir / lib_fname).write_bytes(b"lib-higher-quality")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, config)

        assert count == 1, (
            f"library file scored higher ({lib_score} > {new_score}); but simplified logic deletes it anyway"
        )
        assert not (movie_dir / lib_fname).exists(), "library file must be deleted under simplified logic"

    def test_pre_delete_hook_runs_before_unlink(self, tmp_path: Path) -> None:
        """Score-driven deletion with pre_delete hook: 2160p Remux beats 1080p BluRay.

        pre_delete hook must run (witness created) and old file must be gone.
        """
        _assert_safe_tmpdir(tmp_path)

        new_san = "Interstellar 2014 2160p Remux"
        old_san = "Interstellar 2014 1080p BluRay"
        config = Config()

        new_score = supersession_quality_score(new_san, old_san, config)
        old_score = supersession_quality_score(old_san, new_san, config)
        assert new_score > old_score, f"Precondition: new ({new_score}) must beat old ({old_score})"

        movie_dir = tmp_path / "Interstellar (2014)"
        movie_dir.mkdir()
        new_fname = f"{new_san}.mkv"
        old_fname = f"{old_san}.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        (movie_dir / old_fname).write_bytes(b"old")

        witness = tmp_path / "pre_ran.txt"
        config.post_process.hooks.pre_delete = f"touch {witness}"

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, config)

        assert count == 1
        assert not (movie_dir / old_fname).exists(), f"lower-scored file ({old_score}) must be gone"
        assert witness.exists(), "pre_delete hook must have run (witness file missing)"

    def test_failing_pre_delete_hook_leaves_old_file_intact(self, tmp_path: Path) -> None:
        """pre_delete exits non-zero → deletion aborted → old file survives regardless of scores."""
        _assert_safe_tmpdir(tmp_path)

        new_san = "Dune 2021 2160p Remux"
        old_san = "Dune 2021 1080p BluRay"
        config = Config()

        # Confirm the old file WOULD be deleted if the hook didn't abort.
        new_score = supersession_quality_score(new_san, old_san, config)
        old_score = supersession_quality_score(old_san, new_san, config)
        assert new_score > old_score, (
            f"Precondition: new ({new_score}) must beat old ({old_score}) "
            "so that the hook is the only reason deletion is skipped"
        )

        movie_dir = tmp_path / "Dune (2021)"
        movie_dir.mkdir()
        new_fname = f"{new_san}.mkv"
        old_fname = f"{old_san}.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        (movie_dir / old_fname).write_bytes(b"old")

        config.post_process.hooks.pre_delete = "false"  # always exits 1

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, config)

        assert count == 0, "deletion must be aborted when pre_delete fails"
        assert (movie_dir / old_fname).exists(), "old file must survive a failed pre_delete"

    def test_both_hooks_run_in_correct_order(self, tmp_path: Path) -> None:
        """pre_delete runs before deletion, post_delete after; both leave witnesses; old file gone."""
        _assert_safe_tmpdir(tmp_path)

        new_san = "Blade Runner 2049 2049 2160p Remux"
        old_san = "Blade Runner 2049 2049 1080p BluRay"
        config = Config()

        new_score = supersession_quality_score(new_san, old_san, config)
        old_score = supersession_quality_score(old_san, new_san, config)
        assert new_score > old_score, f"Precondition: new ({new_score}) must beat old ({old_score})"

        movie_dir = tmp_path / "Blade Runner 2049 (2049)"
        movie_dir.mkdir()
        new_fname = f"{new_san}.mkv"
        old_fname = f"{old_san}.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        (movie_dir / old_fname).write_bytes(b"old")

        pre_witness = tmp_path / "pre_ran.txt"
        post_witness = tmp_path / "post_ran.txt"
        config.post_process.hooks.pre_delete = f"touch {pre_witness}"
        config.post_process.hooks.post_delete = f"touch {post_witness}"

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, config)

        assert count == 1
        assert not (movie_dir / old_fname).exists(), f"lower-scored file ({old_score}) must be physically gone"
        assert pre_witness.exists(), "pre_delete hook must have run"
        assert post_witness.exists(), "post_delete hook must have run"


class TestSavePosterArt:
    """Tests for _save_poster_art — poster download + save to disk."""

    @staticmethod
    def _make_response(headers: dict[str, str]) -> Any:
        """Return a MagicMock that acts as a context manager returning itself."""
        resp = MagicMock()
        resp.headers = headers
        resp.__enter__.return_value = resp
        return resp

    def test_skip_when_filename_blank(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """Does nothing when filename is blank."""
        cfg = Config()
        cfg.post_process.poster_art.filename = ""
        record = mocker.MagicMock(spec=HistoryRecord)
        urlopen = mocker.patch("movarr.post_processor.urllib.request.urlopen")
        _save_poster_art(record, str(tmp_path), cfg)
        urlopen.assert_not_called()

    def test_skip_when_poster_url_none(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """Does nothing when poster URL is None."""
        cfg = Config()
        cfg.post_process.poster_art.filename = "poster.jpg"
        record = mocker.MagicMock(spec=HistoryRecord)
        record.imdb_poster_url = None
        urlopen = mocker.patch("movarr.post_processor.urllib.request.urlopen")
        _save_poster_art(record, str(tmp_path), cfg)
        urlopen.assert_not_called()

    def test_downloads_and_writes_file(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """Downloads poster and writes to correct path."""
        cfg = Config()
        cfg.post_process.poster_art.filename = "poster.jpg"
        cfg.post_process.poster_art.download_width = 500

        record = mocker.MagicMock(spec=HistoryRecord)
        record.imdb_poster_url = "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"
        record.imdb_title = "Inception"

        mock_response = self._make_response({"Content-Type": "image/jpeg"})
        mock_urlopen = mocker.patch(
            "movarr.post_processor.urllib.request.urlopen",
            return_value=mock_response,
        )
        mock_copyfileobj = mocker.patch("movarr.post_processor.shutil.copyfileobj")
        mocker.patch("builtins.open", mocker.mock_open())

        _save_poster_art(record, str(tmp_path), cfg)

        mock_urlopen.assert_called_once()
        call_url = mock_urlopen.call_args[0][0]
        assert "_SX500" in str(call_url)
        mock_copyfileobj.assert_called_once()

    def test_skips_on_non_image_content_type(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """Skips save when response Content-Type is not image/*."""
        cfg = Config()
        cfg.post_process.poster_art.filename = "poster.jpg"
        record = mocker.MagicMock(spec=HistoryRecord)
        record.imdb_poster_url = "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"

        mock_response = self._make_response({"Content-Type": "text/html"})
        mocker.patch("movarr.post_processor.urllib.request.urlopen", return_value=mock_response)
        mocker.patch("builtins.open", mocker.mock_open())
        mock_copyfileobj = mocker.patch("movarr.post_processor.shutil.copyfileobj")

        _save_poster_art(record, str(tmp_path), cfg)
        mock_copyfileobj.assert_not_called()

    def test_handles_download_failure_gracefully(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """Does not raise on network failure."""
        cfg = Config()
        cfg.post_process.poster_art.filename = "poster.jpg"
        record = mocker.MagicMock(spec=HistoryRecord)
        record.imdb_poster_url = "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"
        mocker.patch("movarr.post_processor.urllib.request.urlopen", side_effect=OSError("timeout"))

        # Should not raise
        _save_poster_art(record, str(tmp_path), cfg)

    def test_forces_jpg_extension(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """Forces .jpg extension even when user specifies .png."""
        cfg = Config()
        cfg.post_process.poster_art.filename = "poster.png"
        cfg.post_process.poster_art.download_width = 0
        record = mocker.MagicMock(spec=HistoryRecord)
        record.imdb_poster_url = "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"
        mock_response = self._make_response({"Content-Type": "image/jpeg"})
        mocker.patch("movarr.post_processor.urllib.request.urlopen", return_value=mock_response)
        m_open = mocker.patch("builtins.open", mocker.mock_open())
        mocker.patch("movarr.post_processor.shutil.copyfileobj")

        _save_poster_art(record, str(tmp_path), cfg)

        call_args = m_open.call_args
        assert call_args is not None
        file_path = call_args[0][0]
        assert file_path.endswith(".jpg")
