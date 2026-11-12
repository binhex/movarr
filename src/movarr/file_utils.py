"""File system and media utilities for movarr."""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import subprocess
import time
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
        result = subprocess.run(
            ["chattr", "-i", str(target)],
            capture_output=True,
            timeout=5,
            # The branches below match English messages, so pin the locale.
            env={**os.environ, "LC_ALL": "C", "LANG": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if result.returncode == 0:
        return
    stderr = result.stderr.decode(errors="replace").strip()
    lowered = stderr.lower()
    if "not supported" in lowered or "inappropriate ioctl" in lowered or "invalid argument" in lowered:
        _logger.warning(
            "Could not clear the immutable flag on '{}': this file system does not implement the file-flag "
            "ioctl, or does not support the immutable flag through it (Unraid user shares, and other shfs/FUSE "
            "or SMB mounts, all refuse it).  Unlock the array disk path instead, e.g. with a pre_copy hook that "
            "runs chattr -i against '/mnt/disk*'.",
            target,
        )
        return
    if "operation not permitted" in lowered:
        _logger.warning(
            "Could not clear the immutable flag on '{}': chattr needs root plus CAP_LINUX_IMMUTABLE "
            "(run the container with '--cap-add LINUX_IMMUTABLE'), or unlock it from a pre_copy hook.",
            target,
        )
        return
    if "read-only file system" in lowered:
        # Defensive: a read-only mount fails the swap with EROFS rather than
        # EPERM, so this only fires if the mount flips between the syscalls.
        _logger.warning(
            "Could not clear the immutable flag on '{}': the file system is mounted read-only.",
            target,
        )
        return
    _logger.debug("chattr -i failed (rc={}): {}", result.returncode, stderr)


_HEX_CHARS = frozenset("0123456789abcdef")
_STAGING_TOKEN_LEN = 8
_STAGING_MARKER = "movarr-"
_STAGING_SUFFIX = ".part"
# A staging file is written for the whole copy, read once more to verify it,
# and read again after it is swapped in, so its change time stays fresh for the
# length of a slow multi-gigabyte copy plus the hashing passes.
_STALE_STAGING_MIN_AGE_SECS = 2 * 60 * 60


def _staging_name(dst: Path) -> str:
    """Return the staging file name used while replacing *dst*.

    Single source of truth for the naming scheme: a torrent can legitimately
    ship a ``<name>.part`` file itself, and ``.<name>.<datestamp>.part`` is a
    plausible user file, so leftover cleanup may only ever match a name that
    carries our marker.
    """
    return f".{dst.name}.{_STAGING_MARKER}{uuid.uuid4().hex[:_STAGING_TOKEN_LEN]}{_STAGING_SUFFIX}"


def _is_stale_staging(candidate: Path, dst: Path) -> bool:
    """Return True when *candidate* is a staging file an earlier run left for *dst*.

    Staging names are ``.<dst name>.movarr-<8 hex chars>.part``.  Matching the
    marker and the exact token shape keeps a torrent that legitimately ships
    ``<name>.part`` - or a user file named ``.<name>.<datestamp>.part`` - from
    being mistaken for one of our leftovers.
    """
    prefix = f".{dst.name}.{_STAGING_MARKER}"
    if not candidate.name.startswith(prefix) or not candidate.name.endswith(_STAGING_SUFFIX):
        return False
    token = candidate.name[len(prefix) : -len(_STAGING_SUFFIX)]
    return len(token) == _STAGING_TOKEN_LEN and all(char in _HEX_CHARS for char in token)


def _remove_stale_staging(dst: Path, min_age_secs: float = _STALE_STAGING_MIN_AGE_SECS) -> None:
    """Delete staging files an interrupted earlier run left behind for *dst*.

    A failed swap can leave a fully copied staging file in place, and a
    host-side locker that marks it immutable makes the cleanup ``unlink`` fail
    too - every retry would then add another multi-gigabyte leftover.

    Only files whose inode change time is older than *min_age_secs* are
    removed.  A write refreshes the change time, and the metadata preservation
    that backdates the modification time does not backdate it, so a staging
    file that is still being written or hashed stays young.  Records with no
    usable change time (``0``) are kept.

    Assumes no second movarr process is replacing the same destination: a
    ``--once`` run started alongside the daemon is not covered.  File systems
    that synthesise the change time from the modification time can make a live
    staging file look old; ext4/xfs and Unraid's shfs report a real one.

    Only leftovers beside the destination being replaced are reclaimed, so a
    title that is never retried keeps its leftover until it is.
    """
    cutoff = time.time() - min_age_secs
    try:
        candidates = list(dst.parent.iterdir())
    except OSError as exc:
        _logger.debug("Could not scan '{}' for stale staging files: {}.", dst.parent, exc)
        return
    for candidate in candidates:
        if not _is_stale_staging(candidate, dst):
            continue
        try:
            changed = candidate.stat().st_ctime
        except FileNotFoundError:
            continue
        except OSError as exc:
            _logger.debug("Could not inspect staging file '{}': {}.", candidate, exc)
            continue
        if changed <= 0:
            _logger.debug("Keeping staging file '{}' with an unknown change time.", candidate)
            continue
        if changed > cutoff:
            _logger.debug("Keeping recent staging file '{}'; it may belong to a running copy.", candidate)
            continue
        try:
            candidate.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            _logger.warning(
                "Could not remove stale staging file '{}': {}.  Unlock it through the configured pre_copy hook "
                "(or chattr -i on the '/mnt/disk*' path) so the next run can reclaim it.",
                candidate,
                exc,
            )
        else:
            _logger.info("Removed stale staging file '{}' left by an earlier run.", candidate)


def _confirm_swap(dst: Path, digest: str) -> bool:
    """Return True when *dst* now holds the verified content *digest*.

    Re-hashing the destination is the only check a user-share (shfs) rename
    cannot fool: when the rename lands the staged file on another array disk,
    the share keeps resolving a same-named duplicate, so metadata comparison
    can match while the library still serves different bytes.
    """
    try:
        landed = _sha256(dst, label="Verifying '{}' after the swap:")
    except FileNotFoundError:
        _logger.error(
            "Destination '{}' is missing after the swap: a same-named duplicate was probably moved across "
            "array disks instead of being replaced.  Check '/mnt/disk*' and remove the stale copy.",
            dst,
        )
        return False
    except OSError as exc:
        _logger.error("Could not verify '{}' after the swap: {}.", dst, exc)
        return False
    if landed != digest:
        _logger.error(
            "Replacement of '{}' did not take effect: the path resolves to different content "
            "(a same-named duplicate on another array disk?  Check '/mnt/disk*').",
            dst,
        )
        return False
    return True


def _replace_copy(src: Path, dst: Path, *, confirm: bool) -> bool:
    """Copy *src* to a temporary sibling, verify it, then swap it over *dst*.

    The old destination is never removed before a verified replacement exists,
    so a copy that fails before the swap (disk full, permission error, vanished
    source) leaves the previous library file untouched.

    Args:
        src: Source file path.
        dst: Destination file path.
        confirm: Re-read *dst* after the swap and compare it with the staged
            digest.  Only a destination that already existed can be shadowed by
            a same-named copy elsewhere, so the extra full read of the payload
            is limited to the replacement path.

    Returns:
        True when *dst* now holds verified content from *src*.  A swap that
        cannot be confirmed afterwards also returns False - the destination may
        then already have changed, or another copy of the same name may be
        shadowing it.
    """
    tmp = dst.with_name(_staging_name(dst))
    _remove_stale_staging(dst)
    try:
        digest = _perform_copy(src, tmp)
        if digest is None:
            return False
        try:
            os.replace(tmp, dst)
        except PermissionError:
            # Immutable destination: clear the flag and retry the swap once.
            _try_clear_immutable(dst)
            try:
                os.replace(tmp, dst)
            except FileNotFoundError as exc:
                _logger.error(
                    "Staged copy '{}' disappeared while swapping in '{}': {} "
                    "(the file system moved it without replacing the destination, or a cleanup removed it).",
                    tmp,
                    dst,
                    exc,
                )
                return False
        if confirm and not _confirm_swap(dst, digest):
            return False
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


def _perform_copy(src: Path, dst: Path) -> str | None:
    """Copy *src* to *dst* with chunked progress and SHA-256 verification.

    Returns:
        The verified SHA-256 hex digest of the copied content, or ``None`` on
        any copy or verification failure.
    """
    try:
        _do_copy(src, dst)
    except FileNotFoundError as exc:
        _logger.warning("Source '{}' not found during copy: {}.", src, exc)
        return None
    except PermissionError as exc:
        _logger.warning("Permission denied copying '{}' → '{}': {}.", src, dst, exc)
        return None
    except OSError as exc:
        _logger.warning("OS error copying '{}' → '{}': {}.", src, dst, exc)
        return None

    _logger.info("Verifying copy integrity for '{}'.", dst)
    try:
        src_hash = _sha256(src)
    except OSError:
        _logger.error("Source file disappeared during post-copy verification: '{}'", src)
        return None
    try:
        dst_hash = _sha256(dst, label="Verifying '{}' copy integrity:")
    except OSError as exc:
        # Contract is "digest or None", not "raise": a destination that became
        # unreadable after the copy is a failed copy.
        _logger.warning("Could not verify copied file '{}': {}.", dst, exc)
        return None
    if src_hash != dst_hash:
        _logger.warning(
            "Post-copy checksum mismatch for '{}': src={}, dst={}.",
            dst,
            src_hash[:12],
            dst_hash[:12],
        )
        return None

    _logger.info("Verified '{}' (sha256={}).", dst, dst_hash[:12])
    return dst_hash


def copy_with_verify(src: str | Path, dst: str | Path) -> bool:
    """Copy *src* to *dst* with SHA-256 pre/post verification.

    - If *dst* already exists and checksums match, the copy is skipped.
    - If *dst* exists but checksums differ, the file is copied to a temporary
      sibling, verified, and only then swapped into place atomically.  A copy
      that fails before the swap leaves the previous destination untouched.

    Args:
        src: Source file path.
        dst: Destination file path (parent directory must exist, or is created).

    Returns:
        True only when *dst* holds verified content from *src* at the time of
        the check.  ``False`` on any copy, verification or replacement failure:
        a failure before the swap leaves the previous destination in place, one
        after it may already have changed the destination (see
        :func:`_replace_copy`).  See :func:`_verify_existing` for details.
    """
    src_path = Path(src)
    dst_path = Path(dst)
    if not make_directory(dst_path.parent):
        return False
    dst_existed = dst_path.is_file() or dst_path.is_symlink()
    if dst_existed:
        existing = _verify_existing(src_path, dst_path)
        if existing is True:
            return True
        if existing is False:
            return False
        # existing is None: dst differs or is unreadable; replace it atomically

    return _replace_copy(src_path, dst_path, confirm=dst_existed)
