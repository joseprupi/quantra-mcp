"""Tool result shape through the real MCP server with a mocked backend."""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp import Client
from mcp.types import TextContent

from quantra_mcp.errors import EngineError, TransportError
from tests.conftest import FakeBackend

OVERRIDE = {
    "calendar": "TARGET",
    "added_holidays": ["2024-06-14"],
    "removed_holidays": ["2024-05-01"],
}


def _structured(result: Any) -> dict[str, Any]:
    assert result.structured_content is not None
    assert isinstance(result.content[0], TextContent)
    assert json.loads(result.content[0].text) == result.structured_content
    return dict(result.structured_content)


async def test_lists_tools_and_resources(app: Any) -> None:
    async with Client(app) as c:
        tools = {t.name for t in (await c.list_tools()).tools}
        assert tools == {
            "quantra_meta",
            "quantra_health",
            "list_endpoints",
            "engine_schema",
            "list_enums",
            "calendar_holidays",
            "calendar_business_days",
            "calendar_advance",
            "engine_request",
            "list_presets",
            "get_preset",
            "build_curve",
            "build_value_curve",
            "curve_from_pasted_table",
            "build_query",
            "bootstrap_curve",
            "bootstrap_inflation_curve",
            "session_put",
            "session_get",
            "session_list",
            "session_delete",
            "price_vanilla_swap",
            "price_ois_swap",
            "price_fixed_rate_bond",
            "price_floating_rate_bond",
            "price_zero_coupon_bond",
            "price_callable_fixed_rate_bond",
            "price_fra",
            "price_cap_floor",
            "price_swaption",
            "price_cds",
            "price_equity_option",
            "price_zc_inflation_swap",
            "price_yoy_inflation_swap",
            "price_yoy_inflation_cap_floor",
            "calibrate_swaption_vol",
            "calibrate_swaption_model",
            "sample_vol_surface",
            "list_examples",
            "get_example",
            "swap_dv01",
            "key_rate_ladder",
            "scenario",
            "fair_rate",
            "explain_method",
            "compare_results",
            "reprice_with",
        }
        uris = {str(r.uri) for r in (await c.list_resources()).resources}
        assert {
            "quantra://docs/http-api",
            "quantra://docs/versioning",
            "quantra://docs/engine-catalog",
            "quantra://pin",
            "quantra://presets",
            "quantra://examples",
            "quantra://methodology",
        } <= uris
        prompts = {p.name for p in (await c.list_prompts()).prompts}
        assert prompts == {
            "price-a-swap",
            "bootstrap-from-strip",
            "holiday-check",
            "explore-examples",
            "price-from-screen",
        }
        templates = {t.uri_template for t in (await c.list_resource_templates()).resource_templates}
        assert templates == {
            "quantra://schema/{endpoint}",
            "quantra://enums/{name}",
            "quantra://examples/{name}",
            "quantra://examples/{category}/{name}",
            "quantra://presets/{id}",
            "quantra://methodology/{topic}",
        }


async def test_calendar_enum_is_typed_in_input_schema(app: Any) -> None:
    async with Client(app) as c:
        tool = next(t for t in (await c.list_tools()).tools if t.name == "calendar_holidays")
    schema = json.dumps(tool.input_schema)
    assert "TARGET" in schema and "UnitedStatesGovernmentBond" in schema
    assert tool.input_schema["required"] == ["calendar", "start_date", "end_date"]
    props = tool.input_schema["properties"]
    assert props["include_weekends"]["default"] is False
    assert "calendar_overrides" in props


async def test_meta_is_verbatim(app: Any, fake_backend: FakeBackend) -> None:
    async with Client(app) as c:
        r = _structured(await c.call_tool("quantra_meta", {}))
    assert r["ok"] is True and r["endpoint"] == "/meta" and r["request"] is None
    assert r["response"] == fake_backend.responses["/meta"]
    assert r["engine"] == {"api_version": "0.7.0", "request_id": None}


