# Queue Supersession — Cancel Inferior Downloads When a Better Version Is Queued

**Date:** 2026-07-20
**Status:** Design — awaiting implementation

## Motivation

When movarr's acquisition pipeline finds a higher-quality release for a movie it is already
downloading, the inferior download should be canceled and its partial data deleted. Without
this, multiple redundant downloads for the same movie accumulate in qBittorrent (e.g. six
concurrent downloads for *Backrooms 2026* at various quality tiers), wasting bandwidth and
disk space.

## Design

### Trigger point

The supersession check runs **before `_queue_and_persist`** in `search.py`. Only when
`queue_management.supersede_enabled` is `true` — default `false` (opt-in).

### Tag format

Torrents are tagged in qBittorrent with a structured tag encoding IMDb ID and quality score:

```
movarr-a1b2c3d4-imdb-tt1234567-score-134
```

| Segment | Content | Purpose |
|---------|---------|---------|
| `movarr-a1b2c3d4` | Short UUID (first 8 hex chars of `uuid4`) | Uniqueness within qBittorrent; existing `extract_movarr_tag()` still works |
| `imdb-tt1234567` | IMDb ID without `tt` prefix | Same-movie matching |
| `score-134` | Composite quality score (see below) | Comparison — higher score wins |

### Stored score (in tag)

The tag stores only the **base quality score**:

```
stored_score = quality_score(index_title_sanitised)
```

- **`quality_score`** (from `parsing.py`): resolution + source type + audio codec + HDR format. Range ~10–175.

### Comparison score (at runtime)

When comparing a new torrent against an existing one, use `supersession_quality_score`
(from `filters.py`), which adds the preferred-group bonus *relative* to the opponent torrent.
This bonus is contextual (depends on the other torrent's group), so it cannot be pre-baked into
the tag — it's computed at comparison time.

```
comparison_score = supersession_quality_score(this_san, other_san, config)
```

If `comparison_score > 0`, this torrent is better than the other.

IMDb rating is deliberately excluded — it serves as a filter gate (`minimum_rating`), not a quality
ranker. Two torrents for the same IMDb ID are compared purely on audio/video quality. This mirrors
how `post_processor`'s `delete_lower_quality` already works.

### Logic

When `supersede_enabled` is true and a new result passes all filters:

1. **Query qBittorrent** for all torrents in the movarr category.
2. **Parse tags** — extract IMDb ID and score from each torrent's tags.
3. **Find matches** — any torrent with the same IMDb ID as the new result.
4. **Compare scores**:
   - If **no match exists**: proceed normally — queue the new torrent.
   - Compute `supersession_quality_score(new_san, existing_san, config)` for comparison.
   - If **new wins** (comparison_score > 0): delete the existing torrent + data, queue the new one. Log
     the superseded torrent as "Failed: Superseded by higher-scored queued torrent".
   - If **existing wins or tie**: skip the new torrent. Log it as "Failed: Superseded by
     higher-scored in-queue torrent". Do not queue.
   - If **score unparseable** (malformed tag): log warning, skip supersession for that torrent —
     never delete based on unparseable data.
5. **If multiple matches**: cancel ALL inferior torrents. Only the highest-scored one survives.

### Edge cases

| Case | Behavior |
|------|----------|
| Old-format tag (no `imdb-` segment) | Ignored — `_parse_imdb_id_from_tags` returns `None` |
| Same score | New torrent is skipped; existing download continues |
| qBittorrent unreachable | Log warning, skip supersession, queue normally |
| Tag parse failure | Log warning, skip that torrent's supersession check |

## Configuration

### New key

```yaml
queue_management:
  supersede_enabled: false  # default — opt-in
```

### Pydantic model

```python
class QueueManagementConfig(BaseModel):
    # ... existing fields ...
    supersede_enabled: bool = False
```

### Config migration

| From | To | Addition |
|------|----|----------|
| `2.21.0` | `2.22.0` | `(("queue_management", "supersede_enabled"), False)` |

`_CONFIG_VERSION` bumped to `"2.22.0"`.

### Default config (new users)

Handled automatically by the pydantic default. The `_default_config_dict()` function renders it
via model serialization — no additional changes needed.

## Files changed

| File | Change |
|------|--------|
| `src/movarr/config.py` | Add `supersede_enabled: bool = False` to `QueueManagementConfig`. Add `2.21.0 → 2.22.0` migration entry. Bump `_CONFIG_VERSION` to `"2.22.0"`. |
| `src/movarr/qbittorrent.py` | New helpers: `_build_supersede_tag`, `_parse_imdb_id_from_tags`, `_parse_score_from_tags`. Modify `add_torrent` to embed IMDb ID + score in the tag. Shorten UUID to 8 hex chars. |
| `src/movarr/search.py` | New function `_supersede(result, session)`. Called from `_process_single_result` before `_queue_and_persist` when `supersede_enabled` is true. Imports `_group_bonus` or `composite_quality_score` from `filters`. |
| `tests/unit/test_qbittorrent.py` | Tag parsing: valid, missing IMDb, missing score, old format, score comparison edge cases. |
| `tests/unit/test_search.py` | Supersession logic: no match, lower score superseded, higher score skips new, multiple inferiors, qBittorrent unreachable, disabled. |
| `README.md` | Document `supersede_enabled` under `queue_management` table. |

## What does NOT change

- **`extract_movarr_tag()`** — continues to find the `movarr-` prefix in both old and new tags.
- **Queue management** (stalled/metadata monitoring) — untouched.
- **Post-processing** — untouched.
- **Database schema** — no new tables or columns. Superseded torrents are written as
  `result: "Failed"` with a `"Failed: Superseded …"` reason in the existing result table.
- **Notifications** — superseded results use the existing `send_queued_notification` path
  (success) and the standard failure persistence path (superseded-by-better).

## Dependencies

- `quality_score` from `parsing.py` — already imported in `search.py`.
- `supersession_quality_score` from `filters.py` — already public, does quality_score + group_bonus
  without special-edition bonus. Used at comparison time (not baked into stored tag score).

## Testing guidance

### Unit tests for tag helpers (`test_qbittorrent.py`)

- `_build_supersede_tag` returns tag `movarr-<8hex>-imdb-<ttid>-score-<quality_score>`.
- `_parse_imdb_id_from_tags("movarr-a1b2c3d4-imdb-tt1234567-score-134")` → `"tt1234567"`.
- `_parse_imdb_id_from_tags("movarr-oldformat")` → `None`.
- `_parse_score_from_tags(...)` → `134`.
- `_parse_score_from_tags("movarr-oldformat")` → `None`.
- `extract_movarr_tag` works with new format.

### Unit tests for supersession logic (`test_search.py`)

Mock `QBittorrentClient`. Verify:

- `supersede_enabled=false` → no qBittorrent query, result queued normally.
- No matching IMDb ID in queue → result queued.
- Matching IMDb ID, new score > existing → existing deleted, new queued.
- Matching IMDb ID, new score <= existing → new skipped (Failed), existing untouched.
- Multiple matching IMDb IDs, all lower → all deleted, new queued.
- qBittorrent raises → warning logged, result queued anyway.
- Existing torrent has old tag format (no IMDb ID) → treated as no match.
