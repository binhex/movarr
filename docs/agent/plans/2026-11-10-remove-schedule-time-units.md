# Remove Obsolete `schedule_time_units` Key — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the unused `schedule_time_units` key from the three schedule blocks during a config migration, so the dead key and its permanent unknown-key warning disappear after one upgrade.

**Architecture:** One new sequential migration (`_migrate_v225_to_v226`) registered at `MIGRATIONS["2.25.0"]`, bumping `_CONFIG_VERSION` to `2.26.0`. It pops the key from `acquisition`, `queue_management` and `post_processing` only when those blocks already exist as mappings, logs one INFO line naming the cleaned blocks, and makes no model, scheduler or CLI change.

**Tech Stack:** Python 3.12, pydantic v2, loguru, pytest + pytest-mock, ruff, mypy, uv.

**Spec:** `docs/agent/specs/2026-11-10-remove-schedule-time-units-design.md`

---

## Scope check

The spec covers a single subsystem (config migration plus its tests and one README sentence). No split is needed.

## File structure

| File | Action | Responsibility |
| --- | --- | --- |
| `src/movarr/config.py` | Modify | `_CONFIG_VERSION` (2.26.0), `_SCHEDULE_TASK_BLOCKS`, `_MISSING` sentinel, `_migrate_v225_to_v226`, `MIGRATIONS["2.25.0"]` |
| `tests/unit/test_config.py` | Modify | New `TestV225Migration` class; bump 25 existing `"2.25.0"` assertion expectations to `"2.26.0"` |
| `README.md` | Modify | One sentence appended to the schedule-section migration paragraph |

Verified facts this plan relies on (do not re-derive):

- `schedule_time_units` appears nowhere in `src/`, `tests/` or `README.md`; it is only in the legacy
  `docs/specs/SIPHONATOR_SPEC.md`. Movarr therefore treats it as an unknown key and logs
  `Unknown config keys (will be ignored): …`.
- `_CONFIG_VERSION` is `"2.25.0"`, and `MIGRATIONS` ends at `"2.24.0": _migrate_v224_to_v225`.
- Migrations run sequentially until `_CONFIG_VERSION` is reached; each rewrites the file after writing
  `config.yml.bak.<from-version>`.
- `_section(parent, key)` materialises a missing key as `{}`, so it must **not** be used to read the three
  blocks — a minimal config would gain empty blocks.
- `tests/unit/test_config.py` contains exactly 25 double-quoted `"2.25.0"` assertions and **no** single-quoted
  `'2.25.0'` YAML inputs, so a double-quote-only sweep is safe.

---

### Task 1: Write the failing migration tests

**Files:**
- Test: `tests/unit/test_config.py` (append a new class)

- [ ] **Step 1: Append the new test class**

Append to `tests/unit/test_config.py`:

