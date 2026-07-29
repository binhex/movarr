# Dual-Title Library Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a movie has a non-English original title with English alternates, search the library for files matching any title variant — not just the original.

**Architecture:** Store `imdb_original_title` and `imdb_alternate_titles` in IMDb metadata (no new API calls — aux data already fetched). In `_check_library_canonical`, build a set of normalized titles and walk the library once, matching each file against every title. English movies skip alternate matching entirely (zero overhead).

**Tech Stack:** Python 3.12, pytest with pytest-mock, ruff, mypy

---

## File Map

| File | Change |
|------|--------|
| `src/movarr/models.py` | Add `imdb_original_title` and `imdb_alternate_titles` to `ResultDict` |
| `src/movarr/imdb_metadata.py` | `_build_imdbpie_payload`: add `original_title` and `alternate_titles`. `_apply_metadata`: map to result dict |
| `src/movarr/filters.py` | `_find_canonical_matches`: accept multiple title compares. `_check_library_canonical`: build multi-title set, pass to walk |
| `tests/unit/test_imdb_metadata.py` | Verify new fields in metadata payload |
| `tests/unit/test_filters.py` | Dual-title match scenarios |

---

### Task 1: Add model fields

**Files:**
- Modify: `src/movarr/models.py`

- [ ] **Step 1: Add `imdb_original_title` and `imdb_alternate_titles` to `ResultDict`**

```python
# In class ResultDict(TypedDict, total=False), after imdb_title:

    imdb_title: str | None
    imdb_original_title: str | None   # ADD
    imdb_alternate_titles: list[str] | None   # ADD
    imdb_year: int | None
```

- [ ] **Step 2: Verify import works**

Run: `cd /data/movarr && uv run python -c "from movarr.models import ResultDict; r: ResultDict = {'imdb_original_title': None, 'imdb_alternate_titles': None, 'result': 'Passed', 'result_details': []}"`

Expected: exit 0, no errors.

- [ ] **Step 3: Commit**

```bash
git add src/movarr/models.py
git commit -m "feat: add imdb_original_title and imdb_alternate_titles to ResultDict"
```

---

### Task 2: Populate new fields in IMDbPie metadata payload

**Files:**
- Modify: `src/movarr/imdb_metadata.py:150-175` (`_build_imdbpie_payload` return dict)
- Modify: `src/movarr/imdb_metadata.py:366-388` (`_apply_metadata`)

- [ ] **Step 1: Add `original_title` and `alternate_titles` to `_build_imdbpie_payload` return dict**

In `_build_imdbpie_payload`, add two keys to the returned dict. The function already calls `get_title_auxiliary(imdb_id)` and destructures it into the local `aux_data` variable through `_safe_val` calls. Add after the existing `"countries"` line:

```python
# Existing lines in _build_imdbpie_payload (around line 170):
        "languages": _convert_languages(", ".join(_extract_list_or_none(aux_data, "spokenLanguages") or [])) or None,
        "countries": _convert_countries(", ".join(_extract_list_or_none(aux_data, "origins") or [])) or None,
        # ADD these two lines:
        "original_title": aux_data.get("originalTitle"),
        "alternate_titles": (aux_data.get("alternateTitlesSample") or [])[:3],
        "directors": _credits_names(credits_data, "director"),
```

The `aux_data` variable is the raw dict returned by `client.get_title_auxiliary(imdb_id)` and is already in scope. `alternateTitlesSample` is a list of strings; `originalTitle` is a string (both on the top-level of aux_data, not nested). We slice to first 3 entries.

- [ ] **Step 2: Map new fields in `_apply_metadata`**

In `_apply_metadata`, add two lines inside the `result.update(...)` call, after the existing `"imdb_title"` line:

```python
# In _apply_metadata, inside result.update({...}) (around line 373):
            "imdb_title": data.get("title"),
            # ADD these two lines:
            "imdb_original_title": data.get("original_title"),
            "imdb_alternate_titles": data.get("alternate_titles"),
            "imdb_year": data.get("year"),
```

These `data.get(...)` calls return `None` when the key is absent, which is the correct default for OMDb fallback (OMDb's canonical dict won't have these keys).

- [ ] **Step 3: Commit**

```bash
git add src/movarr/imdb_metadata.py
git commit -m "feat: populate imdb_original_title and imdb_alternate_titles from IMDbPie aux data"
```

---

### Task 3: Test metadata changes

**Files:**
- Modify: `tests/unit/test_imdb_metadata.py:29-61` (`_make_imdbpie_data`), `tests/unit/test_imdb_metadata.py:145-161` (existing test)

- [ ] **Step 1: Add new fields to mock data**

