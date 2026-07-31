# Notification Markdown Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert notification body from HTML to Markdown with inline HTML `<details>` for collapsible result details, making notifications readable on ntfy mobile and across all Apprise services.

**Architecture:** Switch `body_format` from `NotifyFormat.HTML` to `NotifyFormat.MARKDOWN` in `_dispatch_apprise`. Rewrite `_build_body` to emit Markdown with `**bold**` labels and inline `<details><summary>` for the check list. Update `_format_result_details` to wrap items in a `<details>` block with pass/fail counting. Convert service alert bodies from HTML to Markdown for consistency. Tests updated to assert on Markdown output.

**Tech Stack:** Python 3.12+, Apprise, pytest

---

### Task 1: Add `body_format` parameter to `_dispatch_apprise`

**Files:**
- Modify: `src/movarr/notifications.py:140-156`

- [ ] **Step 1: Write failing test for Markdown body_format dispatch**

Add to `TestDispatchApprise` in `tests/unit/test_notifications.py`:

```python
    def test_notify_uses_markdown_body_format(self) -> None:
        """_dispatch_apprise sends with NotifyFormat.MARKDOWN by default."""
        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            _dispatch_apprise("subj", "body", ["ntfy://t"])
        mock_ap.notify.assert_called_once()
        _, kwargs = mock_ap.notify.call_args
        from apprise import NotifyFormat
        assert kwargs["body_format"] == NotifyFormat.MARKDOWN

    def test_notify_respects_explicit_body_format(self) -> None:
        """_dispatch_apprise passes through an explicit body_format kwarg."""
        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            from apprise import NotifyFormat
            _dispatch_apprise("subj", "body", ["ntfy://t"], body_format=NotifyFormat.TEXT)
        mock_ap.notify.assert_called_once()
        _, kwargs = mock_ap.notify.call_args
        from apprise import NotifyFormat
        assert kwargs["body_format"] == NotifyFormat.TEXT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /data/movarr && uv run pytest tests/unit/test_notifications.py::TestDispatchApprise::test_notify_uses_markdown_body_format tests/unit/test_notifications.py::TestDispatchApprise::test_notify_respects_explicit_body_format -v`
Expected: FAIL — `body_format` keyword not passed or is still HTML

- [ ] **Step 3: Add `body_format` parameter to `_dispatch_apprise`**

In `src/movarr/notifications.py`, change `_dispatch_apprise` signature and body:

```python
def _dispatch_apprise(
    subject: str,
    body: str,
    urls: list[str],
    body_format: object = apprise.NotifyFormat.MARKDOWN,
) -> bool:
    """Send *subject*/*body* via Apprise to all *urls*.

    Returns True if at least one notification was sent successfully.
    Returns False on empty URL list, empty subject/body, or any error.
    """
    if not urls or not subject or not body:
        return False
    ap = apprise.Apprise()
    for url in urls:
        ap.add(url)
    try:
        sent = ap.notify(title=subject, body=body, body_format=body_format)
    except Exception:  # noqa: BLE001
        logger.warning("Apprise notification failed.")
        return False
    if not sent:
        logger.warning("Apprise notification was not sent (no valid targets or all failed).")
        return False
    return True
```

