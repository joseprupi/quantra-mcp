"""Every shipped example and every calendar-tool request validates against the vendored spec."""

from typing import Any

from quantra_mcp.resources import EXAMPLE_ENDPOINTS, example_names, load_example
from quantra_mcp.schema.enums_generated import BusinessDayConvention, Calendar, TimeUnit
from quantra_mcp.schema.validate import validate_request
from quantra_mcp.tools.calendar import (
    CalendarOverride,
    advance_request,
    business_days_request,
    holidays_request,
)

OVERRIDES = [
    CalendarOverride(
        calendar=Calendar.TARGET, added_holidays=["2024-06-14"], removed_holidays=["2024-05-01"]
    ),
    CalendarOverride(calendar=Calendar.UnitedKingdom, added_holidays=["2024-07-05"]),
]


def test_every_example_has_an_endpoint_and_validates() -> None:
    names = example_names()
    assert names == ["sofr-bootstrap-request", "sofr-ois-swap-request"]
    for name in names:
        endpoint = EXAMPLE_ENDPOINTS[name]
        assert validate_request(endpoint, load_example(name)) == [], name


def test_examples_are_byte_identical_to_the_published_files() -> None:
    import hashlib
    from pathlib import Path

    from quantra_mcp.resources import EXAMPLES_DIR

    published = Path("/root/quantraweb/public/blog/data")
    if not published.is_dir():  # only meaningful on the box that has the website checkout
        return
    for name in example_names():
        ours = hashlib.sha256((EXAMPLES_DIR / f"{name}.json").read_bytes()).hexdigest()
        theirs = hashlib.sha256((published / f"{name}.json").read_bytes()).hexdigest()
        assert ours == theirs, name


def _cases() -> list[tuple[str, dict[str, Any]]]:
    return [
        ("/calendar-holidays", holidays_request(Calendar.TARGET, "2024-04-29", "2024-06-21")),
        (
            "/calendar-holidays",
            holidays_request(Calendar.TARGET, "2024-04-29", "2024-06-21", True, OVERRIDES),
        ),
        (
            "/calendar-business-days",
            business_days_request(Calendar.UnitedStates, "2024-05-01", "2024-05-31"),
        ),
        (
            "/calendar-business-days",
            business_days_request(
                Calendar.Japan, "2024-05-01", "2024-05-31", False, False, OVERRIDES
            ),
        ),
        (
            "/calendar-advance",
            advance_request(
                Calendar.TARGET, "2024-05-01", 2, TimeUnit.Days, BusinessDayConvention.Following
            ),
        ),
        (
            "/calendar-advance",
            advance_request(
                Calendar.UnitedKingdom,
                "2024-01-31",
                -6,
                TimeUnit.Months,
                BusinessDayConvention.ModifiedFollowing,
                True,
                OVERRIDES,
            ),
        ),
    ]


def test_every_calendar_tool_request_validates() -> None:
    for endpoint, body in _cases():
        assert validate_request(endpoint, body) == [], (endpoint, body)
