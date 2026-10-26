"""calibrate_implied_forwards / calibrate_vol_surface (engine 0.14.0) through the real MCP
server with a fake backend; plus the 0.13.0 ``is_final`` event on structured notes."""

from __future__ import annotations

import copy
from typing import Any

from mcp import Client

from quantra_mcp import examples_catalog as cat
from quantra_mcp.config import Settings
from quantra_mcp.schema.validate import validate_request
from quantra_mcp.server import build_server
from tests.conftest import FakeBackend, structured

FWD = "synthetic_ndx_like_2026-09-30"
SURF = "synthetic_svi_sheet_2026-09-30_svi"

FWD_CANNED: dict[str, Any] = {
    "sheet_id": "S",
    "underlying_id": "NDX_SYN",
    "spot": 24000.0,
    "filter": {"max_relative_spread": 0.05, "min_quotes_per_expiry": 4, "moneyness_window": 0.25},
    "expiries": [
        {
            "expiry": "2026-10-16",
            "time": 0.0438,
            "quotes_used": 20,
            "quotes_dropped": 3,
            "dropped": [
                {"expiry": "2026-10-16", "strike": 1.0, "reason": "negative bid"},
                {"expiry": "2026-10-16", "strike": 2.0, "reason": "negative bid"},
                {"expiry": "2026-10-16", "strike": 3.0, "reason": "outside window"},
            ],
            "usable": True,
            "forward": 24033.6,
            "implied_discount_factor": 0.998,
            "curve_discount_factor": 0.998,
            "implied_funding_bp": 0.0,
            "implied_dividend_yield": 0.008,
            "fit_residual_rmse": 1e-12,
            "max_abs_residual": 2e-12,
        },
        {"expiry": "2026-11-20", "usable": False, "dropped_reason": "too few quotes"},
    ],
    "forwards": [{"expiry": "2026-10-16", "forward": 24033.6}],
    "dividend_curve_spec": {"id": "DIV", "bootstrap_trait": "InterpolatedZero", "points": []},
}

SURF_CANNED: dict[str, Any] = {
    "sheet_id": "S",
    "underlying_id": "NDX_SYN",
    "spot": 24000.0,
    "method": "SviPerExpiry",
    "forwards_source": "fitted",
    "filter": {"max_relative_spread": 0.05},
    "forwards": [],
    "expiries": [
        {
            "expiry": "2026-10-16",
            "usable": True,
            "quotes_used": 17,
            "quotes_dropped": 1,
            "dropped": [{"reason": "below intrinsic"}],
            "rmse_vol": 1e-12,
            "max_abs_error_vol": 2e-12,
            "svi": {"a": 0.1, "b": 0.2, "sigma": 0.3, "rho": -0.7, "m": 0.0},
            "butterfly_min_g": 0.27,
            "butterfly_violation": False,
            "butterfly_action": "none",
            "calendar_violations": 0,
            "calendar_action": "none",
        }
    ],
    "strikes_as_moneyness": [0.5, 1.0],
    "grid_strikes": [12000.0, 24000.0],
    "grid_expiries": ["2026-10-16"],
    "vol_surface_spec": {"id": "VOL", "payload_type": "BlackVolSpec", "payload": {}},
    "vanilla_repricing": {
        "count": 17,
        "inside_bid_ask": 17,
        "max_abs_price_error": 1e-9,
        "max_abs_vol_error": 1e-12,
        "quotes": [{"strike": 1.0}],
    },
}


def _app(fake: FakeBackend) -> Any:
    return build_server(Settings(engine_url="http://fake"), backend=fake)


async def test_implied_forwards_echoes_and_summarizes() -> None:
    body = cat.load_body(cat.find(FWD))
    fake = FakeBackend({"/calibrate-implied-forwards": FWD_CANNED})
    async with Client(_app(fake)) as c:
        r = structured(
            await c.call_tool(
                "calibrate_implied_forwards",
                {
                    "market": body["pricing"],
                    "sheet": body["sheet"],
                    "discounting_curve": body["discounting_curve"],
                    "dividend_curve_id": body["dividend_curve_id"],
                    "filter": body["filter"],
                    "market_data_source": "engine_example",
                },
            )
        )
    assert r["ok"] is True and r["endpoint"] == "/calibrate-implied-forwards"
    sent = fake.calls[-1][2]
    assert sent == r["request"] == body  # the example, rebuilt byte for byte
    s = r["summary"]
    assert s["expiries_fitted"] == 1 and s["expiries_total"] == 2
    assert s["quotes_dropped_total"] == 3
    assert s["expiries"][0]["dropped_reasons"] == {"negative bid": 2, "outside window": 1}
    assert "dropped" not in s["expiries"][0]
    assert s["expiries"][1]["dropped_reason"] == "too few quotes"
    assert s["reuse"]["market.curves"] == "response.dividend_curve_spec"
    assert s["reuse"]["id"] == "DIV"
    assert r["market_data_source"] == "engine_example"


