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
        candidate = tmp_path / ".dst.mkv.movarr-feedface.part"
        candidate.write_bytes(b"leftover")
        # A real file (the cleanup opens a descriptor to probe the lock); only the
        # candidate reports an unknown change time.
        real_stat = Path.stat

        def _stat(self: Path) -> object:
            if self.name == candidate.name:
                return SimpleNamespace(st_ctime=0)
            return real_stat(self)

        mocker.patch.object(Path, "stat", _stat)

        with _captured_logs() as records:
            assert copy_with_verify(src, dst) is True

        assert any("unknown change time" in message for message in _messages(records, "DEBUG"))
        assert candidate.exists(), "an unknown change time must keep the leftover"

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

    def test_chattr_success_returns_without_a_warning(self, mocker: MockerFixture) -> None:
        """A successful chattr must stay silent."""
        from movarr.file_utils import _try_clear_immutable

        result = mocker.MagicMock(returncode=0, stderr=b"")
        mocker.patch("movarr.file_utils.subprocess.run", return_value=result)

        with _captured_logs() as records:
            _try_clear_immutable(Path("/tmp/x"))

        assert not _messages(records, "WARNING")


class TestReclaimStagingFile:
    """_reclaim_staging_file removes only demonstrably abandoned staging files."""

    def test_missing_candidate_is_ignored(self, tmp_path: Path) -> None:
        """A candidate that is already gone is not an error and is not counted as reclaimed."""
        candidate = tmp_path / ".dst.mkv.movarr-deadbeef.part"

        assert file_utils._reclaim_staging_file(candidate, cutoff=0.0) is False

    def test_unreadable_staging_file_is_kept_and_reported(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A stat failure keeps the leftover and explains why."""
        candidate = tmp_path / ".dst.mkv.movarr-feedface.part"
        candidate.write_bytes(b"leftover")
        real_stat = Path.stat

        def _stat(self: Path) -> object:
            if self.name == candidate.name:
                raise OSError("io error")
            return real_stat(self)

        mocker.patch.object(Path, "stat", _stat)

        with _captured_logs() as records:
            file_utils._reclaim_staging_file(candidate, cutoff=0.0)

        assert candidate.exists(), "a stat failure must keep the leftover"
        assert any("Could not inspect staging file" in message for message in _messages(records, "DEBUG"))

    def test_staging_file_vanishing_before_unlink_is_ignored(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """A concurrent removal must not raise."""
        candidate = tmp_path / ".dst.mkv.movarr-deadbeef.part"
        candidate.write_bytes(b"leftover")
        mocker.patch("pathlib.Path.unlink", side_effect=FileNotFoundError("gone"))

        file_utils._reclaim_staging_file(candidate, cutoff=time.time() + 60)  # must not raise


class TestStagingNameLength:
    """The staging name must always be a valid, matchable file name."""

    def test_staging_name_fits_name_max_for_long_destinations(self, tmp_path: Path) -> None:
        """A long destination name must not push the staging name past NAME_MAX."""
        dst = tmp_path / (("x" * 233) + ".mkv")

        name = file_utils._staging_name(dst)

        assert len(name.encode()) <= 255, f"staging name is {len(name.encode())} bytes"
        assert file_utils._is_stale_staging(tmp_path / name, dst)

    def test_staging_name_fits_name_max_for_multibyte_destinations(self, tmp_path: Path) -> None:
        """NAME_MAX counts bytes: a multibyte destination name must use the hash stem."""
        dst = tmp_path / ("君" * 79 + ".mkv")  # 241 bytes, 83 characters

        name = file_utils._staging_name(dst)

        assert len(name.encode()) <= 255, f"staging name is {len(name.encode())} bytes"
        assert file_utils._is_stale_staging(tmp_path / name, dst)

    def test_staging_name_matches_for_short_destinations(self, tmp_path: Path) -> None:
        """The readable shape is kept when it fits."""
        dst = tmp_path / "Movie.mkv"

        name = file_utils._staging_name(dst)

        assert name.startswith(".Movie.mkv.movarr-")
        assert file_utils._is_stale_staging(tmp_path / name, dst)


class TestStagingLock:
    """A staging file held under an exclusive lock belongs to a live copy."""

    def test_cleanup_skips_a_locked_staging_file(self, tmp_path: Path) -> None:
        """Cleanup must leave a staging file another process has locked."""
        import fcntl

        dst = tmp_path / "dst.mkv"
        dst.write_bytes(b"final")
        staging = tmp_path / file_utils._staging_name(dst)
        staging.write_bytes(b"in progress")

        with staging.open("rb") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            file_utils._reclaim_staging_file(staging, cutoff=time.time() + 60)

        assert staging.exists(), "a locked staging file must never be reclaimed"

    def test_staging_file_is_locked_while_the_copy_runs(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A live copy holds the staging lock, so a second process cannot reclaim it."""
        import fcntl

        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        observed: dict[str, bool] = {}

        def _fake_copy(_src: str, tmp: str) -> str:
            path = Path(tmp)
            if path.exists():
                with path.open("rb") as fh:
                    try:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        observed["locked"] = False
                    except OSError:
                        observed["locked"] = True
            else:
                observed["locked"] = False
            path.write_bytes(b"staged")
            return "digest"

        mocker.patch("movarr.file_utils._perform_copy", side_effect=_fake_copy)

        assert file_utils._replace_copy(src, dst, confirm=False) is True
        assert observed["locked"] is True, "the staging file must be locked for the copy"


class TestLibrarySweep:
    """Staging leftovers for titles that are never retried must still be reclaimed."""

    def test_sweeps_abandoned_leftovers_across_movies(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A leftover beside another movie is reclaimed; look-alike user files are kept."""
        root = tmp_path / "Movies"
        movie_a = root / "A (2020)"
        movie_b = root / "B (2021)"
        movie_a.mkdir(parents=True)
        movie_b.mkdir()
        leftover = movie_b / ".B.2021.2160p.mkv.movarr-deadbeef.part"
        leftover.write_bytes(b"abandoned")
        user_file = movie_b / ".B.2021.2160p.mkv.20230101.part"
        user_file.write_bytes(b"user file")
        mocker.patch("movarr.file_utils.time.time", return_value=time.time() + 3 * 60 * 60)

        reclaimed = file_utils.sweep_stale_staging_roots([str(root)])

        assert reclaimed == 1
        assert not leftover.exists(), "an abandoned staging leftover must be reclaimed"
        assert user_file.exists(), "a user file that merely looks similar must be kept"


class TestStagingMatcherAndSweepEdges:
    """Defensive branches of the staging matcher, sweep and reclaim path."""

    def test_matcher_rejects_shapes_it_does_not_own(self) -> None:
        """Only dot-prefixed .part names carrying the marker and token are staging files."""
        assert file_utils._is_staging_leftover_name("Movie.mkv") is False
        assert file_utils._is_staging_leftover_name(".Movie.mkv.part") is False
        assert file_utils._is_staging_leftover_name(".Movie.mkv.movarr-notahex.part") is False
        assert file_utils._is_staging_leftover_name(".Movie.mkv.movarr-deadbeef.part") is True

    def test_sweep_skips_a_root_that_does_not_exist(self, tmp_path: Path) -> None:
        """A configured root that is absent must not raise or count."""
        assert file_utils.sweep_stale_staging_roots([str(tmp_path / "missing")]) == 0

    def test_sweep_survives_an_unreadable_tree(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """An OSError while walking a root is logged and skipped."""
        mocker.patch("movarr.file_utils.os.walk", side_effect=OSError("permission denied"))

        with _captured_logs() as records:
            assert file_utils.sweep_stale_staging_roots([str(tmp_path)]) == 0

        assert any("Could not sweep" in message for message in _messages(records, "DEBUG"))

    def test_unopenable_leftover_logs_the_unlock_hint(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A leftover this user cannot open keeps the actionable warning."""
        candidate = tmp_path / ".dst.mkv.movarr-deadbeef.part"
        candidate.write_bytes(b"leftover")
        mocker.patch("movarr.file_utils.os.open", side_effect=PermissionError("denied"))

        with _captured_logs() as records:
            removed = file_utils._reclaim_staging_file(candidate, cutoff=time.time() + 60)

        assert removed is False
        assert candidate.exists()
        assert any(
            "Unlock it through the configured pre_copy hook" in message for message in _messages(records, "WARNING")
        )

    def test_reclaim_keeps_a_file_that_vanishes_before_the_stat(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A candidate removed by another process between open and stat is not an error."""
        candidate = tmp_path / ".dst.mkv.movarr-deadbeef.part"
        candidate.write_bytes(b"leftover")
        real_stat = Path.stat

        def _stat(self: Path) -> object:
            if self.name == candidate.name:
                raise FileNotFoundError("gone")
            return real_stat(self)

        mocker.patch.object(Path, "stat", _stat)

        assert file_utils._reclaim_staging_file(candidate, cutoff=time.time() + 60) is False

    def test_staging_creation_failure_aborts_the_replace(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A staging file that cannot be created aborts cleanly instead of raising."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        mocker.patch("movarr.file_utils.os.open", side_effect=PermissionError("denied"))

        with _captured_logs() as records:
            assert file_utils._replace_copy(src, dst, confirm=False) is False

        assert not dst.exists(), "a failed staging step must leave the destination absent"
        assert any("Could not stage" in message for message in _messages(records, "ERROR"))

    def test_flock_failure_aborts_the_replace_cleanly(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A file system that cannot lock must fail the copy gracefully, not raise."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        mocker.patch("movarr.file_utils.fcntl.flock", side_effect=OSError(38, "No locks available"))
        close_spy = mocker.patch("movarr.file_utils.os.close", wraps=os.close)

        with _captured_logs() as records:
            assert file_utils._replace_copy(src, dst, confirm=False) is False

        assert not dst.exists(), "the destination must be untouched"
        assert close_spy.called, "the staging descriptor must be closed, not leaked"
        assert any("cannot lock" in message for message in _messages(records, "ERROR"))

    def test_unlockable_candidate_is_kept_with_an_accurate_reason(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A missing lock facility is not contention; the leftover is kept either way."""
        candidate = tmp_path / ".dst.mkv.movarr-deadbeef.part"
        candidate.write_bytes(b"leftover")
        mocker.patch("movarr.file_utils.fcntl.flock", side_effect=OSError(38, "No locks available"))

        with _captured_logs() as records:
            assert file_utils._reclaim_staging_file(candidate, cutoff=time.time() + 60) is False

        assert candidate.exists()
        debug = " ".join(_messages(records, "DEBUG"))
        assert "cannot lock" in debug
        assert "another process is still using it" not in debug

    def test_staging_name_survives_an_undecodable_name(self, tmp_path: Path) -> None:
        """A lone surrogate (reachable from JSON metadata) must not raise here."""
        dst = tmp_path / (("x" * 240) + "\ud800")

        name = file_utils._staging_name(dst)

        assert len(name.encode("utf-8", "surrogatepass")) <= 255
        assert file_utils._is_stale_staging(tmp_path / name, dst)

    def test_public_sweep_is_exported(self) -> None:
        """A function imported as public API belongs in __all__."""
        assert "sweep_stale_staging_roots" in file_utils.__all__

    def test_flock_failure_leaves_no_staging_leftover(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A lockless mount must not accumulate one empty staging file per attempt."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        mocker.patch("movarr.file_utils.fcntl.flock", side_effect=OSError(38, "No locks available"))

        assert file_utils._replace_copy(src, dst, confirm=False) is False

        leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".part")]
        assert leftovers == [], f"staging leftovers remain: {leftovers}"

    def test_sweep_reports_an_unreadable_subdirectory(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A directory that cannot be scanned is logged, not silently skipped."""

        def _walk(_root: str, onerror: object = None, **_kwargs: object) -> Iterator[tuple[str, list[str], list[str]]]:
            if callable(onerror):
                onerror(OSError("permission denied"))
            return iter(())

        mocker.patch("movarr.file_utils.os.walk", side_effect=_walk)

        with _captured_logs() as records:
            assert file_utils.sweep_stale_staging_roots([str(tmp_path)]) == 0

        assert any("Could not scan" in message for message in _messages(records, "DEBUG"))

    def test_readable_stem_is_kept_at_the_exact_limit(self, tmp_path: Path) -> None:
        """A 233-byte destination name still fits the readable stem (255-byte result)."""
        dst = tmp_path / (("x" * 229) + ".mkv")

        name = file_utils._staging_name(dst)

        assert name.startswith("." + dst.name + ".movarr-")
        assert len(name.encode()) == 255

    def test_one_byte_over_the_limit_uses_the_hash_stem(self, tmp_path: Path) -> None:
        """A 234-byte destination name would overflow, so it gets the hash stem."""
        dst = tmp_path / (("x" * 230) + ".mkv")

        name = file_utils._staging_name(dst)

        assert not name.startswith("." + dst.name), "the readable stem cannot fit"
        assert len(name.encode()) <= 255

    def test_sweep_reclaims_the_hashed_staging_shape(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """The shape matcher must also recognise the hashed stem the sweep exists for."""
        movie = tmp_path / "A (2020)"
        movie.mkdir()
        hashed = movie / ".0123456789abcdef.movarr-deadbeef.part"
        hashed.write_bytes(b"leftover")
        mocker.patch("movarr.file_utils.time.time", return_value=time.time() + 3 * 60 * 60)

        assert file_utils.sweep_stale_staging_roots([str(tmp_path)]) == 1
        assert not hashed.exists()

    def test_read_only_leftover_is_reclaimed(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A leftover that only opens for reading must still be reclaimed.

        The write-mode failure is forced so the test does not depend on running as an
        unprivileged user (root opens a 0444 file for writing).
        """
        candidate = tmp_path / ".dst.mkv.movarr-deadbeef.part"
        candidate.write_bytes(b"leftover")
        real_open = os.open
        attempted: list[int] = []

        def _open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
            attempted.append(flags)
            if flags & os.O_RDWR:
                raise PermissionError("denied")
            return real_open(path, flags, *args, **kwargs)

        mocker.patch("movarr.file_utils.os.open", side_effect=_open)

        assert file_utils._reclaim_staging_file(candidate, cutoff=time.time() + 60) is True
        assert not candidate.exists()
        assert any(flags & os.O_RDWR for flags in attempted), "the write attempt must come first"
        assert any(not flags & os.O_RDWR for flags in attempted), "the read-only fallback must run"

    def test_probe_survives_an_unexpected_open_error(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A non-permission open failure keeps the leftover and logs at debug."""
        candidate = tmp_path / ".dst.mkv.movarr-deadbeef.part"
        candidate.write_bytes(b"leftover")
        mocker.patch("movarr.file_utils.os.open", side_effect=OSError(5, "I/O error"))

        with _captured_logs() as records:
            assert file_utils._reclaim_staging_file(candidate, cutoff=time.time() + 60) is False

        assert candidate.exists()
        assert any("Could not inspect staging file" in message for message in _messages(records, "DEBUG"))

    def test_fifo_shaped_like_a_staging_file_does_not_hang(self, tmp_path: Path) -> None:
        """A FIFO the probe cannot open for writing must not block the reclaim path."""
        import os as _os

        fifo = tmp_path / ".dst.mkv.movarr-deadbeef.part"
        _os.mkfifo(fifo, 0o444)
        try:
            # Must return promptly; a blocking O_RDONLY open would hang here.
            assert file_utils._reclaim_staging_file(fifo, cutoff=time.time() + 60) in (True, False)
        finally:
            with contextlib.suppress(FileNotFoundError):
                _os.unlink(fifo)

    def test_sweep_skips_a_root_it_cannot_stat(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A root that cannot be stat'ed must not abort the whole post-processing pass."""
        real_is_dir = Path.is_dir

        def _is_dir(self: Path) -> bool:
            if self.name == "unreadable":
                raise PermissionError("denied")
            return real_is_dir(self)

        mocker.patch.object(Path, "is_dir", _is_dir)

        with _captured_logs() as records:
            assert file_utils.sweep_stale_staging_roots([str(tmp_path / "unreadable")]) == 0

        assert any("Could not sweep" in message for message in _messages(records, "DEBUG"))

    def test_existing_staging_name_is_never_reused(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A colliding staging name must abort, not write into someone else's file."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        fixed = mocker.MagicMock()
        fixed.hex = "deadbeef" * 4
        mocker.patch("movarr.file_utils.uuid.uuid4", return_value=fixed)
        staging = tmp_path / file_utils._staging_name(dst)
        staging.write_bytes(b"someone else's copy")

        assert file_utils._replace_copy(src, dst, confirm=False) is False

        assert staging.read_bytes() == b"someone else's copy", "the existing staging file must be untouched"

    def test_staging_logs_do_not_echo_control_characters(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A swept look-alike name can carry escapes; the log lines must stay clean."""
        candidate = tmp_path / ".Evil\x1b]0;pwn\x07\nFORGED.movarr-deadbeef.part"
        candidate.write_bytes(b"leftover")
        mocker.patch("movarr.file_utils.os.open", side_effect=PermissionError("denied"))

        with _captured_logs() as records:
            file_utils._reclaim_staging_file(candidate, cutoff=time.time() + 60)

        emitted = " ".join(record["message"] for record in records)
        assert "\x1b" not in emitted
        assert "\n" not in emitted

    def test_vanished_candidate_is_not_reported_as_unlockable(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A file removed between the two open attempts must not raise a warning."""
        candidate = tmp_path / ".dst.mkv.movarr-deadbeef.part"
        candidate.write_bytes(b"leftover")
        mocker.patch(
            "movarr.file_utils.os.open",
            side_effect=[PermissionError("denied"), FileNotFoundError("gone")],
        )

        with _captured_logs() as records:
            assert file_utils._reclaim_staging_file(candidate, cutoff=time.time() + 60) is False

        assert not _messages(records, "WARNING"), "a vanished file is not an unlock problem"

    def test_fifo_fallback_uses_nonblocking_flags(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """The read-only fallback must carry O_NONBLOCK regardless of the test user's uid."""
        import os as _os

        candidate = tmp_path / ".dst.mkv.movarr-deadbeef.part"
        candidate.write_bytes(b"leftover")
        real_open = _os.open
        flags_seen: list[int] = []

        def _open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
            flags_seen.append(flags)
            if flags & _os.O_RDWR:
                raise PermissionError("denied")
            return real_open(path, flags, *args, **kwargs)

        mocker.patch("movarr.file_utils.os.open", side_effect=_open)

        file_utils._reclaim_staging_file(candidate, cutoff=time.time() + 60)

        fallback = [flags for flags in flags_seen if not flags & _os.O_RDWR]
        assert fallback, "the read-only fallback must run"
        assert all(flags & _os.O_NONBLOCK for flags in fallback)

    def test_exception_text_does_not_echo_control_characters(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """The exception carries the filename too; that channel must be sanitised as well."""
        candidate = tmp_path / ".Evil\x1b]0;pwn\x07\nFORGED.movarr-deadbeef.part"
        candidate.write_bytes(b"leftover")
        mocker.patch(
            "movarr.file_utils.os.open",
            side_effect=OSError(5, "I/O error", str(candidate)),
        )

        with _captured_logs() as records:
            file_utils._reclaim_staging_file(candidate, cutoff=time.time() + 60)

        emitted = " ".join(record["message"] for record in records)
        assert "\x1b" not in emitted
        assert "\n" not in emitted

    def test_unlink_failure_does_not_echo_control_characters(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """The unlink WARNING must sanitise the exception that names the file."""
        candidate = tmp_path / ".Evil\x1b]0;pwn\x07\nFORGED.movarr-deadbeef.part"
        candidate.write_bytes(b"leftover")
        mocker.patch(
            "pathlib.Path.unlink",
            side_effect=OSError(1, "Operation not permitted", str(candidate)),
        )

        with _captured_logs() as records:
            file_utils._reclaim_staging_file(candidate, cutoff=time.time() + 60)

        emitted = " ".join(record["message"] for record in records)
        assert "\x1b" not in emitted
        assert "\n" not in emitted

    def test_sweep_skips_a_root_whose_is_dir_raises_value_error(self, mocker: MockerFixture) -> None:
        """is_dir() raises ValueError on the 3.12 floor for a NUL host path; the sweep must not abort."""
        mocker.patch("pathlib.Path.is_dir", side_effect=ValueError("embedded null byte"))

        with _captured_logs() as records:
            assert file_utils.sweep_stale_staging_roots(["/media/Movies"]) == 0

        assert any("Could not sweep" in message for message in _messages(records, "DEBUG"))

    def test_lock_descriptor_is_closed_after_a_successful_replace(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A successful replace must release its staging lock descriptor."""
        import os as _os

        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        close_spy = mocker.patch("movarr.file_utils.os.close", wraps=_os.close)

        assert file_utils._replace_copy(src, dst, confirm=False) is True

        assert close_spy.called, "the staging descriptor must be released"

    def test_staging_lock_retries_when_a_cleanup_probe_holds_it(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A probe can take the brand-new file's lock in the open->flock window."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        mocker.patch(
            "movarr.file_utils.fcntl.flock",
            side_effect=[OSError(11, "Resource temporarily unavailable"), None],
        )

        assert file_utils._replace_copy(src, dst, confirm=False) is True
        assert dst.read_bytes() == b"source data"

    def test_unencodable_name_aborts_cleanly(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A name the filesystem cannot encode (ValueError) must not escape the replace path."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        mocker.patch(
            "movarr.file_utils.os.open",
            side_effect=UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates not allowed"),
        )

        assert file_utils._replace_copy(src, dst, confirm=False) is False
        assert not dst.exists()

    def test_contention_retry_failure_aborts_cleanly(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A retry that fails must return False, release the fd, and leave no leftover."""
        import os as _os

        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        mocker.patch(
            "movarr.file_utils.fcntl.flock",
            side_effect=[OSError(11, "Resource temporarily unavailable")] * 8,
        )
        close_spy = mocker.patch("movarr.file_utils.os.close", wraps=_os.close)

        assert file_utils._replace_copy(src, dst, confirm=False) is False

        assert not dst.exists()
        assert not [p for p in tmp_path.iterdir() if p.name.endswith(".part")]
        assert close_spy.called, "the staging descriptor must be released"

    def test_contention_failure_message_names_the_cause(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A persistent holder must be reported as contention, not as a lockless mount."""
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(b"source data")
        mocker.patch(
            "movarr.file_utils.fcntl.flock",
            side_effect=[OSError(11, "Resource temporarily unavailable")] * 8,
        )

        with _captured_logs() as records:
            assert file_utils._replace_copy(src, dst, confirm=False) is False

        errors = " ".join(_messages(records, "ERROR"))
        assert "a cleanup pass is still holding it" in errors
        assert "cannot lock" not in errors

    def test_write_only_leftover_is_reclaimed(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A leftover whose mode denies reading can still be locked and unlinked."""
        import os as _os

        candidate = tmp_path / ".dst.mkv.movarr-deadbeef.part"
        candidate.write_bytes(b"leftover")
        real_open = _os.open

        def _open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
            if not flags & _os.O_WRONLY:  # O_RDONLY is 0, so test the write bit instead
                raise PermissionError("denied")
            return real_open(path, flags, *args, **kwargs)

        mocker.patch("movarr.file_utils.os.open", side_effect=_open)

        assert file_utils._reclaim_staging_file(candidate, cutoff=time.time() + 60) is True
        assert not candidate.exists()

    def test_display_is_encodable_for_undecodable_names(self) -> None:
        """A log line must never carry text the sink cannot encode."""
        text = file_utils._display(".Evil\udcff.movarr-deadbeef.part")

        assert text.encode("utf-8")
        assert "\udcff" not in text

    def test_probe_reports_an_io_error_instead_of_the_unlock_hint(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A non-permission open failure is an I/O problem, not an unlock problem."""
        import os as _os

        candidate = tmp_path / ".dst.mkv.movarr-deadbeef.part"
        candidate.write_bytes(b"leftover")

        def _open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
            if flags & _os.O_RDWR:
                raise PermissionError("denied")
            raise OSError(5, "I/O error", str(path))

        mocker.patch("movarr.file_utils.os.open", side_effect=_open)

        with _captured_logs() as records:
            assert file_utils._reclaim_staging_file(candidate, cutoff=time.time() + 60) is False

        assert not any("Unlock it through" in message for message in _messages(records, "WARNING"))
        assert any("Could not inspect staging file" in message for message in _messages(records, "DEBUG"))

    def test_fallback_mode_contention_is_reported_accurately(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """A lock refusal through a read-only handle is not another process's doing."""
        import os as _os

        candidate = tmp_path / ".dst.mkv.movarr-deadbeef.part"
        candidate.write_bytes(b"leftover")
        real_open = _os.open

        def _open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
            if flags & _os.O_RDWR:
                raise PermissionError("denied")
            return real_open(path, flags, *args, **kwargs)

        mocker.patch("movarr.file_utils.os.open", side_effect=_open)
        mocker.patch(
            "movarr.file_utils.fcntl.flock",
            side_effect=OSError(11, "Resource temporarily unavailable"),
        )

        with _captured_logs() as records:
            assert file_utils._reclaim_staging_file(candidate, cutoff=time.time() + 60) is False

        debug = " ".join(_messages(records, "DEBUG"))
        assert "cannot take an exclusive lock" in debug
        assert "another process is still using it" not in debug
