# Notification Format Redesign: Mobile-Readable Markdown

**Date:** 2026-06-22
**Status:** Approved

## Problem

Movarr notifications display poorly on mobile (ntfy). Two issues:

1. **Section labels blend in** — `Status:`, `Score:`, `Plot:` etc. use `<strong>` HTML tags, but ntfy strips HTML formatting, so labels and values render at the same weight. Hard to skim on a phone screen.

2. **Result Details is a wall of text** — 20+ individual check lines scroll forever. The information is useful for debugging but overwhelming in a notification.

Both fixes must work across all Apprise-compatible services (ntfy, Discord, Telegram, email), not just ntfy.

## Design

### 1. Switch body format from HTML to Markdown

Change `body_format` from `apprise.NotifyFormat.HTML` to `apprise.NotifyFormat.MARKDOWN`.

Apprise's `NotifyFormat.MARKDOWN` auto-converts to HTML for services that expect it (Discord, email) and passes Markdown natively for services that support it (ntfy with `?format=markdown`, Telegram).

**User action required:** ntfy users must add `?format=markdown` to their Apprise URL. Example: `ntfy://mytopic?format=markdown`. Without it, Markdown renders as plain text (bold markers show as `**text**` — still readable).

### 2. Bold section labels via Markdown syntax

Replace `<strong>Status:</strong> Started` with `**Status:** Started`.

All section labels (`Status`, `Score`, `IMDb`, `Plot`, `Actors`, `Directors`, `Genres`, `Release`, `Size`, `Result Details`) use `**` bold markers.

### 3. Collapsible Result Details via inline HTML `<details>` tag

Wrap the check list in `<details><summary>` HTML tags inside the Markdown body:

```markdown
**Result Details:**

<details>
<summary>20 checks passed</summary>

- Passed: Release group rips is not in reject list.
- Passed: Index title passes search criteria 2160p remux.
...
</details>
```

Most Markdown renderers pass through safe HTML tags. The `<details>` element degrades gracefully — services that strip HTML show the list inline (not hidden). Services that support it get a tap-to-expand toggle.

The `<summary>` text shows the pass/fail count: `"20 checks passed"`, `"18 passed, 2 failed"`, etc.

### 4. Body layout (unchanged structure, new format)

```
movarr: Arlington Road (1999)                    ← plain text subject (unchanged)
                                                  ← blank line
**Status:** Started                               ← Markdown bold labels
**Score:** 7.2 from 99141 users
**IMDb:** https://imdb.com/title/tt0137363
**Plot:** A man begins to suspect...
**Actors:** Jeff Bridges, Tim Robbins, ...
**Directors:** Mark Pellington
**Genres:** Action, Crime, Drama, Mystery, Thriller
**Release:** Arlington Road (1999) 2160p Bluray Remux...
**Size:** 51679 MB
**Result Details:**
<details>
<summary>20 checks passed</summary>
- Passed: ... (all 20 checks as list items)
</details>
```

## Files Changed

| File | Changes |
|------|---------|
| `src/movarr/notifications.py` | `body_format`: `HTML` → `MARKDOWN`. `_build_body` rewritten in Markdown + inline HTML. `_format_result_details` unchanged except wrapping. `_extract_body_fields` may need minor adjustments (remove unused `result_details_html`, rename). |
| `tests/unit/test_notifications.py` | Update all body assertions from HTML to Markdown. Add test for `<details>` wrapper. Add test for pass/fail count in `<summary>`. |
| `README.md` | Note about `?format=markdown` for ntfy users. |

## Tests

### Unit tests to add/update

- `test_body_uses_markdown_bold_labels` — body contains `**Status:**` not `<strong>Status:</strong>`
- `test_result_details_wrapped_in_details_tag` — body contains `<details><summary>` with pass count
- `test_result_details_degrade_gracefully` — when `<details>` is unsupported, list content is still present
- `test_pass_count_in_summary` — `<summary>` shows "20 checks passed" for all-passed results
- `test_mixed_pass_fail_count` — `<summary>` shows "18 passed, 2 failed" when there are failures
- Update all existing tests that assert on body HTML to assert on Markdown

### Behavior unchanged

- Subject line (`_build_subject`): no change, already plain text
- IMDb link: rendered as auto-linked URL (Markdown auto-links raw URLs, same behavior as plain text)
- `_extract_*_fields` helpers: no change needed
- `send_queued_notification`, `send_service_alert`, `send_index_proxy_alert`: no change needed

## Edge Cases

- **No result details** (empty list): `<summary>` says "0 checks" and `<details>` contains no items
- **Only failures**: `<summary>` says "0 passed, 3 failed"
- **Plain text fallback**: ntfy without `?format=markdown` shows `**Status:**` as literal text with asterisks — still readable, bold visible as emphasis markers
- **Very long check lines**: existing HTML escaping (`html.escape`) still applied to each check item inside `<details>`
- **Missing IMDb ID**: IMDb line omitted (existing behavior, unchanged)
