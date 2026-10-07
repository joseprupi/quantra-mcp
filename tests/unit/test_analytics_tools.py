"""Analytics tools through the real MCP server with a fake engine whose NPV is a
function of the quotes it receives (so bumps change the number and the
subtractions / sums the tools report can be checked against the fake's own
per-call outputs)."""

from __future__ import annotations

import json
from typing import Any

from mcp import Client

from quantra_mcp.backend.base import BackendResponse
from quantra_mcp.config import Settings
from quantra_mcp.resources import load_example
from quantra_mcp.server import build_server
from tests.conftest import FakeBackend

FIXTURE = "irs_eur_5y_payer_ois_discounted_multicurve"


def _rates(body: dict[str, Any]) -> list[float]:
    out: list[float] = []
    for c in body["pricing"]["rates"]["curves"]:
        for w in c["points"]:
            out.append(float(w["point"]["rate"]))
    return out


class QuoteSensitiveBackend(FakeBackend):
    """A toy engine: npv = -1e6 * sum(rates) so every bump is visible; records each body."""

    def __init__(self) -> None:
        super().__init__()
        self.bodies: dict[str, Any] = {}
        self.inflight = 0
        self.max_inflight = 0

    async def post(
        self, endpoint: str, body: Any, request_id: str | None = None
    ) -> BackendResponse:
        if endpoint not in ("/price-vanilla-swap", "/price-ois-swap"):
            return await super().post(endpoint, body, request_id)
        self.calls.append(("POST", endpoint, body, request_id))
        self.bodies[request_id or ""] = json.loads(json.dumps(body))
        npv = -1e6 * sum(_rates(body))
        return BackendResponse(
            200,
            {"swaps": [{"npv": npv, "fair_rate": 0.0311, "fixed_leg_npv": 1.0}]},
            {"x-quantra-api-version": "0.7.0", "x-request-id": request_id or ""},
        )


def _trade(**over: Any) -> dict[str, Any]:
    base = {
        "product": "vanilla_swap",
        "preset": "EUR_EURIBOR_6M",
        "discounting_curve": "EUR_OIS",
        "forwarding_curve": "EUR_6M_CURVE",
        "index_id": "EUR_6M",
        "swap_type": "Payer",
        "notional": 10_000_000.0,
        "fixed_rate": 0.032,
        "effective_date": "2025-01-17",
        "termination_date": "2030-01-17",
    }
    base.update(over)
    return base


def _market() -> dict[str, Any]:
    return load_example(FIXTURE)["pricing"]


async def _call(backend: FakeBackend, name: str, args: dict[str, Any]) -> dict[str, Any]:
    app = build_server(Settings(engine_url="http://fake"), backend=backend)
    async with Client(app) as c:
        r = await c.call_tool(name, args)
    assert not r.is_error, r.content
    assert r.structured_content is not None
    return dict(r.structured_content)


async def test_swap_dv01_reports_both_calls_and_the_difference() -> None:
    be = QuoteSensitiveBackend()
    r = await _call(be, "swap_dv01", {"market": _market(), "trade": _trade()})
    assert r["ok"], r
    assert r["tool"] == "swap_dv01" and r["scope"] == "all" and r["bump_bp"] == 1.0
    assert r["curves_bumped"] == ["EUR_OIS", "EUR_6M_CURVE"]
    assert [c["label"] for c in r["calls"]] == ["base", "bumped"]
    base, bumped = r["calls"]
    assert base["result"]["ok"] and bumped["result"]["ok"]
    assert r["base_npv"] == base["result"]["response"]["swaps"][0]["npv"]
    assert r["bumped_npv"] == bumped["result"]["response"]["swaps"][0]["npv"]
    assert r["dv01"] == r["bumped_npv"] - r["base_npv"]
    # 8 pillars bumped by 1e-4 each on a -1e6 * sum(rates) toy engine
    assert len(r["bumped_quotes"]) == 8
    assert abs(r["dv01"] + 800.0) < 1e-6
    # the bumped request differs from the base request only in the 8 quotes
    b0, b1 = base["result"]["request"], bumped["result"]["request"]
    assert b0["swaps"] == b1["swaps"] and b0["pricing"]["as_of_date"] == b1["pricing"]["as_of_date"]
    assert [round(y - x, 10) for x, y in zip(_rates(b0), _rates(b1), strict=True)] == [1e-4] * 8
    for e in r["bumped_quotes"]:
        assert e["to"] - e["from"] == 1e-4 or abs(e["to"] - e["from"] - 1e-4) < 1e-12
    # every quote the fake received is the one the tool echoed
    assert be.bodies[bumped["result"]["engine"]["request_id"]] == b1


