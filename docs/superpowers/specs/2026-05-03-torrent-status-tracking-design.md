# Torrent Status Tracking Design

**Date:** 2026-05-03
**Status:** Approved

## Problem

The movarr history database records every torrent submitted to qBittorrent as
`result="Passed"`. There is no way to distinguish between:

- A torrent that **completed successfully** (downloaded and post-processed)
- A torrent that **stalled** (no seeds/peers, or metadata fetch timeout) and
  was deleted by the queue manager

Because stalled titles stay in the DB as "Passed" forever, movarr never retries
them — even if a re-seeded torrent for the same title appears days later.

## Goal

- Record the final download outcome for each submitted torrent: **Completed** or
  **Stalled**.
- Allow stalled titles to be retried after a configurable number of days.
- Keep all existing deduplication behaviour for completed torrents (permanent
  skip).

---

## Result Lifecycle

```
[submitted to qBittorrent] → result="Passed"
        │
        ├─ post-processor copies files successfully
        │       └─ result="Completed"  (permanent — never retried)
        │
        └─ queue manager deletes stalledDL / metaDL torrent
                └─ result="Stalled", stalled_at=<utc now>
                        └─ after stalled_expiry_days → row deleted → title retried
```

---

## DB Schema Changes

**New column** on the `history` table:

```sql
ALTER TABLE history ADD COLUMN stalled_at TEXT  -- nullable ISO-8601 UTC timestamp
```

Set only when `result` transitions to `"Stalled"`. NULL for all other states.

**Migration:** DB version 8 → 9. Uses `PRAGMA table_info` guard (idempotent).

---

## Config Changes

New section added to `movarr.yml`, introduced via config version migration:

```yaml
database:
  stalled_expiry_days: 7
```

`stalled_expiry_days: 0` disables expiry (stalled records never deleted).

---

## Database Methods

### New methods

| Method | Signature | Behaviour |
|--------|-----------|-----------|
| `mark_stalled` | `(torrent_tag: str) -> None` | Sets `result="Stalled"`, `stalled_at=utcnow` on the record matching `torrent_tag` |
| `mark_completed` | `(torrent_tag: str) -> None` | Sets `result="Completed"`, `verified="true"` on the record matching `torrent_tag` |
| `expire_stalled` | `(days: int) -> int` | Deletes all `result="Stalled"` rows where `stalled_at` is older than `days` days; returns count deleted. No-op if `days == 0`. |

### Modified methods

**`has_passed(index_title)`** — updated filter:

```python
# Before
result == "Passed"

# After
result IN ("Passed", "Completed", "Stalled")
```

Expired stalled rows are removed by `expire_stalled()` before the check runs,
so no timestamp logic is needed inside `has_passed`.

**`set_verified(torrent_tag)`** — retained for backward compatibility; `mark_completed`
calls it internally.

---

## Component Changes

### `queue_manager.py`

- `run_queue_management(config, qbt)` → `run_queue_management(config, qbt, db)`
- After each `delete_stalled()` call (both `stalledDL` and `metaDL` paths),
  iterate the deleted torrent map, extract the movarr tag from each torrent's
  tag list, and call `db.mark_stalled(tag)`.

### `post_processor.py`

- Replace `db.set_verified(tag)` with `db.mark_completed(tag)`.
- `mark_completed` sets both `result="Completed"` and `verified="true"` to
  preserve compatibility with any code that reads the `verified` field.

### `scheduler.py`

- `_task_queue_management(config, qbt)` → `_task_queue_management(config, qbt, db)`
- `_task_search` calls `db.expire_stalled(config.database.stalled_expiry_days)`
  **before** calling `run_search`.
- `run_once` and `_run_daemon` pass `db` to the queue management job/task.

---

## Testing

Each new/modified method gets unit tests following the TDD-first pattern used
throughout the codebase:

- `TestMarkStalled` — tag found: sets result + stalled_at; tag not found: no-op
- `TestMarkCompleted` — tag found: sets result="Completed" + verified="true"; tag not found: no-op
- `TestExpireStalled` — deletes rows older than N days; retains rows within window; returns count; days=0 is no-op
- `TestHasPassed` — updated: Stalled within window → True; Stalled after expire → False (row gone)
- `TestQueueManager` — mock `db.mark_stalled` called for each deleted torrent (stalledDL + metaDL)
- `TestPostProcessor` — `db.mark_completed` called (not `set_verified` directly)
- DB migration test — v8→v9 adds `stalled_at` column

---

## Out of Scope

- Detecting stall state directly from qBittorrent API (the queue manager already
  handles this via `identify_for_deletion`; no new polling task needed).
- Tracking partial downloads or per-file completion.
- Notifications on stall/completion events (separate concern).