In `_make_imdbpie_data`, add `originalTitle` and `alternateTitlesSample` to `aux_data`:

```python
# In _make_imdbpie_data(), inside the aux_data dict (around line 52):
    aux_data: dict = {
        "spokenLanguages": ["English"],
        "origins": ["US"],
        "certificate": {"certificate": "15"},
        "videos": {"mainTrailer": {"id": "vi1234567"}},
        # ADD these two lines:
        "originalTitle": "The Matrix",
        "alternateTitlesSample": [
            "Matrix",
            "La matrice",
            "Die Matrix",
        ],
    }
```

- [ ] **Step 2: Write test for new fields populated**

Add a new test method to `TestFetchImdbpie` class (around line 190):

```python
    def test_original_title_and_alternates_populated(self, mocker: MockerFixture) -> None:
        _mock_imdbpie_client(mocker)
        result = _make_result()
        out = _fetch_imdbpie(result)
        assert out["imdb_original_title"] == "The Matrix"
        assert out["imdb_alternate_titles"] == ["Matrix", "La matrice", "Die Matrix"]
```

- [ ] **Step 3: Write test for non-English movie with different original title**

Add a second test — modify `_make_imdbpie_data` to use "The Raid" test data. But since `_make_imdbpie_data` is a shared helper, use `_mock_imdbpie_client` with a custom override instead:

```python
    def test_non_english_movie_has_different_original_and_alternate_titles(self, mocker: MockerFixture) -> None:
        """For foreign films, original title differs from English alternates."""
        title_data, genres_data, credits_data, aux_data = _make_imdbpie_data()
        title_data["base"]["title"] = "Serbuan maut"
        aux_data["originalTitle"] = "Serbuan maut"
        aux_data["alternateTitlesSample"] = [
            "Operação Invasão",
            "The Raid",
            "The Raid: Redemption",
        ]
        # Build mock with overridden data
        mock_imdbpie = mocker.MagicMock()
        mock_client = mocker.MagicMock()
        mock_imdbpie.Imdb.return_value = mock_client
        mock_client.get_title.return_value = title_data
        mock_client.get_title_genres.return_value = genres_data
        mock_client.get_title_credits.return_value = credits_data
        mock_client.get_title_auxiliary.return_value = aux_data
        mocker.patch.dict("sys.modules", {"imdbpie": mock_imdbpie})
        result = _make_result()
        out = _fetch_imdbpie(result)
        assert out["imdb_title"] == "Serbuan maut"
        assert out["imdb_original_title"] == "Serbuan maut"
        assert out["imdb_alternate_titles"] == ["Operação Invasão", "The Raid", "The Raid: Redemption"]
```

- [ ] **Step 4: Run tests to verify**

Run: `cd /data/movarr && uv run pytest tests/unit/test_imdb_metadata.py -v`

Expected: All tests pass.

- [ ] **Step 5: Update existing test to assert new fields**

In `test_successful_fetch_populates_all_fields` (line 145), add assertions for the new fields after the existing assertions:

```python
        assert out["imdb_original_title"] == "The Matrix"
        assert out["imdb_alternate_titles"] == ["Matrix", "La matrice", "Die Matrix"]
```

- [ ] **Step 6: Run tests again to verify**

Run: `cd /data/movarr && uv run pytest tests/unit/test_imdb_metadata.py -v`

Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add tests/unit/test_imdb_metadata.py
git commit -m "test: verify imdb_original_title and imdb_alternate_titles fields"
```

---

### Task 4: Multi-title matching in `_find_canonical_matches`

**Files:**
- Modify: `src/movarr/filters.py:633-651` (`_find_canonical_matches`), `src/movarr/filters.py:654-670` (`_check_library_canonical`)

- [ ] **Step 1: Add a helper to detect non-ASCII titles**

Add a private function above `_find_canonical_matches`:

```python
def _contains_non_ascii(text: str) -> bool:
    """Return True if *text* contains any character outside the ASCII range."""
    return any(ord(ch) > 127 for ch in text)
```

- [ ] **Step 2: Build multi-title set in `_check_library_canonical`**

Replace the current single-title canonical_compare logic. The key change: after computing `canonical_compare` for the main `imdb_title`, also normalize alternate titles and add them to a set. Then pass the set through to `_find_canonical_matches`.

In `_check_library_canonical`, replace lines 661-670:

**Before:**
```python
    canonical_compare = normalise_for_compare(imdb_title)
    if not canonical_compare:
        return _fail(result, "Cannot normalise IMDb title for canonical library check.")
    matches = _find_canonical_matches(canonical_compare, imdb_year, library_walk, result)
    if not matches:
        return _pass(result, f"'{imdb_title} ({imdb_year})' not found in library.")
