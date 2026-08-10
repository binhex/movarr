# pid-path CLI Option & Config Default Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--pid-path` CLI override option (following the existing `--db-path` / `--log-path` pattern) and change the default `pid_path` in newly generated config from `"configs"` to `"pids"`. The internal filename `movarr.pid` remains hardcoded in the scheduler.

**Architecture:** Four-line addition to the CLI, one-line config default change. The option mirrors `--db-path` exactly: `None` default means "not supplied, use YAML value"; the `_apply_general_overrides` helper applies non-None values onto `config.general.pid_path`. The migration already strips filenames from `pid_path` — no migration change needed.

**Tech Stack:** Python 3.12, Click 8, Pydantic v2, pytest.

---

## File Map

| File | Change |
|------|--------|
| `src/movarr/config.py:371` | Change `pid_path` default from `"configs"` → `"pids"` |
| `src/movarr/cli.py:24-25` | Add `pid_path` handling in `_apply_general_overrides` |
| `src/movarr/cli.py:104` | Insert `@click.option("--pid-path", ...)` decorator (after `--db-path`) |
| `src/movarr/cli.py:210` | Add `pid_path: str \| None` to `cli()` function signature |
| `src/movarr/cli.py:241` | Pass `pid_path=pid_path` in the `_apply_cli_overrides` call |
| `tests/unit/test_cli.py:84-88` | Remove stale `test_help_does_not_mention_removed_options` |
| `tests/unit/test_cli.py:after line 88` | Add `test_help_mentions_pid_path` in `TestCliHelp` |
| `tests/unit/test_cli.py:after line 335` | Add `TestCliPidPathOverride` class (new name to avoid collision with existing `TestCliPidPath`) |
| `tests/unit/test_config.py:1077` | Change expected default from `"configs"` → `"pids"` |
| `README.md:130` | Update default from `"configs"` → `"pids"`, add `--pid-path` override note |
| `configs/movarr.yml:11` | Update reference config `pid_path` → `pids` |

---

### Task 1: Change config default and add CLI option

**Files:**
- Modify: `src/movarr/config.py:371`
- Modify: `src/movarr/cli.py:24-25, 104, 210, 241`

- [ ] **Step 1: Change `pid_path` default in `GeneralConfig`**

In `src/movarr/config.py`, find line ~371:

```python
    pid_path: str = "configs"
```

Change to:

```python
    pid_path: str = "pids"
```

- [ ] **Step 2: Add `pid_path` override to `_apply_general_overrides`**

In `src/movarr/cli.py`, find the `_apply_general_overrides` function (line ~21). After the `db_path` block (line ~24), insert:

```python
    if overrides.get("pid_path") is not None:
        config.general.pid_path = str(overrides["pid_path"])
```

So the function becomes:

```python
def _apply_general_overrides(config: Config, overrides: dict[str, object]) -> None:
    if overrides.get("db_path") is not None:
        config.general.db_path = str(overrides["db_path"])
    if overrides.get("pid_path") is not None:
        config.general.pid_path = str(overrides["pid_path"])
    if overrides.get("library_path_list") is not None:
        config.general.library_path_list = [
            p.strip() for p in str(overrides["library_path_list"]).split(",") if p.strip()
        ]
    if overrides.get("daemon"):
        config.general.daemon_mode = "background"
```

- [ ] **Step 3: Add `--pid-path` Click option decorator**

In `src/movarr/cli.py`, find the `--db-path` option block (ends ~line 103). After it, insert:

```python
@click.option(
    "--pid-path",
    type=click.Path(file_okay=False, dir_okay=True, resolve_path=True),
    default=None,
    show_default=False,
    metavar="<dir>",
    help="Override the PID file directory from config.",
)
```

This insertion point is between the `--db-path` option block and the `--library-path-list` option block.

- [ ] **Step 4: Add `pid_path` parameter to `cli()` function signature**

In `src/movarr/cli.py`, find the `cli()` function signature (~line 206). After `db_path: str | None,`, add:

```python
    pid_path: str | None,
```

So the relevant portion becomes:

```python
def cli(
    config_path: str,
    log_path: str | None,
    log_level: str | None,
    db_path: str | None,
    pid_path: str | None,
    library_path_list: str | None,
```

- [ ] **Step 5: Pass `pid_path` in `_apply_cli_overrides` call**

In `src/movarr/cli.py`, find the `_apply_cli_overrides` call (~line 239). The `db_path=db_path,` line is around line 241. Add `pid_path=pid_path,` after it:

```python
    _apply_cli_overrides(
        config,
        db_path=db_path,
        pid_path=pid_path,
        library_path_list=library_path_list,
```

- [ ] **Step 6: Commit config and CLI changes**

```bash
git add src/movarr/config.py src/movarr/cli.py
git commit -m "feat: add --pid-path CLI option, change default pid_path to pids"
```

---

### Task 2: Update CLI tests

**Files:**
- Modify: `tests/unit/test_cli.py:84-88` (remove stale test)
- Modify: `tests/unit/test_cli.py:after line 88` (add help mention test in `TestCliHelp`)
- Modify: `tests/unit/test_cli.py:after line 335` (add `TestCliPidPathOverride` class — renamed to avoid collision with existing `TestCliPidPath` at line 264)

- [ ] **Step 1: Remove the stale test that asserts `--pid-path` is absent**

In `tests/unit/test_cli.py`, remove the `test_help_does_not_mention_removed_options` method (lines ~84-88):

