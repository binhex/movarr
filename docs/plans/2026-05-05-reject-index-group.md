# reject_ Rename + reject_index_group_list Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename all `bad_*` config fields to `reject_*`, add `reject_index_group_list` filter, and wire everything through config migration, filters, tests, and docs.

**Architecture:** Config model rename with automatic v2.7.0 → v2.8.0 migration; new `_check_reject_index_group()` filter placed first in the `filter_by_index` gate chain so group rejection is the cheapest possible check.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, ruff, mypy

---

## File Map

| File | Responsibility |
|------|---------------|
| `src/movarr/config.py` | Config model fields, default values, migration chain, version constant |
| `src/movarr/filters.py` | Filter functions (`_check_reject_*`), gate chain composition |
| `tests/unit/test_filters.py` | Filter unit tests (rename + new group tests) |
| `tests/unit/test_config.py` | Migration tests (v2.7.0 → v2.8.0, version bumps) |
| `README.md` | Documentation table and FAQ |

---

### Task 1: Config Model — Rename `bad_` → `reject_` and Add `reject_index_group_list`

**Files:**
- Modify: `src/movarr/config.py:162-164`

- [ ] **Step 1: Rename fields and add new field**

Replace:
```python
    bad_index_title_list: list[str] = Field(default_factory=list)
    bad_genre_list: list[str] = Field(default_factory=list)
    bad_movie_title_list: list[str] = Field(default_factory=list)
```

With:
```python
    reject_index_title_list: list[str] = Field(default_factory=list)
    reject_genre_list: list[str] = Field(default_factory=list)
    reject_movie_title_list: list[str] = Field(default_factory=list)
    reject_index_group_list: list[str] = Field(default_factory=list)
```

- [ ] **Step 2: Run ruff check to catch syntax issues**

```bash
uv run ruff check src/movarr/config.py
```
Expected: Pass (or expected import errors from missing renames in filters.py)

---

### Task 2: Config Migration — v2.7.0 → v2.8.0

**Files:**
- Modify: `src/movarr/config.py:18, 77-86`

- [ ] **Step 1: Bump config version**

Change:
```python
_CONFIG_VERSION = "2.7.0"
```

To:
```python
_CONFIG_VERSION = "2.8.0"
```

- [ ] **Step 2: Add migration function after `_migrate_v26_to_v27`**

Insert after `_migrate_v26_to_v27`:

```python
def _migrate_v27_to_v28(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.7.0 → v2.8.0: rename bad_* fields to reject_* in filters."""
    filters = raw.setdefault("filters", {})
    for old, new in (
        ("bad_index_title_list", "reject_index_title_list"),
        ("bad_genre_list", "reject_genre_list"),
        ("bad_movie_title_list", "reject_movie_title_list"),
    ):
        if old in filters:
            filters[new] = filters.pop(old)
    raw.setdefault("general", {})["config_version"] = "2.8.0"
    return raw
```

- [ ] **Step 3: Register migration in MIGRATIONS dict**

Add `"2.7.0": _migrate_v27_to_v28` to the `MIGRATIONS` dict.

---

### Task 3: Filters — Rename Functions and Variables

**Files:**
- Modify: `src/movarr/filters.py:68, 72-73, 182-199, 253-274, 296-307, 320-334`

- [ ] **Step 1: Rename `_check_bad_keywords` → `_check_reject_keywords`**

Change function signature and all internal references:
```python
def _check_reject_keywords(result: ResultDict, config: Config) -> ResultDict:
    reject_list = config.filters.reject_index_title_list
    if not reject_list:
        return _pass(result, "No reject index title keywords defined.")

    index_title = result.get("index_title") or ""
    for keyword in reject_list:
        if keyword.lower() in index_title.lower():
            return _fail(result, f"Index title contains rejected keyword '{keyword}'.")
    return _pass(result, "No rejected keywords found in index title.")
```

- [ ] **Step 2: Rename `_check_bad_genre` → `_check_reject_genre`**