The only change is adding `body_format: object = apprise.NotifyFormat.MARKDOWN` to the signature and passing `body_format=body_format` in the `ap.notify()` call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /data/movarr && uv run pytest tests/unit/test_notifications.py -v`
Expected: All existing tests pass, plus the 2 new tests pass

- [ ] **Step 5: Commit**

```bash
git add src/movarr/notifications.py tests/unit/test_notifications.py
git commit -m "feat: add body_format parameter to _dispatch_apprise, default to MARKDOWN"
```

---

### Task 2: Convert service alert bodies from HTML to Markdown

**Files:**
- Modify: `src/movarr/notifications.py:57-91`

- [ ] **Step 1: Write failing test for Markdown alert body**

Add to `TestSendServiceAlert` in `tests/unit/test_notifications.py`:

```python
    def test_body_uses_markdown_bold_labels(self) -> None:
        """Service alert body uses **bold** Markdown, not <strong> HTML."""
        config = _make_config(["ntfy://t"])
        mock_ap = MagicMock()
        mock_ap.notify.return_value = True
        with patch("movarr.notifications.apprise.Apprise", return_value=mock_ap):
            send_service_alert(service_name="qBittorrent", hours_elapsed=2.0, config=config)
        _, kwargs = mock_ap.notify.call_args
        body = kwargs["body"]
        assert "<strong>" not in body
        assert "**" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /data/movarr && uv run pytest tests/unit/test_notifications.py::TestSendServiceAlert::test_body_uses_markdown_bold_labels -v`
Expected: FAIL — body still contains `<strong>` HTML tags

- [ ] **Step 3: Convert `send_service_alert` body to Markdown**

In `src/movarr/notifications.py`, change the body construction in `send_service_alert`:

```python
    body = (
        "**movarr service health alert**\n\n"
        f"**Service:** {safe_service}\n"
        f"**Duration:** Unavailable for {safe_hours} hours.\n\n"
        f"movarr will keep retrying every cycle. "
        f"Check that {safe_service} is running and accessible."
    )
```

Remove the `html.escape()` calls on `service_name` and `hours_str` since Markdown doesn't need HTML escaping for plain text. Replace `<p><strong>` with `**` and `<br>` with blank lines.

Remove unused `html.escape` calls:
- `safe_service` variable becomes just `service_name`
- `safe_hours` becomes just `hours_str`

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /data/movarr && uv run pytest tests/unit/test_notifications.py -v`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add src/movarr/notifications.py tests/unit/test_notifications.py
git commit -m "refactor: convert service alert body from HTML to Markdown"
```

---

### Task 3: Rewrite `_format_result_details` with `<details>` wrapper and pass/fail counting

**Files:**
- Modify: `src/movarr/notifications.py:264-273`

- [ ] **Step 1: Write failing test for `<details>` wrapper with pass count**

Add to `TestFormatResultDetails` in `tests/unit/test_notifications.py`:

```python
    def test_details_wraps_list_with_summary_and_pass_count(self) -> None:
        """Output is wrapped in <details><summary> with pass/fail count."""
        details = [
            "Passed: check alpha",
            "Passed: check beta",
            "Passed: check gamma",
        ]
        result = _format_result_details(details)
        assert "<details>" in result
        assert "<summary>3 checks passed</summary>" in result
        assert "- Passed: check alpha" in result
        assert "</details>" in result

    def test_summary_counts_mixed_pass_fail(self) -> None:
        """Summary shows separate pass and fail counts."""
        details = [
            "Passed: check a",
            "Failed: check b",
            "Passed: check c",
        ]
        result = _format_result_details(details)
        assert "<summary>2 passed, 1 failed</summary>" in result

    def test_summary_all_failed(self) -> None:
        """Summary shows 0 passed when all checks failed."""
        details = [
            "Failed: check a",
            "Failed: check b",
        ]
        result = _format_result_details(details)
        assert "<summary>0 passed, 2 failed</summary>" in result

    def test_empty_list_shows_zero_checks(self) -> None:
        """Empty list produces <details> with '0 checks' summary."""
        result = _format_result_details([])
        assert "<details>" in result
        assert "<summary>0 checks</summary>" in result
        assert "</details>" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /data/movarr && uv run pytest tests/unit/test_notifications.py::TestFormatResultDetails -v`
Expected: New tests FAIL — output is `<ul><li>` not `<details><summary>`

- [ ] **Step 3: Rewrite `_format_result_details`**

Replace the current implementation in `src/movarr/notifications.py`:

```python
def _format_result_details(details: list[str]) -> str:
    """Format pipeline result_details as a collapsible Markdown list.

    Wraps items in an HTML ``<details><summary>`` block for
    tap-to-expand behaviour.  The ``<summary>`` shows the pass/fail
    counts so the user doesn't need to count manually.

    Each entry is ``"Passed: msg"`` or ``"Failed: msg"``, so they are
    always rendered as simple ``- `` list items inside the collapsible
    block.
    """
    passed = sum(1 for d in details if d.startswith("Passed"))
    failed = sum(1 for d in details if d.startswith("Failed"))

    if passed + failed == 0:
        count_str = "0 checks"
    elif failed == 0:
        count_str = f"{passed} checks passed"
    elif passed == 0:
        count_str = f"0 passed, {failed} failed"
    else:
        count_str = f"{passed} passed, {failed} failed"

    items = ""
    for item in details:
        items += f"- {html.escape(item)}\n"

    return f"<details>\n<summary>{count_str}</summary>\n\n{items}</details>"