```python
class TestV225Migration:
    """Migration 2.25.0 -> 2.26.0 removes the obsolete schedule_time_units key."""

    def _write(self, tmp_path: Path, body: str) -> Path:
        config_path = tmp_path / "config.yml"
        config_path.write_text(body)
        return config_path

    def _on_disk(self, config_path: Path) -> dict:
        return yaml.safe_load(config_path.read_text())

    def test_key_is_removed_from_all_three_blocks(self, tmp_path: Path) -> None:
        """Every task block loses the key, and the file records the new version."""
        config_path = self._write(
            tmp_path,
            "general:\n  config_version: '2.25.0'\n"
            "schedule:\n"
            "  acquisition:\n    schedule_time_mins: 30\n    schedule_time_units: minutes\n"
            "  queue_management:\n    schedule_time_mins: 5\n    schedule_time_units: minutes\n"
            "  post_processing:\n    cron: '*/5 0-5 * * *'\n    schedule_time_units: minutes\n",
        )

        load_config(config_path)

        on_disk = self._on_disk(config_path)["schedule"]
        for block in ("acquisition", "queue_management", "post_processing"):
            assert "schedule_time_units" not in on_disk[block], block
        assert self._on_disk(config_path)["general"]["config_version"] == "2.26.0"

    def test_one_info_line_names_every_cleaned_block(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """The removal is announced once, naming each block."""
        config_path = self._write(
            tmp_path,
            "general:\n  config_version: '2.25.0'\n"
            "schedule:\n"
            "  acquisition:\n    schedule_time_mins: 30\n    schedule_time_units: minutes\n"
            "  queue_management:\n    schedule_time_mins: 5\n    schedule_time_units: minutes\n"
            "  post_processing:\n    cron: '*/5 * * * *'\n    schedule_time_units: minutes\n",
        )
        mock_info = mocker.patch("movarr.config.logger.info")

        load_config(config_path)

        # The migration machinery logs its own INFO lines, so select ours before counting.
        messages = [str(call.args[0]).format(*call.args[1:]) for call in mock_info.call_args_list]
        removed = [message for message in messages if "Removed obsolete schedule_time_units" in message]
        assert len(removed) == 1
        assert "acquisition" in removed[0]
        assert "queue_management" in removed[0]
        assert "post_processing" in removed[0]

    @pytest.mark.parametrize("value", ["hours", "seconds", "null", "''"])
    def test_value_is_removed_whatever_it_says(self, tmp_path: Path, value: str) -> None:
        """The key was never interpreted, so no value is preserved or converted."""
        rendered = "null" if value == "null" else ("" if value == "''" else value)
        config_path = self._write(
            tmp_path,
            "general:\n  config_version: '2.25.0'\n"
            "schedule:\n"
            "  acquisition:\n"
            f"    schedule_time_mins: 30\n    schedule_time_units: {rendered}\n",
        )

        load_config(config_path)

        assert "schedule_time_units" not in self._on_disk(config_path)["schedule"]["acquisition"]

    def test_absent_key_is_a_noop(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """Nothing is logged and no block is created when the key is absent."""
        config_path = self._write(tmp_path, "general:\n  config_version: '2.25.0'\n")
        mock_info = mocker.patch("movarr.config.logger.info")

        load_config(config_path)

        messages = [str(call.args[0]) for call in mock_info.call_args_list]
        assert not any("Removed obsolete schedule_time_units" in message for message in messages)
        on_disk = self._on_disk(config_path)
        assert on_disk["general"]["config_version"] == "2.26.0"
        # No block was invented: the file keeps the schema defaults and nothing else.
        assert set(on_disk["schedule"]) == {
            "timezone",
            "acquisition",
            "queue_management",
            "post_processing",
        }
        for block in ("acquisition", "queue_management", "post_processing"):
            assert "schedule_time_units" not in on_disk["schedule"][block]

    def test_neighbouring_keys_and_sections_survive(self, tmp_path: Path) -> None:
        """Only the one key is removed."""
        config_path = self._write(
            tmp_path,
            "general:\n  config_version: '2.25.0'\n"
            "schedule:\n"
            "  acquisition:\n"
            "    enabled: false\n    schedule_time_mins: 45\n    run_on_start: false\n"
            "    schedule_time_units: minutes\n"
            "  post_processing:\n    cron: '*/5 0-5 * * *'\n    schedule_time_units: minutes\n"
            "filters:\n  minimum_rating: 7.5\n",
        )

        config = load_config(config_path)

        assert config.schedule.acquisition.schedule_time_mins == 45
        assert config.schedule.acquisition.enabled is False
        assert config.schedule.acquisition.run_on_start is False
        assert config.schedule.post_processing.cron == ["*/5 0-5 * * *"]
        on_disk = self._on_disk(config_path)
        assert on_disk["filters"]["minimum_rating"] == 7.5

    def test_backup_of_the_old_file_is_created(self, tmp_path: Path) -> None:
        """The pre-migration file is backed up before rewriting."""
        config_path = self._write(tmp_path, "general:\n  config_version: '2.25.0'\n")

        load_config(config_path)

        assert (tmp_path / "config.yml.bak.2.25.0").exists()

    def test_migration_is_idempotent(self, tmp_path: Path) -> None:
        """A second load changes nothing beyond what the first one did."""
        config_path = self._write(
            tmp_path,
            "general:\n  config_version: '2.25.0'\n"
            "schedule:\n  acquisition:\n    schedule_time_mins: 30\n    schedule_time_units: minutes\n",
        )

        first = load_config(config_path)
        first_text = config_path.read_text()
        second = load_config(config_path)

        assert first.schedule.acquisition.schedule_time_mins == second.schedule.acquisition.schedule_time_mins == 30
        assert config_path.read_text() == first_text

    def test_no_unknown_key_warning_after_migration(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """The key that produced the unknown-key warning is gone from disk."""
        config_path = self._write(
            tmp_path,
            "general:\n  config_version: '2.25.0'\n"
            "schedule:\n  post_processing:\n    cron: '*/5 * * * *'\n    schedule_time_units: minutes\n",
        )
        mock_warning = mocker.patch("movarr.config.logger.warning")

        load_config(config_path)

        assert not any("schedule_time_units" in str(call) for call in mock_warning.call_args_list)

    def test_null_block_is_ignored(self, tmp_path: Path) -> None:
        """A block written as null is neither materialised nor treated as an error."""
        config_path = self._write(
            tmp_path,
            "general:\n  config_version: '2.25.0'\nschedule:\n  acquisition:\n",
        )

        config = load_config(config_path)

        assert config.schedule.acquisition.schedule_time_mins == 30
        assert self._on_disk(config_path)["general"]["config_version"] == "2.26.0"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_config.py::TestV225Migration -v --no-cov`

