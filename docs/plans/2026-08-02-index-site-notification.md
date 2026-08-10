# Index Site Name in Notifications — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the release site name (`index_tracker`) as a separate `**Site:**` line in the queued-torrent notification body.

**Architecture:** `index_tracker` is already populated by both Jackett and Prowlarr in `ResultDict`. This is a display-only change: add the field to `_extract_index_fields`, then render it in `_build_markdown_body` and `_build_text_body` between `**Genres:**` and `**Release:**`.

**Tech Stack:** Python 3.12+, pytest, ruff, mypy

---

### Task 1: Add `index_tracker` to `_extract_index_fields`

**Files:**
- Modify: `src/movarr/notifications.py:_extract_index_fields` (lines 345-355)

- [ ] **Step 1: Add the field extraction**

In `src/movarr/notifications.py`, `_extract_index_fields` (around line 345-355):

```python
# Before:
def _extract_index_fields(result: ResultDict) -> dict[str, str]:
    """Extract and format index/torrent release fields."""
    index_title = _escape_markdown_text(result.get("index_title") or "")
    index_size_mb = _escape_markdown_text(str(result.get("index_size_mb") or "?"))
    index_details = _safe_url(result.get("index_details") or "")
    return {"index_title": index_title, "index_size_mb": index_size_mb, "index_details": index_details}

# After:
def _extract_index_fields(result: ResultDict) -> dict[str, str]:
    """Extract and format index/torrent release fields."""
    index_title = _escape_markdown_text(result.get("index_title") or "")
    index_size_mb = _escape_markdown_text(str(result.get("index_size_mb") or "?"))
    index_details = _safe_url(result.get("index_details") or "")
    index_tracker = _escape_markdown_text(result.get("index_tracker") or "Unknown")
    return {
        "index_title": index_title,
        "index_size_mb": index_size_mb,
        "index_details": index_details,
        "index_tracker": index_tracker,
    }
```

- [ ] **Step 2: Run existing notification tests to verify no regression**

Run: `uv run pytest tests/unit/test_notifications.py -q`
Expected: tests that depend on `_extract_index_fields` still pass (field is added but not yet rendered in body).

- [ ] **Step 3: Commit**

```bash
git add src/movarr/notifications.py
git commit -m "feat: extract index_tracker for notification site name display"
```

---

### Task 2: Render site name in `_build_markdown_body`

**Files:**
- Modify: `src/movarr/notifications.py:_build_markdown_body` (lines 386-400)

- [ ] **Step 1: Add `**Site:**` line**

In `src/movarr/notifications.py`, `_build_markdown_body` (around line 395), insert between `**Genres:**` and `**Release:**`:

```python
# Before:
        f"**Genres:** {f['genres_str']}",
        "",
        f"**Release:** {f['index_title']}",

# After:
        f"**Genres:** {f['genres_str']}",
        "",
        f"**Site:** {f['index_tracker']}",
        "",
        f"**Release:** {f['index_title']}",
```

- [ ] **Step 2: Run existing body tests**

Run: `uv run pytest tests/unit/test_notifications.py::TestBuildMarkdownBody -q`
Expected: some tests fail because they don't yet expect the `**Site:**` line.

- [ ] **Step 3: Update `test_body_uses_markdown_bold_labels` to include `**Site:**`**

In `tests/unit/test_notifications.py`, find the assertion block in `test_body_uses_markdown_bold_labels` (around line 217). Add one assertion:

```python
# Add this line after "**Genres:**" assertion and before "**Release:**":
        assert "**Site:**" in body
```

- [ ] **Step 4: Update `test_full_result_contains_key_fields` to assert site presence**

In the same file, `test_full_result_contains_key_fields` (around line 105), add:

```python
        assert "Site:" in body
```

- [ ] **Step 5: Run markdown body tests again**

Run: `uv run pytest tests/unit/test_notifications.py::TestBuildMarkdownBody -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/movarr/notifications.py tests/unit/test_notifications.py
git commit -m "feat: render site name in markdown notification body"
```

