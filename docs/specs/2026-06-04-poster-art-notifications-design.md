# Poster Art in Notifications & Disk Save

**Date:** 2026-06-04
**Status:** Draft
**Config version target:** 2.21.0

---

## 1. Summary

Add three linked features around the IMDb poster URL already stored in the pipeline:

1. **Embed poster art in queued notifications** via Apprise's `attach` parameter.
2. **Save poster art to disk** after verified post-processing copy.
3. **Add a plain-text IMDb link** to notification bodies so ntfy (and other plain-text-oriented services) show a tappable URL.

All three share a small URL manipulation helper for Amazon S3 poster URLs (stripping or appending a `_SX<width>` resolution suffix).

---

## 2. Architecture

### 2.1 Shared poster URL helpers

A small set of pure functions added to `src/movarr/notifications.py`. Exported and imported by `src/movarr/post_processor.py`.

```python
import re

_AMAZON_POSTER_RES_RE = re.compile(r'_S[XWY]\d+')
_AMAZON_POSTER_SUFFIX = '._V1_.jpg'


def _strip_poster_resolution(url: str) -> str:
    """Strip any _SX<width> / _SY<height> / _SW<width> suffix from an Amazon poster URL."""
    return _AMAZON_POSTER_RES_RE.sub('', url)


def _poster_url_with_width(url: str, width: int) -> str:
    """Return the poster URL constrained to *width* pixels (width=0 returns largest)."""
    if width <= 0:
        return _strip_poster_resolution(url)
    stripped = _strip_poster_resolution(url)
    return stripped.replace(_AMAZON_POSTER_SUFFIX, f'._V1_SX{width}.jpg')
```

**No new Python dependency** — `urllib.request` (stdlib) handles the download.

### 2.2 Data flow

```
IMDbPie / OMDb
    │
    ▼
_apply_metadata()   ← strips resolution before storing
    │
    ▼
ResultDict.imdb_poster_url   (resolution-free URL)
    │
    ├──► Database (HistoryRecord.imdb_poster_url)
    │
    ├──► send_queued_notification()
    │       │
    │       ├── _poster_url_with_width(url, poster_embed_width)
    │       │       │
    │       │       ▼
    │       │   ap.notify(..., attach=resized_url)
    │       │
    │       └── IMDb bare URL added to body text
    │
    └──► _post_copy_actions()
            │
            └── _save_poster_art()
                    │
                    ├── _poster_url_with_width(url, download_width)
                    ├── urllib.request.urlopen(resized_url)
                    └── write to {dst_dir}/{filename}
```

---

## 3. Feature 1 — Poster embed in notifications

### 3.1 Config

Add a new subsection under `notification`:

```yaml
notification:
  apprise_urls: []
  index_proxy_alert_hours: 0
  torrent_client_alert_hours: 0
  poster_embed_enabled: true     # NEW — enable poster attach in notification
  poster_embed_width: 500        # NEW — px width (0 = original/largest)
```

### 3.2 Implementation

**File:** `src/movarr/notifications.py` — `send_queued_notification()`

After building subject and body, if the result has a non-None `imdb_poster_url` and config has `poster_embed_enabled: true`:

1. Resolve the URL with `_poster_url_with_width(url, config.notification.poster_embed_width)`.
2. Pass as `attach` to `ap.notify(...)`:
   ```python
   attach = poster_url if poster_url else None
   sent = ap.notify(
       title=subject,
       body=body,
       body_format=apprise.NotifyFormat.HTML,
       attach=attach,
   )
   ```

Apprise handles the download and per-plugin attachment. For ntfy, the poster appears as an inline image in the expanded notification. For email, it's embedded in the message.

If `imdb_poster_url` is `None`, `poster_embed_enabled` is `false`, or URL resolution produces an empty string — no attachment is sent (no change in behavior for titles without poster URLs).

### 3.3 DB storage — strip resolution at source

In `src/movarr/imdb_metadata.py` — `_apply_metadata()`:

