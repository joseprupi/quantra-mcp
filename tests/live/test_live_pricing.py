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

from typing import Any

import pytest

from quantra_mcp import examples_catalog as cat
from quantra_mcp.resources import load_example
from tests.live.test_live_engine import ENGINE_URL, _call, canonical, live_client, replay
from tests.product_cases import blog_ois_args, fixture_body, product_cases
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
        r = await _call(client, "price_vanilla_swap", {**case.live, "market": market})
    assert r["ok"] is False and r["status"] is None  # rejected locally: not a real date
    assert "2025-02-30" in r["error"]