```

Update `_extract_body_fields` to rename the key (line 239):

```python
    fields["result_details_md"] = _format_result_details(result.get("result_details") or [])
```

And `_build_body` to use the new key name.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /data/movarr && uv run pytest tests/unit/test_notifications.py -v`
Expected: All new FormatResultDetails tests pass

- [ ] **Step 5: Commit**

```bash
git add src/movarr/notifications.py tests/unit/test_notifications.py
git commit -m "feat: wrap result details in <details> with pass/fail count summary"
```

---

### Task 4: Rewrite `_build_body` in Markdown with `<details>` result details

**Files:**
- Modify: `src/movarr/notifications.py:243-262`
- Modify: `src/movarr/notifications.py:231-241` (`_extract_body_fields` field rename)

- [ ] **Step 1: Write failing tests for Markdown body output**

Add to `TestBuildBody` in `tests/unit/test_notifications.py`:

```python
    def test_body_uses_markdown_bold_labels(self) -> None:
        """Body uses **bold** Markdown syntax, not <strong> HTML tags."""
        cfg = Config()
        body = _build_body(_make_full_result(), cfg)
        assert "<strong>" not in body
        assert "**Status:**" in body
        assert "**Score:**" in body
        assert "**IMDb:**" in body
        assert "**Plot:**" in body
        assert "**Actors:**" in body
        assert "**Directors:**" in body
        assert "**Genres:**" in body
        assert "**Release:**" in body
        assert "**Size:**" in body

    def test_result_details_is_collapsible_block(self) -> None:
        """Result details are wrapped in <details><summary> block."""
        cfg = Config()
        body = _build_body(_make_full_result(), cfg)
        assert "<details>" in body
        assert "<summary>" in body
        assert "</details>" in body

    def test_imdb_link_is_bare_url(self) -> None:
        """IMDb link is a bare URL (auto-linked by Markdown renderers)."""
        cfg = Config()
        body = _build_body(_make_full_result(imdb_id="tt1375666"), cfg)
        assert "https://imdb.com/title/tt1375666" in body
        # Should not be an HTML <a> tag
        assert "<a href=" not in body

    def test_release_link_is_markdown(self) -> None:
        """Release title links to index details via Markdown [text](url) syntax."""
        cfg = Config()
        body = _build_body(_make_full_result(
            index_title="Inception 2010 1080p BluRay",
            index_details="http://example.com/details",
        ), cfg)
        assert "[Inception 2010 1080p BluRay](http://example.com/details)" in body

    def test_body_no_longer_uses_html_paragraphs(self) -> None:
        """Body uses Markdown line breaks, not <p> or <br> HTML tags."""
        cfg = Config()
        body = _build_body(_make_full_result(), cfg)
        assert "<p>" not in body
        assert "<br>" not in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /data/movarr && uv run pytest tests/unit/test_notifications.py::TestBuildBody -v`
Expected: New Markdown tests FAIL — body still uses HTML

- [ ] **Step 3: Rewrite `_build_body` in Markdown**

Replace the current `_build_body` implementation:

```python
def _build_body(result: ResultDict, config: Config) -> str:
    """Build the Markdown notification body with collapsible result details."""
    f = _extract_body_fields(result, config)

    imdb_line = ""
    if f["imdb_id"]:
        imdb_line = f"**IMDb:** https://imdb.com/title/{f['imdb_id']}\n"

    return (
        f"**Status:** {f['queue_status']}\n"
        f"**Score:** {f['rating']} from {f['votes']} users\n"
        f"{imdb_line}"
        f"**Plot:** {f['plot']}\n"
        f"**Actors:** {f['actors_str']}\n"
        f"**Directors:** {f['directors_str']}\n"
        f"**Genres:** {f['genres_str']}\n"
        f"**Release:** [{f['index_title']}]({f['index_details']})\n"
        f"**Size:** {f['index_size_mb']} MB\n\n"
        f"**Result Details:**\n"
        f"{f['result_details_md']}"
    )
```

Also update `_extract_body_fields` to rename the key from `result_details_html` to `result_details_md`:

```python
def _extract_body_fields(result: ResultDict, config: Config) -> dict[str, str]:
    """Extract and format all fields needed to render the notification body."""
    fields: dict[str, str] = {}
    fields.update(_extract_imdb_fields(result))
    fields.update(_extract_content_fields(result))
    fields.update(_extract_index_fields(result))
    fields["queue_status"] = _queue_status_str(config)
    fields["result_details_md"] = _format_result_details(result.get("result_details") or [])
    return fields
```

- [ ] **Step 4: Update existing tests to match Markdown output**

The following existing tests need assertion updates (remove HTML-specific assertions, replace with Markdown equivalents):

**`test_full_result_contains_key_fields`** — already checks for `"Status:"` and `"Score:"` strings; these pass as-is since Markdown still contains those substrings.

**`test_body_opens_with_status_and_score_not_title`** — update assertions:

```python
    def test_body_opens_with_status_and_score_not_title(self) -> None:
        """Body starts with Status and Score lines, not a redundant Title line."""
        cfg = Config()
        cfg.torrent_client.qbittorrent.add_paused = True
        body = _build_body(_make_full_result(), cfg)
        assert "**Status:** Paused" in body
        assert "**Score:** 8.8 from 2000000 users" in body
        assert "**Title:**" not in body
```

**`test_no_paragraph_breaks_in_body`** — body no longer uses `<p>` or `<br>`; update assertions to verify Markdown line separation:

```python
    def test_no_html_formatting_tags_in_body(self) -> None:
        """Body contains no HTML formatting tags — pure Markdown with <details> block."""
        cfg = Config()
        body = _build_body(_make_full_result(), cfg)
        assert "<p>" not in body
        assert "<br>" not in body
        assert "<strong>" not in body
        # Each field is on its own line
        assert body.startswith("**Status:**")
```

**`test_imdb_url_built_from_id`** — update assertion:

```python
    def test_imdb_url_built_from_id(self) -> None:
        """Body contains an IMDb URL built from imdb_id."""
        cfg = Config()
        assert "tt1375666" in _build_body(_make_full_result(), cfg)
```

(This one still passes — it just checks for the ID substring.)

**`test_missing_imdb_id_and_index_details_uses_hash_fallback`** — update to check Markdown link to `#`:

```python
    def test_missing_imdb_id_and_index_details_uses_hash_fallback(self) -> None:
        """Release link uses '#' as href when index_details is absent."""
        cfg = Config()
        result = _make_full_result()
        result.pop("imdb_id", None)
        result.pop("index_details", None)
        body = _build_body(result, cfg)
        assert "](#)" in body
```

**`test_imdb_bare_url_present_when_id_set`** and **`test_imdb_bare_url_absent_when_id_missing`** — these check for bare IMDb URL strings, which still appear in Markdown. They pass as-is.

**`test_poster_img_never_in_body`** — passes as-is (still no `<img>` in body).

- [ ] **Step 5: Run all tests to verify they pass**

Run: `cd /data/movarr && uv run pytest tests/unit/test_notifications.py -v`
Expected: ALL tests pass (existing + new)

