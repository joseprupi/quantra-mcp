"""M5.5 through the SDK's own HTTP clients of both protocol eras, against the hosted ASGI
app (httpx.ASGITransport, lifespan driven by hand so the SDK session manager runs).

Handshake era (initialize + Mcp-Session-Id): two sessions get separate scratch and
separate rate buckets. Modern era (2026-07-28, single-exchange, no session id): scratch
is refused with an actionable tool error rather than shared, everything else works."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp import Client, ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.types import ASGIApp

from quantra_mcp.config import Settings
from quantra_mcp.hosted import build_http_app
from quantra_mcp.server import build_server
from quantra_mcp.session import SESSIONLESS_ERROR
from tests.conftest import FakeBackend, structured

URL = "http://testserver/mcp"


@asynccontextmanager
async def lifespan(asgi: ASGIApp) -> AsyncIterator[None]:
    inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    started, stopped = asyncio.Event(), asyncio.Event()

    async def receive() -> dict[str, Any]:
        return await inbox.get()

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "lifespan.startup.complete":
            started.set()
        elif message["type"] == "lifespan.shutdown.complete":
            stopped.set()
        elif message["type"].endswith("failed"):  # pragma: no cover
            raise RuntimeError(message)

    task = asyncio.create_task(
        asgi({"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send)
    )
    await inbox.put({"type": "lifespan.startup"})
    await started.wait()
    try:
        yield
    finally:
        await inbox.put({"type": "lifespan.shutdown"})
        await stopped.wait()
        await task


@asynccontextmanager
async def hosted(
    backend: FakeBackend, **overrides: Any
) -> AsyncIterator[Callable[[], httpx.AsyncClient]]:
    settings = Settings(engine_url="http://fake", **overrides)
    asgi = build_http_app(
        build_server(settings, backend=backend), settings, backend, bind_host="0.0.0.0"
    )

    def http() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi), base_url="http://t")

    async with lifespan(asgi):
        yield http


@asynccontextmanager
async def handshake_session(http: httpx.AsyncClient) -> AsyncIterator[ClientSession]:
    """A 2025-era client: ``initialize`` handshake, ``Mcp-Session-Id`` on every request."""
    async with (
        streamable_http_client(URL, http_client=http) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


async def test_handshake_era_sessions_are_isolated_and_rate_limited_separately(
    fake_backend: FakeBackend, ois_example: dict[str, Any]
) -> None:
    curve = ois_example["pricing"]["rates"]["curves"][0]
    seen: dict[int, str] = {}

    def capture(label: int, http: httpx.AsyncClient) -> httpx.AsyncClient:
        async def hook(resp: httpx.Response) -> None:
            if resp.headers.get("mcp-session-id"):
                seen[label] = resp.headers["mcp-session-id"]

        http.event_hooks = {"response": [hook]}
        return http

    async with (
        hosted(fake_backend, rate_limit_burst=4) as http,
        handshake_session(capture(1, http())) as a,
        handshake_session(capture(2, http())) as b,
    ):
        assert seen[1] != seen[2]  # two Mcp-Session-Ids
        put = structured(
            await a.call_tool(
                "session_put",
                {
                    "market_data_source": "user_pasted",
                    "name": "sofr",
                    "kind": "curve",
                    "value": curve,
                },
            )
        )
        assert put["ok"] is True
        assert structured(await b.call_tool("session_list", {}))["items"] == []
        miss = await b.call_tool("session_get", {"name": "sofr"})
        assert miss.is_error and "(none)" in structured(miss)["error"]
        # A: put + 3 lists = 4 tokens; the 5th call is a tool error naming the session bucket
        for _ in range(3):
            assert structured(await a.call_tool("session_list", {}))["ok"] is True
        refused = await a.call_tool("session_list", {})
        body = structured(refused)
        assert refused.is_error and body["status"] == 429 and body["retry_after_s"] >= 1
        assert "session" in body["error"] and "retry" in body["retry_hint"]
        # B, from the same address, is untouched (2 spent of 4)
        assert structured(await b.call_tool("session_list", {}))["ok"] is True


async def test_modern_era_refuses_scratch_instead_of_sharing_it(
    fake_backend: FakeBackend, ois_example: dict[str, Any]
) -> None:
    curve = ois_example["pricing"]["rates"]["curves"][0]
    async with (
        hosted(fake_backend) as http,
        Client(streamable_http_client(URL, http_client=http())) as a,
    ):
        assert a.protocol_version == "2026-07-28"
        put = await a.call_tool(
            "session_put",
            {
                "market_data_source": "user_pasted",
                "name": "sofr",
                "kind": "curve",
                "value": curve,
            },
        )
        assert put.is_error and structured(put)["error"] == SESSIONLESS_ERROR
        assert "inline" in structured(put)["error"]
        for name, args in (
            ("session_list", {}),
            ("session_get", {"name": "x"}),
            ("session_delete", {"name": "x"}),
        ):
            r = await a.call_tool(name, args)
            assert r.is_error and structured(r)["error"] == SESSIONLESS_ERROR, name
        # a session reference inside a tool is the same local error, flagged
        query = structured(
            await a.call_tool(
                "build_query",
                {
                    "curve_id": curve["id"],
                    "measures": ["DF"],
                    "tenors": ["1Y"],
                    "calendar": "TARGET",
                    "business_day_convention": "Following",
                },
            )
        )["query"]
        ref = await a.call_tool(
            "bootstrap_curve",
            {"curves": [{"session": "sofr"}], "as_of": "2025-01-15", "queries": [query]},
        )
        assert ref.is_error and SESSIONLESS_ERROR[:40] in structured(ref)["error"]
        # the inline form works, as does everything without scratch
        assert structured(await a.call_tool("quantra_meta", {}))["ok"] is True
        assert not (await a.call_tool("list_presets", {})).is_error
