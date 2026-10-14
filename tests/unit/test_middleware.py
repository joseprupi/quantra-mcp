"""M5.5: failures are MCP tool errors; the session scope follows the transport's id;
the instructions lead with the rule and point developers at a resource."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from mcp import Client
from mcp.types import CallToolResult, TextContent

from quantra_mcp import prompts, server
from quantra_mcp.middleware import FailuresAreErrors, SessionScope, mcp_session_id
from quantra_mcp.resources import DEVELOPER_GUIDE
from quantra_mcp.rules import ABSOLUTE_RULE, EXAMPLE_SHAPE_RULE, SOURCE_RULE
from quantra_mcp.session import current_session_id
from tests.conftest import FakeBackend, structured


def _ctx(
    method: str = "tools/call", session_id: str | None = None, header: str | None = None
) -> Any:
    connection = SimpleNamespace(session_id=session_id)
    request = None if header is None else SimpleNamespace(headers={"mcp-session-id": header})
    return SimpleNamespace(
        method=method, session=SimpleNamespace(_connection=connection), request=request
    )


def test_mcp_session_id_prefers_the_connection_then_the_header() -> None:
    assert mcp_session_id(_ctx(session_id="abc")) == "abc"
    assert mcp_session_id(_ctx(session_id="abc", header="zzz")) == "abc"
    assert mcp_session_id(_ctx(header="hdr")) == "hdr"
    assert mcp_session_id(_ctx()) is None
    assert mcp_session_id(_ctx(session_id="")) is None


async def test_session_scope_binds_for_the_handler_only() -> None:
    seen: list[str | None] = []

    async def handler(ctx: Any) -> Any:
        seen.append(current_session_id())
        return None

    await SessionScope()(_ctx(session_id="s1"), handler)
    await SessionScope()(_ctx(), handler)
    assert seen == ["s1", None] and current_session_id() is None


async def test_failures_are_errors_flags_ok_false_only_on_tool_calls() -> None:
    def result(ok: bool) -> CallToolResult:
        body = {"ok": ok, "error": "x"}
        return CallToolResult(
            content=[TextContent(type="text", text="{}")], structured_content=body
        )

    async def failing(ctx: Any) -> Any:
        return result(False)

    async def succeeding(ctx: Any) -> Any:
        return result(True)

    async def as_dict(ctx: Any) -> Any:
        return {"content": [], "structuredContent": {"ok": False}}

    mw = FailuresAreErrors()
    flagged = await mw(_ctx(), failing)
    assert isinstance(flagged, CallToolResult) and flagged.is_error
    assert flagged.structured_content == {"ok": False, "error": "x"}  # body untouched
    fine = await mw(_ctx(), succeeding)
    assert isinstance(fine, CallToolResult) and not fine.is_error
    untouched = await mw(_ctx(method="resources/read"), failing)
    assert isinstance(untouched, CallToolResult) and not untouched.is_error
    assert (await mw(_ctx(), as_dict)) == {
        "content": [],
        "structuredContent": {"ok": False},
        "isError": True,
    }


async def test_every_failure_kind_is_a_tool_error_through_the_server(
    app: Any, fake_backend: FakeBackend
) -> None:
    async with Client(app) as c:
        # local rejection (unknown preset)
        local = structured(await c.call_tool("preset", {"id": "NOPE"}))
        assert local["ok"] is False
        # session miss (per-session scratch; the in-memory client is the implicit session)
        miss = await c.call_tool("session", {"action": "get", "name": "never"})
        assert miss.is_error and structured(miss)["ok"] is False
        # engine error passes through verbatim, flagged
        fake_backend.responses.pop("/calendar-holidays")
        eng = await c.call_tool(
            "calendar_holidays",
            {"calendar": "TARGET", "start_date": "2024-01-01", "end_date": "2024-02-01"},
        )
        assert eng.is_error and structured(eng)["status"] == 404
        # success is not flagged
        assert not (await c.call_tool("quantra_meta", {})).is_error


async def test_instructions_lead_with_the_rule_and_the_developer_map_is_a_resource(
    app: Any,
) -> None:
    text = server.INSTRUCTIONS
    assert len(text) < 2000, len(text)
    assert text.startswith(ABSOLUTE_RULE)
    assert text.index(EXAMPLE_SHAPE_RULE) < text.index(SOURCE_RULE) < text.index(server.GUIDANCE)
    assert len(text) < server.INSTRUCTIONS_BUDGET
    # the source rule names QuantLib, the tools and the citation form, and bans the
    # phrases that stood in for reading the source (M5.9a)
    flat = " ".join(SOURCE_RULE.split())
    for phrase in (
        "explain_method first",
        "QuantLib class (PiecewiseYieldCurve, IterativeBootstrap, a pricer, a helper)",
        "search_source / read_source",
        "quantlib:ql/<file>@v1.41:Lx-Ly",
        '"from memory", "general knowledge" or "not verified here"',
        '"not found in the engine or QuantLib source at this pin"',
    ):
        assert phrase in flat, phrase
    assert "quantra://guide/developers" in text and "For developers (the technical map)" not in text
    flat = " ".join(text.split())
    assert "engine_request POSTs any endpoint" not in flat  # the map moved out
    assert prompts.VOICE.endswith(ABSOLUTE_RULE + EXAMPLE_SHAPE_RULE)
    assert "engine_request POSTs any endpoint" in " ".join(DEVELOPER_GUIDE.split())
    async with Client(app) as c:
        uris = {str(r.uri) for r in (await c.list_resources()).resources}
        assert "quantra://guide/developers" in uris
        got = (await c.read_resource("quantra://guide/developers")).contents[0]
        assert getattr(got, "text", "") == DEVELOPER_GUIDE
        init = c.session  # the instructions the client received at initialize
        assert init is not None
