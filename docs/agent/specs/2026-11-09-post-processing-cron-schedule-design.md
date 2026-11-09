# Post-processing cron schedule design

**Status:** Approved (2026-11-09)
**Scope:** configuration, scheduling and documentation. No change to post-processing copy logic.

## Problem

Post-processing copies completed torrents from the download location into the media library. The copy saturates
disk IO for its duration, which causes playback of movies that are already in the library to buffer. The copy runs on
a fixed interval (`schedule.post_processing.schedule_time_mins`, default 5), so it can start at any hour, including
during viewing hours.

Operators want to confine post-processing to chosen windows - for example 00:00-06:00 - using standard crontab
syntax, configured in YAML only (no CLI surface), while keeping today's behaviour for anyone who does not configure
a window.

## Goals

- Confine post-processing runs to operator-defined hours, using standard 5-field crontab expressions.
- Express windows per weekday, including different windows for weekdays and weekends.
- Preserve existing behaviour exactly for an unmodified pre-upgrade config.
- Fail loudly at config load when an expression is invalid, ambiguous or can never fire.
- Keep the change out of `post_processor.py`: the copy path itself does not change.

## Non-goals

- Cron scheduling for the `acquisition` or `queue_management` tasks.
- IO throttling (`ionice`, bandwidth caps) inside the copy path.
- Changing the copy, supersession or deletion behaviour of post-processing.
- Schedule control from the CLI.

## Current state

- `src/movarr/scheduler.py` runs a `BackgroundScheduler` (APScheduler 3.11.2, already a runtime dependency) with one
  `interval` job per enabled task: `search`, `queue_management`, `post_processing`. Jobs use `max_instances=1` and
  `coalesce=True`; `run_on_start` sets `next_run_time=now`; `_log_next_run` reports `job.next_run_time`;
  `run_once()` is the single-pass path used by `movarr --once`.
- `src/movarr/config.py` defines `ScheduleTaskConfig` (`enabled`, `schedule_time_mins`, `run_on_start`) and
  `ScheduleConfig` with `acquisition`, `queue_management`, `post_processing`. Schema migrations are a sequential
  `MIGRATIONS` chain keyed by version, each rewriting the YAML file after a single backup; `_CONFIG_VERSION` is
  `2.24.0`.

## Semantics that constrain the design

Both were verified against the installed APScheduler 3.11.2.

1. **A 5-field expression is a conjunction.** The hour set applies to every selected weekday, so "23:00-07:59
   Monday-Friday and 00:00-05:59 Saturday/Sunday" cannot be expressed by one expression. It needs two, unioned.
   APScheduler provides `apscheduler.triggers.combining.OrTrigger` for the union.
2. **`CronTrigger.from_crontab` does not use POSIX day-of-week numbering.** `DayOfWeekField.get_value()` returns
   `dateval.weekday()`, i.e. Python's 0 = Monday, whereas crontab means 0 = Sunday. A textbook `* 23 * * 0`
   therefore fires on Monday. The day-of-week field must be translated before the trigger is built.
3. **Day-of-month and day-of-week combination differs.** POSIX cron ORs the two fields when both are restricted;
   APScheduler ANDs them. Restricted-vs-restricted expressions must be rejected rather than silently changing
   meaning.
4. **`from_crontab` rejects a wrong field count** (`@daily`, 6-field) with `ValueError`, and **accepts a
   never-firing expression** (`0 0 30 2 *`) whose next fire time is `None`. Validation must reject that case
   explicitly, otherwise the task is silently disabled.

## Configuration

### Schema

```yaml
schedule:
  timezone: "Australia/Sydney"     # optional; default "" = process/container local time
  acquisition:
    enabled: true
    schedule_time_mins: 30         # unchanged, interval trigger
  queue_management:
    enabled: true
    schedule_time_mins: 5          # unchanged, interval trigger
  post_processing:
    enabled: true
    run_on_start: true
    cron:                          # string or list; union of windows
      - "* 0-7,23 * * 1-5"         # Mon-Fri 00:00-07:59 and 23:00-23:59
      - "* 0-5    * * 0,6"         # Sat/Sun 00:00-05:59
```

### Models

- `ScheduleConfig.timezone: str = ""`. Empty means the process timezone. A non-empty value must be a valid IANA
  name. It is resolved once and passed to every cron trigger. It does not affect the interval triggers used by
  `acquisition` and `queue_management`.
