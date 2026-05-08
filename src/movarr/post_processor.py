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
import json
import os
import pathlib
import re
import shlex
import signal
import subprocess
from typing import TYPE_CHECKING

from loguru import logger

from movarr import torrent_client_health
from movarr.file_utils import copy_with_verify, delete_file, make_directory
from movarr.filters import supersession_quality_score
from movarr.parsing import extract_after_year, extract_movie_title, extract_resolution, extract_year, sanitise

if TYPE_CHECKING:
    from movarr.config import Config, CopyLibraryRuleConfig, DefaultCopyLibraryConfig, PathRemappingConfig
    from movarr.database import Database, HistoryRecord
    from movarr.qbittorrent import QBittorrentClient

__all__ = ["run_post_processing"]

_BBFC_ORDER = ["U", "PG", "12", "12A", "15", "18", "R18"]
_VIDEO_EXTS = (".mkv", ".mp4", ".avi")
_MAX_VIDEO_FILES_IN_MOVIE_DIR = 4  # safety cap: abort deletion if dir contains more than this many video files
_RE_PATH_UNSAFE = re.compile(r'[/\\<>:"|?*\x00]|\.\.')
# Known extras/bonus-content markers — files containing these in the post-year
# segment are not quality variants of the main feature and must never be deleted.
_EXTRAS_RE = re.compile(
    r"\b(?:behind[\s_]the[\s_]scenes|making[\s_]of|featurette|deleted[\s_]scene"
    r"|interview|short[\s_]film|theatrical[\s_]trailer|trailer|sample"
    r"|bonus|extra|extras|special)\b",
    re.IGNORECASE,
)


def _run_hook(command: str, dir_path: str, label: str) -> bool:
    """Run a post-process hook command, substituting ``{dir}`` with *dir_path*.

    Uses ``shell=True`` so that glob patterns (e.g. ``chattr -i {dir}/*``) are
    expanded by the shell. The command originates from the user's own config
    file, so the trust boundary is the same as the rest of the configuration.

    ``{dir}`` is replaced with a *shell-quoted* form of *dir_path* via
    :func:`shlex.quote`, so the placeholder is already safe for paths that
    contain spaces or shell metacharacters.  Do **not** add extra quotes around
    ``{dir}`` in the template — doing so will produce literal quote characters
    in the expanded command.  Correct: ``chattr -i {dir}/*``.
    Incorrect: ``chattr -i "{dir}/*"``.

    Args:
        command: Shell command template. ``{dir}`` is replaced with a
            shell-quoted form of *dir_path*. Do not quote ``{dir}`` in the
            template.
        dir_path: Absolute path of the destination directory.
        label: Hook name for log messages (e.g. ``"pre_delete"``).

    Returns:
        True if the command exits with code 0, False otherwise.
    """
    cmd = command.replace("{dir}", shlex.quote(dir_path))
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
        stdout, stderr = proc.communicate(timeout=300)
    except subprocess.TimeoutExpired:
        # Kill the entire process group so shell-spawned children don't
        # outlive the timeout and continue mutating files in the background.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                break  # already gone
            try:
                proc.communicate(timeout=5)
                break
            except subprocess.TimeoutExpired:
                continue  # escalate to SIGKILL
        else:
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass  # process is already killed; nothing more we can do
        logger.error("{} hook timed out after 300 s.", label)
        return False
    if stdout:
        logger.debug("{} hook stdout: {}", label, stdout.rstrip())
    if stderr:
        logger.debug("{} hook stderr: {}", label, stderr.rstrip())
    logger.info("{} hook completed with exit code {}.", label, proc.returncode)
    if proc.returncode != 0:
        logger.warning("{} hook exited with code {}.", label, proc.returncode)
        return False
    return True


def _safe_path_component(value: str) -> str:
    """Strip characters that are unsafe in a filesystem path component."""
    return _RE_PATH_UNSAFE.sub("", value).strip()


