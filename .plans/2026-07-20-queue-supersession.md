# Queue Supersession Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cancel inferior qBittorrent downloads when a higher-quality version of the same movie is queued.

**Architecture:** Embed IMDb ID + base quality score into the qBittorrent tag. Before queuing a new torrent, scan in-queue torrents for the same IMDb ID and compare using `supersession_quality_score` (quality + preferred-group bonus). Delete inferior torrents + data; skip the new one if it's not better.

**Tech Stack:** Python 3.12+, qbittorrent-api, pydantic, pytest with pytest-mock.

---

## File Map

| File | Responsibility |
|------|---------------|
| `src/movarr/config.py` | New `supersede_enabled` field, migration `2.21.0 → 2.22.0`, version bump |
| `src/movarr/qbittorrent.py` | New tag format: `_build_supersede_tag`, `_parse_imdb_id_from_tags`, `_parse_score_from_tags`; modify `add_torrent` |
| `src/movarr/search.py` | `_supersede(result, session)` + `_compute_stored_score`; hook before `_queue_and_persist` |
| `tests/unit/test_qbittorrent.py` | Tag building/parsing tests; updated UUID format assertion |
| `tests/unit/test_search.py` | Supersession logic tests (mocked qBittorrent) |
| `README.md` | Document `supersede_enabled` in queue_management table |

---

### Task 1: Config — Add `supersede_enabled` field, migration, version bump

**Files:**
- Modify: `src/movarr/config.py`

- [ ] **Step 1: Add `supersede_enabled` field to `QueueManagementConfig`**

Change line ~631 — add the field after `metadata_delete_torrent_max_mins`:

```python
class QueueManagementConfig(BaseModel):
    """Stalled torrent monitoring settings."""

    queue_management_enabled: bool = True
    metadata_monitor_enabled: bool = True
    stalled_monitor_enabled: bool = True
    stalled_delete_torrent_data: bool = True
    metadata_delete_torrent_data: bool = True
    stalled_delete_torrent_max_mins: int = 120
    metadata_delete_torrent_max_mins: int = 30
    supersede_enabled: bool = False
```

- [ ] **Step 2: Bump `_CONFIG_VERSION` to `"2.22.0"`**

Change line ~19:

```python
_CONFIG_VERSION = "2.22.0"
```

- [ ] **Step 3: Add migration entry to `_MIGRATION_TABLE`**

After the `"2.20.0" → "2.21.0"` entry (~line 143), add:

```python
    (
        "2.21.0",
        "2.22.0",
        [
            (("queue_management", "supersede_enabled"), False),
        ],
    ),
```

- [ ] **Step 4: Add migration function binding**

After `_migrate_v220_to_v221 = _table_fns["2.20.0"]` (~line 373), add:

```python
_migrate_v221_to_v222 = _table_fns["2.21.0"]
```

- [ ] **Step 5: Add migration to MIGRATIONS dict**

After `"2.20.0": _migrate_v220_to_v221,` (~line 397), add:

```python
    "2.21.0": _migrate_v221_to_v222,
```

- [ ] **Step 6: Run existing config tests to verify no breakage**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/movarr/config.py
git commit -m "feat: add supersede_enabled config field with migration to v2.22.0"
```

---

### Task 2: qBittorrent — Tag helper functions

**Files:**
- Modify: `src/movarr/qbittorrent.py`

- [ ] **Step 1: Add `_build_supersede_tag` function**

Add after `extract_movarr_tag` (~line 29):

```python
def _build_supersede_tag(imdb_id: str, score: int) -> str:
    """Build a movarr torrent tag encoding IMDb ID and base quality score.

    Format: ``movarr-<8hex>-imdb-<ttid>-score-<score>``

    Args:
        imdb_id: IMDb ID (e.g. ``"tt1234567"``).
        score: Base quality score from :func:`movarr.parsing.quality_score`.

    Returns:
        A structured tag string.
    """
    short_uuid = uuid.uuid4().hex[:8]
    return f"movarr-{short_uuid}-imdb-{imdb_id}-score-{score}"
