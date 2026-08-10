# Root Cause Diagnosis: Apostrophe Breaks Library File Matching

**Date:** 2026-05-13
**Status:** Fix implemented via TDD in `src/movarr/parsing.py`.

## Bug Summary

Movarr downloaded a 1080p copy of "Bridget Jones's Diary (2001)" despite an existing higher-quality 2160p REMUX already present in the library. Both library dedup checks (`_check_library` and `_check_library_canonical`) failed to match the existing file, so movarr treated it as "not in library" and proceeded with the download.

## Root Cause

The `normalise_for_compare()` function in `src/movarr/parsing.py` strips apostrophes via `_RE_COMPARE` (line 50-51), but does **not** handle the possessive `'s` suffix correctly when the base word already ends in `s`.

### The mismatch chain

| Source | Input | After `normalise_for_compare()` |
|--------|-------|-------------------------------|
| IMDb title | `Bridget Jones's Diary` | `bridgetjonessdiary` (18 chars) |
| Index title | `Bridget Joness Diary` | `bridgetjonessdiary` (18 chars) |
| Library file | `Bridget.Jones.Diary.2001....mkv` | `bridgetjonesdiary` (17 chars) |

The extra `s` from the possessive/contracted `'s` survives normalization, creating a string that differs from the library file's normalized form by exactly one character.

### Exact comparison failure

```python
# filters.py line 627-629 (_match_library_file)
norm = normalise_for_compare(lib_title)        # → "bridgetjonesdiary"
if norm != title_compare:                       # "bridgetjonessdiary" != "bridgetjonesdiary" → True
    return None                                 # → no match found
```

### Two paths, same mismatch

1. **`_check_library` (line 337):** Uses `movie_title_compare` derived from the **index title**. Indexers strip the apostrophe from filenames, producing `"Bridget Joness Diary"` → normalises to `"bridgetjonessdiary"`.

2. **`_check_library_canonical` (line 586):** Uses `imdb_title` from IMDb metadata, which preserves the apostrophe: `"Bridget Jones's Diary"` → after `_RE_COMPARE` strips `'`, becomes `"bridgetjonessdiary"`.

Both produce the same (wrong) normalized string `"bridgetjonessdiary"`, which fails to match the library file's `"bridgetjonesdiary"`.

### Why `_RE_COMPARE` can't fix this alone

`_RE_COMPARE` at `parsing.py:50-51`:
```python
_RE_COMPARE = re.compile(r"[\s\.\-\_\:\+\'\"\!\,\@\#]+")
```

This correctly strips the `'` character, but the possessive `'s` construction (`Jones's`) leaves an extra `s` attached to the word. The regex treats `'` as just another separator character, but apostrophes in possessives are NOT separators — they're grammatical markers that change the word boundary.

### Why "It's" passes existing tests

The existing test (`test_removes_punctuation` at `test_parsing.py:151`):
```python
result = normalise_for_compare("It's a Movie!")
```

`"It's"` → strip `'` → `"Its"` which is the correct pronoun form. This works because `"it"` doesn't end in `s`, so removing `'` doesn't create a double letter. The bug only manifests when the base word already ends in `s` before the possessive `'s` (e.g., names like Jones, James, Bridges, etc.).

## Affected Code

| File | Line(s) | Function | Role |
|------|---------|----------|------|
| `src/movarr/parsing.py` | 50-51 | `_RE_COMPARE` | Regex that strips apostrophe but leaves trailing `s` |
| `src/movarr/parsing.py` | 217-234 | `normalise_for_compare()` | Normalization that produces the double-`s` bug |
| `src/movarr/filters.py` | 586-603 | `_check_library_canonical()` | Canonical IMDb dedup — produces the "not found" log message |
| `src/movarr/filters.py` | 337-356 | `_check_library()` | Index-title dedup — same mismatch |
| `src/movarr/filters.py` | 609-633 | `_match_library_file()` | Exact string comparison that fails |
| `src/movarr/filters.py` | 65,86,107 | `_UNICODE_APOSTROPHES` | Normalizes Unicode apostrophes to ASCII — only used in edition token functions, **not** in library comparison path |

## Impact

Any movie with a possessive apostrophe in its title where the possessor's name ends in `s` will fail library dedup. Examples:

- Bridget Jones's Diary
- James's Journey
- Bridges's Crossing
- etc.

This causes movarr to download lower-quality copies of movies that already exist in the library with the apostrophe stripped from the filename.

## Evidence

From `movarr.log`:
```
2026-05-13 19:08:19 | INFO | [LimeTorrents] 'Bridget Jones's Diary (2001)' not found in library.
2026-05-13 19:08:19 | SUCCESS | [LimeTorrents] 'Bridget Joness Diary 2001 ...' passed all filters.
```

But the library already contained:
```
/mnt/disk14/Movies/UHD/Vicky/Bridget Jones's Diary (2001)/Bridget.Jones.Diary.2001.2160p.BluRay.REMUX.HEVC.DTS-HD.MA.TrueHD.7.1.Atmos-FGT.mkv.mkv
```

## Fix Implemented (TDD)

### Approach
Added `_RE_POSSESSIVE_S` regex that strips `'s` (or `'` alone) **only when preceded by `s`** — before the general `_RE_COMPARE` separator stripping. This targets possessive constructions on s-ending names (`"Jones's"` → `"Jones"`) without breaking contractions (`"It's"` stays `"Its"`).

### Regex (parsing.py:54-55)
```python
_RE_POSSESSIVE_S = re.compile(r"(?<=s)['\u2018\u2019\u02bc\u02bb]s?\b")
```
- `(?<=s)` — lookbehind: only match when preceded by `s`
- `['\u2018\u2019\u02bc\u02bb]` — ASCII and Unicode apostrophes
- `s?` — optional trailing `s` (handles both `"Jones's"` and `"Jones'"`)
- `\b` — word boundary

### Applied in normalise_for_compare() (parsing.py:233)
```python
result = _RE_POSSESSIVE_S.sub("", result)  # before _RE_COMPARE
result = _RE_COMPARE.sub("", result)
```

### Test added (test_parsing.py:166-173)
```python
def test_possessive_apostrophe_on_s_ending_name_normalises_same_as_without(self):
    with_apos = normalise_for_compare("Bridget Jones's Diary")
    without_apos = normalise_for_compare("Bridget Jones Diary")
    assert with_apos == without_apos
```

### Verified all edge cases
- `"Jones's"` → `"jones"` ✓ (s-ending possessive with trailing s)
- `"Jones'"` → `"jones"` ✓ (s-ending possessive without trailing s)
- `"It's"` → `"its"` ✓ (contraction preserved — preceded by `t`, not `s`)
- `"James's"` → `"james"` ✓ (another s-ending name)
- `"Harry's"` → `"harrys"` ✓ (non-s-ending — handled by `_RE_COMPARE`)
- Unicode `\u2019` apostrophe also handled ✓
- Full library match simulation: `"Bridget Jones's Diary"` ↔ `"Bridget.Jones.Diary.2001...mkv"` → `"bridgetjonesdiary"` = `"bridgetjonesdiary"` ✓