async def test_calendar_holidays_echoes_request_and_summarizes(
    app: Any, fake_backend: FakeBackend
) -> None:
    async with Client(app) as c:
        r = _structured(
            await c.call_tool(
                "calendar_holidays",
                {
                    "calendar": "TARGET",
                    "start_date": "2024-04-29",
                    "end_date": "2024-06-21",
                    "calendar_overrides": [OVERRIDE],
                },
            )
        )
    assert r["ok"] is True
    assert r["request"] == {
        "calendar": "TARGET",
        "start_date": "2024-04-29",
        "end_date": "2024-06-21",
        "include_weekends": False,
        "calendar_overrides": [OVERRIDE],
    }
    assert r["response"] == fake_backend.responses["/calendar-holidays"]
    assert r["summary"] == {"count": 1, "first": "2024-06-14", "last": "2024-06-14"}
    assert r["engine"]["api_version"] == "0.7.0"
    assert r["engine"]["request_id"].startswith("qmcp-")
    method, endpoint, body, rid = fake_backend.calls[-1]
    assert (method, endpoint, body) == ("POST", "/calendar-holidays", r["request"])
    assert rid == r["engine"]["request_id"]


async def test_calendar_bad_date_never_reaches_engine(app: Any, fake_backend: FakeBackend) -> None:
    async with Client(app) as c:
        r = _structured(
            await c.call_tool(
                "calendar_business_days",
                {"calendar": "TARGET", "start_date": "2024-02-30", "end_date": "2024-06-21"},
            )
        )
    assert r["ok"] is False and r["status"] is None
    assert r["problems"] == [{"path": "/start_date", "message": "day is out of range for month"}]
    assert not any(e == "/calendar-business-days" for _, e, _, _ in fake_backend.calls)


async def test_calendar_advance(app: Any) -> None:
    async with Client(app) as c:
        r = _structured(
            await c.call_tool(
                "calendar_advance",
                {
                    "calendar": "TARGET",
                    "date": "2024-05-01",
                    "tenor_number": 1,
                    "tenor_unit": "Days",
                    "convention": "Following",
                },
            )
        )
    assert r["ok"] and r["request"]["end_of_month"] is False
    assert r["summary"] == {"input_date": "2024-05-01", "advanced_date": "2024-05-02"}


async def test_engine_error_passes_through_verbatim(app: Any, fake_backend: FakeBackend) -> None:
    fake_backend.fail_with = EngineError(
        400, "Schedule.calendar is required", {"error": "Schedule.calendar is required", "code": 3}
    )
    async with Client(app) as c:
        r = _structured(
            await c.call_tool(
                "calendar_holidays",
                {"calendar": "TARGET", "start_date": "2024-04-29", "end_date": "2024-06-21"},
            )
        )
    assert r["ok"] is False and r["status"] == 400
    assert r["error"] == "Schedule.calendar is required"
    assert r["response"] == {"error": "Schedule.calendar is required", "code": 3}
    assert r["request"]["calendar"] == "TARGET"


async def test_transport_error_is_reported_not_raised(app: Any, fake_backend: FakeBackend) -> None:
    fake_backend.fail_with = TransportError("ConnectError: refused")
    async with Client(app) as c:
        r = _structured(await c.call_tool("quantra_health", {}))
        r2 = _structured(
            await c.call_tool(
                "calendar_holidays",
                {"calendar": "TARGET", "start_date": "2024-04-29", "end_date": "2024-06-21"},
            )
        )
    assert r["ok"] is False and r["status"] is None and "refused" in r["error"]
    assert r2["ok"] is False and r2["status"] is None and r2["request"]["calendar"] == "TARGET"


async def test_engine_request_validates_before_sending(app: Any, fake_backend: FakeBackend) -> None:
    async with Client(app) as c:
        r = _structured(
            await c.call_tool(
                "engine_request",
                {
                    "endpoint": "price-ois-swap",
                    "body": {"pricing": {"as_of_date": 1}, "swaps": [], "nope": 1},
                },
            )
        )
    assert r["ok"] is False and r["status"] is None
    paths = {p["path"] for p in r["problems"]}
    assert "/pricing/as_of_date" in paths and "/" in paths
    assert "validate=false" in r["error"]
    assert fake_backend.calls == [] or all(
        e != "/price-ois-swap" for _, e, _, _ in fake_backend.calls
    )


