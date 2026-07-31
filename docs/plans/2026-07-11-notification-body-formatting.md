# Notification Body Formatting — Visual Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add blank-line field separators, inline result-detail counts, and bold status prefixes to match the user-provided notification template.

**Architecture:** Four targeted changes to `src/movarr/notifications.py` — no new functions, no dispatch changes, no public API changes. Each function gets a TDD cycle: update test assertions to expect new format → see tests fail → implement code change → see tests pass.

**Tech Stack:** Python 3.12+, pytest

---

### Task 1: `_format_result_details` — inline count + bold status prefixes

**Files:**
- Modify: `src/movarr/notifications.py` (`_format_result_details`)
- Modify: `tests/unit/test_notifications.py` (`TestFormatResultDetails` class)

- [ ] **Step 1: Update test assertions to expect new format**

Replace all 5 tests in `TestFormatResultDetails` with assertions that expect inline count and bold `**Passed:**`/`**Failed:**` prefixes:

```python
class TestFormatResultDetails:
    """Tests for the _format_result_details pure helper."""

    def test_details_inline_count_and_bold_prefixes(self) -> None:
        """Output has inline count with bold label and bold status prefixes on items."""
        details = [
            "Passed: check alpha",
            "Passed: check beta",
            "Passed: check gamma",
        ]
        result = _format_result_details(details)
        assert "**Result Details:** 3 checks passed" in result
        assert "- **Passed:** check alpha" in result
        assert "- **Passed:** check beta" in result
        assert "- **Passed:** check gamma" in result
        # No italic count on a separate line
        assert "_3 checks passed_" not in result

    def test_summary_counts_mixed_pass_fail(self) -> None:
        """Summary shows separate pass and fail counts inline with label."""
        details = [
            "Passed: check a",
            "Failed: check b",
            "Passed: check c",
        ]
        result = _format_result_details(details)
        assert "**Result Details:** 2 passed, 1 failed" in result
        assert "- **Passed:** check a" in result
        assert "- **Failed:** check b" in result
        assert "- **Passed:** check c" in result

    def test_summary_all_failed(self) -> None:
        """Summary shows 0 passed when all checks failed."""
        details = [
            "Failed: check a",
            "Failed: check b",
        ]
        result = _format_result_details(details)
        assert "**Result Details:** 0 passed, 2 failed" in result
        assert "- **Failed:** check a" in result
        assert "- **Failed:** check b" in result

    def test_empty_list_shows_zero_checks(self) -> None:
        """Empty list produces inline '0 checks' label."""
        result = _format_result_details([])
        assert "**Result Details:** 0 checks" in result

    def test_no_html_entities_in_output(self) -> None:
        """HTML entities like &#x27; must NOT appear in Markdown output."""
        details = [
            "Passed: Release group 'byndr' is not in reject list.",
            "Passed: Found via IMDbPie for 'Obsession 2025'.",
        ]
        result = _format_result_details(details)
        assert "&#x27;" not in result, "html.escape() entity leaked into Markdown output"
        # Apostrophes preserved via _escape_markdown_text (backslash-escaped, not HTML-entity-encoded)
        assert "\\'byndr\\'" in result
        assert "\\'Obsession 2025\\'" in result
        assert "- **Passed:**" in result

    def test_items_not_prefixed_with_pass_or_fail(self) -> None:
        """Items without Passed:/Failed: prefix are rendered as plain text (not bolded)."""
        details = ["Quality: Rating: 8.8", "Runtime: 120 min"]
        result = _format_result_details(details)
        assert "- Quality: Rating: 8.8" in result
        assert "- Runtime: 120 min" in result
        # Count labels these as "items" since no Passed/Failed prefix
        assert "2 items" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /data/movarr && uv run pytest tests/unit/test_notifications.py::TestFormatResultDetails -v`
Expected: FAIL — tests expect inline count and bold prefixes, but code still produces italic `_N checks passed_` with plain `- Passed:` items

