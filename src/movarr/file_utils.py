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
    "resolution_label_from_height",
]

# Resolution height strings recognised by the post-processor routing logic.
_KNOWN_HEIGHTS: frozenset[str] = frozenset({"720", "1080", "2160"})


def walk_library(library_paths: list[str]) -> chain[tuple[str, list[str], list[str]]]:
    """Yield ``(root, dirs, files)`` tuples for every path in *library_paths*.

    Args:
        library_paths: List of root directories to walk.
    """
    return chain.from_iterable(os.walk(p) for p in library_paths)


_CHUNK_SIZE = 65_536  # 64 KiB — balance between I/O calls and memory usage


def _sha256(file_path: Path) -> str:
    """Return the SHA-256 hex digest of *file_path*."""
    h = hashlib.sha256()
    with file_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
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


def _verify_existing(src: Path, dst: Path) -> bool | None:
    """Check if *dst* already matches *src* by SHA-256.

    Returns:
        True  — dst is identical to src; skip the copy.
        None  — dst was mismatched and has been deleted; proceed to copy.
        False — an error occurred (src disappeared or delete failed).
    """
    _logger.info("Verifying existing destination '{}' checksum.", dst)
    try:
        src_hash = _sha256(src)
    except OSError:
        _logger.error("Source file disappeared during verification: '{}'", src)
        return False
    dst_hash = _sha256(dst)
    if src_hash == dst_hash:
        _logger.info(
            "Destination '{}' already matches source (sha256={}); skipping copy.",
            dst,
            src_hash[:12],
        )
        return True
    _logger.warning(
        "Destination '{}' checksum mismatch (src={}, dst={}); re-copying.",
        dst,
        src_hash[:12],
        dst_hash[:12],
    )
    if not delete_file(dst):
        return False
    return None


def _perform_copy(src: Path, dst: Path) -> bool:
    """Copy *src* to *dst* via :func:`shutil.copy2` and verify checksums.

    Returns:
        True on success, False on any copy or verification failure.
    """
    try:
        shutil.copy2(str(src), str(dst))
        _logger.info("Copied '{}' → '{}'.", src, dst)
    except FileNotFoundError as exc:
        _logger.warning("Source '{}' not found during copy: {}.", src, exc)
        return False
    except PermissionError as exc:
        _logger.warning("Permission denied copying '{}' → '{}': {}.", src, dst, exc)
        return False
    except OSError as exc:
        _logger.warning("OS error copying '{}' → '{}': {}.", src, dst, exc)
        return False

    _logger.info("Verifying copy integrity for '{}'.", dst)
    try:
        src_hash = _sha256(src)
    except OSError:
        _logger.error("Source file disappeared during post-copy verification: '{}'", src)
        return False
    dst_hash = _sha256(dst)
    if src_hash != dst_hash:
        _logger.warning(
            "Post-copy checksum mismatch for '{}': src={}, dst={}.",
            dst,
            src_hash[:12],
            dst_hash[:12],
        )
        return False

    _logger.info("Verified '{}' (sha256={}).", dst, dst_hash[:12])
    return True


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
        existing = _verify_existing(src_path, dst_path)
        if existing is True:
            return True
        if existing is False:
            return False
        # existing is None: dst was deleted, fall through to copy

    return _perform_copy(src_path, dst_path)


def resolution_label_from_height(height: str | None) -> str:
    """Return ``"UHD"`` for 2160p, ``"HD"`` for anything else.

    Args:
        height: Resolution height string (e.g. ``"1080"`` or ``"2160"``).
    """
    return "UHD" if height == "2160" else "HD"