```

**After:**
```python
    canonical_compare = normalise_for_compare(imdb_title)
    if not canonical_compare:
        return _fail(result, "Cannot normalise IMDb title for canonical library check.")

    # Build set of all title-variants to search for.
    title_variants: set[str] = {canonical_compare}
    alternate_titles = result.get("imdb_alternate_titles")
    if alternate_titles and _contains_non_ascii(imdb_title):
        for alt in alternate_titles:
            norm = normalise_for_compare(alt)
            if norm:
                title_variants.add(norm)

    matches = _find_canonical_matches(title_variants, imdb_year, library_walk, result)
    if not matches:
        return _pass(result, f"'{imdb_title} ({imdb_year})' not found in library.")
```

- [ ] **Step 3: Modify `_find_canonical_matches` to accept a set of titles**

Change the function signature and body to iterate over all titles in the set. Each title uses the same imdb_year + fallback_year logic.

**Before:**
```python
def _find_canonical_matches(
    canonical_compare: str,
    imdb_year: str,
    library_walk: list[tuple[str, list[str], list[str]]],
    result: ResultDict,
) -> list[str]:
    matches = _walk_library_files(canonical_compare, imdb_year, library_walk)
    if matches:
        return matches
    fallback_year = str(result.get("movie_title_year") or "")
    if not fallback_year or fallback_year == imdb_year:
        return []
    return _walk_library_files(canonical_compare, fallback_year, library_walk)
```

**After:**
```python
def _find_canonical_matches(
    title_compares: set[str],
    imdb_year: str,
    library_walk: list[tuple[str, list[str], list[str]]],
    result: ResultDict,
) -> list[str]:
    """Walk library for any title in *title_compares*, stopping on first match.

    When the exact IMDb year finds nothing, try the index (release/remaster)
    year from *result* for each title so that library files named with a
    different year than the original IMDb production still match.
    """
    fallback_year = str(result.get("movie_title_year") or "")
    use_fallback = bool(fallback_year and fallback_year != imdb_year)

    for title_compare in title_compares:
        matches = _walk_library_files(title_compare, imdb_year, library_walk)
        if matches:
            return matches
        if use_fallback:
            matches = _walk_library_files(title_compare, fallback_year, library_walk)
            if matches:
                return matches
    return []
```

The iteration order is deterministic (set iteration order is insertion order in Python 3.7+). Original title is inserted first, so it's tried first.

- [ ] **Step 4: Verify import works**

Run: `cd /data/movarr && uv run python -c "from movarr.filters import _find_canonical_matches, _check_library_canonical; print('OK')"`

Expected: exit 0, "OK".

- [ ] **Step 5: Commit**

```bash
git add src/movarr/filters.py
git commit -m "feat: multi-title canonical library check — search original + alternate titles"
```

---

### Task 5: Test dual-title library matching

**Files:**
- Modify: `tests/unit/test_filters.py:1021-1079` (`TestFilterByImdbCanonicalLibraryEdgeCases`)

- [ ] **Step 1: Write test — library file named with alternate title is found**

Add to `TestFilterByImdbCanonicalLibraryEdgeCases`:

```python
    def test_dual_title_alternate_title_matches_library_file(self) -> None:
        """When original title is non-ASCII, library file named with an
        English alternate should still be found."""
        cfg = Config()
        result = _imdb_result(
            imdb_title="Gisaengchung",
            imdb_year=2019,
            imdb_original_title="Gisaengchung",
            imdb_alternate_titles=["寄生上流", "Parasite", "Parazit"],
            index_title_resolution="1080",
        )
        # Library file uses the English alternate "Parasite"
        library_walk: list[tuple[str, list[str], list[str]]] = [
            ("/library", [], ["Parasite 2019 1080p BluRay.mkv"])
        ]
        out = filter_by_imdb(result, cfg, library_walk=library_walk)
        assert out["result"] == "Passed"
        details = " ".join(out.get("result_details", []))
        assert "not found in library" not in details
```

- [ ] **Step 2: Write test — library file named with original title also found**

```python
    def test_dual_title_original_title_matches_library_file(self) -> None:
        """When alternates are present, library file named with the
        original non-ASCII title should still be found."""
        cfg = Config()
        result = _imdb_result(
            imdb_title="Gisaengchung",
            imdb_year=2019,
            imdb_original_title="Gisaengchung",
            imdb_alternate_titles=["寄生上流", "Parasite", "Parazit"],
            index_title_resolution="1080",
        )
        # Library file uses the original title
        library_walk: list[tuple[str, list[str], list[str]]] = [
            ("/library", [], ["Gisaengchung 2019 1080p BluRay.mkv"])
        ]
        out = filter_by_imdb(result, cfg, library_walk=library_walk)
        assert out["result"] == "Passed"
        details = " ".join(out.get("result_details", []))
        assert "not found in library" not in details