- [ ] **Step 3: Rewrite `_format_result_details`**

Replace the function body in `src/movarr/notifications.py`:

```python
def _format_result_details(details: list[str]) -> str:
    """Format pipeline result_details as a Markdown list with inline count and bold status prefixes.

    The count appears inline with the label (``**Result Details:** 3 checks passed``).
    Items starting with ``Passed:`` or ``Failed:`` get a bold status prefix
    (``**Passed:**`` / ``**Failed:**``); other items are rendered as plain escaped text.
    """
    passed = failed = 0
    for d in details:
        if d.startswith("Passed"):
            passed += 1
        elif d.startswith("Failed"):
            failed += 1

    if not details:
        count_str = "0 checks"
    elif passed + failed == 0:
        count_str = f"{len(details)} items"
    elif failed == 0:
        count_str = f"{passed} checks passed"
    else:
        count_str = f"{passed} passed, {failed} failed"

    escaped_items: list[str] = []
    for item in details:
        if item.startswith("Passed"):
            _label, _, rest = item.partition(": ")
            escaped_items.append(f"- **Passed:** {_escape_markdown_text(rest)}")
        elif item.startswith("Failed"):
            _label, _, rest = item.partition(": ")
            escaped_items.append(f"- **Failed:** {_escape_markdown_text(rest)}")
        else:
            escaped_items.append(f"- {_escape_markdown_text(item)}")
    items_block = "\n".join(escaped_items) + "\n"
    return f"**Result Details:** {count_str}\n\n{items_block}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /data/movarr && uv run pytest tests/unit/test_notifications.py::TestFormatResultDetails -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /data/movarr && git add src/movarr/notifications.py tests/unit/test_notifications.py && git commit -m "feat: inline result details count with bold Passed/Failed prefixes"
```

---

### Task 2: `_format_result_details_text` — inline count

**Files:**
- Modify: `src/movarr/notifications.py` (`_format_result_details_text`)
- Modify: `tests/unit/test_notifications.py` (`TestFormatResultDetailsText` class)

- [ ] **Step 1: Update test assertions to expect inline count**

Replace all 5 tests in `TestFormatResultDetailsText`:

```python
class TestFormatResultDetailsText:
    """Tests for the _format_result_details_text pure helper."""

    def test_all_passed(self) -> None:
        details = ["Passed: a", "Passed: b", "Passed: c"]
        result = _format_result_details_text(details)
        assert "Result Details: 3 checks passed" in result
        assert "  - Passed: a" in result
        assert "  - Passed: b" in result
        assert "  - Passed: c" in result

    def test_mixed_pass_fail(self) -> None:
        details = ["Passed: a", "Failed: b", "Passed: c"]
        result = _format_result_details_text(details)
        assert "Result Details: 2 passed, 1 failed" in result
        assert "  - Passed: a" in result
        assert "  - Failed: b" in result
        assert "  - Passed: c" in result

    def test_all_failed(self) -> None:
        details = ["Failed: a", "Failed: b"]
        result = _format_result_details_text(details)
        assert "Result Details: 0 passed, 2 failed" in result
        assert "  - Failed: a" in result
        assert "  - Failed: b" in result

    def test_empty_list(self) -> None:
        result = _format_result_details_text([])
        assert "0 checks" in result

    def test_no_markdown_formatting(self) -> None:
        """Output must not contain markdown formatting like _italic_ or **bold**."""
        details = ["Passed: check 'byndr'"]
        result = _format_result_details_text(details)
        assert "**" not in result
        assert "_" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /data/movarr && uv run pytest tests/unit/test_notifications.py::TestFormatResultDetailsText -v`
Expected: FAIL — tests expect `"Result Details: N checks passed"` inline, but code returns count-only string (no `"Result Details:"` prefix)

- [ ] **Step 3: Rewrite `_format_result_details_text`**

Replace the function body in `src/movarr/notifications.py`:

