"""Hosted-mode hardening: rate limit, body cap, concurrency gate, proxy trust, host
validation, health routes and the access log. Everything runs against the real
Starlette app the ``--http`` transport serves, through Starlette's TestClient."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
import pytest
from mcp.server.transport_security import TransportSecurityMiddleware
from starlette.requests import Request
from starlette.testclient import TestClient

from quantra_mcp.config import Settings
from quantra_mcp.hosted import (
    ConcurrencyGate,
    HostedGuard,
    TokenBucketLimiter,
    _describe,
    build_http_app,
    display_address,
    transport_security,
)
from quantra_mcp.server import EngineUnavailable, build_server, check_engine
from tests.conftest import FakeBackend, TransportError

MCP_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"},
    },
}


def make_app(backend: FakeBackend, engine_up: bool = True, **overrides: Any) -> Any:
    settings = Settings(engine_url="http://fake", **overrides)
    server = build_server(settings, backend=backend)

    def handler(request: httpx.Request) -> httpx.Response:
        if not engine_up:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json={"status": "healthy"})

    return build_http_app(
        server,
        settings,
        backend,
        bind_host="0.0.0.0",
        readyz_transport=httpx.MockTransport(handler),
    )


def _sse_result(text: str) -> dict[str, Any]:
    for line in text.splitlines():
        if line.startswith("data:"):
            return dict(json.loads(line[5:].strip()))
    return dict(json.loads(text))


# --- primitives -------------------------------------------------------------


def test_token_bucket_burst_then_refill() -> None:
    now = [0.0]
    lim = TokenBucketLimiter(rpm=60, burst=3, clock=lambda: now[0])
    assert [lim.acquire("a") for _ in range(3)] == [0.0, 0.0, 0.0]
    wait = lim.acquire("a")
    assert 0 < wait <= 1.0
    assert lim.acquire("b") == 0.0  # other key untouched
    now[0] += 2.0  # 2 tokens back at 1/s
    assert lim.acquire("a") == 0.0 and lim.acquire("a") == 0.0 and lim.acquire("a") > 0


def test_token_bucket_disabled_and_bounded_keys() -> None:
    assert TokenBucketLimiter(rpm=0, burst=1).acquire("x") == 0.0
    lim = TokenBucketLimiter(rpm=60, burst=1, max_keys=2)
    for k in "abc":
        lim.acquire(k)
    assert list(lim._buckets) == ["b", "c"]


async def test_concurrency_gate_refuses_past_limit_plus_queue() -> None:
    gate = ConcurrencyGate(limit=1, max_queue=1)
    assert gate.try_enter()
    await gate.wait()  # running
    assert gate.try_enter()  # queued
    assert not gate.try_enter()  # refused
    gate.leave()
    await gate.wait()
    gate.leave()
    assert gate.active == 0 and gate.waiting == 0


def test_display_address_hashes_unless_raw() -> None:
    assert display_address("203.0.113.9", raw=True) == "203.0.113.9"
    hashed = display_address("203.0.113.9", raw=False)
    assert hashed.startswith("ip:") and len(hashed) == 15 and "203" not in hashed


def test_describe_status_from_structured_content() -> None:
    assert _describe({"structuredContent": {"ok": True, "endpoint": "/meta"}}) == ("/meta", "ok")
    assert _describe({"structuredContent": {"ok": False, "endpoint": "/x", "status": 400}}) == (
        "/x",
        "engine_400",
    )
    assert _describe({"structuredContent": {"ok": False, "endpoint": "/x", "status": None}}) == (
        "/x",
        "local_error",
    )
    assert _describe({"isError": True}) == ("-", "tool_error")
    assert _describe(None) == ("-", "ok")


# --- the guard on a raw ASGI app ---------------------------------------------


def _scope(method: str = "POST", path: str = "/mcp", headers: dict[str, str] | None = None) -> Any:
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": hdrs,
        "client": ("10.0.0.1", 1234),
    }


async def _run(app: Any, scope: Any, body_chunks: list[bytes]) -> tuple[int, dict[str, str], bytes]:
    chunks = list(body_chunks)

    async def receive() -> Any:
        if chunks:
            chunk = chunks.pop(0)
            return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}
        return {"type": "http.disconnect"}

    status = 0
    headers: dict[str, str] = {}
    out = bytearray()

    async def send(message: Any) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
            headers.update({k.decode(): v.decode() for k, v in message["headers"]})
        elif message["type"] == "http.response.body":
            out.extend(message.get("body", b""))

    await app(scope, receive, send)
    return status, headers, bytes(out)


async def test_guard_503_when_tool_calls_saturate_limit_and_queue() -> None:
    release = asyncio.Event()
    entered = 0

    async def slow(scope: Any, receive: Any, send: Any) -> None:
        nonlocal entered
        await receive()
        entered += 1
        await release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    guard = HostedGuard(
        slow,
        limiter=TokenBucketLimiter(0, 1),
        gate=ConcurrencyGate(limit=2, max_queue=1),
        max_body_bytes=1024,
        trust_proxy=False,
    )
    call = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}}).encode()
    tasks = [asyncio.create_task(_run(guard, _scope(), [call])) for _ in range(4)]
    await asyncio.sleep(0.05)
    assert entered == 2  # limit running, one queued, one refused
    release.set()
    results = await asyncio.gather(*tasks)
    statuses = sorted(s for s, _, _ in results)
    assert statuses == [200, 200, 200, 503]
    refused = next(r for r in results if r[0] == 503)
    assert refused[1]["retry-after"] == "1"
    assert json.loads(refused[2])["limit"] == 2


async def test_guard_does_not_gate_non_tool_calls() -> None:
    async def echo(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    gate = ConcurrencyGate(limit=1, max_queue=0)
    assert gate.try_enter()
    await gate.wait()  # saturated
    guard = HostedGuard(
        echo, limiter=TokenBucketLimiter(0, 1), gate=gate, max_body_bytes=1024, trust_proxy=False
    )
    status, _, _ = await _run(guard, _scope(), [json.dumps(INIT).encode()])
    assert status == 200
    status, _, _ = await _run(guard, _scope(method="GET", path="/healthz"), [])
    assert status == 200


async def test_guard_413_on_streamed_body_without_content_length() -> None:
    async def sink(scope: Any, receive: Any, send: Any) -> None:
        while (await receive())["type"] == "http.request":
            pass
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    guard = HostedGuard(
        sink,
        limiter=TokenBucketLimiter(0, 1),
        gate=ConcurrencyGate(4, 4),
        max_body_bytes=10,
        trust_proxy=False,
    )
    status, _, body = await _run(guard, _scope(), [b"x" * 8, b"y" * 8])
    assert status == 413 and json.loads(body)["max_body_bytes"] == 10
    status, _, _ = await _run(guard, _scope(path="/other"), [b"x" * 8, b"y" * 8])
    assert status == 413


# --- the real app through TestClient -----------------------------------------


def test_healthz_root_and_readyz(fake_backend: FakeBackend) -> None:
    with TestClient(make_app(fake_backend, public_url="https://mcp.quantra.io")) as c:
        r = c.get("/healthz")
        assert r.status_code == 200 and r.json()["status"] == "ok"
        assert r.headers["access-control-allow-origin"] == "*"
        assert not [call for call in fake_backend.calls if call[1] == "/health"]

        r = c.get("/")
        assert r.status_code == 200
        assert r.json()["mcp"] == "https://mcp.quantra.io/mcp"
        assert "custom MCP connector" in r.json()["how"]
        assert r.headers["access-control-allow-origin"] == "*"

        r = c.get("/readyz")
        assert r.status_code == 200 and r.json() == {
            "status": "ok",
            "engine": {"status": "healthy"},
        }


def test_readyz_503_when_engine_down(fake_backend: FakeBackend) -> None:
    with TestClient(make_app(fake_backend, engine_up=False)) as c:
        r = c.get("/readyz")
        assert r.status_code == 503 and r.json()["status"] == "degraded"


def test_rate_limit_429_with_retry_after(fake_backend: FakeBackend) -> None:
    app = make_app(fake_backend, rate_limit_rpm=60, rate_limit_burst=3)
    with TestClient(app) as c:
        codes = [c.get("/readyz").status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]
        r = c.get("/readyz")
        assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
        assert r.json()["error"] == "rate limit exceeded"
        assert c.get("/healthz").status_code == 200  # exempt


def test_x_forwarded_for_only_trusted_behind_proxy(fake_backend: FakeBackend) -> None:
    hop = lambda ip: {"x-forwarded-for": f"{ip}, 10.0.0.2"}  # noqa: E731
    with TestClient(make_app(fake_backend, rate_limit_burst=2)) as c:
        codes = [c.get("/readyz", headers=hop(f"198.51.100.{i}")).status_code for i in range(3)]
        assert codes == [200, 200, 429]  # header ignored: one bucket (the socket peer)
    with TestClient(make_app(fake_backend, rate_limit_burst=2, trust_proxy=True)) as c:
        codes = [c.get("/readyz", headers=hop(f"198.51.100.{i}")).status_code for i in range(3)]
        assert codes == [200, 200, 200]  # header honoured: three buckets


def test_body_cap_413_on_declared_length(fake_backend: FakeBackend) -> None:
    with TestClient(make_app(fake_backend, max_body_bytes=2048)) as c:
        big = json.dumps({**INIT, "params": {**INIT["params"], "pad": "x" * 4096}})
        r = c.post("/mcp", content=big, headers=MCP_HEADERS)
        assert r.status_code == 413 and r.json()["max_body_bytes"] == 2048


def test_mcp_over_http_calls_quantra_meta_and_logs_access(
    fake_backend: FakeBackend, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="quantra_mcp.access")
    with TestClient(make_app(fake_backend)) as c:
        r = c.post("/mcp", json=INIT, headers=MCP_HEADERS)
        assert r.status_code == 200, r.text
        sid = r.headers["mcp-session-id"]
        result = _sse_result(r.text)["result"]
        assert result["serverInfo"]["name"] == "quantra"
        h = {**MCP_HEADERS, "mcp-session-id": sid}
        r = c.post(
            "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=h
        )
        assert r.status_code in (200, 202)
        call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "quantra_meta", "arguments": {"secret_marker": "DO-NOT-LOG"}},
        }
        r = c.post("/mcp", json=call, headers=h)
        assert r.status_code == 200, r.text
        payload = _sse_result(r.text)["result"]
        structured = payload["structuredContent"]
        assert structured["ok"] is True and structured["response"]["openapi_version"] == "0.7.0"
    lines = [rec.getMessage() for rec in caplog.records if rec.name == "quantra_mcp.access"]
    assert len(lines) == 1, lines
    line = lines[0]
    assert "tool=quantra_meta" in line and "endpoint=/meta" in line and "status=ok" in line
    assert "client=ip:" in line and "testclient" not in line  # hashed
    assert "DO-NOT-LOG" not in line and "ms=" in line


def test_access_log_raw_ip_when_enabled(
    fake_backend: FakeBackend, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="quantra_mcp.access")
    with TestClient(make_app(fake_backend, log_raw_ip=True)) as c:
        r = c.post("/mcp", json=INIT, headers=MCP_HEADERS)
        h = {**MCP_HEADERS, "mcp-session-id": r.headers["mcp-session-id"]}
        c.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=h)
        call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "quantra_meta"},
        }
        assert c.post("/mcp", json=call, headers=h).status_code == 200
    lines = [rec.getMessage() for rec in caplog.records if rec.name == "quantra_mcp.access"]
    assert lines and "client=testclient" in lines[0]


# --- host validation (SDK transport security) ---------------------------------


def test_transport_security_wiring(caplog: pytest.LogCaptureFixture) -> None:
    assert transport_security(Settings(), "127.0.0.1") is None  # SDK localhost defaults
    with caplog.at_level(logging.WARNING, logger="quantra_mcp.hosted"):
        assert transport_security(Settings(), "0.0.0.0") is None
    assert "DNS-rebinding protection) is OFF" in caplog.text

    ts = transport_security(Settings(allowed_hosts=("mcp.quantra.io", "localhost")), "0.0.0.0")
    assert ts is not None and ts.enable_dns_rebinding_protection
    assert ts.allowed_hosts == ["mcp.quantra.io", "mcp.quantra.io:*", "localhost", "localhost:*"]
    assert ts.allowed_origins == ["http:*", "https:*"]  # any origin unless restricted
    ts2 = transport_security(
        Settings(allowed_hosts=("mcp.quantra.io",), allowed_origins=("https://claude.ai",)),
        "0.0.0.0",
    )
    assert ts2 is not None and ts2.allowed_origins == ["https://claude.ai"]


def _validate(ts: Any, headers: dict[str, str]) -> int:
    mw = TransportSecurityMiddleware(ts)
    scope = _scope(headers={"content-type": "application/json", **headers})
    resp = asyncio.run(mw.validate_request(Request(scope), is_post=True))
    return 200 if resp is None else resp.status_code


def test_sdk_host_and_origin_checks_with_our_settings() -> None:
    ts = transport_security(Settings(allowed_hosts=("mcp.quantra.io",)), "0.0.0.0")
    assert _validate(ts, {"host": "mcp.quantra.io"}) == 200
    assert _validate(ts, {"host": "mcp.quantra.io:443"}) == 200
    assert _validate(ts, {"host": "evil.example"}) == 421
    assert _validate(ts, {"host": "mcp.quantra.io", "origin": "https://claude.ai"}) == 200
    assert _validate(ts, {"host": "mcp.quantra.io", "origin": "http://localhost:6274"}) == 200
    strict = transport_security(
        Settings(allowed_hosts=("mcp.quantra.io",), allowed_origins=("https://claude.ai",)),
        "0.0.0.0",
    )
    assert _validate(strict, {"host": "mcp.quantra.io", "origin": "https://claude.ai"}) == 200
    assert _validate(strict, {"host": "mcp.quantra.io", "origin": "https://evil.example"}) == 403


def test_mcp_endpoint_rejects_wrong_host_header(fake_backend: FakeBackend) -> None:
    with TestClient(make_app(fake_backend, allowed_hosts=("mcp.quantra.io",))) as c:
        r = c.post("/mcp", json=INIT, headers={**MCP_HEADERS, "host": "evil.example"})
        assert r.status_code == 421
        r = c.post("/mcp", json=INIT, headers={**MCP_HEADERS, "host": "mcp.quantra.io"})
        assert r.status_code == 200
        assert c.get("/healthz", headers={"host": "evil.example"}).status_code == 200


# --- startup engine requirement -------------------------------------------------


async def test_check_engine_require_raises_when_unreachable(fake_backend: FakeBackend) -> None:
    fake_backend.fail_with = TransportError("ConnectError: refused")
    await check_engine(fake_backend)  # warn only
    with pytest.raises(EngineUnavailable, match="QUANTRA_REQUIRE_ENGINE=1"):
        await check_engine(fake_backend, require=True)
