"""Curve tables copied from a terminal screen: ``1 WK`` / ``18 MO`` / ``10 YR`` labels,
extra columns (Term, Market Rate, Shift, Shifted Rate, Zero Rate, Discount), ON/TN rows,
and the structured ``rows`` alternative to ``text``. The numbers must reach the builder
exactly as pasted; nothing is retyped."""

from __future__ import annotations

from typing import Any

import pytest
from mcp import Client

from quantra_mcp.builders import pasted_table as pt
from quantra_mcp.builders.tenor import OVERNIGHT_REASON
from tests.conftest import structured
from tests.screen_tables import HEADER6, SCREEN_LABELS, expected_column, six_column, two_column

BASE = {"market_data_source": "user_pasted", "id": "SOFR", "reference_date": "2025-01-15"}


def _s(result: Any) -> dict[str, Any]:
    return structured(result)


def test_screen_labels_parse_like_compact_labels() -> None:
    screen = pt.parse_table(two_column(True), "par")
    compact = pt.parse_table(two_column(False), "par")
    assert screen.unparsed == [] and compact.unparsed == []
    assert len(screen.rows) == 32
    assert [(r.tenor, r.value) for r in screen.rows] == [(r.tenor, r.value) for r in compact.rows]
    assert [r.label for r in screen.rows] == [short for _, short, _ in SCREEN_LABELS]


@pytest.mark.parametrize("sep", ["\t", "   ", ";", ","])
@pytest.mark.parametrize("kind", ["par", "zero", "discount"])
def test_six_column_screen_picks_the_named_column(sep: str, kind: str) -> None:
    t = pt.parse_table(six_column(sep), kind)  # type: ignore[arg-type]
    assert t.refusal is None, t.notes
    assert t.header == HEADER6
    assert t.unparsed == []
    assert [r.value for r in t.rows] == expected_column(kind)
    want = {"par": "Market Rate", "zero": "Zero Rate", "discount": "Discount"}[kind]
    assert any(n.startswith(f"value column = {want!r}") for n in t.notes), t.notes


def test_single_space_header_is_joined_only_when_it_aligns() -> None:
    text = six_column("\t").replace("\t".join(HEADER6), " ".join(HEADER6), 1)
    t = pt.parse_table(text, "zero")
    assert t.header == HEADER6 and t.refusal is None
    assert any("joined into column names" in n for n in t.notes)
    assert [r.value for r in t.rows] == expected_column("zero")


def test_ambiguous_value_column_is_refused_with_the_columns() -> None:
    t = pt.parse_table("Term\tBid Rate\tAsk Rate\n1 YR\t4.1\t4.2\n2 YR\t4.0\t4.1", "par")
    assert t.rows == [] and t.refusal is not None
    assert "ambiguous" in t.refusal and "'Bid Rate'" in t.refusal and "'Ask Rate'" in t.refusal


@pytest.mark.parametrize(
    "text",
    [
        "1 YR 4.1 4.2\n2 YR 4.0 4.1",  # no header, two numeric columns
        "Term\tShift\tShifted Rate\n1 YR\t0\t4.1",  # header names no zero column
    ],
)
def test_several_numeric_columns_without_a_named_one_are_refused(text: str) -> None:
    t = pt.parse_table(text, "zero")
    assert t.rows == [] and t.refusal is not None and "never picked silently" in t.refusal


def test_overnight_rows_get_the_specific_reason() -> None:
    t = pt.parse_table("ON 4.33\nO/N 4.33\nT/N 4.34\nSN 4.3\n1 WK 4.31\n2 MO 4.3\n5 YR 3.9", "par")
    assert [r.label for r in t.rows] == ["1W", "2M", "5Y"]
    assert [u["reason"] for u in t.unparsed] == [OVERNIGHT_REASON] * 4


