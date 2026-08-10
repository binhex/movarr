# Reject Genre Exclusive Filter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new `reject_genre_exclusive_list` config option that rejects a movie only when every single one of its IMDb genres is in the list, allowing pure-horror rejection while accepting horror/sci-fi hybrids.

**Architecture:** A new private function `_check_reject_genre_exclusive` in the existing stage-2 filter pipeline, gated by a new `FiltersConfig` field. The function is inserted immediately after the existing `_check_reject_genre` so the stricter "always reject" gate runs first.

**Tech Stack:** Python 3.12, Pydantic (config models), pytest + coverage

---

### Task 1: Add config field

**Files:**
- Modify: `src/movarr/config.py:449-450`

- [ ] **Step 1: Add `reject_genre_exclusive_list` field to `FiltersConfig`**

After line 450 (`reject_genre_list`), insert the new field:

```python
    reject_genre_list: list[str] = Field(default_factory=list)
    reject_genre_exclusive_list: list[str] = Field(default_factory=list)  # NEW
    reject_movie_title_list: list[str] = Field(default_factory=list)
```

An empty list (the Pydantic default) means the feature is disabled — no behavior change.

- [ ] **Step 2: Verify the model loads correctly**

Run: `uv run python -c "from movarr.config import Config; c = Config(); assert c.filters.reject_genre_exclusive_list == []; print('OK')"`
Expected: prints `OK`

- [ ] **Step 3: Commit**

```bash
git add src/movarr/config.py
git commit -m "feat: add reject_genre_exclusive_list config field"
```

---

### Task 2: Implement filter function + wire into pipeline

**Files:**
- Modify: `src/movarr/filters.py:199-200` (pipeline wiring)
- Modify: `src/movarr/filters.py:376-389` (new function after `_check_reject_genre`)

- [ ] **Step 1: Add the `_check_reject_genre_exclusive` function**

Insert this function immediately after `_check_reject_genre` (after line 389, the closing of `_check_reject_genre`):

```python
def _check_reject_genre_exclusive(result: ResultDict, config: Config) -> ResultDict:
    """Reject a movie only if ALL of its genres are in the reject-exclusive list.

    If the movie has ANY genre that is NOT in this list, it passes this check.
    This allows rejecting pure horror while accepting horror/sci-fi hybrids.
    """
    reject_list = config.filters.reject_genre_exclusive_list
    if not reject_list:
        return _pass(result, "No reject genre exclusive list defined.")

    genres = result.get("imdb_genres_list") or []
    if not genres:
        return _pass(result, "No genre data; skipping reject genre exclusive check.")

    genres_lower = [g.lower() for g in genres]
    reject_lower = [r.lower() for r in reject_list]

    for genre in genres_lower:
        if genre not in reject_lower:
            return _pass(
                result,
                f"Genre '{genre}' is not in reject genre exclusive list; movie has non-rejected genre(s).",
            )

    return _fail(
        result,
        f"All genres {genres_lower} are in reject genre exclusive list.",
    )
```

- [ ] **Step 2: Wire into the pipeline**

In `filter_by_imdb`, insert the new check into the `checks` list after the existing `_check_reject_genre` (line 199). Change:

```python
        lambda r: _check_reject_genre(r, config),
        _check_bitrate,
```

to:

```python
        lambda r: _check_reject_genre(r, config),
        lambda r: _check_reject_genre_exclusive(r, config),
        _check_bitrate,
```

- [ ] **Step 3: Quick smoke test**

Run: `uv run python -c "from movarr.filters import _check_reject_genre_exclusive; print('OK')"`
Expected: prints `OK`

- [ ] **Step 4: Commit**

```bash
git add src/movarr/filters.py
git commit -m "feat: implement _check_reject_genre_exclusive filter gate"
```

---

### Task 3: Write tests

**Files:**
- Modify: `tests/unit/test_filters.py` (insert after `TestFilterByImdbRejectGenre` closes, around line 436)

- [ ] **Step 1: Add test class before `TestFilterByImdbLanguage`**

Insert this class after line 436 (the last line of `TestFilterByImdbRejectGenre`):

