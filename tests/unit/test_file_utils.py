"""Unit tests for movarr.file_utils."""

from __future__ import annotations

import contextlib
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from loguru import logger as _logger

from movarr import file_utils

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pytest_mock import MockerFixture

from movarr.file_utils import (
    copy_with_verify,
    delete_file,
    make_directory,
    walk_library,
)


@contextlib.contextmanager
def _captured_logs() -> Iterator[list[Any]]:
    """Collect loguru records emitted inside the with-block."""
    records: list[Any] = []
    sink_id = _logger.add(lambda message: records.append(message.record), level=0)
    try:
        yield records
    finally:
        _logger.remove(sink_id)


def _messages(records: list[Any], level: str) -> list[str]:
    """Return the messages of *records* whose level name is *level*."""
    return [record["message"] for record in records if record["level"].name == level]


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

    def test_keeps_existing_dst_when_replacement_cannot_be_swapped(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """A destination that cannot be replaced must not be destroyed or reported as success."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.write_bytes(b"different data")
        # The swap fails even after the immutable-flag retry.
        mocker.patch("movarr.file_utils.os.replace", side_effect=PermissionError("immutable"))
        mocker.patch("movarr.file_utils._try_clear_immutable")

        assert copy_with_verify(src, dst) is False
        assert dst.read_bytes() == b"different data"
        assert not list(tmp_path.glob(".dst.mkv.*.part"))

    def test_failed_copy_leaves_previous_destination_intact(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """A copy failure must not remove the file it was going to replace."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.write_bytes(b"different data")
        mocker.patch("movarr.file_utils._perform_copy", return_value=None)

        assert copy_with_verify(src, dst) is False
        assert dst.read_bytes() == b"different data"

    def test_replacement_clears_immutable_flag_and_retries(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """A PermissionError on the swap triggers one chattr -i retry."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.write_bytes(b"different data")
        real_replace = os.replace
        calls = {"count": 0}

        def _replace_first_call_fails(from_path: str, to_path: str) -> None:
            calls["count"] += 1
            if calls["count"] == 1:
                raise PermissionError("immutable")
            real_replace(from_path, to_path)

        replace = mocker.patch("movarr.file_utils.os.replace", side_effect=_replace_first_call_fails)
        clear = mocker.patch("movarr.file_utils._try_clear_immutable")

        assert copy_with_verify(src, dst) is True
        clear.assert_called_once()
        assert replace.call_count == 2

    def test_returns_false_when_swap_does_not_change_destination(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """A rename that reports success without swapping the file must fail the copy.

        On Unraid user shares (shfs) a rename can land on a different disk while
        the share keeps resolving a same-named duplicate, so the destination still
        holds the old content after the swap "succeeds".
        """
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.write_bytes(b"different data")
        mocker.patch("movarr.file_utils.os.replace")

        with _captured_logs() as records:
            result = copy_with_verify(src, dst)

        assert result is False
        assert dst.read_bytes() == b"different data"
        assert any("did not take effect" in message for message in _messages(records, "ERROR"))

    def test_returns_false_when_swap_leaves_destination_missing(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """A swap that loses the destination must report it as missing, not as a mismatch."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.write_bytes(b"different data")

        def _replace_loses_destination(from_path: str, to_path: str) -> None:
            os.unlink(to_path)

        mocker.patch("movarr.file_utils.os.replace", side_effect=_replace_loses_destination)

        with _captured_logs() as records:
            result = copy_with_verify(src, dst)

        assert result is False
        assert any("is missing after the swap" in message for message in _messages(records, "ERROR"))

    def test_replaces_same_size_destination_with_different_content(self, tmp_path: Path) -> None:
        """Content, not size, decides whether the destination is up to date."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.write_bytes(b"broken data")
        assert len(src.read_bytes()) == len(dst.read_bytes())

        assert copy_with_verify(src, dst) is True

        assert dst.read_bytes() == b"source data"

    def test_returns_false_when_same_size_destination_is_not_swapped(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """A silent no-op swap of a same-sized destination is still detected."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.write_bytes(b"broken data")
        mocker.patch("movarr.file_utils.os.replace")

        with _captured_logs() as records:
            result = copy_with_verify(src, dst)

        assert result is False
        assert any("did not take effect" in message for message in _messages(records, "ERROR"))

    def test_logs_disk_path_hint_when_file_flags_are_unsupported(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """chattr failing on a FUSE/shfs mount must produce an actionable warning."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.write_bytes(b"different data")
        mocker.patch("movarr.file_utils.os.replace", side_effect=PermissionError("immutable"))
        mocker.patch(
            "movarr.file_utils.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["chattr", "-i", str(dst)],
                returncode=1,
                stdout=b"",
                stderr=b"chattr: Operation not supported while reading flags on /media/x.mkv",
            ),
        )

        with _captured_logs() as records:
            result = copy_with_verify(src, dst)

        assert result is False
        warnings = _messages(records, "WARNING")
        assert any("disk path" in message for message in warnings)
        assert any("pre_copy" in message for message in warnings)

    def test_logs_ioctl_hint_for_other_flag_ioctl_errors(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """ENOTTY (cifs-style) also names the disk-path remedy instead of staying at DEBUG."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.write_bytes(b"different data")
        mocker.patch("movarr.file_utils.os.replace", side_effect=PermissionError("immutable"))
        mocker.patch(
            "movarr.file_utils.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["chattr", "-i", str(dst)],
                returncode=1,
                stdout=b"",
                stderr=b"chattr: Inappropriate ioctl for device while reading flags on /media/x.mkv",
            ),
        )

        with _captured_logs() as records:
            copy_with_verify(src, dst)

        assert any("disk path" in message for message in _messages(records, "WARNING"))

    def test_logs_disk_path_hint_for_invalid_flag_requests(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """EINVAL from the flag ioctl gets the same disk-path remedy as ENOTSUP."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.write_bytes(b"different data")
        mocker.patch("movarr.file_utils.os.replace", side_effect=PermissionError("immutable"))
        mocker.patch(
            "movarr.file_utils.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["chattr", "-i", str(dst)],
                returncode=1,
                stdout=b"",
                stderr=b"chattr: Invalid argument while setting flags on /media/x.mkv",
            ),
        )

        with _captured_logs() as records:
            copy_with_verify(src, dst)

        assert any("disk path" in message for message in _messages(records, "WARNING"))

    def test_logs_warning_when_flag_cannot_be_cleared_on_read_only_mount(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """A read-only mount gets its own warning instead of a DEBUG line."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.write_bytes(b"different data")
        mocker.patch("movarr.file_utils.os.replace", side_effect=PermissionError("immutable"))
        mocker.patch(
            "movarr.file_utils.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["chattr", "-i", str(dst)],
                returncode=1,
                stdout=b"",
                stderr=b"chattr: Read-only file system while setting flags on /media/x.mkv",
            ),
        )

        with _captured_logs() as records:
            copy_with_verify(src, dst)

        assert any("read-only" in message for message in _messages(records, "WARNING"))

    def test_logs_capability_hint_when_chattr_is_not_permitted(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """EPERM from chattr points at CAP_LINUX_IMMUTABLE."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.write_bytes(b"different data")
        mocker.patch("movarr.file_utils.os.replace", side_effect=PermissionError("immutable"))
        mocker.patch(
            "movarr.file_utils.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["chattr", "-i", str(dst)],
                returncode=1,
                stdout=b"",
                stderr=b"chattr: Operation not permitted while setting flags on /media/x.mkv",
            ),
        )

        with _captured_logs() as records:
            copy_with_verify(src, dst)

        assert any("LINUX_IMMUTABLE" in message for message in _messages(records, "WARNING"))

    def test_keeps_chattr_failures_without_a_known_remedy_at_debug_level(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """An unrecognised chattr failure must not raise a misleading warning."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.write_bytes(b"different data")
        mocker.patch("movarr.file_utils.os.replace", side_effect=PermissionError("immutable"))
        mocker.patch(
            "movarr.file_utils.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["chattr", "-i", str(dst)],
                returncode=1,
                stdout=b"",
                stderr=b"chattr: No such file or directory while trying to open /media/x.mkv",
            ),
        )

        with _captured_logs() as records:
            copy_with_verify(src, dst)

        assert not [message for message in _messages(records, "WARNING") if "immutable flag" in message]

    def test_reports_vanished_staging_file_when_retry_swap_fails(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """A retry that hits ENOENT must explain that the staged copy was moved away."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.write_bytes(b"different data")
        mocker.patch(
            "movarr.file_utils.os.replace",
            side_effect=[PermissionError("immutable"), FileNotFoundError("gone")],
        )
        mocker.patch("movarr.file_utils._try_clear_immutable")

        with _captured_logs() as records:
            result = copy_with_verify(src, dst)

        assert result is False
        assert dst.read_bytes() == b"different data"
        assert any("disappeared while swapping in" in message for message in _messages(records, "ERROR"))
        assert any("gone" in message for message in _messages(records, "ERROR"))

    def test_removes_stale_staging_file_from_interrupted_run(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """Leftovers of an earlier interrupted run for the same destination are removed."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.write_bytes(b"different data")
        stale = tmp_path / ".dst.mkv.movarr-deadbeef.part"
        stale.write_bytes(b"leftover")
        # Push the clock past the minimum age so the leftover counts as abandoned.
        mocker.patch("movarr.file_utils.time.time", return_value=time.time() + 2 * 24 * 60 * 60)

        assert copy_with_verify(src, dst) is True

        assert not stale.exists()

    def test_keeps_recent_staging_file_of_a_running_copy(self, tmp_path: Path) -> None:
        """A staging file young enough to belong to a live copy must be preserved."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.write_bytes(b"different data")
        in_flight = tmp_path / ".dst.mkv.movarr-feedface.part"
        in_flight.write_bytes(b"still copying")

        assert copy_with_verify(src, dst) is True

        assert in_flight.exists()

    def test_leaves_unrelated_part_files_alone(self, tmp_path: Path) -> None:
        """Files that merely look similar must never be deleted."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.write_bytes(b"different data")
        keep = [
            tmp_path / "dst.mkv.part",
            tmp_path / ".dst.mkv.20230101.part",
            tmp_path / ".dst.mkv.movarr-notauuid.part",
            tmp_path / ".other.mkv.movarr-deadbeef.part",
            tmp_path / ".dst.mkv.movarr-DEADBEEF.part",
            tmp_path / ".dst.mkv.movarr-deadbeef12.part",
        ]
        for path in keep:
            path.write_bytes(b"keep me")

        assert copy_with_verify(src, dst) is True

        assert all(path.exists() for path in keep)

    def test_skips_post_swap_verification_for_a_new_destination(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """A brand-new destination cannot be shadowed, so the payload is not re-read."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        confirm = mocker.patch("movarr.file_utils._confirm_swap")

        assert copy_with_verify(src, dst) is True

        confirm.assert_not_called()
        assert dst.read_bytes() == b"source data"

    def test_returns_false_when_destination_cannot_be_read_after_swap(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """A post-swap read error must be a clean failure, not an exception."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.write_bytes(b"different data")
        original = file_utils._sha256

        def _sha_after_swap_fails(path: Path, label: str | None = None) -> str:
            if label and "after the swap" in label:
                raise PermissionError("io error")
            return original(Path(path), label)

        mocker.patch("movarr.file_utils._sha256", side_effect=_sha_after_swap_fails)

        with _captured_logs() as records:
            result = copy_with_verify(src, dst)

        assert result is False
        assert any("Could not verify" in message for message in _messages(records, "ERROR"))

    def test_copies_when_staging_scan_fails(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """An unreadable destination directory must not abort the copy."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.write_bytes(b"different data")
        mocker.patch("pathlib.Path.iterdir", side_effect=OSError("scan failed"))

        with _captured_logs() as records:
            result = copy_with_verify(src, dst)

        assert result is True
        assert any("Could not scan" in message for message in _messages(records, "DEBUG"))

        assert dst.read_bytes() == b"source data"

    def test_logs_warning_when_stale_staging_file_cannot_be_removed(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """An undeletable leftover must be reported, not silently ignored."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.write_bytes(b"different data")
        stale = tmp_path / ".dst.mkv.movarr-deadbeef.part"
        stale.write_bytes(b"leftover")
        mocker.patch("movarr.file_utils.time.time", return_value=time.time() + 3 * 60 * 60)
        mocker.patch("pathlib.Path.unlink", side_effect=PermissionError("immutable"))

        with _captured_logs() as records:
            result = copy_with_verify(src, dst)

        assert result is True
        assert any("Could not remove stale staging file" in message for message in _messages(records, "WARNING"))

    def test_staging_name_is_recognised_by_the_cleanup_matcher(self, tmp_path: Path) -> None:
        """The creator and the matcher must not drift apart."""
        dst = tmp_path / "dst.mkv"

        created = tmp_path / file_utils._staging_name(dst)

        assert file_utils._is_stale_staging(created, dst)
        assert not file_utils._is_stale_staging(tmp_path / f".{dst.name}.20230101.part", dst)

    def test_copies_over_a_dangling_symlink_destination(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """A dangling symlink destination is treated as pre-existing and confirmed."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.symlink_to(tmp_path / "missing-target.mkv")
        confirm = mocker.patch("movarr.file_utils._confirm_swap", wraps=file_utils._confirm_swap)

        assert copy_with_verify(src, dst) is True

        assert confirm.call_count == 1
        assert dst.read_bytes() == b"source data"

    def test_keeps_staging_file_with_unknown_change_time(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """A change time of 0 means unknown, so the leftover is left alone."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.write_bytes(b"different data")
        candidate = mocker.MagicMock()
        candidate.name = ".dst.mkv.movarr-feedface.part"
        candidate.stat.return_value = SimpleNamespace(st_ctime=0)
        mocker.patch("pathlib.Path.iterdir", return_value=[candidate])

        with _captured_logs() as records:
            assert copy_with_verify(src, dst) is True

        assert any("unknown change time" in message for message in _messages(records, "DEBUG"))
        candidate.unlink.assert_not_called()

    def test_mismatched_destination_is_replaced_atomically(self, tmp_path: Path) -> None:
        """The swapped-in file holds the verified source content."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        dst.write_bytes(b"different data")

        assert copy_with_verify(src, dst) is True
        assert dst.read_bytes() == b"source data"
        assert not list(tmp_path.glob(".dst.mkv.*.part"))

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


class TestUnreadableDestinationRecovery:
    """A destination that becomes unreadable must be re-copied, not raised."""

    def test_verify_existing_falls_through_when_dst_unreadable(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """An OSError while hashing an existing destination triggers a re-copy."""
        import hashlib

        from movarr.file_utils import copy_with_verify

        src = tmp_path / "src.mkv"
        src.write_bytes(b"payload")
        dst = tmp_path / "dst.mkv"
        dst.write_bytes(b"stale")

        real_sha = hashlib.sha256
        state = {"raised": False}

        def fake_sha(path: Path, label: str | None = None) -> str:
            if Path(path) == dst and not state["raised"]:
                state["raised"] = True
                raise OSError("destination vanished")
            return real_sha(Path(path).read_bytes()).hexdigest()

        mocker.patch("movarr.file_utils._sha256", side_effect=fake_sha)

        assert copy_with_verify(src, dst) is True
        assert dst.read_bytes() == b"payload"
        assert state["raised"] is True


class TestPostCopyVerificationFailure:
    """A destination hash that fails after the copy returns False, not raise."""

    def test_post_copy_dst_hash_oserror_returns_false(self, tmp_path: Path, mocker: MockerFixture) -> None:
        from movarr.file_utils import copy_with_verify

        src = tmp_path / "src.mkv"
        src.write_bytes(b"payload")
        dst = tmp_path / "dst.mkv"

        import hashlib

        real_sha = hashlib.sha256
        state = {"n": 0}

        def fake_sha(path: Path, label: str | None = None) -> str:
            state["n"] += 1
            # dst does not pre-exist, so call 1 = src, call 2 = dst (post-copy).
            if state["n"] >= 2:
                raise OSError("destination gone")
            return real_sha(Path(path).read_bytes()).hexdigest()

        mocker.patch("movarr.file_utils._sha256", side_effect=fake_sha)

        assert copy_with_verify(src, dst) is False


class TestWalkLibraryIsReusable:
    """walk_library returns a list, so a second pass still sees every entry."""

    def test_second_pass_sees_the_same_entries(self, tmp_path: Path) -> None:
        from movarr.file_utils import walk_library

        (tmp_path / "Movie (2020)").mkdir()
        (tmp_path / "Movie (2020)" / "Movie 2020 1080p.mkv").write_bytes(b"x")

        entries = walk_library([str(tmp_path)])
        first = [root for root, _dirs, _files in entries]
        second = [root for root, _dirs, _files in entries]

        assert first == second
        assert len(first) >= 1


class TestDeleteFileDirectory:
    """A directory must never be reported as a deleted file."""

    def test_directory_returns_false(self, tmp_path: Path) -> None:
        from movarr.file_utils import delete_file

        target = tmp_path / "movie.mkv"
        target.mkdir()

        assert delete_file(target) is False
        assert target.is_dir()


class TestImmutableFlagRetry:
    """chattr failures never raise: recognised causes warn, the rest stay at debug."""

    def test_chattr_oserror_is_swallowed(self, mocker: MockerFixture) -> None:
        from movarr.file_utils import _try_clear_immutable

        mocker.patch("movarr.file_utils.subprocess.run", side_effect=FileNotFoundError("no chattr"))

        _try_clear_immutable(Path("/tmp/x"))  # must not raise

    def test_nonzero_return_is_logged(self, mocker: MockerFixture) -> None:
        from movarr.file_utils import _try_clear_immutable

        result = mocker.MagicMock(returncode=1, stderr=b"nope")
        mocker.patch("movarr.file_utils.subprocess.run", return_value=result)

        with _captured_logs() as records:
            _try_clear_immutable(Path("/tmp/x"))  # must not raise

        assert any("chattr -i failed (rc=1)" in message for message in _messages(records, "DEBUG"))
