# Release review — residual findings (round 10 of 10)

**Branch:** `main`
**Commit:** `38faf45` (`Dev (#65)`) + uncommitted working-tree changes
**Round reached:** 10
**Circuit breaker cap:** 10 (`CIRCUIT_BREAKER_ROUNDS`)
**Reviewer models:** `opencode-go/deepseek-v4.1-flash:max` (Reviewer A) and `opencode-go/glm-5.3-flash:max` (Reviewer B)

Round 10 was the final round allowed by the cap. It reported the findings below. Every one was
**fixed**, but by definition of the cap they were **not re-reviewed** by either reviewer afterwards.

## Findings

| # | Severity | Location | Description | Status |
| --- | ---------- | ---------- | ------------- | -------- |
| 1 | HIGH | `src/movarr/search.py:216`, `src/movarr/post_processor.py:366` | "Different cut" edition guard failed open: both sides of the comparison were built from `sanitise()`d text, and `sanitise()` strips trailing bracket tags, so `…[Extended]` and a theatrical release compared as the same edition and supersession could delete a different cut. | applied, not independently re-reviewed |
| 2 | MEDIUM | `src/movarr/filters.py:39` (`_RE_SPECIAL`), `:1257` | The mirror-image defect: dotted/hyphenated edition tags (`Movie.2019.Directors.Cut.2160p`) were never matched, so a same-edition upgrade of a Director's Cut was silently never superseded in the library. | applied, not independently re-reviewed |
| 3 | MEDIUM | `src/movarr/scheduler.py` (`run_once`), `src/movarr/cli.py` | `run_once()` was advertised as a foreground mode but was unreachable from the CLI; it existed only to be tested. | applied, not independently re-reviewed |
| 4 | MEDIUM | `src/movarr/filters.py:258`, `:408` | Float-formatted byte sizes (`"4294967296.0"`) raised `ValueError` and failed otherwise-valid torrents, inconsistent with `bytes_to_mb`, which tolerates that shape. | applied, not independently re-reviewed |
| 5 | MEDIUM | `src/movarr/queue_manager.py:79` | The `metaDL` pass measured age from `added_on` (time added) rather than "time stuck", contradicting the README. | applied, not independently re-reviewed |
| 6 | LOW | `src/movarr/parsing.py:51` | Dead compiled regex `_RE_SEPARATOR_BRACKETS` (no readers). | applied, not independently re-reviewed |

### What was changed for each finding

1. Supersession now compares `edition_token_set()` over the **raw** release names (`index_title` on the
   search side, `raw_release_name` threaded through `_maybe_delete_superseded` → `_delete_superseded_files`
   → `_delete_superseded_loop` on the post-processing side).
2. `edition_token_set()` normalises `.`, `_`, `-` and bracket characters to spaces before matching, so
   dotted and bracketed edition tags are recognised from either side.
3. A `--once` CLI flag now runs `run_once(config)` and exits, giving the documented foreground mode a
   real entry point; `--help` and the module docstrings were corrected.
4. `utils.parse_size_bytes()` is the single tolerant byte parser and is used by both the size and
   bitrate gates.
5. The `metaDL` pass uses `filter_type="last_activity"` (falling back to `added_on` when 0), matching the
   stalled pass and the documented semantics.
6. The dead regex was deleted.

## Verification gap

Round 10's fixes were applied **after** the last reviewer pass and were never re-reviewed. They are
covered by the project's own gates only:

- `uv run pytest` — 1539 passed, 100% statement coverage
- `uv run ruff check .` — clean
- `uv run ruff format --check .` — clean
- `uv run mypy .` — clean
- `uv run pre-commit run --all-files` — all hooks pass

A later review run should re-check findings 1–5 in particular: 1 and 2 touch irreversible deletion
paths, and 3 changes a CLI entry point.

## Context

Rounds 1–9 of this review found and fixed a much larger set of issues (data-loss paths in
post-processing and supersession, credential leakage into logs/DB/notifications, crafted-torrent path
traversal, unvalidated indexer-supplied IMDb IDs, non-atomic replacement of library files, and a long
tail of documentation drift). Those findings were fixed and re-reviewed in the rounds that followed,
so they are not repeated here; this ledger records only the residuals from the final round.