```python
def _format_result_details_text(details: list[str]) -> str:
    """Format pipeline result_details as plain text with inline count.

    The count appears inline with the label (``Result Details: 3 checks passed``).
    Items use two-space indented ``  - `` prefixes for visual grouping.
    """
    passed = failed = 0
    for d in details:
        if d.startswith("Passed"):
            passed += 1
        elif d.startswith("Failed"):
            failed += 1

    if not details:
        count_str = "0 checks"
    elif passed + failed == 0:
        count_str = f"{len(details)} items"
    elif failed == 0:
        count_str = f"{passed} checks passed"
    else:
        count_str = f"{passed} passed, {failed} failed"

    items = "\n".join(f"  - {item}" for item in details)
    return f"Result Details: {count_str}\n\n{items}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /data/movarr && uv run pytest tests/unit/test_notifications.py::TestFormatResultDetailsText -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /data/movarr && git add src/movarr/notifications.py tests/unit/test_notifications.py && git commit -m "feat: inline result details count in plain-text formatter"
```

---

### Task 3: `_build_markdown_body` — blank lines between fields

**Files:**
- Modify: `src/movarr/notifications.py` (`_build_markdown_body` — field lines + remove separate `**Result Details:**` header line)
- Modify: `tests/unit/test_notifications.py` (`TestBuildMarkdownBody` — result details assertions)

- [ ] **Step 1: Update test assertions**

The only failing assertions from this change are in `test_result_details_is_markdown_list` — the result details format changed (inline count, bold prefixes, and the `**Result Details:**` header is now embedded in the details string rather than a separate body line). Update it:

Replace the existing test:

```python
    def test_result_details_is_markdown_list(self) -> None:
        """Result details rendered with inline count and bold status prefixes."""
        fields = _make_fields(_make_full_result())
        body = _build_markdown_body(fields)
        assert "**Result Details:** 1 items" in body
        assert "- **Quality:** Rating: 8.8" in body
```

All other `TestBuildMarkdownBody` tests pass as-is — they use substring matching (`assert "**Status:**" in body`) which is unaffected by blank lines.

- [ ] **Step 2: Run the specific test to verify it fails**

Run: `cd /data/movarr && uv run pytest tests/unit/test_notifications.py::TestBuildMarkdownBody::test_result_details_is_markdown_list -v`
Expected: FAIL — still has separate `**Result Details:**` line + italic count + plain `- Quality:` item

- [ ] **Step 3: Add blank lines and remove separate result-details header**

Replace `_build_markdown_body` in `src/movarr/notifications.py`:

```python
def _build_markdown_body(f: dict[str, str]) -> str:
    """Build the Markdown notification body with blank-line field separators.

    Args:
        f: Extracted body fields from :func:`_extract_body_fields`.
    """
    lines = [
        f"**Status:** {f['queue_status']}",
        "",
        f"**Score:** {f['rating']} from {f['votes']} users",
        "",
        f"**Plot:** {f['plot']}",
        "",
        f"**Actors:** {f['actors_str']}",
        "",
        f"**Directors:** {f['directors_str']}",
        "",
        f"**Genres:** {f['genres_str']}",
        "",
        f"**Release:** {f['index_title']}",
        "",
        f"**Size:** {f['index_size_mb']} MB",
    ]

    links = _build_links_section(f, use_markdown=True)
    if links:
        lines.append("")
        lines.append(links)

    lines.append("")
    # result_details_md now includes the **Result Details:** header inline
    lines.append(f["result_details_md"])

    return "\n".join(lines)
```

Key changes from current:
- `""` empty strings added between every field pair
- Removed separate `f"**Result Details:**"` line (now embedded in `result_details_md` from Task 1)

- [ ] **Step 4: Run all notification tests to verify**