def test_unit_words_outside_the_list_are_not_guessed() -> None:
    t = pt.parse_table("6 MIN 4.1\n6 Q 4.0\n1 MO 4.2", "par")
    assert [r.label for r in t.rows] == ["1M"]
    assert len(t.unparsed) == 2


async def test_tool_screen_text_has_the_same_digest_as_compact(app: Any) -> None:
    args = {**BASE, "kind": "par", "preset": "USD_SOFR_OIS"}
    async with Client(app) as c:
        screen = _s(
            await c.call_tool("curve_from_pasted_table", {**args, "text": two_column(True)})
        )
        compact = _s(
            await c.call_tool("curve_from_pasted_table", {**args, "text": two_column(False)})
        )
        six = _s(
            await c.call_tool(
                "curve_from_pasted_table", {**args, "text": six_column(), "percent": True}
            )
        )
    assert screen["ok"] and compact["ok"] and six["ok"], (screen, compact, six)
    assert screen["unparsed"] == [] and len(screen["parsed_rows"]) == 32
    assert screen["digest"] == compact["digest"] == six["digest"]
    assert screen["curve"] == compact["curve"]
    assert any("'Market Rate'" in n for n in six["notes"])


async def test_tool_rows_input(app: Any) -> None:
    rows = [
        {"tenor": lab, "value": float(v.split("\t")[1].rstrip("%")) / 100}
        for (lab, _, _), v in zip(SCREEN_LABELS, two_column(True).splitlines(), strict=True)
    ]
    rows_pct = [
        {"tenor": r["tenor"], "value": ln.split("\t")[1]}
        for r, ln in zip(rows, two_column(True).splitlines(), strict=True)
    ]
    args = {**BASE, "kind": "par", "preset": "USD_SOFR_OIS"}
    async with Client(app) as c:
        text = _s(await c.call_tool("curve_from_pasted_table", {**args, "text": two_column(True)}))
        as_rows = _s(await c.call_tool("curve_from_pasted_table", {**args, "rows": rows_pct}))
        both = _s(
            await c.call_tool(
                "curve_from_pasted_table", {**args, "rows": rows, "text": two_column(True)}
            )
        )
        neither = _s(await c.call_tool("curve_from_pasted_table", args))
        bad = _s(
            await c.call_tool(
                "curve_from_pasted_table",
                {
                    **args,
                    "rows": [
                        {"tenor": "ON", "value": "4.33%"},
                        {"tenor": "1 MO", "value": "4.3%"},
                        {"tenor": "1 MIN", "value": "4.3%"},
                        {"date": "2026-01-15", "tenor": "1Y", "value": "4.1%"},
                        {"tenor": "2 YR", "value": "four"},
                    ],
                },
            )
        )
    assert text["ok"] and as_rows["ok"], (text, as_rows)
    assert as_rows["digest"] == text["digest"]
    assert as_rows["unparsed"] == []
    assert both["ok"] is False and "exactly one of text or rows" in both["error"]
    assert neither["ok"] is False and "exactly one of text or rows" in neither["error"]
    assert bad["ok"], bad
    assert [r["label"] for r in bad["parsed_rows"]] == ["1M"]
    reasons = [u["reason"] for u in bad["unparsed"]]
    assert reasons[0] == OVERNIGHT_REASON
    assert "1 MIN" in reasons[1]
    assert "exactly one of tenor or date" in reasons[2]
    assert "not a number" in reasons[3]


async def test_tool_refusal_is_an_error_with_the_columns(app: Any) -> None:
    async with Client(app) as c:
        r = _s(
            await c.call_tool(
                "curve_from_pasted_table",
                {
                    **BASE,
                    "kind": "par",
                    "preset": "USD_SOFR_OIS",
                    "text": "Term\tBid Rate\tAsk Rate\n1 YR\t4.1\t4.2",
                },
            )
        )
    assert (
        r["ok"] is False
        and "ambiguous" in r["error"]
        and r["header"]
        == [
            "Term",
            "Bid Rate",
            "Ask Rate",
        ]
    )
