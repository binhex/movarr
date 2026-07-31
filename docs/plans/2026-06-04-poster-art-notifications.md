# Poster Art in Notifications & Disk Save Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Embed poster art in queued notifications, save poster art to disk during post-processing, and add a plain-text IMDb URL to notification bodies for ntfy compatibility.

**Architecture:** Three features sharing a small URL-manipulation helper (strip/set `_SX<width>` resolution on Amazon poster URLs). Poster URL is already stored in the database; notification embedding uses Apprise's `attach` parameter; disk save creates a file in the movie's destination folder after verified copy. No new Python dependencies.

**Tech Stack:** Python 3.12, Apprise, SQLAlchemy, Pydantic, urllib (stdlib)

---

## Scope

This plan covers three tightly coupled features from one design doc:

1. **Poster embed in notifications** — Apprise `attach` with configurable width
2. **Poster save to disk** — download to `{dst_dir}/{filename}.jpg` after verified copy
3. **IMDb plain-text link** — bare URL in notification body for ntfy tap-target

All three are small, independent enough to be implemented in sequence. No sub-system decomposition needed.

---

## File Structure

| File | Status | Responsibility |
|------|--------|---------------|
| `src/movarr/notifications.py` | Modify | Add `_strip_poster_resolution`, `_poster_url_with_width`, attach poster to `notify()` call, add IMDb plain-text URL line to `_build_body()` |
| `src/movarr/imdb_metadata.py` | Modify | Strip resolution from poster URL in `_apply_metadata()` |
| `src/movarr/post_processor.py` | Modify | Add `_save_poster_art()` function, wire into `_post_copy_actions()`, add `db_record` param |
| `src/movarr/config.py` | Modify | Add `PosterArtConfig` Pydantic model, add fields to `NotificationConfig`, add migration entry |
| `tests/unit/test_notifications.py` | Modify | Add tests for URL helpers, attach param, IMDb link line |
| `tests/unit/test_post_processor.py` | Modify | Add tests for `_save_poster_art` (mock download) |
| `tests/unit/test_imdb_metadata.py` | Modify | Add test for poster resolution stripping |
| `tests/unit/test_config.py` | Modify | Add tests for new config defaults + migration |

---

## Task 1: Poster URL helpers

Add two pure functions to `src/movarr/notifications.py` that manipulate Amazon poster URLs. These are the shared building blocks used by both notification embedding and disk save.

**Files:**
- Modify: `src/movarr/notifications.py` — add after imports, before `__all__`
- Test: `tests/unit/test_notifications.py` — add `TestPosterUrlHelpers` class

- [ ] **Step 1: Write passing tests for URL helpers**

Add to `tests/unit/test_notifications.py` (before the existing `TestBuildSubject` class):

```python
from movarr.notifications import (
    _build_body,
    _build_subject,
    _dispatch_apprise,
    _format_result_details,
    _poster_url_with_width,  # new
    _strip_poster_resolution,  # new
    send_index_proxy_alert,
    send_queued_notification,
    send_service_alert,
)
```

Add new test class at the end of the file:

```python
class TestPosterUrlHelpers:
    """Tests for poster URL resolution/strip helpers."""

    def test_strip_removes_sx500(self) -> None:
        url = "https://m.media-amazon.com/images/M/MV5B._V1_SX500.jpg"
        assert _strip_poster_resolution(url) == "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"

    def test_strip_removes_sy1080(self) -> None:
        url = "https://m.media-amazon.com/images/M/MV5B._V1_SY1080.jpg"
        assert _strip_poster_resolution(url) == "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"

    def test_strip_removes_sw300(self) -> None:
        url = "https://m.media-amazon.com/images/M/MV5B._V1_SW300.jpg"
        assert _strip_poster_resolution(url) == "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"

    def test_strip_leaves_unmodified_url_without_resolution(self) -> None:
        url = "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"
        assert _strip_poster_resolution(url) == url

    def test_strip_handles_url_without_v1_suffix(self) -> None:
        url = "https://m.media-amazon.com/images/M/MV5B.jpg"
        assert _strip_poster_resolution(url) == url

    def test_width_500_inserts_sx500(self) -> None:
        url = "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"
        assert _poster_url_with_width(url, 500) == "https://m.media-amazon.com/images/M/MV5B._V1_SX500.jpg"

    def test_width_0_strips_existing_resolution(self) -> None:
        url = "https://m.media-amazon.com/images/M/MV5B._V1_SX1080.jpg"
        assert _poster_url_with_width(url, 0) == "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"

    def test_width_negative_treated_as_zero(self) -> None:
        url = "https://m.media-amazon.com/images/M/MV5B._V1_SX500.jpg"
        assert _poster_url_with_width(url, -1) == "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"

    def test_width_500_on_already_resized_url_replaces_resolution(self) -> None:
        url = "https://m.media-amazon.com/images/M/MV5B._V1_SX1080.jpg"
        assert _poster_url_with_width(url, 500) == "https://m.media-amazon.com/images/M/MV5B._V1_SX500.jpg"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/movarr && uv run pytest tests/unit/test_notifications.py::TestPosterUrlHelpers -v --tb=short
```

Expected: `FAILED` — `ImportError: cannot import name '_strip_poster_resolution'`

- [ ] **Step 3: Implement the URL helpers**

Add at the top of `src/movarr/notifications.py`, after the imports and before `__all__`:

```python
import re

_AMAZON_POSTER_RES_RE = re.compile(r'_S[XWY]\d+')
_AMAZON_POSTER_SUFFIX = '._V1_.jpg'


def _strip_poster_resolution(url: str) -> str:
    """Strip any _SX<width> / _SY<height> / _SW<width> suffix from an Amazon poster URL."""
    return _AMAZON_POSTER_RES_RE.sub('', url)


def _poster_url_with_width(url: str, width: int) -> str:
    """Return the poster URL constrained to *width* pixels (width <= 0 returns largest/original)."""
    if width <= 0:
        return _strip_poster_resolution(url)
    stripped = _strip_poster_resolution(url)
    return stripped.replace(_AMAZON_POSTER_SUFFIX, f'._V1_SX{width}.jpg')
```

Also add to `__all__`:

```python
__all__ = [
    "send_index_proxy_alert",
    "send_queued_notification",
    "send_service_alert",
]
```

