"""M4 live acceptance (skipped unless ``QUANTRA_ENGINE_URL`` is set).

On the engine fixture ``irs_eur_5y_payer_ois_discounted_multicurve`` (10m 5Y
EUR payer, OIS-discounted): the parallel DV01 (centered by default: (NPV(+1bp)
- NPV(-1bp)) / 2 from three engine calls) is of the order of a few thousand per
bp (positive for the payer: rates up favour paying fixed; the receiver's is the
negative of it) and brackets the one-sided ``up`` / ``down`` numbers; the
key-rate ladder sums to the parallel DV01 within 2%; the receiver ladder is
concentrated at the 5Y pillar (negative, >= 95% of the parallel), the other
buckets are within 2% of it (a pillar
bumped alone reshapes the bootstrapped forwards around it, so a small bucket
may carry either sign) and the 10Y bucket beyond maturity is ~0; a zero-bump
scenario reproduces the base NPV exactly; every reprice is byte-equal to a
direct replay of its echoed request.
"""

from __future__ import annotations

from typing import Any

import pytest

from quantra_mcp.resources import load_example
from tests.live.test_live_engine import ENGINE_URL, _call, canonical, live_client, replay

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not ENGINE_URL, reason="QUANTRA_ENGINE_URL not set"),
]

FIXTURE = "irs_eur_5y_payer_ois_discounted_multicurve"
FIXTURE_NPV = -47408.48798885872  # engine catalog reference
SRC = {"market_data_source": "engine_example"}


def _trade(swap_type: str = "Payer") -> dict[str, Any]:
    return {
        "product": "vanilla_swap",
        "preset": "EUR_EURIBOR_6M",
        "discounting_curve": "EUR_OIS",
        "forwarding_curve": "EUR_6M_CURVE",
        "index_id": "EUR_6M",
        "swap_type": swap_type,
        "notional": 10_000_000.0,
        "fixed_rate": 0.032,
        "effective_date": "spot",
        "tenor": "5Y",
    }


async def _replay_all(result: dict[str, Any]) -> None:
    for call in result["calls"]:
        r = call["result"]
        assert r["ok"], (call["label"], r.get("error"))
        assert canonical(await replay(r["endpoint"], r["request"])) == canonical(r["response"])


async def test_swap_dv01_payer_centered_brackets_the_one_sided_numbers() -> None:
    market = load_example(FIXTURE)["pricing"]
    async with live_client() as client:
        r = await _call(client, "swap_dv01", {"market": market, **SRC, "trade": _trade()})
        up = await _call(
            client, "swap_dv01", {"market": market, **SRC, "trade": _trade(), "method": "up"}
        )
        down = await _call(
            client, "swap_dv01", {"market": market, **SRC, "trade": _trade(), "method": "down"}
        )
    for x in (r, up, down):
        assert x["ok"], x
        # the preset's Backward rule generates the same dates as the fixture's Forward rule
        # for a regular spot-start 5Y: the base NPV is the catalog reference
        assert x["base_npv"] == FIXTURE_NPV
    assert r["method"] == "centered" and [c["label"] for c in r["calls"]] == ["base", "up", "down"]
    assert r["dv01"] == (r["npvs"]["up"] - r["npvs"]["down"]) / 2
    assert up["dv01"] == up["npvs"]["up"] - up["base_npv"]
    assert down["dv01"] == down["base_npv"] - down["npvs"]["down"]
    assert r["npvs"]["up"] == up["npvs"]["up"] and r["npvs"]["down"] == down["npvs"]["down"]
    # a payer gains when rates rise; order of magnitude a few thousand per bp on 10m 5Y
    assert 1_000 < r["dv01"] < 10_000, r["dv01"]
    # centered = mean of the two one-sided differences (exactly, by construction)
    assert r["dv01"] == pytest.approx((up["dv01"] + down["dv01"]) / 2, abs=1e-9)
    lo, hi = sorted([up["dv01"], down["dv01"]])
    assert lo <= r["dv01"] <= hi
    assert abs(up["dv01"] - down["dv01"]) < 0.01 * abs(r["dv01"])  # convexity is small at 1bp
    assert len(r["bumped_quotes"]["up"]) == 8 and len(r["bumped_quotes"]["down"]) == 8
    print(
        f"\n5Y EUR payer fixture DV01: centered={r['dv01']!r} up={up['dv01']!r} "
        f"down={down['dv01']!r}"
    )
    for x in (r, up, down):
        await _replay_all(x)


