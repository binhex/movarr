"""File system and media utilities for movarr."""

from __future__ import annotations

import hashlib
import os
import shutil
from itertools import chain
from pathlib import Path

from loguru import logger as _logger

__all__ = [
    "walk_library",
    "copy_with_verify",
    "make_directory",
    "delete_file",
    "resolution_from_ffprobe",
    "resolution_label_from_height",
]

# Mapping from pixel width to canonical resolution height string.
_WIDTH_TO_HEIGHT: dict[int, str] = {
    1280: "720",
    1920: "1080",
    3840: "2160",
}


def walk_library(library_paths: list[str]) -> chain[tuple[str, list[str], list[str]]]:
    """Yield ``(root, dirs, files)`` tuples for every path in *library_paths*.

    Args:
        library_paths: List of root directories to walk.
    """
    return chain.from_iterable(os.walk(p) for p in library_paths)


def _sha256(file_path: Path) -> str:
    """Return the SHA-256 hex digest of *file_path*."""
    h = hashlib.sha256()
    with file_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def make_directory(path: str | Path) -> bool:
    """Create *path* and all parents.  Returns True on success, False on error.

    Args:
        path: Directory path to create.
    """
    target = Path(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
        _logger.debug("Created directory '{}'.", target)
        return True
    except PermissionError as exc:
        _logger.warning("Permission denied creating '{}': {}.", target, exc)
    except OSError as exc:
        _logger.warning("OS error creating '{}': {}.", target, exc)
    return False


def delete_file(path: str | Path) -> bool:
    """Delete *path*.  Returns True on success or if file is already absent.

    Args:
        path: File path to delete.
    """
    target = Path(path)
    if not target.is_file():
        _logger.debug("File '{}' does not exist; treating as already deleted.", target)
        return True
    try:
        target.unlink()
        _logger.info("Deleted file '{}'.", target)
        return True
    except PermissionError as exc:
        _logger.warning("Permission denied deleting '{}': {}.", target, exc)
    except IsADirectoryError as exc:
        _logger.warning("'{}' is a directory, not a file: {}.", target, exc)
    except OSError as exc:
        _logger.warning("OS error deleting '{}': {}.", target, exc)
    return False


def copy_with_verify(src: str | Path, dst: str | Path) -> bool:
    """Copy *src* to *dst* with SHA-256 pre/post verification.

    - If *dst* already exists and checksums match, the copy is skipped.
    - If *dst* exists but checksums differ, the destination is deleted and
      the file is re-copied.
    - After copying, checksums are compared again to confirm integrity.

    Args:
        src: Source file path.
        dst: Destination file path (parent directory must exist, or is created).

    Returns:
        True if the file is present at *dst* with the correct checksum.
    """
    src_path = Path(src)
    dst_path = Path(dst)

    if not make_directory(dst_path.parent):
        return False

    if dst_path.is_file():
        src_hash = _sha256(src_path)
        dst_hash = _sha256(dst_path)
        if src_hash == dst_hash:
            _logger.info(
                "Destination '{}' already matches source (sha256={}); skipping copy.",
                dst_path,
                src_hash[:12],
            )
            return True
        _logger.warning(
            "Destination '{}' checksum mismatch (src={}, dst={}); re-copying.",
            dst_path,
            src_hash[:12],
            dst_hash[:12],
        )
        if not delete_file(dst_path):
            return False

    try:
        shutil.copy2(str(src_path), str(dst_path))
        _logger.info("Copied '{}' → '{}'.", src_path, dst_path)
    except FileNotFoundError as exc:
        _logger.warning("Source '{}' not found during copy: {}.", src_path, exc)
        return False
    except PermissionError as exc:
        _logger.warning("Permission denied copying '{}' → '{}': {}.", src_path, dst_path, exc)
        return False
    except OSError as exc:
        _logger.warning("OS error copying '{}' → '{}': {}.", src_path, dst_path, exc)
        return False

    src_hash = _sha256(src_path)
    dst_hash = _sha256(dst_path)
    if src_hash != dst_hash:
        _logger.warning(
            "Post-copy checksum mismatch for '{}': src={}, dst={}.",
            dst_path,
            src_hash[:12],
            dst_hash[:12],
        )
        return False

    _logger.info("Verified '{}' (sha256={}).", dst_path, dst_hash[:12])
    return True


def resolution_from_ffprobe(file_path: str | Path, ffprobe_path: str | None = None) -> str | None:
    """Return the resolution height string by probing *file_path* with ffprobe.

    Maps pixel widths 1280→720, 1920→1080, 3840→2160.  Other widths fall
    through to the raw height value.

    Args:
        file_path: Path to the media file.
        ffprobe_path: Path to the ffprobe binary.  Uses PATH if ``None``.

    Returns:
        Resolution as a string (e.g. ``"1080"``), or ``None`` on failure.
    """
    try:
        import ffmpeg
    except ImportError:
        _logger.warning("ffmpeg-python not installed; cannot probe '{}'.", file_path)
        return None

    try:
        probe_kwargs: dict[str, str | Path] = {}
        if ffprobe_path:
            probe_kwargs["cmd"] = ffprobe_path
        info = ffmpeg.probe(str(file_path), select_streams="v", **probe_kwargs)
        width: int = info["streams"][0]["width"]
        height: str = str(info["streams"][0]["height"])
        return _WIDTH_TO_HEIGHT.get(width, height)
    except (ffmpeg.Error, KeyError, IndexError) as exc:
        _logger.warning("ffprobe failed on '{}': {}.", file_path, exc)
        return None


def resolution_label_from_height(height: str | None) -> str:
    """Return ``"UHD"`` for 2160p, ``"HD"`` for anything else.

    Args:
        height: Resolution height string (e.g. ``"1080"`` or ``"2160"``).
    """
    return "UHD" if height == "2160" else "HD"
