# Code review — residual findings (round 10 of 10)

**Branch:** `main`
**Base:** `HEAD` (`f75655b`) + uncommitted working-tree changes (9 files)
**Round reached:** 10
**Circuit breaker cap:** 10 (`CIRCUIT_BREAKER_ROUNDS`)
**Reviewer models:** `opencode-go/deepseek-v4.1-flash:max` (Reviewer A) and
`opencode-go/glm-5.3-flash:max` (Reviewer B)

Round 10 was the final round allowed by the cap. It reported the findings below. Every one was
**fixed**, but by definition of the cap they were **not re-reviewed** by either reviewer
afterwards.

## Findings

| # | Severity | Location | Description | Status |
| --- | -------- | -------- | ------------- | -------- |
| 1 | MEDIUM | `src/movarr/post_processor.py:247` (`_staging_sweep_roots`) | The round-10 symlink-retention change called `path.is_symlink()` outside any guard. On the supported 3.12 floor `is_symlink()` propagates `EACCES`/`ESTALE`/`EIO` (3.13+ swallows them), so a root that cannot be stat'ed aborted `_sweep_staging_leftovers` → `run_post_processing` before the per-torrent loop, skipping every completed torrent for that cycle once per sweep interval — the same failure mode round 5 fixed for the `is_dir()` probe. | applied, not independently re-reviewed |
| 2 | LOW | `src/movarr/file_utils.py:497, 518, 537, 539, 558, 605` | The new staging/swap diagnostic lines passed `tmp`/`dst` to the logger raw. A destination name carrying an unpaired surrogate would make loguru drop the record (its default `catch=True`), losing the very diagnostic that explains the abort. Reviewer B judged the path unreachable; the fix removes the disagreement at no cost. | applied, not independently re-reviewed |
| 3 | LOW | `src/movarr/file_utils.py:642-648` (`_perform_copy`), `:621-624` (`_do_copy`) | Contract drift after round 8 widened `_replace_copy` to catch `ValueError`: `_perform_copy` still promised "`None` on any copy or verification failure", while an unencodable source/destination path raises `UnicodeEncodeError` past it (converted to `False` by the caller). | applied, not independently re-reviewed |

### What was changed for each finding

1. The probe is guarded — `try: is_link = path.is_symlink() except (OSError, ValueError):
   is_link = False` — keeping the root on a failed probe (a redundant walk, never lost
   coverage). New test: `test_sweep_roots_survive_an_unstatable_symlink_probe` (patches
   `Path.is_symlink` to raise, asserts the root survives).
2. Every remaining staging/swap diagnostic now logs `_display(tmp)` / `_display(dst)`:
   "Could not stage …", "Could not replace …", "Replacement of … did not take effect",
   "Staged copy … disappeared while swapping in …".
3. `_perform_copy` states that an unencodable path raises `ValueError` past it and that
   `_replace_copy` converts it to `False`; `_do_copy`'s `Raises:` list gained `ValueError`.

## Verification gap

Round 10's fixes were applied **after** the last reviewer pass and were never re-reviewed. They
are covered by the project's own gates only:

- `uv run --frozen pytest` — 1806 passed, 100.00% statement coverage
- `uv run --frozen ruff check .` — clean
- `uv run --frozen ruff format --check .` — clean
- `uv run --frozen mypy .` — clean
- `markdownlint` — clean on every changed markdown file

A later review run should re-check finding 1 in particular: it guards the sweep entry point
that the whole change exists to add.

## Context

Rounds 1–9 of this review found and fixed a much larger set of issues in the staging-cleanup
change: the `NAME_MAX` guard measured characters instead of bytes (multibyte names still
overflowed), a `0.0` sentinel against `time.monotonic()` suppressed the first sweep after every
boot, an unguarded `flock` could raise out of the replace path leaving a descriptor and a
staging file behind, a cleanup probe could steal a brand-new staging file's lock and abort the
copy, read-only/write-only/immutable leftovers were unreclaimable through the probe, `Path.is_dir`
`ValueError`/`OSError` could abort a whole pass, the sweep ran on nearly every pass, its roots
could miss the configured copy destinations, a `staging-name` collision could be written into
(no `O_EXCL`), and several log lines echoed untrusted paths. Those were fixed and re-reviewed in
the rounds that followed, so they are not repeated here; this ledger records only the residuals
from the final round.

The corresponding deferred ledger
(`docs/agent/reviews/2026-11-12-unraid-shfs-replace-deferred.md`) records the closure of items
1–5 by this change.