(No new public exports needed — the helpers start with `_`.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /data/movarr && uv run pytest tests/unit/test_notifications.py::TestPosterUrlHelpers -v --tb=short
```

Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
cd /data/movarr && git add src/movarr/notifications.py tests/unit/test_notifications.py
git commit -m "feat: add poster URL resolution helpers (_strip_poster_resolution, _poster_url_with_width)"
```

---

## Task 2: Strip poster resolution at metadata source

Ensure the poster URL stored in the ResultDict (and thus the database) is always resolution-free, so downstream consumers (notifications, disk save) start from a clean base.

**Files:**
- Modify: `src/movarr/imdb_metadata.py:365` — `_apply_metadata()` poster line
- Test: `tests/unit/test_imdb_metadata.py` — add to existing metadata tests

- [ ] **Step 1: Write test for poster resolution stripping**

Add to the appropriate class in `tests/unit/test_imdb_metadata.py` (find a relevant test class, e.g. `TestApplyMetadata`). The test needs `_apply_metadata` to be importable, or test through `fetch_metadata`. Let's test through the canonically-shaped dict:

```python
def test_poster_url_has_resolution_stripped(self) -> None:
    """_apply_metadata strips _SX resolution from poster URL."""
    from movarr.imdb_metadata import _apply_metadata

    result: ResultDict = {"imdb_id": "tt1375666", "result": "Passed", "result_details": []}
    data = {"poster": "https://m.media-amazon.com/images/M/MV5B._V1_SX300.jpg"}
    _apply_metadata(result, data)
    assert result["imdb_poster_url"] == "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"
```

Check the existing test file to find the right class to add this to:

```bash
cd /data/movarr && grep -n "class Test" tests/unit/test_imdb_metadata.py
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /data/movarr && uv run pytest tests/unit/test_imdb_metadata.py::<TestClass>::test_poster_url_has_resolution_stripped -v --tb=short
```

Expected: `FAILED` — poster URL is stored unchanged (still has `_SX300`)

- [ ] **Step 3: Modify `_apply_metadata` to strip resolution**

In `src/movarr/imdb_metadata.py`, line 365, change:

```python
            "imdb_poster_url": data.get("poster"),
```

to:

```python
            "imdb_poster_url": _strip_poster_resolution(data.get("poster")) if data.get("poster") else None,
```

And add the import at the top of `imdb_metadata.py`. The helper is in `notifications.py`:

```python
from movarr.notifications import _strip_poster_resolution
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /data/movarr && uv run pytest tests/unit/test_imdb_metadata.py -v --tb=short
```

Expected: All `imdb_metadata` tests pass

- [ ] **Step 5: Commit**

```bash
cd /data/movarr && git add src/movarr/imdb_metadata.py tests/unit/test_imdb_metadata.py
git commit -m "feat: strip poster URL resolution at metadata source"
```

---

## Task 3: Poster embed + IMDb link in notifications

**Files:**
- Modify: `src/movarr/notifications.py:81-103` — `send_queued_notification()`, `_build_body()`
- Test: `tests/unit/test_notifications.py`

- [ ] **Step 1: Write failing tests for poster embed and IMDb link**

Add to `TestSendQueuedNotification` class:

```python
def test_notify_called_with_attach_when_poster_embedded(self, mocker: MockerFixture) -> None:
    """Calls apprise.notify() with attach=poster_url when poster_embed_enabled=True."""
    cfg = Config()
    cfg.notification.apprise_urls = ["ntfy://topic"]
    cfg.notification.poster_embed_enabled = True
    cfg.notification.poster_embed_width = 500

    mock_instance = mocker.MagicMock()
    mock_instance.notify.return_value = True
    mocker.patch("movarr.notifications.apprise.Apprise", return_value=mock_instance)

    result = _make_full_result(imdb_poster_url="https://m.media-amazon.com/images/M/MV5B._V1_.jpg")
    send_queued_notification(result, cfg)

    _call_kwargs = mock_instance.notify.call_args[1]
    assert "attach" in _call_kwargs
    assert _call_kwargs["attach"] is not None

def test_notify_called_with_attach_none_when_disabled(self, mocker: MockerFixture) -> None:
    """Calls apprise.notify() with attach=None when poster_embed_enabled=False."""
    cfg = Config()
    cfg.notification.apprise_urls = ["ntfy://topic"]
    cfg.notification.poster_embed_enabled = False

    mock_instance = mocker.MagicMock()
    mock_instance.notify.return_value = True
    mocker.patch("movarr.notifications.apprise.Apprise", return_value=mock_instance)

    result = _make_full_result(imdb_poster_url="https://m.media-amazon.com/images/M/MV5B._V1_.jpg")
    send_queued_notification(result, cfg)

    _call_kwargs = mock_instance.notify.call_args[1]
    assert "attach" not in _call_kwargs or _call_kwargs["attach"] is None

def test_notify_called_with_attach_none_when_poster_url_none(self, mocker: MockerFixture) -> None:
    """Calls apprise.notify() without attach when imdb_poster_url is None."""
    cfg = Config()
    cfg.notification.apprise_urls = ["ntfy://topic"]
    cfg.notification.poster_embed_enabled = True

    mock_instance = mocker.MagicMock()
    mock_instance.notify.return_value = True
    mocker.patch("movarr.notifications.apprise.Apprise", return_value=mock_instance)

    result = _make_full_result(imdb_poster_url=None)
    send_queued_notification(result, cfg)

    _call_kwargs = mock_instance.notify.call_args[1]
    assert "attach" not in _call_kwargs or _call_kwargs["attach"] is None
```

Add to `TestBuildBody` class:

```python
def test_imdb_bare_url_present_when_id_set(self) -> None:
    """Body contains a bare IMDb URL line when imdb_id is present."""
    cfg = Config()
    result = _make_full_result(imdb_id="tt1375666")
    body = _build_body(result, cfg)
    assert "https://imdb.com/title/tt1375666" in body

def test_imdb_bare_url_absent_when_id_missing(self) -> None:
    """Body omits the IMDb bare URL line when imdb_id is missing."""
    cfg = Config()
    result = _make_full_result()
    result.pop("imdb_id", None)
    body = _build_body(result, cfg)
    assert "https://imdb.com/title/" not in body
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/movarr && uv run pytest tests/unit/test_notifications.py -v --tb=short
```

Expected: New tests FAIL (old tests pass)

- [ ] **Step 3: Modify `send_queued_notification` and `_build_body`**

In `send_queued_notification()` (line 81 of `notifications.py`), change the `_dispatch_apprise` call to pass `attach`:

```python
def send_queued_notification(result: ResultDict, config: Config) -> bool:
    urls = config.notification.apprise_urls
    if not urls:
        logger.debug("No apprise URLs configured; skipping notification.")
        return False

    body = _build_body(result, config)
    subject = _build_subject(result)

    # Build poster attachment if enabled
    attach: str | None = None
    if config.notification.poster_embed_enabled:
        poster = result.get("imdb_poster_url")
        if poster:
            attach = _poster_url_with_width(poster, config.notification.poster_embed_width)

    if not _dispatch_apprise(subject, body, list(urls), attach=attach):
        return False

    logger.info("Notification sent: {}", subject)
    return True
```

Update `_dispatch_apprise` to accept and forward `attach`:

```python
def _dispatch_apprise(subject: str, body: str, urls: list[str], attach: str | None = None) -> bool:
    if not urls or not subject or not body:
        return False
    ap = apprise.Apprise()
    for url in urls:
        ap.add(url)
    try:
        notify_kwargs: dict[str, object] = {
            "title": subject,
            "body": body,
            "body_format": apprise.NotifyFormat.HTML,
        }
        if attach is not None:
            notify_kwargs["attach"] = attach
        sent = ap.notify(**notify_kwargs)
    except Exception:  # noqa: BLE001
        logger.warning("Apprise notification failed.")
        return False
    if not sent:
        logger.warning("Apprise notification was not sent (no valid targets or all failed).")
        return False
    return True
```

In `_build_body()`, add the IMDb bare URL line after the Title line:

```python
def _build_body(result: ResultDict, config: Config) -> str:
    f = _extract_body_fields(result, config)
    imdb_line = ""
    if f["imdb_id"] and f["imdb_id"] != "#":
        imdb_line = f'<p><strong>IMDb:</strong> https://imdb.com/title/{html.escape(f["imdb_id"])}</p>\n'
    return f"""
<p><strong>Title:</strong> <a href="{f["imdb_url"]}">{f["title"]} ({f["year"]})</a> \u2014 {f["rating"]} from {f["votes"]} users</p>
{imdb_line}<p><strong>Plot:</strong> {f["plot"]}</p>
<p><strong>Actors:</strong> {f["actors_str"]}</p>
<p><strong>Directors:</strong> {f["directors_str"]}</p>
<p><strong>Genres:</strong> {f["genres_str"]}</p>
<p><strong>Queue Status:</strong> {f["queue_status"]}</p>
<p><strong>Release:</strong> <a href="{f["index_details"]}">{f["index_title"]}</a></p>
<p><strong>Size:</strong> {f["index_size_mb"]} MB</p>
<p><strong>Result Details:</strong></p>
{f["result_details_html"]}
"""
```

Also update the `_extract_imdb_fields` helper to return a safe `imdb_id` key we can check:

The existing `_extract_imdb_fields` already returns `imdb_id` and `imdb_url`. The `imdb_id` in the body fields dict comes from there. The guard `f["imdb_id"]` needs to check that it's a non-empty, non-`#` value. Since `_safe_url` returns `"#"` when the URL is invalid, and `_extract_imdb_fields` already uses `result.get("imdb_id") or ""` for `imdb_id`, the check `f["imdb_id"]` should be sufficient — but let me also check what `_extract_imdb_fields` does:

```python
def _extract_imdb_fields(result: ResultDict) -> dict[str, str]:
    title = html.escape(result.get("imdb_title") or "Unknown")
    year = html.escape(str(result.get("imdb_year") or ""))
    imdb_id = result.get("imdb_id") or ""
    ...
    return {"title": title, "year": year, "imdb_id": imdb_id, ...}
```

So `imdb_id` is empty string if not present. The check `if f["imdb_id"]` will correctly skip the line when the ID is missing. Good.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /data/movarr && uv run pytest tests/unit/test_notifications.py -v --tb=short
```

Expected: All tests pass

- [ ] **Step 5: Fix any edge case tests (like existing notifications tests that mock `_dispatch_apprise`)**

The existing tests mock `apprise.Apprise` directly, so they won't be affected by the `attach` parameter change. The `_dispatch_apprise` signature change (adding `attach`) is backward-compatible since it has a default of None.

- [ ] **Step 6: Commit**

```bash
cd /data/movarr && git add src/movarr/notifications.py tests/unit/test_notifications.py
git commit -m "feat: embed poster art in notifications + add IMDb plain-text link"
```

---

## Task 4: Config models and migration

**Files:**
- Modify: `src/movarr/config.py` — NotificationConfig + PostProcessConfig + migration table
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write failing tests for new config fields**

Add to the relevant test class in `tests/unit/test_config.py`. Find a suitable existing test, e.g. `TestNotificationConfigDefaults`:

```python
def test_poster_embed_enabled_defaults_to_true(self) -> None:
    """poster_embed_enabled defaults to True."""
    cfg = Config()
    assert cfg.notification.poster_embed_enabled is True

def test_poster_embed_width_defaults_to_500(self) -> None:
    """poster_embed_width defaults to 500."""
    cfg = Config()
    assert cfg.notification.poster_embed_width == 500

def test_poster_art_filename_defaults_to_empty(self) -> None:
    """poster_art.filename defaults to empty string (disabled)."""
    cfg = Config()
    assert cfg.post_process.poster_art.filename == ""

def test_poster_art_download_width_defaults_to_0(self) -> None:
    """poster_art.download_width defaults to 0 (largest available)."""
    cfg = Config()
    assert cfg.post_process.poster_art.download_width == 0
```

And a migration test (pattern follows existing tests like `test_v219_to_v220_adds_reject_genre_exclusive_list`):

```python
def test_v220_to_v221_adds_poster_fields(self, tmp_path: Path) -> None:
    """Migration to v2.21.0 adds poster_embed and poster_art defaults."""
    cfg_file = tmp_path / "movarr.yml"
    cfg_file.write_text(
        "general:\n"
        "  config_version: '2.20.0'\n"
        "notification:\n"
        "  apprise_urls: []\n"
        "post_process:\n"
        "  post_process_enabled: true\n"
    )
    with patch.object(config_module, "MIGRATIONS", config_module.MIGRATIONS):
        cfg = load_config(cfg_file)
    assert cfg.notification.poster_embed_enabled is True
    assert cfg.notification.poster_embed_width == 500
    assert cfg.post_process.poster_art.filename == ""
    assert cfg.post_process.poster_art.download_width == 0
```

Add the import at the top of the test file if needed (check existing imports first):

```bash
cd /data/movarr && grep -n "^import\|^from" tests/unit/test_config.py | head -10
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/movarr && uv run pytest tests/unit/test_config.py -v --tb=short -k "poster_embed or poster_art or v220_to_v221"
```

Expected: `FAILED` — field doesn't exist on models yet

- [ ] **Step 3: Add `PosterArtConfig` pydantic model**

In `src/movarr/config.py`, add a new model before `PostProcessConfig`:

```python
class PosterArtConfig(BaseModel):
    """Poster art download settings for post-processing.

    ``filename``: target filename (e.g. ``"poster.jpg"`` or ``"folder.jpg"``).
    Empty string disables poster download. Extension is forced to ``.jpg`` at
    save time (the user can omit or specify any extension).

    ``download_width``: pixel width for the poster. ``0`` means largest available.
    """

    filename: str = ""
    download_width: int = 0
```

Add the new field to `NotificationConfig`:

```python
class NotificationConfig(BaseModel):
    apprise_urls: list[str] = Field(default_factory=list)
    index_proxy_alert_hours: float = 0
    torrent_client_alert_hours: float = 0
    poster_embed_enabled: bool = True
    poster_embed_width: int = 500
```

Add the new field to `PostProcessConfig`:

```python
class PostProcessConfig(BaseModel):
    ...
    delete_lower_quality: bool = False
    hooks: PostProcessHooksConfig = Field(default_factory=PostProcessHooksConfig)
    poster_art: PosterArtConfig = Field(default_factory=PosterArtConfig)
```

Bump `_CONFIG_VERSION` on line 19:

```python
_CONFIG_VERSION = "2.20.0"
```

to:

```python
_CONFIG_VERSION = "2.21.0"
```

Add migration entry at the end of `_MIGRATION_TABLE` (before the closing `]`):

```python
    (
        "2.20.0",
        "2.21.0",
        [
            (("notification", "poster_embed_enabled"), True),
            (("notification", "poster_embed_width"), 500),
            (("post_process", "poster_art"), {"filename": "", "download_width": 0}),
        ],
    ),
