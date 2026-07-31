# Notification Body Formatting — Visual Polish

**Date:** 2026-07-11
**Status:** Approved

## Problem

The current movarr notification markdown body renders correctly but feels cramped on mobile. The
user provided a template (`/data/.scratch/movarr.md`) showing the desired visual style: blank-line
spacing between fields and bold status prefixes in result details.

## Design

Three targeted changes to `src/movarr/notifications.py`. The dual-Apprise dispatch architecture,
Links section, subject line, and all public API functions are unchanged.

### 1. Blank lines between body fields

`_build_markdown_body` inserts an empty string between every field (Status, Score, Plot, Actors,
Directors, Genres, Release, Size, Links, Result Details). The `"\n".join(lines)` produces blank-line
separators for visual breathing room.

`_build_text_body` gets matching blank lines for consistency.

**Before:**
```
**Status:** Started
**Score:** 8.8 from 2000000 users
**Plot:** A thief who steals corporate secrets.
```

**After:**
```
**Status:** Started

**Score:** 8.8 from 2000000 users

**Plot:** A thief who steals corporate secrets.
```

### 2. Result details — inline count, bold status prefixes

`_format_result_details` moves the pass/fail count inline with the label and bolds each item's
status prefix (`**Passed:**` / `**Failed:**`).

**Before:**
```
**Result Details:**
_3 checks passed_
- Passed: check alpha
- Failed: check beta
```

**After:**
```
**Result Details:** 3 checks passed

- **Passed:** check alpha
- **Failed:** check beta
```

### 3. Plain-text result details — matching format

`_format_result_details_text` mirrors the markdown changes: count inline, no bold markup. Uses
two-space indented `  - ` bullets to distinguish items visually.

```
Result Details: 3 checks passed

  - Passed: check alpha
  - Failed: check beta
```

### Functions modified

| Function | Change |
|----------|--------|
| `_build_markdown_body` | Insert `""` entries between every field line |
| `_build_text_body` | Insert `""` entries between every field line |
| `_format_result_details` | Inline count: `f"**Result Details:** {count_str}\n\n{items}"`. Items use `**Passed:**` / `**Failed:**` bold prefixes |
| `_format_result_details_text` | Inline count: `f"Result Details: {count_str}\n\n{items}"`. Items use plain `Passed:` / `Failed:` prefixes |

### What does NOT change

- Subject line (`_build_subject`) — stays as plain text for Apprise `title=` parameter
- No H1 heading in body — subject is Apprise title only
- Links section (`_build_links_section`) — already matches template
- Dual Apprise dispatch (`_dispatch_apprise`) — unchanged
- `send_queued_notification`, `send_service_alert` — unchanged
- All other modules — untouched

### Edge cases preserved

- No IMDb ID → Links section omitted (unchanged behavior)
- Empty result details → "0 checks" (unchanged)
- Mixed pass/fail → "2 passed, 1 failed" (unchanged)
- Markdown escaping of apostrophes and special chars (unchanged, `_escape_markdown_text` still applied)

## Files changed

| File | Changes |
|------|---------|
| `src/movarr/notifications.py` | `_build_markdown_body`, `_build_text_body`, `_format_result_details`, `_format_result_details_text` |
| `tests/unit/test_notifications.py` | Update assertions for blank lines and new result details format |

## Tests

### Assertions to update

- `TestBuildMarkdownBody` — all body assertions must expect blank lines between fields
- `TestBuildMarkdownBody.test_body_opens_with_status_and_score_not_title` — body starts with `**Status:** Paused\n\n` (blank line after)
- `TestBuildMarkdownBody.test_result_details_is_markdown_list` — assert `"**Result Details:** 1 items"` (no italic, count inline) and `"**Passed:**"` or `"**Quality:**"` (bold prefix on items that start with "Passed"/"Failed")
- `TestFormatResultDetails` — count inline with `**Result Details:**` prefix, bold status prefixes
- `TestFormatResultDetailsText` — count inline, no markdown formatting in items
- `TestBuildTextBody` — blank lines between fields

### No new test classes needed

Existing coverage already exercises all paths through the modified functions. The changes are
formatting-only — no new branches or control flow.

## Rendering comparison

### Current
```
**Status:** Started
**Score:** 8.8 from 2000000 users
**Plot:** A thief who steals corporate secrets.
**Actors:** Leonardo DiCaprio, Joseph Gordon-Levitt
**Directors:** Christopher Nolan
**Genres:** Action, Adventure, Sci-Fi
**Release:** Inception 2010 1080p BluRay
**Size:** 8192 MB

**Links:** [IMDb](https://imdb.com/title/tt1375666)

**Result Details:**
_1 items_
- Quality: Rating: 8.8
```

### After
```
**Status:** Started

**Score:** 8.8 from 2000000 users

**Plot:** A thief who steals corporate secrets.

**Actors:** Leonardo DiCaprio, Joseph Gordon-Levitt

**Directors:** Christopher Nolan

**Genres:** Action, Adventure, Sci-Fi

**Release:** Inception 2010 1080p BluRay

**Size:** 8192 MB

**Links:** [IMDb](https://imdb.com/title/tt1375666)

**Result Details:** 1 items

- **Quality:** Rating: 8.8
```