Expected: FAIL — the version stays at `2.25.0` and the key survives, e.g.
`assert 'schedule_time_units' not in {...'schedule_time_units': 'minutes'}` and
`assert '2.25.0' == '2.26.0'`.

Do not change any production code in this task.

---

### Task 2: Implement the migration and bump the schema version

**Files:**
- Modify: `src/movarr/config.py` (version constant near line 22; new migration after
  `_migrate_v224_to_v225`, around line 568-597; `MIGRATIONS` registry)
- Test: `tests/unit/test_config.py` (the 25 existing `"2.25.0"` assertions)

- [ ] **Step 1: Add the block and sentinel constants**

Immediately after `_INITIAL_CONFIG_VERSION = "1.0.0"` in `src/movarr/config.py`:

```python
# Schedule task blocks that the schema defines; only these are swept by the 2.25.0 -> 2.26.0
# migration, and every one of them may carry the legacy siphonator ``schedule_time_units`` key.
_SCHEDULE_TASK_BLOCKS = ("acquisition", "queue_management", "post_processing")
# Distinguishes "key absent" from "key present with a null value" so a null is still removed.
_MISSING = object()
```

- [ ] **Step 2: Raise the schema version**

Replace:

```python
_CONFIG_VERSION = "2.25.0"
```

with:

```python
_CONFIG_VERSION = "2.26.0"
```

- [ ] **Step 3: Add the migration**

Insert directly after `_migrate_v224_to_v225` (before the `# Bind table-generated functions…` comment block):

```python
def _migrate_v225_to_v226(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.25.0 -> v2.26.0: drop the obsolete ``schedule_time_units`` key.

    The key came from siphonator and was never read by movarr, so its only effect was an
    unknown-key warning. Values are removed unconditionally, blocks that are absent or are not
    mappings are left alone (never materialised), and one INFO line names the cleaned blocks.
    """
    schedule = raw.get("schedule")
    cleaned: list[str] = []
    if isinstance(schedule, dict):
        for name in _SCHEDULE_TASK_BLOCKS:
            block = schedule.get(name)
            if isinstance(block, dict) and block.pop("schedule_time_units", _MISSING) is not _MISSING:
                cleaned.append(name)
    if cleaned:
        logger.info("Removed obsolete schedule_time_units from: {}.", ", ".join(cleaned))
    _section(raw, "general")["config_version"] = "2.26.0"
    return raw
```

- [ ] **Step 4: Register the migration**

Add the final entry to `MIGRATIONS`:

```python
    "2.25.0": _migrate_v225_to_v226,
```

So the tail reads:

```python
    "2.23.0": _migrate_v223_to_v224,
    "2.24.0": _migrate_v224_to_v225,
    "2.25.0": _migrate_v225_to_v226,
}
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `uv run pytest tests/unit/test_config.py::TestV225Migration -v --no-cov`

Expected: 12 passed (the value parametrisation expands to four cases).

- [ ] **Step 6: Update the existing version expectations**

The version bump breaks every assertion that expects the current schema version:

```bash
sed -i 's/"2\.25\.0"/"2.26.0"/g' tests/unit/test_config.py
grep -n '"2\.25\.0"' tests/unit/test_config.py
grep -n '"2\.24\.0"' tests/unit/test_config.py | head
```

Expected: the first `grep` prints nothing; the second still shows the `TestV224Migration` assertions and the
`config.yml.bak.2.24.0` backup path, which must stay as they are (they describe that migration's own input and
backup, not the current version).

- [ ] **Step 7: Run the whole config suite**

Run: `uv run pytest tests/unit/test_config.py -q --no-cov`

Expected: all pass (the suite is ~250 tests; the previous count was 241 plus the 12 new ones).

- [ ] **Step 8: Commit**

```bash
git add src/movarr/config.py tests/unit/test_config.py
git commit -m "feat: drop obsolete schedule_time_units in a config migration"
```

---

### Task 3: Document the cleanup in the README

**Files:**
- Modify: `README.md` (the migration paragraph at the end of the `### schedule` section)