```

Add the bound migration function after `_migrate_v219_to_v220 = _table_fns["2.19.0"]`:

```python
_migrate_v220_to_v221 = _table_fns["2.20.0"]
```

And register it in `MIGRATIONS` dict:

```python
MIGRATIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    ...
    "2.19.0": _migrate_v219_to_v220,
    "2.20.0": _migrate_v220_to_v221,
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /data/movarr && uv run pytest tests/unit/test_config.py -v --tb=short -k "poster_embed or poster_art or v220_to_v221"
```

Expected: All pass

- [ ] **Step 5: Check the `_table_fns` generation**

The `_table_fns` dict is built from `_MIGRATION_TABLE` entries. Since we added a `("2.20.0", "2.21.0", ...)` entry, `_table_fns["2.20.0"]` will exist automatically.

- [ ] **Step 6: Run full config test suite**

```bash
cd /data/movarr && uv run pytest tests/unit/test_config.py -v --tb=short
```

Expected: All pass

- [ ] **Step 7: Commit**

```bash
cd /data/movarr && git add src/movarr/config.py tests/unit/test_config.py
git commit -m "feat: add poster art config models and migration to v2.21.0"
```

---

## Task 5: Poster save to disk in post-processor

**Files:**
- Modify: `src/movarr/post_processor.py` — add `_save_poster_art`, wire into `_post_copy_actions`, add `db_record` param
- Test: `tests/unit/test_post_processor.py`

- [ ] **Step 1: Write failing tests for `_save_poster_art`**

Add a new test class at the end of `tests/unit/test_post_processor.py`:

```python
class TestSavePosterArt:
    """Tests for _save_poster_art — poster download + save to disk."""

    def test_skip_when_filename_blank(self, mocker: MockerFixture) -> None:
        """Does nothing when filename is blank."""
        cfg = Config()
        cfg.post_process.poster_art.filename = ""
        record = mocker.MagicMock(spec=HistoryRecord)
        urlopen = mocker.patch("movarr.post_processor.urllib.request.urlopen")
        _save_poster_art(record, "/tmp/dst", cfg)
        urlopen.assert_not_called()

    def test_skip_when_poster_url_none(self, mocker: MockerFixture) -> None:
        """Does nothing when poster URL is None."""
        cfg = Config()
        cfg.post_process.poster_art.filename = "poster.jpg"
        record = mocker.MagicMock(spec=HistoryRecord)
        record.imdb_poster_url = None
        urlopen = mocker.patch("movarr.post_processor.urllib.request.urlopen")
        _save_poster_art(record, "/tmp/dst", cfg)
        urlopen.assert_not_called()

    def test_downloads_and_writes_file(self, mocker: MockerFixture) -> None:
        """Downloads poster and writes to correct path."""
        cfg = Config()
        cfg.post_process.poster_art.filename = "poster.jpg"
        cfg.post_process.poster_art.download_width = 500

        record = mocker.MagicMock(spec=HistoryRecord)
        record.imdb_poster_url = "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"
        record.imdb_title = "Inception"

        mock_response = mocker.MagicMock()
        mock_response.headers = {"Content-Type": "image/jpeg"}
        mock_urlopen = mocker.patch(
            "movarr.post_processor.urllib.request.urlopen",
            return_value=mock_response,
        )
        mock_copyfileobj = mocker.patch("movarr.post_processor.shutil.copyfileobj")

        _save_poster_art(record, "/tmp/dst", cfg)

        mock_urlopen.assert_called_once()
        call_url = mock_urlopen.call_args[0][0]
        assert "_SX500" in str(call_url)
        mock_copyfileobj.assert_called_once_with(mock_response, mocker.ANY)

    def test_skips_on_non_image_content_type(self, mocker: MockerFixture) -> None:
        """Skips save when response Content-Type is not image/*."""
        cfg = Config()
        cfg.post_process.poster_art.filename = "poster.jpg"
        record = mocker.MagicMock(spec=HistoryRecord)
        record.imdb_poster_url = "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"

        mock_response = mocker.MagicMock()
        mock_response.headers = {"Content-Type": "text/html"}
        mocker.patch("movarr.post_processor.urllib.request.urlopen", return_value=mock_response)
        mock_copyfileobj = mocker.patch("movarr.post_processor.shutil.copyfileobj")

        _save_poster_art(record, "/tmp/dst", cfg)
        mock_copyfileobj.assert_not_called()

    def test_handles_download_failure_gracefully(self, mocker: MockerFixture) -> None:
        """Does not raise on network failure."""
        cfg = Config()
        cfg.post_process.poster_art.filename = "poster.jpg"
        record = mocker.MagicMock(spec=HistoryRecord)
        record.imdb_poster_url = "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"
        mocker.patch("movarr.post_processor.urllib.request.urlopen", side_effect=OSError("timeout"))

        # Should not raise
        _save_poster_art(record, "/tmp/dst", cfg)

    def test_forces_jpg_extension(self, mocker: MockerFixture) -> None:
        """Forces .jpg extension even when user specifies .png."""
        cfg = Config()
        cfg.post_process.poster_art.filename = "poster.png"
        cfg.post_process.poster_art.download_width = 0
        record = mocker.MagicMock(spec=HistoryRecord)
        record.imdb_poster_url = "https://m.media-amazon.com/images/M/MV5B._V1_.jpg"
        mock_response = mocker.MagicMock()
        mock_response.headers = {"Content-Type": "image/jpeg"}
        mocker.patch("movarr.post_processor.urllib.request.urlopen", return_value=mock_response)
        m_open = mocker.patch("builtins.open", mocker.mock_open())
        mocker.patch("movarr.post_processor.shutil.copyfileobj")

        _save_poster_art(record, "/tmp/dst", cfg)

        # Verify the file path ends in .jpg, not .png
        call_args = m_open.call_args
        assert call_args is not None
        file_path = call_args[0][0]
        assert file_path.endswith(".jpg")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/movarr && uv run pytest tests/unit/test_post_processor.py::TestSavePosterArt -v --tb=short
