"""Post-processor for movarr.

Copies completed torrents to the library, applies routing rules (genre/cert/
resolution), marks records as verified in the DB, and optionally removes the
source torrent.

Key improvements over siphonator:
- Routing prefers the cert from ``imdb_cert_source`` (bug #8 fix) — MPAA certs
  are not routed through BBFC rules.
- SHA256 copy verification is delegated to ``file_utils.copy_with_verify()``.
- The ``verified`` flag is set in the DB only after all files copy successfully.
"""

from __future__ import annotations

import ast
import contextlib
import json
import os
import pathlib
import re
import shlex
import signal
import subprocess
import urllib.request
from http.client import HTTPException as HttpClientException
from typing import TYPE_CHECKING

from loguru import logger

from movarr import torrent_client_health
from movarr.file_utils import copy_with_verify, delete_file, make_directory
from movarr.filters import edition_token_set, supersession_quality_score
from movarr.parsing import extract_movie_title, extract_resolution, extract_year, normalise_for_compare, sanitise

if TYPE_CHECKING:
    from movarr.config import Config, CopyLibraryRuleConfig, DefaultCopyLibraryConfig
    from movarr.database import Database, HistoryRecord
    from movarr.qbittorrent import QBittorrentClient

__all__ = ["run_post_processing"]

_BBFC_ORDER = ["U", "PG", "12", "12A", "15", "18", "R18"]
_VIDEO_EXTS = (".mkv", ".mp4", ".avi")
_MAX_VIDEO_FILES_IN_MOVIE_DIR = 4  # safety cap: abort deletion if dir contains more than this many video files
# Hard cap on downloaded poster size (bytes); a hostile or broken poster URL must
# not be able to fill the library volume.
_MAX_POSTER_BYTES = 20 * 1024 * 1024
_RE_PATH_UNSAFE = re.compile(r'[/\\<>:"|?*\x00]|\.\.')
# Known extras/bonus-content markers.  Matching is scoped by _title_remainder(), which
# aligns and removes the movie's own title from the filename first, so a movie whose
# title contains a marker word (e.g. 'Interview with the Vampire') is not misclassified
# as bonus content and its superseded library file can still be deleted.
# 'extras' and 'specials' are deliberately plural-only: their singular forms
# collide with real titles ('Extra') and edition labels ('Special Edition').
_EXTRAS_RE = re.compile(
    r"\b(?:behind[\s_.\-]+the[\s_.\-]+scenes|making[\s_.\-]+of|featurettes?"
    r"|deleted[\s_.\-]+scenes?|interviews?|short[\s_.\-]+films?"
    r"|theatrical[\s_.\-]+trailer|trailers?|samples?"
    r"|bonus(?:es)?|extras|special[\s_.\-]+features?|specials)\b",
    re.IGNORECASE,
)
_BRACKET_RE = re.compile(r"[\[{]([^\]\}]+)[\]\}]")
_LEADING_ARTICLES = frozenset({"a", "an", "the"})


def _hook_timeout_secs(config: Config) -> float | None:
    """Return the hook timeout in seconds from the config, or None for no timeout."""
    mins = config.post_process.hooks.hook_timeout_mins
    return None if mins == 0 else mins * 60.0