Change function signature and internal references:
```python
def _check_reject_genre(result: ResultDict, config: Config) -> ResultDict:
    reject_list = config.filters.reject_genre_list
    if not reject_list:
        return _pass(result, "No reject genre list defined.")
```
(Keep rest of function body identical, changing `bad_lower` → `reject_lower`)

- [ ] **Step 3: Rename `_check_bad_movie_titles` → `_check_reject_movie_titles`**

Change function signature and internal references:
```python
def _check_reject_movie_titles(result: ResultDict, config: Config) -> ResultDict:
    reject_list = config.filters.reject_movie_title_list
    if not reject_list:
        return _pass(result, "No reject movie title list defined.")
```
(Keep rest of function body identical, changing `bad_list` → `reject_list`, `bad_lower` → `reject_lower`)

- [ ] **Step 4: Update `filter_by_index` check chain references**

Change:
```python
        lambda r: _check_bad_keywords(r, config),
```
To:
```python
        lambda r: _check_reject_keywords(r, config),
```

Change:
```python
        lambda r: _check_bad_movie_titles(r, config),
```
To:
```python
        lambda r: _check_reject_movie_titles(r, config),
```

Change:
```python
        lambda r: _check_bad_genre(r, config),
```
To:
```python
        lambda r: _check_reject_genre(r, config),
```

---

### Task 4: Filters — Add `_check_reject_index_group()`

**Files:**
- Modify: `src/movarr/filters.py` (insert after `_check_reject_keywords` or at top of Stage 1 helpers)

- [ ] **Step 1: Add import for `extract_group` and `sanitise`**

Verify they're already imported at top of `filters.py` (they should be from `movarr.parsing`).

- [ ] **Step 2: Write `_check_reject_index_group()`**

Insert as first Stage 1 helper (before `_check_search_criteria`):

```python
def _check_reject_index_group(result: ResultDict, config: Config) -> ResultDict:
    """Reject torrents from specific release groups.

    Args:
        result: Pipeline dict with ``index_title`` populated.
        config: Application configuration.

    Returns:
        Updated result dict.
    """
    reject_list = config.filters.reject_index_group_list
    if not reject_list:
        return _pass(result, "No reject_index_group_list defined.")

    index_title = result.get("index_title") or ""
    group = extract_group(sanitise(index_title))
    if not group:
        return _pass(result, "No release group detected; skipping group check.")

    reject_lower = [g.lower() for g in reject_list]
    if group.lower() in reject_lower:
        return _fail(result, f"Release group '{group}' is in reject_index_group_list.")
    return _pass(result, f"Release group '{group}' is not in reject_index_group_list.")
```

- [ ] **Step 3: Wire into `filter_by_index` check chain**

Change:
```python
    checks = [
        lambda r: _check_search_criteria(r, index_site),
```

To:
```python
    checks = [
        lambda r: _check_reject_index_group(r, config),
        lambda r: _check_search_criteria(r, index_site),
```

---

### Task 5: Tests — Update `test_filters.py` for Renames

**Files:**
- Modify: `tests/unit/test_filters.py`

- [ ] **Step 1: Rename test class `TestFilterByIndexBadKeywords` → `TestFilterByIndexRejectKeywords`**

- [ ] **Step 2: Update all `_make_config(bad_index_title_list=...)` → `_make_config(reject_index_title_list=...)`**

Find all occurrences and rename.

- [ ] **Step 3: Rename test class `TestFilterByImdbBadGenre` → `TestFilterByImdbRejectGenre`**

- [ ] **Step 4: Update all `_make_config(bad_genre_list=...)` → `_make_config(reject_genre_list=...)`**

- [ ] **Step 5: Rename test class `TestFilterByImdbBadMovieTitle` → `TestFilterByImdbRejectMovieTitle`**

- [ ] **Step 6: Update all `_make_config(bad_movie_title_list=...)` → `_make_config(reject_movie_title_list=...)`**

---

### Task 6: Tests — Add `reject_index_group_list` Tests

**Files:**
- Modify: `tests/unit/test_filters.py` (after `TestFilterByIndexRejectKeywords`)

- [ ] **Step 1: Add `TestFilterByIndexRejectGroup` class**