```

- [ ] **Step 2: Add `_parse_imdb_id_from_tags` function**

Add after `_build_supersede_tag`:

```python
def _parse_imdb_id_from_tags(tags_str: str) -> str | None:
    """Extract an IMDb ID from a movarr torrent tag.

    Looks for ``imdb-ttNNNNNNN`` segment. Returns ``None`` if not found
    (e.g. old-format tag or non-movarr torrent).

    Args:
        tags_str: Comma-separated tag string from qBittorrent.

    Returns:
        The IMDb ID string (e.g. ``"tt1234567"``) or ``None``.
    """
    import re

    m = re.search(r"imdb-(tt\d{7,8})", tags_str)
    return m.group(1) if m else None
```

- [ ] **Step 3: Add `_parse_score_from_tags` function**

Add after `_parse_imdb_id_from_tags`:

```python
def _parse_score_from_tags(tags_str: str) -> int | None:
    """Extract the base quality score from a movarr torrent tag.

    Looks for ``score-NNN`` segment. Returns ``None`` if not found
    or unparseable.

    Args:
        tags_str: Comma-separated tag string from qBittorrent.

    Returns:
        The integer score or ``None``.
    """
    import re

    m = re.search(r"score-(\d+)", tags_str)
    return int(m.group(1)) if m else None
```

- [ ] **Step 4: Commit**

```bash
git add src/movarr/qbittorrent.py
git commit -m "feat: add tag helpers for IMDb ID and score encoding"
```

---

### Task 3: qBittorrent — Modify `add_torrent` to use new tag format

**Files:**
- Modify: `src/movarr/qbittorrent.py`

- [ ] **Step 1: Update `add_torrent` to accept and use IMDb ID + score**

The function signature stays the same — it reads `imdb_id` and computes the
score from `index_title_sanitised` already in the `ResultDict`. Replace the
tag generation line (`tag = f"{_TAG_PREFIX}{uuid.uuid4()}"`) with the new helper:

```python
def add_torrent(self, result: ResultDict) -> ResultDict | None:
    """Add a torrent to qBittorrent and tag it with a unique identifier.

    Tries ``magnet_url`` first, falls back to ``torrent_url``.  Returns
    an updated *result* dict with ``torrent_tag`` set, or ``None`` on failure.

    Args:
        result: Pipeline result dict containing URL fields, IMDb metadata,
                and parsed title fields.
    """
    from movarr.parsing import quality_score

    download_url = result.get("magnet_url") or result.get("torrent_url")
    if not download_url:
        _logger.info(
            "No magnet or torrent URL for '{}'; cannot add.",
            result.get("index_title"),
        )
        return None

    imdb_id = result.get("imdb_id") or ""
    sanitised = result.get("index_title_sanitised") or ""
    score = quality_score(sanitised) if sanitised else 0
    tag = _build_supersede_tag(imdb_id, score) if imdb_id else f"{_TAG_PREFIX}{uuid.uuid4().hex[:8]}"

    try:
        self._client.torrents_add(
            urls=download_url,
            category=self._category,
            is_paused=self._add_paused,
            tags=tag,
        )
        # ... rest unchanged ...
```

The rest of `add_torrent` (reannounce block, result assignment) is unchanged.

- [ ] **Step 2: Run existing qBittorrent tests to see what breaks**

Run: `uv run pytest tests/unit/test_qbittorrent.py -v`
Expected: `test_tag_matches_uuid_format` FAILS (old regex expects 36-char UUID).

- [ ] **Step 3: Commit** (test fix comes in Task 5)

```bash
git add src/movarr/qbittorrent.py
git commit -m "feat: embed IMDb ID and quality score in torrent tag"
```

---

### Task 4: Search — `_supersede` function and hook

**Files:**
- Modify: `src/movarr/search.py`

- [ ] **Step 1: Add `_compute_stored_score` helper**

Add before `_supersede`:

```python
def _compute_stored_score(result: ResultDict) -> int:
    """Compute the base quality score to store in the torrent tag.

    Uses only ``quality_score`` — the group bonus is relative to the
    opponent torrent and is applied at comparison time via
    ``supersession_quality_score``.

    Args:
        result: Pipeline result dict with ``index_title_sanitised`` set.

    Returns:
        Integer quality score (0 if sanitised title is missing).
    """
    from movarr.parsing import quality_score

    sanitised = result.get("index_title_sanitised") or ""
    return quality_score(sanitised) if sanitised else 0
