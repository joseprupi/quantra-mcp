"""0.1.13: ``market_data_source=sourced`` and the ``sources`` citation list.

Operator decision 2026-10-22: market data may be obtained in the conversation from a
trusted public publisher (other sites only after asking the user) or from a data
connector the user connected; every value is cited (what, value, as_of, url or
connector, derived?). ``sourced`` without valid citations is refused before anything
is sent; valid citations are echoed in every result's ``provenance``."""

from __future__ import annotations

from typing import Any

import pytest
from mcp import Client

from quantra_mcp import examples_catalog as cat
from quantra_mcp.config import Settings
from quantra_mcp.errors import LocalValidationError
from quantra_mcp.server import build_server
from quantra_mcp.session import SessionStore
from quantra_mcp.tools._market_source import (
    MARKET_DATA_SOURCES,
    check_sources,
    source_refusal,
    stamp,
)
from quantra_mcp.tools.session import session_put_impl
from tests.conftest import FakeBackend, structured
from tests.product_cases import product_cases
from tests.unit.test_market_data_source import BUILD_ARGS, CARRYING_TOOLS, GUARDED_TOOLS
from tests.unit.test_structured_note_tool import CANNED as NOTE_CANNED
from tests.unit.test_structured_note_tool import FIXTURE as NOTE_FIXTURE

URL = "https://www.newyorkfed.org/markets/reference-rates/sofr"
GOOD: list[dict[str, Any]] = [
    {"what": "SOFR overnight", "value": 0.0431, "as_of": "2025-01-15", "url": URL},
    {
        "what": "SPX/SX5E correlation",
        "value": 0.62,
        "as_of": "2025-01-15",
        "connector": "fred:get_series",
        "derived": "Pearson correlation of 250 daily log returns",
    },
]


def test_enum_has_sourced_and_no_invented_value() -> None:
    assert "sourced" in MARKET_DATA_SOURCES
    assert not {"estimated", "recalled", "placeholder", "public"} & set(MARKET_DATA_SOURCES)


@pytest.mark.parametrize("sources", [None, []])
def test_sourced_requires_sources(sources: Any) -> None:
    with pytest.raises(LocalValidationError, match="sourced requires `sources`") as exc:
        check_sources("sourced", sources)
    assert exc.value.problems == [{"path": "/sources", "message": "required for sourced"}]


@pytest.mark.parametrize(
    ("item", "path"),
    [
        ({"what": "x", "value": 1, "as_of": "2025-01-15", "url": "ftp://a.org/x"}, "url"),
        ({"what": "x", "value": 1, "as_of": "2025-01-15", "url": "not a url"}, "url"),
        ({"what": "x", "value": 1, "as_of": "2025-01-15"}, "url"),  # neither url nor connector
        ({"what": "x", "value": 1, "as_of": "2025-01-15", "connector": " "}, "url"),
        ({"what": "x", "value": 1, "url": URL}, "as_of"),
        ({"what": "x", "value": 1, "url": URL, "as_of": "2025-13-01"}, "as_of"),
        ({"what": "x", "value": 1, "url": URL, "as_of": "15/01/2025"}, "as_of"),
        ({"value": 1, "url": URL, "as_of": "2025-01-15"}, "what"),
    ],
)
def test_bad_citation_is_refused(item: dict[str, Any], path: str) -> None:
    with pytest.raises(LocalValidationError, match="never fill them in from memory") as exc:
        check_sources("sourced", [item])
    assert [p["path"] for p in exc.value.problems] == [f"/sources/0/{path}"]
    refusal = source_refusal("sourced", [item])
    assert refusal is not None and refusal["ok"] is False and refusal["status"] is None


def test_valid_citations_pass_and_are_stamped() -> None:
    assert check_sources("sourced", GOOD) == GOOD
    assert source_refusal("sourced", GOOD) is None
    assert source_refusal("user_pasted", None) is None
    out = stamp({"ok": True}, "sourced", GOOD)
    assert out["provenance"]["sources"] == GOOD
    assert out["provenance"]["market_data_source"] == "sourced"
    assert "trusted public publisher" in out["provenance"]["meaning"]
    assert out["notes"][0].endswith("; 2 cited sources in provenance")
    # citations are validated whatever the declared source
    assert source_refusal("user_pasted", [{"what": "x", "url": URL}]) is not None


