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

`Database` has 30 methods covering several unrelated responsibilities:

| Responsibility | Methods |
| --- | --- |
| Schema migration | `_ensure_schema`, `_upgrade`, `_upgrade_v7…v13`, `_repair_missing_columns` (plus the module-level `_add_column_if_absent` helper) |
| Result persistence | `write`, `_result_to_record`, `find_by_tag`, `mark_completed`, `mark_stalled` |
| Expiry | `_expire_by_result`, `expire_stalled`, `expire_failed`, `expire_passed` |
| Deduplication | `is_duplicate_exact` |
| IMDb metadata cache | `find_imdb_metadata`, `_record_to_imdb_dict` |
| Key-value store | `kv_get`, `kv_set`, `kv_set_if_absent`, `kv_delete` |

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

**CRAP register:** see `docs/agent/plans/2026-11-03-crap-debt-register.md` for the
functions still at CRAP >= 9 (CC <= 9, accepted debt) and the re-check command.

*Last updated: 2026-11-03 — method inventory refreshed (dead `vacuum`, `has_passed`,
`is_duplicate_fuzzy`, `set_verified`/`is_verified` and `genres_for_tag` removed; schema
now at v13). Still an intentional deferral.*
