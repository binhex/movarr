# Index Site Name in Notifications

**Date:** 2026-08-02
**Status:** Approved

## Overview

Add the release site name (e.g. "RARBG", "1337x") to the queued-torrent
notification body so users can see which site a release came from.

## Context

Both index proxies expose the release site name:

- **Jackett** — `jackettindexer` element text in the Torznab feed
  (e.g. "RARBG", "1337x")
- **Prowlarr** — `indexer` field in the JSON search result

The name is already captured in `ResultDict.index_tracker` by both clients
(`jackett.py` line 178, `prowlarr.py` line 190) and flows through the whole
pipeline to `send_queued_notification()`. The notification body just never
renders it.

This is a display-only change. No new API calls, no config, no model changes.

## Requirements

1. The release site name appears as a separate line in the notification body:
   `**Site:** RARBG` (markdown) / `Site: RARBG` (plain text).
2. The line always appears, even when the site name is empty or missing —
   in that case it reads `**Site:** Unknown`.
3. The change applies to all notification targets (markdown-capable and
   text-only apprise services).

## Implementation

### File: `src/movarr/notifications.py`

**1. `_extract_index_fields(result: ResultDict) -> dict[str, str]`**

Add `index_tracker` to the returned dict, defaulting to `"Unknown"` when
empty or missing:

```python
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

**2. `_build_markdown_body(f: dict[str, str]) -> str`**

Add a `**Site:**` line between `**Genres:**` and `**Release:**`:

```python
f"**Genres:** {f['genres_str']}",
"",
f"**Site:** {f['index_tracker']}",
"",
f"**Release:** {f['index_title']}",
```

**3. `_build_text_body(f: dict[str, str]) -> str`**

Same placement for plain-text targets:

```python
f"Genres: {f['genres_str']}",
"",
f"Site: {f['index_tracker']}",
"",
f"Release: {f['index_title']}",
```

### Resulting markdown body

```
**Status:** Started

**Score:** 8.5 from 1,234,567 users

**Plot:** ...

**Actors:** ...

**Directors:** ...

**Genres:** ...

**Site:** RARBG

**Release:** Some.Movie.2011.1080p.BluRay.x264-SCENE

**Size:** 1234 MB

**Links:** [IMDb](https://imdb.com/title/tt1234567)

**Result Details:** 12 checks passed
```

## Testing

File: `tests/unit/test_notifications.py`

- Update `test_full_result_contains_key_fields` (and any other body
  assertions) to include the new `**Site:**` / `Site:` line.
- New test: `test_empty_index_tracker_shows_unknown` — a result without
  `index_tracker` renders `**Site:** Unknown` (and `Site: Unknown` in text).
- New test (optional): `test_index_tracker_preserved` — non-empty tracker
  (e.g. `"RARBG"`) renders exactly.

## Out of Scope

- No link to the release site — proxy URLs (e.g. `localhost:9696/api/...`)
  are not externally useful and stay excluded from the Links section.
- No changes to `search.py`, `jackett.py`, `prowlarr.py`, or `models.py`.
- No config options — the site line is always shown.

## Files Changed

| File | Change |
|------|--------|
| `src/movarr/notifications.py` | `_extract_index_fields` (+1 field), `_build_markdown_body` (+2 lines), `_build_text_body` (+2 lines) |
| `tests/unit/test_notifications.py` | Update body assertions, add empty-tracker + tracker-preserved tests |