async def test_every_market_tool_takes_optional_sources() -> None:
    app = build_server(Settings(engine_url="http://fake"), backend=FakeBackend())
    async with Client(app) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    for name in sorted(GUARDED_TOOLS | CARRYING_TOOLS):
        schema = tools[name].input_schema
        assert "sources" in schema["properties"], name
        assert "sources" not in schema.get("required", []), name
        assert "sourced" in str(schema["properties"]["market_data_source"]), name


def _note_args(**extra: Any) -> dict[str, Any]:
    body = cat.load_body(cat.find(NOTE_FIXTURE))
    return {"market": body["pricing"], "notes": body["notes"], **extra}


async def test_structured_note_refuses_sourced_without_valid_sources() -> None:
    fake = FakeBackend({"/price-structured-note": NOTE_CANNED})
    app = build_server(Settings(engine_url="http://fake"), backend=fake)
    async with Client(app) as c:
        missing = await c.call_tool(
            "price_structured_note", _note_args(market_data_source="sourced")
        )
        body = structured(missing)
        assert missing.is_error and body["problems"][0]["path"] == "/sources"
        bad = structured(
            await c.call_tool(
                "price_structured_note",
                _note_args(
                    market_data_source="sourced",
                    sources=[{"what": "x", "value": 1, "as_of": "2025-01-15", "url": "x"}],
                ),
            )
        )
        assert bad["ok"] is False and bad["problems"][0]["path"] == "/sources/0/url"
        assert not [x for x in fake.calls if x[0] == "POST"]  # nothing was sent
        ok = structured(
            await c.call_tool(
                "price_structured_note", _note_args(market_data_source="sourced", sources=GOOD)
            )
        )
    assert ok["ok"] is True
    assert ok["provenance"] == {
        "market_data_source": "sourced",
        "meaning": ok["provenance"]["meaning"],
        "sources": GOOD,
    }
    assert ok["market_data_source"] == "sourced"


async def test_every_pricing_tool_refuses_and_echoes() -> None:
    from tests.unit.test_pricing_tools import CANNED

    app = build_server(Settings(engine_url="http://fake"), backend=FakeBackend(CANNED))
    async with Client(app) as c:
        for case in product_cases():
            args = {**case.explicit, "market_data_source": "sourced"}
            refused = structured(await c.call_tool(case.tool, args))
            assert refused["ok"] is False and "requires `sources`" in refused["error"], case.tool
            r = structured(await c.call_tool(case.tool, {**args, "sources": GOOD}))
            assert r["ok"], (case.tool, r)
            assert r["provenance"]["sources"] == GOOD, case.tool


async def test_curve_session_and_reprice_carry_the_citations() -> None:
    from tests.unit.test_pricing_tools import CANNED

    app = build_server(Settings(engine_url="http://fake"), backend=FakeBackend(CANNED))
    async with Client(app) as c:
        built = structured(
            await c.call_tool(
                "build_curve", {**BUILD_ARGS, "market_data_source": "sourced", "sources": GOOD}
            )
        )
        assert built["provenance"]["sources"] == GOOD
        put = structured(
            await c.call_tool(
                "session_put",
                {"name": "s", "kind": "curve", "value": built, "market_data_source": "sourced"},
            )
        )
        assert put["provenance"]["sources"] == GOOD and put["item"]["sources"] == GOOD
        case = next(k for k in product_cases() if k.tool == "price_vanilla_swap")
        priced = structured(
            await c.call_tool(
                case.tool, {**case.explicit, "market_data_source": "sourced", "sources": GOOD}
            )
        )
        change = [{"path": "swaps[0].vanilla_swap.fixed_leg.coupons.rate", "value": 0.035}]
        rep = structured(
            await c.call_tool("reprice_with", {"result_or_request": priced, "changes": change})
        )
    assert rep["ok"] and rep["market_data_source"] == "sourced"
    assert rep["provenance"]["sources"] == GOOD


def test_session_impl_refuses_sourced_without_citations() -> None:
    store = SessionStore()
    bad = session_put_impl(store, "b", "index", {"id": "IX", "name": "ix"}, "sourced")
    assert bad["ok"] is False and bad["problems"][0]["path"] == "/sources"
    assert len(store) == 0
    ok = session_put_impl(store, "b", "index", {"id": "IX", "name": "ix"}, "sourced", GOOD)
    assert ok["ok"] and store.get("b").sources == GOOD
