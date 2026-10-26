"""M5.11 live acceptance for option quotes (engine >= 0.14.0; skipped unless
``QUANTRA_ENGINE_URL`` is set).

Every vendored ``implied_forwards`` / ``vol_surface`` fixture runs (a) through
``engine_request`` and (b) through ``calibrate_implied_forwards`` /
``calibrate_vol_surface`` (arguments = the fixture's own fields); the built request is
JSON-equal to the fixture, both results are JSON-equal to a direct HTTP replay of the
echoed request and the catalog oracle holds. The chain test feeds the engine's own
outputs (dividend curve, VolSurfaceSpec) on a synthetic sheet into
``price_structured_note`` (local vol, an ``is_final`` last event) and
``price_equity_option``.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from quantra_mcp import examples_catalog as cat
from tests.live.test_live_engine import ENGINE_URL, _call, canonical, live_client, replay

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not ENGINE_URL, reason="QUANTRA_ENGINE_URL not set"),
]

FWD = "/calibrate-implied-forwards"
SURF = "/calibrate-vol-surface"
ROWS = [r for r in cat.rows() if r["endpoint"] in (FWD, SURF)]

TOOL_ARGS = {
    FWD: ("calibrate_implied_forwards", ("dividend_curve_id", "filter")),
    SURF: (
        "calibrate_vol_surface",
        ("vol_surface_id", "method", "filter", "forwards", "output_grid", "svi"),
    ),
}


def test_every_option_quote_fixture_is_vendored() -> None:
    assert sorted(r["name"] for r in ROWS) == [
        "synthetic_ndx_like_2026-09-30",
        "synthetic_svi_sheet_2026-09-30_andreasen_huge",
        "synthetic_svi_sheet_2026-09-30_svi",
    ]


@pytest.mark.parametrize("name", [r["name"] for r in ROWS])
async def test_option_quote_fixture_runs_both_ways(name: str) -> None:
    row = cat.find(name)
    endpoint = row["endpoint"]
    body = cat.load_body(row)
    tool_name, optional = TOOL_ARGS[endpoint]
    args: dict[str, Any] = {
        "market": body["pricing"],
        "sheet": body["sheet"],
        "discounting_curve": body["discounting_curve"],
        "market_data_source": "engine_example",
    }
    for key in optional:
        if key in body:
            args[key] = body[key]
    async with live_client() as client:
        raw = await _call(client, "engine_request", {"endpoint": endpoint, "body": body})
        tool = await _call(client, tool_name, args)
    assert raw["ok"], (name, raw.get("status"), raw.get("error"))
    assert tool["ok"], (name, tool.get("status"), tool.get("error"))
    assert tool["request"] == body
    direct = await replay(endpoint, body)
    assert canonical(raw["response"]) == canonical(direct)
    assert canonical(tool["response"]) == canonical(direct)
    check = cat.oracle_check(row, tool["response"])
    assert check["checked"] and check["ok"], (name, check)
    s = tool["summary"]
    assert s["expiries_total"] == len(direct["expiries"])
    assert s["quotes_dropped_total"] == sum(e.get("quotes_dropped", 0) for e in direct["expiries"])
    if endpoint == SURF:
        rep = direct["vanilla_repricing"]
        assert s["vanilla_repricing"]["inside_bid_ask"] == rep["inside_bid_ask"]
        assert s["method"] == body["method"]
        print(name, "repricing", rep["inside_bid_ask"], "/", rep["count"])
    else:
        print(name, "forwards", [e.get("forward") for e in s["expiries"]][:3])


async def test_engine_error_is_verbatim() -> None:
    body = cat.load_body(cat.find("synthetic_ndx_like_2026-09-30"))
    async with live_client() as client:
        r = await _call(
            client,
            "calibrate_implied_forwards",
            {
                "market": body["pricing"],
                "sheet": body["sheet"],
                "discounting_curve": body["discounting_curve"],
                "dividend_curve_id": "D",
                "filter": {**body["filter"], "min_quotes_per_expiry": 1},
                "market_data_source": "engine_example",
            },
        )
    assert r["ok"] is False and r["status"] == 400 and "min_quotes_per_expiry" in r["error"]


def _chain_note(as_of_body: dict[str, Any]) -> dict[str, Any]:
    """A synthetic single-underlying reverse convertible on the synthetic index, last
    event a maturity-only ``is_final`` observation (engine 0.13.0)."""
    return {
        "trade_id": "SYNTHETIC_RC_ON_SURFACE",
        "currency": "USD",
        "notional": 1000.0,
        "underlyings": [
            {"underlying_id": "NDX_SYN", "volatility_id": "NDX_SYN_VOL", "initial_level": 24000.0}
        ],
        "performance_rule": "WorstOf",
        "events": [
            {
                "observation_date": "2027-03-31",
                "payment_date": "2027-04-07",
                "coupon_trigger": 0.0,
                "coupon": 0.02,
            },
            {
                "observation_date": "2027-06-30",
                "payment_date": "2027-07-07",
                "coupon_trigger": 0.0,
                "coupon": 0.02,
            },
            {
                "observation_date": "2027-09-30",
                "payment_date": "2027-10-07",
                "is_final": True,
            },
        ],
        "coupon_rule": {"memory": False, "pay_only_on_call": False},
        "capital": {
            "barrier": 0.7,
            "monitoring": "European",
            "put_strike": 1.0,
            "participation": 1.0,
        },
        "maturity_payment_date": "2027-10-07",
        "discounting_curve": as_of_body["discounting_curve"],
        "funding_spread_bp": 0.0,
        "monte_carlo": {
            "paths": 4096,
            "seed": 42,
            "sequence": "Sobol",
            "antithetic": True,
            "brownian_bridge": True,
            "steps_per_year": 252,
        },
    }


async def test_chain_surface_feeds_structured_note_and_equity_option() -> None:
    body = cat.load_body(cat.find("synthetic_svi_sheet_2026-09-30_svi"))
    base = {
        "market": body["pricing"],
        "sheet": body["sheet"],
        "discounting_curve": body["discounting_curve"],
        "market_data_source": "engine_example",
    }
    async with live_client() as client:
        fwd = await _call(
            client,
            "calibrate_implied_forwards",
            {**base, "dividend_curve_id": "NDX_SYN_DIV_IMPLIED", "filter": body["filter"]},
        )
        assert fwd["ok"], fwd.get("error")
        surf = await _call(
            client,
            "calibrate_vol_surface",
            {
                **base,
                "vol_surface_id": "NDX_SYN_VOL",
                "method": "SviPerExpiry",
                "forwards": fwd["response"]["expiries"],
            },
        )
        assert surf["ok"], surf.get("error")
        assert surf["response"]["forwards_source"] == "request"
        div = fwd["response"]["dividend_curve_spec"]
        vol = surf["response"]["vol_surface_spec"]
        assert fwd["summary"]["reuse"]["id"] == div["id"]
        assert surf["summary"]["reuse"]["id"] == vol["id"]

        pricing = copy.deepcopy(body["pricing"])
        pricing["rates"]["curves"].append(div)
        pricing["equity"]["equity_underlyings"][0]["dividend_yield_curve_id"] = div["id"]
        pricing.setdefault("volatility", {})["vol_surfaces"] = [vol]
        note = _chain_note(body)
        priced = await _call(
            client,
            "price_structured_note",
            {"market": pricing, "notes": [note], "market_data_source": "engine_example"},
        )
        assert priced["ok"], (priced.get("status"), priced.get("error"))
        direct = await replay("/price-structured-note", priced["request"])
        assert canonical(priced["response"]) == canonical(direct)
        n = priced["summary"]["notes"][0]
        assert n["npv"] > 0 and n["status"] == "Alive"
        assert n["terms"]["events"][-1]["is_final"] is True
        print("chain note npv", n["npv"], "se", n["standard_error"])

        opt = await _call(
            client,
            "price_equity_option",
            {
                "market": pricing,
                "market_data_source": "engine_example",
                "spot": "NDX_SYN_SPOT",
                "strike": 24000.0,
                "expiry": "2027-03-19",
                "option_type": "Call",
                "vol": "NDX_SYN_VOL",
                "rate_curve": body["discounting_curve"],
                "dividend_yield": div["id"],
                # the tool builds its own EquityUnderlyingSpec: a distinct id
                "underlying_id": "NDX_SYN_OPTION",
            },
        )
        assert opt["ok"], (opt.get("status"), opt.get("error"))
        direct_opt = await replay("/price-equity-option", opt["request"])
        assert canonical(opt["response"]) == canonical(direct_opt)
        print("chain option npv", opt["response"])
