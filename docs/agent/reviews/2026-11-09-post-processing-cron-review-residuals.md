# Code review — residual findings (round 10 of 10)

**Branch:** `main`
**Base:** `origin/main` (`adeeed0`)
**Round reached:** 10
**Circuit breaker cap:** 10 (`CIRCUIT_BREAKER_ROUNDS`)
**Reviewer models:** `opencode-go/deepseek-v4.1-flash:max` (Reviewer A) and `opencode-go/glm-5.3-flash:max` (Reviewer B)

## Findings

| # | Severity | Location | Description | Status |
| --- | -------- | -------- | ------------- | -------- |
| 7 | LOW | `src/movarr/config.py` (`create_default_config`) | On a host whose local zone cannot be resolved, first-run bootstrap raises inside `_default_config_dict()` before writing the file, so the recommended remedy (set `schedule.timezone`) cannot be applied through the tool. | not fixed — left as a documented limitation; the error names `tzdata` and `TZ`, and no file is left behind |

## Context

Items 1–6 of this ledger were fixed and verified in the review rounds that followed: the
no-timezone-database "usable cron" gate, the missing disabled-task validation test, the
`_weekday_span` comment, `_negative_minutes` for quoted negative intervals, the reason-bearing
replacement warning, and the scheduler docstring. Only item 7 remains open, by design.
