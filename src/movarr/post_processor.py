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
from typing import TYPE_CHECKING

from loguru import logger

from movarr import torrent_client_health
from movarr.file_utils import copy_with_verify, delete_file, make_directory
from movarr.filters import edition_token_set, primary_edition_token, supersession_quality_score
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
    r"\b(?:behind[\s_.\-]+the[\s_.\-]+scenes|making[\s_.\-]+of|featurettes?"
    r"|deleted[\s_.\-]+scenes?|interviews?|short[\s_.\-]+films?"
    r"|theatrical[\s_.\-]+trailer|trailer|sample"
    r"|bonus|extras|special[\s_.\-]+features?|specials)\b",
    re.IGNORECASE,
)
_BRACKET_RE = re.compile(r"[\[{]([^\]\}]+)[\]\}]")


def _kill_process(proc: subprocess.Popen, pgid: int | None, label: str, timeout_secs: float) -> None:
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
    logger.error("{} hook timed out after {} s.", label, timeout_secs)


def _log_hook_output(stdout: str, stderr: str, label: str) -> None:
    """Log stdout and stderr from a hook process at DEBUG level if non-empty."""
    if stdout:
        logger.debug("{} hook stdout: {}", label, stdout.rstrip())
    if stderr:
        logger.debug("{} hook stderr: {}", label, stderr.rstrip())


def _run_hook(command: str, dir_path: str, label: str, timeout_secs: float = 300.0) -> bool:
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

    Important:
        Hooks **must not rename or move** the target files. The ``post_copy``
        hook fires before library supersession; if it renames the newly copied
        primary file, supersession will skip deletion (the primary is no longer
        found). The ``pre_delete`` hook fires before the deletion loop; if it
        renames a library candidate, the loop will report a false-positive
        deletion count. Use hooks only for in-place operations (e.g. ``chattr
        -i``, ``trimarr``).
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
        pgid = os.getpgid(proc.pid)
    except (OSError, ProcessLookupError):
        pgid = proc.pid  # With start_new_session=True, proc.pid IS the PGID
    try:
        stdout, stderr = proc.communicate(timeout=timeout_secs)
    except subprocess.TimeoutExpired:
        _kill_process(proc, pgid, label, timeout_secs)
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


# Copy helper


def _copy_files(
    src_files: list[str],
    dst_dir: str,
    largest_fname: str,
    canonical_fname: str,
) -> tuple[bool, set[str]]:
    """Copy *src_files* to *dst_dir*, renaming the largest file to *canonical_fname*.

    Returns ``(all_ok, copied_fnames)``.  Aborts and returns ``False`` on first failure.
    """
    copied_fnames: set[str] = set()
    for src_path in src_files:
        src_fname = os.path.basename(src_path)
        dst_fname = canonical_fname if src_fname == largest_fname else src_fname
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
) -> None:
    """Mark the torrent completed and run any configured post-copy operations."""
    db.mark_completed(tag)
    logger.info("Marked tag '{}' as completed.", tag)
    if config.post_process.hooks.post_copy:
        try:
            if not _run_hook(
                config.post_process.hooks.post_copy,
                resolved_dst_dir,
                "post_copy",
                config.post_process.hooks.hook_timeout_secs,
            ):
                logger.warning("post_copy hook failed for '{}'; continuing.", resolved_dst_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning("post_copy hook raised an exception for '{}': {}; continuing.", resolved_dst_dir, exc)
    if config.post_process.delete_lower_quality and canonical_fname in copied_fnames:
        deleted = _delete_superseded_files(
            dst_dir, dst_base, canonical_fname, config, copied_fnames=frozenset(copied_fnames)
        )
        if deleted:
            logger.info("Auto-deleted {} lower-quality file(s) from '{}'.", deleted, dst_dir)
    if config.post_process.remove_completed:
        qbt.delete_torrent(torrent_hash, delete_data=True, state="completed")


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
    if not make_directory(dst_dir):
        logger.error("Cannot create destination directory '{}'; skipping.", dst_dir)
        return None
    return src_files, dst_base, dst_dir


def _process_one(
    torrent: dict,
    config: Config,
    qbt: QBittorrentClient,
    db: Database,
) -> None:
    tag, torrent_hash = _torrent_tag_and_hash(torrent)

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

    if all_ok:
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
        )


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
    rel_path = file_dict.get("file_name") or ""
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
    genres = _parse_genres(db_record.imdb_genres_list or [])
    cert: str = str(db_record.imdb_certification or "")
    # Default to empty string (not "imdbpie") so legacy/unknown rows
    # do not accidentally get BBFC routing applied.
    cert_source: str = str(db_record.imdb_cert_source or "")
    # Bug #8 fix: only apply BBFC cert routing if the cert came from imdbpie (UK BBFC certs).
    # OMDb returns MPAA ratings which are not compatible with BBFC ordering.
    effective_cert = cert if cert_source == "imdbpie" else ""
    resolution = _resolution_from_index_title(str(db_record.index_title or ""))
    return _pick_path(genres, effective_cert, resolution, rules, default)


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


