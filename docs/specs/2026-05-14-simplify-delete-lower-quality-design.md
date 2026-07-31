# Simplify `delete_lower_quality` — Remove Redundant Re-comparison

**Date:** 2026-05-14
**Scope:** `src/movarr/post_processor.py`

## Motivation

The `delete_lower_quality` post-processing step currently re-runs a full
resolution/quality comparison between the newly-copied file and every existing
video file in the destination folder. This comparison is redundant: the search
pipeline (`_check_library_canonical` in `filters.py`) already compares the
incoming torrent against the library before allowing a download. A torrent only
reaches post-processing if it is strictly better than what already exists.

The re-comparison adds ~100 lines of complex logic for no practical gain.
Simplifying to "delete all other video files in the destination folder" (with
appropriate safety guards) produces the same outcome in all normal cases.

## Design

### Core change

Replace the resolution/quality re-comparison with a simple rule:

> Delete all video files (`.mkv`, `.mp4`, `.avi`) in the destination folder
> **except** those in the protected set.

### Protected set

Files that are **never** deletion candidates:

1. The newly-copied primary file (`new_primary_fname`)
2. Any file written in the current torrent run (`copied_fnames`)
3. Files matching extras/bonus-content patterns (`_is_extras_file`)

### Remaining safety guards (unchanged)

- **Depth check:** `dst_dir` must be a direct child of `dst_base` — aborts if not.
- **Max video files cap:** if the directory contains more than 4 video files, abort.
- **Primary file must exist** in the directory — aborts if missing (e.g. post_copy hook renamed it).
- **Hooks:** `pre_delete` and `post_delete` run as before.

### What is removed

| Function | Reason |
|---|---|
| `_should_delete_file` | No longer needed — all non-protected video files are deleted |
| `_collect_superseded_files` | Replaced by simple loop with protected-set filtering |
| `_candidate_basic_match` | Title/year matching is unnecessary — the folder IS per-movie |
| `_compare_parsed_resolutions` | Resolution comparison already done upstream in search |
| `_resolution_supersedes` | Quality re-comparison already done upstream in search |

Note: the `extract_movie_title`, `extract_year`, and `extract_resolution` imports
from `parsing` may become unused in `post_processor.py` — remove them if no other
caller remains in the module.

### New delete-decision flow (pseudocode)

```python
def _delete_superseded_files(dst_dir, dst_base, new_primary_fname, config, *, copied_fnames=frozenset()):
    preconditions = _check_delete_preconditions(dst_dir, dst_base, new_primary_fname)
    if preconditions is None:
        return 0
    video_files, resolved_dst = preconditions

    if not _run_pre_delete_hook_and_verify(config, resolved_dst, dst_dir, new_primary_fname):
        return 0

    protected = frozenset(copied_fnames) | {new_primary_fname}
    deleted = 0
    for fname in video_files:
        if fname in protected:
            continue
        if _is_extras_file(fname, sanitise(fname) or ""):
            logger.debug("Skipping auto-delete for '{}': looks like extra/bonus content.", fname)
            continue
        lib_path = str(resolved_dst / fname)
        if delete_file(lib_path):
            logger.info("Auto-deleted superseded library file '{}'.", lib_path)
            deleted += 1
        else:
            logger.error("Failed to auto-delete superseded library file '{}'.", lib_path)

    _run_post_delete_hook(config, resolved_dst, dst_dir)
    return deleted
```

### Kept functions (unchanged)

- `_check_delete_preconditions`
- `_is_extras_file`
- `_run_pre_delete_hook_and_verify`
- `_run_post_delete_hook`
- `_delete_superseded_files` (rewritten body, same signature)

### Kept module-level constants (unchanged)

- `_EXTRAS_RE`
- `_BRACKET_RE`
- `_MAX_VIDEO_FILES_IN_MOVIE_DIR`

## Out of scope

- Cross-library-path deletion (deleting superseded files in library paths other
  than the destination folder). This is a future enhancement.
- Changes to `filters.py` or `search.py` — the upstream library check remains
  the sole quality gate.
- Config changes — `delete_lower_quality: true` behaves identically from the
  user's perspective.

## Testing

- Unit test: destination folder with one superseded file → deleted.
- Unit test: destination folder with extras file → skipped, not deleted.
- Unit test: destination folder with the just-copied file → skipped.
- Unit test: directory with >4 video files → abort, nothing deleted.
- Unit test: depth check fails → abort.
- Unit test: primary file missing from directory → abort.
- Unit test: `pre_delete` hook renames primary file → abort.
- Unit test: `copied_fnames` includes companion files → all skipped.
