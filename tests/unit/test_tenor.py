import pytest

from quantra_mcp.builders.tenor import (
    OVERNIGHT_REASON,
    approx_days,
    parse_tenor,
    parse_tenor_text,
    tenor_label,
    unsupported_tenor_reason,
)
from quantra_mcp.errors import LocalValidationError


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1W", {"n": 1, "unit": "Weeks"}),
        ("3M", {"n": 3, "unit": "Months"}),
        ("18M", {"n": 18, "unit": "Months"}),
        ("2Y", {"n": 2, "unit": "Years"}),
        ("10D", {"n": 10, "unit": "Days"}),
        (" 6m ", {"n": 6, "unit": "Months"}),
        ("0D", {"n": 0, "unit": "Days"}),
        ({"n": 5, "unit": "Years"}, {"n": 5, "unit": "Years"}),
        # terminal-screen spellings: optional whitespace, closed list of unit words
        ("1 WK", {"n": 1, "unit": "Weeks"}),
        ("2 wks", {"n": 2, "unit": "Weeks"}),
        ("1 MO", {"n": 1, "unit": "Months"}),
        ("18 MO", {"n": 18, "unit": "Months"}),
        ("6 months", {"n": 6, "unit": "Months"}),
        ("9MTH", {"n": 9, "unit": "Months"}),
        ("4 Mos", {"n": 4, "unit": "Months"}),
        ("2 YR", {"n": 2, "unit": "Years"}),
        ("10 yrs", {"n": 10, "unit": "Years"}),
        ("1 year", {"n": 1, "unit": "Years"}),
        ("3 DY", {"n": 3, "unit": "Days"}),
        ("7 days", {"n": 7, "unit": "Days"}),
    ],
)
def test_parse_tenor(text: object, expected: dict[str, object]) -> None:
    assert parse_tenor(text) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "6",
        "M6",
        "1.5Y",
        "6 MIN",
        "6 Q",
        "6 MOO",
        "1 WK 2",
        "",
        {"n": -1, "unit": "Days"},
        {"n": 1, "unit": "Hours"},
        6,
    ],
)
def test_parse_tenor_rejects(bad: object) -> None:
    with pytest.raises(LocalValidationError) as exc:
        parse_tenor(bad, "quotes/0/tenor")
    assert exc.value.problems[0]["path"].startswith("/quotes/0/tenor")


def test_label_and_ordering() -> None:
    assert tenor_label(parse_tenor("18M")) == "18M"
    days = [approx_days(parse_tenor(t)) for t in ("1W", "10D", "3M", "18M", "2Y")]
    assert days == sorted(days)
    assert approx_days(parse_tenor("12M")) == approx_days(parse_tenor("1Y"))


@pytest.mark.parametrize("label", ["ON", "O/N", "tn", "T/N", "SN", "s/n"])
def test_overnight_labels_get_a_specific_reason(label: str) -> None:
    assert unsupported_tenor_reason(label) == OVERNIGHT_REASON
    assert parse_tenor_text(label) is None
    with pytest.raises(LocalValidationError) as exc:
        parse_tenor(label, "quotes/0/tenor")
    assert OVERNIGHT_REASON in exc.value.error


def test_mo_is_months() -> None:
    assert parse_tenor_text("1 MO") == {"n": 1, "unit": "Months"}
    assert unsupported_tenor_reason("1 MO") is None
