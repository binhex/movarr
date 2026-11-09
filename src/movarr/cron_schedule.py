"""Crontab handling for movarr's scheduler.

APScheduler's ``CronTrigger.from_crontab`` accepts standard 5-field crontab text, but it numbers
weekdays from Monday (0) where crontab numbers them from Sunday (0), and it ANDs day-of-month with
day-of-week where POSIX ORs them.  Expressions are normalised here so the YAML config really is
standard crontab, and so an unusable expression fails at config load rather than at job registration.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["build_trigger", "describe_windows", "parse_expression", "resolve_timezone"]

FIELD_COUNT = 5
_WEEKDAY_NAMES = {"mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 0}
_ALL_WEEKDAYS = 7


def _weekday_number(token: str) -> int:
    """Return the POSIX weekday number (0-7, where 0 and 7 both mean Sunday) for *token*.

    Weekday names are matched case-insensitively, as crontab's own parser does.
    """
    name = _WEEKDAY_NAMES.get(token.lower())
    if name is not None:
        return name
    try:
        number = int(token)
    except ValueError as exc:
        raise ValueError(f"{token!r} is not a weekday name or number") from exc
    if not 0 <= number <= 7:
        raise ValueError(f"weekday {number} is outside 0-7")
    return number


def _step_value(raw_step: str, field_name: str) -> int:
    """Return the step from a ``/N`` suffix, rejecting non-numeric or non-positive steps."""
    try:
        step = int(raw_step)
    except ValueError as exc:
        raise ValueError(f"invalid {field_name} step {raw_step!r}") from exc
    if step < 1:
        raise ValueError(f"{field_name} step must be at least 1, got {step}")
    return step


def _weekday_bounds(chunk: str) -> tuple[int, int]:
    """Return the inclusive POSIX weekday bounds for one ``*``, ``a-b`` or single token."""
    if chunk == "*":
        return 0, 6
    if "-" in chunk:
        raw_low, _, raw_high = chunk.partition("-")
        return _weekday_number(raw_low), _weekday_number(raw_high)
    single = _weekday_number(chunk)
    return single, single


def _weekday_span(low: int, high: int) -> list[int]:
    """Return the weekday numbers from *low* to *high*, wrapping past Sunday.

    Bounds stay in the POSIX 0-7 space: folding 7 onto 0 happens after expansion, in
    :func:`_expand_weekdays`, so a range such as ``0-7`` covers every day instead of
    collapsing onto Sunday alone. A step applied to a wrapped range counts across the whole
    span (``4-1/2`` selects Thu, Sat and Mon), which is the concatenated-span reading. A step
    on a bare value (``1/2``) spans to Sunday, so it selects Mon, Wed, Fri and Sun, one day
    more than APScheduler's own ``first/step`` grammar (which stops at Saturday).
    """
    if low <= high:
        return list(range(low, high + 1))
    # A range starting at 7 keeps that 7 as the wrap anchor; dropping it would start the span
    # at 0 and shift the step parity, so ``7-1/2`` would select Sunday alone instead of Sun+Mon.
    # Ranges that start lower wrap through 0.
    anchor = [7] if low == 7 else []
    return [*range(low, 7), *anchor, *range(0, high + 1)]


def _expand_weekdays(field: str) -> set[int]:
    """Expand a day-of-week field into a set of POSIX weekday numbers.

    Supports ``*``, weekday names such as ``mon``, single values, comma lists, ``a-b`` ranges
    (including wrapping ranges such as ``5-1``), and ``*/step`` or ``a-b/step`` forms.
    """
    days: set[int] = set()
    for part in field.split(","):
        chunk = part.strip()
        if not chunk:
            raise ValueError(f"empty weekday entry in {field!r}")
        step = 1
        stepped = "/" in chunk
        if stepped:
            chunk, _, raw_step = chunk.partition("/")
            step = _step_value(raw_step, "weekday")
        low, high = _weekday_bounds(chunk)
        if stepped and high == low and "-" not in chunk:
            # A step on a bare value (``1/2``) runs to the end of the week; an explicit
            # ``5-5/2`` stays the single day it spells out.
            high = 7
        days.update(day % 7 for day in _weekday_span(low, high)[::step])
    return days


def _translate_weekdays(field: str) -> str:
    """Return *field* rewritten in APScheduler's Monday-based numbering."""
    # POSIX numbers 0 = Sunday; APScheduler numbers 0 = Monday, so the map is (n - 1) % 7.
    translated = {(day - 1) % _ALL_WEEKDAYS for day in _expand_weekdays(field)}
    if len(translated) == _ALL_WEEKDAYS:
        return "*"
    return ",".join(str(day) for day in sorted(translated))