async def test_key_rate_ladder_sums_to_parallel_within_2pct_and_receiver_shape() -> None:
    market = load_example(FIXTURE)["pricing"]
    async with live_client() as client:
        payer = await _call(
            client,
            "key_rate_ladder",
            {"market": market, **SRC, "trade": _trade(), "curve": "EUR_6M_CURVE"},
        )
        receiver = await _call(
            client,
            "key_rate_ladder",
            {"market": market, **SRC, "trade": _trade("Receiver"), "curve": "EUR_6M_CURVE"},
        )
    for r in (payer, receiver):
        assert r["ok"], r
        assert r["method"] == "centered" and len(r["calls"]) == 1 + 2 + 2 * 4
        assert [row["pillar"] for row in r["ladder"]] == ["6M", "2Y", "5Y", "10Y"]
        for row in r["ladder"]:
            assert row["dv01"] == (row["npv_up"] - row["npv_down"]) / 2
        assert r["sum_of_buckets"] == sum(row["dv01"] for row in r["ladder"])
        par = r["parallel"]["dv01"]
        assert abs(r["sum_of_buckets"] - par) <= 0.02 * abs(par), (r["sum_of_buckets"], par)
        await _replay_all(r)
    assert payer["base_npv"] == FIXTURE_NPV
    rows = {row["pillar"]: row["dv01"] for row in receiver["ladder"]}
    par = receiver["parallel"]["dv01"]
    assert par < 0 and rows["5Y"] < 0, rows
    assert rows["5Y"] == min(rows.values())  # most negative = concentrated at maturity
    assert abs(rows["5Y"]) >= 0.95 * abs(par), (rows, par)
    for pillar in ("6M", "2Y"):
        assert abs(rows[pillar]) < 0.02 * abs(rows["5Y"]), rows
    assert abs(rows["10Y"]) < 1e-6 * abs(rows["5Y"]), rows  # ~0 beyond maturity
    # the payer ladder is the receiver ladder with the sign flipped
    for prow, rrow in zip(payer["ladder"], receiver["ladder"], strict=True):
        assert prow["dv01"] == pytest.approx(-rrow["dv01"], abs=1e-6)


async def test_scenario_zero_bump_reproduces_base() -> None:
    market = load_example(FIXTURE)["pricing"]
    async with live_client() as client:
        r = await _call(
            client,
            "scenario",
            {
                "market": market,
                **SRC,
                "trade": _trade(),
                "scenarios": [
                    {
                        "name": "zero",
                        "bumps": [
                            {"curve": "EUR_OIS", "bp": 0},
                            {"curve": "EUR_6M_CURVE", "bp": 0},
                        ],
                    },
                    {
                        "name": "up50",
                        "bumps": [
                            {"curve": "EUR_OIS", "bp": 50},
                            {"curve": "EUR_6M_CURVE", "bp": 50},
                        ],
                    },
                    {
                        "name": "dn50",
                        "bumps": [
                            {"curve": "EUR_OIS", "bp": -50},
                            {"curve": "EUR_6M_CURVE", "bp": -50},
                        ],
                    },
                ],
            },
        )
    assert r["ok"], r
    by = {row["name"]: row for row in r["table"]}
    assert by["base"]["npv"] == FIXTURE_NPV
    assert by["zero"]["npv"] == FIXTURE_NPV and by["zero"]["change"] == 0.0
    assert by["up50"]["change"] > 0 > by["dn50"]["change"]
    await _replay_all(r)


async def test_fair_rate_from_engine() -> None:
    market = load_example(FIXTURE)["pricing"]
    async with live_client() as client:
        r = await _call(client, "fair_rate", {"market": market, **SRC, "trade": _trade()})
    assert r["ok"] and r["provided_by_engine"] is True
    assert r["fair_rate"] == r["calls"][0]["result"]["response"]["swaps"][0]["fair_rate"]
    assert 0.02 < r["fair_rate"] < 0.04
    await _replay_all(r)