```

- [ ] **Step 3: Write test — ASCII title skips alternate search**

```python
    def test_ascii_title_skips_alternate_search(self) -> None:
        """English movies (ASCII title) skip alternate search entirely.
        Library file named with a foreign translation alternate should NOT match."""
        cfg = Config()
        result = _imdb_result(
            imdb_title="The Matrix",
            imdb_year=1999,
            imdb_original_title="The Matrix",
            imdb_alternate_titles=["Matrix", "La matrice", "Die Matrix"],
            index_title_resolution="1080",
        )
        # Library file uses the German alternate — should NOT match because
        # alternates are skipped for ASCII-original titles.
        library_walk: list[tuple[str, list[str], list[str]]] = [
            ("/library", [], ["Die Matrix 1999 1080p BluRay.mkv"])
        ]
        out = filter_by_imdb(result, cfg, library_walk=library_walk)
        # Should report "not found" since we only search the original (ASCII) title.
        details = " ".join(out.get("result_details", []))
        assert "not found in library" in details
```

- [ ] **Step 4: Write test — empty alternates falls back to single-title**

```python
    def test_empty_alternates_falls_back_to_single_title(self) -> None:
        """When imdb_alternate_titles is None (OMDb fallback), single-title
        behavior is used."""
        cfg = Config()
        result = _imdb_result(
            imdb_title="Gisaengchung",
            imdb_year=2019,
            imdb_original_title="Gisaengchung",
            imdb_alternate_titles=None,
            index_title_resolution="1080",
        )
        library_walk: list[tuple[str, list[str], list[str]]] = [
            ("/library", [], ["Gisaengchung 2019 1080p BluRay.mkv"])
        ]
        out = filter_by_imdb(result, cfg, library_walk=library_walk)
        assert out["result"] == "Passed"
```

- [ ] **Step 5: Write test — deduplication of identical normalized titles**

```python
    def test_alternate_normalizes_to_same_as_original_is_deduplicated(self) -> None:
        """When an alternate normalizes to the same string as the original,
        it is deduplicated by the set — no duplicate search."""
        cfg = Config()
        result = _imdb_result(
            imdb_title="Gisaengchung",
            imdb_year=2019,
            imdb_original_title="Gisaengchung",
            imdb_alternate_titles=["Gisaengchung", "Parasite"],
            index_title_resolution="1080",
        )
        library_walk: list[tuple[str, list[str], list[str]]] = [
            ("/library", [], ["Parasite 2019 1080p BluRay.mkv"])
        ]
        out = filter_by_imdb(result, cfg, library_walk=library_walk)
        assert out["result"] == "Passed"
```

- [ ] **Step 6: Run new tests to verify they fail (RED phase)**

Run: `cd /data/movarr && uv run pytest tests/unit/test_filters.py::TestFilterByImdbCanonicalLibraryEdgeCases -v`

Expected: The 4 existing tests pass, the 5 new tests fail with relevant errors (KeyError on `imdb_original_title` if model not found, or assertion failure on "not found in library" for the alternate-title test).

- [ ] **Step 7: After Task 4 implementation, run tests to verify they pass (GREEN phase)**

Run: `cd /data/movarr && uv run pytest tests/unit/test_filters.py::TestFilterByImdbCanonicalLibraryEdgeCases -v`

Expected: All 9 tests pass (4 existing + 5 new).

- [ ] **Step 8: Commit**

```bash
git add tests/unit/test_filters.py
git commit -m "test: dual-title library matching — alternate title search scenarios"
```

---

### Task 6: Full test suite verification

- [ ] **Step 1: Run full test suite**

Run: `cd /data/movarr && uv run pytest -v`

Expected: All tests pass, no regressions.

- [ ] **Step 2: Run type checker**

Run: `cd /data/movarr && uv run mypy src/movarr/`

Expected: 0 errors.

- [ ] **Step 3: Run linter**

Run: `cd /data/movarr && uv run ruff check src/movarr/models.py src/movarr/imdb_metadata.py src/movarr/filters.py`

Expected: 0 issues (or pre-existing issues only — no new ones from our changes).

- [ ] **Step 4: Run format check**

Run: `cd /data/movarr && uv run ruff format --check src/movarr/models.py src/movarr/imdb_metadata.py src/movarr/filters.py`

Expected: Already formatted (or fix any drift with `uv run ruff format`).

- [ ] **Step 5: Commit if any formatting fixes**

```bash
git add -u
git commit -m "chore: final QA passes for dual-title library matching"
```