- New `PostProcessingScheduleConfig(BaseModel)`: `enabled: bool = True`, `run_on_start: bool = True`,
  `cron: str | list[str] = "*/5 * * * *"`. A string is normalised to a one-element list at validation time.
- `ScheduleConfig.post_processing: PostProcessingScheduleConfig`. `acquisition` and `queue_management` keep
  `ScheduleTaskConfig`, so `schedule_time_mins` remains meaningful for them and disappears from post-processing.

### Default

`cron` defaults to `"*/5 * * * *"`, which reproduces today's five-minute cadence. Scheduling is therefore on by
default; restricting it to out-of-hours is a matter of editing the expression.

## New module: `src/movarr/cron_schedule.py`

The crontab handling is isolated so it can be tested without a scheduler. Public surface (declared in `__all__`):

| Function | Responsibility |
| --- | --- |
| `resolve_timezone(name: str) -> ZoneInfo \| None` | Empty name returns `None` (process timezone); otherwise an IANA lookup that raises a config error naming the value. |
| `parse_expression(expr: str) -> str` | Validate and normalise one expression; returns the expression to hand to `CronTrigger.from_crontab`. Minute, hour, day-of-month and month fields are passed through verbatim. |
| `build_trigger(expressions: Sequence[str], tz: ZoneInfo \| None) -> CronTrigger \| OrTrigger` | Single normalised expression builds a `CronTrigger`; several build an `OrTrigger` union. |

### `parse_expression` rules

1. Reject anything that is not exactly 5 whitespace-separated fields (`@daily` and 6-field forms included), naming
   the expression.
2. Parse the day-of-week field into a **set of POSIX weekdays**: `*`, single values, comma lists, `a-b` ranges
   (including wrapping ranges such as `5-1`), and `range/step` or `*/step` forms. Day names (`mon`..`sun`) map to
   POSIX numbers.
3. Map each POSIX weekday `n` to APScheduler's numbering with `(n - 1) % 7`, so 0 and 7 become Sunday (6), and 1-6
   become Monday-Saturday (0-5). Re-emit as `*` when all seven days are selected, otherwise as a sorted
   comma-separated list.
4. Reject expressions where **both** the day-of-month and the day-of-week fields are restricted (neither is `*`),
   because POSIX ORs the two fields and APScheduler ANDs them. The error states that one of the two must be `*`.
5. Build a `CronTrigger` for the normalised expression and reject it when its next fire time is `None` (for example
   `0 0 30 2 *`), naming the expression and the timezone.

Helpers raise `ValueError` with the reason; the pydantic validators wrap them, so config loading fails with a
validation error naming the key path (`schedule.post_processing.cron` or `schedule.timezone`), the offending entry,
its index in the list and the reason. Validation happens at config load, before any qBittorrent, IMDb or database
work starts.

## Migration

`_CONFIG_VERSION` becomes `2.25.0` and `MIGRATIONS["2.24.0"] = _migrate_v224_to_v225`.

| Existing `schedule.post_processing.schedule_time_mins` | Migrated `cron` |
| --- | --- |
| 1 to 60 | the smallest divisor of 60 that is at least N: `*/5`, `*/10` for 7, `0 * * * *` for 60 |
| 61 to 1440 | the smallest divisor of 1440 that is at least N: `0 */2 * * *` for 90, `0 0 * * *` for 1380 |
| 1441 to 10080 | `0 0 * * 0` (weekly) |
| 10081 to 40320 (28 days) | `0 0 1 * *` (monthly; 28 days is the shortest month) |
| beyond 40320 | `0 0 1 1 *` (yearly) |
| missing, non-numeric, or not a whole number of minutes | `0 0 1 1 *` plus a WARNING naming the yearly cadence |
| zero or negative | rejected with a `ValueError` (the old field required `gt=0`) |

The migration removes `schedule.post_processing.schedule_time_mins`, preserves `enabled` and `run_on_start`, and
bumps `general.config_version`. The existing machinery writes `config.yml.bak.2.24.0` before the rewrite, and the
merged rewrite drops the removed key from the file. Re-running the migration on an already-migrated config is a
no-op because the version guard skips it.

Every band rounds *up* to the next cadence a 5-field crontab repeats evenly, so the migrated schedule never runs
more often than the interval did (calendar cadences above a week can drift by days, and are warned about). A
`cron` value already in the file is kept unless this version cannot build a trigger from it; an unusable value is
replaced by the converted interval, or by the yearly cadence when there is no interval to convert, and a warning
is logged.

