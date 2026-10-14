"""Every tool: a title, MCP annotations, a description under the ceiling, every argument
(top-level and nested) described, no pydantic ``title`` keys, enums and patterns where
the arguments are dates, tenors or closed choices (M5.6)."""

from __future__ import annotations

import json
from typing import Any

from mcp import Client

from quantra_mcp.tools._meta import MAX_DESCRIPTION_CHARS

WRITERS = {"session_put", "session"}
ENGINE_CALLERS_PREFIX = ("price_", "calendar_")
ENGINE_CALLERS = {
    "quantra_meta",
    "engine_request",
    "bootstrap_curve",
    "swap_dv01",
    "key_rate_ladder",
    "scenario",
    "fair_rate",
    "reprice_with",
}


def _props(schema: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out = [(k, v) for k, v in schema.get("properties", {}).items()]
    for dname, d in schema.get("$defs", {}).items():
        out += [(f"{dname}.{k}", v) for k, v in d.get("properties", {}).items()]
    return out


async def test_every_tool_is_titled_annotated_and_described(app: Any) -> None:
    async with Client(app) as c:
        tools = (await c.list_tools()).tools
    assert len(tools) == 41
    for t in tools:
        assert t.title, t.name
        a = t.annotations
        assert a is not None and a.title == t.title, t.name
        assert a.destructive_hint is False and a.idempotent_hint is True, t.name
        assert a.read_only_hint == (t.name not in WRITERS), t.name
        engine = t.name in ENGINE_CALLERS or t.name.startswith(ENGINE_CALLERS_PREFIX)
        assert a.open_world_hint == engine, t.name
        desc = t.description or ""
        assert 0 < len(desc) <= MAX_DESCRIPTION_CHARS, (t.name, len(desc))
        assert not desc.startswith(" ") and "\n        " not in desc, t.name  # dedented
        schema = t.input_schema
        assert "title" not in schema, t.name
        for path, prop in _props(schema):
            assert "title" not in prop, (t.name, path)
            assert prop.get("description"), (t.name, path)


async def test_dates_tenors_and_choices_are_constrained(app: Any) -> None:
    async with Client(app) as c:
        listed = (await c.list_tools()).tools
        tools = {t.name: t.input_schema["properties"] for t in listed}
        full = {t.name: json.dumps(t.input_schema) for t in listed}
    date = r"^\d{4}-\d{2}-\d{2}$"
    assert tools["calendar_holidays"]["start_date"]["pattern"] == date
    assert tools["bootstrap_curve"]["as_of"]["pattern"] == date
    assert tools["price_vanilla_swap"]["effective_date"]["pattern"] == r"^(\d{4}-\d{2}-\d{2}|spot)$"
    tenor = tools["price_vanilla_swap"]["tenor"]
    assert any(a.get("pattern") == r"^\d+[DWMY]$" for a in tenor["anyOf"]), tenor
    assert tools["price_swaption"]["underlying_type"]["enum"] == ["VanillaSwap", "OisSwap"]
    assert tools["price_equity_option"]["exercise"]["enum"] == ["European", "American", "Bermudan"]
    sw = full["price_swaption"]
    assert "#/$defs/SettlementType" in sw and "#/$defs/SettlementMethod" in sw
    assert "#/$defs/IrModelType" in sw and "#/$defs/VolatilityType" in sw
    assert "#/$defs/IrModelType" in json.dumps(tools["price_cap_floor"]["model"])
    assert "#/$defs/CdsEngineType" in json.dumps(tools["price_cds"]["model"])
    assert tools["session"]["action"]["enum"] == ["get", "list", "delete"]
    assert tools["swap_dv01"]["include_calls"]["default"] is False