async def test_vol_surface_echoes_and_summarizes() -> None:
    body = cat.load_body(cat.find(SURF))
    fake = FakeBackend({"/calibrate-vol-surface": SURF_CANNED})
    async with Client(_app(fake)) as c:
        r = structured(
            await c.call_tool(
                "calibrate_vol_surface",
                {
                    "market": body["pricing"],
                    "sheet": body["sheet"],
                    "discounting_curve": body["discounting_curve"],
                    "vol_surface_id": body["vol_surface_id"],
                    "method": body["method"],
                    "filter": body["filter"],
                    "market_data_source": "engine_example",
                },
            )
        )
    assert r["ok"] is True
    assert fake.calls[-1][2] == r["request"] == body
    s = r["summary"]
    assert s["method"] == "SviPerExpiry" and s["forwards_source"] == "fitted"
    assert s["vanilla_repricing"] == {
        "count": 17,
        "inside_bid_ask": 17,
        "max_abs_price_error": 1e-9,
        "max_abs_vol_error": 1e-12,
    }
    e = s["expiries"][0]
    assert e["svi"]["rho"] == -0.7 and e["butterfly_action"] == "none"
    assert e["dropped_reasons"] == {"below intrinsic": 1}
    assert s["reuse"]["market.vol_surfaces"] == "response.vol_surface_spec"
    assert s["grid"] == {"strikes": 2, "expiries": ["2026-10-16"]}


async def test_method_is_required_and_source_declared() -> None:
    body = cat.load_body(cat.find(SURF))
    fake = FakeBackend({"/calibrate-vol-surface": SURF_CANNED})
    args = {
        "market": body["pricing"],
        "sheet": body["sheet"],
        "discounting_curve": body["discounting_curve"],
        "vol_surface_id": "V",
        "market_data_source": "user_pasted",
    }
    async with Client(_app(fake)) as c:
        r = await c.call_tool("calibrate_vol_surface", args)
        assert r.is_error and "method" in r.content[0].text  # type: ignore[union-attr]
        no_src = {k: v for k, v in args.items() if k != "market_data_source"}
        r2 = await c.call_tool("calibrate_vol_surface", {**no_src, "method": "AndreasenHuge"})
        assert r2.is_error and "market_data_source" in r2.content[0].text  # type: ignore[union-attr]
        r3 = await c.call_tool(
            "calibrate_vol_surface", {**no_src, "method": "x", "market_data_source": "user_file"}
        )
        assert r3.is_error
    assert not [c for c in fake.calls if c[0] == "POST"]


async def test_local_checks_name_the_missing_piece() -> None:
    body = cat.load_body(cat.find(FWD))
    fake = FakeBackend({"/calibrate-implied-forwards": FWD_CANNED})
    sheet = copy.deepcopy(body["sheet"])
    sheet["underlying_id"] = "NOPE"
    async with Client(_app(fake)) as c:
        r = structured(
            await c.call_tool(
                "calibrate_implied_forwards",
                {
                    "market": body["pricing"],
                    "sheet": sheet,
                    "discounting_curve": body["discounting_curve"],
                    "dividend_curve_id": "D",
                    "market_data_source": "user_pasted",
                },
            )
        )
        assert r["ok"] is False and r["status"] is None and "NOPE" in r["error"]
        r2 = structured(
            await c.call_tool(
                "calibrate_implied_forwards",
                {
                    "market": body["pricing"],
                    "sheet": body["sheet"],
                    "discounting_curve": "NO_CURVE",
                    "dividend_curve_id": "D",
                    "market_data_source": "user_pasted",
                },
            )
        )
        assert r2["ok"] is False and "NO_CURVE" in r2["error"]
    assert not [c for c in fake.calls if c[0] == "POST"]


def test_is_final_event_validates_against_the_pin() -> None:
    body = cat.load_body(cat.find("synthetic_two_asset_worst_of_flat_vol_autocall"))
    note = copy.deepcopy(body["notes"][0])
    last = note["events"][-1]
    note["events"][-1] = {
        "observation_date": last["observation_date"],
        "payment_date": note["maturity_payment_date"],
        "is_final": True,
    }
    assert validate_request("/price-structured-note", {**body, "notes": [note]}) == []
    note["events"][-1]["is_final"] = "yes"
    assert validate_request("/price-structured-note", {**body, "notes": [note]}) != []
