# Simplify `delete_lower_quality` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove redundant resolution/quality re-comparison from `_delete_superseded_files` — the search pipeline already guarantees the new file is better.

**Architecture:** Replace the 5-function deletion decision chain (`_should_delete_file` → `_collect_superseded_files` → `_candidate_basic_match` / `_compare_parsed_resolutions` / `_resolution_supersedes`) with a simple loop: for each video file in the destination folder, delete it unless it's the new primary, a companion from the same torrent run, or extras/bonus content.

**Tech Stack:** Python 3.12+, pytest, ruff, mypy

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/movarr/post_processor.py` | Modify | Remove 5 functions, simplify `_delete_superseded_files`, inline `_run_deletion`, clean up imports |
| `tests/unit/test_post_processor.py` | Modify | Remove 10 old conservative tests, add 3 new tests for simplified behavior, keep ~20 unchanged |

---

### Task 1: Remove old conservative deletion tests

**Files:**
- Modify: `tests/unit/test_post_processor.py`

- [ ] **Step 1: Remove 10 tests that verify old re-comparison behavior**

Remove these test methods from `class TestDeleteSupersededFiles`:

1. `test_keeps_equal_quality` — equal quality kept (now deletes)
2. `test_keeps_higher_resolution_library_file` — higher res kept (now deletes)
3. `test_skips_unparseable_resolution_library_file` — unparseable skipped (now deletes)
4. `test_skips_unparseable_resolution_new_file` — new unparseable skips all (now deletes)
5. `test_special_edition_not_deleted_when_it_replaces_theatrical` — edition protection (now deletes)
6. `test_skips_deletion_when_edition_differs_across_resolution` — edition mismatch (now deletes)
7. `test_skips_deletion_same_res_edition_mismatch` — same-res edition mismatch (now deletes)
8. `test_different_title_sibling_is_preserved` — different title kept (now deletes)
9. `test_skips_file_with_unparseable_title` — unparseable title kept (now deletes)
10. `test_skips_file_with_mismatched_year` — mismatched year kept (now deletes)

- [ ] **Step 2: Run tests to verify remaining tests still pass (implementation unchanged)**

```bash
cd /data/movarr && uv run pytest tests/unit/test_post_processor.py::TestDeleteSupersededFiles -v
```

Expected: all remaining tests pass (implementation is still the old one, just fewer tests).

- [ ] **Step 3: Add 3 new tests for simplified behavior**

Add these test methods to `class TestDeleteSupersededFiles`:

```python
def test_deletes_all_other_video_files_regardless_of_quality(self, tmp_path: Path) -> None:
    """All other video files are deleted regardless of resolution/quality/edition."""
    movie_dir = tmp_path / "The Matrix (1999)"
    movie_dir.mkdir()
    new_fname = "The Matrix 1999 1080p BluRay.mkv"
    (movie_dir / new_fname).write_bytes(b"new")
    # Files that would have been protected by the old logic
    higher_res = "The Matrix 1999 2160p BluRay.mkv"
    diff_title = "The Matrix Reloaded 2003 2160p BluRay.mkv"
    unparseable = "The Matrix.mkv"
    diff_year = "The Matrix 2003 1080p.mkv"
    for name in [higher_res, diff_title, unparseable, diff_year]:
        (movie_dir / name).write_bytes(b"old")

    count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

    assert count == 4
    assert not (movie_dir / higher_res).exists()
    assert not (movie_dir / diff_title).exists()
    assert not (movie_dir / unparseable).exists()
    assert not (movie_dir / diff_year).exists()
    assert (movie_dir / new_fname).exists()


def test_skips_extras_from_prior_runs(self, tmp_path: Path) -> None:
    """Extras/bonus content from prior torrent runs is NOT deleted."""
    movie_dir = tmp_path / "The Matrix (1999)"
    movie_dir.mkdir()
    new_fname = "The Matrix 1999 2160p BluRay.mkv"
    (movie_dir / new_fname).write_bytes(b"new")
    # Extras from a prior run — must survive
    extras = [
        "The Matrix 1999 Behind the Scenes 1080p.mkv",
        "The Matrix 1999 [Featurettes] 1080p.mkv",
        "The Matrix 1999 Deleted Scenes 1080p.mkv",
    ]
    for name in extras:
        (movie_dir / name).write_bytes(b"extra")
    # A real movie file that SHOULD be deleted
    old_movie = "The Matrix 1999 1080p BluRay.mkv"
    (movie_dir / old_movie).write_bytes(b"old")

    count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

    assert count == 1
    assert not (movie_dir / old_movie).exists()
    for name in extras:
        assert (movie_dir / name).exists(), f"{name} must not be deleted"


def test_skips_deletion_when_new_primary_is_extras(self, tmp_path: Path) -> None:
    """If new primary is extras content, nothing is deleted — protects the real movie."""
    movie_dir = tmp_path / "The Matrix (1999)"
    movie_dir.mkdir()
    new_fname = "The.Matrix.1999.Making.Of.2160p.mkv"
    (movie_dir / new_fname).write_bytes(b"extras")
    real_movie = "The.Matrix.1999.1080p.BluRay.mkv"
    (movie_dir / real_movie).write_bytes(b"real")

    count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, Config())

    assert count == 0
    assert (movie_dir / real_movie).exists()
```

- [ ] **Step 4: Run new tests — they should FAIL (old implementation still in place)**

```bash
cd /data/movarr && uv run pytest tests/unit/test_post_processor.py::TestDeleteSupersededFiles::test_deletes_all_other_video_files_regardless_of_quality -v
```

Expected: FAIL — old logic skips some of those files by title/year/edition matching.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_post_processor.py
git commit -m "test: update delete_lower_quality tests for simplified behavior"
```

