# Tech Debt — Unresolved Architectural Smells

> **AGENT NOTICE**: Flag these items to the user at the start of any session that
> touches `src/movarr/models.py`, `src/movarr/database.py`, or any module that
> imports `ResultDict` or `Database`. Both are P2 (correctness-affecting as the
> codebase grows) and have been deferred intentionally — they are not forgotten.

---

## 1. `ResultDict` primitive obsession

**File**: `src/movarr/models.py`
**Priority**: P2
**Identified by**: Both code-review agents (rounds completed 2026-05-08)

### Problem

`ResultDict` is a 40+ field mutable `TypedDict` shared across 8 modules via plain
string key access:

```
search.py  filters.py  imdb_search.py  imdb_metadata.py
database.py  notifications.py  qbittorrent.py  post_processor.py
```

Every pipeline stage reaches into the same bag. There are no invariants, no
encapsulation, and no compile-time guarantee that a field is set before it is read.
Every new pipeline field forces updates across all 8 modules plus all their test files.

### Intended fix

Replace `ResultDict` with a proper typed dataclass (or Pydantic model) with attribute
access. Callers use `result.index_title` instead of `result["index_title"]`. Validation
and default values move into the class, not scattered across callers.

### Scope

All 8 source modules above + all their corresponding test files. Estimate: full-day
refactor with 100% test coverage as the safety net.

---

## 2. `Database` god object

**File**: `src/movarr/database.py`
**Priority**: P2
**Identified by**: Both code-review agents (rounds completed 2026-05-08)

### Problem

`Database` has 26 methods covering six unrelated responsibilities:

| Responsibility | Methods |
|---|---|
| Schema migration | `_migrate`, `_create_tables` |
| Result persistence | `write`, `find_by_tag`, `mark_completed`, `mark_stalled`, `expire` |
| Deduplication | `is_duplicate`, `dedup_*` |
| IMDb metadata cache | `get_imdb_cache`, `set_imdb_cache` |
| Vacuuming / housekeeping | `vacuum` |
| Key-value store | `get_kv`, `set_kv`, `delete_kv` |

Every caller that needs the KV store also drags in all the result-persistence logic
and vice versa.

### Intended fix

Split into focused components, e.g.:

- `ResultRepository` — write/find/mark/expire result rows
- `ImdbCache` — get/set IMDb metadata cache
- `KvStore` — get/set/delete the service-health key-value entries
- Keep `Database` as a thin facade that composes these, or eliminate it

### Scope

All callers of `Database` across the codebase + all test files that mock it.
Estimate: full-day refactor; requires updating every `from movarr.database import Database`
call site.

---

*Last updated: 2026-05-08 — deferred after tech-debt session, intentional deferral.*
