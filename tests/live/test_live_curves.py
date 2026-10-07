"""M2 live acceptance: presets -> build -> bootstrap on a real engine.

Skipped unless ``QUANTRA_ENGINE_URL`` is set. Every engine ``response`` must be
byte-equal to a direct replay of the echoed ``request``.
"""

from __future__ import annotations

import math
from itertools import pairwise
from typing import Any

import pytest
from mcp import Client

from quantra_mcp.presets.registry import get_preset, preset_ids
from quantra_mcp.resources import load_example
from tests.live.test_live_engine import (
    BOOTSTRAP_50Y_DF_ORACLE,
    ENGINE_URL,
    _call,
    canonical,
    live_client,
    replay,
)
from tests.strips import STRIPS

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not ENGINE_URL, reason="QUANTRA_ENGINE_URL not set"),
]


async def _build(client: Client, preset_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    strip = STRIPS[preset_id]
    preset = get_preset(preset_id)
    built = await _call(
        client,
        "build_curve",
        {
            "id": preset_id,
            "preset": preset_id,
            "quotes": strip["quotes"],
            "reference_date": strip["reference_date"],
        },
    )
    assert built["ok"], built
    query = await _call(
        client,
        "build_query",
        {
            "curve_id": preset_id,
            "measures": ["DF", "ZERO"],
            "tenors": strip["grid"],
            "calendar": str(preset.index.calendar),
            "business_day_convention": str(preset.index.business_day_convention),
        },
    )
    assert query["ok"], query
    return built, query


def _df(result: dict[str, Any], curve_id: str) -> list[float]:
    res = next(r for r in result["response"]["results"] if r["id"] == curve_id)
    return list(next(s for s in res["series"] if s.get("measure", "DF") == "DF")["values"])


async def test_sofr_strip_reproduces_the_gold_example_and_oracle() -> None:
    async with live_client() as client:
        built, query = await _build(client, "USD_SOFR_OIS")
        r = await _call(
            client,
            "bootstrap_curve",
            {"curves": [built], "as_of": "2025-01-15", "queries": [query]},
        )
    assert r["ok"], r
    assert canonical(r["request"]) == canonical(load_example("sofr-bootstrap-request"))
    assert repr(_df(r, "USD_SOFR_OIS")[-1]).startswith(BOOTSTRAP_50Y_DF_ORACLE)
    assert r["summary"]["curves"][0]["measures"] == ["DF", "ZERO"]
    assert canonical(r["response"]) == canonical(await replay("/bootstrap-curves", r["request"]))


@pytest.mark.parametrize("preset_id", [p for p in preset_ids() if p != "USD_SOFR_OIS"])
async def test_presets_bootstrap_with_monotone_discount_factors(preset_id: str) -> None:
    async with live_client() as client:
        built, query = await _build(client, preset_id)
        r = await _call(
            client,
            "bootstrap_curve",
            {"curves": [built], "as_of": STRIPS[preset_id]["reference_date"], "queries": [query]},
        )
    assert r["ok"], r
    dfs = _df(r, preset_id)
    assert all(0 < b < a <= 1.0 for a, b in pairwise(dfs)), dfs
    assert canonical(r["response"]) == canonical(await replay("/bootstrap-curves", r["request"]))


async def test_discount_value_curve_round_trip() -> None:
    async with live_client() as client:
        vc = await _call(
            client,
            "build_value_curve",
            {
                "id": "VC",
                "kind": "discount",
                "points": [{"date": "2025-01-15", "value": 1.0}, {"tenor": "1Y", "value": 0.96}],
                "reference_date": "2025-01-15",
                "preset": "USD_SOFR_OIS",
            },
        )
        q = await _call(
            client,
            "build_query",
            {
                "curve_id": "VC",
                "measures": ["DF", "ZERO"],
                "tenors": ["1Y"],
                "calendar": "UnitedStatesGovernmentBond",
                "business_day_convention": "ModifiedFollowing",
            },
        )
        r = await _call(
            client, "bootstrap_curve", {"curves": [vc], "as_of": "2025-01-15", "queries": [q]}
        )
    assert r["ok"], r
    res = r["response"]["results"][0]
    df = next(s for s in res["series"] if s.get("measure", "DF") == "DF")["values"][0]
    zero = next(s for s in res["series"] if s.get("measure") == "ZERO")["values"][0]
    assert df == 0.96  # the pillar is honoured
    # the engine's continuous zero at the 1Y pillar; -ln(0.96) is the oracle for the test only
    assert math.isclose(zero, -math.log(0.96), rel_tol=0, abs_tol=1e-9), zero
    assert canonical(r["response"]) == canonical(await replay("/bootstrap-curves", r["request"]))


async def test_session_reference_resolves_to_the_same_request() -> None:
    async with live_client() as client:
        built, query = await _build(client, "EUR_ESTR_OIS")
        direct = await _call(
            client,
            "bootstrap_curve",
            {"curves": [built], "as_of": "2025-01-15", "queries": [query]},
        )
        put = await _call(client, "session_put", {"name": "estr", "kind": "curve", "value": built})
        via = await _call(
            client,
            "bootstrap_curve",
            {"curves": [{"session": "estr"}], "as_of": "2025-01-15", "queries": [query]},
        )
    assert put["ok"] and direct["ok"] and via["ok"]
    assert via["request"] == direct["request"]  # echoed request shows the resolved curve
    assert via["request"]["pricing"]["rates"]["curves"][0] == built["curve"]
    assert canonical(via["response"]) == canonical(direct["response"])
    assert via["notes"][0].startswith("curves[0] <- session 'estr'")