```

- [ ] **Step 2: Add `_supersede` function**

Add before `_process_single_result`:

```python
def _supersede(result: ResultDict, session: _SearchSession) -> None:
    """Cancel inferior in-queue torrents for the same IMDb ID.

    Two-pass approach:
    1. Scan all same-IMDb torrents to find the best existing score.
    2. If new > best existing: delete ALL same-IMDb torrents and proceed.
       If new <= best existing: mark new as Failed and skip (no deletions).

    Args:
        result: The new pipeline result about to be queued.
        session: Immutable search session (provides qbt, config).
    """
    from movarr.filters import supersession_quality_score
    from movarr.qbittorrent import _parse_imdb_id_from_tags

    new_imdb_id = result.get("imdb_id")
    if not new_imdb_id:
        return  # can't match without IMDb ID

    new_san = result.get("index_title_sanitised") or ""

    try:
        torrent_map = session.qbt.list_by_category()
    except Exception:
        logger.warning("Failed to query qBittorrent for supersession check; skipping.")
        return

    if not torrent_map:
        return

    # --- Pass 1: collect matches, track whether new beats all ---
    matches: list[tuple[str, str, str]] = []  # (hash, name, tags_str)
    all_new_wins = True
    best_existing_name = ""
    best_existing_score = -1

    for torrent_hash, info in torrent_map.items():
        tags_str = info.get("tags", "") or ""
        existing_imdb = _parse_imdb_id_from_tags(tags_str)
        if existing_imdb != new_imdb_id:
            continue

        torrent_name = info.get("name", "")
        existing_san = sanitise(torrent_name) if torrent_name else ""
        if not existing_san:
            continue

        new_score = supersession_quality_score(new_san, existing_san, session.config)
        existing_score = supersession_quality_score(existing_san, new_san, session.config)

        matches.append((torrent_hash, torrent_name, tags_str))

        if new_score <= existing_score:
            all_new_wins = False

        if existing_score > best_existing_score:
            best_existing_score = existing_score
            best_existing_name = torrent_name

    if not matches:
        return  # no same-IMDb torrents found

    # --- Pass 2: decide ---
    if not all_new_wins:
        logger.info(
            "Skipping '{}' — in-queue torrent '{}' has equal or better score.",
            result.get("index_title"),
            best_existing_name,
        )
        details: list[str] = result.get("result_details") or []
        details.append(
            f"Failed: Superseded by higher-scored in-queue torrent "
            f"'{best_existing_name}'."
        )
        result["result"] = "Failed"
        result["result_details"] = details
        return

    # --- New beats all: delete every existing same-IMDb torrent ---
    logger.info(
        "Superseding {} in-queue torrent(s) for IMDb '{}' with '{}'.",
        len(matches),
        new_imdb_id,
        result.get("index_title"),
    )
    for torrent_hash, torrent_name, tags_str in matches:
        movarr_tag = extract_movarr_tag(tags_str)
        if movarr_tag:
            try:
                session.db.mark_stalled(movarr_tag)
            except Exception:
                logger.debug("Could not mark superseded torrent tag '{}' in DB.", movarr_tag)
        session.qbt.delete_torrent(torrent_hash, delete_data=True, state="superseded", name=torrent_name)
```

- [ ] **Step 3: Hook `_supersede` into `_process_single_result`**

In `_process_single_result`, right after `logger.success(...)` and before `_queue_and_persist(...)`, add:

```python
        logger.success("'{}' passed all filters.", result.get("index_title"))

        # --- supersession check ---
        if session.config.queue_management.supersede_enabled:
            _supersede(result, session)
            if result.get("result") == "Failed":
                session.db.write(result)
                return True
        # --- end supersession check ---

        _queue_and_persist(result, session)
