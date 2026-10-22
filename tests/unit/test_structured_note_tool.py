"""price_structured_note through the real MCP server with a fake backend."""

from __future__ import annotations

from typing import Any

from mcp import Client

from quantra_mcp import examples_catalog as cat
from quantra_mcp.config import Settings
from quantra_mcp.server import build_server
from tests.conftest import FakeBackend, structured

FIXTURE = "synthetic_two_asset_worst_of_flat_vol_autocall"

CANNED = {
    "notes": [
        {
            "trade_id": "SYNTHETIC_TWO_ASSETS_RHO_0_7",
            "npv": 894.5,
            "npv_pct_of_notional": 89.45,
            "standard_error": 5.6,
            "paths": 4096,
            "seed": 42,
            "status": "Alive",
            "legs": {
                "bond_redemption": {"pv": 1.0},
                "coupons": {"pv": 2.0},
                "capital_loss": {"pv": -3.0},
            },
            "event_probabilities": [{"index": 0, "observation_date": "2027-04-21", "p_call": 0.4}],
            "p_barrier_breach": 0.22,
            "expected_life_years": 1.3,
            "terms": {"trade_id": "SYNTHETIC_TWO_ASSETS_RHO_0_7"},
            "seasoning": {"next_event_index": 0},
            "model": {"time_steps": 4},
            "checks": {"bond_only_npv": 900.0},
        }
    ]
}


async def test_price_structured_note_echoes_and_summarizes() -> None:
    body = cat.load_body(cat.find(FIXTURE))
    fake = FakeBackend({"/price-structured-note": CANNED})
    app = build_server(Settings(engine_url="http://fake"), backend=fake)
    async with Client(app) as c:
        r = structured(
            await c.call_tool(
                "price_structured_note",
                {
                    "market": body["pricing"],
                    "notes": body["notes"],
                    "diagnostics": {"checks": True},
                    "market_data_source": "user_pasted",
                },
            )
        )
    assert r["ok"] is True and r["endpoint"] == "/price-structured-note"
    sent = fake.calls[-1][2]
    assert sent == r["request"]
    assert sent["notes"] == body["notes"] and sent["pricing"] == body["pricing"]
    assert sent["diagnostics"] == {"checks": True} and "greeks" not in sent
    s = r["summary"]["notes"][0]
    assert s["npv"] == 894.5 and s["standard_error"] == 5.6 and s["status"] == "Alive"
    assert s["terms"] and s["seasoning"] and s["model"] and s["legs"]
    assert "checks" not in s  # diagnostics stay in the response
    assert r["market_data_source"] == "user_pasted"


async def test_market_object_form_places_correlations_and_fx() -> None:
    body = cat.load_body(cat.find(FIXTURE))
    p = body["pricing"]
    market: dict[str, Any] = {
        "curves": p["rates"]["curves"],
        "quotes": p["quotes"],
        "vol_surfaces": p["volatility"]["vol_surfaces"],
        "equity_underlyings": p["equity"]["equity_underlyings"],
        "correlation_matrices": p["correlations"]["matrices"],
        "fx_spots": [],
    }
    fake = FakeBackend({"/price-structured-note": CANNED})
    app = build_server(Settings(engine_url="http://fake"), backend=fake)
    async with Client(app) as c:
        r = structured(
            await c.call_tool(
                "price_structured_note",
                {
                    "market": market,
                    "as_of": p["as_of_date"],
                    "notes": body["notes"],
                    "market_data_source": "user_pasted",
                },
            )
        )
    assert r["ok"], r
    sent = r["request"]["pricing"]
    assert sent["correlations"] == p["correlations"]
    assert sent["equity"] == p["equity"] and sent["quotes"] == p["quotes"]


async def test_invalid_note_is_rejected_locally() -> None:
    body = cat.load_body(cat.find(FIXTURE))
    fake = FakeBackend({"/price-structured-note": CANNED})
    app = build_server(Settings(engine_url="http://fake"), backend=fake)
    note = dict(body["notes"][0])
    note.pop("capital")
    async with Client(app) as c:
        r = structured(
            await c.call_tool(
                "price_structured_note",
                {"market": body["pricing"], "notes": [note], "market_data_source": "user_pasted"},
            )
        )
    assert r["ok"] is False and r["status"] is None and r["problems"]
    assert not [call for call in fake.calls if call[1] == "/price-structured-note"]
