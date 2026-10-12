"""M5.7: the three sovereign bond presets (DE_BUND, US_TREASURY, UK_GILT).

Each validates as preset data with public provenance on every field, and
``price_fixed_rate_bond`` builds a request from it with every convention explicit
that validates against the vendored spec (hermetic; the live suite prices them).
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp import Client

from quantra_mcp.config import Settings
from quantra_mcp.presets.registry import get_preset, presets_with_trade
from quantra_mcp.schema.validate import validate_request
from quantra_mcp.server import build_server
from tests.conftest import FakeBackend, structured
from tests.product_cases import fixture_body

BOND_PRESETS = {
    "DE_BUND": ("EUR", 2, "Germany", "Annual"),
    "US_TREASURY": ("USD", 1, "UnitedStatesGovernmentBond", "Semiannual"),
    "UK_GILT": ("GBP", 1, "UnitedKingdom", "Semiannual"),
}
SOURCE_URL = {
    "DE_BUND": "https://www.deutsche-finanzagentur.de/en/federal-securities/",
    "US_TREASURY": "https://www.law.cornell.edu/cfr/text/31/appendix-B_to_part_356",
    "UK_GILT": "https://www.dmo.gov.uk/media/gxraurqc/yldeqns_v1.pdf",
}
QL_ACTACT = "https://github.com/lballabio/QuantLib/blob/v1.41/ql/time/daycounters/actualactual.hpp"
FIELDS = [
    "settlement_days",
    "schedule.calendar",
    "schedule.convention",
    "schedule.termination_date_convention",
    "schedule.date_generation_rule",
    "schedule.end_of_month",
    "frequency",
    "accrual_day_counter",
    "payment_convention",
    "redemption",
    "yield.day_counter",
    "yield.compounding",
    "yield.frequency",
]


@pytest.mark.parametrize("pid", sorted(BOND_PRESETS))
def test_bond_preset_conventions_and_public_provenance(pid: str) -> None:
    ccy, settle, cal, freq = BOND_PRESETS[pid]
    p = get_preset(pid)
    assert p.currency == ccy and not p.is_curve_preset and p.trade_types == ["fixed_rate_bond"]
    b = p.trade_block("fixed_rate_bond")
    assert b.settlement_days == settle and str(b.schedule.calendar) == cal
    assert str(b.frequency) == freq and str(b.yield_.frequency) == freq
    assert str(b.accrual_day_counter) == "ActualActualISMA"
    assert str(b.yield_.day_counter) == "ActualActualISMA"
    assert str(b.yield_.compounding) == "Compounded"
    assert str(b.schedule.convention) == "Unadjusted"
    assert str(b.schedule.termination_date_convention) == "Unadjusted"
    assert str(b.schedule.date_generation_rule) == "Backward"
    assert b.schedule.end_of_month is False and b.redemption == 100.0
    assert SOURCE_URL[pid] in p.provenance and QL_ACTACT in p.provenance
    for f in FIELDS:
        prov = p.provenance_of(f"trades.fixed_rate_bond.{f}")
        assert prov != p.provenance, f  # every field has its own entry
        assert "http" in prov, (f, prov)
        if f.endswith("day_counter"):
            assert QL_ACTACT in prov and "ActualActual::ISMA" in prov, f
        elif f == "payment_convention":
            assert "market practice" in prov  # honest: not a field of the cited source
        else:
            assert SOURCE_URL[pid] in prov, (f, prov)
    assert pid in presets_with_trade("fixed_rate_bond")
    assert "engine fixture" not in p.provenance  # sovereign conventions, not the fixture's


async def test_price_fixed_rate_bond_builds_explicit_requests_from_each_preset() -> None:
    backend = FakeBackend(
        {"/price-fixed-rate-bond": {"bonds": [{"npv": 1.0, "clean_price": 99.9}]}}
    )
    app = build_server(Settings(engine_url="http://fake"), backend=backend)
    market: dict[str, Any] = fixture_body("frb_eur_5y_at_par_annual_30360")["pricing"]
    async with Client(app) as c:
        for pid, (_, settle, cal, freq) in BOND_PRESETS.items():
            r = structured(
                await c.call_tool(
                    "price_fixed_rate_bond",
                    {
                        "market": market,
                        "market_data_source": "engine_example",
                        "preset": pid,
                        "face_amount": 1_000_000.0,
                        "coupon_rate": 0.03,
                        "issue_date": "2025-01-17",
                        "maturity_date": "2030-01-17",
                        "discounting_curve": "discount",
                    },
                )
            )
            assert r["ok"], (pid, r)
            assert validate_request("/price-fixed-rate-bond", r["request"]) == [], pid
            item = r["request"]["bonds"][0]
            bond = item["fixed_rate_bond"]
            assert bond["settlement_days"] == settle
            assert bond["accrual_day_counter"] == "ActualActualISMA"
            assert bond["payment_convention"] == "Following" and bond["redemption"] == 100.0
            sched = bond["schedule"]
            assert sched["calendar"] == cal and sched["frequency"] == freq
            assert sched["convention"] == "Unadjusted"
            assert sched["termination_date_convention"] == "Unadjusted"
            assert sched["date_generation_rule"] == "Backward" and sched["end_of_month"] is False
            assert sched["effective_date"] == "2025-01-17"
            assert sched["termination_date"] == "2030-01-17"
            assert item["yield"] == {
                "day_counter": "ActualActualISMA",
                "compounding": "Compounded",
                "frequency": freq,
            }
            assert any(SOURCE_URL[pid] in n for n in r["notes"]), r["notes"]
            assert "date_resolution" not in r
