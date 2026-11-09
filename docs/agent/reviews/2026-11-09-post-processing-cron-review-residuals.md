# Code review — residual findings (round 10 of 10)

**Branch:** `main`
**Base:** `origin/main` (change uncommitted; `adeeed0` is the last commit)
**Round reached:** 10
**Circuit breaker cap:** 10 (`CIRCUIT_BREAKER_ROUNDS`)
**Reviewer models:** `opencode-go/deepseek-v4.1-flash:max` (Reviewer A) and `opencode-go/glm-5.3-flash:max` (Reviewer B)

Round 10 was the final round allowed by the cap. It reported the findings below. Every one was **addressed**, but by
definition of the cap they were **not re-reviewed** by either reviewer afterwards.

## Findings

| # | Severity | Location | Description | Status |
| --- | --- | --- | --- | --- |
| 1 | MEDIUM | `src/movarr/config.py` (`_cron_problem`) | On a host with no timezone database the "usable cron" gate treated every value as unusable and persisted the replacement, contradicting its own docstring claim of a parse-only fallback. | applied, not independently re-reviewed |
| 2 | MEDIUM | `tests/unit/test_config.py` | No test covered the documented rule that an invalid expression is rejected even when `post_processing.enabled` is false. | applied, not independently re-reviewed |
| 3 | LOW | `src/movarr/cron_schedule.py` (`_weekday_span` comment) | The comment described the consequence of dropping the `7` wrap anchor as "clipped to Monday onwards"; the real effect is Sunday only (step parity shifts). | applied, not independently re-reviewed |
| 4 | LOW | `src/movarr/config.py` (`_interval_to_cron`) | A quoted negative fractional interval (`"-5.5"`) fell through to the yearly fallback instead of being rejected like every other negative form. | applied, not independently re-reviewed |
| 5 | LOW | `src/movarr/config.py` (`_migrate_v224_to_v225`) | The warning for an unusable `cron` value did not include the reason, so a POSIX-OR expression dropped by the day-field rule looked like a typo. | applied, not independently re-reviewed |
| 6 | LOW | `src/movarr/scheduler.py` (`_add_cron_job_if_enabled`) | The docstring claimed defence in depth for any post-validation mutation, but only the disabled path is guarded. | applied, not independently re-reviewed |
| 7 | LOW | `src/movarr/config.py` (`create_default_config`) | On a host whose local zone cannot be resolved, first-run bootstrap raises inside `_default_config_dict()` before writing the file, so the recommended remedy (set `schedule.timezone`) cannot be applied through the tool. | not fixed — left as a documented limitation; the error message names `tzdata` and the record is carried here |

### What was changed for findings 1–6

1. `_cron_problem()` now returns the reason a value is unusable, and its no-timezone-database branch really does fall
   back to `parse_expression`, logging that only structural checks are possible. `_is_usable_cron()` is a thin
   wrapper over it.
2. Added `TestPostProcessingCronConfig::test_disabled_task_still_rejects_an_invalid_expression`, asserting a
   `ValidationError` naming `cron[0]` for `enabled: false` with `cron: ["@daily"]`.
3. Reworded the `_weekday_span` comment: without the anchor the span starts at 0 and the step parity shifts, so
   `7-1/2` would select Sunday alone.
4. Added `_negative_minutes()`, which recognises negative numbers, numeric strings and floats, so every negative
   spelling takes the rejection path.
5. The replacement warning now interpolates `_cron_problem(existing_cron)`, so operators see the actual cause.
6. Reworded the scheduler docstring: the enabled path is fail-fast by design; only the disabled path is skipped.

## Verification gap

Round 10's fixes were applied **after** the last reviewer pass and were never re-reviewed. They are covered by the
project's own gates only:

- `uv run pytest -q` — 1664 passed, 100% statement coverage
- `uv run ruff check .` — clean
- `uv run ruff format --check .` — clean
- `uv run mypy .` — clean

A later review run should re-check findings 1 and 2 in particular: 1 changes the migration's fallback behaviour on
hosts without a timezone database, and 2 is the regression test for unconditional validation.

## Context

Rounds 1–9 found and fixed a much larger set of issues in this change: the POSIX day-of-week translation
(`0-7`, `7-…` wrap ranges, steps on bare values), the day-of-month/day-of-week OR-vs-AND guard, the never-fires
check, the interval-to-cron cadence mapping (including a silent 288× speed-up and later a silent yearly downgrade
of the default), the "usable cron" gate across absent/null/empty/malformed/never-firing values, timezone error
messages and tzdata handling, startup next-fire logging, and several weakened or vacuous test assertions. Those were
fixed and re-reviewed in the rounds that followed, so they are not repeated here.