def _apply_path_remapping(path: str, remappings: list[PathRemappingConfig]) -> str:
    """Replace the first matching from_path prefix with its to_path counterpart.

    Handles both forward-slash and OS-native separators so mappings work
    whether the qBittorrent host is Linux or Windows.
    """
    for remap in sorted(remappings, key=lambda r: len(r.from_path), reverse=True):
        src = remap.from_path.rstrip("/\\")
        dst = remap.to_path.rstrip("/\\")
        if not src:
            continue
        if path.startswith(src + "/") or path.startswith(src + "\\") or path == src:
            remapped = dst + path[len(src) :]
            logger.debug("Path remapped: '{}' → '{}'.", path, remapped)
            return remapped
    return path


def run_post_processing(config: Config, qbt: QBittorrentClient, db: Database) -> None:
    """Main post-processing entry point.

    Args:
        config: Application configuration.
        qbt: An already-connected ``QBittorrentClient`` instance.
        db: Open database instance.
    """
    pp_cfg = config.post_process
    if not pp_cfg.post_process_enabled:
        logger.debug("Post-processing disabled; skipping.")
        return

    if not qbt.is_connected():
        logger.warning("qBittorrent is unreachable; skipping post-processing.")
        torrent_client_health.check_and_notify(is_reachable=False, db=db, config=config)
        return
    torrent_client_health.check_and_notify(is_reachable=True, db=db, config=config)

    completed = qbt.list_completed()
    if not completed:
        logger.debug("No completed torrents to post-process.")
        return

    for torrent in completed:
        _process_one(torrent, config, qbt, db)


# Per-torrent processing


def _process_one(
    torrent: dict,
    config: Config,
    qbt: QBittorrentClient,
    db: Database,
) -> None:
    tag = torrent.get("torrent_tag") or ""
    torrent_hash = torrent.get("torrent_hash") or ""

    db_record = db.find_by_tag(tag)
    if not db_record:
        logger.warning("No DB record for tag '{}'; skipping.", tag)
        return

    # If copy_completed is disabled, skip the copy path entirely.
    # movarr assumes the user has configured qBittorrent to download directly
    # to the final library path, so no file copy is needed.
    # When remove_completed is also True, remove only the torrent queue entry
    # (NOT the downloaded files) by passing delete_data=False.
    if not config.post_process.copy_completed:
        logger.debug("copy_completed is False; skipping copy for tag '{}'", tag)
        db.mark_completed(tag)
        if config.post_process.remove_completed:
            qbt.delete_torrent(torrent_hash, delete_data=False, state="completed")
        return

    # Build the file copy list (applying exclusion rules).
    src_files = _build_copy_list(torrent, config)
    if not src_files:
        logger.debug("No files to copy for tag '{}'.", tag)
        return

    # Determine destination.
    dst_base = _resolve_destination(db_record, config)
    if not dst_base:
        logger.warning("Could not resolve copy destination for tag '{}'; skipping.", tag)
        return

    imdb_title = _safe_path_component(str(db_record.imdb_title or "Unknown")) or "Unknown"
    # Guard single dots (e.g. "." from stripping "...") which os.path.join treats as CWD.
    if not imdb_title.strip("."):
        imdb_title = "Unknown"
    imdb_year = _safe_path_component(str(db_record.imdb_year or ""))
    folder_name = f"{imdb_title} ({imdb_year})" if imdb_year else imdb_title
    dst_dir = os.path.join(dst_base, folder_name)

    if not make_directory(dst_dir):
        logger.error("Cannot create destination directory '{}'; skipping.", dst_dir)
        return

    # Determine canonical filename for the largest file (rename to parent-dir style).
    largest_fname, largest_rel_path = _largest_file(torrent)
    canonical_fname = _canonical_filename(largest_fname, largest_rel_path)

    all_ok = True
    copied_fnames: set[str] = set()
    for src_path in src_files:
        src_fname = os.path.basename(src_path)
        dst_fname = canonical_fname if src_fname == largest_fname else src_fname

        dst_path = os.path.join(dst_dir, dst_fname)
        logger.info("Copying '{}' → '{}'.", src_path, dst_path)
        if not copy_with_verify(src_path, dst_path):
            logger.error("Copy/verify failed for '{}'; aborting this torrent.", src_path)
            all_ok = False
            break
        copied_fnames.add(dst_fname)

    if all_ok:
        db.mark_completed(tag)
        logger.info("Marked tag '{}' as completed.", tag)
        if config.post_process.hooks.post_copy:
            try:
                if not _run_hook(config.post_process.hooks.post_copy, dst_dir, "post_copy"):
                    logger.warning("post_copy hook failed for '{}'; continuing.", dst_dir)
            except Exception as exc:  # noqa: BLE001
                logger.warning("post_copy hook raised an exception for '{}': {}; continuing.", dst_dir, exc)
        if config.post_process.delete_lower_quality and canonical_fname in copied_fnames:
            deleted = _delete_superseded_files(
                dst_dir, dst_base, canonical_fname, config, copied_fnames=frozenset(copied_fnames)
            )
            if deleted:
                logger.info("Auto-deleted {} lower-quality file(s) from '{}'.", deleted, dst_dir)
    # Remove source torrent if configured (we're already processing completed torrents).
    if all_ok and config.post_process.remove_completed:
        qbt.delete_torrent(torrent_hash, delete_data=True, state="completed")