Run: `cd /data/movarr && uv run pytest tests/unit/test_notifications.py -v --tb=short`
Expected: All 106 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /data/movarr && git add src/movarr/notifications.py tests/unit/test_notifications.py && git commit -m "feat: blank line separators between markdown notification body fields"
```

---

### Task 4: `_build_text_body` — blank lines between fields

**Files:**
- Modify: `src/movarr/notifications.py` (`_build_text_body` — field lines + remove separate `Result Details:` header line)
- Modify: `tests/unit/test_notifications.py` (`TestBuildTextBody` — result details assertions)

- [ ] **Step 1: Update test assertions**

Update `test_text_body_result_details_no_markdown` to expect the new inline format:

```python
    def test_text_body_result_details_no_markdown(self) -> None:
        """Text body result details have no markdown formatting."""
        fields = _make_fields(_make_full_result())
        body = _build_text_body(fields)
        assert "_1 items_" not in body
        assert "**" not in body
        assert "Result Details: 1 items" in body
```

All other `TestBuildTextBody` tests pass as-is.

- [ ] **Step 2: Run the specific test to verify it fails**

Run: `cd /data/movarr && uv run pytest tests/unit/test_notifications.py::TestBuildTextBody::test_text_body_result_details_no_markdown -v`
Expected: FAIL — still has separate `Result Details:` line with count on separate line

- [ ] **Step 3: Add blank lines and remove separate result-details header**

Replace `_build_text_body` in `src/movarr/notifications.py`:

```python
def _build_text_body(f: dict[str, str]) -> str:
    """Build the plain-text notification body with blank-line field separators.

    Args:
        f: Extracted body fields from :func:`_extract_body_fields`.
    """
    lines = [
        f"Status: {f['queue_status']}",
        "",
        f"Score: {f['rating']} from {f['votes']} users",
        "",
        f"Plot: {f['plot']}",
        "",
        f"Actors: {f['actors_str']}",
        "",
        f"Directors: {f['directors_str']}",
        "",
        f"Genres: {f['genres_str']}",
        "",
        f"Release: {f['index_title']}",
        "",
        f"Size: {f['index_size_mb']} MB",
    ]

    links = _build_links_section(f, use_markdown=False)
    if links:
        lines.append("")
        lines.append(links)

    lines.append("")
    # result_details_text now includes the Result Details: header inline
    lines.append(f["result_details_text"])

    return "\n".join(lines)
```

Key changes from current:
- `""` empty strings added between every field pair
- Removed separate `"Result Details:"` line (now embedded in `result_details_text` from Task 2)

- [ ] **Step 4: Run all notification tests to verify**

Run: `cd /data/movarr && uv run pytest tests/unit/test_notifications.py -v --tb=short`
Expected: All 106 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /data/movarr && git add src/movarr/notifications.py tests/unit/test_notifications.py && git commit -m "feat: blank line separators between text notification body fields"
```

---

### Task 5: Full suite verification + lint fix

**Files:** (none — verification only)

- [ ] **Step 1: Run full notification test suite**

Run: `cd /data/movarr && uv run pytest tests/unit/test_notifications.py -v --tb=short`
Expected: All 106 tests PASS

- [ ] **Step 2: Run broader unit test suite**

Run: `cd /data/movarr && uv run pytest tests/unit/ -v --tb=short 2>&1 | tail -10`
Expected: All tests PASS, 0 failures

- [ ] **Step 3: Fix ruff lint issue (pre-existing)**

The lint check from verification reported an f-string without placeholders. This was a pre-existing issue from the notification markdown redesign — it's now fixed since Tasks 3 & 4 removed the separate `f"**Result Details:**"` and `"Result Details:"` lines.

Run: `cd /data/movarr && uv run ruff check src/movarr/notifications.py tests/unit/test_notifications.py`
Expected: 0 errors (or only the pre-existing SIM102 in tests if not changed)

- [ ] **Step 4: Run ruff format**

Run: `cd /data/movarr && uv run ruff format src/movarr/notifications.py tests/unit/test_notifications.py`
(Commit if any reformatting occurs)

- [ ] **Step 5: Final commit**

```bash
cd /data/movarr && git add -A && git commit -m "chore: final lint and format pass for notification body formatting"
```
