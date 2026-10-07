"""Live acceptance against a real engine. Skipped unless ``QUANTRA_ENGINE_URL`` is set.

Every tool result's ``response`` must equal a direct HTTP replay of its echoed
``request`` (byte-equal JSON), and the published oracle must hold.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest
from mcp import Client

from quantra_mcp.config import Settings
from quantra_mcp.resources import load_example
from quantra_mcp.server import build_server

ENGINE_URL = os.environ.get("QUANTRA_ENGINE_URL", "").rstrip("/")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not ENGINE_URL, reason="QUANTRA_ENGINE_URL not set"),
]

OIS_NPV_ORACLE = "337986.7913"  # quantra.io/blog/bootstrapping-a-sofr-curve
BOOTSTRAP_50Y_DF_ORACLE = "0.262755579831"

OVERRIDE = {
    "calendar": "TARGET",
    "added_holidays": ["2024-06-14"],
    "removed_holidays": ["2024-05-01"],
}


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


async def replay(endpoint: str, body: Any) -> Any:
    async with httpx.AsyncClient(base_url=ENGINE_URL, timeout=60) as http:
        r = await http.post(endpoint, json=body)
        r.raise_for_status()
        return r.json()


def live_client() -> Client:
    """A fresh in-memory client per test (a shared async fixture would cross anyio task scopes)."""
    return Client(build_server(Settings(engine_url=ENGINE_URL)))


async def _call(client: Client, name: str, args: dict[str, Any]) -> dict[str, Any]:
    r = await client.call_tool(name, args)
    assert not r.is_error, r.content
    assert r.structured_content is not None
    return dict(r.structured_content)


async def test_meta_and_health() -> None:
    async with live_client() as client:
        meta = await _call(client, "quantra_meta", {})
        health = await _call(client, "quantra_health", {})
    assert meta["ok"] and meta["request"] is None
    # X-Quantra-Api-Version is stamped on POST responses only; GET /meta carries it in the body
    assert meta["response"]["openapi_version"] == "0.7.0"
    assert health["ok"] and health["response"]["status"] == "healthy"
    async with httpx.AsyncClient(base_url=ENGINE_URL) as http:
        assert (await http.get("/meta")).json() == meta["response"]


async def test_holidays_with_override_matches_doc_example() -> None:
    async with live_client() as client:
        r = await _call(
            client,
            "calendar_holidays",
            {
                "calendar": "TARGET",
                "start_date": "2024-04-29",
                "end_date": "2024-06-21",
                "calendar_overrides": [OVERRIDE],
            },
        )
        assert r["ok"], r
        dates = r["response"]["dates"]
        assert "2024-06-14" in dates and "2024-05-01" not in dates
        assert r["summary"]["count"] == r["response"]["count"]
        assert canonical(r["response"]) == canonical(
            await replay("/calendar-holidays", r["request"])
        )
        # and without the override May 1 is a TARGET holiday
        base = await _call(
            client,
            "calendar_holidays",
            {"calendar": "TARGET", "start_date": "2024-04-29", "end_date": "2024-06-21"},
        )
        assert (
            "2024-05-01" in base["response"]["dates"]
            and "2024-06-14" not in base["response"]["dates"]
        )


async def test_business_days_and_advance_replay() -> None:
    async with live_client() as client:
        bd = await _call(
            client,
            "calendar_business_days",
            {
                "calendar": "UnitedStates",
                "start_date": "2024-05-24",
                "end_date": "2024-05-28",
                "calendar_overrides": [],
            },
        )
        assert bd["ok"] and "2024-05-27" not in bd["response"]["dates"]  # Memorial Day
        assert canonical(bd["response"]) == canonical(
            await replay("/calendar-business-days", bd["request"])
        )
        adv = await _call(
            client,
            "calendar_advance",
            {
                "calendar": "TARGET",
                "date": "2024-04-30",
                "tenor_number": 1,
                "tenor_unit": "Days",
                "convention": "Following",
            },
        )
        assert (
            adv["ok"] and adv["response"]["advanced_date"] == "2024-05-02"
        )  # May 1 is a TARGET holiday
        assert canonical(adv["response"]) == canonical(
            await replay("/calendar-advance", adv["request"])
        )


async def test_ois_example_matches_published_npv() -> None:
    async with live_client() as client:
        body = load_example("sofr-ois-swap-request")
        r = await _call(client, "engine_request", {"endpoint": "/price-ois-swap", "body": body})
        assert r["ok"], r
        npv = r["response"]["swaps"][0]["npv"]
        assert repr(npv).startswith(OIS_NPV_ORACLE), npv
        assert r["request"] == body
        assert canonical(r["response"]) == canonical(await replay("/price-ois-swap", r["request"]))


async def test_bootstrap_example_matches_published_50y_df() -> None:
    async with live_client() as client:
        body = load_example("sofr-bootstrap-request")
        r = await _call(client, "engine_request", {"endpoint": "/bootstrap-curves", "body": body})
        assert r["ok"], r
        assert canonical(r["response"]) == canonical(
            await replay("/bootstrap-curves", r["request"])
        )
        text = canonical(r["response"])
        assert BOOTSTRAP_50Y_DF_ORACLE in text, text[:500]


async def test_engine_400_and_422_pass_through() -> None:
    async with live_client() as client:
        bad = await _call(
            client,
            "engine_request",
            {
                "endpoint": "/price-ois-swap",
                "body": {"pricing": {}, "swaps": []},
                "validate": False,
            },
        )
        assert bad["ok"] is False and bad["status"] == 400 and bad["error"]
        assert bad["response"]["error"] == bad["error"]
        assert bad["engine"]["api_version"]