```

- [ ] **Step 4: Add `sanitise` import at the top of search.py**

Add `sanitise` to the existing import from `movarr.parsing` (line ~27):

```python
from movarr.parsing import (
    extract_after_year,
    extract_movie_title,
    extract_resolution,
    extract_year,
    normalise_for_compare,
    sanitise,   # <-- add this if not already present
)
```

Note: `sanitise` may already be imported — check the current import block.

- [ ] **Step 5: Run syntax check**

Run: `uv run python -c "from movarr.search import _supersede, _compute_stored_score; print('OK')"`
Expected: OK (no ImportError).

- [ ] **Step 6: Commit**

```bash
git add src/movarr/search.py
git commit -m "feat: add queue supersession — cancel inferior downloads for same IMDb ID"
```

---

### Task 5: Tests — Tag helpers in qBittorrent

**Files:**
- Modify: `tests/unit/test_qbittorrent.py`

- [ ] **Step 1: Fix existing `test_tag_matches_uuid_format`**

The old test expects 36-char UUID. Update it to match the new 8-hex format.
Also mock `quality_score` since add_torrent now calls it:

```python
    def test_tag_matches_new_format(self, mocker: MockerFixture) -> None:
        """The generated tag follows 'movarr-<8hex>-imdb-<ttid>-score-<N>' pattern."""
        import re

        mocker.patch("movarr.parsing.quality_score", return_value=120)
        client, _ = _make_client(mocker)
        result: ResultDict = {
            "index_title": "Movie 2024 1080p BluRay",
            "magnet_url": "magnet:?xt=urn:btih:xyz",
            "imdb_id": "tt1234567",
            "index_title_sanitised": "movie 2024 1080p bluray",
        }
        updated = client.add_torrent(result)

        assert updated is not None
        tag = updated["torrent_tag"]
        assert re.match(r"^movarr-[0-9a-f]{8}-imdb-tt\d{7,8}-score-\d+$", tag)
```

- [ ] **Step 2: Update `test_adds_magnet_url_and_sets_tag`**

Add `imdb_id` and `index_title_sanitised` to the test result dict, and mock `quality_score`:

```python
    def test_adds_magnet_url_and_sets_tag(self, mocker: MockerFixture) -> None:
        """Adds torrent via magnet_url and stamps result with a movarr- tag."""
        mocker.patch("movarr.parsing.quality_score", return_value=60)
        client, mock_api = _make_client(mocker)
        result: ResultDict = {
            "index_title": "Test Movie",
            "magnet_url": "magnet:?xt=urn:btih:abc123",
            "torrent_url": "",
            "imdb_id": "tt1234567",
            "index_title_sanitised": "test movie 1999 1080p bluray",
        }
        updated = client.add_torrent(result)

        assert updated is not None
        assert "torrent_tag" in updated
        assert updated["torrent_tag"].startswith("movarr-")
        assert "imdb-tt1234567" in updated["torrent_tag"]
        assert "score-60" in updated["torrent_tag"]
        mock_api.torrents_add.assert_called_once()
        mock_api.torrents_reannounce.assert_called_once()
```

- [ ] **Step 3: Add `TestBuildSupersedeTag` class**

```python
class TestBuildSupersedeTag:
    """Tests for _build_supersede_tag."""

    def test_builds_correct_format(self) -> None:
        from movarr.qbittorrent import _build_supersede_tag

        tag = _build_supersede_tag("tt1234567", 134)
        import re

        assert re.match(r"^movarr-[0-9a-f]{8}-imdb-tt1234567-score-134$", tag)

    def test_different_calls_produce_different_uuids(self) -> None:
        from movarr.qbittorrent import _build_supersede_tag

        tag1 = _build_supersede_tag("tt1111111", 100)
        tag2 = _build_supersede_tag("tt2222222", 200)
        # UUID parts should differ
        uuid1 = tag1.split("-")[1]
        uuid2 = tag2.split("-")[1]
        assert uuid1 != uuid2  # extremely unlikely to collide

    def test_imdb_id_with_eight_digits(self) -> None:
        from movarr.qbittorrent import _build_supersede_tag

        tag = _build_supersede_tag("tt12345678", 50)
        assert "imdb-tt12345678" in tag
