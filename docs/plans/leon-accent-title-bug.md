# Root Cause Diagnosis: Accented Characters + Original Title Break Library Matching

**Date:** 2026-05-14
**Status:** Diagnosis complete — root causes found. Do NOT fix here; next chain step handles the fix.

## Bug Summary

Movarr downloaded a 1080p copy of "Léon: The Professional (1994)" despite an existing 2160p REMUX at `/media/Movies/UHD/Paul/Leon The Professional (1994)/`. Two independent issues caused the library dedup check to miss the existing UHD file.

## Evidence from Logs

```
Line 43309: OMDb: 'Leon: The Professional (1994)' does not match 'Leon' (1994).
Line 43322: Index title '...Leon.1994...1080p...' passes library check — library file
            'Léon.1994.1080p.Bluray.x264.mkv' at 1080p found (lib score: 70, index score: 80).
Line 43326: Notification sent: movarr: Léon (1994) — IMDb 8.5 — Queued
```

The library check found a **lower quality 1080p** file (`Léon.1994.1080p.Bluray.x264.mkv`) that happened to match, but missed the **higher quality 2160p REMUX** at `Leon.The.Professional.1994.2160p.BluRay.REMUX...mkv`.

## Root Cause 1: Accented Characters Survive Normalization

### The mismatch

| Source | Input | After `normalise_for_compare()` |
|--------|-------|-------------------------------|
| IMDb title | `Léon` | `léon` |
| IMDb title (full) | `Léon: The Professional` | `léontheprofessional` |
| Library 1080p (has accent) | `Léon` | `léon` |
| Library 2160p (no accent) | `Leon The Professional` | `leontheprofessional` |

The accented `é` (U+00E9) survives the entire normalization pipeline because:

1. **`sanitise()` `_RE_NON_ASCII`** (`parsing.py:27-29`): requires **2+ consecutive** non-ASCII characters. A single `é` does NOT match — it passes through untouched.

2. **`normalise_for_compare()` `_RE_COMPARE`** (`parsing.py:50-51`): only strips **ASCII** separator characters: `[\s\.\-\_\:\+\'\"\!\,\@\#\u2018\u2019\u02bc\u02bb]+`. Non-ASCII letters like `é` are NOT in this character class.

### Affected code path

```
_check_library_canonical (filters.py:586)
  → imdb_title = "Léon"
  → canonical_compare = normalise_for_compare("Léon")  → "léon"
  → _walk_library_files("léon", "1994", ...)
    → _match_library_file("Leon.The.Professional.1994...mkv", "léon", "1994")
      → san = sanitise(...) → "Leon The Professional 1994..."
      → lib_title = extract_movie_title(san) → "Leon The Professional"
      → norm = normalise_for_compare("Leon The Professional") → "leontheprofessional"
      → norm != title_compare  → "leontheprofessional" != "léon"  → MISMATCH
```

## Root Cause 2: IMDb Original Title ≠ Library Full Title

IMDb returns the **original French title** `"Léon"` (single word) while the library file has the **full English release title** `"Leon The Professional"`. Even if accents were normalized, these would never match because the text content is different.

### The mismatch

| Source | Title text | After normalization |
|--------|-----------|-------------------|
| IMDb | `Léon` | `léon` |
| Library 2160p | `Leon The Professional` | `leontheprofessional` |

`"léon"` ≠ `"leontheprofessional"` — completely different strings.

### Affected code path

Same as Root Cause 1 — the library check requires exact string match after normalization (`filters.py:629`):
```python
if norm != title_compare:
    return None
```

There is no alternate-title lookup or fuzzy matching.

## Why the 1080p File WAS Found

The 1080p library file `Léon.1994.1080p.Bluray.x264.mkv` happened to match because:
- Its filename contains the same accented `é` as the IMDb title
- Its movie title portion is just `Léon` (same as the IMDb original title)
- `"léon" == "léon"` → match ✓

## Impact

**Any movie where:**
1. The IMDb title contains accented/special characters (é, ñ, ü, ç, ø, etc.) 
2. AND the library filename uses ASCII equivalents (e → e, ñ → n, etc.)
→ Library dedup will fail.

**OR where:**
3. IMDb returns the original foreign-language title while the library file uses the English/localized title
→ Library dedup will fail regardless of accent handling.

Examples of affected movies: "Léon: The Professional", "Amélie", "Caché", "La Haine", "Y Tu Mamá También", "Fanny och Alexander", etc.

## Fix Direction

Two separate fixes needed:

### Fix 1: Strip/convert accented characters in `normalise_for_compare()`
Add a step that converts common accented/special characters to their ASCII equivalents (e.g., é→e, ñ→n, ü→u) using Unicode normalization (NFKD + ASCII filter) before the `_RE_COMPARE` substitution.

### Fix 2: Add alternate-title awareness to library matching
When IMDb returns only the original title, also check against the full localized title if available. Or: include the IMDb "original title" as an additional search term when walking the library.