- [ ] **Step 1: Find the paragraph**

Run: `grep -n "Upgrading from config version 2.24.0" README.md`

Expected: one hit inside the `### schedule` section, in the paragraph that describes the interval-to-cron
conversion.

- [ ] **Step 2: Append one sentence**

After the sentence ending `…and a \`cron\` value already present in the file is kept as written unless this version
cannot build a trigger from it, in which case it is replaced by the converted interval and a warning is logged.`,
append:

```markdown
Upgrading also deletes the obsolete `schedule_time_units` key from `acquisition`, `queue_management` and
`post_processing`: it came from siphonator and was never read by movarr, so the only thing it produced was an
unknown-key warning.
```

- [ ] **Step 3: Verify the docs mention the removal and nothing else changed**

Run: `grep -n "schedule_time_units" README.md`

Expected: exactly the two lines added above (the key named once, wrapped across two lines).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: note that upgrading removes schedule_time_units"
```

---

### Task 4: Full quality gate and a hand-run migration check

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite with coverage**

Run: `uv run pytest -q`

Expected: all pass, `Total coverage: 100.00%`, exit 0.

- [ ] **Step 2: Run lint, format check and types**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy .
```

Expected: `All checks passed!`, `55 files already formatted`, `Success: no issues found in 55 source files`.

- [ ] **Step 3: Hand-run the migration through the CLI**

```bash
rm -rf /tmp/mv-units && mkdir -p /tmp/mv-units
printf "general:\n  config_version: '2.25.0'\nschedule:\n  acquisition:\n    schedule_time_mins: 30\n    schedule_time_units: minutes\n  queue_management:\n    schedule_time_mins: 5\n    schedule_time_units: minutes\n  post_processing:\n    cron: '*/5 0-5 * * *'\n    schedule_time_units: minutes\n" > /tmp/mv-units/movarr.yml
uv run movarr --config-path /tmp/mv-units --test 2>&1 | tail -4
cat /tmp/mv-units/movarr.yml | sed -n '/^schedule:/,/^[a-z]/p'
ls /tmp/mv-units
rm -rf /tmp/mv-units
```

Expected: `Configuration loaded successfully. Test mode — exiting.`; the log shows
`Removed obsolete schedule_time_units from: acquisition, queue_management, post_processing.` and no
`Unknown config keys` warning; the `schedule` block prints with `timezone: ''` and the three blocks without
`schedule_time_units`; the directory contains `movarr.yml` and `movarr.yml.bak.2.25.0`.

- [ ] **Step 4: Confirm the tree state**

```bash
git status --short
git log --oneline -3
```

Expected: clean tree, with the two new commits on top of `932a75d`.

---

## Self-review notes

- Spec coverage: migration mechanics and logging (Task 2), the three known blocks only (Task 1's tests plus
  `_SCHEDULE_TASK_BLOCKS`), unconditional value removal (Task 1 parametrisation), absent/non-mapping no-ops (Task 1
  `test_absent_key_is_a_noop`, `test_null_block_is_ignored`), neighbour preservation, backup, idempotency and the
  unknown-key warning (Task 1), README sentence (Task 3), gates and hand-run acceptance (Task 4). Non-goals have no
  tasks by design.
- Naming is consistent throughout: `_migrate_v225_to_v226`, `_SCHEDULE_TASK_BLOCKS`, `_MISSING`,
  `TestV225Migration`.
- The sentinel matters: `pop(..., None) is not None` would silently miss a key written as `schedule_time_units:`
  (YAML null), which one parametrised case pins.
- Deliberate duplication: `TestV225Migration._write` mirrors `TestV224Migration._write`. Extracting a shared helper
  would touch an unrelated test class for four lines; the plan keeps the diff focused.
- Test count note: the class defines 9 test methods, one of which is parametrised with 4 values, so 12 cases run.
