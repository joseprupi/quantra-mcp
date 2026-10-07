"""The bump builder: every helper type bumped exactly once, value curves per kind,
unbumpable pillars and unknown pillars rejected with names."""

from __future__ import annotations

from typing import Any

import pytest

from quantra_mcp.builders import bumps
from quantra_mcp.errors import LocalValidationError


def _curve(points: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    return {
        "id": "C",
        "reference_date": "2025-01-15",
        "points": [{"point_type": t, "point": p} for t, p in points],
    }


ALL_HELPERS: list[tuple[str, dict[str, Any]]] = [
    ("DepositHelper", {"rate": 0.03, "tenor": {"n": 6, "unit": "Months"}}),
    ("FRAHelper", {"rate": 0.031, "months_to_start": 3, "months_to_end": 6}),
    ("FutureHelper", {"futures_price": 96.5, "future_start_date": "2025-03-19"}),
    ("FutureHelper", {"rate": 0.034, "future_start_date": "2025-06-18"}),
    ("SwapHelper", {"rate": 0.032, "tenor": {"n": 5, "unit": "Years"}, "float_index": {"id": "X"}}),
    (
        "OISHelper",
        {"rate": 0.027, "tenor": {"n": 2, "unit": "Years"}, "overnight_index": {"id": "O"}},
    ),
    (
        "DatedOISHelper",
        {"rate": 0.0265, "start_date": "2025-03-19", "end_date": "2025-06-18"},
    ),
    ("BondHelper", {"rate": 0.04, "schedule": {"termination_date": "2030-01-15"}}),
    ("TenorBasisSwapHelper", {"spread": 0.0005, "tenor": {"n": 10, "unit": "Years"}}),
    ("CrossCcyBasisHelper", {"spread": -0.0012, "tenor": {"n": 3, "unit": "Years"}}),
    ("ZeroRatePoint", {"date": "2026-01-15", "zero_rate": 0.03}),
    ("ForwardRatePoint", {"date": "2027-01-15", "forward_rate": 0.035}),
]


def test_every_helper_type_is_bumped_exactly_once() -> None:
    curve = _curve(ALL_HELPERS)
    bumped, edits = bumps.bump_curve(curve, 1.0)
    assert len(edits) == len(ALL_HELPERS)
    assert [e["pillar"] for e in edits] == [p.label for p in bumps.pillars_of(curve)]
    by_type = {(e["point_type"], e["field"]): e for e in edits}
    assert by_type[("DepositHelper", "rate")]["to"] == pytest.approx(0.0301)
    assert by_type[("FRAHelper", "rate")]["to"] == pytest.approx(0.0311)
    assert by_type[("FutureHelper", "futures_price")]["to"] == pytest.approx(96.49)
    assert by_type[("FutureHelper", "rate")]["to"] == pytest.approx(0.0341)
    assert by_type[("SwapHelper", "rate")]["to"] == pytest.approx(0.0321)
    assert by_type[("OISHelper", "rate")]["to"] == pytest.approx(0.0271)
    assert by_type[("DatedOISHelper", "rate")]["to"] == pytest.approx(0.0266)
    assert by_type[("BondHelper", "rate")]["to"] == pytest.approx(0.0401)
    assert by_type[("TenorBasisSwapHelper", "spread")]["to"] == pytest.approx(0.0006)
    assert by_type[("CrossCcyBasisHelper", "spread")]["to"] == pytest.approx(-0.0011)
    assert by_type[("ZeroRatePoint", "zero_rate")]["to"] == pytest.approx(0.0301)
    assert by_type[("ForwardRatePoint", "forward_rate")]["to"] == pytest.approx(0.0351)
    # the source curve is untouched; only the quote field moved on the copy
    assert curve["points"][0]["point"]["rate"] == 0.03
    for (ptype, _), e in by_type.items():
        orig = next(p for t, p in ALL_HELPERS if t == ptype and e["field"] in p)
        assert e["from"] == orig[e["field"]]
    assert bumped["points"][4]["point"]["float_index"] == {"id": "X"}


def test_labels_and_single_pillar_bump() -> None:
    curve = _curve(ALL_HELPERS)
    labels = [p.label for p in bumps.pillars_of(curve)]
    assert labels == [
        "6M",
        "3x6",
        "FUT 2025-03-19",
        "FUT 2025-06-18",
        "5Y",
        "2Y",
        "2025-03-19/2025-06-18",
        "BOND 2030-01-15",
        "10Y",
        "3Y",
        "2026-01-15",
        "2027-01-15",
    ]
    bumped, edits = bumps.bump_curve(curve, -2.5, [4])
    assert len(edits) == 1 and edits[0]["pillar"] == "5Y"
    assert bumped["points"][4]["point"]["rate"] == pytest.approx(0.03175)
    assert bumped["points"][0]["point"]["rate"] == 0.03


def test_duplicate_labels_are_suffixed_with_the_index() -> None:
    curve = _curve(
        [
            ("DepositHelper", {"rate": 0.03, "tenor": {"n": 6, "unit": "Months"}}),
            ("SwapHelper", {"rate": 0.031, "tenor": {"n": 6, "unit": "Months"}}),
        ]
    )
    assert [p.label for p in bumps.pillars_of(curve)] == ["6M#0", "6M#1"]


@pytest.mark.parametrize(
    ("ptype", "point", "fragment"),
    [
        ("DiscountFactorPoint", {"date": "2026-01-15", "discount_factor": 0.97}, "discount factor"),
        ("FxSwapHelper", {"fx_points": 12.5, "tenor": {"n": 3, "unit": "Months"}}, "fx_points"),
        ("BondHelper", {"price": 99.5, "schedule": {}}, "clean price"),
        ("SwapHelper", {"quote_id": "EUR.IRS.5Y", "tenor": {"n": 5, "unit": "Years"}}, "quote_id"),
        ("DepositHelper", {"tenor": {"n": 1, "unit": "Months"}}, "no numeric quote"),
    ],
)
def test_unbumpable_pillars_are_rejected_by_name(
    ptype: str, point: dict[str, Any], fragment: str
) -> None:
    curve = _curve(
        [("DepositHelper", {"rate": 0.03, "tenor": {"n": 1, "unit": "Weeks"}}), (ptype, point)]
    )
    pillars = bumps.pillars_of(curve)
    assert pillars[1].field is None and pillars[1].reason is not None
    with pytest.raises(LocalValidationError) as exc:
        bumps.bump_curve(curve, 1.0)
    assert fragment in exc.value.error
    # the bumpable pillar alone still works
    _, edits = bumps.bump_curve(curve, 1.0, [0])
    assert len(edits) == 1


def test_unknown_pillar_lists_the_available_labels() -> None:
    pillars = bumps.pillars_of(_curve(ALL_HELPERS[:2]))
    with pytest.raises(LocalValidationError) as exc:
        bumps.resolve_pillar(pillars, "7Y", "/x")
    assert "unknown pillar '7Y'" in exc.value.error
    assert "['6M', '3x6']" in exc.value.error
    with pytest.raises(LocalValidationError):
        bumps.resolve_pillar(pillars, 2, "/x")
    assert bumps.resolve_pillar(pillars, 1, "/x").label == "3x6"
    assert bumps.resolve_pillar(pillars, "6M", "/x").index == 0


def test_replace_quote() -> None:
    curve = _curve(ALL_HELPERS[:2])
    out, edit = bumps.replace_quote(curve, 1, 0.05)
    assert edit == {
        "curve": "C",
        "pillar": "3x6",
        "point_type": "FRAHelper",
        "field": "rate",
        "from": 0.031,
        "to": 0.05,
    }
    assert out["points"][1]["point"]["rate"] == 0.05
    assert curve["points"][1]["point"]["rate"] == 0.031
