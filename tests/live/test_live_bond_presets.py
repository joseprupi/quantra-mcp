"""M5.7 live sanity for the sovereign bond presets: a bond issued at spot with the
coupon equal to the flat zero rate (same compounding and frequency as the coupon)
prices close to par. Sanity, not parity: no QuantLib run is claimed here."""

from __future__ import annotations

import pytest

from quantra_mcp.presets.registry import get_preset
from tests.live.test_live_engine import ENGINE_URL, _call, canonical, live_client, replay

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not ENGINE_URL, reason="QUANTRA_ENGINE_URL not set"),
]

AS_OF = "2025-01-15"
COUPON = 0.04


@pytest.mark.parametrize("pid", ["DE_BUND", "US_TREASURY", "UK_GILT"])
async def test_bond_preset_prices_near_par_on_a_flat_curve(pid: str) -> None:
    conv = get_preset(pid).trade_block("fixed_rate_bond")
    async with live_client() as client:
        flat = await _call(
            client,
            "build_value_curve",
            {
                "market_data_source": "user_pasted",
                "id": "FLAT",
                "kind": "zero",
                # anchored at the reference date (the engine anchors a value curve at its
                # first point; a 0D tenor is read as absent, so the date is explicit)
                "points": [
                    {"date": AS_OF, "value": COUPON},
                    {"tenor": "5Y", "value": COUPON},
                    {"tenor": "12Y", "value": COUPON},
                ],
                "reference_date": AS_OF,
                "conventions": {
                    "day_counter": "Actual365Fixed",  # a curve day counter; Act/Act ISMA
                    # has no reference period on a curve (engine: "negative time")
                    "calendar": str(conv.schedule.calendar),
                    "business_day_convention": "Following",
                },
                "compounding": "Compounded",
                "frequency": str(conv.frequency),
            },
        )
        assert flat["ok"], flat
        r = await _call(
            client,
            "price_fixed_rate_bond",
            {
                "market": {"curves": [flat], "indices": []},
                "market_data_source": "user_pasted",
                "preset": pid,
                "face_amount": 1_000_000.0,
                "coupon_rate": COUPON,
                "issue_date": "spot",
                "tenor": "10Y",
                "discounting_curve": "FLAT",
                "as_of": AS_OF,
                "include_details": True,
            },
        )
    assert r["ok"], r
    bond = r["response"]["bonds"][0]
    clean = bond["clean_price"]
    print(f"{pid}: clean_price={clean!r} yield={bond.get('yield')!r} npv={bond['npv']!r}")
    assert abs(clean - 100.0) < 0.5, (pid, clean)
    assert abs(bond["yield"] - COUPON) < 5e-4, (pid, bond["yield"])
    assert r["request"]["bonds"][0]["fixed_rate_bond"]["accrual_day_counter"] == "ActualActualISMA"
    assert canonical(r["response"]) == canonical(await replay(r["endpoint"], r["request"]))