## Scheduler wiring

- `_run_daemon` builds the post-processing trigger with `build_trigger(post_processing.cron, tz)`, keeping
  `max_instances=1` and `coalesce=True` so a copy in flight is never overlapped and an overrun coalesces into a
  single follow-up run.
- `acquisition` and `queue_management` continue to use the interval trigger and `_add_job_if_enabled` unchanged.
- `run_on_start: true` continues to set `next_run_time=now` unconditionally, including outside every configured
  window. This is deliberate: an operator who restarts movarr wants the task to run. The README states the
  consequence and points at `run_on_start: false` for strict out-of-hours operation.
- Startup logs the resolved timezone, each configured window, and the first next-fire time. `_log_next_run`
  continues to report `job.next_run_time` and works unchanged for cron and combined triggers.
- `run_once()` and therefore `movarr --once` bypass the windows: the single-pass path runs each enabled task once,
  logging explicitly that the post-processing window was bypassed.

## Post-processing behaviour unchanged

`run_post_processing`, supersession, `_process_one` and the delete paths are not modified. Only when the task is
started changes.

## Testing

Tests target the public API; no private helpers are called directly.

- `tests/unit/test_cron_schedule.py`: weekday translation (`0`, `7`, `1-5`, `0-6`, `5-1`, `*/2`, names, mixed
  lists), field-count rejections (`@daily`, 6 fields, 4 fields), day-of-month plus day-of-week rejection, the
  never-firing rejection, invalid timezone names, and single-versus-list trigger composition.
- `tests/unit/test_config.py`: defaults (`*/5 * * * *`, empty timezone), string-to-list normalisation, list
  validation error contents (key path, index), migration table for 5 / 1 / 60 / 1380 / 90 / absent, key removal,
  backup creation, idempotent re-run, and preservation of `enabled` / `run_on_start`.
- `tests/unit/test_scheduler.py`: post-processing registers a cron (and combined) trigger with the resolved
  timezone, `run_on_start` overrides the next fire time, `--once` runs post-processing outside the window and logs
  the bypass, interval triggers for the other two tasks are unchanged.
- Gates: `uv run pytest --cov=movarr --cov-fail-under=100`, `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run mypy .`, `uv run pre-commit run --all-files`.

## Documentation

- README `schedule` table: add `timezone`, replace `schedule_time_mins` with `cron` for `post_processing`, and say
  the value may be a list.
- README `Scheduler` section: post-processing is cron-driven, the other two tasks stay interval-driven; include the
  weekday/weekend example, the window-end convention (hour `0-5` means up to 05:59), the day-of-week numbering
  (0 and 7 are Sunday, names are safest), the day-of-month/day-of-week restriction, and the `TZ` caveat for
  containers.
- README `--once` row: note that it bypasses the post-processing window.

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Day-of-week numbering trap (0 = Monday in APScheduler) | Translation in `parse_expression`, covered by a translation table test. |
| Day-of-month plus day-of-week silently changing meaning | Hard validation error instead of a silent AND. |
| Never-firing expression silently disabling post-processing | Next-fire check at config load. |
| Lost interval when migrating (e.g. 90 minutes) | Whole-hour and 1-59 mappings; anything else defaults and logs a warning. |
| IO contention from a wide window (`*` minute field) | Documented: the minute field sets the cadence, `*/5` for every five minutes. |
| Daytime restart firing a copy inside viewing hours | Documented; `run_on_start: false` is the remedy. |

## Acceptance criteria

- With `cron: ["* 0-7,23 * * 1-5", "* 0-5 * * 0,6"]` and `timezone: "Australia/Sydney"`, no post-processing run
  starts outside Mon-Fri 00:00-07:59 and 23:00-23:59, or Sat/Sun 00:00-05:59, Sydney time.
- With `cron: "*/5 0-5 * * *"`, post-processing runs every five minutes from 00:00 to 05:59 and does not run
  outside that window.
- Runs never overlap, and a run that overruns coalesces into one follow-up.
- An unmodified pre-upgrade config migrates to `cron: "*/5 * * * *"` and behaves exactly as before.
- An invalid, ambiguous or never-firing expression stops startup with an error naming the key, the entry and the
  reason.
- `movarr --once` always runs post-processing and logs that the window was bypassed.