# File-list helpers


def _build_copy_list(torrent: dict, config: Config) -> list[str]:
    """Return absolute paths for files that should be copied to the library."""
    pp = config.post_process
    raw_save_path = torrent.get("torrent_save_path") or ""
    save_path = _apply_path_remapping(raw_save_path, pp.path_remapping)

    # Guard: an empty save_path resolves to CWD, bypassing the path-traversal
    # check below.  Reject early to avoid accidentally copying CWD-relative paths.
    if not save_path:
        tag = torrent.get("torrent_tag", "unknown")
        logger.warning("torrent_save_path is empty for tag '{}'; skipping copy.", tag)
        return []

    file_list = torrent.get("torrent_file_list") or []

    exclude_min_kb = pp.exclude_file_min_kb or 0
    exclude_file_regexes: list[re.Pattern[str]] = []
    for r in pp.exclude_file_regex_list or []:
        try:
            exclude_file_regexes.append(re.compile(r, re.IGNORECASE))
        except re.error:
            logger.warning("Invalid file-exclude regex '{}'; skipping.", r)
    exclude_folder_regexes: list[re.Pattern[str]] = []
    for r in pp.exclude_folder_regex_list or []:
        try:
            exclude_folder_regexes.append(re.compile(r, re.IGNORECASE))
        except re.error:
            logger.warning("Invalid folder-exclude regex '{}'; skipping.", r)

    result: list[str] = []
    save_root = pathlib.Path(save_path).resolve()
    for file_dict in file_list:
        rel_path = file_dict.get("file_name") or ""
        if not rel_path:
            logger.debug("Skipping file entry with empty name.")
            continue
        abs_path = os.path.join(save_path, rel_path)
        folder_part = os.path.dirname(rel_path)

        # Guard against crafted torrent paths that escape the save directory.
        try:
            if not pathlib.Path(abs_path).resolve().is_relative_to(save_root):
                logger.warning("Skipping '{}': path escapes save directory.", rel_path)
                continue
        except (ValueError, OSError):
            logger.warning("Skipping '{}': could not resolve path.", rel_path)
            continue

        if any(rx.search(rel_path) for rx in exclude_file_regexes):
            logger.debug("Excluding file '{}' (file regex match).", rel_path)
            continue
        if any(rx.search(folder_part) for rx in exclude_folder_regexes):
            logger.debug("Excluding folder '{}' (folder regex match).", folder_part)
            continue

        file_size = file_dict.get("file_size") or 0
        file_size_kb = file_size >> 10
        if exclude_min_kb and file_size_kb < exclude_min_kb:
            logger.debug("Excluding '{}' ({}KB < {}KB min).", rel_path, file_size_kb, exclude_min_kb)
            continue

        result.append(abs_path)

    return result