```python
    def test_help_does_not_mention_removed_options(self) -> None:
        """--pid-path has been removed; --log-path, --log-level, and --db-path are kept."""
        result = CliRunner().invoke(cli, ["--help"])
        for removed in ("--pid-path",):
            assert removed not in result.output, f"removed option {removed!r} still in help"
```

Delete this entire method.

- [ ] **Step 2: Add `--pid-path` help mention test in `TestCliHelp`**

In the same `TestCliHelp` class, after `test_help_mentions_log_level` (line ~91), add:

```python
    def test_help_mentions_pid_path(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert "--pid-path" in result.output
```

- [ ] **Step 3: Add `TestCliPidPathOverride` test class**

Add a new test class after the existing `TestCliOverrides._invoke` helper (after ~line 365 — the end of the `_invoke` method definition). The class is named `TestCliPidPathOverride` to avoid collision with the existing `TestCliPidPath` class at line 264:

```python
class TestCliPidPathOverride:
    """--pid-path CLI flag overrides config.general.pid_path."""

    def test_pid_path_flag_passed_to_override(self, mocker: MockerFixture) -> None:
        """--pid-path is forwarded via _apply_cli_overrides."""
        mocker.patch("movarr.cli.create_logger")
        mock_cfg = _make_config_mock(pid_path="pids")
        mocker.patch("movarr.config.load_config", return_value=mock_cfg)
        CliRunner().invoke(cli, ["--pid-path", "/run/movarr", "--test"])
        # config.general.pid_path was updated by the override
        assert mock_cfg.general.pid_path == "/run/movarr"

    def test_pid_path_default_from_config_used_when_not_supplied(self, mocker: MockerFixture) -> None:
        """Without --pid-path, config.general.pid_path is unchanged."""
        mocker.patch("movarr.cli.create_logger")
        mock_cfg = _make_config_mock(pid_path="/custom/pids")
        mocker.patch("movarr.config.load_config", return_value=mock_cfg)
        CliRunner().invoke(cli, ["--test"])
        assert mock_cfg.general.pid_path == "/custom/pids"

    def test_pid_path_flag_overrides_config(self, mocker: MockerFixture) -> None:
        """--pid-path overrides config.general.pid_path when supplied."""
        mocker.patch("movarr.cli.create_logger")
        mock_cfg = _make_config_mock(pid_path="pids")
        mocker.patch("movarr.config.load_config", return_value=mock_cfg)
        CliRunner().invoke(cli, ["--pid-path", "/tmp/pids", "--test"])
        assert mock_cfg.general.pid_path == "/tmp/pids"
```

- [ ] **Step 4: Run the new tests to verify they pass**

```bash
uv run pytest tests/unit/test_cli.py::TestCliHelp::test_help_mentions_pid_path tests/unit/test_cli.py::TestCliPidPathOverride -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Run the full CLI test suite**

```bash
uv run pytest tests/unit/test_cli.py -v
```

Expected: all tests PASS, no failures introduced. The existing `TestCliPidPath` class (line 264) continues to pass because it tests config-based pid_path behavior which is unchanged.

- [ ] **Step 6: Commit test changes**

```bash
git add tests/unit/test_cli.py
git commit -m "test: add --pid-path CLI option tests, remove stale absence assertion"
```

---

### Task 3: Update config default test

**Files:**
- Modify: `tests/unit/test_config.py:1077`

- [ ] **Step 1: Change expected default in test**

In `tests/unit/test_config.py`, find line ~1077:

```python
        assert config.general.pid_path == "configs"
```

Change to:

```python
        assert config.general.pid_path == "pids"
```

- [ ] **Step 2: Run the test to verify**

```bash
uv run pytest tests/unit/test_config.py -k "test_load_config_creates_default" -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_config.py
git commit -m "test: update pid_path default assertion from configs to pids"
```

---

### Task 4: Update reference config

**Files:**
- Modify: `configs/movarr.yml:11`

- [ ] **Step 1: Update `pid_path` in reference config**

In `configs/movarr.yml`, find line 11:

```yaml
  pid_path: configs/movarr.pid
```

Change to:

```yaml
  pid_path: pids
```

- [ ] **Step 2: Commit**

```bash
git add configs/movarr.yml
git commit -m "chore: update reference config pid_path to new default pids"
```

---

### Task 5: Update README documentation

**Files:**
- Modify: `README.md:130`

- [ ] **Step 1: Update `pid_path` row in README config table**

In `README.md`, find line ~130:

```
| `pid_path` | Directory for the PID file (`movarr.pid` is created inside). Empty string disables PID file creation. | `"configs"` |
```

Change to:

```
| `pid_path` | Directory for the PID file (`movarr.pid` is created inside). Empty string disables PID file creation. Overridden by `--pid-path`. | `"pids"` |
```

This matches the existing pattern for `db_path` and `log_path` rows which already mention their respective `--db-path` / `--log-path` override options.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: update pid_path default to pids, note --pid-path override"
```

---

### Task 6: Full QC gate

- [ ] **Step 1: Run ruff check + format**

```bash
uv run ruff check --fix . && uv run ruff format .
```

Expected: clean output, no errors for modified files.

- [ ] **Step 2: Run mypy**

```bash
uv run mypy .
```

Expected: clean, no new type errors.

- [ ] **Step 3: Run full test suite with coverage**

```bash
uv run pytest --cov=movarr --cov-fail-under=80 -v
```

Expected: all tests PASS, coverage ≥ 80%.

- [ ] **Step 4: Run shellcheck on any modified shell scripts** (none expected — skip)

- [ ] **Step 5: Final commit (if any QC fixes needed)** or confirm all clean.
