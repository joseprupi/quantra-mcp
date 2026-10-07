import pytest

from quantra_mcp.builders.tenor import approx_days, parse_tenor, tenor_label
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
    ],
)
def test_parse_tenor(text: object, expected: dict[str, object]) -> None:
    assert parse_tenor(text) == expected


@pytest.mark.parametrize(
    "bad",
    ["6", "M6", "1.5Y", "6 months", "", {"n": -1, "unit": "Days"}, {"n": 1, "unit": "Hours"}, 6],
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