```python
class TestFilterByIndexRejectGroup:
    """Release group rejection filter."""

    def test_matching_group_fails(self) -> None:
        cfg = _make_config(reject_index_group_list=["FGT"])
        result = _index_result(index_title="Movie 2020 1080p BluRay FGT")
        out = filter_by_index(result, _default_site_dict(), cfg)
        assert out["result"] == "Failed"
        assert "FGT" in " ".join(out.get("result_details", []))

    def test_non_matching_group_passes(self) -> None:
        cfg = _make_config(reject_index_group_list=["FGT"])
        result = _index_result(index_title="Movie 2020 1080p BluRay SPARKS")
        out = filter_by_index(result, _default_site_dict(), cfg)
        assert out["result"] == "Passed"

    def test_empty_list_skips_check(self) -> None:
        cfg = Config()  # reject_index_group_list = []
        result = _index_result(index_title="Movie 2020 1080p BluRay FGT")
        out = filter_by_index(result, _default_site_dict(), cfg)
        assert out["result"] == "Passed"

    def test_case_insensitive_match(self) -> None:
        cfg = _make_config(reject_index_group_list=["fgt"])
        result = _index_result(index_title="Movie 2020 1080p BluRay FGT")
        out = filter_by_index(result, _default_site_dict(), cfg)
        assert out["result"] == "Failed"

    def test_no_group_detected_passes(self) -> None:
        cfg = _make_config(reject_index_group_list=["FGT"])
        result = _index_result(index_title="Movie 2020 1080p BluRay")
        out = filter_by_index(result, _default_site_dict(), cfg)
        assert out["result"] == "Passed"
```

---

### Task 7: Tests — Update `test_config.py` Migration Versions

**Files:**
- Modify: `tests/unit/test_config.py`

- [ ] **Step 1: Update all `assert cfg.general.config_version == "2.7.0"` → `"2.8.0"`**

Find and replace across the file. Locations:
- `test_v1_config_is_migrated_to_v2`: `"2.7.0"` → `"2.8.0"`
- `test_no_version_key_treated_as_v1`: `"2.7.0"` → `"2.8.0"`
- `test_v2_config_migrated_to_v21`: `"2.7.0"` → `"2.8.0"`
- `test_v21_config_migrated_to_v22`: `"2.7.0"` → `"2.8.0"`
- `test_v22_config_migrated_to_v23`: `"2.7.0"` → `"2.8.0"`
- `test_v23_config_migrated_to_v24`: `"2.7.0"` → `"2.8.0"`
- `test_v24_config_migrated_to_v25`: `"2.7.0"` → `"2.8.0"`
- `test_existing_config_at_v25_is_migrated_to_v26`: `"2.7.0"` → `"2.8.0"`
- `test_existing_config_at_v26_is_migrated_to_v27`: update docstring and assertions to v27→v28

- [ ] **Step 2: Rename `test_existing_config_at_v26_is_migrated_to_v27` → `test_existing_config_at_v26_is_migrated_to_v27_to_v28`**

Update docstring to reflect both migrations happen:
```python
def test_existing_config_at_v26_is_migrated_to_v28(self, tmp_path: Path) -> None:
    """A config at v2.6.0 is migrated through v2.7.0 to v2.8.0."""
```
Update assertion: `assert cfg.general.config_version == "2.8.0"`

- [ ] **Step 3: Update `test_existing_config_at_v27_needs_no_migration`**

Change input to `"2.8.0"`:
```python
def test_existing_config_at_v28_needs_no_migration(self, tmp_path: Path) -> None:
    """A config already at v2.8.0 is not re-migrated."""
    cfg_file = tmp_path / "config.yml"
    cfg_file.write_text("general:\n  config_version: '2.8.0'\n")
    cfg = load_config(str(cfg_file))
    assert cfg.general.config_version == "2.8.0"
    backup = tmp_path / "config.yml.bak.2.8.0"
    assert not backup.exists()
```

- [ ] **Step 4: Add `TestMigrationV27toV28` class**

