"""File system and media utilities for movarr."""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from loguru import logger as _logger

__all__ = [
    "walk_library",
    "copy_with_verify",
    "make_directory",
    "delete_file",
]


def walk_library(library_paths: list[str]) -> list[tuple[str, list[str], list[str]]]:
    """Return ``(root, dirs, files)`` tuples for every path in *library_paths*.

    Materialised as a list on purpose: the dedup pipeline walks the result more
    than once, and a one-shot iterator would silently appear empty the second
    time round.

    Args:
        library_paths: List of root directories to walk.
    """
    return [entry for path in library_paths for entry in os.walk(path)]


_CHUNK_SIZE = 65_536  # 64 KiB — balance between I/O calls and memory usage


def _sha256(file_path: Path, label: str | None = None) -> str:
    """Return the SHA-256 hex digest of *file_path*.

    If *label* is provided, progress is logged at 25/50/75% milestones
    using *label* as the message prefix with ``{}`` for the file path
    (e.g. ``"Verifying '{}' checksum: 50% complete."``).
    """
    h = hashlib.sha256()
    total = file_path.stat().st_size
    next_pct = 25
    with file_path.open("rb") as fh:
        done = 0
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            h.update(chunk)
            if label and total > 0:
                done += len(chunk)
                pct = done * 100 // total
                while next_pct <= pct and next_pct <= 75:
                    _logger.info(label + " {}% complete.", file_path, next_pct)
                    next_pct += 25
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
    if target.is_dir():
        _logger.error("Refusing to delete '{}': it is a directory, not a file.", target)
        return False
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
    """Check whether *dst* already matches *src* by SHA-256.

    This never modifies either file.  Replacing a mismatched destination is the
    caller's job, and it happens only after the replacement copy is verified.

    Returns:
        True  - dst is identical to src (the copy can be skipped).
        None  - dst exists but differs, or became unreadable (replace it).
        False - the source file disappeared during verification.
    """
    _logger.info("Verifying existing destination '{}' checksum.", dst)
    try:
        src_hash = _sha256(src)
    except OSError:
        _logger.error("Source file disappeared during verification: '{}'", src)
        return False
    try:
        dst_hash = _sha256(dst, label="Verifying '{}' checksum:")
    except OSError:
        _logger.warning("Destination '{}' became unreadable during verification; replacing.", dst)
        return None
    if src_hash == dst_hash:
        _logger.info(
            "Destination '{}' already matches source (sha256={}); skipping copy.",
            dst,
            src_hash[:12],
        )
        return True
    _logger.warning(
        "Destination '{}' checksum mismatch (src={}, dst={}); replacing.",
        dst,
        src_hash[:12],
        dst_hash[:12],
    )
    return None


def _try_clear_immutable(target: Path) -> None:
    """Best-effort ``chattr -i`` so an immutable destination can be replaced."""
    _logger.debug("Attempting to remove immutable attribute from '{}'.", target)
    try:
        result = subprocess.run(["chattr", "-i", str(target)], capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        _logger.debug("chattr -i failed (rc={}): {}", result.returncode, stderr)


def _replace_copy(src: Path, dst: Path) -> bool:
    """Copy *src* to a temporary sibling, verify it, then swap it over *dst*.

    The old destination is never removed before a verified replacement exists,
    so a failed copy (disk full, permission error, vanished source) leaves the
    previous library file untouched.

    Returns:
        True when *dst* now holds verified content from *src*.
    """
    # Unique suffix: a torrent can legitimately contain "<name>.part" itself.
    tmp = dst.with_name(f".{dst.name}.{uuid.uuid4().hex[:8]}.part")
    try:
        if not _perform_copy(src, tmp):
            return False
        try:
            os.replace(tmp, dst)
        except PermissionError:
            # Immutable destination: clear the flag and retry the swap once.
            _try_clear_immutable(dst)
            os.replace(tmp, dst)
        _logger.info("Replaced '{}' with verified copy.", dst)
        return True
    except OSError as exc:
        _logger.error("Could not replace '{}': {}.", dst, exc)
        return False
    finally:
        with contextlib.suppress(OSError):
            if tmp.exists():
                tmp.unlink()


def _do_copy(src: Path, dst: Path) -> None:
    """Copy *src* to *dst* in chunks, logging progress at 25/50/75% milestones.

    Preserves file metadata (timestamps, permissions) via :func:`shutil.copystat`
    after the data transfer, matching :func:`shutil.copy2` semantics.

    Raises:
        FileNotFoundError: if *src* does not exist.
        PermissionError: if *src* cannot be read or *dst* cannot be written.
        OSError: on any other I/O error.
    """
    total = src.stat().st_size
    next_pct = 25
    _logger.info("Copying '{}' → '{}'.", src, dst)
    with src.open("rb") as fsrc, dst.open("wb") as fdst:
        done = 0
        for chunk in iter(lambda: fsrc.read(_CHUNK_SIZE), b""):
            fdst.write(chunk)
            done += len(chunk)
            if total > 0:
                pct = done * 100 // total
                while next_pct <= pct and next_pct <= 75:
                    _logger.info("Copying '{}' → '{}': {}% complete.", src, dst, next_pct)
                    next_pct += 25
    shutil.copystat(str(src), str(dst))
    _logger.info("Copied '{}' → '{}'.", src, dst)


def _perform_copy(src: Path, dst: Path) -> bool:
    """Copy *src* to *dst* with chunked progress and SHA-256 verification.

    Returns:
        True on success, False on any copy or verification failure.
    """
    try:
        _do_copy(src, dst)
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
    try:
        dst_hash = _sha256(dst, label="Verifying '{}' copy integrity:")
    except OSError as exc:
        # Contract is "True/False", not "raise": a destination that became
        # unreadable after the copy is a failed copy.
        _logger.warning("Could not verify copied file '{}': {}.", dst, exc)
        return False
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
    - If *dst* exists but checksums differ, the file is copied to a temporary
      sibling, verified, and only then swapped into place atomically.  A failed
      copy leaves the previous destination untouched.

    Args:
        src: Source file path.
        dst: Destination file path (parent directory must exist, or is created).

    Returns:
        True only when *dst* holds verified content from *src*.  ``False`` on
        any copy, verification or replacement failure — the previous
        destination is then left in place, not deleted.
        See :func:`_verify_existing` for details.
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
        # existing is None: dst differs or is unreadable; replace it atomically

    return _replace_copy(src_path, dst_path)