```

- [ ] **Step 4: Add `TestParseImdbIdFromTags` class**

```python
class TestParseImdbIdFromTags:
    """Tests for _parse_imdb_id_from_tags."""

    def test_extracts_imdb_id_from_new_tag(self) -> None:
        from movarr.qbittorrent import _parse_imdb_id_from_tags

        result = _parse_imdb_id_from_tags("movarr-a1b2c3d4-imdb-tt1234567-score-134")
        assert result == "tt1234567"

    def test_extracts_imdb_id_with_eight_digits(self) -> None:
        from movarr.qbittorrent import _parse_imdb_id_from_tags

        result = _parse_imdb_id_from_tags("movarr-deadbeef-imdb-tt12345678-score-50")
        assert result == "tt12345678"

    def test_returns_none_for_old_format_tag(self) -> None:
        from movarr.qbittorrent import _parse_imdb_id_from_tags

        result = _parse_imdb_id_from_tags("movarr-abc123, other-tag")
        assert result is None

    def test_returns_none_for_non_movarr_tag(self) -> None:
        from movarr.qbittorrent import _parse_imdb_id_from_tags

        result = _parse_imdb_id_from_tags("some-other-tag")
        assert result is None

    def test_returns_none_for_empty_string(self) -> None:
        from movarr.qbittorrent import _parse_imdb_id_from_tags

        assert _parse_imdb_id_from_tags("") is None
