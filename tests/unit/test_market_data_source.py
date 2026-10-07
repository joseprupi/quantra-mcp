"""M5.3 mechanical guard: every tool that takes market numbers requires an explicit
``market_data_source`` declaration drawn from exactly four values, none of which
describes invented data; the declaration is echoed in the result and stored with
session items."""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp import Client

from quantra_mcp.config import Settings
from quantra_mcp.server import build_server
from quantra_mcp.session import SessionStore
from quantra_mcp.tools._market_source import MARKET_DATA_SOURCES, check_source, stamp
from quantra_mcp.tools.session import session_put_impl
from tests.conftest import FakeBackend
from tests.product_cases import product_cases
from tests.strips import STRIPS
from tests.unit.test_pricing_tools import CANNED

EXPECTED_VALUES = ["user_pasted", "user_file", "engine_example", "session"]

GUARDED_TOOLS = {
    "build_curve",
    "build_value_curve",
    "curve_from_pasted_table",
    "session_put",
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
    "swap_dv01",
    "key_rate_ladder",
    "scenario",
    "fair_rate",
}

#: carries the declaration of the result it reprices, so the argument is optional there
CARRYING_TOOLS = {"reprice_with"}

SOFR = STRIPS["USD_SOFR_OIS"]
BUILD_ARGS: dict[str, Any] = {
    "id": "USD_SOFR_OIS",
    "preset": "USD_SOFR_OIS",
    "quotes": SOFR["quotes"],
    "reference_date": SOFR["reference_date"],
}


def _s(result: Any) -> dict[str, Any]:
    assert result.structured_content is not None, result.content
    return dict(result.structured_content)


@pytest.fixture
def pricing_app():  # type: ignore[no-untyped-def]
    return build_server(Settings(engine_url="http://fake"), backend=FakeBackend(CANNED))


def test_enum_is_exactly_the_four_values() -> None:
    assert list(MARKET_DATA_SOURCES) == EXPECTED_VALUES
    for v in EXPECTED_VALUES:
        assert check_source(v) == v
    for bad in ("estimated", "placeholder", "recalled", "", None, 1):
        with pytest.raises(Exception, match="no value for estimated, recalled or placeholder"):
            check_source(bad)


async def test_every_guarded_tool_requires_the_enum(app: Any) -> None:
    async with Client(app) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    guarded = {n for n, t in tools.items() if "market_data_source" in t.input_schema["properties"]}
    assert guarded == GUARDED_TOOLS | CARRYING_TOOLS
    # every price_* tool that takes a market is guarded
    assert {n for n in tools if n.startswith("price_")} <= GUARDED_TOOLS
    for name in sorted(GUARDED_TOOLS):
        schema = tools[name].input_schema
        prop = schema["properties"]["market_data_source"]
        assert prop["enum"] == EXPECTED_VALUES, name
        assert prop["type"] == "string", name
        assert "market_data_source" in schema["required"], name
        assert "default" not in prop, name
        desc = " ".join(((tools[name].description or "") + json.dumps(prop)).split())
        assert "There is no value for estimated, recalled or placeholder data" in desc, name
        assert "do not call this tool: ask the user for the data" in desc, name
    for name in sorted(CARRYING_TOOLS):
        schema = tools[name].input_schema
        prop = schema["properties"]["market_data_source"]
        assert "market_data_source" not in schema["required"], name
        enum = next(a for a in prop["anyOf"] if a.get("type") == "string")
        assert enum["enum"] == EXPECTED_VALUES, name
        assert prop["default"] is None, name
        desc = " ".join(((tools[name].description or "") + json.dumps(prop)).split())
        assert "There is no value for estimated, recalled or placeholder data" in desc, name
        assert "do not call this tool: ask the user for the data" in desc, name


async def test_call_without_or_with_invalid_source_fails_validation(app: Any) -> None:
    async with Client(app) as c:
        missing = await c.call_tool("build_curve", BUILD_ARGS)
        assert missing.is_error and missing.structured_content is None
        assert "market_data_source" in missing.content[0].text  # type: ignore[union-attr]
        for bad in ("estimated", "placeholder", "approximate", "from_memory"):
            r = await c.call_tool("build_curve", {**BUILD_ARGS, "market_data_source": bad})
            assert r.is_error and r.structured_content is None, bad
            assert "'user_pasted', 'user_file', 'engine_example' or 'session'" in (
                r.content[0].text  # type: ignore[union-attr]
            )
        # the same for a pricing tool and for session_put
        case = next(k for k in product_cases() if k.tool == "price_vanilla_swap")
        args = {k: v for k, v in case.explicit.items() if k != "market_data_source"}
        r = await c.call_tool("price_vanilla_swap", args)
        assert r.is_error and "market_data_source" in r.content[0].text  # type: ignore[union-attr]
        r = await c.call_tool(
            "session_put", {"name": "x", "kind": "curve", "value": {"id": "c", "points": []}}
        )
        assert r.is_error and "market_data_source" in r.content[0].text  # type: ignore[union-attr]