- [ ] **Step 6: Commit**

```bash
git add src/movarr/notifications.py tests/unit/test_notifications.py
git commit -m "feat: rewrite _build_body in Markdown with collapsible <details> results"
```

---

### Task 5: Update `TestSafeUrlEdgeCases` tests for Markdown release link format

**Files:**
- Modify: `tests/unit/test_notifications.py` (TestSafeUrlEdgeCases class)

- [ ] **Step 1: Update `_safe_url` edge case test assertions**

The `_safe_url` function returns `"#"` for invalid URLs and `html.escape(raw, quote=True)` for valid ones. In Markdown, the release link uses `[title](url)` syntax. Update assertions:

```python
class TestSafeUrlEdgeCases:
    """_safe_url edge-cases exercised through _build_body."""

    def test_missing_index_details_uses_hash_href(self) -> None:
        """When index_details is absent the Markdown link uses href='#'."""
        cfg = Config()
        result = _make_full_result()
        result.pop("index_details", None)
        body = _build_body(result, cfg)
        assert "](#)" in body

    def test_non_http_scheme_uses_hash_href(self) -> None:
        """A non-http/https scheme like ftp:// must produce href='#'."""
        cfg = Config()
        result = _make_full_result(index_details="ftp://tracker.example.com/")
        body = _build_body(result, cfg)
        assert "](#)" in body

    def test_urlparse_exception_falls_back_to_hash(self, mocker: MockerFixture) -> None:
        """When urlparse raises, _safe_url returns '#'."""
        mocker.patch("movarr.notifications.urllib.parse.urlparse", side_effect=Exception("parse error"))
        cfg = Config()
        result = _make_full_result(index_details="http://example.com/details")
        body = _build_body(result, cfg)
        assert "](#)" in body
```

- [ ] **Step 2: Run tests to verify**

Run: `cd /data/movarr && uv run pytest tests/unit/test_notifications.py::TestSafeUrlEdgeCases -v`
Expected: All 3 tests pass

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_notifications.py
git commit -m "test: update SafeUrl edge case assertions for Markdown link format"
```

---

### Task 6: Update README with ntfy `?format=markdown` note

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add note about Markdown formatting for ntfy users**

Find the Notifications section in README and add a note:

```markdown
- **Notifications** — sends alerts via any [apprise](https://github.com/caronc/apprise)-compatible service
  (ntfy, Discord, Telegram, email, and more).  Notifications use Markdown formatting;
  **ntfy users** must append `?format=markdown` to their Apprise URL for bold text and
  links to render correctly (e.g. `ntfy://mytopic?format=markdown`).
```

- [ ] **Step 2: Verify formatting**

Run: `cd /data/movarr && markdownlint --fix README.md 2>/dev/null || echo "markdownlint not available, skipping"`
Expected: No lint errors (or skip if tool unavailable)

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: note ntfy ?format=markdown requirement in README"
```

---

### Task 7: Run full test suite and QA checks

**Files:** (none — verification only)

- [ ] **Step 1: Run full notification test suite**

Run: `cd /data/movarr && uv run pytest tests/unit/test_notifications.py -v`
Expected: ALL tests pass (should be ~60+ tests)

- [ ] **Step 2: Run mypy type check**

Run: `cd /data/movarr && uv run mypy src/movarr/notifications.py`
Expected: No type errors

- [ ] **Step 3: Run ruff lint/format**

Run: `cd /data/movarr && uv run ruff check --fix src/movarr/notifications.py tests/unit/test_notifications.py && uv run ruff format src/movarr/notifications.py tests/unit/test_notifications.py`
Expected: No lint errors (or only pre-existing ones in unchanged lines)

- [ ] **Step 4: Run full project test suite**

Run: `cd /data/movarr && uv run pytest -v`
Expected: All tests pass

- [ ] **Step 5: Final commit (if any lint/format changes)**

```bash
git add -A
git commit -m "chore: final lint and format pass for notification Markdown redesign"
```