def _kill_process(proc: subprocess.Popen, pgid: int | None, label: str, timeout_mins: float | None) -> None:
    """Escalate signals (SIGTERM → SIGKILL) to terminate the process group, then reap."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        with contextlib.suppress(OSError, ProcessLookupError):
            if pgid is not None:
                os.killpg(pgid, sig)
        try:
            proc.communicate(timeout=5)
            break
        except subprocess.TimeoutExpired:
            continue
    # Unconditional SIGKILL to catch TERM-ignoring children that closed their pipes
    with contextlib.suppress(OSError, ProcessLookupError):
        if pgid is not None:
            os.killpg(pgid, signal.SIGKILL)
    # Final reap
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)
    if timeout_mins is not None:
        logger.error("{} hook timed out after {} min.", label, timeout_mins)
    else:
        logger.error("{} hook terminated.", label)


def _log_hook_output(stdout: str, stderr: str, label: str) -> None:
    """Log stdout and stderr from a hook process at DEBUG level if non-empty."""
    if stdout:
        logger.debug("{} hook stdout: {}", label, stdout.rstrip())
    if stderr:
        logger.debug("{} hook stderr: {}", label, stderr.rstrip())


_PLACEHOLDER_RE = re.compile(r"\{(dir|leaf)\}")


def _expand_placeholders(command: str, dir_path: str) -> str:
    """Expand ``{dir}`` and ``{leaf}`` placeholders in a hook command.

    Uses a single regex pass so that the output of one substitution can never
    be re-substituted as another placeholder (avoids double-expansion when
    *dir_path* itself contains the literal text ``{leaf}`` or ``{dir}``).
    """
    subs: dict[str, str] = {
        "dir": shlex.quote(dir_path),
        "leaf": shlex.quote(os.path.basename(dir_path.rstrip("/"))),
    }
    return _PLACEHOLDER_RE.sub(lambda m: subs[m.group(1)], command)


def _run_hook(command: str, dir_path: str, label: str, timeout_secs: float | None = 300.0) -> bool:
    """Run a post-process hook command, substituting ``{dir}`` and ``{leaf}`` with
    *dir_path* and its final component respectively.

    Uses ``shell=True`` so that glob patterns (e.g. ``chattr -i {dir}/*``) are
    expanded by the shell. The command originates from the user's own config
    file, so the trust boundary is the same as the rest of the configuration.

    ``{dir}`` is replaced with a *shell-quoted* form of *dir_path* via
    :func:`shlex.quote`, so the placeholder is already safe for paths that
    contain spaces or shell metacharacters.  Do **not** add extra quotes around
    ``{dir}`` in the template — doing so will produce literal quote characters
    in the expanded command.  Correct: ``chattr -i {dir}/*``.
    Incorrect: ``chattr -i "{dir}/*"``.

    ``{leaf}`` is replaced with a shell-quoted form of the last path
    component of *dir_path* (e.g. the movie folder name).  The same quoting
    rules apply: do not add extra quotes around ``{leaf}``.

    Args:
        command: Shell command template. ``{dir}`` is replaced with a
            shell-quoted form of *dir_path*; ``{leaf}`` with the shell-quoted
            last component. Do not quote either placeholder in the template.
        dir_path: Absolute path of the destination directory.
        label: Hook name for log messages (e.g. ``"pre_delete"``).
        timeout_secs: Seconds to wait for the command before terminating its
            process group; ``None`` waits indefinitely.

    Returns:
        True if the command exits with code 0, False otherwise.

    Important:
        Hooks **must not rename or move** the target files. The ``post_copy``
        hook fires before library supersession; if it renames the newly copied
        primary file, supersession will skip deletion (the primary is no longer
        found). The ``pre_delete`` hook fires before the deletion loop; if it
        renames a library candidate, the loop will report a false-positive
        deletion count. Use hooks only for in-place operations (e.g. ``chattr
        -i``, ``trimarr``).
    """
    cmd = _expand_placeholders(command, dir_path)
    logger.info("Running {} hook: {}", label, cmd)
    proc = subprocess.Popen(  # noqa: S602
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    try:
        pgid = os.getpgid(proc.pid)
    except (OSError, ProcessLookupError):
        pgid = proc.pid  # With start_new_session=True, proc.pid IS the PGID
    try:
        stdout, stderr = proc.communicate(timeout=timeout_secs)
    except subprocess.TimeoutExpired:
        _kill_process(proc, pgid, label, timeout_secs / 60.0 if timeout_secs is not None else None)
        return False
    _log_hook_output(stdout, stderr, label)
    if proc.returncode != 0:
        logger.warning("{} hook failed (exit {}).", label, proc.returncode)
        return False
    logger.debug("{} hook completed (exit 0).", label)
    return True


def _safe_path_component(value: str) -> str:
    """Strip characters that are unsafe in a filesystem path component."""
    return _RE_PATH_UNSAFE.sub("", value).strip()


def run_post_processing(config: Config, qbt: QBittorrentClient, db: Database) -> None:
    """Main post-processing entry point.

    Args:
        config: Application configuration.
        qbt: ``QBittorrentClient`` instance; reachability is checked here.
        db: Open database instance.
    """
    pp_cfg = config.post_process
    if not pp_cfg.post_process_enabled:
        logger.debug("Post-processing disabled; skipping.")
        return

    if not qbt.is_reachable():
        logger.warning("qBittorrent is unreachable; skipping post-processing.")
        torrent_client_health.check_and_notify(is_reachable=False, db=db, config=config)
        return
    torrent_client_health.check_and_notify(is_reachable=True, db=db, config=config)

    completed = qbt.list_completed()
    if not completed:
        logger.debug("No completed torrents to post-process.")
        return

    for torrent in completed:
        # One malformed torrent must never abort the remaining torrents in the cycle.
        try:
            _process_one(torrent, config, qbt, db)
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).error(
                "Post-processing raised for torrent '{}'; continuing with the next torrent.",
                torrent.get("torrent_name") or torrent.get("torrent_tag") or "<unknown>",
            )


# Copy helper


def _copy_files(
    src_files: list[str],
    dst_dir: str,
    largest_fname: str,
    canonical_fname: str,
) -> tuple[bool, set[str]]:
    """Copy *src_files* to *dst_dir*, renaming the largest file to *canonical_fname*.

    Destination names are derived from each source basename, so a torrent that
    holds the same basename in two sub-directories would silently overwrite one
    file with the other — and the source is removed afterwards.  Collisions are
    therefore detected before anything is written and abort the copy.

    Returns ``(all_ok, copied_fnames)``.  Aborts and returns ``False`` on first failure.
    """
    dst_fnames = [
        canonical_fname if os.path.basename(src) == largest_fname else os.path.basename(src) for src in src_files
    ]
    collisions = sorted({name for name in dst_fnames if dst_fnames.count(name) > 1})
    if collisions:
        logger.error(
            "Aborting copy: multiple source files map to the same destination name {}.",
            collisions,
        )
        return False, set()

    copied_fnames: set[str] = set()
    for src_path, dst_fname in zip(src_files, dst_fnames, strict=True):
        dst_path = os.path.join(dst_dir, dst_fname)
        if not copy_with_verify(src_path, dst_path):
            logger.error("Copy/verify failed for '{}'; aborting this torrent.", src_path)
            return False, copied_fnames
        copied_fnames.add(dst_fname)
    return True, copied_fnames


# Per-torrent processing


def _build_dst_dir(db_record: HistoryRecord, dst_base: str) -> str:
    """Build the per-movie destination directory path from the DB record."""
    imdb_title = _safe_path_component(str(db_record.imdb_title or "Unknown")) or "Unknown"
    # Guard single dots (e.g. "." from stripping "...") which os.path.join treats as CWD.
    if not imdb_title.strip("."):
        imdb_title = "Unknown"
    imdb_year = _safe_path_component(str(db_record.imdb_year or ""))
    folder_name = f"{imdb_title} ({imdb_year})" if imdb_year else imdb_title
    return os.path.join(dst_base, folder_name)


def _run_post_copy_hook(config: Config, resolved_dst_dir: str) -> None:
    """Run the post_copy hook if configured, logging failures without raising."""
    if not config.post_process.hooks.post_copy:
        return
    try:
        if not _run_hook(
            config.post_process.hooks.post_copy,
            resolved_dst_dir,
            "post_copy",
            _hook_timeout_secs(config),
        ):
            logger.warning("post_copy hook failed for '{}'; continuing.", resolved_dst_dir)
    except Exception as exc:  # noqa: BLE001
        logger.warning("post_copy hook raised an exception for '{}': {}; continuing.", resolved_dst_dir, exc)


def _maybe_delete_superseded(
    config: Config,
    dst_dir: str,
    dst_base: str,
    canonical_fname: str,
    copied_fnames: set[str],
    release_san: str = "",
    raw_release_name: str = "",
) -> None:
    """Delete lower-quality superseded files if configured.

    *release_san* is the sanitised release name (the index title); the title,
    scoring and extras-primary decisions use it rather than *canonical_fname*:
    the canonical name is just ``<folder><ext>`` and carries no quality or
    edition tokens, so using it made every library release look better and
    silently disabled the whole pass.  *raw_release_name* is the un-sanitised
    release name, used for edition evidence because
    :func:`movarr.parsing.sanitise` strips bracketed markers such as
    ``[Extended]``.
    """
    if not config.post_process.delete_lower_quality or canonical_fname not in copied_fnames:
        return
    library_roots = [pathlib.Path(p).resolve() for p in (config.general.library_path_list or [])]
    resolved_dst = pathlib.Path(dst_dir).resolve()
    if not any(resolved_dst == root or resolved_dst.is_relative_to(root) for root in library_roots):
        # The acquisition-time comparison only walked the configured library
        # roots, so a destination outside them was never proven better.
        logger.debug(
            "Skipping superseded-file deletion: '{}' is outside general.library_path_list.",
            resolved_dst,
        )
        return
    # An empty library_path_list is covered by the root check above: with no
    # roots, nothing is inside the library, so deletion never runs.
    deleted = _delete_superseded_files(
        dst_dir,
        dst_base,
        canonical_fname,
        config,
        copied_fnames=frozenset(copied_fnames),
        release_san=release_san,
        raw_release_name=raw_release_name,
    )
    if deleted:
        logger.info("Auto-deleted {} lower-quality file(s) from '{}'.", deleted, dst_dir)


def _post_copy_actions(
    config: Config,
    tag: str,
    db: Database,
    qbt: QBittorrentClient,
    torrent_hash: str,
    dst_dir: str,
    dst_base: str,
    resolved_dst_dir: str,
    canonical_fname: str,
    copied_fnames: set[str],
    torrent_name: str = "",
    db_record: HistoryRecord | None = None,
) -> None:
    """Mark the torrent completed and run any configured post-copy operations."""
    db.mark_completed(tag)
    logger.info("Marked tag '{}' as completed.", tag)
    _run_post_copy_hook(config, resolved_dst_dir)
    # Save poster art
    if db_record and config.post_process.poster_art.filename:
        _save_poster_art(db_record, dst_dir, config)
    _maybe_delete_superseded(
        config,
        dst_dir,
        dst_base,
        canonical_fname,
        copied_fnames,
        release_san=sanitise(torrent_name) or "",
        raw_release_name=torrent_name,
    )
    if config.post_process.remove_completed:
        qbt.delete_torrent(torrent_hash, delete_data=True, state="completed", name=torrent_name)


def _make_safe_poster_path(filename: str) -> str:
    """Sanitise *filename* to a .jpg path component."""
    stem = os.path.basename(os.path.splitext(filename)[0])
    return f"{stem}.jpg" if stem else "poster.jpg"


def _user_agent() -> str:
    """Return the poster-download User-Agent, derived from the installed version."""
    try:
        from importlib.metadata import version  # noqa: PLC0415

        return f"movarr/{version('movarr')}"
    except Exception:  # noqa: BLE001
        # Package metadata is unavailable (e.g. running from a source tree).
        return "movarr"


def _download_poster_art(resolved_url: str, dst_path: str) -> None:
    """Download poster image from *resolved_url* to *dst_path*.

    Logs and returns silently on any failure — does NOT abort post-processing.
    """
    try:
        req = urllib.request.Request(
            resolved_url,
            headers={"User-Agent": _user_agent()},
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            content_type = (response.headers.get("Content-Type") or "").strip().lower()
            if not content_type or not content_type.startswith("image/"):
                logger.warning("Poster URL returned non-image content '{}'; skipping.", content_type)
                return
            payload = response.read(_MAX_POSTER_BYTES + 1)
        if len(payload) > _MAX_POSTER_BYTES:
            logger.warning("Poster at '{}' exceeds {} bytes; skipping.", resolved_url, _MAX_POSTER_BYTES)
            return
        with open(dst_path, "wb") as f:
            f.write(payload)
        logger.info("Saved poster art to '{}'.", dst_path)
    except (OSError, ValueError, HttpClientException) as exc:
        logger.warning("Failed to download/save poster art from '{}': {}.", resolved_url, exc)


def _save_poster_art(
    db_record: HistoryRecord,
    dst_dir: str,
    config: Config,
) -> None:
    """Download poster art to *dst_dir* with configured name/resolution.

    Logs and returns silently on any failure — does NOT abort post-processing.
    """
    poster_cfg = config.post_process.poster_art
    filename = poster_cfg.filename or ""
    if not filename:
        return

    safe_name = _make_safe_poster_path(filename)

    poster_url = str(db_record.imdb_poster_url or "")
    if not poster_url:
        logger.debug("No poster URL for '{}'; skipping poster save.", db_record.imdb_title)
        return

    from movarr.notifications import _poster_url_with_width  # noqa: PLC0415

    resolved_url = _poster_url_with_width(poster_url, poster_cfg.download_width)
    dst_path = os.path.join(dst_dir, safe_name)
    _download_poster_art(resolved_url, dst_path)


def _torrent_tag_and_hash(torrent: dict) -> tuple[str, str]:
    """Return (tag, torrent_hash) from *torrent*, defaulting both to empty string."""
    return torrent.get("torrent_tag") or "", torrent.get("torrent_hash") or ""


def _build_copy_target(
    torrent: dict,
    db_record: HistoryRecord,
    config: Config,
) -> tuple[list[str], str, str] | None:
    """Return *(src_files, dst_base, dst_dir)* or None if processing should be skipped.

    Builds the file copy list, resolves the destination, creates the directory.
    Logs and returns None for any condition that should abort processing.
    """
    src_files = _build_copy_list(torrent, config)
    if not src_files:
        logger.debug("No files to copy for tag '{}'.", torrent.get("torrent_tag", ""))
        return None
    dst_base = _resolve_destination(db_record, config)
    if not dst_base:
        logger.warning(
            "Could not resolve copy destination for tag '{}'; skipping.",
            torrent.get("torrent_tag", ""),
        )
        return None
    dst_dir = _build_dst_dir(db_record, dst_base)
    save_path = str(torrent.get("torrent_save_path") or "")
    if save_path:
        resolved_save = pathlib.Path(save_path).resolve()
        resolved_dst = pathlib.Path(dst_dir).resolve()
        if resolved_dst == resolved_save or resolved_dst.is_relative_to(resolved_save):
            logger.error(
                "Destination '{}' is inside the torrent save path '{}'; refusing to copy"
                + (
                    " (remove_completed would delete the library copy)."
                    if config.post_process.remove_completed
                    else "."
                ),
                resolved_dst,
                resolved_save,
            )
            return None
    if not make_directory(dst_dir):
        logger.error("Cannot create destination directory '{}'; skipping.", dst_dir)
        return None
    return src_files, dst_base, dst_dir


def _process_one_no_copy(
    config: Config,
    qbt: QBittorrentClient,
    db: Database,
    tag: str,
    torrent_hash: str,
    db_record: HistoryRecord | None,
) -> None:
    """Handle the case where copy_completed is disabled."""
    logger.debug("copy_completed is False; skipping copy for tag '{}'", tag)
    db.mark_completed(tag)
    if db_record and config.post_process.poster_art.filename:
        poster_dst_base = _resolve_destination(db_record, config)
        if poster_dst_base:
            poster_dst_dir = _build_dst_dir(db_record, poster_dst_base)
            if make_directory(poster_dst_dir):
                _save_poster_art(db_record, poster_dst_dir, config)
    if config.post_process.remove_completed:
        qbt.delete_torrent(
            torrent_hash,
            delete_data=False,
            state="completed",
            name=str(db_record.index_title or "") if db_record else "",
        )


def _is_record_completed(db_record: HistoryRecord) -> bool:
    """Return True when the history row was already post-processed."""
    return str(db_record.result or "") == "Completed" or str(db_record.verified or "") == "true"


def _retry_removal(
    config: Config,
    qbt: QBittorrentClient,
    tag: str,
    torrent_hash: str,
    db_record: HistoryRecord,
) -> None:
    """Remove a completed torrent whose earlier removal did not happen."""
    if not config.post_process.remove_completed:
        logger.debug("Torrent '{}' was already post-processed; skipping.", tag)
        return
    logger.debug("Torrent '{}' was already post-processed; retrying removal.", tag)
    # delete_data=False: this row may have been completed with copy_completed
    # disabled (the download IS the library copy), and the current config cannot
    # prove otherwise.  Never destroy data here.
    qbt.delete_torrent(
        torrent_hash,
        delete_data=False,
        state="completed",
        name=str(db_record.index_title or ""),
    )


def _copy_phase(
    torrent: dict,
    config: Config,
    db: Database,
    qbt: QBittorrentClient,
    tag: str,
    torrent_hash: str,
    db_record: HistoryRecord,
) -> None:
    """Copy the torrent into the library and run the post-copy actions."""
    target = _build_copy_target(torrent, db_record, config)
    if target is None:
        return
    src_files, dst_base, dst_dir = target
    resolved_dst_dir = str(pathlib.Path(dst_dir).resolve())

    if not _run_pre_copy_hook(config, resolved_dst_dir, dst_dir):
        return

    # Determine canonical filename for the largest file (rename to parent-dir style).
    largest_fname, largest_rel_path = _largest_file(torrent)
    canonical_fname = _canonical_filename(largest_fname, largest_rel_path)

    all_ok, copied_fnames = _copy_files(src_files, dst_dir, largest_fname, canonical_fname)
    if not all_ok:
        return

    if copied_fnames and not any(name.lower().endswith(_VIDEO_EXTS) for name in copied_fnames):
        # Only non-video files (subtitles, artwork) were copied: marking the
        # record Completed would let remove_completed delete the only copy of
        # the feature, which the exclusion rules dropped.
        logger.warning(
            "No video file was copied for tag '{}'; marking the record completed so the "
            "torrent is not re-processed every cycle. Its data is left untouched.",
            tag,
        )
        db.mark_completed(tag)
        return

    _post_copy_actions(
        config,
        tag,
        db,
        qbt,
        torrent_hash,
        dst_dir,
        dst_base,
        resolved_dst_dir,
        canonical_fname,
        copied_fnames,
        torrent_name=str(db_record.index_title or ""),
        db_record=db_record,
    )


def _process_one(
    torrent: dict,
    config: Config,
    qbt: QBittorrentClient,
    db: Database,
) -> None:
    """Process a single completed torrent: copy to library, save poster, clean up."""
    tag, torrent_hash = _torrent_tag_and_hash(torrent)

    db_record = db.find_by_tag(tag)
    if not db_record:
        logger.warning("No DB record for tag '{}'; skipping.", tag)
        return

    # A completed torrent can stay in qBittorrent indefinitely (remove_completed:
    # false), so a record that is already Completed must not be copied, hashed,
    # poster-downloaded or hook-run again on every cycle.
    if _is_record_completed(db_record):
        _retry_removal(config, qbt, tag, torrent_hash, db_record)
        return

    # If copy_completed is disabled, handle the non-copy path and return.
    if not config.post_process.copy_completed:
        _process_one_no_copy(config, qbt, db, tag, torrent_hash, db_record)
        return

    _copy_phase(torrent, config, db, qbt, tag, torrent_hash, db_record)


# File-list helpers


def _compile_exclusion_regexes(patterns: list[str] | None, label: str) -> list[re.Pattern[str]]:
    """Compile *patterns* into case-insensitive regexes, logging a warning on error."""
    result: list[re.Pattern[str]] = []
    for r in patterns or []:
        try:
            result.append(re.compile(r, re.IGNORECASE))
        except re.error:
            logger.warning("Invalid {} regex '{}'; skipping.", label, r)
    return result


def _path_escapes_save_root(abs_path: str, save_root: pathlib.Path) -> bool | None:
    """Return True if *abs_path* escapes *save_root*, None on OS error, False if safe."""
    try:
        return not pathlib.Path(abs_path).resolve().is_relative_to(save_root)
    except (ValueError, OSError):
        return None


def _file_excluded_by_rules(
    rel_path: str,
    folder_part: str,
    file_size_kb: int,
    exclude_file_regexes: list[re.Pattern[str]],
    exclude_folder_regexes: list[re.Pattern[str]],
    exclude_min_kb: int,
) -> bool:
    """Return True if this file matches any exclusion rule and should be skipped."""
    if any(rx.search(rel_path) for rx in exclude_file_regexes):
        logger.debug("Excluding file '{}' (file regex match).", rel_path)
        return True
    if any(rx.search(folder_part) for rx in exclude_folder_regexes):
        logger.debug("Excluding folder '{}' (folder regex match).", folder_part)
        return True
    if exclude_min_kb and file_size_kb < exclude_min_kb:
        logger.debug("Excluding '{}' ({}KB < {}KB min).", rel_path, file_size_kb, exclude_min_kb)
        return True
    return False


def _should_include_file(
    file_dict: dict,
    save_path: str,
    save_root: pathlib.Path,
    exclude_file_regexes: list[re.Pattern[str]],
    exclude_folder_regexes: list[re.Pattern[str]],
    exclude_min_kb: int,
) -> str | None:
    """Return the absolute path for *file_dict* if it should be copied, else None."""
    # Normalise Windows separators first: on POSIX a name like
    # ``..\\..\\etc\\passwd`` is a single literal component, so validating it
    # before normalisation let it through and the conversion below then made it
    # a real traversal.
    rel_path = (file_dict.get("file_name") or "").replace("\\", "/")
    if not rel_path:
        logger.debug("Skipping file entry with empty name.")
        return None
    abs_path = os.path.join(save_path, rel_path)
    folder_part = os.path.dirname(rel_path)

    # Guard against crafted torrent paths that escape the save directory.
    escapes = _path_escapes_save_root(abs_path, save_root)
    if escapes is None:
        logger.warning("Skipping '{}': could not resolve path.", rel_path)
        return None
    if escapes:
        logger.warning("Skipping '{}': path escapes save directory.", rel_path)
        return None

    file_size = file_dict.get("file_size") or 0
    file_size_kb = file_size >> 10
    if _file_excluded_by_rules(
        rel_path, folder_part, file_size_kb, exclude_file_regexes, exclude_folder_regexes, exclude_min_kb
    ):
        return None

    return abs_path


def _build_copy_list(torrent: dict, config: Config) -> list[str]:
    """Return absolute paths for files that should be copied to the library."""
    pp = config.post_process
    save_path = torrent.get("torrent_save_path") or ""

    # Guard: an empty save_path resolves to CWD, bypassing the path-traversal
    # check below.  Reject early to avoid accidentally copying CWD-relative paths.
    if not save_path:
        tag = torrent.get("torrent_tag", "unknown")
        logger.warning("torrent_save_path is empty for tag '{}'; skipping copy.", tag)
        return []

    file_list = torrent.get("torrent_file_list") or []
    exclude_min_kb = pp.exclude_file_min_kb or 0
    exclude_file_regexes = _compile_exclusion_regexes(pp.exclude_file_regex_list, "file-exclude")
    exclude_folder_regexes = _compile_exclusion_regexes(pp.exclude_folder_regex_list, "folder-exclude")
    save_root = pathlib.Path(save_path).resolve()

    result: list[str] = []
    for file_dict in file_list:
        abs_path = _should_include_file(
            file_dict, save_path, save_root, exclude_file_regexes, exclude_folder_regexes, exclude_min_kb
        )
        if abs_path is not None:
            result.append(abs_path)
    return result


def _largest_file(torrent: dict) -> tuple[str, str]:
    """Return (filename, relative_path_dir) of the largest file in the torrent."""
    file_list = torrent.get("torrent_file_list") or []
    if not file_list:
        return "", ""
    biggest = max(file_list, key=lambda f: f.get("file_size") or 0)
    rel_path = biggest.get("file_name") or ""
    # Normalise Windows backslashes before path parsing.
    rel_path = rel_path.replace("\\", "/")
    return os.path.basename(rel_path), os.path.dirname(rel_path)


def _canonical_filename(largest_fname: str, largest_path_dir: str) -> str:
    """Derive the best destination filename for the primary video file.

    If the torrent has a non-trivial parent directory name that is longer than
    the filename, rename the file to ``<parent_dir><ext>`` (siphonator convention).
    """
    if not largest_fname.lower().endswith(_VIDEO_EXTS):
        return largest_fname

    first_level = _first_level_dir(largest_path_dir)
    if not first_level:
        return largest_fname

    parent_san = sanitise(first_level)
    if not parent_san:
        return largest_fname

    if len(parent_san) < len(largest_fname):
        return largest_fname

    ext = pathlib.Path(largest_fname).suffix
    return f"{parent_san}{ext}"


def _first_level_dir(path: str) -> str:
    """Return the top-level directory component of a relative path."""
    parts = pathlib.PurePosixPath(path).parts
    return parts[0] if parts else ""


# Destination routing


def _resolve_destination(db_record: HistoryRecord, config: Config) -> str | None:
    """Choose the library copy path based on genre, cert, and resolution rules.

    ``copy_library_rules`` is the primary routing mechanism and
    ``default_copy_library`` is documented as the fallback, so a rules-only
    configuration must still resolve a destination.  The guard therefore runs
    against the *resolved* path, not against the default paths alone.
    """
    pp = config.post_process
    genres = _parse_genres(db_record.imdb_genres_list or [])
    cert: str = str(db_record.imdb_certification or "")
    # Default to empty string (not "imdbpie") so legacy/unknown rows
    # do not accidentally get BBFC routing applied.
    cert_source: str = str(db_record.imdb_cert_source or "")
    # Bug #8 fix: only apply BBFC cert routing if the cert came from imdbpie (UK BBFC certs).
    # OMDb returns MPAA ratings which are not compatible with BBFC ordering.
    effective_cert = cert if cert_source == "imdbpie" else ""
    resolution = _resolution_from_index_title(str(db_record.index_title or ""))
    destination = _pick_path(genres, effective_cert, resolution, pp.copy_library_rules, pp.default_copy_library)
    if not destination:
        logger.warning(
            "Cannot copy: no 'copy_library_rules' entry matched and no 'default_copy_library' path is configured."
        )
    return destination


def _get_default_path(default: DefaultCopyLibraryConfig, path_key: str) -> str | None:
    """Return the default library path for *path_key*, falling back to hd_path for UHD."""
    primary = getattr(default, path_key) or None
    if primary is None and path_key == "uhd_path":
        return default.hd_path or None
    return primary


def _genre_overlap_score(rule: CopyLibraryRuleConfig, genres_lower: set[str]) -> int:
    """Return the number of genres in *rule* that overlap with *genres_lower*."""
    return len({g.lower() for g in rule.genres} & genres_lower)


def _find_top_rules(
    scored: list[tuple[int, CopyLibraryRuleConfig]],
) -> list[CopyLibraryRuleConfig]:
    """Return all rules tied at the maximum score."""
    max_score = max(s for s, _ in scored)
    return [r for s, r in scored if s == max_score]


def _select_best_rule(
    genres: list[str],
    rules: list[CopyLibraryRuleConfig],
) -> CopyLibraryRuleConfig | None:
    """Return the single best-matching rule by genre overlap, or None if tied/no match."""
    genres_lower = {g.lower() for g in genres}
    scored: list[tuple[int, CopyLibraryRuleConfig]] = []
    for rule in rules:
        s = _genre_overlap_score(rule, genres_lower)
        if s > 0:
            scored.append((s, rule))
    if not scored:
        return None
    top_rules = _find_top_rules(scored)
    if len(top_rules) != 1:
        logger.info("Genres {} tied across rules {}; using default path.", genres, [r.name for r in top_rules])
        return None
    return top_rules[0]


def _get_rule_path(
    rule: CopyLibraryRuleConfig,
    path_key: str,
    cert: str,
    default: DefaultCopyLibraryConfig,
) -> str | None:
    """Return the library path for *rule* respecting *cert* and *path_key*, or the default."""
    if rule.max_certification and not _cert_acceptable(cert, rule.max_certification):
        logger.info(
            "Cert '{}' fails max_cert '{}' for rule '{}'; using default.", cert, rule.max_certification, rule.name
        )
        return _get_default_path(default, path_key)
    path = getattr(rule, path_key, "") or ""
    if not path and path_key == "uhd_path":
        path = getattr(rule, "hd_path", "") or ""
    if not path:
        logger.warning("Rule '{}' has no '{}'; using default.", rule.name, path_key)
        return _get_default_path(default, path_key)
    return path


def _pick_path(
    genres: list[str],
    cert: str,
    resolution: str | None,
    rules: list[CopyLibraryRuleConfig],
    default: DefaultCopyLibraryConfig,
) -> str | None:
    path_key = "uhd_path" if resolution in ("2160", "4k") else "hd_path"
    rule = _select_best_rule(genres, rules)
    if rule is None:
        return _get_default_path(default, path_key)
    return _get_rule_path(rule, path_key, cert, default)


def _cert_acceptable(movie_cert: str, max_cert: str) -> bool:
    mc = (movie_cert or "").strip().upper()
    mx = (max_cert or "").strip().upper()
    if mc not in _BBFC_ORDER or mx not in _BBFC_ORDER:
        return False
    return _BBFC_ORDER.index(mc) <= _BBFC_ORDER.index(mx)


def _resolution_from_index_title(index_title: str) -> str | None:
    san = sanitise(index_title)
    if not san:
        return None
    return extract_resolution(san)


def _coerce_genre_list(items: object) -> list[str]:
    """Return *items* coerced to a list of stripped strings."""
    return [str(g).strip() for g in items]  # type: ignore[attr-defined]


def _parse_genres(raw: object) -> list[str]:
    if isinstance(raw, list):
        return _coerce_genre_list(raw)
    if not isinstance(raw, (str, bytes, bytearray)):
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return _coerce_genre_list(parsed)
    except (ValueError, TypeError):
        pass
    try:
        parsed = ast.literal_eval(raw if isinstance(raw, str) else raw.decode())
        if isinstance(parsed, list):
            return _coerce_genre_list(parsed)
    except (ValueError, SyntaxError):
        pass
    return []


# Library supersession


def _check_directory_safety(resolved_dst: pathlib.Path, resolved_base: pathlib.Path) -> bool:
    """Return True iff *resolved_dst* is a direct child of *resolved_base*.

    A failed check means the directory depth guard has triggered and the caller
    should abort deletion immediately.
    """
    return resolved_dst.parent == resolved_base


def _list_and_validate_entries(
    resolved_dst_dir: pathlib.Path,
    dst_dir: str,
    new_primary_fname: str,
) -> list[str] | None:
    """List directory entries and validate them; return video file list or None to abort."""
    try:
        entries = os.listdir(resolved_dst_dir)
    except OSError:
        logger.error("Could not list directory '{}'; skipping auto-delete.", dst_dir)
        return None
    if new_primary_fname not in entries:
        logger.warning(
            "Auto-delete skipped: primary file '{}' not found in '{}'; "
            "cannot safely distinguish new from old library files.",
            new_primary_fname,
            dst_dir,
        )
        return None
    video_files = [f for f in entries if f.lower().endswith(_VIDEO_EXTS)]
    if len(video_files) > _MAX_VIDEO_FILES_IN_MOVIE_DIR:
        logger.warning(
            "Auto-delete skipped: {} video files in '{}' exceeds max {}; no files deleted.",
            len(video_files),
            dst_dir,
            _MAX_VIDEO_FILES_IN_MOVIE_DIR,
        )
        return None
    return video_files


def _check_delete_preconditions(
    dst_dir: str,
    dst_base: str,
    new_primary_fname: str,
) -> tuple[list[str], pathlib.Path] | None:
    """Run safety checks; return *(video_files, resolved_dst)* or *None* to abort."""
    if not os.path.isdir(dst_dir):
        return None
    if not new_primary_fname.lower().endswith(_VIDEO_EXTS):
        logger.debug(
            "Auto-delete skipped: primary file '{}' is not a recognised video format.",
            new_primary_fname,
        )
        return None
    resolved_dst = pathlib.Path(dst_dir).resolve()
    resolved_base = pathlib.Path(dst_base).resolve()
    if not _check_directory_safety(resolved_dst, resolved_base):
        logger.error(
            "Auto-delete safety check failed: '{}' is not a direct child of '{}'; skipping.",
            dst_dir,
            dst_base,
        )
        return None
    video_files = _list_and_validate_entries(resolved_dst, dst_dir, new_primary_fname)
    if video_files is None:
        return None
    return video_files, resolved_dst


def _title_remainder(lib_san: str, dir_san: str) -> str | None:
    """Return the part of *lib_san* that follows the movie's own title.

    The file's title region is aligned with the library folder title, tolerating a
    release that drops a leading article (folder 'The Making of a Lady' vs file
    'Making of a Lady 2012').  The title must be followed by a word separator, so a
    short title such as 'The' cannot match the start of an unrelated word like
    'Theodore'.

    Returns *None* when the title cannot be aligned, which means the caller cannot
    tell where the movie's own name ends in the filename.
    """
    title = extract_movie_title(dir_san) or ""
    if not title:
        return None
    variants = [title]
    head, _, tail = title.partition(" ")
    if tail and head.lower() in _LEADING_ARTICLES:
        variants.append(tail)
    lowered = lib_san.lower()
    for variant in variants:
        if not lowered.startswith(variant.lower()):
            continue
        remainder = lib_san[len(variant) :]
        if not remainder or remainder.startswith(" "):
            return remainder
    return None


def _marker_outside_title(lib_san: str, dir_san: str) -> bool:
    """Return True if *lib_san* holds an extras marker that is not part of the title.

    Used when the movie's title could not be aligned with the filename, so the name
    may still contain it.  A marker whose matched text appears in the movie's own
    name (e.g. 'Interview' in 'Interview with the Vampire') belongs to that title,
    not to bonus content.
    """
    haystack = dir_san.lower()
    return any(match.group(0).lower() not in haystack for match in _EXTRAS_RE.finditer(lib_san.lower()))


def _is_extras_file(fname: str, lib_san: str, movie_dir_name: str) -> bool:
    """Return True if *fname* looks like extras/bonus content for *movie_dir_name*."""
    lib_bracket = " ".join(_BRACKET_RE.findall(fname))
    if lib_bracket and _EXTRAS_RE.search(lib_bracket.lower()):
        return True
    dir_san = sanitise(movie_dir_name) or ""
    remainder = _title_remainder(lib_san, dir_san)
    if remainder is not None:
        # The movie's own title has been removed, so any marker left in the
        # remainder is release-name metadata and counts wherever it appears.
        return bool(_EXTRAS_RE.search(remainder))
    return _marker_outside_title(lib_san, dir_san)


def _is_extras_primary(new_primary_fname: str, new_san: str, movie_dir_name: str) -> bool:
    """Return True if *new_primary_fname* looks like extras/bonus content for *movie_dir_name*.

    Delegates to ``_is_extras_file`` since the logic is identical.
    """
    return _is_extras_file(new_primary_fname, new_san, movie_dir_name)


def _run_pre_copy_hook(config: Config, resolved_dst_dir: str, dst_dir: str) -> bool:
    """Run the pre_copy hook if configured.  Return False to abort the copy."""
    if not config.post_process.hooks.pre_copy:
        return True
    try:
        if not _run_hook(
            config.post_process.hooks.pre_copy,
            resolved_dst_dir,
            "pre_copy",
            _hook_timeout_secs(config),
        ):
            logger.error("pre_copy hook failed for '{}'; aborting copy.", dst_dir)
            return False
    except Exception as exc:  # noqa: BLE001
        logger.error("pre_copy hook raised an exception for '{}': {}; aborting copy.", dst_dir, exc)
        return False
    return True


def _run_pre_delete_hook_and_verify(
    config: Config,
    resolved_dst: pathlib.Path,
    dst_dir: str,
    new_primary_fname: str,
) -> bool:
    """Run the pre_delete hook and verify the primary file remains.  Return False to abort."""
    if not config.post_process.hooks.pre_delete:
        return True
    try:
        if not _run_hook(
            config.post_process.hooks.pre_delete,
            str(resolved_dst),
            "pre_delete",
            _hook_timeout_secs(config),
        ):
            logger.error("pre_delete hook failed for '{}'; aborting deletion pass.", dst_dir)
            return False
    except Exception as exc:  # noqa: BLE001
        logger.error("pre_delete hook raised an exception for '{}': {}; aborting deletion pass.", dst_dir, exc)
        return False
    try:
        remaining = os.listdir(str(resolved_dst))
    except OSError:
        logger.error(
            "pre_delete hook appears to have removed or made unreadable '{}'; aborting deletion pass.",
            dst_dir,
        )
        return False
    if new_primary_fname not in remaining:
        logger.warning(
            "pre_delete hook appears to have renamed the primary file '{}'; aborting deletion pass.",
            new_primary_fname,
        )
        return False
    return True


def _run_post_delete_hook(config: Config, resolved_dst: pathlib.Path, dst_dir: str) -> None:
    """Run the post_delete hook if configured; log warnings on failure."""
    if not config.post_process.hooks.post_delete:
        return
    try:
        if not _run_hook(
            config.post_process.hooks.post_delete,
            str(resolved_dst),
            "post_delete",
            _hook_timeout_secs(config),
        ):
            logger.warning("post_delete hook failed for '{}'; continuing.", dst_dir)
    except Exception as exc:  # noqa: BLE001
        logger.warning("post_delete hook raised an exception for '{}': {}; continuing.", dst_dir, exc)


def _delete_superseded_files(
    dst_dir: str,
    dst_base: str,
    new_primary_fname: str,
    config: Config,
    *,
    copied_fnames: frozenset[str] = frozenset(),
    release_san: str = "",
    raw_release_name: str = "",
) -> int:
    """Delete superseded video files in *dst_dir* after a new copy.

    A video file is deleted only when *all* of these hold:

    - it is not the newly-copied primary file and was not written by this run
      (*copied_fnames*);
    - it belongs to the same film (title, plus year when both parse);
    - it does not match the extras/bonus-content patterns;
    - its edition set equals the new release's (different cuts are preserved);
    - its quality is parseable **and** does not outrank the new release.

    The last two conditions are a deliberate belt-and-braces layer on top of the
    upstream library check (:func:`movarr.filters._check_library_canonical`):
    a file added after the search ran, or one whose quality cannot be parsed,
    must never be removed.  Every guard here can only prevent a deletion.

    Two hard-stop safety guards protect against runaway deletion:
    1. *Depth check*: ``dst_dir`` must be a direct child of ``dst_base``.
    2. *Count cap*: if the directory holds more than
       ``_MAX_VIDEO_FILES_IN_MOVIE_DIR`` video files, abort.

    Args:
        dst_dir: Absolute path to the per-movie destination directory.
        dst_base: Absolute path to the configured library base directory.
        new_primary_fname: Filename of the newly copied primary video.
        config: Application configuration.
        copied_fnames: All destination filenames written during this torrent run.
            Every filename in this set — including ``new_primary_fname`` — is
            protected from deletion.
        release_san: Sanitised release name for the new copy; used for the
            extras-primary, title, resolution and quality comparisons.
        raw_release_name: Un-sanitised release name; supplies the edition tokens
            because ``sanitise()`` strips bracketed markers such as ``[Extended]``.

    Returns:
        Number of files successfully deleted.
    """
    preconditions = _check_delete_preconditions(dst_dir, dst_base, new_primary_fname)
    if preconditions is None:
        return 0
    video_files, resolved_dst = preconditions

    # Identity for scoring/edition/extras comes from the *release* name: the
    # canonical destination name ("<folder><ext>") carries no quality or edition
    # tokens, so using it made every library release outrank the new copy.
    new_san = release_san or sanitise(new_primary_fname) or ""
    # Edition/extras evidence comes from the un-sanitised release text: sanitise()
    # strips "[Extended]"/"[Featurettes]" markers before they can be compared.
    raw_release = raw_release_name or f"{release_san} {new_primary_fname}".strip() or new_san
    # The extras *primary* decision keeps using the sanitised form: its title
    # alignment logic (which prevents "Making Of A Monarch" being read as a
    # bonus feature) depends on it.
    if _is_extras_primary(new_primary_fname, new_san, resolved_dst.name):
        logger.debug(
            "Auto-delete skipped: new primary '{}' is bonus/extras content.",
            new_primary_fname,
        )
        return 0

    if not _run_pre_delete_hook_and_verify(config, resolved_dst, dst_dir, new_primary_fname):
        return 0

    protected = frozenset(copied_fnames) | {new_primary_fname}
    deleted = _delete_superseded_loop(video_files, resolved_dst, protected, new_san, raw_release, config)

    _run_post_delete_hook(config, resolved_dst, dst_dir)
    return deleted


def _title_key(sanitised: str) -> tuple[str, ...]:
    """Return a comparable word tuple for the title part of *sanitised*.

    Leading articles are dropped and each word is normalised, so
    ``"The Making of a Lady"`` and ``"Making of a Lady"`` compare equal while a
    sequel (``"The Matrix Reloaded"``) does not.
    """
    title = extract_movie_title(sanitised) or sanitised
    words = [normalise_for_compare(word) or word for word in title.lower().split()]
    while words and words[0] in _LEADING_ARTICLES:
        words.pop(0)
    return tuple(word for word in words if word)


def _same_title(new_san: str, lib_san: str, folder_title: tuple[str, ...]) -> bool:
    """Return True when a library file belongs to the same film as the new copy.

    Identity must be positive, not merely "same year": a per-movie folder can
    hold a second feature released the same year.  A file qualifies only when
    its title matches the new primary's title, or the destination folder's
    title (``lib`` abbreviations such as ``IWTV`` cannot be reconciled, so they
    are preserved).  A filename whose title cannot be resolved never authorises
    a deletion.

    Release years are still compared: a remake ("The Thing" 1982 vs 2011) is a
    different film even though its title matches.
    """
    new_key = _title_key(new_san)
    lib_key = _title_key(lib_san)
    if not new_key or not lib_key:
        return False
    new_year = extract_year(new_san)
    lib_year = extract_year(lib_san)
    if new_year and lib_year and new_year != lib_year:
        return False
    return lib_key == new_key or (bool(folder_title) and lib_key == folder_title)


def _delete_superseded_loop(
    video_files: list[str],
    resolved_dst: pathlib.Path,
    protected: frozenset[str],
    new_san: str,
    raw_release: str,
    config: Config,
) -> int:
    """Delete files in *video_files* that are not *protected* and not extras.

    Five protections apply, each of which must hold before a file is removed:

    - the file must belong to the same title as the new primary (a per-movie
      directory can legitimately hold a sequel or a second feature);
    - it must not match the extras/bonus-content patterns;
    - its edition set must equal the new primary's — different cuts
      (Theatrical vs Director's Cut) must never be deleted;
    - its resolution must not exceed the new primary's;
    - its quality must be parseable and strictly worse than the new primary's.

    The quality precondition (*the new copy is better*) is not inherited from
    the caller: :func:`_maybe_delete_superseded` only establishes that the
    destination is inside ``general.library_path_list`` (so the
    acquisition-time comparison covered it), and
    :func:`_is_superseded_candidate` re-checks resolution and score per file.
    """
    new_editions = edition_token_set(raw_release or new_san)
    folder_title = _title_key(sanitise(resolved_dst.name) or resolved_dst.name)
    deleted = 0
    for fname in video_files:
        if fname in protected:
            continue
        if not _is_superseded_candidate(fname, new_san, new_editions, folder_title, resolved_dst.name, config):
            continue
        lib_path = str(resolved_dst / fname)
        if delete_file(lib_path):
            logger.info("Auto-deleted superseded library file '{}'.", lib_path)
            deleted += 1
        else:
            logger.error("Failed to auto-delete superseded library file '{}'.", lib_path)
    return deleted


def _is_superseded_candidate(
    fname: str,
    new_san: str,
    new_editions: frozenset[str],
    folder_title: tuple[str, ...],
    folder_name: str,
    config: Config,
) -> bool:
    """Return True when *fname* is provably worse than the new copy and may be deleted.

    Every check is a veto applied in order: title identity, extras markers,
    edition set, resolution, unparseable quality, then a final score comparison.
    A False result is always the safe outcome.
    """
    lib_san = sanitise(fname) or ""
    if not _same_title(new_san, lib_san, folder_title):
        logger.debug("Skipping auto-delete for '{}': different title to the new file.", fname)
        return False
    if _is_extras_file(fname, lib_san, folder_name):
        logger.debug("Skipping auto-delete for '{}': looks like extra/bonus content.", fname)
        return False
    if not _same_edition(fname, new_editions):
        return False
    if _library_resolution_is_higher(fname, new_san):
        return False
    return _library_quality_is_worse(fname, lib_san, new_san, config)


def _same_edition(fname: str, new_editions: frozenset[str]) -> bool:
    """Return True when *fname* carries the same edition tags as the new copy."""
    lib_editions = edition_token_set(fname)
    if lib_editions == new_editions:
        return True
    logger.debug(
        "Edition mismatch for '{}' (new={}, lib={}).",
        fname,
        sorted(new_editions),
        sorted(lib_editions),
    )
    return False


def _library_resolution_is_higher(fname: str, new_san: str) -> bool:
    """Return True when the library file has a higher resolution than the copy.

    Acquisition treats a higher resolution as dominant regardless of source or
    score, so the delete pass must agree.
    """
    lib_res = extract_resolution(sanitise(fname) or "")
    new_res = extract_resolution(new_san)
    if not (lib_res and new_res and lib_res.isdigit() and new_res.isdigit()):
        return False
    if int(lib_res) <= int(new_res):
        return False
    logger.warning(
        "Skipping auto-delete for '{}': it has a higher resolution ({}p) than the copy ({}p).",
        fname,
        lib_res,
        new_res,
    )
    return True


def _library_quality_is_worse(fname: str, lib_san: str, new_san: str, config: Config) -> bool:
    """Return True when the library file's quality is parseable and strictly worse."""
    lib_score = supersession_quality_score(lib_san, new_san, config)
    if lib_score <= 0:
        # No parseable quality tokens (e.g. "The Matrix.mkv"): the file may well
        # be the better copy, so it must survive.
        logger.warning(
            "Skipping auto-delete for '{}': its quality cannot be parsed, so it may be the better copy.",
            fname,
        )
        return False
    new_score = supersession_quality_score(new_san, lib_san, config)
    if lib_score >= new_score:
        # Final safety net: the library file is at least as good as what was just
        # copied (e.g. it was added after the search ran).
        logger.warning(
            "Skipping auto-delete for '{}': it scores at least as high as the copied file '{}'.",
            fname,
            new_san,
        )
        return False
    return True
