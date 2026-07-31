# Post-Process Hooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `post_process.hooks` config block with three shell-command hooks (`post_copy`, `pre_delete`, `post_delete`) that fire at defined points in the post-processing pipeline, enabling users to run arbitrary commands (e.g. `chattr -i {dir}/*`, `trimarr ...`) against the destination directory.

**Architecture:** A new `PostProcessHooksConfig` Pydantic model is nested under `PostProcessConfig.hooks`. A single `_run_hook(command, dir_path, label)` helper in `post_processor.py` handles `{dir}` substitution and `subprocess.run` (shell=True for glob expansion). Three call sites wire the hooks into the existing pipeline: `post_copy` after all copies succeed in `_process_one`; `pre_delete` (abort-on-failure) and `post_delete` (best-effort) in `_delete_superseded_files`.

**Tech Stack:** Python 3.12+, Pydantic v2, subprocess (stdlib), pytest + pytest-mock, uv

---

## File Map

| File | Change |
|---|---|
| `src/movarr/config.py` | Add `PostProcessHooksConfig` model; add `hooks` field to `PostProcessConfig`; add `_migrate_v213_to_v214`; bump `_CONFIG_VERSION` to `"2.14.0"`; add `MIGRATIONS["2.13.0"]` entry |
| `src/movarr/post_processor.py` | Add `import subprocess`; add `_run_hook` helper; wire `post_copy` in `_process_one`; wire `pre_delete`/`post_delete` in `_delete_superseded_files` |
| `tests/unit/test_config.py` | Import `_migrate_v213_to_v214`; update 12 chain-migration version assertions from `"2.13.0"` → `"2.14.0"`; add `TestMigrationV213ToV214` class |
| `tests/unit/test_post_processor.py` | Add `TestRunHook` class; add tests for `post_copy` wiring; add tests for `pre_delete`/`post_delete` wiring |

---

## Task 1: `PostProcessHooksConfig` model + migration

**Files:**
- Modify: `src/movarr/config.py`
- Modify: `tests/unit/test_config.py`

### Steps

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_config.py`, add the import and new test class at the end of the file:

```python
from movarr.config import (
    # existing imports ...
    _migrate_v212_to_v213,
    _migrate_v213_to_v214,   # add this
    load_config,
)
```

Add at the end of the file:

```python
class TestMigrationV213ToV214:
    """Tests for the v2.13.0 -> v2.14.0 config migration."""

    def test_adds_hooks_block(self) -> None:
        """Migration inserts post_process.hooks as an empty dict."""
        raw: dict = {"general": {"config_version": "2.13.0"}}
        result = _migrate_v213_to_v214(raw)
        assert result["post_process"]["hooks"] == {}

    def test_does_not_overwrite_existing_hooks(self) -> None:
        """Migration does not clobber a pre-existing hooks block."""
        raw: dict = {
            "general": {"config_version": "2.13.0"},
            "post_process": {"hooks": {"pre_delete": "chattr -i {dir}/*"}},
        }
        result = _migrate_v213_to_v214(raw)
        assert result["post_process"]["hooks"]["pre_delete"] == "chattr -i {dir}/*"

    def test_bumps_version_to_v214(self) -> None:
        raw: dict = {"general": {"config_version": "2.13.0"}}
        assert _migrate_v213_to_v214(raw)["general"]["config_version"] == "2.14.0"

    def test_preserves_existing_post_process_keys(self) -> None:
        """Migration does not drop existing post_process settings."""
        raw: dict = {
            "general": {"config_version": "2.13.0"},
            "post_process": {"delete_lower_quality": True},
        }
        result = _migrate_v213_to_v214(raw)
        assert result["post_process"]["delete_lower_quality"] is True
        assert "hooks" in result["post_process"]
```

Also update the 12 existing chain-migration version assertions. Search for `assert cfg.general.config_version == "2.13.0"` and change each to `"2.14.0"`. There are 12 occurrences — use a targeted find-and-replace, verifying each is a version assertion and not a migration-specific test.

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_config.py::TestMigrationV213ToV214 -v
```

