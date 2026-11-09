# Post-processing Cron Schedule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sub-agents (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Confine movarr's post-processing task to operator-defined crontab windows, configured in YAML only, while preserving today's five-minute behaviour for existing configs.

**Architecture:** A new pure-helper module (`cron_schedule.py`) validates a 5-field crontab expression, normalises the day-of-week field from POSIX numbering (0 = Sunday) to APScheduler's numbering (0 = Monday), and builds one `CronTrigger` or an `OrTrigger` union for multiple windows. `ScheduleConfig` gains `timezone` and a dedicated post-processing model carrying `cron`; a 2.24.0 -> 2.25.0 config migration converts the old interval to an equivalent expression. The scheduler registers post-processing on that trigger instead of an interval and logs the bypass in single-pass mode.

**Tech Stack:** Python 3.12, pydantic v2, APScheduler 3.11.2 (`CronTrigger`, `OrTrigger`), pytest + pytest-mock, ruff, mypy, uv.

**Spec:** `docs/agent/specs/2026-11-09-post-processing-cron-schedule-design.md`

---

## Scope check

The spec covers a single subsystem (scheduling of one task plus its config plumbing). No split is needed.

## File structure

| File | Action | Responsibility |
| --- | --- | --- |
| `src/movarr/cron_schedule.py` | Create | Crontab validation, weekday translation, trigger composition, timezone resolution. No scheduler state. |
| `tests/unit/test_cron_schedule.py` | Create | Unit tests for that module only. |
| `src/movarr/config.py` | Modify | `PostProcessingScheduleConfig`, `ScheduleConfig.timezone`, load-time validation, migration 2.24.0 -> 2.25.0, `_CONFIG_VERSION`. |
| `tests/unit/test_config.py` | Modify | New config/migration tests; update 24 existing `2.24.0` expectations. |
| `src/movarr/scheduler.py` | Modify | Register post-processing on the cron trigger, shared job body, startup logging, `run_once` bypass log, module docstring. |
| `tests/unit/test_scheduler.py` | Modify | Trigger-wiring and bypass tests. |
| `README.md` | Modify | `--once` note, `schedule` key table, `Scheduler` section, feature bullets. |

Verified facts this plan relies on (do not re-derive):

- `CronTrigger.from_crontab` rejects a wrong field count with `ValueError`; `@daily` and 6-field forms both fail that check.
- `CronTrigger.from_crontab("0 0 30 2 *")` builds successfully and its `get_next_fire_time(...)` returns `None`; it must be rejected explicitly.
- `DayOfWeekField.get_value()` returns `dateval.weekday()`, so numeric day-of-week is Monday-based.
- `OrTrigger` lives at `apscheduler.triggers.combining.OrTrigger` and accepts a list of triggers.

---

### Task 1: Crontab expression parsing and weekday translation

**Files:**

- Create: `src/movarr/cron_schedule.py`
- Test: `tests/unit/test_cron_schedule.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_cron_schedule.py`:

```python
"""Unit tests for movarr.cron_schedule."""

from __future__ import annotations

import pytest

from movarr.cron_schedule import parse_expression


class TestParseExpressionWeekdays:
    """parse_expression translates POSIX weekday numbering to APScheduler's."""

    @pytest.mark.parametrize(
        ("field", "expected"),
        [
            ("*", "*"),
            ("0", "6"),
            ("7", "6"),
            ("1-5", "0,1,2,3,4"),
            ("0-6", "*"),
            ("5-1", "0,4,5,6"),
            ("0,6", "5,6"),
            ("mon-fri", "0,1,2,3,4"),
            ("sat,sun", "5,6"),
            ("*/2", "1,3,5,6"),
        ],
    )
    def test_translates_weekday_field(self, field: str, expected: str) -> None:
        """POSIX weekdays become APScheduler weekdays (0 = Monday)."""
        assert parse_expression(f"*/5 0-5 * * {field}") == f"*/5 0-5 * * {expected}"

    def test_other_fields_pass_through(self) -> None:
        """Minute, hour, day-of-month and month fields are untouched."""
        assert parse_expression("1,2 3-4 5 6 0") == "1,2 3-4 5 6 6"


class TestParseExpressionValidation:
    """parse_expression rejects expressions APScheduler would misread."""

    @pytest.mark.parametrize("expression", ["@daily", "*/5 0-5 * *", "*/5 0-5 * * * *"])
    def test_rejects_wrong_field_count(self, expression: str) -> None:
        """Only 5 fields are accepted; shorthands and 6-field forms are rejected."""
        with pytest.raises(ValueError, match="must have 5 fields"):
            parse_expression(expression)

    def test_rejects_day_of_month_with_day_of_week(self) -> None:
        """POSIX ORs the two day fields where APScheduler ANDs them."""
        with pytest.raises(ValueError, match="restricts both day-of-month and day-of-week"):
            parse_expression("0 0 1 * 1")

    def test_rejects_out_of_range_weekday(self) -> None:
        """Weekday numbers above 7 are rejected."""
        with pytest.raises(ValueError, match="outside 0-7"):
            parse_expression("0 0 * * 8")

    def test_rejects_unknown_weekday_name(self) -> None:
        """Non-numeric, non-name weekday tokens are rejected."""
        with pytest.raises(ValueError, match="not a weekday name or number"):
            parse_expression("0 0 * * funday")

    def test_rejects_empty_weekday_entry(self) -> None:
        """An empty list entry such as '1,,2' is rejected."""
        with pytest.raises(ValueError, match="empty weekday entry"):
            parse_expression("0 0 * * 1,,2")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_cron_schedule.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'movarr.cron_schedule'`

- [ ] **Step 3: Create the module with parsing only**

Create `src/movarr/cron_schedule.py`:

```python
"""Crontab handling for movarr's scheduler.

APScheduler's ``CronTrigger.from_crontab`` accepts standard 5-field crontab text, but it numbers
weekdays from Monday (0) where crontab numbers them from Sunday (0), and it ANDs day-of-month with
day-of-week where POSIX ORs them.  Expressions are normalised here so the YAML config really is
standard crontab, and so an unusable expression fails at config load rather than at job registration.
"""

from __future__ import annotations

__all__ = ["build_trigger", "describe_windows", "parse_expression", "resolve_timezone"]

FIELD_COUNT = 5
_WEEKDAY_NAMES = {"mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 0}
_ALL_WEEKDAYS = 7


def _weekday_number(token: str) -> int:
    """Return the POSIX weekday number (0 = Sunday) for *token*."""
    name = _WEEKDAY_NAMES.get(token)
    if name is not None:
        return name
    try:
        number = int(token)
    except ValueError as exc:
        raise ValueError(f"{token!r} is not a weekday name or number") from exc
    if number == 7:
        return 0
    if not 0 <= number <= 6:
        raise ValueError(f"weekday {number} is outside 0-7")
    return number


def _expand_weekdays(field: str) -> set[int]:
    """Expand a day-of-week field into a set of POSIX weekday numbers.

    Supports ``*``, single values, comma lists, ``a-b`` ranges (including wrapping ranges such as
    ``5-1``), and ``*/step`` or ``a-b/step`` forms.
    """
    days: set[int] = set()
    for part in field.split(","):
        chunk = part.strip()
        if not chunk:
            raise ValueError(f"empty weekday entry in {field!r}")
        step = 1
        if "/" in chunk:
            chunk, _, raw_step = chunk.partition("/")
            try:
                step = int(raw_step)
            except ValueError as exc:
                raise ValueError(f"invalid weekday step {raw_step!r}") from exc
            if step < 1:
                raise ValueError(f"weekday step must be at least 1, got {step}")
        if chunk == "*":
            low, high = 0, 6
        elif "-" in chunk:
            raw_low, _, raw_high = chunk.partition("-")
            low, high = _weekday_number(raw_low), _weekday_number(raw_high)
        else:
            low = high = _weekday_number(chunk)
        span = list(range(low, high + 1)) if low <= high else [*range(low, 7), *range(0, high + 1)]
        days.update(span[::step])
    return days


def _translate_weekdays(field: str) -> str:
    """Return *field* rewritten in APScheduler's Monday-based numbering."""
    # POSIX numbers 0 = Sunday; APScheduler numbers 0 = Monday, so the map is (n - 1) % 7.
    translated = {(day - 1) % _ALL_WEEKDAYS for day in _expand_weekdays(field)}
    if len(translated) == _ALL_WEEKDAYS:
        return "*"
    return ",".join(str(day) for day in sorted(translated))


def parse_expression(expression: str) -> str:
    """Validate a 5-field crontab *expression* and return the APScheduler form."""
    fields = expression.split()
    if len(fields) != FIELD_COUNT:
        raise ValueError(f"{expression!r} must have {FIELD_COUNT} fields, got {len(fields)}")
    minute, hour, day_of_month, month, day_of_week = fields
    if day_of_month != "*" and day_of_week != "*":
        raise ValueError(
            f"{expression!r} restricts both day-of-month and day-of-week; POSIX ORs them where "
            "APScheduler ANDs them, so leave one as '*'"
        )
    return " ".join([minute, hour, day_of_month, month, _translate_weekdays(day_of_week)])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_cron_schedule.py -v`
