"""M3 live acceptance on a real engine (skipped unless ``QUANTRA_ENGINE_URL`` is set).

(a) The 221-fixture sweep: every vendored example POSTed via ``engine_request``
    returns 200 and, where the engine catalog has a reference value, the primary
    number matches it (NPV to 0.01, the engine's own gate; dates exactly; series
    shapes; calibration fields to 1e-7 / 6 significant digits).
(b) Each convenience tool reproduces its fixture: the built request is JSON-equal
    (order-insensitive) to the fixture, the engine number equals the oracle and
    the response is byte-equal to a direct replay of the echoed request.
(c) price_ois_swap on a build_curve market reproduces the SOFR blog example
    (NPV 337986.79130030936).
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from quantra_mcp import examples_catalog as cat
from quantra_mcp.resources import load_example
from tests.live.test_live_engine import ENGINE_URL, _call, canonical, live_client, replay
from tests.product_cases import (
    USD_OIS_SWAPTION_FIXTURES,
    blog_ois_args,
    fixture_body,
    product_cases,
    usd_ois_swaption_args,
)
from tests.strips import STRIPS

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not ENGINE_URL, reason="QUANTRA_ENGINE_URL not set"),
]

OIS_NPV_ORACLE = 337986.79130030936


@pytest.mark.parametrize("name", [r["name"] for r in cat.rows()])
async def test_every_example_prices_and_matches_its_oracle(name: str) -> None:
    row = cat.find(name)
    body = cat.load_body(row)
    async with live_client() as client:
        r = await _call(client, "engine_request", {"endpoint": row["endpoint"], "body": body})
    expected = int(row.get("expected_status", 200))
    assert r["request"] == body
    if expected != 200:  # the engine's own error fixtures (index: expected_status + why)
        assert r["ok"] is False and r["status"] == expected, (name, r.get("status"), r.get("error"))
        assert row["expected_error_contains"] in r["error"], (name, r["error"])
        return
    assert r["ok"], (name, r.get("status"), r.get("error"))
    check = cat.oracle_check(row, r["response"])
    if check["checked"]:
        assert check["ok"], (name, check)


CASES = {c.tool: c for c in product_cases()}


@pytest.mark.parametrize("tool", sorted(CASES))
async def test_tool_reproduces_its_fixture(tool: str) -> None:
    case = CASES[tool]
    fixture = fixture_body(case.fixture)
    row = cat.find(case.fixture)
    async with live_client() as client:
        r = await _call(client, tool, case.live)
    assert r["ok"], r
    assert r["request"] == fixture  # order-insensitive JSON equality with the engine fixture
    assert len(r.get("date_resolution", [])) == case.calendar_calls
    number = case.number(r["response"])
    check = cat.oracle_check(row, r["response"])
    assert check["ok"], (tool, number, check)
    assert canonical(r["response"]) == canonical(await replay(r["endpoint"], r["request"]))
    assert r["summary"]


async def test_ois_swap_on_a_built_sofr_curve_matches_the_blog_example() -> None:
    strip = STRIPS["USD_SOFR_OIS"]
    async with live_client() as client:
        built = await _call(
            client,
            "build_curve",
            {
                "market_data_source": "user_pasted",
                "id": "USD_SOFR_OIS",
                "preset": "USD_SOFR_OIS",
                "quotes": strip["quotes"],
                "reference_date": strip["reference_date"],
            },
        )
        r = await _call(client, "price_ois_swap", blog_ois_args(built))
    assert r["ok"], r
    assert r["request"] == load_example("sofr-ois-swap-request")
    assert r["response"]["swaps"][0]["npv"] == OIS_NPV_ORACLE
    assert canonical(r["response"]) == canonical(await replay("/price-ois-swap", r["request"]))


async def test_engine_error_during_date_resolution_is_reported() -> None:
    """A bad calendar date for 'spot' surfaces the engine's error, request still echoed."""
    case = CASES["price_vanilla_swap"]
    market: dict[str, Any] = dict(case.live["market"])
    market["as_of_date"] = "2025-02-30"
    async with live_client() as client:
        r = await _call(
            client,
            "price_vanilla_swap",
            {"market_data_source": "engine_example", **case.live, "market": market},
        )
    assert r["ok"] is False and r["status"] is None  # rejected locally: not a real date
    assert "2025-02-30" in r["error"]


@pytest.mark.parametrize("fixture", sorted(USD_OIS_SWAPTION_FIXTURES))
async def test_usd_ois_swaption_reproduces_the_engine_fixture(fixture: str) -> None:
    """price_swaption on the USD_SOFR_OIS preset rebuilds the engine's USD SOFR OIS
    swaption fixture (``swaption_ois_request``, ParYieldCurve) JSON-equal, with spot / 10Y
    resolved by the engine; the engine's NPV equals a direct POST of the fixture (no
    QuantLib oracle exists for it)."""
    method = USD_OIS_SWAPTION_FIXTURES[fixture]
    body = fixture_body(fixture)
    async with live_client() as client:
        r = await _call(client, "price_swaption", usd_ois_swaption_args(fixture, method))
    assert r["ok"], r
    assert r["request"] == body
    assert len(r["date_resolution"]) == 2
    direct = await replay("/price-swaption", body)
    assert r["response"]["swaptions"][0]["npv"] == direct["swaptions"][0]["npv"]
    assert canonical(r["response"]) == canonical(await replay("/price-swaption", r["request"]))
    assert r["summary"]["swaptions"][0]["used_strike"] == 0.03367463


async def test_swaption_rebump_theta_on_a_value_curve_market_is_non_zero() -> None:
    """Engine 0.8.0+: the rebump roll shifts every explicit ``reference_date``, so a swaption
    priced on a value curve (InterpolatedZero points pinned to a reference date) reports a
    non-zero rebump ``theta``. On 0.7.0 the roll left fixed-reference value curves where they
    were, theta came out exactly zero and was dropped from the JSON. The market is the EUR
    swaption case with its bootstrapped curve replaced by the engine's own zero-rate value
    curve fixture (same as-of), so no market data is invented."""
    case = CASES["price_swaption"]
    zero = fixture_body("frb_usd_10y_treasury_semiannual_zero_curve")["pricing"]["rates"]["curves"]
    assert len(zero) == 1 and zero[0]["bootstrap_trait"] == "InterpolatedZero"
    market: dict[str, Any] = copy.deepcopy(case.live["market"])
    market["rates"]["curves"] = [{**zero[0], "id": "discount"}]
    async with live_client() as client:
        r = await _call(
            client,
            "price_swaption",
            {
                "market_data_source": "engine_example",
                **case.live,
                "market": market,
                "include_rebump": True,
            },
        )
    assert r["ok"], r
    assert r["request"]["pricing"]["options"] == {"swaption_pricing_rebump": True}
    assert (
        r["request"]["pricing"]["rates"]["curves"][0]["points"][0]["point_type"] == "ZeroRatePoint"
    )
    sw = r["response"]["swaptions"][0]
    for greek in ("dv01", "gamma", "vega", "theta"):
        assert greek in sw, (greek, sw)
    assert "delta" not in sw and "atm_forward" not in sw  # details-only fields stay absent
    assert sw["theta"] != 0.0, sw
    assert r["summary"]["swaptions"][0]["theta"] == sw["theta"]
    assert canonical(r["response"]) == canonical(await replay("/price-swaption", r["request"]))
