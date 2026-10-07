"""M2 tools through the real MCP server with the fake backend."""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp import Client

from quantra_mcp.resources import load_example
from tests.conftest import FakeBackend
from tests.strips import STRIPS

SOFR = STRIPS["USD_SOFR_OIS"]
CANNED_BOOTSTRAP = {
    "results": [
        {
            "id": "USD_SOFR_OIS",
            "reference_date": "2025-01-15",
            "grid_dates": ["2025-02-18", "2075-01-15"],
            "series": [
                {"values": [0.99, 0.262755579831]},
                {"measure": "ZERO", "values": [0.05, 0.03]},
            ],
            "pillar_dates": ["2025-01-15", "2075-01-15"],
        }
    ]
}


def _s(result: Any) -> dict[str, Any]:
    assert result.structured_content is not None, result.content
    return dict(result.structured_content)


async def _build_sofr(c: Client) -> dict[str, Any]:
    return _s(
        await c.call_tool(
            "build_curve",
            {
                "id": "USD_SOFR_OIS",
                "preset": "USD_SOFR_OIS",
                "quotes": SOFR["quotes"],
                "reference_date": SOFR["reference_date"],
            },
        )
    )


async def _sofr_query(c: Client) -> dict[str, Any]:
    return _s(
        await c.call_tool(
            "build_query",
            {
                "curve_id": "USD_SOFR_OIS",
                "measures": ["DF", "ZERO"],
                "tenors": SOFR["grid"],
                "calendar": "UnitedStatesGovernmentBond",
                "business_day_convention": "ModifiedFollowing",
            },
        )
    )


async def test_presets_tools_and_resources(app: Any) -> None:
    async with Client(app) as c:
        lst = _s(await c.call_tool("list_presets", {}))
        one = _s(await c.call_tool("get_preset", {"id": "EUR_ESTR_OIS"}))
        bad = _s(await c.call_tool("get_preset", {"id": "NOPE"}))
        idx = await c.read_resource("quantra://presets")
        res = await c.read_resource("quantra://presets/USD_SOFR_OIS")
        with pytest.raises(Exception, match="unknown preset"):
            await c.read_resource("quantra://presets/NOPE")
    assert lst["ok"] and [p["id"] for p in lst["presets"]][-1] == "USD_SOFR_OIS"
    assert one["ok"] and one["preset"]["helpers"]["ois"]["payment_lag"] == 1
    assert one["preset"]["field_provenance"]["helpers.ois.payment_lag"].startswith(
        "market standard"
    )
    assert bad["ok"] is False and "USD_SOFR_OIS" in bad["error"]
    assert json.loads(idx.contents[0].text)["presets"][0]["uri"] == "quantra://presets/EUR_ESTR_OIS"  # type: ignore[union-attr]
    assert json.loads(res.contents[0].text)["index"]["id"] == "USD_SOFR"  # type: ignore[union-attr]


async def test_build_curve_tool_shape_and_no_engine_call(
    app: Any, fake_backend: FakeBackend
) -> None:
    async with Client(app) as c:
        r = await _build_sofr(c)
        bad = _s(
            await c.call_tool(
                "build_curve",
                {
                    "id": "x",
                    "preset": "USD_SOFR_OIS",
                    "quotes": [{"type": "ois", "tenor": "1Y"}],
                    "reference_date": "2025-01-15",
                },
            )
        )
        unknown = _s(
            await c.call_tool(
                "build_curve",
                {
                    "id": "x",
                    "preset": "NOPE",
                    "quotes": [{"type": "ois", "tenor": "1Y", "rate": 0.03}],
                    "reference_date": "2025-01-15",
                },
            )
        )
    assert set(r) == {"ok", "curve", "indices", "preset", "notes"} and r["ok"] is True
    assert r["preset"] == "USD_SOFR_OIS" and len(r["curve"]["points"]) == 15
    assert any(n.startswith("ois.payment_lag=2 from preset USD_SOFR_OIS") for n in r["notes"])
    assert (
        bad["ok"] is False
        and bad["status"] is None
        and bad["problems"][0]["path"] == "/quotes/0/rate"
    )
    assert unknown["ok"] is False and "available presets" in unknown["error"]
    assert all(m == "GET" for m, *_ in fake_backend.calls)  # only the startup /meta probe


