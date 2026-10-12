"""Builder digests: stamped on build results and session items, recognised by the
pricing and analytics tools, never sent to the engine (M5.6)."""

from __future__ import annotations

import json
from typing import Any

from mcp import Client

from quantra_mcp.digest import DigestRegistry, canonical_digest
from quantra_mcp.resources import load_example
from quantra_mcp.session import bind_session
from tests.conftest import FakeBackend, structured
from tests.strips import STRIPS

FIXTURE = "irs_eur_5y_payer_ois_discounted_multicurve"


def test_registry_is_per_session_and_bounded() -> None:
    reg = DigestRegistry(per_bucket=2, max_buckets=2)
    with bind_session("A"):
        reg.remember("d1", "d2", "d3")
        assert not reg.known("d1") and reg.known("d2") and reg.known("d3")
    with bind_session("B"):
        assert not reg.known("d3")
        reg.remember("x")
    with bind_session(None):  # the process-wide bucket (stdio / in-process)
        reg.remember("p")
        assert reg.known("p") and not reg.known("x")
    with bind_session("A"):
        assert not reg.known("d3")  # bucket A was the least recently used and went
    assert canonical_digest({"b": 1, "a": [1, 2]}) == canonical_digest({"a": [1, 2], "b": 1})


async def test_build_results_and_session_items_carry_the_digest(app: Any) -> None:
    async with Client(app) as c:
        built = structured(
            await c.call_tool(
                "build_curve",
                {
                    "market_data_source": "user_pasted",
                    "id": "USD_SOFR_OIS",
                    "preset": "USD_SOFR_OIS",
                    "quotes": STRIPS["USD_SOFR_OIS"]["quotes"],
                    "reference_date": "2025-01-15",
                },
            )
        )
        put = structured(
            await c.call_tool(
                "session_put",
                {
                    "market_data_source": "user_pasted",
                    "name": "sofr",
                    "kind": "curve",
                    "value": built,
                },
            )
        )
        got = structured(await c.call_tool("session", {"action": "get", "name": "sofr"}))
    assert built["digest"] == canonical_digest(
        {"curve": built["curve"], "indices": built["indices"]}
    )
    assert built["curve_digest"] == canonical_digest(built["curve"])
    assert built["digest"].startswith("sha256:") and built["digest"] != built["curve_digest"]
    # the stored curve item's digest is the curve's own digest
    assert put["item"]["digest"] == built["curve_digest"] == got["digest"]
    # the engine never sees it
    assert "digest" not in json.dumps(built["curve"])


async def test_pricing_tools_say_whether_the_market_came_from_a_builder() -> None:
    backend = FakeBackend({"/price-vanilla-swap": {"swaps": [{"npv": 1.0, "fair_rate": 0.03}]}})
    from quantra_mcp.config import Settings
    from quantra_mcp.server import build_server

    app = build_server(Settings(engine_url="http://fake"), backend=backend)
    market = load_example(FIXTURE)["pricing"]
    trade = {
        "preset": "EUR_EURIBOR_6M",
        "discounting_curve": "EUR_OIS",
        "forwarding_curve": "EUR_6M_CURVE",
        "index_id": "EUR_6M",
        "swap_type": "Payer",
        "notional": 1e7,
        "fixed_rate": 0.032,
        "effective_date": "2025-01-17",
        "termination_date": "2030-01-17",
    }
    async with Client(app) as c:
        hand = structured(
            await c.call_tool(
                "price_vanilla_swap",
                {"market": market, "market_data_source": "engine_example", **trade},
            )
        )
        built = structured(
            await c.call_tool(
                "build_curve",
                {
                    "market_data_source": "user_pasted",
                    "id": "EUR_OIS",
                    "preset": "EUR_ESTR_OIS",
                    "quotes": STRIPS["EUR_ESTR_OIS"]["quotes"],
                    "reference_date": "2025-01-15",
                },
            )
        )
        mixed = structured(
            await c.call_tool(
                "price_vanilla_swap",
                {
                    "market": {
                        "curves": [built, market["rates"]["curves"][1]],
                        "indices": market["rates"]["indices"],
                    },
                    "as_of": "2025-01-15",
                    "market_data_source": "user_pasted",
                    **trade,
                },
            )
        )
        dv = structured(
            await c.call_tool(
                "swap_dv01",
                {
                    "market": market,
                    "market_data_source": "engine_example",
                    "trade": {"product": "vanilla_swap", **trade},
                },
            )
        )
    assert hand["ok"] and all(not v["from_builder"] for v in hand["market_digests"].values())
    assert sum("market block not produced by a builder" in n for n in hand["notes"]) == 2
    assert mixed["ok"]
    assert mixed["market_digests"]["EUR_OIS"] == {
        "digest": built["curve_digest"],
        "from_builder": True,
    }
    assert mixed["market_digests"]["EUR_6M_CURVE"]["from_builder"] is False
    assert any("from a builder result in this session" in n for n in mixed["notes"])
    assert "digest" not in json.dumps(mixed["request"])
    assert dv["ok"] and any("market block not produced by a builder" in n for n in dv["notes"])


async def test_meta_api_version_falls_back_to_the_body() -> None:
    class HeaderlessBackend(FakeBackend):
        async def get(self, path: str) -> Any:
            resp = await super().get(path)
            resp.headers.pop("x-quantra-api-version", None)
            return resp

    from quantra_mcp.config import Settings
    from quantra_mcp.server import build_server

    app = build_server(Settings(engine_url="http://fake"), backend=HeaderlessBackend())
    async with Client(app) as c:
        meta = structured(await c.call_tool("quantra_meta", {}))
    assert meta["engine"]["api_version"] == "0.7.0"  # from the body's openapi_version