```python
class TestMigrationV27toV28:
    """v2.7.0 → v2.8.0 migration renames bad_* fields to reject_*."""

    def _v27_config(self, tmp_path: Path) -> Path:
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(
            "general:\n  config_version: '2.7.0'\n"
            "filters:\n"
            "  bad_index_title_list: [xvid]\n"
            "  bad_genre_list: [horror]\n"
            "  bad_movie_title_list: [Bad Movie]\n"
        )
        return cfg_file

    def test_v27_renames_bad_fields(self, tmp_path: Path) -> None:
        cfg_file = self._v27_config(tmp_path)
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.8.0"
        assert cfg.filters.reject_index_title_list == ["xvid"]
        assert cfg.filters.reject_genre_list == ["horror"]
        assert cfg.filters.reject_movie_title_list == ["Bad Movie"]

    def test_v27_migration_creates_backup(self, tmp_path: Path) -> None:
        cfg_file = self._v27_config(tmp_path)
        load_config(str(cfg_file))
        backup = tmp_path / "config.yml.bak.2.7.0"
        assert backup.exists()

    def test_v27_preserves_allow_and_override_fields(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(
            "general:\n  config_version: '2.7.0'\n"
            "filters:\n"
            "  allow_country_list: [us]\n"
            "  override_director_list: [Nolan]\n"
        )
        cfg = load_config(str(cfg_file))
        assert cfg.filters.allow_country_list == ["us"]
        assert cfg.filters.override_director_list == ["Nolan"]
```

---

### Task 8: Documentation — Update README.md

**Files:**
- Modify: `README.md:128-133, 344-350`

- [ ] **Step 1: Update filters table**

Replace:
```markdown
| `bad_index_title_list` | Index titles containing any of these keywords are rejected before IMDb lookup. | *(see default config)* |
| `bad_genre_list` | Reject any movie whose IMDb genres include one of these values. | `[]` |
| `bad_movie_title_list` | Reject movies whose resolved title exactly matches any entry. | `[]` |
```

With:
```markdown
| `reject_index_title_list` | Index titles containing any of these keywords are rejected before IMDb lookup. | *(see default config)* |
| `reject_genre_list` | Reject any movie whose IMDb genres include one of these values. | `[]` |
| `reject_movie_title_list` | Reject movies whose resolved title exactly matches any entry. | `[]` |
| `reject_index_group_list` | Reject torrents from these release groups (case-insensitive). | `[]` |
```

- [ ] **Step 2: Update FAQ if any `bad_` references exist**

Search for `bad_` and replace with `reject_`.

---

### Task 9: Run Full Test Suite

- [ ] **Step 1: Run tests with coverage**

```bash
uv run pytest --no-header -q --cov=src/movarr --cov-report=term-missing --cov-fail-under=100
```
Expected: All tests pass, 100% coverage

- [ ] **Step 2: Run pre-commit**

```bash
uv run pre-commit run --all-files
```
Expected: All hooks pass

---

### Task 10: Commit

- [ ] **Step 1: Stage and commit**

```bash
git add -A
git commit -m "config: rename bad_* to reject_*, add reject_index_group_list filter

Standardises reject-list vocabulary:
- bad_index_title_list → reject_index_title_list
- bad_genre_list → reject_genre_list
- bad_movie_title_list → reject_movie_title_list

Adds reject_index_group_list filter that blocks torrents from specific
release groups before any IMDb lookup. Checked first in the index filter
chain for efficiency.

Config migration v2.7.0 → v2.8.0 automatically renames old fields in
existing config files with .bak backup.

N tests passing, 100% line coverage, all pre-commit hooks green."
```

---

## Self-Review Checklist

1. **Spec coverage:**
   - [x] Rename `bad_` → `reject_` (Task 1, 3, 5, 7)
   - [x] Add `reject_index_group_list` (Task 1, 4, 6)
   - [x] Config migration v2.7.0 → v2.8.0 (Task 2, 7)
   - [x] Update docs (Task 8)
   - [x] Run tests + pre-commit (Task 9)

2. **Placeholder scan:** None found.

3. **Type consistency:**
   - `reject_index_group_list: list[str]` matches other `*_list` fields
   - `_check_reject_index_group` signature matches other `_check_*` functions
   - `extract_group` and `sanitise` already imported in `filters.py`
