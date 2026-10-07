"""Pricing tools through the real MCP server with the fake backend (hermetic).

With explicit dates no engine call is needed to build a request, so every
tool's echoed ``request`` must equal its fixture body (order-insensitive) and
is snapshotted under ``tests/golden/products/``. The ``spot`` / tenor path is
exercised against canned ``/calendar-advance`` answers to check the plumbing
(``date_resolution`` entries, notes), not the dates themselves (that is the
live suite's job).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from quantra_mcp.config import Settings
from quantra_mcp.resources import load_example
from quantra_mcp.server import build_server
from tests.conftest import FakeBackend
from tests.product_cases import (
    FIXTURES,
    blog_ois_args_explicit,
    fixture_body,
    market_without,
    product_cases,
)
from tests.strips import STRIPS

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden" / "products"
REGEN = os.environ.get("QUANTRA_REGEN_GOLDENS") == "1"

CANNED: dict[str, Any] = {
    "/calendar-advance": {"input_date": "2025-01-15", "advanced_date": "2025-01-17"},
    "/price-vanilla-swap": {"swaps": [{"npv": -47408.49, "fair_rate": 0.031}]},
    "/price-ois-swap": {"swaps": [{"npv": -28117.54, "fair_rate": 0.0395}]},
    "/price-fixed-rate-bond": {"bonds": [{"npv": 999841.67, "clean_price": 99.9, "yield": 0.031}]},
    "/price-floating-rate-bond": {"bonds": [{"npv": 999841.67}]},
    "/price-zero-coupon-bond": {"bonds": [{"npv": 729090.25}]},
    "/price-callable-fixed-rate-bond": {"bonds": [{"npv": 1.0, "clean_price": 1.0}]},
    "/price-fra": {"fras": [{"npv": 0.03, "forward_rate": 0.0313}]},
    "/price-cap-floor": {"cap_floors": [{"npv": 49435.97, "atm_rate": 0.03}]},
    "/price-swaption": {"swaptions": [{"npv": 12516.87, "implied_volatility": 0.2}]},
    "/price-cds": {"cds_list": [{"npv": 39739.83, "fair_spread": 0.011}]},
    "/price-equity-option": {"options": [{"trade_id": "ACME_CALL_ATM_1Y", "npv": 9.41}]},
    "/price-zero-coupon-inflation-swap": {"swaps": [{"npv": -4675.89}]},
    "/price-year-on-year-inflation-swap": {"swaps": [{"npv": 2285.59}]},
    "/price-year-on-year-inflation-cap-floor": {"cap_floors": [{"npv": 1.0, "atm_rate": 0.02}]},
    "/calibrate-swaption-model": {"model_id": "hw_model", "hw_a": 0.03, "hw_sigma": 0.0087},
    "/calibrate-swaption-vol": {
        "vol_id": "swaption_sabr",
        "diagnostics": {"calibration": {"overall_rmse": 0.0}},
    },
    "/sample-vol-surfaces": {"results": [{"vol_id": "swaption_const", "vols": [0.2] * 12}]},
}


def _golden(name: str, body: dict[str, Any]) -> None:
    path = GOLDEN_DIR / f"{name}.json"
    text = json.dumps(body, indent=2) + "\n"
    if REGEN:
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    assert path.is_file(), f"missing golden {path.name}; run with QUANTRA_REGEN_GOLDENS=1"
    assert json.loads(path.read_text()) == body, f"golden {path.name} differs"


def _s(result: Any) -> dict[str, Any]:
    assert result.structured_content is not None, result.content
    return dict(result.structured_content)


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend(CANNED)


@pytest.fixture
def pricing_app(backend: FakeBackend):  # type: ignore[no-untyped-def]
    return build_server(Settings(engine_url="http://fake"), backend=backend)


CASES = {c.tool: c for c in product_cases()}


@pytest.mark.parametrize("tool", sorted(CASES))
async def test_explicit_dates_rebuild_the_fixture_exactly(
    pricing_app: Any, backend: FakeBackend, tool: str
) -> None:
    case = CASES[tool]
    async with Client(pricing_app) as c:
        r = _s(await c.call_tool(tool, case.explicit))
    assert r["ok"], r
    assert r["request"] == fixture_body(case.fixture)  # order-insensitive JSON equality
    assert r["endpoint"] == backend.calls[-1][1]
    assert "date_resolution" not in r  # explicit dates: no /calendar-advance
    assert all(c[1] != "/calendar-advance" for c in backend.calls)
    assert r["notes"] and all(isinstance(n, str) for n in r["notes"])
    assert r["summary"]
    _golden(tool, r["request"])


@pytest.mark.parametrize("tool", sorted(CASES))
async def test_spot_and_tenor_go_through_calendar_advance(
    pricing_app: Any, backend: FakeBackend, tool: str
) -> None:
    case = CASES[tool]
    async with Client(pricing_app) as c:
        r = _s(await c.call_tool(tool, case.live))
    assert r["ok"], r
    advances = [c for c in backend.calls if c[1] == "/calendar-advance"]
    assert len(advances) == case.calendar_calls
    if case.calendar_calls:
        assert len(r["date_resolution"]) == case.calendar_calls
        assert all(d["endpoint"] == "/calendar-advance" for d in r["date_resolution"])
        assert any("/calendar-advance" in n for n in r["notes"])
        for adv in advances:
            assert "calendar_overrides" not in adv[2]
    else:
        assert "date_resolution" not in r


async def test_every_note_names_a_source(pricing_app: Any) -> None:
    case = CASES["price_vanilla_swap"]
    async with Client(pricing_app) as c:
        r = _s(await c.call_tool("price_vanilla_swap", case.explicit))
    notes = r["notes"]
    conventions = [n for n in notes if ("=" in n and "schedule" in n) or "day_counter" in n]
    assert conventions
    for n in conventions:
        assert "preset EUR_EURIBOR_6M" in n or "(explicit" in n, n
    assert any("engine fixture" in n for n in notes)  # the provenance travels with the note
    assert any(n.startswith("index='EUR_6M' (explicit argument)") for n in notes)


async def test_blog_ois_example_from_a_built_curve(pricing_app: Any) -> None:
    strip = STRIPS["USD_SOFR_OIS"]
    async with Client(pricing_app) as c:
        built = _s(
            await c.call_tool(
                "build_curve",
                {
                    "id": "USD_SOFR_OIS",
                    "preset": "USD_SOFR_OIS",
                    "quotes": strip["quotes"],
                    "reference_date": strip["reference_date"],
                },
            )
        )
        r = _s(await c.call_tool("price_ois_swap", blog_ois_args_explicit(built)))
        put = _s(
            await c.call_tool("session_put", {"name": "sofr", "kind": "curve", "value": built})
        )
        args = blog_ois_args_explicit(built)
        args["market"] = {"session": "sofr"}
        via = _s(await c.call_tool("price_ois_swap", args))
    assert r["ok"] and put["ok"] and via["ok"], (r, put, via)
    assert r["request"] == load_example("sofr-ois-swap-request")
    assert via["request"] == r["request"]
    assert any("session 'sofr'" in n for n in via["notes"])
    _golden("price_ois_swap.blog", r["request"])


async def test_local_errors_never_reach_the_engine(pricing_app: Any, backend: FakeBackend) -> None:
    case = CASES["price_vanilla_swap"]
    async with Client(pricing_app) as c:
        unknown = _s(await c.call_tool("price_vanilla_swap", {**case.explicit, "preset": "NOPE"}))
        no_trade = _s(
            await c.call_tool("price_vanilla_swap", {**case.explicit, "preset": "EUR_CDS"})
        )
        bad_index = _s(await c.call_tool("price_vanilla_swap", {**case.explicit, "index_id": "X"}))
        bad_curve = _s(
            await c.call_tool("price_vanilla_swap", {**case.explicit, "discounting_curve": "nope"})
        )
        both = _s(await c.call_tool("price_vanilla_swap", {**case.explicit, "tenor": "5Y"}))
        as_of = _s(
            await c.call_tool("price_vanilla_swap", {**case.explicit, "as_of": "2024-01-01"})
        )
        no_market_as_of = _s(
            await c.call_tool(
                "price_vanilla_swap",
                {
                    **case.explicit,
                    "market": {"curves": [case.explicit["market"]["rates"]["curves"][0]]},
                },
            )
        )
    for r in (unknown, no_trade, bad_index, bad_curve, both, as_of, no_market_as_of):
        assert r["ok"] is False and r["status"] is None, r
    assert "available presets" in unknown["error"]
    assert "no 'vanilla_swap' trade conventions" in no_trade["error"]
    assert "EUR_EURIBOR_6M" in no_trade["error"]  # names a preset that has it
    assert "not defined in the market" in bad_index["error"]
    assert "not a curve of the market" in bad_curve["error"]
    assert "exactly one of termination_date" in both["error"]
    assert "differs from market.as_of_date" in as_of["error"]
    assert "as_of (YYYY-MM-DD) is required" in no_market_as_of["error"]
    assert [c for c in backend.calls if c[0] == "POST"] == []


async def test_market_object_with_build_curve_result_and_additions(pricing_app: Any) -> None:
    """A market given as {curves: [...]} is assembled; conflicting ids are rejected."""
    strip = STRIPS["EUR_EURIBOR_3M"]
    async with Client(pricing_app) as c:
        built = _s(
            await c.call_tool(
                "build_curve",
                {
                    "id": "E3",
                    "preset": "EUR_EURIBOR_3M",
                    "quotes": strip["quotes"],
                    "reference_date": "2025-01-15",
                },
            )
        )
        r = _s(
            await c.call_tool(
                "price_cap_floor",
                {
                    "market": {"curves": [built]},
                    "as_of": "2025-01-15",
                    "preset": "EUR_EURIBOR_3M",
                    "cap_floor_type": "Cap",
                    "notional": 1e6,
                    "strike": 0.02,
                    "effective_date": "2025-01-17",
                    "termination_date": "2030-01-17",
                    "discounting_curve": "E3",
                    "forwarding_curve": "E3",
                    "vol": {"constant": 0.2, "type": "Lognormal"},
                    "model": "Black",
                },
            )
        )
        clash = _s(
            await c.call_tool(
                "price_cap_floor",
                {
                    "market": {
                        "curves": [built],
                        "models": [
                            {
                                "id": "black_model",
                                "payload_type": "CapFloorModelSpec",
                                "payload": {"model_type": "Bachelier"},
                            }
                        ],
                    },
                    "as_of": "2025-01-15",
                    "preset": "EUR_EURIBOR_3M",
                    "cap_floor_type": "Cap",
                    "notional": 1e6,
                    "strike": 0.02,
                    "effective_date": "2025-01-17",
                    "termination_date": "2030-01-17",
                    "discounting_curve": "E3",
                    "forwarding_curve": "E3",
                    "vol": {"constant": 0.2, "type": "Lognormal"},
                    "model": "Black",
                },
            )
        )
    assert r["ok"], r
    pricing = r["request"]["pricing"]
    assert pricing["rates"]["indices"][0]["id"] == "EURIBOR_3M"
    assert pricing["rates"]["curves"][0] == built["curve"]
    assert pricing["volatility"]["vol_surfaces"][0]["id"] == "vol_lognormal_0p2"
    assert pricing["volatility"]["models"][0] == {
        "id": "black_model",
        "payload_type": "CapFloorModelSpec",
        "payload": {"model_type": "Black"},
    }
    assert r["request"]["cap_floors"][0]["cap_floor"]["index"] == {"id": "EURIBOR_3M"}
    assert any("[id default]" in n for n in r["notes"])
    assert clash["ok"] is False and "already in the market with different fields" in clash["error"]


async def test_floating_bond_coupon_pricer_rules(pricing_app: Any) -> None:
    case = CASES["price_floating_rate_bond"]
    full = fixture_body(case.fixture)["pricing"]  # carries 'iborpricer'
    async with Client(pricing_app) as c:
        ambiguous = _s(
            await c.call_tool("price_floating_rate_bond", {**case.explicit, "market": full})
        )
        chosen = _s(
            await c.call_tool(
                "price_floating_rate_bond",
                {**case.explicit, "market": full, "coupon_pricer": "iborpricer"},
            )
        )
        missing = _s(
            await c.call_tool(
                "price_floating_rate_bond", {**case.explicit, "coupon_pricer": "nope"}
            )
        )
    assert ambiguous["ok"] is False and "already has coupon pricers" in ambiguous["error"]
    assert chosen["ok"] and chosen["request"] == fixture_body(case.fixture)
    assert missing["ok"] is False and "not in pricing.rates.coupon_pricers" in missing["error"]


async def test_inflation_fixings_are_required_and_set(pricing_app: Any) -> None:
    case = CASES["price_zc_inflation_swap"]
    stripped = market_without(case.explicit["market"])
    stripped["inflation"]["inflation_indices"][0].pop("fixings")
    async with Client(pricing_app) as c:
        r = _s(await c.call_tool("price_zc_inflation_swap", {**case.explicit, "market": stripped}))
        empty = _s(
            await c.call_tool(
                "price_zc_inflation_swap", {**case.explicit, "market": stripped, "fixings": []}
            )
        )
    assert r["ok"] and r["request"] == fixture_body(case.fixture)
    assert any(
        n.startswith("fixings: set 7 fixings on inflation index 'EUHICP'") for n in r["notes"]
    )
    assert empty["ok"] is False and "base fixing is required" in empty["error"]


async def test_additional_trades_batch_into_one_request(
    pricing_app: Any, backend: FakeBackend
) -> None:
    case = CASES["price_vanilla_swap"]
    extra = {
        "swap_type": "Receiver",
        "notional": 5_000_000.0,
        "fixed_rate": 0.034,
        "effective_date": "2025-01-17",
        "termination_date": "2027-01-17",
        "index_id": "EUR_6M",
    }
    async with Client(pricing_app) as c:
        r = _s(
            await c.call_tool(
                "price_vanilla_swap",
                {**case.explicit, "additional_trades": [extra], "include_flows": True},
            )
        )
    assert r["ok"], r
    assert len(r["request"]["swaps"]) == 2
    assert r["request"]["swaps"][0] == fixture_body(case.fixture)["swaps"][0]
    assert r["request"]["swaps"][1]["vanilla_swap"]["swap_type"] == "Receiver"
    assert r["request"]["include_flows"] is True
    assert backend.calls[-1][2] == r["request"]


async def test_callable_bond_and_equity_variants(pricing_app: Any) -> None:
    cfrb = fixture_body("cfrb_eur_8y_5pct_call_100_itm")
    amer = fixture_body("eqopt_amer_call_atm_1y")
    async with Client(pricing_app) as c:
        bond = _s(
            await c.call_tool(
                "price_callable_fixed_rate_bond",
                {
                    "market": market_without(cfrb["pricing"], "volatility"),
                    "preset": "EUR_FIXED_BOND",
                    "face_amount": 1_000_000.0,
                    "coupon_rate": 0.05,
                    "issue_date": "2025-01-17",
                    "maturity_date": "2033-01-17",
                    "discounting_curve": "discount",
                    "call_schedule": [{"date": "2029-01-17", "price": 100.0, "type": "Call"}],
                    "model": {"a": 0.03, "sigma": 0.01, "id": "hw_explicit"},
                },
            )
        )
        option = _s(
            await c.call_tool(
                "price_equity_option",
                {
                    **CASES["price_equity_option"].explicit,
                    "exercise": "American",
                    "trade_id": "ACME_AMER_CALL_ATM_1Y",
                },
            )
        )
    assert bond["ok"], bond
    assert bond["request"] == cfrb
    assert option["ok"], option
    assert option["request"] == amer


async def test_raw_shaped_tools_validate_and_forward(
    pricing_app: Any, backend: FakeBackend
) -> None:
    hw = fixture_body("hwcal_eur_constant_normal_sigma_only")
    sabr = fixture_body("sabrcal_eur_2x2_beta_fixed_exact_fit")
    vs = fixture_body("volsample_swaption_constant_cube")
    async with Client(pricing_app) as c:
        r1 = _s(await c.call_tool("calibrate_swaption_model", {"body": hw}))
        r2 = _s(await c.call_tool("calibrate_swaption_vol", {"body": sabr}))
        r3 = _s(await c.call_tool("sample_vol_surface", {"body": vs}))
        bad = _s(await c.call_tool("sample_vol_surface", {"body": {"pricing": {}, "queries": []}}))
    assert r1["ok"] and r1["request"] == hw and r1["summary"]["hw_sigma"] == 0.0087
    assert (
        r2["ok"] and r2["request"] == sabr and r2["summary"]["calibration"] == {"overall_rmse": 0.0}
    )
    assert r3["ok"] and r3["request"] == vs and r3["summary"]["results"][0]["n_vols"] == 12
    assert bad["ok"] is False and bad["status"] is None and bad["problems"]
    assert [c[1] for c in backend.calls if c[0] == "POST"] == [
        "/calibrate-swaption-model",
        "/calibrate-swaption-vol",
        "/sample-vol-surfaces",
    ]


def test_every_mapped_fixture_is_cataloged_with_an_npv_oracle() -> None:
    from quantra_mcp import examples_catalog as cat

    for name in FIXTURES:
        row = cat.find(name)
        assert row["oracle"] and row["oracle"]["kind"] == "npv", name