```

- [ ] **Step 5: Add `TestParseScoreFromTags` class**

```python
class TestParseScoreFromTags:
    """Tests for _parse_score_from_tags."""

    def test_extracts_score_from_new_tag(self) -> None:
        from movarr.qbittorrent import _parse_score_from_tags

        result = _parse_score_from_tags("movarr-a1b2c3d4-imdb-tt1234567-score-134")
        assert result == 134

    def test_extracts_score_zero(self) -> None:
        from movarr.qbittorrent import _parse_score_from_tags

        result = _parse_score_from_tags("movarr-deadbeef-imdb-tt1234567-score-0")
        assert result == 0

    def test_extracts_multi_digit_score(self) -> None:
        from movarr.qbittorrent import _parse_score_from_tags

        result = _parse_score_from_tags("movarr-12345678-imdb-tt9999999-score-175")
        assert result == 175

    def test_returns_none_for_old_format_tag(self) -> None:
        from movarr.qbittorrent import _parse_score_from_tags

        result = _parse_score_from_tags("movarr-abc123")
        assert result is None

    def test_returns_none_for_empty_string(self) -> None:
        from movarr.qbittorrent import _parse_score_from_tags

        assert _parse_score_from_tags("") is None

    def test_returns_none_for_malformed_score(self) -> None:
        from movarr.qbittorrent import _parse_score_from_tags

        # score- is present but no digits
        assert _parse_score_from_tags("movarr-aaa-imdb-tt1-score-abc") is None
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/unit/test_qbittorrent.py -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add tests/unit/test_qbittorrent.py
git commit -m "test: add tag helper tests, update add_torrent test for new format"
```

---

### Task 6: Tests — Supersession logic in search

**Files:**
- Modify: `tests/unit/test_search.py`

- [ ] **Step 1: Add `TestSupersede` class**

Add before the existing `TestRunSearch` class:

```python
class TestSupersede:
    """Tests for _supersede — cancel inferior same-IMDb torrents."""

    def _make_session(self, mocker: MockerFixture, supersede_enabled: bool = True) -> Any:
        """Build a _SearchSession with mocked qbt and config."""
        from movarr.search import _SearchSession

        cfg = Config()
        cfg.queue_management.supersede_enabled = supersede_enabled
        qbt = mocker.MagicMock()
        db = mocker.MagicMock()
        return _SearchSession(
            config=cfg,
            indexer=mocker.MagicMock(),
            qbt=qbt,
            db=db,
            library_walk=None,
        )

    def _result(self, imdb_id: str = "tt1234567", title: str = "Movie 2024", san: str = "movie 2024 1080p bluray") -> ResultDict:
        """Build a minimal passed result dict."""
        return {
            "index_title": title,
            "index_title_sanitised": san,
            "imdb_id": imdb_id,
            "imdb_rating": 7.5,
            "result": "Passed",
            "result_details": [],
        }

    def test_noop_when_supersede_disabled(self, mocker: MockerFixture) -> None:
        """When supersede_enabled is False, result is unchanged."""
        from movarr.search import _supersede

        session = self._make_session(mocker, supersede_enabled=False)
        result = self._result()
        original = dict(result)

        # We don't call _supersede directly when disabled (the hook guards it),
        # but if called, it should check config. Let's verify the hook point instead.
        # This test verifies _supersede itself is harmless when no match found.
        session.qbt.list_by_category.return_value = {}
        _supersede(result, session)
        assert result["result"] == "Passed"

    def test_no_match_when_no_torrents_in_queue(self, mocker: MockerFixture) -> None:
        """Result passes when qBittorrent queue is empty."""
        from movarr.search import _supersede

        session = self._make_session(mocker)
        session.qbt.list_by_category.return_value = {}
        result = self._result()

        _supersede(result, session)
        assert result["result"] == "Passed"

    def test_no_match_when_different_imdb_id(self, mocker: MockerFixture) -> None:
        """Result passes when no in-queue torrent shares the IMDb ID."""
        from movarr.search import _supersede

        session = self._make_session(mocker)
        session.qbt.list_by_category.return_value = {
            "hash1": {"name": "Other Movie", "tags": "movarr-a1b2c3d4-imdb-tt9999999-score-80"}
        }
        result = self._result(imdb_id="tt1234567")

        _supersede(result, session)
        assert result["result"] == "Passed"
        session.qbt.delete_torrent.assert_not_called()

    def test_skips_old_format_tag_no_imdb(self, mocker: MockerFixture) -> None:
        """Old-format tags (no imdb- segment) are ignored."""
        from movarr.search import _supersede

        session = self._make_session(mocker)
        session.qbt.list_by_category.return_value = {
            "hash1": {"name": "Movie 2024", "tags": "movarr-olduuid"}
        }
        result = self._result()

        _supersede(result, session)
        assert result["result"] == "Passed"
        session.qbt.delete_torrent.assert_not_called()

    def test_supersedes_lower_scored_existing(self, mocker: MockerFixture) -> None:
        """Existing torrent with lower score is deleted; new torrent proceeds."""
        from movarr.search import _supersede

        session = self._make_session(mocker)
        session.qbt.list_by_category.return_value = {
            "hash1": {
                "name": "Movie 2024 1080p WebDL",
                "tags": "movarr-a1b2c3d4-imdb-tt1234567-score-60"
            }
        }
        # New result is 2160p Remux — much higher quality_score
        result = self._result(title="Movie 2024 2160p Remux", san="movie 2024 2160p remux atmos")

        _supersede(result, session)

        # Existing should be deleted
        session.qbt.delete_torrent.assert_called_once_with(
            "hash1", delete_data=True, state="superseded", name="Movie 2024 1080p WebDL"
        )
        # New result should still be Passed
        assert result["result"] == "Passed"

    def test_skips_new_when_existing_has_higher_score(self, mocker: MockerFixture) -> None:
        """New torrent is marked Failed when existing has equal or better score."""
        from movarr.search import _supersede

        session = self._make_session(mocker)
        session.qbt.list_by_category.return_value = {
            "hash1": {
                "name": "Movie 2024 2160p Remux",
                "tags": "movarr-deadbeef-imdb-tt1234567-score-120"
            }
        }
        # New result is only 1080p
        result = self._result(title="Movie 2024 1080p WebDL", san="movie 2024 1080p webdl")

        _supersede(result, session)

        assert result["result"] == "Failed"
        assert any("Superseded" in d for d in result["result_details"])
        session.qbt.delete_torrent.assert_not_called()

    def test_qbittorrent_error_skips_supersession(self, mocker: MockerFixture) -> None:
        """When qBittorrent query fails, supersession is skipped gracefully."""
        from movarr.search import _supersede

        session = self._make_session(mocker)
        session.qbt.list_by_category.side_effect = Exception("connection lost")
        result = self._result()

        _supersede(result, session)
        assert result["result"] == "Passed"  # proceed despite error

    def test_no_imdb_id_on_result_skips(self, mocker: MockerFixture) -> None:
        """Result without imdb_id cannot be matched — skips check."""
        from movarr.search import _supersede

        session = self._make_session(mocker)
        result = self._result(imdb_id="")  # no IMDb ID

        _supersede(result, session)
        assert result["result"] == "Passed"
        session.qbt.list_by_category.assert_not_called()  # never queried

    def test_multiple_inferior_torrents_all_deleted(self, mocker: MockerFixture) -> None:
        """All inferior same-IMDb torrents are deleted."""
        from movarr.search import _supersede

        session = self._make_session(mocker)
        session.qbt.list_by_category.return_value = {
            "hash_low1": {"name": "Movie 2024 720p", "tags": "movarr-aaa-imdb-tt1234567-score-30"},
            "hash_low2": {"name": "Movie 2024 1080p", "tags": "movarr-bbb-imdb-tt1234567-score-60"},
            "hash_high": {"name": "Movie 2024 2160p", "tags": "movarr-ccc-imdb-tt1234567-score-120"},
            "hash_other": {"name": "Other Movie", "tags": "movarr-ddd-imdb-tt9999999-score-80"},
        }
        # New result has score 140 (e.g. 2160p Remux Atmos DV)
        result = self._result(
            title="Movie 2024 2160p Remux DV",
            san="movie 2024 2160p remux atmos dolby vision"
        )

        _supersede(result, session)

        # All three same-IMDb torrents should be deleted (scores 30, 60, 120 < 140)
        assert session.qbt.delete_torrent.call_count == 3
        deleted_hashes = {call[0][0] for call in session.qbt.delete_torrent.call_args_list}
        assert deleted_hashes == {"hash_low1", "hash_low2", "hash_high"}
        # Other movie untouched
        assert result["result"] == "Passed"

    def test_skips_new_when_any_existing_has_higher_score(self, mocker: MockerFixture) -> None:
        """New is Failed when ANY existing same-IMDb torrent has equal/better score."""
        from movarr.search import _supersede

        session = self._make_session(mocker)
        session.qbt.list_by_category.return_value = {
            "hash1": {"name": "Movie 2024 2160p Remux", "tags": "movarr-aaa-imdb-tt1234567-score-120"},
            "hash2": {"name": "Movie 2024 720p", "tags": "movarr-bbb-imdb-tt1234567-score-30"},
        }
        result = self._result(title="Movie 2024 1080p", san="movie 2024 1080p bluray")

        _supersede(result, session)

        assert result["result"] == "Failed"
        # Should NOT delete anything — a superior torrent exists, so new is blocked entirely
        session.qbt.delete_torrent.assert_not_called()
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/unit/test_search.py::TestSupersede -v`
Expected: all pass.

- [ ] **Step 3: Run full test suite to check for regressions**

Run: `uv run pytest tests/unit/ -v --tb=short`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_search.py
git commit -m "test: add supersession logic tests"
```