```

Expected: `FAILED` — `_save_poster_art` not importable

- [ ] **Step 3: Implement `_save_poster_art` and wiring**

Add imports at the top of `src/movarr/post_processor.py`:

```python
import os
import pathlib
import re
import shlex
import shutil  # shutil.copyfileobj
import signal
import subprocess
import urllib.request  # urlopen
from typing import TYPE_CHECKING
```

Add the function after `_post_copy_actions` (around line 290):

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
    from movarr.notifications import _poster_url_with_width  # noqa: PLC0415

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

Modify `_post_copy_actions` to accept `db_record` and call `_save_poster_art`:

Change the signature:
```python
def _post_copy_actions(
    config: Config,
    tag: str,
    db: Database,
    qbt: QBittorrentClient,
    torrent_hash: str,
    dst_dir: str,
    dst_base: str,
    resolved_dst_dir: str,
    canonical_fname: str,
    copied_fnames: set[str],
    torrent_name: str = "",
    db_record: HistoryRecord | None = None,  # NEW
) -> None:
```

Add the poster save call before the `delete_lower_quality` check (after `db.mark_completed` and `post_copy` hook):

```python
def _post_copy_actions(
    config: Config,
    tag: str,
    db: Database,
    qbt: QBittorrentClient,
    torrent_hash: str,
    dst_dir: str,
    dst_base: str,
    resolved_dst_dir: str,
    canonical_fname: str,
    copied_fnames: set[str],
    torrent_name: str = "",
    db_record: HistoryRecord | None = None,
) -> None:
    """Mark the torrent completed and run any configured post-copy operations."""
    db.mark_completed(tag)
    logger.info("Marked tag '{}' as completed.", tag)
    if config.post_process.hooks.post_copy:
        ...
    # Save poster art
    if db_record and config.post_process.poster_art.filename:
        _save_poster_art(db_record, dst_dir, config)
    if config.post_process.delete_lower_quality and canonical_fname in copied_fnames:
        ...