---

### Task 2: Remove unused functions from post_processor.py

**Files:**
- Modify: `src/movarr/post_processor.py`

- [ ] **Step 1: Remove `_should_delete_file` (lines ~796-815)**

Remove:
```python
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
```

- [ ] **Step 2: Remove `_collect_superseded_files` (lines ~817-840)**

Remove:
```python
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
```

- [ ] **Step 3: Remove `_candidate_basic_match` (lines ~694-720)**

Remove:
```python
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
```

- [ ] **Step 4: Remove `_compare_parsed_resolutions` (lines ~729-764)**

Remove:
```python
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
```

- [ ] **Step 5: Remove `_resolution_supersedes` (lines ~766-794)**

Remove:
```python
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
```

- [ ] **Step 6: Run tests — they should still pass (the removed functions aren't called yet since `_run_deletion` hasn't been updated)**

```bash
cd /data/movarr && uv run pytest tests/unit/test_post_processor.py::TestDeleteSupersededFiles -v
```

Expected: import errors or function-not-found errors because `_run_deletion` still references the removed functions. This is fine — the next task rewrites `_run_deletion` and `_delete_superseded_files`.

- [ ] **Step 7: Commit**

```bash
git add src/movarr/post_processor.py
git commit -m "refactor: remove unused deletion comparison functions"
```

---

### Task 3: Simplify `_delete_superseded_files` and remove `_run_deletion`

**Files:**
- Modify: `src/movarr/post_processor.py`

- [ ] **Step 1: Remove `_run_deletion` function entirely (lines ~970-1003)**

Remove the entire `_run_deletion` function.

- [ ] **Step 2: Rewrite `_delete_superseded_files` (lines ~1006-1057)**

Replace the body after the docstring with:

```python
def _delete_superseded_files(
    dst_dir: str,
    dst_base: str,
    new_primary_fname: str,
    config: Config,
    *,
    copied_fnames: frozenset[str] = frozenset(),
) -> int:
    """Delete superseded video files in *dst_dir* after a new copy.

    All video files in *dst_dir* are deleted except:
    - The newly-copied primary file
    - Files written in the current torrent run (*copied_fnames*)
    - Files matching extras/bonus-content patterns

    The search pipeline (:func:`movarr.filters._check_library_canonical`) already
    guarantees the newly-downloaded file is strictly better than anything in the
    library, so no resolution/quality re-comparison is needed here.

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

    Returns:
        Number of files successfully deleted.
    """
    preconditions = _check_delete_preconditions(dst_dir, dst_base, new_primary_fname)
    if preconditions is None:
        return 0
    video_files, resolved_dst = preconditions

    new_san = sanitise(new_primary_fname) or ""
    if _is_extras_primary(new_primary_fname, new_san):
        logger.debug(
            "Auto-delete skipped: new primary '{}' is bonus/extras content.",
            new_primary_fname,
        )
        return 0

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

- [ ] **Step 3: Clean up imports — remove unused symbols**

Change the imports:

**Old (line 31):**
```python
from movarr.filters import edition_token_set, primary_edition_token, supersession_quality_score
```

**New:**
```python
# (no imports from movarr.filters needed for deletion path)
```

Remove the entire line 31.

**Old (line 32):**
```python
from movarr.parsing import extract_after_year, extract_movie_title, extract_resolution, extract_year, sanitise
```

**New:**
```python
from movarr.parsing import extract_after_year, extract_resolution, sanitise
```

(`extract_movie_title` and `extract_year` are no longer used; `extract_after_year` is still used by `_is_extras_file`; `extract_resolution` is still used by `_resolution_from_index_title`; `sanitise` is still used by `_is_extras_file`, `_canonical_filename`, and the new `_delete_superseded_files`.)

- [ ] **Step 4: Run tests — the new simplified tests should now PASS**

```bash
cd /data/movarr && uv run pytest tests/unit/test_post_processor.py::TestDeleteSupersededFiles -v
```

Expected: all tests pass (the new behavior matches the new tests, and remaining old tests are compatible).

- [ ] **Step 5: Commit**

```bash
git add src/movarr/post_processor.py
git commit -m "refactor: simplify delete_lower_quality to delete all other video files"
```

---

### Task 4: Run full QC suite

**Files:**
- (none modified — verification only)

- [ ] **Step 1: Run ruff check and fix**

```bash
cd /data/movarr && uv run ruff check --fix src/movarr/post_processor.py tests/unit/test_post_processor.py
```

Expected: clean (no new issues).

- [ ] **Step 2: Run ruff format**

```bash
cd /data/movarr && uv run ruff format src/movarr/post_processor.py tests/unit/test_post_processor.py
```

Expected: formatting applied (if any).

- [ ] **Step 3: Run mypy**

```bash
cd /data/movarr && uv run mypy src/movarr/post_processor.py
```

Expected: no type errors.

- [ ] **Step 4: Run full test suite**

```bash
cd /data/movarr && uv run pytest tests/unit/ -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit any QC fixes**

```bash
git add -u
git commit -m "chore: QC fixes (ruff, mypy)"
```

---

### Task 5: Final commit with coverage check

- [ ] **Step 1: Run coverage for post_processor**

```bash
cd /data/movarr && uv run pytest tests/unit/test_post_processor.py --cov=movarr.post_processor --cov-report=term -v
```

Expected: coverage stays at or above existing levels; no uncovered branches in the simplified code.

- [ ] **Step 2: Final commit if all passes**

```bash
git commit --allow-empty -m "chore: coverage check passes for delete_lower_quality simplification"
```