```python
class TestFilterByImdbRejectGenreIfOnly:
    """Genre exclusive exclusion filter (reject_genre_exclusive_list)."""

    def test_if_only_genre_pure_rejects(self) -> None:
        """Single genre matching exclusive list → reject."""
        cfg = _make_config(reject_genre_exclusive_list=["Horror"])
        result = _imdb_result(imdb_genres_list=["Horror"])
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"

    def test_if_only_genre_with_other_passes(self) -> None:
        """Exclusive genre + non-matching genre → pass (has other genre)."""
        cfg = _make_config(reject_genre_exclusive_list=["Horror"])
        result = _imdb_result(imdb_genres_list=["Horror", "Sci-Fi"])
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_if_only_no_match_passes(self) -> None:
        """No genres match the exclusive list → pass."""
        cfg = _make_config(reject_genre_exclusive_list=["Horror"])
        result = _imdb_result(imdb_genres_list=["Action", "Drama"])
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_empty_list_always_passes(self) -> None:
        """Empty reject_genre_exclusive_list → pass (feature disabled)."""
        cfg = Config()  # empty lists
        result = _imdb_result(imdb_genres_list=["Horror"])
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"

    def test_case_insensitive_match(self) -> None:
        """Case-mismatched genre in exclusive list matches case-insensitively."""
        cfg = _make_config(reject_genre_exclusive_list=["horror"])
        result = _imdb_result(imdb_genres_list=["Horror"])
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"

    def test_multiple_genres_all_rejected(self) -> None:
        """All genres in the exclusive list (2+ genres) → reject."""
        cfg = _make_config(reject_genre_exclusive_list=["Horror", "Thriller"])
        result = _imdb_result(imdb_genres_list=["Horror", "Thriller"])
        out = filter_by_imdb(result, cfg)
        assert out["result"] != "Passed"

    def test_no_genre_data_passes(self) -> None:
        """No genre metadata available → pass (insufficient info to reject)."""
        cfg = _make_config(reject_genre_exclusive_list=["Horror"])
        result = _imdb_result(imdb_genres_list=[])
        out = filter_by_imdb(result, cfg)
        assert out["result"] == "Passed"
```

- [ ] **Step 2: Run the new tests**

Run: `uv run pytest tests/unit/test_filters.py::TestFilterByImdbRejectGenreIfOnly -v`
Expected: All 7 tests PASS

- [ ] **Step 3: Run the full test suite to check interactions**

Run: `uv run pytest tests/unit/test_filters.py -v`
Expected: All existing tests still pass, plus the 7 new ones.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_filters.py
git commit -m "test: add TestFilterByImdbRejectGenreIfOnly"
```

---

### Task 4: Quality control pass

- [ ] **Step 1: Run ruff**

Run: `uv run ruff check --fix src/movarr/config.py src/movarr/filters.py tests/unit/test_filters.py && uv run ruff format src/movarr/config.py src/movarr/filters.py tests/unit/test_filters.py`
Expected: No errors. If formatting changes occur, they're whitespace-only.

- [ ] **Step 2: Run mypy**

Run: `uv run mypy src/movarr/config.py src/movarr/filters.py`
Expected: No type errors.

- [ ] **Step 3: Run coverage**

Run: `uv run pytest --cov=movarr --cov-fail-under=80 -v`
Expected: Coverage check passes (current baseline is well above 80%).

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: qc pass for reject_genre_exclusive_list"
```

---

### Task 5 (optional): Bump config version

If the project convention requires a config version bump for any schema addition, update `_CONFIG_VERSION` in `config.py` and add an empty migration entry.

- [ ] **Step 1: Check if bump is needed**

Run: `grep -n 'config_version: 2.19.0' configs/movarr.yml`
If the project already runs at 2.19.0 and convention requires a bump, proceed. Otherwise skip this task.

- [ ] **Step 2: Bump version (if needed)**

In `config.py`, change `_CONFIG_VERSION = "2.19.0"` to `_CONFIG_VERSION = "2.20.0"`.

Add an empty migration entry to `_MIGRATION_TABLE`:

Append to `_MIGRATION_TABLE`:
```python
    (
        "2.19.0",
        "2.20.0",
        [],
    ),
```

- [ ] **Step 3: Commit (if bumped)**

```bash
git add src/movarr/config.py
git commit -m "chore: bump config_version to 2.20.0"
```