def _candidate_basic_match(
    fname: str,
    lib_san: str,
    new_title: str | None,
    new_year: str | None,
) -> bool:
    """Return True iff *fname*'s title and year match the new file."""
    lib_title = extract_movie_title(lib_san)
    if not (new_title and lib_title and new_title == lib_title):
        logger.debug(
            "Skipping auto-delete for '{}': title mismatch or unparseable (new='{}', lib='{}').",
            fname,
            new_title,
            lib_title,
        )
        return False
    lib_year = extract_year(lib_san)
    if not lib_year or lib_year != new_year:
        logger.debug(
            "Skipping auto-delete for '{}': year mismatch or unparseable (new='{}', lib='{}').",
            fname,
            new_year,
            lib_year,
        )
        return False
    return True


def _is_extras_file(fname: str, lib_san: str) -> bool:
    """Return True if *fname* looks like extras/bonus content."""
    lib_after = extract_after_year(lib_san) or ""
    lib_bracket = " ".join(_BRACKET_RE.findall(fname))
    return bool(_EXTRAS_RE.search(lib_after) or (lib_bracket and _EXTRAS_RE.search(lib_bracket.lower())))


def _compare_parsed_resolutions(
    fname: str,
    new_san: str,
    lib_san: str,
    new_res_int: int,
    lib_res_int: int,
    config: Config,
) -> bool | None:
    """Return True to delete, False to keep, None to skip (edition mismatch or lower res).

    Called after resolution integers have already been parsed.
    """
    if new_res_int > lib_res_int:
        if edition_token_set(new_san) != edition_token_set(lib_san):
            logger.debug(
                "Skipping auto-delete for '{}': edition mismatch despite higher resolution (new='{}', lib='{}').",
                fname,
                primary_edition_token(new_san) or "base",
                primary_edition_token(lib_san) or "base",
            )
            return None
        return True
    if new_res_int == lib_res_int:
        if edition_token_set(new_san) != edition_token_set(lib_san):
            logger.debug(
                "Skipping auto-delete for '{}': edition mismatch at same resolution (new='{}', lib='{}').",
                fname,
                primary_edition_token(new_san) or "base",
                primary_edition_token(lib_san) or "base",
            )
            return None
        new_score = supersession_quality_score(new_san, lib_san, config)
        lib_score = supersession_quality_score(lib_san, new_san, config)
        return new_score > lib_score
    return False


def _resolution_supersedes(
    fname: str,
    new_san: str,
    lib_san: str,
    new_res_str: str | None,
    config: Config,
) -> bool | None:
    """Return True to delete, False to keep, None to skip.

    Compares *new_san*'s resolution against *lib_san* and returns whether the
    new file supersedes the library file.  Returns None when a resolution is
    unparseable or when an edition mismatch prevents safe comparison.
    """
    lib_res_str = extract_resolution(lib_san)
    if not new_res_str or not lib_res_str:
        logger.debug(
            "Skipping auto-delete for '{}': resolution unparseable (new='{}', lib='{}').",
            fname,
            new_res_str,
            lib_res_str,
        )
        return None
    try:
        new_res_int = int(new_res_str)
        lib_res_int = int(lib_res_str)
    except (ValueError, TypeError):
        return None
    return _compare_parsed_resolutions(fname, new_san, lib_san, new_res_int, lib_res_int, config)


