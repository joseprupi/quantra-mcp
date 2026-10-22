"""M5.10 live acceptance for structured notes (engine >= 0.12.0; skipped unless
``QUANTRA_ENGINE_URL`` is set).

Every vendored ``structured_note`` fixture prices (a) through ``engine_request`` and
(b) through ``price_structured_note`` (market = the fixture's pricing block, notes /
greeks / diagnostics = the fixture's), the built request is JSON-equal to the fixture
and both results are JSON-equal to a direct HTTP replay of the echoed request. The
``timing`` diagnostic (wall-clock milliseconds) is the only field that may differ
between two runs and is dropped before the comparison.
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

ROWS = [r for r in cat.rows() if r["endpoint"] == "/price-structured-note"]


def _no_timing(response: Any) -> Any:
    out = copy.deepcopy(response)
    for note in out.get("notes", []):
        note.pop("timing", None)
    return out


def test_every_structured_note_fixture_is_vendored() -> None:
    assert len(ROWS) == 8


@pytest.mark.parametrize("name", [r["name"] for r in ROWS])
async def test_structured_note_fixture_prices_both_ways(name: str) -> None:
    body = cat.load_body(cat.find(name))
    args: dict[str, Any] = {
        "market": body["pricing"],
        "notes": body["notes"],
        "market_data_source": "engine_example",
    }
    for key in ("greeks", "diagnostics"):
        if key in body:
            args[key] = body[key]
    async with live_client() as client:
        raw = await _call(
            client, "engine_request", {"endpoint": "/price-structured-note", "body": body}
        )
        tool = await _call(client, "price_structured_note", args)
    assert raw["ok"], (name, raw.get("status"), raw.get("error"))
    assert tool["ok"], (name, tool.get("status"), tool.get("error"))
    assert tool["request"] == body
    direct = await replay("/price-structured-note", body)
    assert canonical(_no_timing(raw["response"])) == canonical(_no_timing(direct))
    assert canonical(_no_timing(tool["response"])) == canonical(_no_timing(direct))
    row = cat.find(name)
    check = cat.oracle_check(row, tool["response"])
    if check["checked"]:
        assert check["ok"], (name, check)
    summary = tool["summary"]["notes"][0]
    note = direct["notes"][0]
    for key in ("npv", "standard_error", "paths", "seed", "status", "legs", "terms", "model"):
        assert summary.get(key) == note.get(key), key
    if "greeks" in body:
        assert summary["greeks"] == note["greeks"]
    print(name, "npv", summary.get("npv"), "se", summary.get("standard_error"))


async def test_structured_note_engine_error_is_verbatim() -> None:
    body = cat.load_body(cat.find("synthetic_two_asset_worst_of_flat_vol_autocall"))
    bad = copy.deepcopy(body["notes"])
    bad[0]["monte_carlo"]["paths"] = 1001  # odd paths with antithetic: engine 400
    async with live_client() as client:
        r = await _call(
            client,
            "price_structured_note",
            {"market": body["pricing"], "notes": bad, "market_data_source": "engine_example"},
        )
    assert r["ok"] is False and r["status"] == 400 and "paths" in r["error"]
