"""Unit tests for movarr.file_utils."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

from movarr.file_utils import (
    copy_with_verify,
    delete_file,
    make_directory,
    resolution_label_from_height,
    walk_library,
)


class TestWalkLibrary:
    """Tests for walk_library()."""

    def test_yields_root_entry_for_empty_directory(self, tmp_path: Path) -> None:
        results = list(walk_library([str(tmp_path)]))
        roots = [r for r, _, _ in results]
        assert str(tmp_path) in roots

    def test_yields_files_in_subdirectory(self, tmp_path: Path) -> None:
        sub = tmp_path / "Movie 2020"
        sub.mkdir()
        (sub / "movie.mkv").write_text("data")
        results = list(walk_library([str(tmp_path)]))
        all_files = [f for _, _, files in results for f in files]
        assert "movie.mkv" in all_files

    def test_multiple_paths_yields_files_from_each(self, tmp_path: Path) -> None:
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "file_a.mkv").write_text("a")
        (dir_b / "file_b.mkv").write_text("b")
        results = list(walk_library([str(dir_a), str(dir_b)]))
        all_files = [f for _, _, files in results for f in files]
        assert "file_a.mkv" in all_files
        assert "file_b.mkv" in all_files

    def test_empty_path_list_returns_no_entries(self) -> None:
        results = list(walk_library([]))
        assert results == []


class TestMakeDirectory:
    """Tests for make_directory()."""

    def test_creates_nested_directories(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "c"
        assert not target.exists()
        result = make_directory(target)
        assert result is True
        assert target.is_dir()

    def test_returns_true_when_already_exists(self, tmp_path: Path) -> None:
        target = tmp_path / "existing"
        target.mkdir()
        result = make_directory(target)
        assert result is True

    def test_returns_false_on_permission_error(self, tmp_path: Path, mocker: MockerFixture) -> None:
        mocker.patch("pathlib.Path.mkdir", side_effect=PermissionError("denied"))
        result = make_directory(tmp_path / "new_dir")
        assert result is False

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        target = str(tmp_path / "string_path")
        result = make_directory(target)
        assert result is True
        assert Path(target).is_dir()

    def test_returns_false_on_os_error(self, tmp_path: Path, mocker: MockerFixture) -> None:
        mocker.patch("pathlib.Path.mkdir", side_effect=OSError("filesystem error"))
        result = make_directory(tmp_path / "new_dir")
        assert result is False


class TestDeleteFile:
    """Tests for delete_file()."""

    def test_deletes_existing_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = delete_file(f)
        assert result is True
        assert not f.exists()

    def test_returns_true_when_file_absent(self, tmp_path: Path) -> None:
        f = tmp_path / "nonexistent.txt"
        result = delete_file(f)
        assert result is True

    def test_returns_false_on_permission_error(self, tmp_path: Path, mocker: MockerFixture) -> None:
        f = tmp_path / "locked.txt"
        f.write_text("locked")
        mocker.patch("pathlib.Path.unlink", side_effect=PermissionError("denied"))
        result = delete_file(f)
        assert result is False

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("content")
        result = delete_file(str(f))
        assert result is True
        assert not f.exists()

    def test_returns_false_on_os_error(self, tmp_path: Path, mocker: MockerFixture) -> None:
        f = tmp_path / "err.txt"
        f.write_text("data")
        mocker.patch("pathlib.Path.unlink", side_effect=OSError("io error"))
        result = delete_file(f)
        assert result is False

    def test_returns_false_on_is_a_directory_error(self, tmp_path: Path, mocker: MockerFixture) -> None:
        f = tmp_path / "notafile.txt"
        f.write_text("data")
        mocker.patch("pathlib.Path.unlink", side_effect=IsADirectoryError("is a directory"))
        result = delete_file(f)
        assert result is False


class TestCopyWithVerify:
    """Tests for copy_with_verify()."""

    def test_copies_file_successfully(self, tmp_path: Path) -> None:
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst" / "dst.mkv"
        src.write_bytes(b"video data")
        result = copy_with_verify(src, dst)
        assert result is True
        assert dst.is_file()
        assert dst.read_bytes() == b"video data"

    def test_skips_copy_when_dst_already_matches(self, tmp_path: Path, mocker: MockerFixture) -> None:
        content = b"identical content"
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(content)
        dst.write_bytes(content)
        spy = mocker.patch("movarr.file_utils._do_copy")
        result = copy_with_verify(src, dst)
        assert result is True
        spy.assert_not_called()

    def test_recopies_when_dst_checksum_mismatches(self, tmp_path: Path) -> None:
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source content")
        dst.write_bytes(b"different content")
        result = copy_with_verify(src, dst)
        assert result is True
        assert dst.read_bytes() == b"source content"

    def test_returns_false_on_missing_source(self, tmp_path: Path) -> None:
        src = tmp_path / "missing.mkv"
        dst = tmp_path / "dst.mkv"
        result = copy_with_verify(src, dst)
        assert result is False

    def test_returns_false_on_post_copy_checksum_mismatch(self, tmp_path: Path, mocker: MockerFixture) -> None:
        src = tmp_path / "src.mkv"
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        dst = dst_dir / "dst.mkv"
        src.write_bytes(b"data")
        # Patch _sha256 to return mismatched digests after copy
        mocker.patch("movarr.file_utils._sha256", side_effect=["aaa111aaa111", "bbb222bbb222"])
        result = copy_with_verify(src, dst)
        assert result is False

    def test_returns_false_when_directory_creation_fails(self, tmp_path: Path, mocker: MockerFixture) -> None:
        src = tmp_path / "src.mkv"
        src.write_bytes(b"data")
        dst = tmp_path / "newdir" / "dst.mkv"
        mocker.patch("movarr.file_utils.make_directory", return_value=False)
        result = copy_with_verify(src, dst)
        assert result is False

    def test_creates_destination_parent_directory(self, tmp_path: Path) -> None:
        src = tmp_path / "src.mkv"
        dst = tmp_path / "nested" / "deep" / "dst.mkv"
        src.write_bytes(b"content")
        result = copy_with_verify(src, dst)
        assert result is True
        assert dst.parent.is_dir()

    def test_accepts_existing_dst_when_delete_of_mismatched_dst_fails(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """When dst exists with wrong checksum but delete_file fails (e.g. immutable file),
        accept existing destination to prevent infinite loop in post-processing."""
        content_src = b"source data"
        content_dst = b"different data"
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(content_src)
        dst.write_bytes(content_dst)
        # delete_file fails (simulating EPERM on immutable file)
        mocker.patch("movarr.file_utils.delete_file", return_value=False)
        # chattr -i also fails (simulating missing LINUX_IMMUTABLE capability)
        mocker.patch("movarr.file_utils.subprocess.run", side_effect=FileNotFoundError("chattr not found"))
        result = copy_with_verify(src, dst)
        assert result is True
        # Destination file should remain untouched
        assert dst.read_bytes() == b"different data"

    def test_retries_delete_after_chattr_when_delete_of_mismatched_dst_fails(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """When delete_file fails, try chattr -i first, then retry delete.
        If chattr succeeds and retry delete succeeds, re-copy proceeds."""
        content_src = b"source data"
        content_dst = b"different data"
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(content_src)
        dst.write_bytes(content_dst)
        # delete_file fails first time (immutable), succeeds after chattr
        delete_calls: list[int] = [0]

        def fake_delete(_path: str | Path) -> bool:
            delete_calls[0] += 1
            return delete_calls[0] > 1  # first call fails, subsequent succeed

        mocker.patch("movarr.file_utils.delete_file", side_effect=fake_delete)
        # chattr succeeds
        mock_run = mocker.patch("movarr.file_utils.subprocess.run")
        mock_run.return_value.returncode = 0
        result = copy_with_verify(src, dst)
        assert result is True
        # File was re-copied with source content
        assert dst.read_bytes() == b"source data"
        assert delete_calls[0] == 2  # first delete + retry after chattr

    def test_chattr_succeeds_but_retry_delete_still_fails_accepts_existing(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """When chattr -i succeeds but the retry delete still fails, accept existing."""
        content_src = b"source data"
        content_dst = b"different data"
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(content_src)
        dst.write_bytes(content_dst)
        # delete_file always fails (even after chattr)
        mocker.patch("movarr.file_utils.delete_file", return_value=False)
        # chattr succeeds (returncode 0)
        mock_run = mocker.patch("movarr.file_utils.subprocess.run")
        mock_run.return_value.returncode = 0
        result = copy_with_verify(src, dst)
        assert result is True
        # Destination file should remain untouched
        assert dst.read_bytes() == b"different data"

    def test_chattr_returns_nonzero_exit_still_accepts_existing(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """When chattr -i runs but returns non-zero, log stderr and accept existing."""
        content_src = b"source data"
        content_dst = b"different data"
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(content_src)
        dst.write_bytes(content_dst)
        # delete_file fails
        mocker.patch("movarr.file_utils.delete_file", return_value=False)
        # chattr runs but returns non-zero (e.g., EPERM, missing capability)
        mock_run = mocker.patch("movarr.file_utils.subprocess.run")
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = b"Operation not permitted\n"
        result = copy_with_verify(src, dst)
        assert result is True
        # Destination file should remain untouched
        assert dst.read_bytes() == b"different data"

    def test_returns_false_on_permission_error_during_copy(self, tmp_path: Path, mocker: MockerFixture) -> None:
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst" / "dst.mkv"
        src.write_bytes(b"data")
        mocker.patch("movarr.file_utils._do_copy", side_effect=PermissionError("denied"))
        result = copy_with_verify(src, dst)
        assert result is False

    def test_returns_false_on_os_error_during_copy(self, tmp_path: Path, mocker: MockerFixture) -> None:
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst" / "dst.mkv"
        src.write_bytes(b"data")
        mocker.patch("movarr.file_utils._do_copy", side_effect=OSError("disk full"))
        result = copy_with_verify(src, dst)
        assert result is False

    def test_logs_checksum_verification_in_progress(self, tmp_path: Path) -> None:
        """INFO log is emitted before post-copy SHA-256 so user knows it's not stuck."""
        from loguru import logger as _loguru_logger

        records: list = []
        sink_id = _loguru_logger.add(lambda m: records.append(m.record), level=0)
        try:
            src = tmp_path / "src.mkv"
            dst = tmp_path / "dst" / "dst.mkv"
            src.write_bytes(b"video data")
            copy_with_verify(src, dst)
        finally:
            _loguru_logger.remove(sink_id)
        assert any("erifying" in r["message"] for r in records)

    def test_logs_pre_copy_checksum_check_when_dst_exists(self, tmp_path: Path) -> None:
        """INFO log is emitted before pre-copy SHA-256 when destination already exists."""
        from loguru import logger as _loguru_logger

        records: list = []
        sink_id = _loguru_logger.add(lambda m: records.append(m.record), level=0)
        try:
            content = b"identical content"
            src = tmp_path / "src.mkv"
            dst = tmp_path / "dst.mkv"
            src.write_bytes(content)
            dst.write_bytes(content)
            copy_with_verify(src, dst)
        finally:
            _loguru_logger.remove(sink_id)
        assert any("erifying" in r["message"] for r in records)

    def test_returns_false_when_src_sha256_raises_during_dst_verification(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """Source disappears between listing and verification; must return False not raise."""
        content = b"same content"
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(content)
        dst.write_bytes(content)
        mocker.patch("movarr.file_utils._sha256", side_effect=FileNotFoundError("gone"))
        result = copy_with_verify(src, dst)
        assert result is False

    def test_returns_false_when_src_disappears_after_copy(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """Source disappears between copy and post-copy sha256; must return False."""
        src = tmp_path / "src.mkv"
        src.write_bytes(b"data")
        dst = tmp_path / "dst.mkv"

        import movarr.file_utils as fu

        original_sha = fu._sha256
        call_count: list[int] = [0]

        def fake_sha(p: Path, label: str | None = None) -> str:
            call_count[0] += 1
            if call_count[0] == 1:  # first call is src post-copy verification
                raise OSError("gone")
            return original_sha(p)

        mocker.patch("movarr.file_utils._sha256", side_effect=fake_sha)
        mocker.patch("movarr.file_utils._do_copy")
        result = fu.copy_with_verify(src, dst)
        assert result is False

    def test_logs_copy_progress_at_milestones(self, tmp_path: Path) -> None:
        """Copy emits '25% complete', '50% complete', '75% complete' progress lines."""
        from loguru import logger as _loguru_logger

        records: list = []
        sink_id = _loguru_logger.add(lambda m: records.append(m.record), level=0)
        try:
            src = tmp_path / "src.mkv"
            dst = tmp_path / "dst" / "dst.mkv"
            # ~256 KB to span multiple 64 KB chunks
            src.write_bytes(b"\x00" * 262144)
            copy_with_verify(src, dst)
        finally:
            _loguru_logger.remove(sink_id)
        messages = [r["message"] for r in records]
        assert any("25% complete" in m for m in messages)
        assert any("50% complete" in m for m in messages)
        assert any("75% complete" in m for m in messages)

    def test_logs_sha_progress_at_milestones(self, tmp_path: Path) -> None:
        """Post-copy SHA-256 emits '25% complete', '50% complete', '75% complete' lines."""
        from loguru import logger as _loguru_logger

        records: list = []
        sink_id = _loguru_logger.add(lambda m: records.append(m.record), level=0)
        try:
            src = tmp_path / "src.mkv"
            dst = tmp_path / "dst" / "dst.mkv"
            src.write_bytes(b"\x00" * 262144)
            copy_with_verify(src, dst)
        finally:
            _loguru_logger.remove(sink_id)
        messages = [r["message"] for r in records]
        # SHA progress lines include "Verifying"
        sha_msgs = [m for m in messages if "Verifying" in m and "copy integrity" in m]
        assert any("25% complete" in m for m in sha_msgs)
        assert any("50% complete" in m for m in sha_msgs)
        assert any("75% complete" in m for m in sha_msgs)

    def test_progress_skipped_for_empty_file(self, tmp_path: Path) -> None:
        """Empty files skip progress logging (division by zero guard)."""
        from loguru import logger as _loguru_logger

        records: list = []
        sink_id = _loguru_logger.add(lambda m: records.append(m.record), level=0)
        try:
            src = tmp_path / "src.mkv"
            dst = tmp_path / "dst" / "dst.mkv"
            src.write_bytes(b"")
            result = copy_with_verify(src, dst)
            assert result is True
        finally:
            _loguru_logger.remove(sink_id)
        messages = [r["message"] for r in records]
        assert not any("% complete" in m for m in messages)


class TestResolutionLabelFromHeight:
    """Tests for resolution_label_from_height()."""

    def test_2160_returns_uhd(self) -> None:
        assert resolution_label_from_height("2160") == "UHD"

    def test_1080_returns_hd(self) -> None:
        assert resolution_label_from_height("1080") == "HD"

    def test_720_returns_hd(self) -> None:
        assert resolution_label_from_height("720") == "HD"

    def test_none_returns_hd(self) -> None:
        assert resolution_label_from_height(None) == "HD"

    def test_480_returns_hd(self) -> None:
        assert resolution_label_from_height("480") == "HD"

    def test_arbitrary_string_returns_hd(self) -> None:
        assert resolution_label_from_height("unknown") == "HD"