async def test_engine_request_forwards_with_validate_off_and_custom_request_id(
    app: Any, fake_backend: FakeBackend, ois_example: dict[str, Any]
) -> None:
    fake_backend.responses["/price-ois-swap"] = {"swaps": [{"npv": 1.5}]}
    async with Client(app) as c:
        r = _structured(
            await c.call_tool(
                "engine_request",
                {"endpoint": "/price-ois-swap", "body": ois_example, "request_id": "trace-42"},
            )
        )
        bad = _structured(
            await c.call_tool(
                "engine_request",
                {"endpoint": "/price-ois-swap", "body": {"x": 1}, "validate": False},
            )
        )
    assert (
        r["ok"] is True
        and r["request"] == ois_example
        and r["response"] == {"swaps": [{"npv": 1.5}]}
    )
    assert r["engine"]["request_id"] == "trace-42"
    assert fake_backend.calls[-2][3] == "trace-42"
    assert bad["ok"] is True  # validation skipped, fake engine answered
    assert bad["request"] == {"x": 1}


async def test_engine_request_unknown_endpoint(app: Any) -> None:
    async with Client(app) as c:
        r = _structured(
            await c.call_tool("engine_request", {"endpoint": "/price-everything", "body": {}})
        )
    assert r["ok"] is False and "/price-ois-swap" in r["error"]


async def test_discovery_tools(app: Any) -> None:
    async with Client(app) as c:
        eps = _structured(await c.call_tool("list_endpoints", {}))
        schema = _structured(
            await c.call_tool("engine_schema", {"endpoint": "calendar-advance", "depth": 1})
        )
        enums = _structured(await c.call_tool("list_enums", {"name": "TimeUnit"}))
        bad_enum = _structured(await c.call_tool("list_enums", {"name": "Nope"}))
        bad_ep = _structured(await c.call_tool("engine_schema", {"endpoint": "/nope"}))
    assert eps["count"] == 24 and eps["endpoints"][0]["endpoint"] == "/price-fixed-rate-bond"
    assert schema["required"] == ["date"]
    assert schema["request_schema"]["properties"]["tenor_unit"]["enum"] == enums["values"]
    assert "advanced_date" in schema["response_schema"]["properties"]
    assert bad_enum["ok"] is False and "Calendar" in bad_enum["available"]
    assert bad_ep["ok"] is False and "/calendar-advance" in bad_ep["error"]


async def test_resources(app: Any, ois_example: dict[str, Any]) -> None:
    async with Client(app) as c:
        example = await c.read_resource("quantra://examples/sofr-ois-swap-request")
        enum = await c.read_resource("quantra://enums/Calendar")
        schema = await c.read_resource("quantra://schema/price-ois-swap")
        doc = await c.read_resource("quantra://docs/http-api")
        versioning = await c.read_resource("quantra://docs/versioning")
        pin = await c.read_resource("quantra://pin")
        with pytest.raises(Exception, match="unknown example"):
            await c.read_resource("quantra://examples/nope")
    assert json.loads(example.contents[0].text) == ois_example  # type: ignore[union-attr]
    assert json.loads(enum.contents[0].text)["values"][0] == "Argentina"  # type: ignore[union-attr]
    assert json.loads(schema.contents[0].text)["endpoint"] == "/price-ois-swap"  # type: ignore[union-attr]
    assert doc.contents[0].text.startswith("# HTTP API Contract")  # type: ignore[union-attr]
    assert "0.7.0" in versioning.contents[0].text  # type: ignore[union-attr]
    assert json.loads(pin.contents[0].text)["tag"] == "v0.7.0"  # type: ignore[union-attr]
