"""The pasted-table parser (pure) and the curve_from_pasted_table tool with the fake
backend. Parsing is the only transformation: percent -> decimal, thousands
separators, date layouts, tenors; unreadable rows are reported, never dropped."""

from __future__ import annotations

from typing import Any

import pytest
from mcp import Client

from quantra_mcp.builders import pasted_table as pt


def _s(result: Any) -> dict[str, Any]:
    assert result.structured_content is not None, result.content
    return dict(result.structured_content)


def test_tsv_with_header_dates_and_tenors() -> None:
    text = "Date\tDF\n2025-01-15\t1.0\n2026-01-15\t0.96\n18-Sep-2034\t0.72\n10Y\t0.70\n"
    t = pt.parse_table(text, "discount")
    assert t.header == ["Date", "DF"]
    assert [(r.label, r.value) for r in t.rows] == [
        ("2025-01-15", 1.0),
        ("2026-01-15", 0.96),
        ("2034-09-18", 0.72),
        ("10Y", 0.70),
    ]
    assert t.rows[3].tenor == {"n": 10, "unit": "Years"}
    assert t.unparsed == []
    assert any("header row detected" in n for n in t.notes)


def test_percent_and_thousands_are_the_only_numeric_rules() -> None:
    t = pt.parse_table("1Y 3.5%\n2Y 3,000\n5Y, 0.03\n7Y 4.1", "par")
    assert [(r.label, r.value) for r in t.rows] == [
        ("1Y", 0.035),
        ("2Y", 3000.0),
        ("5Y", 0.03),
        ("7Y", 4.1),  # no % sign: left exactly as written
    ]
    rules = [n for n in t.notes if n.startswith("parsing rule applied")]
    assert any("percent -> decimal" in n for n in rules)
    assert any("thousands separators stripped" in n for n in rules)


def test_percent_flag_divides_every_value() -> None:
    t = pt.parse_table("1Y 3.5\n2Y 4", "zero", percent=True)
    assert [r.value for r in t.rows] == [0.035, 0.04]
    assert any("percent=true" in n for n in t.notes)


def test_header_names_pick_the_value_column() -> None:
    text = "Tenor;Zero;DF\n1Y;3.5%;0.965\n2Y;3.7%;0.93"
    zero = pt.parse_table(text, "zero")
    disc = pt.parse_table(text, "discount")
    assert [r.value for r in zero.rows] == [0.035, 0.037]
    assert [r.value for r in disc.rows] == [0.965, 0.93]


def test_slash_dates_need_a_format_unless_inferable() -> None:
    amb = pt.parse_table("01/02/2030 0.9", "discount")
    assert amb.rows == [] and "ambiguous slash date" in amb.unparsed[0]["reason"]
    mdy = pt.parse_table("01/02/2030 0.9", "discount", date_format="mdy")
    assert mdy.rows[0].date == "2030-01-02"
    dmy = pt.parse_table("01/02/2030 0.9", "discount", date_format="dmy")
    assert dmy.rows[0].date == "2030-02-01"
    inferred = pt.parse_table("09/18/2034 0.72\n01/02/2030 0.9", "discount")
    assert [r.date for r in inferred.rows] == ["2034-09-18", "2030-01-02"]
    assert any("month/day/year" in n for n in inferred.notes)


def test_unreadable_rows_are_reported_with_a_reason() -> None:
    t = pt.parse_table("junk 12 row\n2Y\n1Y 0,96\n3Y 0.9", "discount")
    assert [r.label for r in t.rows] == ["3Y"]
    reasons = [u["reason"] for u in t.unparsed]
    assert "no date" in reasons[0]
    assert "no numeric value" in reasons[1]
    assert "comma inside a number" in reasons[2]
    assert t.notes[-1] == "1 row(s) parsed, 3 could not be read"


def test_helper_type_word_tags_par_rows() -> None:
    t = pt.parse_table("6M 3.1% deposit\n2Y 3.0% swap", "par", helper_types=("deposit", "swap"))
    assert [r.helper_type for r in t.rows] == ["deposit", "swap"]


def test_invalid_calendar_date_is_not_a_date() -> None:
    t = pt.parse_table("2025-02-30 0.9", "discount")
    assert t.rows == [] and len(t.unparsed) == 1


async def test_tool_discount_table_builds_a_value_curve(app: Any) -> None:
    text = "Date,DF\n2026-01-15,0.96\n2030-01-15,0.80\nnot a row\n"
    async with Client(app) as c:
        r = _s(
            await c.call_tool(
                "curve_from_pasted_table",
                {
                    "text": text,
                    "id": "VC",
                    "kind": "discount",
                    "preset": "USD_SOFR_OIS",
                    "reference_date": "2025-01-15",
                },
            )
        )
    assert r["ok"], r
    pts = r["curve"]["points"]
    assert r["curve"]["bootstrap_trait"] == "InterpolatedDiscount"
    # the anchor the engine requires is added in front; pasted values are untouched
    assert pts[0]["point"] == {
        "date": "2025-01-15",
        "calendar": "UnitedStatesGovernmentBond",
        "business_day_convention": "ModifiedFollowing",
        "discount_factor": 1.0,
    }
    assert [p["point"]["discount_factor"] for p in pts[1:]] == [0.96, 0.80]
    assert any("added the anchor point" in n for n in r["notes"])
    assert r["parsed_rows"] == [
        {"line": 2, "label": "2026-01-15", "value": 0.96},
        {"line": 3, "label": "2030-01-15", "value": 0.80},
    ]
    assert r["unparsed"][0]["line"] == 4 and r["header"] == ["Date", "DF"]