Expected: `ImportError` (function doesn't exist yet) or `FAILED`.

- [ ] **Step 3: Implement the model and migration in `config.py`**

**3a.** Change `_CONFIG_VERSION` (line 18):
```python
_CONFIG_VERSION = "2.14.0"
```

**3b.** Add `PostProcessHooksConfig` immediately before `PostProcessConfig` (currently around line 447):
```python
class PostProcessHooksConfig(BaseModel):
    """Shell commands to run at defined points in the post-processing pipeline.

    Each field is a command template. Leave empty (the default) to disable.
    The placeholder ``{dir}`` is substituted with the resolved absolute path of
    the destination directory before the command is executed.

    ``shell=True`` is used so that glob patterns such as ``chattr -i {dir}/*``
    are expanded by the shell. Commands come from the user's own config file,
    so the trust boundary is identical to the rest of the configuration.
    """

    post_copy: str = ""
    pre_delete: str = ""
    post_delete: str = ""
```

**3c.** Add the `hooks` field to `PostProcessConfig` after the `delete_lower_quality` line:
```python
hooks: PostProcessHooksConfig = Field(default_factory=PostProcessHooksConfig)
```

**3d.** Add the migration function after `_migrate_v212_to_v213`:
```python
def _migrate_v213_to_v214(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.13.0 -> v2.14.0: add post_process.hooks (all hooks disabled by default)."""
    raw.setdefault("post_process", {}).setdefault("hooks", {})
    raw.setdefault("general", {})["config_version"] = "2.14.0"
    return raw
```

**3e.** Add the entry to `MIGRATIONS`:
```python
MIGRATIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    # ... existing entries ...
    "2.12.0": _migrate_v212_to_v213,
    "2.13.0": _migrate_v213_to_v214,   # add this line
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_config.py -v
```

Expected: all config tests pass (currently 84 tests + 4 new = 88).

- [ ] **Step 5: Adversarial review**

REQUIRED SUB-SKILL: Use superpowers:adversarial-review

- [ ] **Step 6: Commit**

```bash
git add src/movarr/config.py tests/unit/test_config.py
git commit -m "feat(config): add post_process.hooks config block (v2.14.0)"
```

---

## Task 2: `_run_hook` helper

**Files:**
- Modify: `src/movarr/post_processor.py`
- Modify: `tests/unit/test_post_processor.py`

### Steps

- [ ] **Step 1: Write the failing tests**

Add `TestRunHook` in `tests/unit/test_post_processor.py`. Import `_run_hook` alongside the existing private-function imports at the top of the file:

```python
from movarr.post_processor import (
    # existing imports ...
    _run_hook,
)
```

Add the test class:

```python
class TestRunHook:
    """Tests for the _run_hook subprocess helper."""

    def test_returns_true_on_zero_exit(self, mocker: MockerFixture) -> None:
        mock_run = mocker.patch("movarr.post_processor.subprocess.run")
        mock_run.return_value = mocker.Mock(returncode=0, stdout="", stderr="")
        assert _run_hook("echo hello", "/tmp/movie", "post_copy") is True

    def test_returns_false_on_nonzero_exit(self, mocker: MockerFixture) -> None:
        mock_run = mocker.patch("movarr.post_processor.subprocess.run")
        mock_run.return_value = mocker.Mock(returncode=1, stdout="", stderr="error")
        assert _run_hook("false", "/tmp/movie", "pre_delete") is False

    def test_substitutes_dir_placeholder(self, mocker: MockerFixture) -> None:
        mock_run = mocker.patch("movarr.post_processor.subprocess.run")
        mock_run.return_value = mocker.Mock(returncode=0, stdout="", stderr="")
        _run_hook("chattr -i {dir}/*", "/mnt/media/The Matrix (1999)", "pre_delete")
        cmd = mock_run.call_args[0][0]
        assert cmd == "chattr -i /mnt/media/The Matrix (1999)/*"

    def test_uses_shell_true(self, mocker: MockerFixture) -> None:
        mock_run = mocker.patch("movarr.post_processor.subprocess.run")
        mock_run.return_value = mocker.Mock(returncode=0, stdout="", stderr="")
        _run_hook("echo {dir}", "/tmp/movie", "post_copy")
        assert mock_run.call_args[1]["shell"] is True

    def test_returns_false_on_timeout(self, mocker: MockerFixture) -> None:
        import subprocess as _subprocess
        mock_run = mocker.patch("movarr.post_processor.subprocess.run")
        mock_run.side_effect = _subprocess.TimeoutExpired(cmd="echo", timeout=300)
        assert _run_hook("echo {dir}", "/tmp/movie", "post_copy") is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_post_processor.py::TestRunHook -v
```

Expected: `ImportError` — `_run_hook` does not exist yet.

- [ ] **Step 3: Implement `_run_hook` in `post_processor.py`**

**3a.** Add `import subprocess` to the stdlib imports block (after `import re`):

```python
import re
import subprocess
```

**3b.** Add `_run_hook` after the `_EXTRAS_RE` constant (before `_safe_path_component`):

```python
def _run_hook(command: str, dir_path: str, label: str) -> bool:
    """Run a post-process hook command, substituting ``{dir}`` with *dir_path*.

    Uses ``shell=True`` so that glob patterns (e.g. ``chattr -i {dir}/*``) are
    expanded by the shell. The command originates from the user's own config
    file, so the trust boundary is the same as the rest of the configuration.

    Args:
        command: Shell command template. ``{dir}`` is replaced with *dir_path*.
        dir_path: Absolute path of the destination directory.
        label: Hook name for log messages (e.g. ``"pre_delete"``).

    Returns:
        True if the command exits with code 0, False otherwise.
    """
    cmd = command.replace("{dir}", dir_path)
    logger.info("Running {} hook: {}", label, cmd)
    try:
        result = subprocess.run(  # noqa: S602
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.stdout:
            logger.debug("{} hook stdout: {}", label, result.stdout.rstrip())
        if result.stderr:
            logger.debug("{} hook stderr: {}", label, result.stderr.rstrip())
        if result.returncode != 0:
            logger.warning("{} hook exited with code {}.", label, result.returncode)
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error("{} hook timed out after 300 s.", label)
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_post_processor.py::TestRunHook -v
```

Expected: 5 passed.

- [ ] **Step 5: Adversarial review**

REQUIRED SUB-SKILL: Use superpowers:adversarial-review

- [ ] **Step 6: Commit**

```bash
git add src/movarr/post_processor.py tests/unit/test_post_processor.py
git commit -m "feat(post_processor): add _run_hook subprocess helper"
```

---

## Task 3: Wire `post_copy` hook into `_process_one`

**Files:**
- Modify: `src/movarr/post_processor.py`
- Modify: `tests/unit/test_post_processor.py`

The `post_copy` hook fires after `db.mark_completed()` and before the
`delete_lower_quality` pass, so trimarr processes the new file before superseded
files are removed. It fires only when `all_ok` is True (all copies succeeded).

### Steps

- [ ] **Step 1: Write the failing tests**

Add a new class `TestProcessOneHooks` to `tests/unit/test_post_processor.py`.
It follows the exact same helper pattern as `TestProcessOne` (already in the file).
The `_process_one` signature is `(torrent, config, qbt, db)` — `db_record` is
fetched internally via `db.find_by_tag()`.

```python
class TestProcessOneHooks:
    """Tests for post_copy hook wiring in _process_one."""

    def _config(self) -> Config:
        cfg = Config()
        cfg.post_process.default_copy_library = DefaultCopyLibraryConfig(
            hd_path="/media/hd", uhd_path=""
        )
        return cfg

    def _torrent(self) -> dict[str, Any]:
        return {
            "torrent_tag": "tag1",
            "torrent_hash": "abc123",
            "torrent_save_path": "/downloads",
            "torrent_file_list": [
                {"file_name": "movie/The Matrix 1999 1080p.mkv", "file_size": 4_000_000_000},
            ],
        }

    def _db_record(self, mocker: MockerFixture) -> Any:
        rec = mocker.MagicMock()
        rec.imdb_title = "The Matrix"
        rec.imdb_year = "1999"
        rec.imdb_genres_list = "[]"
        rec.imdb_certification = ""
        rec.imdb_cert_source = "imdbpie"
        rec.index_title = "The Matrix 1999 1080p BluRay"
        return rec

    def test_post_copy_hook_fires_on_successful_copy(
        self, mocker: MockerFixture
    ) -> None:
        """post_copy hook is called after all files copy successfully."""
        config = self._config()
        config.post_process.hooks.post_copy = "echo {dir}"

        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        qbt = mocker.MagicMock()

        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/movie.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor.copy_with_verify", return_value=True)
        mock_hook = mocker.patch("movarr.post_processor._run_hook", return_value=True)

        _process_one(self._torrent(), config, qbt, db)

        labels = [call[0][2] for call in mock_hook.call_args_list]
        assert "post_copy" in labels

    def test_post_copy_hook_does_not_fire_on_copy_failure(
        self, mocker: MockerFixture
    ) -> None:
        """post_copy hook is NOT called when a copy fails."""
        config = self._config()
        config.post_process.hooks.post_copy = "echo {dir}"

        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        qbt = mocker.MagicMock()

        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/movie.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor.copy_with_verify", return_value=False)
        mock_hook = mocker.patch("movarr.post_processor._run_hook", return_value=True)

        _process_one(self._torrent(), config, qbt, db)

        labels = [call[0][2] for call in mock_hook.call_args_list]
        assert "post_copy" not in labels

    def test_post_copy_hook_not_called_when_empty(
        self, mocker: MockerFixture
    ) -> None:
        """No subprocess is spawned when post_copy is empty string (default)."""
        config = self._config()
        # hooks.post_copy defaults to "" — intentionally left unset

        db = mocker.MagicMock()
        db.find_by_tag.return_value = self._db_record(mocker)
        qbt = mocker.MagicMock()

        mocker.patch("movarr.post_processor._build_copy_list", return_value=["/dl/movie.mkv"])
        mocker.patch("movarr.post_processor._resolve_destination", return_value="/media/hd")
        mocker.patch("movarr.post_processor.make_directory", return_value=True)
        mocker.patch("movarr.post_processor.copy_with_verify", return_value=True)
        mock_hook = mocker.patch("movarr.post_processor._run_hook", return_value=True)

        _process_one(self._torrent(), config, qbt, db)

        labels = [call[0][2] for call in mock_hook.call_args_list]
        assert "post_copy" not in labels
```
- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_post_processor.py::TestProcessOneHooks -v
```

Expected: FAILED — hook call is never made.

- [ ] **Step 3: Wire `post_copy` in `_process_one`**

In `post_processor.py`, find the `if all_ok:` block (around line 173). Add the
`post_copy` hook call after `db.mark_completed()` and before the
`delete_lower_quality` guard:

```python
    if all_ok:
        db.mark_completed(tag)
        logger.info("Marked tag '{}' as completed.", tag)
        if config.post_process.hooks.post_copy:
            _run_hook(config.post_process.hooks.post_copy, dst_dir, "post_copy")
        if config.post_process.delete_lower_quality and canonical_fname in copied_fnames:
            deleted = _delete_superseded_files(
                dst_dir, dst_base, canonical_fname, config, copied_fnames=frozenset(copied_fnames)
            )
            if deleted:
                logger.info("Auto-deleted {} lower-quality file(s) from '{}'.", deleted, dst_dir)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_post_processor.py::TestProcessOneHooks -v
```

Expected: 3 passed.

- [ ] **Step 5: Adversarial review**

REQUIRED SUB-SKILL: Use superpowers:adversarial-review

- [ ] **Step 6: Commit**

```bash
git add src/movarr/post_processor.py tests/unit/test_post_processor.py
git commit -m "feat(post_processor): wire post_copy hook into _process_one"
```

---

## Task 4: Wire `pre_delete` / `post_delete` hooks into `_delete_superseded_files`

**Files:**
- Modify: `src/movarr/post_processor.py`
- Modify: `tests/unit/test_post_processor.py`

`pre_delete` fires after both safety guards pass and **aborts** the deletion pass
(returns 0) on failure. `post_delete` fires after the deletion loop completes — it
is best-effort and never aborts. `post_delete` does NOT fire if `pre_delete` aborts.

### Steps

- [ ] **Step 1: Write the failing tests**

Add `TestDeleteSupersededFilesHooks` to `tests/unit/test_post_processor.py`:

```python
class TestDeleteSupersededFilesHooks:
    """Tests for pre_delete / post_delete hook wiring."""

    def test_pre_delete_hook_fires_before_deletion(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """pre_delete hook is called when the deletion pass starts."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 2160p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        (movie_dir / "The Matrix 1999 1080p BluRay.mkv").write_bytes(b"old")

        config = Config()
        config.post_process.hooks.pre_delete = "chattr -i {dir}/*"

        mock_hook = mocker.patch("movarr.post_processor._run_hook", return_value=True)
        mocker.patch("movarr.post_processor.delete_file", return_value=True)

        _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, config)

        mock_hook.assert_any_call("chattr -i {dir}/*", mocker.ANY, "pre_delete")

    def test_pre_delete_hook_failure_aborts_deletion(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """If pre_delete hook returns False, no files are deleted and count is 0."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 2160p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        old_fname = "The Matrix 1999 1080p BluRay.mkv"
        (movie_dir / old_fname).write_bytes(b"old")

        config = Config()
        config.post_process.hooks.pre_delete = "chattr -i {dir}/*"

        mocker.patch("movarr.post_processor._run_hook", return_value=False)
        mock_delete = mocker.patch("movarr.post_processor.delete_file")

        count = _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, config)

        assert count == 0
        mock_delete.assert_not_called()
        assert (movie_dir / old_fname).exists()

    def test_post_delete_hook_fires_after_deletion(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """post_delete hook is called after the deletion loop completes."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 2160p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        (movie_dir / "The Matrix 1999 1080p BluRay.mkv").write_bytes(b"old")

        config = Config()
        config.post_process.hooks.post_delete = "chattr +i {dir}/*"

        call_order: list[str] = []

        def fake_hook(cmd: str, d: str, label: str) -> bool:
            call_order.append(label)
            return True

        mocker.patch("movarr.post_processor._run_hook", side_effect=fake_hook)
        mocker.patch("movarr.post_processor.delete_file", return_value=True)

        _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, config)

        assert "post_delete" in call_order


    def test_post_delete_does_not_fire_when_pre_delete_aborts(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """Cleaner version: post_delete label never appears in call list when pre_delete fails."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 2160p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        (movie_dir / "The Matrix 1999 1080p BluRay.mkv").write_bytes(b"old")

        config = Config()
        config.post_process.hooks.pre_delete = "chattr -i {dir}/*"
        config.post_process.hooks.post_delete = "chattr +i {dir}/*"

        called_labels: list[str] = []

        def fake_hook(cmd: str, d: str, label: str) -> bool:
            called_labels.append(label)
            return False  # always fail

        mocker.patch("movarr.post_processor._run_hook", side_effect=fake_hook)

        _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, config)

        assert "post_delete" not in called_labels

    def test_hooks_not_called_when_empty(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """No subprocess is spawned when hooks are empty strings."""
        movie_dir = tmp_path / "The Matrix (1999)"
        movie_dir.mkdir()
        new_fname = "The Matrix 1999 2160p Remux.mkv"
        (movie_dir / new_fname).write_bytes(b"new")
        (movie_dir / "The Matrix 1999 1080p BluRay.mkv").write_bytes(b"old")

        config = Config()
        # hooks default to "" — leave unset
        mock_hook = mocker.patch("movarr.post_processor._run_hook")

        _delete_superseded_files(str(movie_dir), str(tmp_path), new_fname, config)

        mock_hook.assert_not_called()
```


- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_post_processor.py::TestDeleteSupersededFilesHooks -v
```

Expected: FAILED — hooks are not wired yet.

- [ ] **Step 3: Wire `pre_delete` and `post_delete` in `_delete_superseded_files`**

In `post_processor.py`, in `_delete_superseded_files`, add the hook calls after
Safety Guard 2 (the video-file count cap) and after the deletion loop. The
function currently ends with `return deleted`. The new structure:

```python
    # (after Safety Guard 2 — the _MAX_VIDEO_FILES_IN_MOVIE_DIR check)

    if config.post_process.hooks.pre_delete:
        if not _run_hook(config.post_process.hooks.pre_delete, str(resolved_dst), "pre_delete"):
            logger.error(
                "pre_delete hook failed for '{}'; aborting deletion pass.", dst_dir
            )
            return 0

    # ... existing protected set, new_san, new_title, new_res_str, deleted=0, for loop ...

    if config.post_process.hooks.post_delete:
        _run_hook(config.post_process.hooks.post_delete, str(resolved_dst), "post_delete")

    return deleted
```

Note: `resolved_dst` is already computed earlier in the function as the resolved
`Path` of `dst_dir`. Pass `str(resolved_dst)` to `_run_hook` for a consistent
absolute path.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_post_processor.py::TestDeleteSupersededFilesHooks -v
```

Expected: all pass.

- [ ] **Step 5: Adversarial review**

REQUIRED SUB-SKILL: Use superpowers:adversarial-review

- [ ] **Step 6: Commit**

```bash
git add src/movarr/post_processor.py tests/unit/test_post_processor.py
git commit -m "feat(post_processor): wire pre_delete/post_delete hooks into _delete_superseded_files"
```

---

## Task 5: QC pass

**Files:** all modified files

- [ ] **Step 1: Ruff**

```bash
uv run ruff check --fix . && uv run ruff format .
```

Fix any issues introduced by this feature. Pre-existing issues in unmodified files
do not need addressing.

- [ ] **Step 2: Mypy**

```bash
uv run mypy .
```

Expected: only the pre-existing `src/movarr/imdb_search.py:32: error: Unused "type: ignore" comment` — no new errors. Fix any new type errors.

- [ ] **Step 3: Full test suite**

```bash
uv run pytest --cov=movarr --cov-fail-under=80 -v
```

Expected: all tests pass, coverage ≥ 80% (currently 97.93%).

- [ ] **Step 4: Commit if ruff/mypy made changes**

```bash
git add -A
git commit -m "chore: ruff/mypy fixes for post-process hooks"
```

Only commit if there are staged changes. Skip if clean.