---

### Task 3: Render site name in `_build_text_body`

**Files:**
- Modify: `src/movarr/notifications.py:_build_text_body` (lines 414-428)

- [ ] **Step 1: Add `Site:` line to text body**

In `src/movarr/notifications.py`, `_build_text_body` (around line 425), insert between `Genres:` and `Release:`:

```python
# Before:
        f"Genres: {f['genres_str']}",
        "",
        f"Release: {f['index_title']}",

# After:
        f"Genres: {f['genres_str']}",
        "",
        f"Site: {f['index_tracker']}",
        "",
        f"Release: {f['index_title']}",
```

- [ ] **Step 2: Verify existing text body tests**

Run: `uv run pytest tests/unit/test_notifications.py -q -k "text" --ignore-glob='*test*'`
Or more precisely: `uv run pytest tests/unit/test_notifications.py -q`
Expected: all tests pass (text body tests aren't asserting a full list of labels, but check key fields).

- [ ] **Step 3: Commit**

```bash
git add src/movarr/notifications.py
git commit -m "feat: render site name in plain-text notification body"
```

---

### Task 4: Add edge-case tests

**Files:**
- Modify: `tests/unit/test_notifications.py` (add 2 new tests in `TestBuildMarkdownBody`)

- [ ] **Step 1: Add `test_empty_index_tracker_shows_unknown`**

At the end of `class TestBuildMarkdownBody` (after the last test, around line 280):

```python
    def test_empty_index_tracker_shows_unknown(self) -> None:
        """Body shows 'Unknown' when index_tracker is empty or missing."""
        result = _make_full_result()
        result.pop("index_tracker", None)
        body_md = _build_markdown_body(_make_fields(result))
        body_text = _build_text_body(_make_fields(result))
        assert "**Site:** Unknown" in body_md
        assert "Site: Unknown" in body_text
```

- [ ] **Step 2: Run the new test to verify it passes**

Run: `uv run pytest tests/unit/test_notifications.py::TestBuildMarkdownBody::test_empty_index_tracker_shows_unknown -v`
Expected: PASS

- [ ] **Step 3: Add `test_index_tracker_preserved_in_body`**

```python
    def test_index_tracker_preserved_in_body(self) -> None:
        """Body shows the actual tracker name when index_tracker is set."""
        result = _make_full_result(index_tracker="RARBG")
        body_md = _build_markdown_body(_make_fields(result))
        body_text = _build_text_body(_make_fields(result))
        assert "**Site:** RARBG" in body_md
        assert "Site: RARBG" in body_text
```

- [ ] **Step 4: Run the new test to verify it passes**

Run: `uv run pytest tests/unit/test_notifications.py::TestBuildMarkdownBody::test_index_tracker_preserved_in_body -v`
Expected: PASS

- [ ] **Step 5: Run all notification tests**

Run: `uv run pytest tests/unit/test_notifications.py -q`
Expected: all tests pass — the 2 new tests plus all existing ones (no regression).

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_notifications.py
git commit -m "test: edge cases for index site name in notification body"
```

---

### Final Gate: Run full test suite + lint + type check

- [ ] **Step 1: Full test suite**

Run: `uv run pytest -q`
Expected: all ~1350+ tests pass, coverage ≥ 95%.

- [ ] **Step 2: Lint and format**

Run: `uv run ruff check --fix . && uv run ruff format .`
Expected: "All checks passed!" / "N files left unchanged"

- [ ] **Step 3: Type check**

Run: `uv run mypy .`
Expected: "Success: no issues found in N source files"

---

## Files Changed

| File | Task | Change |
|------|------|--------|
| `src/movarr/notifications.py` | 1, 2, 3 | `_extract_index_fields` (+1 field), `_build_markdown_body` (+2 lines), `_build_text_body` (+2 lines) |
| `tests/unit/test_notifications.py` | 2, 4 | Update `test_body_uses_markdown_bold_labels` and `test_full_result_contains_key_fields`, add 2 new edge-case tests |