```

Update the call site in `_process_one` (around line 370) to pass `db_record=db_record`:

```python
    if all_ok:
        _post_copy_actions(
            config,
            tag,
            db,
            qbt,
            torrent_hash,
            dst_dir,
            dst_base,
            resolved_dst_dir,
            canonical_fname,
            copied_fnames,
            torrent_name=str(db_record.index_title or ""),
            db_record=db_record,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /data/movarr && uv run pytest tests/unit/test_post_processor.py -v --tb=short
```

Expected: All tests pass (existing tests should be unaffected since `db_record` param is optional with `None` default)

- [ ] **Step 5: Commit**

```bash
cd /data/movarr && git add src/movarr/post_processor.py tests/unit/test_post_processor.py
git commit -m "feat: save poster art to disk after verified post-processing copy"
```

---

## Task 6: Full suite verification and final commit

- [ ] **Step 1: Run ruff check + format**

```bash
cd /data/movarr && uv run ruff check --fix . && uv run ruff format .
```

Expected: No errors

- [ ] **Step 2: Run mypy**

```bash
cd /data/movarr && uv run mypy .
```

Expected: No type errors

- [ ] **Step 3: Run full pytest suite with coverage**

```bash
cd /data/movarr && uv run pytest tests/ --tb=short -q --cov=src/movarr --cov-report=term-missing
```

Expected: All tests pass, 100% coverage

- [ ] **Step 4: Run pre-commit**

```bash
cd /data/movarr && uv run pre-commit run --all-files
```

Expected: All hooks pass

- [ ] **Step 5: Final commit (bump version marker)**

The `_CONFIG_VERSION` was already bumped to `"2.21.0"` in Task 4. All feature commits are done. No additional commit needed unless the full suite revealed issues.

```bash
cd /data/movarr && git status
```

Expected: Clean working tree (all changes committed across Tasks 1-5)
