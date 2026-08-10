# Design: Rename `bad_` → `reject_` and Add `reject_index_group_list`

## Overview

Standardises the reject-list vocabulary by renaming existing `bad_` fields to
`reject_`, and adds a new filter that blocks torrents from specific release
groups.

## Motivation

1. **Naming consistency** — the `allow_`/`reject_`/`override_`/`prefer_` scheme
   is self-documenting and avoids the ambiguity of "bad" (bad how? bad quality?
   bad taste?).
2. **User request** — users want to block specific release groups (e.g. known
   for poor encodes, wrong languages, watermarks) without waiting for IMDb
   metadata.

## Fields

### Renamed (existing)

| Current | New |
|---------|-----|
| `bad_index_title_list` | `reject_index_title_list` |
| `bad_genre_list` | `reject_genre_list` |
| `bad_movie_title_list` | `reject_movie_title_list` |

### New

| Field | Type | Default | Behaviour |
|-------|------|---------|-----------|
| `reject_index_group_list` | `list[str]` | `[]` | If the release group extracted from the index title matches any entry (case-insensitive), the torrent is rejected before IMDb lookup. Empty = skip check. |

## Config Model (`config.py`)

- Bump `_CONFIG_VERSION` to `"2.8.0"`.
- Rename fields on `FiltersConfig`.
- Add `reject_index_group_list` to `FiltersConfig`.
- Add `_migrate_v27_to_v28()`:
  ```python
  def _migrate_v27_to_v28(raw: dict[str, Any]) -> dict[str, Any]:
      filters = raw.setdefault("filters", {})
      for old, new in (
          ("bad_index_title_list", "reject_index_title_list"),
          ("bad_genre_list", "reject_genre_list"),
          ("bad_movie_title_list", "reject_movie_title_list"),
      ):
          if old in filters:
              filters[new] = filters.pop(old)
      raw.setdefault("general", {})["config_version"] = "2.8.0"
      return raw
  ```
- Register migration in `MIGRATIONS` dict.

## Filter Logic (`filters.py`)

### New function: `_check_reject_index_group()`

```python
def _check_reject_index_group(result: ResultDict, config: Config) -> ResultDict:
    """Reject torrents from specific release groups."""
    reject_list = config.filters.reject_index_group_list
    if not reject_list:
        return _pass(result, "No reject_index_group_list defined.")

    index_title = result.get("index_title") or ""
    group = extract_group(sanitise(index_title))
    if not group:
        return _pass(result, "No release group detected; skipping group check.")

    reject_lower = [g.lower() for g in reject_list]
    if group.lower() in reject_lower:
        return _fail(result, f"Release group '{group}' is in reject_index_group_list.")
    return _pass(result, f"Release group '{group}' is not in reject_index_group_list.")
```

### Placement in `filter_by_index`

Insert at the start of the check chain (before `_check_search_criteria`):

```python
checks = [
    lambda r: _check_reject_index_group(r, config),
    lambda r: _check_search_criteria(r, index_site),
    ...
]
```

This ensures group rejection happens first — cheapest check, no IMDb needed.

### Renamed functions

- `_check_bad_keywords` → `_check_reject_keywords`
- `_check_bad_genre` → `_check_reject_genre`
- `_check_bad_movie_titles` → `_check_reject_movie_titles`

Internal variable names updated accordingly.

## Tests

### `test_filters.py`

Update existing test classes:
- `TestFilterByIndexBadKeywords` → `TestFilterByIndexRejectKeywords`
- `TestFilterByImdbBadGenre` → `TestFilterByImdbRejectGenre`
- `TestFilterByImdbBadMovieTitle` → `TestFilterByImdbRejectMovieTitle`
- Update all `_make_config(...)` calls from `bad_*` to `reject_*`.

Add new class `TestFilterByIndexRejectGroup`:
- `test_reject_group_match_fails` — "Movie 2020 1080p BluRay FGT" with `["FGT"]` → Failed
- `test_non_reject_group_passes` — "Movie 2020 1080p BluRay SPARKS" with `["FGT"]` → Passed
- `test_empty_list_skips_check` — default config → Passed
- `test_case_insensitive_match` — `["fgt"]` matches `"FGT"` → Failed

### `test_config.py`

- Update all migration assertions from `"2.7.0"` to `"2.8.0"`.
- Add `TestMigrationV27toV28`:
  - `test_v27_renames_bad_fields` — config with old `bad_*` fields loads as `reject_*`
  - `test_v27_migration_creates_backup` — `.bak.2.7.0` exists
  - `test_v27_preserves_data` — renamed lists contain same values post-migration
  - `test_existing_v28_needs_no_migration` — no backup created

## Documentation (`README.md`)

Update the filters table:

| Key | Description | Default |
|-----|-------------|---------|
| `reject_index_title_list` | Index titles containing any of these keywords are rejected before IMDb lookup. | `[]` |
| `reject_genre_list` | Reject any movie whose IMDb genres include one of these values. | `[]` |
| `reject_movie_title_list` | Reject movies whose resolved title exactly matches any entry. | `[]` |
| `reject_index_group_list` | Reject torrents from these release groups (case-insensitive). | `[]` |

Update FAQ and inline config comments to use `reject_` terminology.

## Backward Compatibility

Existing configs with `bad_*` fields are automatically migrated on load:
- Old fields are renamed in-memory and written back to disk.
- A `.bak.<version>` backup is created before migration.
- No manual user action required.

## Open Questions

- None. Design approved by user on 2026-05-05.