async def test_tool_reference_date_from_first_discount_row(app: Any) -> None:
    async with Client(app) as c:
        r = _s(
            await c.call_tool(
                "curve_from_pasted_table",
                {
                    "text": "2025-01-15 1.0\n1Y 0.96",
                    "id": "VC",
                    "kind": "discount",
                    "preset": "USD_SOFR_OIS",
                },
            )
        )
        missing = _s(
            await c.call_tool(
                "curve_from_pasted_table",
                {"text": "1Y 3.5%", "id": "Z", "kind": "zero", "preset": "USD_SOFR_OIS"},
            )
        )
    assert r["ok"] and r["curve"]["reference_date"] == "2025-01-15"
    assert any("taken from the first row" in n for n in r["notes"])
    assert missing["ok"] is False and "reference_date is required" in missing["error"]
    assert missing["parsed_rows"][0]["value"] == 0.035  # the rows were still read


async def test_tool_par_table_builds_helpers_from_the_preset(app: Any) -> None:
    async with Client(app) as c:
        r = _s(
            await c.call_tool(
                "curve_from_pasted_table",
                {
                    "text": "Tenor Rate\n1Y 4.95%\n2Y 4.40%\n2030-01-15 3.75%",
                    "id": "SOFR",
                    "kind": "par",
                    "preset": "USD_SOFR_OIS",
                    "reference_date": "2025-01-15",
                },
            )
        )
        several = _s(
            await c.call_tool(
                "curve_from_pasted_table",
                {
                    "text": "1Y 3%",
                    "id": "E",
                    "kind": "par",
                    "preset": "EUR_EURIBOR_6M",
                    "reference_date": "2025-01-15",
                },
            )
        )
        typed = _s(
            await c.call_tool(
                "curve_from_pasted_table",
                {
                    "text": "6M 3% deposit\n2Y 3.1%",
                    "id": "E",
                    "kind": "par",
                    "preset": "EUR_EURIBOR_6M",
                    "reference_date": "2025-01-15",
                    "quote_type": "swap",
                },
            )
        )
    assert r["ok"], r
    pts = r["curve"]["points"]
    assert [p["point_type"] for p in pts] == ["OISHelper", "OISHelper"]
    assert [p["point"]["rate"] for p in pts] == [0.0495, 0.044]
    assert r["indices"][0]["id"] == "USD_SOFR"
    assert "par quotes need a tenor" in r["unparsed"][0]["reason"]
    assert several["ok"] is False and "quote_type is required" in several["error"]
    assert typed["ok"], typed
    assert [p["point_type"] for p in typed["curve"]["points"]] == ["DepositHelper", "SwapHelper"]


async def test_tool_zero_table_and_errors(app: Any) -> None:
    async with Client(app) as c:
        z = _s(
            await c.call_tool(
                "curve_from_pasted_table",
                {
                    "text": "1W 5.33%\n10Y 3.60%",
                    "id": "Z",
                    "kind": "zero",
                    "reference_date": "2024-08-14",
                    "conventions": {
                        "day_counter": "Actual365Fixed",
                        "calendar": "UnitedStatesGovernmentBond",
                        "business_day_convention": "Following",
                    },
                },
            )
        )
        nothing = _s(
            await c.call_tool(
                "curve_from_pasted_table",
                {
                    "text": "no 1\nrows 2\nhere 3",
                    "id": "Z",
                    "kind": "zero",
                    "reference_date": "2024-08-14",
                },
            )
        )
        bad_preset = _s(
            await c.call_tool(
                "curve_from_pasted_table",
                {"text": "1Y 0.9", "id": "Z", "kind": "discount", "preset": "NOPE"},
            )
        )
    assert z["ok"], z
    p0 = z["curve"]["points"][0]["point"]
    assert p0["zero_rate"] == 0.0533 and p0["compounding"] == "Continuous"
    assert z["curve"]["bootstrap_trait"] == "InterpolatedZero"
    assert nothing["ok"] is False and len(nothing["unparsed"]) == 3
    assert bad_preset["ok"] is False and "unknown preset" in bad_preset["error"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,234.5", 1234.5),
        ("12.5%", 0.125),
        ("-0.25%", -0.0025),
        ("1e-3", 0.001),
        (".5", 0.5),
    ],
)
def test_number_parsing(text: str, expected: float) -> None:
    value, _ = pt._parse_number(text, None)
    assert value == expected


def test_number_parsing_rejects_words_and_ambiguous_commas() -> None:
    assert pt._parse_number("abc", None) == (None, None)
    assert pt._parse_number("0,96", None) == (None, None)
