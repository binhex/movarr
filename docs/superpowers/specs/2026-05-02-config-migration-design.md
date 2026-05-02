# Config Migration System — Design Spec

**Date:** 2026-05-02  
**Status:** Approved (autopilot)

---

## Problem

When movarr's configuration schema changes (e.g. `notification.email.*` → `notification.apprise_urls`), existing `config.yml` files on disk are not updated. Users silently run with stale/incompatible config keys until they manually edit their file or delete and recreate it.

As the project evolves this will happen repeatedly. A migration system is needed.

---

## Goals

- Automatically migrate existing `config.yml` files to the current schema on startup
- Back up the file before any modification so users can always revert
- Log every migration step clearly
- Make it trivial to register future migrations (one function per version bump)
- Keep the mechanism entirely in `config.py` (no new files needed)

---

## Non-Goals

- Interactive migration prompts
- Database migrations (separate concern)
- Downgrade / rollback logic

---

## Design

### Version signal

`general.config_version` (already present as `str = "1.0.0"`) is the migration key.  
The current application version constant (`_CONFIG_VERSION`) advances with each breaking schema change.

| Value | Meaning |
|-------|---------|
| `"1.0.0"` | Original schema — `notification.email` SMTP block |
| `"2.0.0"` | Current schema — `notification.apprise_urls: list[str]` |

Any config file without `general.config_version` is treated as `"1.0.0"` (oldest known).

### Migration registry

```python
MIGRATIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "1.0.0": _migrate_v1_to_v2,
}
```

Keys are the **from** version; each function returns the transformed raw dict with `general.config_version` updated to the next version.

### Migration chain

`_run_migrations(raw, path)` walks the chain:

```
current_version = raw["general"]["config_version"]   # e.g. "1.0.0"
while current_version in MIGRATIONS:
    backup if first iteration
    raw = MIGRATIONS[current_version](raw)
    current_version = raw["general"]["config_version"]  # updated by migration fn
write migrated config to disk
```

### Backup strategy

Before the first migration step, copy `config.yml` → `config.yml.bak.<old_version>` (e.g. `config.yml.bak.1.0.0`) in the same directory. Subsequent chained migrations reuse the same backup. No backup is written if no migration is needed.

### v1.0.0 → v2.0.0 migration

```python
def _migrate_v1_to_v2(raw: dict) -> dict:
    notification = raw.setdefault("notification", {})
    notification.pop("email", None)           # remove old SMTP block
    notification.setdefault("apprise_urls", [])  # add new field (empty = disabled)
    raw.setdefault("general", {})["config_version"] = "2.0.0"
    return raw
```

### load_config() integration

```
load_config(path):
    if not exists: create_default_config; return
    raw = yaml.safe_load(path)
    raw = _run_migrations(raw, path)        ← NEW
    merged = _deep_merge(defaults, raw)
    return Config.model_validate(merged)
```

### _CONFIG_VERSION constant

Advances from `"1.0.0"` to `"2.0.0"`. `GeneralConfig.config_version` default also advances to `"2.0.0"`. New configs written by `create_default_config()` will therefore have `general.config_version: "2.0.0"` out of the box.

---

## File changes

| File | Change |
|------|--------|
| `src/movarr/config.py` | Advance `_CONFIG_VERSION` to `"2.0.0"`, add `_run_migrations()`, add `_migrate_v1_to_v2()`, call `_run_migrations()` in `load_config()` |
| `tests/unit/test_config.py` | Tests for `_run_migrations()`: no-op when current, migrates v1→v2, backup created, chained migrations |

---

## Testing

- `test_run_migrations_noop`: config already at `"2.0.0"` → no backup written, dict unchanged
- `test_run_migrations_v1_to_v2`: config with `notification.email` → backup created, `email` removed, `apprise_urls` added, file updated on disk, version set to `"2.0.0"`
- `test_run_migrations_missing_version_treated_as_v1`: no `general.config_version` key → treated as `"1.0.0"`, migration runs
- `test_run_migrations_writes_file`: post-migration YAML on disk is valid and parseable
- Future: `test_run_migrations_chained`: v1→v2→v3 in one `load_config()` call

---

## Error handling

- Migration functions must not raise on benign missing keys (use `.get()` / `.setdefault()`)
- If an unexpected exception occurs during migration, it propagates (startup fails loudly — better than silently running with bad config)
- Backup failure is non-fatal (logged as warning, migration still proceeds)