@pytest.mark.parametrize("source", EXPECTED_VALUES)
async def test_curve_tools_echo_the_declared_source(app: Any, source: str) -> None:
    async with Client(app) as c:
        built = _s(await c.call_tool("build_curve", {**BUILD_ARGS, "market_data_source": source}))
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
                    "market_data_source": source,
                },
            )
        )
        pasted = _s(
            await c.call_tool(
                "curve_from_pasted_table",
                {
                    "text": "2025-01-15 1.0\n1Y 0.96",
                    "id": "P",
                    "kind": "discount",
                    "preset": "USD_SOFR_OIS",
                    "market_data_source": source,
                },
            )
        )
    for r in (built, vc, pasted):
        assert r["ok"], r
        assert r["market_data_source"] == source
        assert r["notes"][0].startswith(f"market_data_source={source} (declared by the caller")


async def test_pricing_tools_echo_the_declared_source(pricing_app: Any) -> None:
    async with Client(pricing_app) as c:
        for case in product_cases():
            r = _s(await c.call_tool(case.tool, case.explicit))
            assert r["ok"], (case.tool, r)
            assert r["market_data_source"] == case.explicit["market_data_source"], case.tool
            assert r["notes"][0].startswith(
                f"market_data_source={case.explicit['market_data_source']} (declared"
            ), case.tool
            # a local rejection still carries the declaration
            bad = _s(await c.call_tool(case.tool, {**case.explicit, "preset": "NOPE"}))
            assert bad["ok"] is False and bad["market_data_source"] == r["market_data_source"]


async def test_session_put_stores_the_source_and_refs_carry_it_forward(app: Any) -> None:
    async with Client(app) as c:
        built = _s(
            await c.call_tool("build_curve", {**BUILD_ARGS, "market_data_source": "user_file"})
        )
        put = _s(
            await c.call_tool(
                "session_put",
                {
                    "name": "sofr",
                    "kind": "curve",
                    "value": built,
                    "market_data_source": "user_file",
                },
            )
        )
        assert put["ok"] and put["market_data_source"] == "user_file"
        assert put["item"]["market_data_source"] == "user_file"
        got = _s(await c.call_tool("session_get", {"name": "sofr"}))
        assert got["market_data_source"] == "user_file"
        listed = _s(await c.call_tool("session_list", {}))
        assert listed["items"][0]["market_data_source"] == "user_file"
        # the declaration carried by a build result must agree with the argument
        clash = _s(
            await c.call_tool(
                "session_put",
                {
                    "name": "s2",
                    "kind": "curve",
                    "value": built,
                    "market_data_source": "user_pasted",
                },
            )
        )
        assert clash["ok"] is False and "differs from the one the value carries" in clash["error"]
        # a later {"session": name} reference reports the stored source in notes
        q = _s(
            await c.call_tool(
                "build_query",
                {
                    "curve_id": "USD_SOFR_OIS",
                    "measures": ["DF"],
                    "tenors": ["1Y"],
                    "calendar": "UnitedStatesGovernmentBond",
                    "business_day_convention": "ModifiedFollowing",
                },
            )
        )
        boot = _s(
            await c.call_tool(
                "bootstrap_curve",
                {"curves": [{"session": "sofr"}], "as_of": SOFR["reference_date"], "queries": [q]},
            )
        )
    assert any("stored market_data_source=user_file" in n for n in boot["notes"]), boot["notes"]


def test_store_and_impl_level_guards() -> None:
    store = SessionStore()
    item, _ = store.put("a", "index", {"id": "IX", "name": "ix"}, "user_pasted")
    assert item.market_data_source == "user_pasted"
    assert item.summary()["market_data_source"] == "user_pasted"
    bad = session_put_impl(store, "b", "index", {"id": "IX", "name": "ix"}, "estimated")
    assert bad["ok"] is False and "no value for estimated" in bad["error"]
    assert stamp({"ok": True, "notes": ["x"]}, "session") == {
        "ok": True,
        "notes": [
            "market_data_source=session (declared by the caller: a market previously "
            "stored in this session (which itself came from one of the above))",
            "x",
        ],
        "market_data_source": "session",
    }


async def test_analytics_tools_echo_the_declared_source(app: Any) -> None:
    """The analytics tools reprice a market: they require and echo the declaration too."""
    from tests.unit.test_analytics_tools import QuoteSensitiveBackend, _market, _trade

    be = QuoteSensitiveBackend()
    app2 = build_server(Settings(engine_url="http://fake"), backend=be)
    async with Client(app2) as c:
        for name, extra in [
            ("swap_dv01", {}),
            ("key_rate_ladder", {}),
            ("scenario", {"scenarios": [{"name": "up", "bumps": [{"curve": "EUR_OIS", "bp": 1}]}]}),
            ("fair_rate", {}),
        ]:
            missing = await c.call_tool(name, {"market": _market(), "trade": _trade(), **extra})
            assert missing.is_error and "market_data_source" in missing.content[0].text  # type: ignore[union-attr]
            r = _s(
                await c.call_tool(
                    name,
                    {
                        "market": _market(),
                        "market_data_source": "user_file",
                        "trade": _trade(),
                        **extra,
                    },
                )
            )
            assert r["ok"], (name, r)
            assert r["market_data_source"] == "user_file", name
            assert r["notes"][0].startswith("market_data_source=user_file (declared"), name
            bad = _s(
                await c.call_tool(
                    name,
                    {
                        "market": _market(),
                        "market_data_source": "user_file",
                        "trade": _trade(preset="NOPE"),
                        **extra,
                    },
                )
            )
            assert bad["ok"] is False and bad["market_data_source"] == "user_file", name
