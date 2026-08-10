# Dual-Title Library Matching

**Date:** 2026-07-28
**Status:** Approved
**Scope:** IMDb metadata, library dedup (filters.py), models

## Problem

When a movie has a non-English original title (e.g. "Serbuan maut"), IMDb also provides alternate
titles in other languages (e.g. "The Raid: Redemption"). The user's media library may contain files
named with **either** title — the original or the English/popular alternate. Currently movarr only
searches the library for the original IMDb title (`imdb_title`), so files named with an alternate
title are invisible to the canonical library check. This causes false negatives: the library file
exists but movarr reports "not found in library."

## Solution

Store the original title AND the most common alternate titles in IMDb metadata. During the canonical
library check, match library files against **all** titles — original + alternates — in a single
filesystem walk. Post-processing continues to use the original title for folder naming (consistent,
no guesswork about which alternate to use).

### When dual-title search activates

Only when the original title contains non-ASCII characters (indicating a non-English/foreign
title). English-language movies (e.g. "The Matrix") skip alternate matching — zero overhead.

### Alternate title selection

Up to 3 alternates from `alternateTitlesSample` in IMDbPie's auxiliary data. Empirical testing
shows the most common English/popular title appears within the first 3 entries for every
foreign-language movie tested (The Raid, Parasite, Oldboy, etc.).

OMDb fallback: no alternates available → single-title behavior only.

## Design

### 1. IMDb metadata — fetch and store alternate titles

**File:** `src/movarr/imdb_metadata.py`

In `_build_imdbpie_payload`, add two fields from the already-fetched auxiliary data:

```python
"original_title": aux.get("originalTitle"),
"alternate_titles": (aux.get("alternateTitlesSample") or [])[:3],
```

In `_apply_metadata`, map them to result fields:

```python
"imdb_original_title": data.get("original_title"),
"imdb_alternate_titles": data.get("alternate_titles"),
```

No new API calls — `get_title_auxiliary()` is already called for certs, languages, and countries.

### 2. Models — add fields to ResultDict

**File:** `src/movarr/models.py`

```python
imdb_original_title: str | None
imdb_alternate_titles: list[str] | None
```

### 3. Library check — single-pass multi-title match

**File:** `src/movarr/filters.py` — `_check_library_canonical`

Build a deduplicated set of normalized titles:

```python
titles: set[str] = {normalise_for_compare(imdb_title)}
if imdb_alternate_titles and _contains_non_ascii(imdb_title):
    for alt in imdb_alternate_titles:
        norm = normalise_for_compare(alt)
        if norm and norm != titles_first:
            titles.add(norm)
```

Walk the library once. For each file, check against **every** title in the set. First match
(across any title) wins — stop walking.

The `_match_library_file` helper continues to require both title **and** year to match, so
false positives (e.g. matching "The Raid 2" against "The Raid") are prevented by either the
year filter or the normalized title comparison.

### 4. Post-processing — no changes

Post-processing already uses `imdb_title` (the original title) for folder naming:

```python
folder_name = f"{imdb_title} ({imdb_year})"
```

This stays consistent. Over time, new library additions use the original title. Existing files
with alternate-title names remain discoverable via the dual search.

## Edge Cases

| Scenario | Behavior |
|---|---|
| English movie (title already ASCII) | Alternate search skipped. Single-title behavior. |
| OMDb fallback (no alternates) | `imdb_alternate_titles` is `None`. Single-title behavior. |
| Original and alternate normalize to same string | Deduplicated by the `set` — only one search performed. |
| Multiple library files match different titles | First match wins (file found → stop). Same as current behavior. |
| Movie has `None` for `imdb_title` | `_check_library_canonical` exits early — already handled by existing guards. |
| Database cached metadata from before this change | Old records lack `imdb_original_title` and `imdb_alternate_titles` → treated as single-title (graceful degradation). |

## Files Modified

| File | Change |
|---|---|
| `src/movarr/imdb_metadata.py` | `_build_imdbpie_payload`: add `original_title`, `alternate_titles` fields. `_apply_metadata`: map to result dict. |
| `src/movarr/models.py` | Add `imdb_original_title`, `imdb_alternate_titles` to `ResultDict`. |
| `src/movarr/filters.py` | `_check_library_canonical`: multi-title library check. |
| `tests/unit/test_filters.py` | New tests for dual-title matching + existing tests unchanged (backward compat). |
| `tests/unit/test_imdb_metadata.py` | Verify new fields populated correctly from IMDbPie payload. |

No config changes. No new modules. No API changes.

## Performance

- **API calls**: zero overhead — aux data already fetched.
- **Library walk for English movies**: unchanged. Skip alternate matching entirely.
- **Library walk for non-English movies**: extra ~3 string comparisons per library file.
  For a 500-file library: ~7.5ms additional time. Filesystem walk dominates at hundreds of ms.
- **Memory**: ~3 extra strings and one `set` per non-English result. Negligible.

## Test Strategy

1. **`test_filters.py`**: Add test cases to the existing `TestFilterByImdbCanonicalLibraryEdgeCases`
   class for:
   - Non-ASCII original title + alternates → library file named with alternate → found
   - Non-ASCII original title + alternates → library file named with original → found
   - Non-ASCII original title + empty alternates → single-title fallback
   - ASCII original title → alternates skipped, single-title behavior
   - Original and alternate normalize to same string → deduplication works
   - Multiple alternates, file matches second alternate → found

2. **`test_imdb_metadata.py`**: Verify `imdb_original_title` and `imdb_alternate_titles`
   are populated when IMDbPie returns them, and `None` when they're absent.

3. **Existing tests**: All 1,300+ tests must continue to pass — no regression in single-title
   behavior.