def _largest_file(torrent: dict) -> tuple[str, str]:
    """Return (filename, relative_path_dir) of the largest file in the torrent."""
    file_list = torrent.get("torrent_file_list") or []
    if not file_list:
        return "", ""
    biggest = max(file_list, key=lambda f: f.get("file_size") or 0)
    rel_path = biggest.get("file_name") or ""
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
    """Choose the library copy path based on genre, cert, and resolution rules."""
    pp = config.post_process
    rules = pp.copy_library_rules
    default = pp.default_copy_library

    if not default.hd_path and not default.uhd_path:
        logger.warning("No 'default_copy_library' configured; cannot copy.")
        return None

    genres_raw: object = db_record.imdb_genres_list or []
    genres = _parse_genres(genres_raw)

    cert: str = str(db_record.imdb_certification or "")
    # Default to empty string (not "imdbpie") so legacy/unknown rows
    # do not accidentally get BBFC routing applied.
    cert_source: str = str(db_record.imdb_cert_source or "")

    # Bug #8 fix: only apply BBFC cert routing if the cert came from imdbpie (UK BBFC certs).
    # OMDb returns MPAA ratings which are not compatible with BBFC ordering.
    effective_cert = cert if cert_source == "imdbpie" else ""

    resolution = _resolution_from_index_title(str(db_record.index_title or ""))

    return _pick_path(genres, effective_cert, resolution, rules, default)


def _pick_path(
    genres: list[str],
    cert: str,
    resolution: str | None,
    rules: list[CopyLibraryRuleConfig],
    default: DefaultCopyLibraryConfig,
) -> str | None:
    path_key = "uhd_path" if resolution in ("2160", "4k") else "hd_path"

    def default_path() -> str | None:
        primary = getattr(default, path_key) or None
        if primary is None and path_key == "uhd_path":
            return default.hd_path or None
        return primary

    genres_lower = {g.lower() for g in genres}
    scored = [(len({g.lower() for g in rule.genres} & genres_lower), rule) for rule in rules]
    scored = [(s, r) for s, r in scored if s > 0]

    if not scored:
        return default_path()

    max_score = max(s for s, _ in scored)
    top_rules = [r for s, r in scored if s == max_score]

    if len(top_rules) != 1:
        names = [r.name for r in top_rules]
        logger.info("Genres {} tied across rules {}; using default path.", genres, names)
        return default_path()

    rule = top_rules[0]
    if rule.max_certification and not _cert_acceptable(cert, rule.max_certification):
        logger.info(
            "Cert '{}' fails max_cert '{}' for rule '{}'; using default.", cert, rule.max_certification, rule.name
        )
        return default_path()

    path = getattr(rule, path_key, "") or ""
    if not path and path_key == "uhd_path":
        path = getattr(rule, "hd_path", "") or ""
    if not path:
        logger.warning("Rule '{}' has no '{}'; using default.", rule.name, path_key)
        return default_path()

    return path


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


def _parse_genres(raw: object) -> list[str]:
    if isinstance(raw, list):
        return [str(g).strip() for g in raw]
    if not isinstance(raw, (str, bytes, bytearray)):
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(g).strip() for g in parsed]
    except (ValueError, TypeError):
        pass
    try:
        parsed = ast.literal_eval(raw if isinstance(raw, str) else raw.decode())
        if isinstance(parsed, list):
            return [str(g).strip() for g in parsed]
    except (ValueError, SyntaxError):
        pass
    return []


# Library supersession


