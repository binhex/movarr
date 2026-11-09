"""Unit tests for movarr.cron_schedule."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger

from movarr.cron_schedule import build_trigger, describe_windows, parse_expression, resolve_timezone

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


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
            ("0-7", "*"),
            ("0-7/2", "1,3,5,6"),
            ("5-1", "0,4,5,6"),
            ("0,6", "5,6"),
            ("mon-fri", "0,1,2,3,4"),
            ("MON-FRI", "0,1,2,3,4"),
            ("sat,sun", "5,6"),
            ("*/2", "1,3,5,6"),
            ("mon-fri/2", "0,2,4"),
            ("4-1/2", "0,3,5"),
            ("7-1/2", "0,6"),
            ("1/2", "0,2,4,6"),
            ("5-5/1", "4"),
            ("3-3/2", "2"),
        ],
    )
    def test_translates_weekday_field(self, field: str, expected: str) -> None:
        """POSIX weekdays become APScheduler weekdays (0 = Monday)."""
        assert parse_expression(f"*/5 0-5 * * {field}") == f"*/5 0-5 * * {expected}"

    def test_other_fields_pass_through(self) -> None:
        """Minute, hour, day-of-month and month fields are untouched."""
        assert parse_expression("1,2 3-4 * 6 0") == "1,2 3-4 * 6 6"


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

    @pytest.mark.parametrize(
        "expression", ["0 0 1 * 0-6", "0 0 */1 * 1", "0 3 1-31 * 1", "0 3 1-31/1 * 1", "0 3 1,2-31 * 1"]
    )
    def test_rejects_both_day_fields_even_when_one_covers_every_day(self, expression: str) -> None:
        """POSIX ORs two textual restrictions, so a covering field would silently narrow."""
        with pytest.raises(ValueError, match="restricts both day-of-month and day-of-week"):
            parse_expression(expression)

    def test_unreadable_day_of_month_token_still_fails_closed(self) -> None:
        """A token APScheduler cannot parse is treated as a restriction."""
        with pytest.raises(ValueError, match="restricts both day-of-month and day-of-week"):
            parse_expression("0 3 L * 1")

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
        with pytest.raises(ValueError, match="not a timezone this system knows"):
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
        """The trigger fires at the instant the expression selects in that timezone."""
        trigger = build_trigger(["0 0 * * *"], ZoneInfo("Australia/Sydney"))
        now = datetime.datetime(2026, 11, 10, 14, 20, tzinfo=datetime.UTC)
        fire = trigger.get_next_fire_time(None, now)

        assert fire is not None
        assert fire.astimezone(datetime.UTC) == datetime.datetime(2026, 11, 11, 13, 0, tzinfo=datetime.UTC)

    def test_never_firing_expression_raises(self) -> None:
        """A syntactically valid but impossible date is rejected."""
        with pytest.raises(ValueError, match="never fires"):
            build_trigger(["0 0 30 2 *"], None)

    def test_empty_expression_list_raises(self) -> None:
        """At least one expression is required."""
        with pytest.raises(ValueError, match="at least one cron expression"):
            build_trigger([], None)


class TestFireInstants:
    """The translated numbers must produce the right calendar day, not just the right string."""

    def test_sunday_expression_fires_on_sunday(self) -> None:
        """POSIX 0 (Sunday) is APScheduler 6, so the next fire is a Sunday at midnight."""
        trigger = build_trigger(["0 0 * * 0"], ZoneInfo("UTC"))
        now = datetime.datetime(2026, 11, 9, 12, 0, tzinfo=datetime.UTC)  # a Monday

        fire = trigger.get_next_fire_time(None, now)

        assert fire is not None
        assert fire.astimezone(datetime.UTC) == datetime.datetime(2026, 11, 15, 0, 0, tzinfo=datetime.UTC)
        assert fire.astimezone(datetime.UTC).strftime("%A") == "Sunday"

    def test_union_waits_for_the_next_window(self) -> None:
        """An instant between windows waits for the evening window, not the next tick."""
        trigger = build_trigger(["* 0-7,23 * * 1-5", "* 0-5 * * 0,6"], ZoneInfo("Australia/Sydney"))
        now = datetime.datetime(2026, 11, 9, 9, 0, tzinfo=ZoneInfo("Australia/Sydney"))  # Monday 09:00

        fire = trigger.get_next_fire_time(None, now)

        assert fire is not None
        assert fire.astimezone(ZoneInfo("Australia/Sydney")) == datetime.datetime(
            2026, 11, 9, 23, 0, tzinfo=ZoneInfo("Australia/Sydney")
        )


class TestUnionTolerance:
    """A union is accepted when any member can fire."""

    def test_one_never_firing_member_does_not_reject_the_union(self) -> None:
        """OrTrigger ignores a member that can never fire."""
        trigger = build_trigger(["0 0 30 2 *", "* * * * *"], None)

        assert isinstance(trigger, OrTrigger)


class TestProcessTimezoneFailure:
    """A broken process timezone surfaces as a ValueError, not a raw KeyError."""

    def test_zone_info_not_found_becomes_a_value_error(self, mocker: MockerFixture) -> None:
        """tzlocal raises ZoneInfoNotFoundError; callers only catch ValueError."""
        from zoneinfo import ZoneInfoNotFoundError

        mocker.patch(
            "movarr.cron_schedule.CronTrigger.from_crontab",
            side_effect=ZoneInfoNotFoundError("no timezone database"),
        )

        with pytest.raises(ValueError, match="could not resolve the timezone .the process timezone."):
            build_trigger(["* * * * *"], None)

    def test_named_timezone_is_reported_by_name(self) -> None:
        """A bad expression names the zone, not its repr."""
        with pytest.raises(ValueError, match="for .Australia/Sydney."):
            build_trigger(["60 0 * * *"], ZoneInfo("Australia/Sydney"))


class TestDescribeWindows:
    """describe_windows renders expressions for log lines."""

    def test_joins_expressions(self) -> None:
        """Several windows are joined with a plus sign."""
        assert describe_windows(["* 0-5 * * *", "* 23 * * 1-5"]) == "* 0-5 * * * + * 23 * * 1-5"


class TestParseExpressionWeekdayStepErrors:
    """Malformed weekday steps are rejected with a specific message."""

    def test_rejects_non_numeric_step(self) -> None:
        """A non-numeric step such as '*/x' is rejected."""
        with pytest.raises(ValueError, match="invalid weekday step"):
            parse_expression("0 0 * * */x")

    def test_rejects_zero_step(self) -> None:
        """A step below 1 would select no day and is rejected."""
        with pytest.raises(ValueError, match="must be at least 1"):
            parse_expression("0 0 * * */0")


class TestBuildTriggerFieldRange:
    """A field outside its crontab range is rejected by the trigger builder."""

    def test_out_of_range_minute_is_rejected(self) -> None:
        """Minute 60 is outside 0-59 and surfaces as an invalid expression."""
        with pytest.raises(ValueError, match="is not a valid crontab expression"):
            build_trigger(["60 0 * * *"], None)
