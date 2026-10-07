import pytest

from quantra_mcp.errors import LocalValidationError
from quantra_mcp.schema.enums_generated import BusinessDayConvention, Calendar, TimeUnit
from quantra_mcp.tools.calendar import (
    CalendarOverride,
    advance_request,
    advance_summary,
    business_days_request,
    check_date,
    dates_summary,
    holidays_request,
    overrides_to_wire,
)


@pytest.mark.parametrize(
    "bad", ["2024-02-30", "2024/01/02", "20240102", "2024-1-2", "", "yesterday"]
)
def test_bad_dates_rejected_locally(bad: str) -> None:
    with pytest.raises(LocalValidationError) as exc:
        check_date(bad, "start_date")
    assert exc.value.problems[0]["path"] == "/start_date"


def test_good_date_passes() -> None:
    assert check_date("2024-02-29", "d") == "2024-02-29"


def test_holidays_request_is_explicit_and_override_free_by_default() -> None:
    body = holidays_request(Calendar.TARGET, "2024-04-29", "2024-06-21")
    assert body == {
        "calendar": "TARGET",
        "start_date": "2024-04-29",
        "end_date": "2024-06-21",
        "include_weekends": False,
    }


def test_overrides_serialized_and_dates_checked() -> None:
    ov = [
        CalendarOverride(
            calendar=Calendar.TARGET, added_holidays=["2024-06-14"], removed_holidays=["2024-05-01"]
        )
    ]
    assert overrides_to_wire(ov) == [
        {"calendar": "TARGET", "added_holidays": ["2024-06-14"], "removed_holidays": ["2024-05-01"]}
    ]
    assert overrides_to_wire([CalendarOverride(calendar=Calendar.UnitedKingdom)]) == [
        {"calendar": "UnitedKingdom"}
    ]
    with pytest.raises(LocalValidationError) as exc:
        overrides_to_wire(
            [CalendarOverride(calendar=Calendar.TARGET, added_holidays=["2024-13-01"])]
        )
    assert exc.value.problems[0]["path"] == "/calendar_overrides/0/added_holidays/0"


def test_business_days_and_advance_requests() -> None:
    assert business_days_request(
        Calendar.UnitedStates, "2024-05-01", "2024-05-03", False, True
    ) == {
        "calendar": "UnitedStates",
        "start_date": "2024-05-01",
        "end_date": "2024-05-03",
        "include_start": False,
        "include_end": True,
    }
    assert advance_request(
        Calendar.TARGET,
        "2024-05-01",
        -3,
        TimeUnit.Months,
        BusinessDayConvention.ModifiedFollowing,
        True,
    ) == {
        "calendar": "TARGET",
        "date": "2024-05-01",
        "tenor_number": -3,
        "tenor_unit": "Months",
        "convention": "ModifiedFollowing",
        "end_of_month": True,
    }


def test_summaries_are_selections() -> None:
    assert dates_summary({"dates": ["a", "b", "c"], "count": 3}) == {
        "count": 3,
        "first": "a",
        "last": "c",
    }
    assert dates_summary({"dates": []}) == {"count": 0}
    assert dates_summary("nope") is None
    assert advance_summary({"input_date": "x", "advanced_date": "y", "calendar": "TARGET"}) == {
        "input_date": "x",
        "advanced_date": "y",
    }