async def test_swap_dv01_scope_and_spot_dates_are_pinned() -> None:
    be = QuoteSensitiveBackend()
    be.responses["/calendar-advance"] = {"input_date": "2025-01-15", "advanced_date": "2025-01-17"}
    r = await _call(
        be,
        "swap_dv01",
        {
            "market": _market(),
            "trade": _trade(effective_date="spot", termination_date=None, tenor="5Y"),
            "scope": "forwarding",
            "bump_bp": 10,
        },
    )
    assert r["ok"], r
    assert r["curves_bumped"] == ["EUR_6M_CURVE"] and len(r["bumped_quotes"]) == 4
    assert abs(r["dv01"] + 4000.0) < 1e-6
    base, bumped = r["calls"]
    assert len(base["result"]["date_resolution"]) == 2
    assert "date_resolution" not in bumped["result"]  # pinned: no calendar calls on reprice
    assert any("dates pinned" in n for n in r["notes"])
    assert base["result"]["request"]["swaps"] == bumped["result"]["request"]["swaps"]
    advances = [c for c in be.calls if c[1] == "/calendar-advance"]
    assert len(advances) == 2


async def test_key_rate_ladder_buckets_are_the_curves_pillars() -> None:
    be = QuoteSensitiveBackend()
    r = await _call(
        be, "key_rate_ladder", {"market": _market(), "trade": _trade(), "curve": "EUR_6M_CURVE"}
    )
    assert r["ok"], r
    assert [row["pillar"] for row in r["ladder"]] == ["6M", "2Y", "5Y", "10Y"]
    assert [row["point_type"] for row in r["ladder"]] == [
        "DepositHelper",
        "SwapHelper",
        "SwapHelper",
        "SwapHelper",
    ]
    labels = [c["label"] for c in r["calls"]]
    assert labels == ["base", "parallel", "pillar:6M", "pillar:2Y", "pillar:5Y", "pillar:10Y"]
    for row, call in zip(r["ladder"], r["calls"][2:], strict=True):
        assert row["bumped_npv"] == call["result"]["response"]["swaps"][0]["npv"]
        assert row["dv01"] == row["bumped_npv"] - r["base_npv"]
        assert len(call["edits"]) == 1 and call["edits"][0]["pillar"] == row["pillar"]
    assert r["sum_of_buckets"] == sum(row["dv01"] for row in r["ladder"])
    assert abs(r["parallel"]["dv01"] + 400.0) < 1e-6
    assert abs(r["sum_of_buckets"] + 400.0) < 1e-6
    assert r["parallel"]["bumped_npv"] == r["calls"][1]["result"]["response"]["swaps"][0]["npv"]


async def test_key_rate_ladder_defaults_to_the_discounting_curve() -> None:
    r = await _call(
        QuoteSensitiveBackend(), "key_rate_ladder", {"market": _market(), "trade": _trade()}
    )
    assert r["ok"] and r["curve"] == "EUR_OIS"
    assert [row["pillar"] for row in r["ladder"]] == ["1Y", "2Y", "5Y", "10Y"]
    assert any("defaulted to trade.discounting_curve" in n for n in r["notes"])


async def test_concurrency_is_bounded() -> None:
    import asyncio

    class SlowBackend(QuoteSensitiveBackend):
        async def post(
            self, endpoint: str, body: Any, request_id: str | None = None
        ) -> BackendResponse:
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
            try:
                await asyncio.sleep(0.01)
                return await super().post(endpoint, body, request_id)
            finally:
                self.inflight -= 1

    be = SlowBackend()
    app = build_server(Settings(engine_url="http://fake", max_concurrency=2), backend=be)
    async with Client(app) as c:
        r = await c.call_tool("key_rate_ladder", {"market": _market(), "trade": _trade()})
    assert r.structured_content is not None and r.structured_content["ok"]
    assert be.max_inflight == 2
    assert any("at most 2 concurrent" in n for n in r.structured_content["notes"])