def parse_expression(expression: str) -> str:
    """Validate a 5-field crontab *expression* and return the APScheduler form.

    The day-of-week field is translated from POSIX to APScheduler's Monday-based numbering, and an
    expression that restricts both day-of-month and day-of-week is rejected because POSIX ORs those
    fields where APScheduler ANDs them.  Field ranges outside the day-of-week are left for
    ``CronTrigger`` to validate when the trigger is built.
    """
    fields = expression.split()
    if len(fields) != FIELD_COUNT:
        raise ValueError(f"{expression!r} must have {FIELD_COUNT} fields, got {len(fields)}")
    minute, hour, day_of_month, month, day_of_week = fields
    translated = _translate_weekdays(day_of_week)
    # POSIX ORs the two day fields whenever both are textual restrictions, so an expression that
    # merely *covers* every day (``0-6``, ``1-31``) would silently narrow under APScheduler's AND.
    # Rejecting is the fail-closed reading, and the message tells the operator to leave one as '*'.
    if day_of_month != "*" and day_of_week != "*":
        raise ValueError(
            f"{expression!r} restricts both day-of-month and day-of-week; POSIX ORs them where "
            "APScheduler ANDs them, so leave one as '*'"
        )
    return " ".join([minute, hour, day_of_month, month, translated])


def resolve_timezone(name: str) -> ZoneInfo | None:
    """Return the :class:`ZoneInfo` for *name*, or ``None`` (process timezone) for an empty *name*.

    Raises:
        ValueError: If *name* cannot be looked up on this system, either because it is not a valid
            IANA name or because the timezone database (``tzdata``) is not installed.
    """
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(
            f"{name!r} is not a timezone this system knows; if the name is correct, install the "
            "timezone database (tzdata)"
        ) from exc


def build_trigger(expressions: Sequence[str], timezone: ZoneInfo | None) -> CronTrigger | OrTrigger:
    """Return the trigger for *expressions*: one window, or the union of several.

    Raises:
        ValueError: If an expression is invalid, if *expressions* is empty, or if the resulting
            trigger can never fire.
    """
    label = timezone.key if timezone is not None else "the process timezone"
    triggers: list[CronTrigger] = []
    for expression in expressions:
        normalised = parse_expression(expression)
        try:
            triggers.append(CronTrigger.from_crontab(normalised, timezone=timezone))
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"could not resolve the timezone {label!r}: {exc}; install the timezone database "
                "(tzdata), or set 'schedule.timezone' to a known zone"
            ) from exc
        except ValueError as exc:
            raise ValueError(f"{expression!r} is not a valid crontab expression for {label!r}: {exc}") from exc
    if not triggers:
        raise ValueError("at least one cron expression is required")
    trigger = triggers[0] if len(triggers) == 1 else OrTrigger(triggers)
    if trigger.get_next_fire_time(None, datetime.datetime.now(tz=datetime.UTC)) is None:
        raise ValueError(f"{', '.join(expressions)} never fires")
    return trigger


def describe_windows(expressions: Sequence[str]) -> str:
    """Return *expressions* joined for logging."""
    return " + ".join(expressions)