def _should_delete_file(
    fname: str,
    protected: frozenset[str],
    new_san: str,
    new_title: str | None,
    new_year: str | None,
    new_res_str: str | None,
    config: Config,
) -> bool:
    """Return True if *fname* is superseded by the new file and should be deleted."""
    if fname in protected:
        return False
    lib_san = sanitise(fname) or ""
    if not _candidate_basic_match(fname, lib_san, new_title, new_year):
        return False
    if _is_extras_file(fname, lib_san):
        logger.debug("Skipping auto-delete for '{}': looks like extra/bonus content.", fname)
        return False
    return _resolution_supersedes(fname, new_san, lib_san, new_res_str, config) is True


def _collect_superseded_files(
    video_files: list[str],
    protected: frozenset[str],
    new_san: str,
    new_res_str: str | None,
    config: Config,
) -> list[str]:
    """Examine *video_files* and return the filenames that should be deleted.

    A file is a deletion candidate when:
    - It is not in *protected*.
    - Its title, year, and extras-status match the new file's.
    - Both resolutions are parseable integers.
    - Either the new resolution is strictly higher (and editions match), or the
      resolutions are equal, editions match, and the new supersession score wins.
    """
    new_title = extract_movie_title(new_san)
    new_year = extract_year(new_san)
    to_delete: list[str] = []
    for fname in video_files:
        if _should_delete_file(fname, protected, new_san, new_title, new_year, new_res_str, config):
            to_delete.append(fname)
    return to_delete


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
    try:
        entries = os.listdir(resolved_dst)
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
    return video_files, resolved_dst


def _is_extras_primary(new_primary_fname: str, new_san: str) -> bool:
    """Return True if *new_primary_fname* looks like extras/bonus content."""
    new_after = extract_after_year(new_san) or ""
    new_bracket = " ".join(_BRACKET_RE.findall(new_primary_fname))
    return bool(_EXTRAS_RE.search(new_after) or (new_bracket and _EXTRAS_RE.search(new_bracket.lower())))


def _run_pre_copy_hook(config: Config, resolved_dst_dir: str, dst_dir: str) -> bool:
    """Run the pre_copy hook if configured.  Return False to abort the copy."""
    if not config.post_process.hooks.pre_copy:
        return True
    try:
        if not _run_hook(
            config.post_process.hooks.pre_copy,
            resolved_dst_dir,
            "pre_copy",
            config.post_process.hooks.hook_timeout_secs,
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
            config.post_process.hooks.hook_timeout_secs,
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
            config.post_process.hooks.hook_timeout_secs,
        ):
            logger.warning("post_delete hook failed for '{}'; continuing.", dst_dir)
    except Exception as exc:  # noqa: BLE001
        logger.warning("post_delete hook raised an exception for '{}': {}; continuing.", dst_dir, exc)


def _run_deletion(
    video_files: list[str],
    resolved_dst: pathlib.Path,
    dst_dir: str,
    new_primary_fname: str,
    config: Config,
    copied_fnames: frozenset[str],
) -> int:
    """Execute the supersession deletion pass after all safety checks have passed."""
    protected = frozenset(copied_fnames) | {new_primary_fname}
    new_san = sanitise(new_primary_fname) or ""
    new_res_str = extract_resolution(new_san)

    if _is_extras_primary(new_primary_fname, new_san):
        logger.debug(
            "Auto-delete skipped: new primary '{}' is bonus/extras content.",
            new_primary_fname,
        )
        return 0

    if not _run_pre_delete_hook_and_verify(config, resolved_dst, dst_dir, new_primary_fname):
        return 0

    deleted = 0
    for fname in _collect_superseded_files(video_files, protected, new_san, new_res_str, config):
        lib_path = str(resolved_dst / fname)
        if delete_file(lib_path):
            logger.info("Auto-deleted superseded library file '{}'.", lib_path)
            deleted += 1
        else:
            logger.error("Failed to auto-delete superseded library file '{}'.", lib_path)

    _run_post_delete_hook(config, resolved_dst, dst_dir)
    return deleted


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
    preconditions = _check_delete_preconditions(dst_dir, dst_base, new_primary_fname)
    if preconditions is None:
        return 0
    video_files, resolved_dst = preconditions
    return _run_deletion(video_files, resolved_dst, dst_dir, new_primary_fname, config, copied_fnames)