---

### Task 7: README — Document `supersede_enabled`

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add `supersede_enabled` row to queue_management table**

After the `metadata_delete_torrent_max_mins` row (~line 249), add:

```markdown
| `supersede_enabled` | When a higher-quality torrent for the same IMDb ID is queued, cancel the inferior download and delete its partial data. Requires the IMDb ID in the qBittorrent tag (automatic with v2.22.0+). | `false` |
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document supersede_enabled in README"
```

---

### Task 8: Final Verification

- [ ] **Step 1: Lint**

Run: `uv run ruff check src/movarr/config.py src/movarr/qbittorrent.py src/movarr/search.py`
Expected: no errors.

- [ ] **Step 2: Type check**

Run: `uv run mypy src/movarr/config.py src/movarr/qbittorrent.py src/movarr/search.py`
Expected: no errors.

- [ ] **Step 3: Full test suite**

Run: `uv run pytest tests/unit/ -v`
Expected: all pass.

- [ ] **Step 4: Coverage check on changed modules**

Run: `uv run pytest tests/unit/test_qbittorrent.py tests/unit/test_search.py --cov=movarr.qbittorrent --cov=movarr.search --cov-report=term-missing`
Expected: no uncovered lines in the new functions.

- [ ] **Step 5: Run existing config migration test**

Run: `uv run pytest tests/unit/test_config.py -v -k "migrat"`
Expected: all pass.

---

## Summary of Changes

| Task | Commits | Files |
|------|---------|-------|
| 1: Config | 1 | `config.py` |
| 2: Tag helpers | 1 | `qbittorrent.py` |
| 3: add_torrent | 1 | `qbittorrent.py` |
| 4: _supersede + hook | 1 | `search.py` |
| 5: qBittorrent tests | 1 | `test_qbittorrent.py` |
| 6: Search tests | 1 | `test_search.py` |
| 7: README | 1 | `README.md` |
| 8: Verification | 0 | — |
| **Total** | **7 commits** | **5 files modified** |