async def test_scenario_table_zero_bump_reproduces_base() -> None:
    be = QuoteSensitiveBackend()
    r = await _call(
        be,
        "scenario",
        {
            "market": _market(),
            "trade": _trade(),
            "scenarios": [
                {"name": "flat", "bumps": [{"curve": "EUR_OIS", "bp": 0}]},
                {
                    "name": "up50",
                    "bumps": [{"curve": "EUR_OIS", "bp": 50}, {"curve": "EUR_6M_CURVE", "bp": 50}],
                },
                {
                    "name": "dn50",
                    "bumps": [
                        {"curve": "EUR_OIS", "bp": -50},
                        {"curve": "EUR_6M_CURVE", "bp": -50},
                    ],
                },
                {"name": "5y_steep", "bumps": [{"curve": "EUR_6M_CURVE", "bp": 5, "pillar": "5Y"}]},
                {
                    "name": "set_10y",
                    "replace_quotes": [{"curve": "EUR_6M_CURVE", "pillar": 3, "value": 0.04}],
                },
            ],
        },
    )
    assert r["ok"], r
    names = [row["name"] for row in r["table"]]
    assert names == ["base", "flat", "up50", "dn50", "5y_steep", "set_10y"]
    by = {row["name"]: row for row in r["table"]}
    assert by["flat"]["npv"] == r["base_npv"] and by["flat"]["change"] == 0.0
    assert (
        abs(by["up50"]["change"] + 40_000.0) < 1e-6 and abs(by["dn50"]["change"] - 40_000.0) < 1e-6
    )
    assert abs(by["5y_steep"]["change"] + 500.0) < 1e-6
    assert abs(by["set_10y"]["change"] - (-1e6 * (0.04 - 0.032))) < 1e-6
    for row, call in zip(r["table"][1:], r["calls"][1:], strict=True):
        assert row["npv"] == call["result"]["response"]["swaps"][0]["npv"]
        assert row["change"] == row["npv"] - r["base_npv"]
        assert row["edits"] == len(call["edits"])
    assert by["up50"]["edits"] == 8 and by["5y_steep"]["edits"] == 1


async def test_scenario_unknown_pillar_is_a_local_error() -> None:
    r = await _call(
        QuoteSensitiveBackend(),
        "scenario",
        {
            "market": _market(),
            "trade": _trade(),
            "scenarios": [{"name": "x", "bumps": [{"curve": "EUR_OIS", "bp": 1, "pillar": "7Y"}]}],
        },
    )
    assert r["ok"] is False and "unknown pillar '7Y'" in r["error"]
    assert r["calls"] == [] and r["problems"][0]["path"] == "/scenarios/0/bumps/0/pillar"


async def test_fair_rate_is_read_from_the_response() -> None:
    be = QuoteSensitiveBackend()
    r = await _call(be, "fair_rate", {"market": _market(), "trade": _trade()})
    assert r["ok"] and r["fair_rate"] == 0.0311 and r["fair_spread"] is None
    assert r["provided_by_engine"] is True and r["message"] is None
    assert r["npv"] == r["calls"][0]["result"]["response"]["swaps"][0]["npv"]

    class NoFair(QuoteSensitiveBackend):
        async def post(
            self, endpoint: str, body: Any, request_id: str | None = None
        ) -> BackendResponse:
            resp = await super().post(endpoint, body, request_id)
            if endpoint.startswith("/price-"):
                resp.body["swaps"][0].pop("fair_rate")
            return resp

    r = await _call(NoFair(), "fair_rate", {"market": _market(), "trade": _trade()})
    assert r["ok"] and r["fair_rate"] is None and r["provided_by_engine"] is False
    assert "not provided by the engine" in r["message"]


async def test_engine_failure_on_reprice_is_reported_with_the_calls() -> None:
    be = QuoteSensitiveBackend()
    r = await _call(
        be,
        "swap_dv01",
        {
            "market": _market(),
            "trade": _trade(
                product="ois_swap",
                preset="EUR_ESTR_OIS",
                discounting_curve="EUR_OIS",
                forwarding_curve="EUR_OIS",
                index_id="EUR_ESTR",
            ),
        },
    )
    assert r["ok"] and r["product"] == "ois_swap" and r["curves_bumped"] == ["EUR_OIS"]
    assert r["calls"][0]["result"]["endpoint"] == "/price-ois-swap"

    r = await _call(be, "swap_dv01", {"market": _market(), "trade": _trade(payment_lag=2)})
    assert r["ok"] is False and "apply to ois_swap only" in r["error"]

    r = await _call(be, "swap_dv01", {"market": _market(), "trade": _trade(), "bump_bp": 0})
    assert r["ok"] is False and "non-zero" in r["error"]

    r = await _call(
        be, "swap_dv01", {"market": _market(), "trade": _trade(discounting_curve="NOPE")}
    )
    assert r["ok"] is False and "NOPE" in r["error"]

    r = await _call(be, "swap_dv01", {"market": _market(), "trade": _trade(preset="NOPE")})
    assert r["ok"] is False and "NOPE" in r["error"]