Expected: 16 passed (10 parametrised weekday cases + 5 validation classes' cases + pass-through)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check --fix src/movarr/cron_schedule.py tests/unit/test_cron_schedule.py
uv run ruff format src/movarr/cron_schedule.py tests/unit/test_cron_schedule.py
uv run mypy src/movarr/cron_schedule.py
git add src/movarr/cron_schedule.py tests/unit/test_cron_schedule.py
git commit -m "feat: add crontab expression parsing with POSIX weekday translation"
```

---

### Task 2: Timezone resolution and trigger composition

**Files:**

- Modify: `src/movarr/cron_schedule.py`
- Test: `tests/unit/test_cron_schedule.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_cron_schedule.py`, and extend the existing imports at the top of the file so
the import block reads:

```python
import datetime
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger

from movarr.cron_schedule import build_trigger, describe_windows, parse_expression, resolve_timezone
```

```python
class TestResolveTimezone:
    """resolve_timezone turns a config string into a ZoneInfo or None."""

    def test_empty_name_means_process_timezone(self) -> None:
        """An empty name defers to the process timezone."""
        assert resolve_timezone("") is None

    def test_valid_name_returns_zoneinfo(self) -> None:
        """A valid IANA name resolves to the matching zone."""
        assert resolve_timezone("Australia/Sydney") == ZoneInfo("Australia/Sydney")

    def test_invalid_name_raises(self) -> None:
        """An unknown zone name raises a ValueError naming the problem."""
        with pytest.raises(ValueError, match="not a valid IANA timezone"):
            resolve_timezone("Mars/Olympus")


class TestBuildTrigger:
    """build_trigger composes one trigger from one or more expressions."""

    def test_single_expression_returns_cron_trigger(self) -> None:
        """One expression builds a plain CronTrigger."""
        trigger = build_trigger(["*/5 0-5 * * *"], ZoneInfo("Australia/Sydney"))
        assert isinstance(trigger, CronTrigger)

    def test_multiple_expressions_return_or_trigger(self) -> None:
        """Several expressions build a union, so windows can differ per weekday."""
        trigger = build_trigger(["* 0-7,23 * * 1-5", "* 0-5 * * 0,6"], ZoneInfo("Australia/Sydney"))
        assert isinstance(trigger, OrTrigger)

    def test_next_fire_time_respects_expression(self) -> None:
        """The trigger fires at the hour the expression selects."""
        trigger = build_trigger(["0 0 * * *"], ZoneInfo("Australia/Sydney"))
        now = datetime.datetime(2026, 11, 10, 14, 20, tzinfo=datetime.UTC)
        fire = trigger.get_next_fire_time(None, now)
        assert fire is not None
        assert (fire.hour, fire.minute) == (0, 0)

    def test_never_firing_expression_raises(self) -> None:
        """A syntactically valid but impossible date is rejected."""
        with pytest.raises(ValueError, match="never fires"):
            build_trigger(["0 0 30 2 *"], None)

    def test_empty_expression_list_raises(self) -> None:
        """At least one expression is required."""
        with pytest.raises(ValueError, match="at least one cron expression"):
            build_trigger([], None)


class TestDescribeWindows:
    """describe_windows renders expressions for log lines."""

    def test_joins_expressions(self) -> None:
        """Several windows are joined with a plus sign."""
        assert describe_windows(["* 0-5 * * *", "* 23 * * 1-5"]) == "* 0-5 * * * + * 23 * * 1-5"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_cron_schedule.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_trigger' from 'movarr.cron_schedule'`

- [ ] **Step 3: Implement the three functions**

Replace the whole imports block at the top of `src/movarr/cron_schedule.py` (everything from
`from __future__ import annotations` down to the `if TYPE_CHECKING:` block) with exactly:

```python
from __future__ import annotations

import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger

if TYPE_CHECKING:
    from collections.abc import Sequence
```

Then append these functions to the module:

```python
def resolve_timezone(name: str) -> ZoneInfo | None:
    """Return the :class:`ZoneInfo` for *name*, or ``None`` for the process timezone."""
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"{name!r} is not a valid IANA timezone name") from exc


def build_trigger(expressions: Sequence[str], timezone: ZoneInfo | None) -> CronTrigger | OrTrigger:
    """Return the trigger for *expressions*: one window, or the union of several.

    Raises:
        ValueError: If an expression is invalid, or if the resulting trigger can never fire.
    """
    triggers: list[CronTrigger] = []
    for expression in expressions:
        normalised = parse_expression(expression)
        try:
            triggers.append(CronTrigger.from_crontab(normalised, timezone=timezone))
        except ValueError as exc:
            raise ValueError(f"{expression!r} is not a valid crontab expression: {exc}") from exc
    if not triggers:
        raise ValueError("at least one cron expression is required")
    trigger = triggers[0] if len(triggers) == 1 else OrTrigger(triggers)
    if trigger.get_next_fire_time(None, datetime.datetime.now(tz=datetime.UTC)) is None:
        raise ValueError(f"{', '.join(expressions)} never fires")
    return trigger


def describe_windows(expressions: Sequence[str]) -> str:
    """Return *expressions* joined for logging."""
    return " + ".join(expressions)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_cron_schedule.py -v`
Expected: all tests pass (25 total)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check --fix src/movarr/cron_schedule.py tests/unit/test_cron_schedule.py
uv run ruff format src/movarr/cron_schedule.py tests/unit/test_cron_schedule.py
uv run mypy src/movarr/cron_schedule.py
git add src/movarr/cron_schedule.py tests/unit/test_cron_schedule.py
git commit -m "feat: build cron triggers with timezone resolution and window unions"
```

---

### Task 3: Config models for timezone and post-processing cron

**Files:**

- Modify: `src/movarr/config.py:482-496` (the `ScheduleTaskConfig` / `ScheduleConfig` block)
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_config.py`:

```python
class TestPostProcessingCronConfig:
    """schedule.post_processing.cron and schedule.timezone validation."""

    def test_cron_defaults_to_every_five_minutes(self) -> None:
        """Default cron reproduces the old five-minute interval."""
        assert Config().schedule.post_processing.cron == ["*/5 * * * *"]

    def test_timezone_defaults_to_empty(self) -> None:
        """Empty timezone means the process timezone."""
        assert Config().schedule.timezone == ""

    def test_string_cron_is_normalised_to_list(self) -> None:
        """A single expression is stored as a one-element list."""
        config = Config(schedule={"post_processing": {"cron": "*/5 0-5 * * *"}})
        assert config.schedule.post_processing.cron == ["*/5 0-5 * * *"]

    def test_list_cron_is_accepted(self) -> None:
        """Several expressions are accepted and preserved in order."""
        expressions = ["* 0-7,23 * * 1-5", "* 0-5 * * 0,6"]
        config = Config(schedule={"post_processing": {"cron": expressions}})
        assert config.schedule.post_processing.cron == expressions

    def test_invalid_expression_names_the_entry(self) -> None:
        """An invalid expression reports the key path and index."""
        with pytest.raises(ValidationError, match=r"cron\[0\]"):
            Config(schedule={"post_processing": {"cron": ["@daily"]}})

    def test_never_firing_expression_is_rejected(self) -> None:
        """An impossible date is rejected at config load."""
        with pytest.raises(ValidationError, match="never fires"):
            Config(schedule={"post_processing": {"cron": ["0 0 30 2 *"]}})

    def test_day_of_month_with_day_of_week_is_rejected(self) -> None:
        """Restricted day-of-month plus day-of-week is ambiguous and rejected."""
        with pytest.raises(ValidationError, match="restricts both day-of-month and day-of-week"):
            Config(schedule={"post_processing": {"cron": ["0 0 1 * 1"]}})

    def test_non_string_entry_is_rejected(self) -> None:
        """List entries must be strings."""
        with pytest.raises(ValidationError, match=r"cron\[1\] must be a string"):
            Config(schedule={"post_processing": {"cron": ["* * * * *", 5]}})

    def test_empty_list_is_rejected(self) -> None:
        """An empty list would schedule nothing and is rejected."""
        with pytest.raises(ValidationError, match="at least one expression"):
            Config(schedule={"post_processing": {"cron": []}})

    def test_invalid_timezone_is_rejected(self) -> None:
        """An unknown zone name is rejected and names the key."""
        with pytest.raises(ValidationError, match="schedule.timezone"):
            Config(schedule={"timezone": "Mars/Olympus"})

    def test_post_processing_has_no_interval_key(self) -> None:
        """schedule_time_mins no longer exists on the post-processing block."""
        assert not hasattr(Config().schedule.post_processing, "schedule_time_mins")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_config.py::TestPostProcessingCronConfig -v`
Expected: FAIL - `ValidationError` for unexpected `cron` key, and `AttributeError` for `timezone`

- [ ] **Step 3: Implement the models**

In `src/movarr/config.py`, update the pydantic import line:

```python
from pydantic import BaseModel, Field, field_validator, model_validator
```

Add this import below the pydantic import:

```python
from movarr.cron_schedule import build_trigger, resolve_timezone
```

Replace the `ScheduleTaskConfig` / `ScheduleConfig` definitions with:

```python
class ScheduleTaskConfig(BaseModel):
    """A single interval-scheduled task configuration."""

    enabled: bool = True
    schedule_time_mins: int = Field(default=30, gt=0, description="Interval in minutes (must be > 0).")
    run_on_start: bool = True


class PostProcessingScheduleConfig(BaseModel):
    """Cron-scheduled post-processing configuration."""

    enabled: bool = True
    run_on_start: bool = True
    cron: list[str] = Field(default_factory=lambda: ["*/5 * * * *"])

    @field_validator("cron", mode="before")
    @classmethod
    def _normalise_cron(cls, value: object) -> object:
        """Accept a single 5-field expression or a list of them.

        The raw expressions are kept: validation happens on the parent
        ``ScheduleConfig``, where the timezone is available, and the startup log
        shows the operator's own text rather than a rewritten form.
        """
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            raise ValueError("cron must be a 5-field crontab expression or a list of them")
        if not value:
            raise ValueError("cron must contain at least one expression")
        for index, entry in enumerate(value):
            if not isinstance(entry, str):
                raise ValueError(f"cron[{index}] must be a string, got {type(entry).__name__}")
        return value


class ScheduleConfig(BaseModel):
    """Schedule intervals and cron windows for the three background tasks."""

    timezone: str = ""
    acquisition: ScheduleTaskConfig = Field(default_factory=lambda: ScheduleTaskConfig(schedule_time_mins=30))
    queue_management: ScheduleTaskConfig = Field(default_factory=lambda: ScheduleTaskConfig(schedule_time_mins=5))
    post_processing: PostProcessingScheduleConfig = Field(default_factory=PostProcessingScheduleConfig)

    @model_validator(mode="after")
    def _validate_cron_schedule(self) -> ScheduleConfig:
        """Reject an unknown timezone or an unusable cron expression at load time."""
        try:
            timezone = resolve_timezone(self.timezone)
        except ValueError as exc:
            raise ValueError(f"schedule.timezone: {exc}") from exc
        for index, expression in enumerate(self.post_processing.cron):
            try:
                build_trigger([expression], timezone)
            except ValueError as exc:
                raise ValueError(f"schedule.post_processing.cron[{index}]: {exc}") from exc
        return self
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_config.py::TestPostProcessingCronConfig -v`
Expected: 11 passed

- [ ] **Step 5: Run the whole config suite to catch regressions**

Run: `uv run pytest tests/unit/test_config.py -q`
Expected: all pass. No existing test references the old post-processing interval key (the only
`schedule_time_mins` consumers are `scheduler.py` and the acquisition/queue_management defaults), so this task
should not break anything else. If a failure appears, read it before continuing.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check --fix src/movarr/config.py tests/unit/test_config.py
uv run ruff format src/movarr/config.py tests/unit/test_config.py
uv run mypy src/movarr/config.py
git add src/movarr/config.py tests/unit/test_config.py
git commit -m "feat: add schedule timezone and post-processing cron config"
```

---

### Task 4: Config migration 2.24.0 -> 2.25.0

**Files:**

- Modify: `src/movarr/config.py:19` (`_CONFIG_VERSION`), `:388-400` (add the new migration), `:425-451` (`MIGRATIONS`)
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_config.py`:

```python
class TestV224Migration:
    """Migration 2.24.0 -> 2.25.0 converts the post-processing interval to cron."""

    def _write(self, tmp_path: Path, body: str) -> Path:
        config_path = tmp_path / "config.yml"
        config_path.write_text(body)
        return config_path

    @pytest.mark.parametrize(
        ("minutes", "expected"),
        [(5, "*/5 * * * *"), (1, "*/1 * * * *"), (60, "0 */1 * * *"), (1380, "0 */23 * * *")],
    )
    def test_interval_becomes_an_equivalent_cron_expression(self, tmp_path: Path, minutes: int, expected: str) -> None:
        """Expressible intervals keep their cadence."""
        config_path = self._write(
            tmp_path,
            "general:\n  config_version: '2.24.0'\n"
            "schedule:\n  post_processing:\n"
            "    enabled: true\n"
            f"    schedule_time_mins: {minutes}\n"
            "    run_on_start: false\n",
        )

        config = load_config(config_path)

        assert config.schedule.post_processing.cron == [expected]

    def test_unexpressible_interval_falls_back_and_warns(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """An interval that cron cannot express falls back to five minutes and warns."""
        config_path = self._write(
            tmp_path,
            "general:\n  config_version: '2.24.0'\n"
            "schedule:\n  post_processing:\n"
            "    schedule_time_mins: 90\n",
        )
        mock_warning = mocker.patch("movarr.config.logger.warning")

        config = load_config(config_path)

        assert config.schedule.post_processing.cron == ["*/5 * * * *"]
        assert any("90" in str(call) for call in mock_warning.call_args_list)

    def test_interval_key_is_removed_from_disk(self, tmp_path: Path) -> None:
        """The obsolete key is not left behind in the rewritten file."""
        config_path = self._write(
            tmp_path,
            "general:\n  config_version: '2.24.0'\n"
            "schedule:\n  post_processing:\n    schedule_time_mins: 5\n",
        )

        load_config(config_path)

        on_disk = yaml.safe_load(config_path.read_text())
        assert "schedule_time_mins" not in on_disk["schedule"]["post_processing"]
        assert on_disk["schedule"]["post_processing"]["cron"] == "*/5 * * * *"

    def test_enabled_and_run_on_start_are_preserved(self, tmp_path: Path) -> None:
        """Enabled flags survive the migration untouched."""
        config_path = self._write(
            tmp_path,
            "general:\n  config_version: '2.24.0'\n"
            "schedule:\n  post_processing:\n"
            "    enabled: false\n"
            "    schedule_time_mins: 5\n"
            "    run_on_start: false\n",
        )

        config = load_config(config_path)

        assert config.schedule.post_processing.enabled is False
        assert config.schedule.post_processing.run_on_start is False

    def test_version_is_bumped(self, tmp_path: Path) -> None:
        """The rewritten file records the new schema version."""
        config_path = self._write(tmp_path, "general:\n  config_version: '2.24.0'\n")

        load_config(config_path)

        assert yaml.safe_load(config_path.read_text())["general"]["config_version"] == "2.25.0"

    def test_backup_of_the_old_file_is_created(self, tmp_path: Path) -> None:
        """The pre-migration file is backed up before rewriting."""
        config_path = self._write(tmp_path, "general:\n  config_version: '2.24.0'\n")

        load_config(config_path)

        assert (tmp_path / "config.yml.bak.2.24.0").exists()

    def test_migration_is_idempotent(self, tmp_path: Path) -> None:
        """Re-loading a migrated file leaves the cron expression unchanged."""
        config_path = self._write(
            tmp_path,
            "general:\n  config_version: '2.24.0'\n"
            "schedule:\n  post_processing:\n    schedule_time_mins: 7\n",
        )

        first = load_config(config_path)
        second = load_config(config_path)

        assert first.schedule.post_processing.cron == ["*/7 * * * *"]
        assert second.schedule.post_processing.cron == ["*/7 * * * *"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_config.py::TestV224Migration -v`
Expected: FAIL - the post-processing block has no `cron` key and no migration runs (`2.24.0` is the current version)

- [ ] **Step 3: Implement the migration**

In `src/movarr/config.py`, change the version constant:

```python
_CONFIG_VERSION = "2.25.0"
```

Add this constant next to it:

```python
_DEFAULT_POST_PROCESSING_CRON = "*/5 * * * *"
```

Add these functions immediately after `_migrate_v223_to_v224`:

```python
def _interval_to_cron(minutes: object) -> str:
    """Return a cron expression with the same cadence as *minutes*, or the default.

    Cron cannot express every interval: minutes up to 59 map to ``*/N``, whole
    hours up to 23 map to ``0 */K``, and anything else (for example 90) falls
    back to the default with a warning rather than silently changing the cadence.
    """
    if isinstance(minutes, bool) or not isinstance(minutes, int):
        logger.warning(
            "Post-processing schedule_time_mins {!r} is not an integer; using '{}'.",
            minutes,
            _DEFAULT_POST_PROCESSING_CRON,
        )
        return _DEFAULT_POST_PROCESSING_CRON
    if 1 <= minutes <= 59:
        return f"*/{minutes} * * * *"
    if minutes % 60 == 0 and 1 <= minutes // 60 <= 23:
        return f"0 */{minutes // 60} * * *"
    logger.warning(
        "Post-processing schedule_time_mins {} cannot be expressed as a cron expression; using '{}'.",
        minutes,
        _DEFAULT_POST_PROCESSING_CRON,
    )
    return _DEFAULT_POST_PROCESSING_CRON


def _migrate_v224_to_v225(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.24.0 -> v2.25.0: the post-processing interval becomes a cron expression."""
    post_processing = _section(_section(raw, "schedule"), "post_processing")
    existing = post_processing.pop("schedule_time_mins", None)
    post_processing["cron"] = (
        _interval_to_cron(existing) if existing is not None else _DEFAULT_POST_PROCESSING_CRON
    )
    _section(raw, "general")["config_version"] = "2.25.0"
    return raw
```

Register it in `MIGRATIONS` by adding the final entry:

```python
    "2.24.0": _migrate_v224_to_v225,
```

- [ ] **Step 4: Update the existing version expectations**

Every existing assertion that expects the current version must move with the bump:

```bash
sed -i 's/"2\.24\.0"/"2.25.0"/g' tests/unit/test_config.py
grep -rn '"2\.24\.0"' tests/unit/test_config.py | grep -v "bak\.\|#"
```

Expected: no output from the `grep` (all quoted expectations updated; comment text and the `.bak.2.24.0` path in `TestV224Migration` must remain).

- [ ] **Step 5: Run the migration and config suites**

Run: `uv run pytest tests/unit/test_config.py -q`
Expected: all pass, including the previously failing `schedule_time_mins` tests

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check --fix src/movarr/config.py tests/unit/test_config.py
uv run ruff format src/movarr/config.py tests/unit/test_config.py
uv run mypy src/movarr/config.py
git add src/movarr/config.py tests/unit/test_config.py
git commit -m "feat: migrate post-processing interval to a cron expression"
```

---

### Task 5: Register post-processing on the cron trigger

**Files:**

- Modify: `src/movarr/scheduler.py:1-27` (docstring and imports), `:142-210` (`_run_daemon`), `:252-285` (`_add_job_if_enabled`)
- Test: `tests/unit/test_scheduler.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_scheduler.py`. `datetime`, `Any`, `MockerFixture`, `Config` and `_run_daemon` are
already imported at the top of that file; add only these:

```python
from zoneinfo import ZoneInfo

from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
```

```python
class TestRunDaemonCronTrigger:
    """post_processing is registered on a cron trigger built from the config."""

    def _daemon(self, mocker: MockerFixture, config: Config) -> Any:
        mocker.patch("movarr.scheduler.Database")
        mocker.patch("movarr.scheduler._connect_qbt")
        mock_sched = mocker.patch("movarr.scheduler.BackgroundScheduler").return_value
        mocker.patch("movarr.scheduler.threading.Event", return_value=mocker.MagicMock())
        _run_daemon(config)
        return mock_sched

    def _trigger(self, mock_sched: Any) -> object:
        for call in mock_sched.add_job.call_args_list:
            if call.kwargs.get("id") == "post_processing":
                return call.kwargs["trigger"]
        raise AssertionError("post_processing job was not registered")

    def test_single_expression_registers_a_cron_trigger(self, mocker: MockerFixture) -> None:
        """One window builds a CronTrigger rather than an interval trigger."""
        config = Config()
        config.schedule.post_processing.cron = ["* 0-5 * * *"]

        assert isinstance(self._trigger(self._daemon(mocker, config)), CronTrigger)

    def test_multiple_expressions_register_an_or_trigger(self, mocker: MockerFixture) -> None:
        """Several windows are unioned so weekdays and weekends can differ."""
        config = Config()
        config.schedule.post_processing.cron = ["* 0-7,23 * * 1-5", "* 0-5 * * 0,6"]

        assert isinstance(self._trigger(self._daemon(mocker, config)), OrTrigger)

    def test_trigger_uses_the_configured_timezone(self, mocker: MockerFixture) -> None:
        """The trigger evaluates the expression in schedule.timezone."""
        config = Config()
        config.schedule.timezone = "Australia/Sydney"
        trigger = self._trigger(self._daemon(mocker, config))

        assert isinstance(trigger, CronTrigger)
        assert trigger.timezone == ZoneInfo("Australia/Sydney")

    def test_cron_job_keeps_single_instance_and_coalescing(self, mocker: MockerFixture) -> None:
        """A copy in flight is never overlapped and an overrun coalesces."""
        config = Config()
        mock_sched = self._daemon(mocker, config)

        for call in mock_sched.add_job.call_args_list:
            if call.kwargs.get("id") == "post_processing":
                assert call.kwargs["max_instances"] == 1
                assert call.kwargs["coalesce"] is True
                return
        raise AssertionError("post_processing job was not registered")

    def test_disabled_post_processing_registers_no_trigger(self, mocker: MockerFixture) -> None:
        """A disabled task registers no job at all."""
        config = Config()
        config.schedule.post_processing.enabled = False
        registered = {c.kwargs.get("id") for c in self._daemon(mocker, config).add_job.call_args_list}

        assert "post_processing" not in registered


class TestRunOnceCronBypass:
    """Single-pass mode ignores the cron windows and says so."""

    def test_logs_that_the_window_is_bypassed(self, mocker: MockerFixture) -> None:
        """run_once announces the bypass before running post-processing."""
        mocker.patch("movarr.scheduler.Database")
        mocker.patch("movarr.scheduler.QBittorrentClient")
        mocker.patch("movarr.scheduler.run_search")
        mocker.patch("movarr.scheduler.run_queue_management")
        mock_pp = mocker.patch("movarr.scheduler.run_post_processing")
        mock_info = mocker.patch("movarr.scheduler.logger.info")

        run_once(Config())

        messages = [str(call.args[0]) for call in mock_info.call_args_list]
        assert any("bypassed" in message for message in messages)
        mock_pp.assert_called_once()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_scheduler.py -k "CronTrigger and not signal" -v`
Expected: FAIL - `post_processing` currently registers an interval trigger, so `call.kwargs["trigger"]` is the string `"interval"`; the bypass test fails because no such log line exists

- [ ] **Step 3: Update the module imports and docstring**

In `src/movarr/scheduler.py`, replace the docstring opening paragraph:

```python
"""APScheduler-based task scheduler for movarr.

Three tasks run on configurable schedules:
  1. **search** — poll Jackett, filter results, add to qBittorrent, persist to DB.
  2. **queue_management** — delete metaDL/stalledDL torrents that exceed wait times.
  3. **post_processing** — copy completed torrents to the library.

``acquisition`` and ``queue_management`` run on fixed intervals;
``post_processing`` runs on a cron schedule (see :mod:`movarr.cron_schedule`), so it
can be confined to out-of-hours windows.

The scheduler runs in **daemon mode** — a long-running foreground process
(suitable for systemd/Docker, writes a PID file) with one job per enabled task.
``run_once()`` executes each enabled task a single time, ignoring the cron windows;
it is the foreground/test helper, and it logs and exits when nothing is enabled.
"""
```

Update the imports block to:

```python
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import SchedulerNotRunningError
from loguru import logger

from movarr.config import PID_FILENAME
from movarr.cron_schedule import build_trigger, describe_windows, resolve_timezone
from movarr.database import Database
from movarr.post_processor import run_post_processing
from movarr.qbittorrent import QBittorrentClient
from movarr.queue_manager import run_queue_management
from movarr.search import run_search

if TYPE_CHECKING:
    import types
    from collections.abc import Callable

    from apscheduler.triggers.combining import OrTrigger
    from apscheduler.triggers.cron import CronTrigger

    from movarr.config import Config, PostProcessingScheduleConfig, ScheduleTaskConfig
```

- [ ] **Step 4: Replace `_run_daemon`**

Replace the body from the `search_mins = ...` line through the `post_processing` `_add_job_if_enabled(...)` call with:

```python
    search_mins = config.schedule.acquisition.schedule_time_mins
    qm_mins = config.schedule.queue_management.schedule_time_mins
    post_processing_trigger = build_trigger(
        config.schedule.post_processing.cron,
        resolve_timezone(config.schedule.timezone),
    )

    _add_job_if_enabled(
        scheduler,
        _task_search,
        "search",
        config.schedule.acquisition,
        config,
        qbt,
        db,
        name="Jackett search + filter + add",
    )
    _add_job_if_enabled(
        scheduler,
        _task_queue_management,
        "queue_management",
        config.schedule.queue_management,
        config,
        qbt,
        db,
    )
    _add_cron_job_if_enabled(
        scheduler,
        _task_post_processing,
        "post_processing",
        config.schedule.post_processing,
        post_processing_trigger,
        config,
        qbt,
        db,
        name="Post-processing (copy to library)",
    )
```

Replace the start-up log line with:

```python
    logger.info(
        "Scheduler started — search every {}m, queue_management every {}m, post_processing on cron '{}' ({}).",
        search_mins,
        qm_mins,
        describe_windows(config.schedule.post_processing.cron),
        config.schedule.timezone or "system timezone",
    )
```

- [ ] **Step 5: Add the shared job body and the cron registration**

Replace the whole `_add_job_if_enabled` function with these three functions:

```python
def _job_callable(
    scheduler: BackgroundScheduler,
    task_fn: Callable[..., None],
    job_id: str,
    config: Config,
    qbt: QBittorrentClient,
    db: Database,
) -> Callable[[], None]:
    """Return the job body shared by interval and cron registration."""

    def _job() -> None:
        task_fn(config, qbt, db)
        try:
            _log_next_run(scheduler, job_id)
        except Exception:
            logger.exception("Failed to log next {} run time.", job_id)

    return _job


def _add_job_if_enabled(
    scheduler: BackgroundScheduler,
    task_fn: Callable[..., None],
    job_id: str,
    schedule_cfg: ScheduleTaskConfig,
    config: Config,
    qbt: QBittorrentClient,
    db: Database,
    *,
    name: str | None = None,
) -> None:
    """Register *task_fn* as a recurring interval job unless *schedule_cfg.enabled* is False."""
    if not schedule_cfg.enabled:
        logger.info("Scheduled {} is disabled — skipping.", job_id)
        return

    extra: dict = {"name": name} if name is not None else {}
    scheduler.add_job(
        _job_callable(scheduler, task_fn, job_id, config, qbt, db),
        trigger="interval",
        minutes=schedule_cfg.schedule_time_mins,
        id=job_id,
        max_instances=1,
        coalesce=True,
        **_next_run_kwargs(schedule_cfg.run_on_start),
        **extra,
    )


def _add_cron_job_if_enabled(
    scheduler: BackgroundScheduler,
    task_fn: Callable[..., None],
    job_id: str,
    schedule_cfg: PostProcessingScheduleConfig,
    trigger: CronTrigger | OrTrigger,
    config: Config,
    qbt: QBittorrentClient,
    db: Database,
    *,
    name: str | None = None,
) -> None:
    """Register *task_fn* on a cron *trigger* unless *schedule_cfg.enabled* is False."""
    if not schedule_cfg.enabled:
        logger.info("Scheduled {} is disabled — skipping.", job_id)
        return

    extra: dict = {"name": name} if name is not None else {}
    scheduler.add_job(
        _job_callable(scheduler, task_fn, job_id, config, qbt, db),
        trigger=trigger,
        id=job_id,
        max_instances=1,
        coalesce=True,
        **_next_run_kwargs(schedule_cfg.run_on_start),
        **extra,
    )
```

- [ ] **Step 6: Add the `run_once` bypass log**

In `run_once`, replace the post-processing branch with:

```python
    if config.schedule.post_processing.enabled:
        logger.info(
            "Post-processing cron window bypassed in single-pass mode ({}).",
            describe_windows(config.schedule.post_processing.cron),
        )
        _task_post_processing(config, qbt, db)
    else:
        logger.info("Post-processing task disabled; skipping.")
```

- [ ] **Step 7: Run the scheduler suite**

Run: `uv run pytest tests/unit/test_scheduler.py -v`
Expected: all pass, including the pre-existing `TestRunDaemon`, `TestRunDaemonRunOnStart` and `TestLogNextRun` classes

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff check --fix src/movarr/scheduler.py tests/unit/test_scheduler.py
uv run ruff format src/movarr/scheduler.py tests/unit/test_scheduler.py
uv run mypy src/movarr/scheduler.py
git add src/movarr/scheduler.py tests/unit/test_scheduler.py
git commit -m "feat: schedule post-processing on a cron trigger"
```

---

### Task 6: README documentation

**Files:**

- Modify: `README.md:33-36` (feature bullet), `:97` (`--once` row), `:143-152` (`schedule` section), `:382-396` (`Scheduler` section)

- [ ] **Step 1: Update the feature bullet**

Replace:

```markdown
- **Three independent schedulers** — acquisition, queue management, and post-processing each run on their own
  configurable interval.
```

with:

```markdown
- **Three independent schedulers** — acquisition and queue management run on their own configurable intervals;
  post-processing runs on a crontab expression so it can be confined to out-of-hours windows.
```

- [ ] **Step 2: Update the `--once` row**

Replace the `--once` table row with:

```markdown
| `--once` | Run each enabled scheduler task once, then exit (foreground mode). Post-processing runs regardless of its cron window. | `false` |
```

- [ ] **Step 3: Rewrite the `schedule` section**

Replace the whole `### schedule` section (heading through the `Tasks:` line) with:

````markdown
### `schedule`

`acquisition` and `queue_management` share one schedule shape:

| Key | Description | Default |
| --- | ----------- | ------- |
| `enabled` | Enable or disable this task. | `true` |
| `schedule_time_mins` | Interval in minutes between runs. | `30` (acquisition), `5` (queue_management) |
| `run_on_start` | Run this task immediately when movarr starts, before the first interval elapses. | `true` |

`post_processing` is scheduled by crontab instead:

| Key | Description | Default |
| --- | ----------- | ------- |
| `enabled` | Enable or disable this task. | `true` |
| `run_on_start` | Run this task immediately when movarr starts, even outside the cron windows. Set to `false` for strict out-of-hours operation. | `true` |
| `cron` | A 5-field crontab expression, or a list of them whose windows are unioned. | `"*/5 * * * *"` |

`schedule.timezone` sets the IANA timezone the expressions are evaluated in (default: the container's
timezone, which is often UTC). Example — copy only overnight on weekdays, and only before 06:00 at weekends:

```yaml
schedule:
  timezone: "Australia/Sydney"
  post_processing:
    enabled: true
    cron:
      - "* 0-7,23 * * 1-5"   # Mon-Fri 00:00-07:59 and 23:00-23:59
      - "* 0-5    * * 0,6"   # Sat/Sun 00:00-05:59
```

Tasks: `acquisition`, `queue_management`, `post_processing`.
````

- [ ] **Step 4: Rewrite the `Scheduler` section**

Replace everything from the `## Scheduler` heading through the "Any task can be disabled independently..." line with:

````markdown
## Scheduler

movarr runs three schedulers concurrently on one APScheduler instance. Acquisition and queue management use a
**fixed interval**; the next fire time is computed from the previous *scheduled* time, so drift does not
accumulate. Post-processing uses a **crontab expression**, evaluated in `schedule.timezone`.

All three jobs run with `max_instances=1`, so a run in flight is never overlapped, and `coalesce=True`, so a run
that overruns its slot is followed by one coalesced run rather than a burst of catch-ups.

| Task | Schedule | Config key |
| ---- | -------- | ---------- |
| Acquisition | every 30 min | `schedule.acquisition` |
| Queue management | every 5 min | `schedule.queue_management` |
| Post-processing | `*/5 * * * *` | `schedule.post_processing` |

Post-processing is the task that saturates disk IO, so it is the one worth pinning to quiet hours. Two details
matter when writing its expression:

- **Hours are inclusive, minutes are not.** Hour `0-5` runs from 00:00 to 05:59; the window ends when the hour
  field stops matching. The minute field sets the cadence inside the window (`*/5` = every five minutes).
- **Weekday numbers follow crontab, not APScheduler.** `0` and `7` are Sunday, `1` is Monday, `5` is Friday.
  Day names (`mon`..`sun`) are accepted and are the safest choice. An expression that restricts *both*
  day-of-month and day-of-week is rejected, because crontab would OR the two fields while the scheduler ANDs
  them.

A restart with `run_on_start: true` runs post-processing immediately, even outside the window. Set
`run_on_start: false` if restarts must also respect it. `--once` always runs post-processing and logs that the
window was bypassed.

Any task can be disabled independently by setting `enabled: false` in its schedule block.
````

- [ ] **Step 5: Verify the docs mention every new key**

```bash
grep -n "schedule.timezone\|cron:" README.md
grep -n "schedule_time_mins" README.md
```

Expected: the first command finds the new `timezone` key and both `cron:` examples; the second finds
`schedule_time_mins` only in the acquisition/queue_management table.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: document post-processing cron scheduling"
```

---

### Task 7: Full quality gate

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite with the coverage floor**

Run: `uv run pytest -q --cov-fail-under=100`
Expected: all tests pass, coverage 100%, no failures

- [ ] **Step 2: Run the remaining gates**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pre-commit run --all-files
```

Expected: all four clean

- [ ] **Step 3: Sanity-check the migration by hand**

```bash
printf "general:\n  config_version: '2.24.0'\nschedule:\n  post_processing:\n    schedule_time_mins: 5\n" > /tmp/movarr-cron-check.yml
uv run movarr --config-path /tmp/movarr-cron-check.yml --test
cat /tmp/movarr-cron-check.yml
rm -f /tmp/movarr-cron-check.yml
```

Expected: config validates; the file now contains `cron: '*/5 * * * *'` under `schedule.post_processing`, no
`schedule_time_mins` there, and `config_version: 2.25.0`

- [ ] **Step 4: Confirm the tree is clean**

```bash
git status --short
git log --oneline -6
```

Expected: clean tree, six commits (one per task) on top of `940751f`

---

## Self-review notes

- Spec coverage: config surface (Task 3), `cron_schedule` module and its three functions (Tasks 1-2), migration
  and version bump (Task 4), scheduler wiring + `run_on_start` + startup logging + `--once` bypass (Task 5),
  README (Task 6), gates and acceptance checks (Task 7). The spec's "out of scope" items have no tasks, by design.
- Names used consistently throughout: `PostProcessingScheduleConfig`, `parse_expression`, `resolve_timezone`,
  `build_trigger`, `describe_windows`, `_add_cron_job_if_enabled`, `_job_callable`,
  `_DEFAULT_POST_PROCESSING_CRON`, `_migrate_v224_to_v225`, `_interval_to_cron`.
- Deliberate deviation from the spec's test command: `pyproject.toml` carries `--cov-fail-under=95`; Task 7 adds
  `--cov-fail-under=100` explicitly, which is stricter and matches the spec's intent.
