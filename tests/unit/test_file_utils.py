"""Unit tests for movarr.file_utils."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from movarr.file_utils import (
    copy_with_verify,
    delete_file,
    make_directory,
    resolution_from_ffprobe,
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

    def test_returns_false_on_permission_error(self, tmp_path: Path, mocker: pytest.MonkeyPatch) -> None:
        mocker.patch("pathlib.Path.mkdir", side_effect=PermissionError("denied"))
        result = make_directory(tmp_path / "new_dir")
        assert result is False

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        target = str(tmp_path / "string_path")
        result = make_directory(target)
        assert result is True
        assert Path(target).is_dir()

    def test_returns_false_on_os_error(self, tmp_path: Path, mocker: pytest.MonkeyPatch) -> None:
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

    def test_returns_false_on_permission_error(self, tmp_path: Path, mocker: pytest.MonkeyPatch) -> None:
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

    def test_returns_false_on_os_error(self, tmp_path: Path, mocker: pytest.MonkeyPatch) -> None:
        f = tmp_path / "err.txt"
        f.write_text("data")
        mocker.patch("pathlib.Path.unlink", side_effect=OSError("io error"))
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

    def test_skips_copy_when_dst_already_matches(self, tmp_path: Path, mocker: pytest.MonkeyPatch) -> None:
        content = b"identical content"
        src = tmp_path / "src.mkv"
        dst = tmp_path / "dst.mkv"
        src.write_bytes(content)
        dst.write_bytes(content)
        spy = mocker.patch("shutil.copy2")
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

    def test_returns_false_on_post_copy_checksum_mismatch(
        self, tmp_path: Path, mocker: pytest.MonkeyPatch
    ) -> None:
        src = tmp_path / "src.mkv"
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        dst = dst_dir / "dst.mkv"
        src.write_bytes(b"data")
        # Patch _sha256 to return mismatched digests after copy
        mocker.patch("movarr.file_utils._sha256", side_effect=["aaa111aaa111", "bbb222bbb222"])
        result = copy_with_verify(src, dst)
        assert result is False

    def test_returns_false_when_directory_creation_fails(
        self, tmp_path: Path, mocker: pytest.MonkeyPatch
    ) -> None:
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


class TestResolutionFromFfprobe:
    """Tests for resolution_from_ffprobe()."""

    def test_returns_none_when_ffmpeg_not_installed(
        self, tmp_path: Path, mocker: pytest.MonkeyPatch
    ) -> None:
        mocker.patch.dict("sys.modules", {"ffmpeg": None})
        result = resolution_from_ffprobe(str(tmp_path / "file.mkv"))
        assert result is None

    def test_maps_1920_width_to_1080(self, tmp_path: Path, mocker: pytest.MonkeyPatch) -> None:
        mock_ffmpeg = mocker.MagicMock()
        mock_ffmpeg.probe.return_value = {"streams": [{"width": 1920, "height": 1080}]}
        mock_ffmpeg.Error = RuntimeError
        mocker.patch.dict("sys.modules", {"ffmpeg": mock_ffmpeg})
        result = resolution_from_ffprobe(str(tmp_path / "file.mkv"))
        assert result == "1080"

    def test_maps_3840_width_to_2160(self, tmp_path: Path, mocker: pytest.MonkeyPatch) -> None:
        mock_ffmpeg = mocker.MagicMock()
        mock_ffmpeg.probe.return_value = {"streams": [{"width": 3840, "height": 2160}]}
        mock_ffmpeg.Error = RuntimeError
        mocker.patch.dict("sys.modules", {"ffmpeg": mock_ffmpeg})
        result = resolution_from_ffprobe(str(tmp_path / "file.mkv"))
        assert result == "2160"

    def test_maps_1280_width_to_720(self, tmp_path: Path, mocker: pytest.MonkeyPatch) -> None:
        mock_ffmpeg = mocker.MagicMock()
        mock_ffmpeg.probe.return_value = {"streams": [{"width": 1280, "height": 720}]}
        mock_ffmpeg.Error = RuntimeError
        mocker.patch.dict("sys.modules", {"ffmpeg": mock_ffmpeg})
        result = resolution_from_ffprobe(str(tmp_path / "file.mkv"))
        assert result == "720"

    def test_returns_raw_height_for_unmapped_width(self, tmp_path: Path, mocker: pytest.MonkeyPatch) -> None:
        mock_ffmpeg = mocker.MagicMock()
        mock_ffmpeg.probe.return_value = {"streams": [{"width": 854, "height": 480}]}
        mock_ffmpeg.Error = RuntimeError
        mocker.patch.dict("sys.modules", {"ffmpeg": mock_ffmpeg})
        result = resolution_from_ffprobe(str(tmp_path / "file.mkv"))
        assert result == "480"

    def test_returns_none_on_probe_error(self, tmp_path: Path, mocker: pytest.MonkeyPatch) -> None:
        mock_ffmpeg = mocker.MagicMock()
        mock_ffmpeg.Error = RuntimeError
        mock_ffmpeg.probe.side_effect = RuntimeError("probe failed")
        mocker.patch.dict("sys.modules", {"ffmpeg": mock_ffmpeg})
        result = resolution_from_ffprobe(str(tmp_path / "file.mkv"))
        assert result is None

    def test_uses_custom_ffprobe_path(self, tmp_path: Path, mocker: pytest.MonkeyPatch) -> None:
        mock_ffmpeg = mocker.MagicMock()
        mock_ffmpeg.probe.return_value = {"streams": [{"width": 1920, "height": 1080}]}
        mock_ffmpeg.Error = RuntimeError
        mocker.patch.dict("sys.modules", {"ffmpeg": mock_ffmpeg})
        result = resolution_from_ffprobe(str(tmp_path / "file.mkv"), ffprobe_path="/usr/bin/ffprobe")
        assert result == "1080"
        _, kwargs = mock_ffmpeg.probe.call_args
        assert kwargs.get("cmd") == "/usr/bin/ffprobe"

    def test_returns_none_on_key_error(self, tmp_path: Path, mocker: pytest.MonkeyPatch) -> None:
        mock_ffmpeg = mocker.MagicMock()
        mock_ffmpeg.probe.return_value = {"streams": [{}]}  # missing width/height
        mock_ffmpeg.Error = RuntimeError
        mocker.patch.dict("sys.modules", {"ffmpeg": mock_ffmpeg})
        result = resolution_from_ffprobe(str(tmp_path / "file.mkv"))
        assert result is None


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