```python
poster_raw = data.get("poster")
result["imdb_poster_url"] = _strip_poster_resolution(poster_raw) if poster_raw else None
```

This ensures the stored URL in the database is always resolution-free, so all downstream consumers start from a clean base.

---

## 4. Feature 2 — Save poster art to disk

### 4.1 Config

Add a new subsection under `post_process`:

```yaml
post_process:
  post_process_enabled: true
  ...
  poster_art:                    # NEW
    filename: ""                 # blank = disabled; e.g. "poster.jpg" or "folder.jpg"
    download_width: 0            # 0 = original/largest, else px width
```

**Rules:**
- `filename` blank → poster is not saved (regardless of any state).
- `filename` set → extension is forced to `.jpg`. If user types `poster.png`, it becomes `poster.jpg`. If they type `poster`, it becomes `poster.jpg`.
- `download_width: 0` → resolution is stripped from the URL (largest available).
- `download_width: > 0` → `_SX<width>` is appended before `.jpg`.

### 4.2 Implementation

**File:** `src/movarr/post_processor.py` — new function `_save_poster_art()`

```python
def _save_poster_art(
    db_record: HistoryRecord,
    dst_dir: str,
    config: Config,
) -> None:
    """Download poster art to *dst_dir* with configured name/resolution.

    Logs and returns silently on any failure — does NOT abort post-processing.
    """
    poster_cfg = config.post_process.poster_art
    filename = poster_cfg.filename or ""
    if not filename:
        return

    # Force .jpg extension
    stem, _ = os.path.splitext(filename)
    safe_name = f"{stem}.jpg"

    poster_url = db_record.imdb_poster_url
    if not poster_url:
        logger.debug("No poster URL for '{}'; skipping poster save.", db_record.imdb_title)
        return

    # Resolve width
    resolved_url = _poster_url_with_width(poster_url, poster_cfg.download_width)

    # Download
    dst_path = os.path.join(dst_dir, safe_name)
    try:
        with urllib.request.urlopen(resolved_url, timeout=30) as response:
            content_type = response.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                logger.warning("Poster URL returned non-image content '{}'; skipping.", content_type)
                return
            with open(dst_path, "wb") as f:
                shutil.copyfileobj(response, f)
        logger.info("Saved poster art to '{}'.", dst_path)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to download/save poster art from '{}'.", resolved_url)
```

**Called from** `_post_copy_actions()` after `db.mark_completed(tag)` and before `delete_lower_quality`:

```python
# Save poster art
if config.post_process.poster_art.filename:
    _save_poster_art(db_record, dst_dir, config)
```

**Error handling:** All errors are logged at `WARNING` level and swallowed — post-processing continues without interruption. This is intentional: missing poster art is cosmetic, not critical.

### 4.3 Migration

Migration for the new fields:

```python
_MIGRATION_TABLE.append((
    "2.20.0",
    "2.21.0",
    [
        (("notification", "poster_embed_enabled"), True),
        (("notification", "poster_embed_width"), 500),
        (("post_process", "poster_art"), {"filename": "", "download_width": 0}),
    ],
))
```

---

## 5. Feature 3 — IMDb link in ntfy notifications

### 5.1 Problem

The current notification body uses an HTML `<a>` tag for the IMDb link:

```html
<a href="https://imdb.com/title/tt1375666">Inception (2010)</a>
```

Apprise converts HTML → plain text for non-HTML plugins (including ntfy). The `<a>` tag's `href` is **lost**, leaving only the visible text with no link target.

### 5.2 Solution

Add a bare IMDb URL as visible text in the notification body, immediately after the title line. ntfy auto-detects bare URLs and makes them tappable in the notification detail view.

In `_build_body()`, add:

```python
f"""<p><strong>IMDb:</strong> https://imdb.com/title/{f["imdb_id"]}</p>"""
```

rendered right after the title line:

```html
<p><strong>Title:</strong> <a href="https://imdb.com/title/tt1375666">Inception (2010)</a> — 8.8 from 2000000 users</p>
<p><strong>IMDb:</strong> https://imdb.com/title/tt1375666</p>
```

For HTML-capable services (email), this renders as a visible URL that recipients can click. For ntfy, the bare URL is tap-able in the expanded notification detail view.

If `imdb_id` is absent, the line is omitted entirely.

---

## 6. Files modified

| File | Change |
|------|--------|
| `src/movarr/notifications.py` | Add `_strip_poster_resolution`, `_poster_url_with_width`. Modify `send_queued_notification` to pass `attach`. Modify `_build_body` to add IMDb plain-text URL. |
| `src/movarr/imdb_metadata.py` | Strip resolution from poster URL in `_apply_metadata`. |
| `src/movarr/post_processor.py` | Add `_save_poster_art`. Call it from `_post_copy_actions`. |
| `src/movarr/config.py` | Add `PosterEmbedConfig`, `PosterArtConfig` pydantic models. Add migration entry. |
| `tests/unit/test_notifications.py` | Tests for URL helpers + attach param. |
| `tests/unit/test_post_processor.py` | Tests for `_save_poster_art` (mock download). |
| `tests/unit/test_imdb_metadata.py` | Update existing poster tests for resolution stripping. |
| `tests/unit/test_config.py` | Tests for new config defaults + migration. |

## 7. Edge cases

| Scenario | Behavior |
|----------|----------|
| Poster URL is `None` (IMDbPie/OMDb returned no URL) | Notification → no attach. Disk save → silently skipped. |
| Poster URL has no `_V1_.jpg` pattern (unexpected format) | `_poster_url_with_width` returns the URL unchanged. Disk save proceeds. |
| Download fails (network timeout, 404, DNS) | Logged at WARNING, post-processing continues. |
| Download returns non-image content (redirect to HTML) | Content-type check catches it, silently skipped. |
| User sets `filename` to `folder` (no extension) | Forced to `folder.jpg`. |
| User sets `filename` to `poster.png` | Forced to `poster.jpg`. |
| IMDb ID is missing/empty | IMDb link line omitted from notification body. |
| `download_width` is negative | Treated as 0 (largest available). |

## 8. Testing plan

### Unit tests

| Test | Location |
|------|----------|
| `_strip_poster_resolution` removes `_SX500` | `test_notifications.py` |
| `_strip_poster_resolution` removes `_SY1080` | `test_notifications.py` |
| `_strip_poster_resolution` leaves URL without resolution unchanged | `test_notifications.py` |
| `_poster_url_with_width(..., 500)` inserts `_SX500` | `test_notifications.py` |
| `_poster_url_with_width(..., 0)` strips resolution | `test_notifications.py` |
| Notification calls `notify()` with `attach=poster_url` when enabled | `test_notifications.py` |
| Notification calls `notify()` without `attach` when disabled | `test_notifications.py` |
| Notification calls `notify()` without `attach` when URL is None | `test_notifications.py` |
| IMDb link line present in body when `imdb_id` set | `test_notifications.py` |
| IMDb link line absent in body when `imdb_id` missing | `test_notifications.py` |
| `_save_poster_art` writes file to correct path | `test_post_processor.py` |
| `_save_poster_art` skips when filename is blank | `test_post_processor.py` |
| `_save_poster_art` skips when poster URL is None | `test_post_processor.py` |
| `_save_poster_art` handles download failure gracefully | `test_post_processor.py` |
| IMDb metadata strips resolution before storing | `test_imdb_metadata.py` |
| Config migration adds new fields with defaults | `test_config.py` |

### Coverage target

No reduction from current 100%. All new code branches covered.

---

## 9. Config version

Bump `_CONFIG_VERSION` from `"2.20.0"` to `"2.21.0"` (next available version after existing `2.20.0`). Add migration entry `_migrate_v220_to_v221`.
