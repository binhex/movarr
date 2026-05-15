# Reject Genre Exclusive Filter — Design Spec

**Date:** 2026-05-15
**Status:** Approved
**Config version target:** 2.20.0

---

## Problem

The existing `reject_genre_list` is a flat blacklist: if **any** of a movie's IMDb
genres appears in the list, the movie is rejected. There is no way to express
"reject this genre only when it's the movie's sole genre, but accept it when
combined with other genres."

**Example:** A user wants to reject pure horror movies but keep horror/sci-fi
hybrids like *Alien*. With the current system, adding `Horror` to
`reject_genre_list` rejects *Alien* too.

---

## Solution

Add a new config option `reject_genre_exclusive_list` that rejects a movie only
when **every single one** of its genres is in the list. If the movie has any
genre **not** in this list, it passes this check.

### Config Model

New field on `FiltersConfig` in `config.py`:

```python
reject_genre_exclusive_list: list[str] = Field(default_factory=list)
```

An empty list (default) disables the feature. No migration required.

### Example Config

```yaml
filters:
  reject_genre_list:
    - Documentary        # Always reject Documentary, even Documentary/Drama
  reject_genre_exclusive_list:
    - Horror             # Reject pure Horror, keep Horror/Sci-Fi
```

### Filter Logic

New function `_check_reject_genre_exclusive` in `filters.py`:

1. If `reject_genre_exclusive_list` is empty → pass (feature disabled).
2. If the movie has no genre data → pass (insufficient information).
3. If **any** of the movie's genres is NOT in the reject-exclusive list → pass
   (the movie has non-rejected genres).
4. If **all** of the movie's genres ARE in the reject-exclusive list → reject.

### Pipeline Placement

Insert into the stage 2 checks in `filter_by_imdb`, immediately after the
existing `_check_reject_genre`:

```
_check_allow_title_type
    ↓
_check_reject_genre          (existing — "always reject" gate)
    ↓
_check_reject_genre_exclusive  (NEW — "reject exclusive" gate)
    ↓
_check_bitrate
    ↓
…remaining gates…
```

Ordering rationale: the stricter "always reject" check runs first. If a genre
appears in both lists, the hard reject takes priority.

### Behavior Matrix

| Movie Genres | `reject_genre_list` | `reject_genre_exclusive_list` | Result |
|---|---|---|---|
| `[Horror]` | `[Documentary]` | `[Horror]` | ❌ Reject (exclusive: all genres match) |
| `[Horror, Sci-Fi]` | `[Documentary]` | `[Horror]` | ✅ Pass (Sci-Fi not in exclusive) |
| `[Horror, Documentary]` | `[Documentary]` | `[Horror]` | ❌ Reject (regular: Documentary in reject list) |
| `[Horror, Documentary]` | `[]` | `[Horror, Documentary]` | ❌ Reject (exclusive: all genres match, both in list) |
| `[Action]` | `[Documentary]` | `[Horror]` | ✅ Pass (no match at all) |
| `[Horror, Sci-Fi]` | `[Horror]` | `[]` | ❌ Reject (regular: Horror in reject list) |
| `[]` | any | any | ✅ Pass (no genre data available — insufficient info to reject) |

### Code Changes

#### `config.py` — `FiltersConfig`
- Add `reject_genre_exclusive_list: list[str] = Field(default_factory=list)`

#### `filters.py`
- Add `_check_reject_genre_exclusive(result, config) → ResultDict`
- Insert into the `checks` list in `filter_by_imdb` after `_check_reject_genre`
- Add to `__all__` if made public (not needed — private function)

### Test Plan

New test class `TestFilterByImdbRejectGenreIfOnly` in `test_filters.py`:

| Test | Scenario | Expected |
|---|---|---|
| `test_if_only_genre_pure_rejects` | Single genre matching exclusive list | REJECT |
| `test_if_only_genre_with_other_passes` | Exclusive genre + non-matching genre | PASS |
| `test_empty_list_always_passes` | No exclusive genres configured | PASS |
| `test_case_insensitive` | Case-mismatched genre in exclusive list | Correctly matched |
| `test_multiple_genres_all_rejected` | All genres in exclusive list (2+ genres) | REJECT |
| `test_no_genre_data_passes` | No genre metadata available | PASS |

Also verify interaction with existing `reject_genre_list` (both lists populated)
in existing test run.

### Backward Compatibility

- Empty list = no behavior change.
- Configs with only `reject_genre_list` unchanged.
- No migration needed; `config_version` bump to `2.20.0` is optional (no
  structural change that requires migration logic).

---

## Implementation Order

1. Add field to `FiltersConfig` in `config.py`
2. Add `_check_reject_genre_exclusive` function to `filters.py` + wire into pipeline
3. Add tests to `test_filters.py`
4. `ruff` + `mypy` + `pytest --cov` pass
5. Optionally bump `config_version` to `2.20.0`
