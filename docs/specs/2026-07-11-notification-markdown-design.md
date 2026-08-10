# Notification Markdown Formatting for Apprise

**Date:** 2026-07-11
**Status:** Implemented

## Problem

movarr's Apprise notification body had two formatting issues:

1. **Release title rendered as a clickable link** — `**Release:** [{index_title}](<{index_details}>)` wrapped the index title in a markdown link, making the release name appear clickable in the notification. This was confusing and visually wrong.

2. **IMDb link was bare text** — `**IMDb:** https://imdb.com/title/{id}` used a bare URL, which is not auto-linked by all Apprise services (notably ntfy). The user saw plain text instead of a clickable link.

Additionally, movarr always sent `NotifyFormat.MARKDOWN` to all Apprise services regardless of whether they support markdown rendering. This meant non-markdown-capable services (email, custom webhooks, etc.) received garbled `**bold**` syntax and `[link](url)` formatting rendered as literal text.

## Reference

gamarr (the game download automation project) already solved this with a clean approach documented in `gamarr/src/gamarr/notifications.py`. The design follows that pattern.

## Design

### Core Insight: Dual-Instance Apprise Dispatch

Rather than sending one body format to all services, split the configured Apprise URLs into two groups:

1. **Markdown-capable services** — receive a body with `**bold**` labels, `[link](url)` syntax, and `NotifyFormat.MARKDOWN`
2. **Text-only services** — receive a clean plain-text body with no markdown formatting

### Markdown-Capable Schemes

Following gamarr's `_MARKDOWN_SCHEMES` set:

| Scheme | Service |
|--------|---------|
| `ntfy`, `ntfys` | Ntfy push notifications |
| `discord` | Discord webhooks |
| `slack` | Slack webhooks |
| `tgram`, `tg` | Telegram |
| `matrix`, `matrixs` | Matrix |

ntfy URLs get `?format=markdown` auto-appended if not already present, since the ntfy Apprise plugin defaults to `NotifyFormat.TEXT`.

### Notification Body Format

**Before (one format for all services):**
```
**Status:** Started
**Score:** 8.8 from 2000000 users
**IMDb:** https://imdb.com/title/tt1375666          ← bare URL, not clickable
**Plot:** ...
**Actors:** ...
**Directors:** ...
**Genres:** ...
**Release:** [Inception 2010...](<url>)               ← title IS a link (wrong!)
**Size:** 8192 MB

**Result Details:**
_2 checks passed_
- Passed: check a
```

**After — Markdown body (ntfy, discord, etc.):**
```
**Status:** Started
**Score:** 8.8 from 2000000 users
**Plot:** ...
**Actors:** ...
**Directors:** ...
**Genres:** ...
**Release:** Inception 2010 1080p BluRay x264 DTS    ← plain text only
**Size:** 8192 MB

**Links:** [IMDb](https://imdb.com/title/tt1375666)  ← clickable link

**Result Details:**
_2 checks passed_
- Passed: check a
```

**After — Text body (email, non-markdown services):**
```
Status: Started
Score: 8.8 from 2000000 users
Plot: ...
Actors: ...
Directors: ...
Genres: ...
Release: Inception 2010 1080p BluRay x264 DTS
Size: 8192 MB

Links: https://imdb.com/title/tt1375666

Result Details:
2 checks passed
  - Passed: check a
```

### Links Section

Only the IMDb link is included. Torrent index URLs are intentionally excluded because Jackett and Prowlarr (index proxies) provide URLs pointing to their internal APIs (e.g., `localhost:9696/api/v1/...`) which are not externally useful. A single reliable link is better than one good link mixed with broken ones.

When no `imdb_id` is available, the Links section is omitted entirely.

### Functions

| Function | Change |
|----------|--------|
| `_build_markdown_body(fields)` | Replaces old `_build_body(result, config)`. Produces markdown body with Links section |
| `_build_text_body(fields)` | New. Produces plain-text body for non-markdown services |
| `_build_links_section(fields, *, use_markdown)` | New. Builds `**Links:** [IMDb](url)` or `Links: url` |
| `_format_result_details_text(details)` | New. Plain-text variant of result details (no `_italic_` or `- bullet`) |
| `_is_markdown_service(url)` | New. Classifies URL by scheme against `_MARKDOWN_SCHEMES` |
| `_extract_body_fields(result, config)` | Extended. Now returns both `result_details_md` and `result_details_text` |
| `_dispatch_apprise(subject, urls, *, body_markdown, body_text)` | Refactored. Splits URLs, sends appropriate format to each group |
| `send_queued_notification(result, config)` | Updated. Builds both bodies, passes both to dispatch |
| `send_service_alert(...)` | Updated. Builds both markdown and text alert bodies |

### Edge Cases

- **No `imdb_id`**: Links section omitted entirely
- **No index_details**: Does not affect output (torrent links excluded by design)
- **ntfy URLs without `?format=markdown`**: Auto-appended
- **Mixed URL lists**: Two separate Apprise instances handle each group independently
- **Apprise exceptions**: Caught per-instance; failure in one group does not block the other

## Testing

106 tests in `tests/unit/test_notifications.py`, all passing. 100% coverage on `notifications.py` (203 statements, 0 missed).

Key test classes:
- `TestBuildMarkdownBody` — 20 tests for markdown body format
- `TestBuildTextBody` — 4 tests for text body format
- `TestBuildLinksSection` — 4 tests for links section construction
- `TestDispatchApprise` — 10 tests for split markdown/text dispatch including exception paths
- `TestIsMarkdownService` — 11 tests for URL scheme classification
- `TestFormatResultDetailsText` — 5 tests for plain-text result details
- `TestSendQueuedNotification` — 6 tests for end-to-end notification dispatch
- `TestSafeUrlEdgeCases` — 3 tests for index_details edge cases