async def test_bootstrap_curve_echoes_resolved_request_and_summarizes(
    app: Any, fake_backend: FakeBackend
) -> None:
    fake_backend.responses["/bootstrap-curves"] = CANNED_BOOTSTRAP
    async with Client(app) as c:
        built = await _build_sofr(c)
        query = await _sofr_query(c)
        direct = _s(
            await c.call_tool(
                "bootstrap_curve",
                {"curves": [built], "as_of": "2025-01-15", "queries": [query], "request_id": "t-1"},
            )
        )
        direct_call = fake_backend.calls[-1]
        put = _s(
            await c.call_tool("session_put", {"name": "sofr", "kind": "curve", "value": built})
        )
        listed = _s(await c.call_tool("session_list", {}))
        got = _s(await c.call_tool("session_get", {"name": "sofr"}))
        via_session = _s(
            await c.call_tool(
                "bootstrap_curve",
                {
                    "curves": [{"session": "sofr"}],
                    "as_of": "2025-01-15",
                    "queries": [query["query"]],
                },
            )
        )
        gone = _s(await c.call_tool("session_delete", {"name": "sofr"}))
        missing = _s(
            await c.call_tool(
                "bootstrap_curve",
                {"curves": [{"session": "sofr"}], "as_of": "2025-01-15", "queries": [query]},
            )
        )
    example = load_example("sofr-bootstrap-request")
    assert direct["ok"] and direct["endpoint"] == "/bootstrap-curves"
    assert direct["request"] == example  # the gold example, built from a strip + preset
    assert direct["response"] == CANNED_BOOTSTRAP
    assert direct["summary"] == {
        "curves": [
            {
                "id": "USD_SOFR_OIS",
                "pillars": 2,
                "first_grid_date": "2025-02-18",
                "last_grid_date": "2075-01-15",
                "measures": ["DF", "ZERO"],
            }
        ]
    }
    assert direct["engine"]["request_id"] == "t-1" and direct_call[3] == "t-1"
    assert direct["notes"] == ["curves[0] is a build_curve result; using its curve and 1 index"]
    assert put["ok"] and put["item"]["curve_id"] == "USD_SOFR_OIS" and put["size"] == 1
    assert listed["items"][0]["name"] == "sofr" and listed["max_items"] == 64
    assert got["value"] == built["curve"] and got["indices"] == built["indices"]
    assert via_session["ok"] and via_session["request"] == example  # resolved, not the ref
    assert via_session["notes"][0].startswith("curves[0] <- session 'sofr'")
    assert gone["deleted"] is True
    assert (
        missing["ok"] is False
        and missing["status"] is None
        and "no session item" in missing["error"]
    )
    # nothing was sent for the failed resolution
    assert sum(1 for m, e, _, _ in fake_backend.calls if e == "/bootstrap-curves") == 2


async def test_bootstrap_curve_validates_before_sending(
    app: Any, fake_backend: FakeBackend
) -> None:
    fake_backend.responses["/bootstrap-curves"] = CANNED_BOOTSTRAP
    async with Client(app) as c:
        r = _s(
            await c.call_tool(
                "bootstrap_curve",
                {
                    "curves": [
                        {"id": "c", "points": [], "reference_date": "2025-01-15", "bogus": 1}
                    ],
                    "as_of": "2025-01-15",
                    "queries": [
                        {"curve_id": "c", "measures": ["DF"], "grid": {"grid": {"tenors": []}}}
                    ],
                },
            )
        )
        empty = _s(
            await c.call_tool(
                "bootstrap_curve", {"curves": [], "as_of": "2025-01-15", "queries": []}
            )
        )
    assert r["ok"] is False and r["status"] is None and "nothing was sent" in r["error"]
    assert any(p["path"].startswith("/pricing/rates/curves/0") for p in r["problems"])
    assert empty["ok"] is False and empty["problems"][0]["path"] == "/curves"
    assert not any(e == "/bootstrap-curves" for _, e, _, _ in fake_backend.calls)


async def test_session_put_rejects_schema_mismatch(app: Any) -> None:
    async with Client(app) as c:
        r = _s(
            await c.call_tool(
                "session_put",
                {
                    "name": "x",
                    "kind": "curve",
                    "value": {"id": "c", "points": [], "day_counter": "NotADayCounter"},
                },
            )
        )
        lst = _s(await c.call_tool("session_list", {}))
    assert r["ok"] is False and r["problems"][0]["path"] == "/day_counter"
    assert lst["items"] == []


async def test_session_cap_from_settings() -> None:
    from quantra_mcp.config import Settings
    from quantra_mcp.server import build_server

    be = FakeBackend()
    app = build_server(Settings(engine_url="http://fake", session_max_items=1), backend=be)
    async with Client(app) as c:
        _s(
            await c.call_tool(
                "session_put", {"name": "a", "kind": "index", "value": {"id": "A", "name": "a"}}
            )
        )
        r = _s(
            await c.call_tool(
                "session_put", {"name": "b", "kind": "index", "value": {"id": "B", "name": "b"}}
            )
        )
    assert r["evicted"] == "a" and r["size"] == 1 and r["max_items"] == 1


async def test_build_value_curve_and_inflation_passthrough(
    app: Any, fake_backend: FakeBackend
) -> None:
    async with Client(app) as c:
        vc = _s(
            await c.call_tool(
                "build_value_curve",
                {
                    "id": "VC",
                    "kind": "discount",
                    "points": [
                        {"date": "2025-01-15", "value": 1.0},
                        {"tenor": "1Y", "value": 0.96},
                    ],
                    "reference_date": "2025-01-15",
                    "preset": "USD_SOFR_OIS",
                },
            )
        )
        bad = _s(
            await c.call_tool(
                "build_value_curve",
                {
                    "id": "VC",
                    "kind": "discount",
                    "points": [{"tenor": "1Y", "value": 0.96}],
                    "reference_date": "2025-01-15",
                    "preset": "USD_SOFR_OIS",
                },
            )
        )
        infl = _s(
            await c.call_tool("bootstrap_inflation_curve", {"body": {"pricing": {}, "queries": []}})
        )
    assert (
        vc["ok"]
        and vc["curve"]["bootstrap_trait"] == "InterpolatedDiscount"
        and vc["indices"] == []
    )
    assert vc["curve"]["points"][1]["point"]["discount_factor"] == 0.96
    assert bad["ok"] is False and bad["problems"][0]["path"] == "/points/0"
    assert (
        infl["ok"] is False
        and infl["status"] is None
        and infl["endpoint"] == "/bootstrap-inflation-curves"
    )
    assert any(p["path"] == "/pricing" for p in infl["problems"])
    assert all(m == "GET" for m, *_ in fake_backend.calls)  # nothing POSTed