def _delete_superseded_files(
    dst_dir: str,
    dst_base: str,
    new_primary_fname: str,
    config: Config,
    *,
    copied_fnames: frozenset[str] = frozenset(),
) -> int:
    """Delete video files in *dst_dir* that are superseded by the newly copied file.

    A library file is superseded if, and only if, the new file is strictly better:
    - new resolution > library resolution, OR
    - same resolution AND new supersession quality score > library score.

    Special-edition tokens (extended, director's cut, theatrical, unrated) are
    deliberately excluded from the score comparison — they represent different cuts
    rather than superior quality and must not trigger deletion of alternate editions.

    Files named in *copied_fnames* (all destinations written in the current torrent
    run) are never deletion candidates, preventing cross-deletion of companion files
    that belong to the same torrent.

    Two hard-stop safety guards protect against runaway deletion:
    1. *Depth check*: ``dst_dir`` must be a direct child of ``dst_base``. Any
       deviation (equal paths, grandchild, unrelated path) aborts immediately.
    2. *Count cap*: if the directory holds more than
       ``_MAX_VIDEO_FILES_IN_MOVIE_DIR`` video files, abort. This catches the
       flat-library case where all movies live in a single directory.

    Args:
        dst_dir: Absolute path to the per-movie destination directory.
        dst_base: Absolute path to the configured library base directory.
            Used exclusively for the depth safety guard.
        new_primary_fname: Filename (not full path) of the newly copied primary video.
            Must be present in ``dst_dir``; if absent the function returns 0 without
            deleting anything.
        config: Application configuration.
        copied_fnames: All destination filenames written during this torrent run.
            Every filename in this set — including ``new_primary_fname`` — is
            protected from deletion regardless of its quality score.

    Returns:
        Number of files successfully deleted.
    """
    if not os.path.isdir(dst_dir):
        return 0

    # Require the primary file to be a video — a non-video primary (e.g. .rar, .nfo)
    # cannot produce a meaningful quality comparison and must not trigger deletions.
    if not new_primary_fname.lower().endswith(_VIDEO_EXTS):
        logger.debug(
            "Auto-delete skipped: primary file '{}' is not a recognised video format.",
            new_primary_fname,
        )
        return 0

    # Safety guard 1: dst_dir must be a direct child of dst_base.
    # Use resolved paths for ALL subsequent I/O to prevent TOCTOU symlink bypass.
    resolved_dst = pathlib.Path(dst_dir).resolve()
    resolved_base = pathlib.Path(dst_base).resolve()
    if resolved_dst.parent != resolved_base:
        logger.error(
            "Auto-delete safety check failed: '{}' is not a direct child of '{}'; skipping.",
            dst_dir,
            dst_base,
        )
        return 0

    try:
        entries = os.listdir(resolved_dst)
    except OSError:
        logger.error("Could not list directory '{}'; skipping auto-delete.", dst_dir)
        return 0

    # Abort if the primary copied file is absent — we cannot safely identify what
    # was just written versus what is an old library copy.
    if new_primary_fname not in entries:
        logger.warning(
            "Auto-delete skipped: primary file '{}' not found in '{}'; "
            "cannot safely distinguish new from old library files.",
            new_primary_fname,
            dst_dir,
        )
        return 0

    # Safety guard 2: cap on number of video files in the directory.
    video_files = [f for f in entries if f.lower().endswith(_VIDEO_EXTS)]
    if len(video_files) > _MAX_VIDEO_FILES_IN_MOVIE_DIR:
        logger.warning(
            "Auto-delete skipped: %d video files in '%s' exceeds max %d; no files deleted.",
            len(video_files),
            dst_dir,
            _MAX_VIDEO_FILES_IN_MOVIE_DIR,
        )
        return 0

    # All filenames written during this torrent run are protected from deletion.
    # This prevents cross-deletion of companion files in a multi-file torrent.
    protected = frozenset(copied_fnames) | {new_primary_fname}

    new_san = sanitise(new_primary_fname) or ""
    new_title = extract_movie_title(new_san)
    new_res_str = extract_resolution(new_san)

    deleted = 0

    if config.post_process.hooks.pre_delete:
        try:
            if not _run_hook(config.post_process.hooks.pre_delete, str(resolved_dst), "pre_delete"):
                logger.error(
                    "pre_delete hook failed for '{}'; aborting deletion pass.", dst_dir
                )
                return 0
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "pre_delete hook raised an exception for '{}': {}; aborting deletion pass.", dst_dir, exc
            )
            return 0

        # Re-snapshot the directory — the hook may have renamed, removed, or added files.
        # Acting on stale names produces incorrect deletion counts and can delete files
        # that no longer exist as superseded candidates.
        try:
            entries = os.listdir(resolved_dst)
        except OSError:
            logger.error("Could not re-list directory '{}' after pre_delete hook; skipping.", dst_dir)
            return 0
        if new_primary_fname not in entries:
            logger.warning(
                "Auto-delete skipped: primary file '{}' absent after pre_delete hook ran.",
                new_primary_fname,
            )
            return 0
        video_files = [f for f in entries if f.lower().endswith(_VIDEO_EXTS)]
        # Re-apply count cap: hook may have added video files since Guard 2 ran.
        if len(video_files) > _MAX_VIDEO_FILES_IN_MOVIE_DIR:
            logger.warning(
                "Auto-delete skipped: %d video files in '%s' after pre_delete hook exceeds max %d; no files deleted.",
                len(video_files),
                dst_dir,
                _MAX_VIDEO_FILES_IN_MOVIE_DIR,
            )
            return 0
    for fname in video_files:
        if fname in protected:
            continue

        lib_san = sanitise(fname) or ""

        # Three-layer content identity check — all must pass to consider deletion.
        # Conservative: skip the file if ANY check cannot positively confirm
        # the candidate is a quality variant of the same content.

        # 1. Title match (fail-closed): both titles must be parseable and identical.
        lib_title = extract_movie_title(lib_san)
        if not (new_title and lib_title and new_title == lib_title):
            logger.debug(
                "Skipping auto-delete for '{}': title mismatch or unparseable (new='{}', lib='{}').",
                fname,
                new_title,
                lib_title,
            )
            continue

        # 2. Year match (fail-closed): both years must be parseable and identical.
        lib_year = extract_year(lib_san)
        if not lib_year or lib_year != extract_year(new_san):
            logger.debug(
                "Skipping auto-delete for '{}': year mismatch or unparseable (new='{}', lib='{}').",
                fname,
                extract_year(new_san),
                lib_year,
            )
            continue

        # 3. Extras keyword guard: skip files whose post-year segment contains
        #    known bonus/extras content labels (e.g. "Behind the Scenes",
        #    "Making Of", "Featurette"). These are different content, not quality
        #    variants, even when sharing the same title and year.
        lib_after = extract_after_year(lib_san) or ""
        if _EXTRAS_RE.search(lib_after):
            logger.debug(
                "Skipping auto-delete for '{}': looks like extra/bonus content.",
                fname,
            )
            continue

        lib_res_str = extract_resolution(lib_san)

        if not new_res_str or not lib_res_str:
            logger.debug(
                "Skipping auto-delete for '{}': resolution unparseable (new='{}', lib='{}').",
                fname,
                new_res_str,
                lib_res_str,
            )
            continue

        try:
            new_res_int = int(new_res_str)
            lib_res_int = int(lib_res_str)
        except (ValueError, TypeError):
            continue

        should_delete = False
        if new_res_int > lib_res_int:
            should_delete = True
        elif new_res_int == lib_res_int:
            new_score = supersession_quality_score(new_san, lib_san, config)
            lib_score = supersession_quality_score(lib_san, new_san, config)
            should_delete = new_score > lib_score
        # else: new_res_int < lib_res_int -> library has higher res, keep it

        if should_delete:
            lib_path = str(resolved_dst / fname)
            if delete_file(lib_path):
                logger.info("Auto-deleted superseded library file '{}'.", lib_path)
                deleted += 1
            else:
                logger.error("Failed to auto-delete superseded library file '{}'.", lib_path)

    if config.post_process.hooks.post_delete:
        try:
            if not _run_hook(config.post_process.hooks.post_delete, str(resolved_dst), "post_delete"):
                logger.warning("post_delete hook failed for '{}'; continuing.", dst_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning("post_delete hook raised an exception for '{}': {}; continuing.", dst_dir, exc)

    return deleted
