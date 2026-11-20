# Release review — residual findings (round 10 of 10)

**Branch:** `main`
**Commit:** `c69eba1` + uncommitted working-tree changes (30 files)
**Round reached:** 10
**Circuit breaker cap:** 10 (`CIRCUIT_BREAKER_ROUNDS`)
**Reviewer models:** `opencode-go/deepseek-v4.1-flash:max` (Reviewer A) and
`opencode-go/glm-5.3-flash:max` (Reviewer B)

Round 10 was the final round allowed by the cap. It reported the findings below. Every one
was **fixed**, but by definition of the cap they were **not re-reviewed** by either reviewer
afterwards.

## Findings

| # | Severity | Location | Description | Status |
| --- | ------------ | ---------- | ------------- | -------- |
| 1 | MEDIUM | `src/movarr/post_processor.py:697` (`_copy_phase`), consumed at `:342` and `:1080` | Regression introduced by round 10's per-file name sanitisation: `_copy_files` returned sanitised names in `copied_fnames`, but `_copy_phase` still passed the **raw** `canonical_fname` on. `_maybe_delete_superseded`'s guard `canonical_fname not in copied_fnames` then returned silently, so for a primary file whose name carried a control character `delete_lower_quality` was a no-op — after `db.mark_completed` had already run, leaving the record `Completed` and the inferior library file undeleted. | applied, not independently re-reviewed |
| 2 | LOW | `src/movarr/file_utils.py:419`, `:96-110`; `src/movarr/post_processor.py:792-800`, `:1423-1425` | The rounds 7–10 log-sanitisation sweep missed the remaining torrent-controlled path sites: the copy-failure `FileNotFoundError` warning, `delete_file`'s four log lines, `_file_excluded_by_rules`' three DEBUG lines (`rel_path`/`folder_part`), and `_delete_superseded_loop`'s deletion/failure pair (`lib_path`). A torrent or pre-existing library name carrying `\n`/ESC could still forge a log line or drive the terminal. | applied, not independently re-reviewed |
| 3 | LOW | `src/movarr/config.py:1237` (`_strip_and_write`) | The atomic rewrite preserved the config's mode and symlink identity but not its ownership: `shutil.copymode` copies permission bits only, so a process that can write a config it does not own (root, per the README's capability/sudo deployment) swapped in a root-owned file. A later start by the unprivileged user then failed to open it. | applied, not independently re-reviewed |
| 4 | LOW | `src/movarr/post_processor.py:206` (`_safe_path_component`), used by `_build_dst_dir` | Round 10 sanitised destination *file* basenames but not the per-movie destination *directory*: `_RE_PATH_UNSAFE` strips filesystem metacharacters and NUL but not C0/C1, so a metadata title could create a library folder whose name carried control characters, which were then echoed by the dst-dir log sites. | applied, not independently re-reviewed |

### What was changed for each finding

1. `_copy_phase` now sanitises the canonical name at the point it is derived
   (`strip_control_chars(_canonical_filename(...))`), so the write set and the supersession
   guard compare the same on-disk name. New test: `TestCanonicalNameSanitisation`
   (canonical branch returns the sanitised name in `copied_fnames`).
2. Every remaining site now logs a display-safe path: `file_utils._display()` is applied to the
   copy-failure warning and all four `delete_file` lines; `strip_control_chars()` wraps
   `rel_path`, `folder_part` and `lib_path` in `post_processor`. Source addressing stays raw.
3. After `copymode`, `_strip_and_write` copies ownership with a suppressed `os.chown` (an
   unprivileged process cannot chown, and that must not fail the rewrite). Tests:
   `TestAtomicRewriteOwnership` (chown called once; a `PermissionError` from chown tolerated).
4. `_safe_path_component` now strips control characters after removing filesystem
   metacharacters (order matters: NUL must be removed, not mapped to a space). New test asserts
   no `\n`/ESC survives and the component stays trimmed.

## Verification gap

Round 10's fixes were applied **after** the last reviewer pass and were never re-reviewed. They
are covered by the project's own gates only:

- `uv run --frozen pytest` — 1755 passed, 100.00% statement coverage
- `uv run --frozen ruff check .` — clean
- `uv run --frozen ruff format --check .` — clean
- `uv run --frozen mypy .` — clean
- `uv lock --check` — clean
- `markdownlint` — clean on every changed markdown file

A later review run should re-check findings 1 and 3 in particular: 1 touches the irreversible
`delete_lower_quality` path, and 3 changes file attributes during a config rewrite.

## Context

Rounds 1–9 of this review series found and fixed a much larger set of issues (dependency
manifest/lock disagreement, secret-bearing values in a config warning, log-format and
control-character injection across the parser/qBittorrent/copy/delete sinks, a poster-URL SSRF
guard bypassable through percent-encoded authorities, hostless-authority and legacy IPv4
spellings, the bootstrap error's dead-end remedy, non-atomic and mode-losing config rewrites,
negative numeric thresholds, and several missing negative-path tests). Those were fixed and
re-reviewed in the rounds that followed, so they are not repeated here; this ledger records only
the residuals from the final round.

Two recorded limitations remain live and are deliberately unchanged (they are not findings of
this change): the no-timezone-database bootstrap limitation in
`docs/agent/reviews/2026-11-09-post-processing-cron-review-residuals.md` item 7, and the five
Unraid/staging-cleanup deferrals in
`docs/agent/reviews/2026-11-12-unraid-shfs-replace-deferred.md` items 1–5.
