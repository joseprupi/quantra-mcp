"""Hosted-mode hardening for ``--http``: everything a public, no-auth deployment needs.

Layers, outermost first:

1. :class:`HostedGuard` (raw ASGI middleware around the whole Starlette app):
   per-client token bucket (429 + ``Retry-After``), request body cap (413),
   and a global concurrency gate on MCP ``tools/call`` requests with a
   bounded wait queue (503 + ``Retry-After``). The client address is the
   socket peer unless ``QUANTRA_TRUST_PROXY=1``, in which case the first
   ``X-Forwarded-For`` hop is used (set it only behind your own reverse proxy).
2. The SDK's transport security on the ``/mcp`` endpoint: with
   ``QUANTRA_ALLOWED_HOSTS`` set, a request whose ``Host`` header is not listed
   gets 421 (DNS-rebinding protection); ``Origin`` is checked against
   ``QUANTRA_ALLOWED_ORIGINS`` when that is set, otherwise any origin passes
   (the server holds no credentials). On a loopback bind with nothing set the
   SDK's localhost defaults apply; on a public bind with nothing set the check
   is off and a warning is logged.
3. Plain routes beside ``/mcp``: ``GET /`` (JSON pointer), ``GET /healthz``
   (process alive, no engine call), ``GET /readyz`` (engine ``/health`` with a
   2 s timeout).
4. :class:`AccessLog`, an MCP-level middleware: one stderr line per
   ``tools/call`` (timestamp, hashed client address, tool, endpoint, status,
   milliseconds). Never the arguments, never the bodies.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from typing import Any

import httpx
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from quantra_mcp import __version__
from quantra_mcp.backend.base import Backend
from quantra_mcp.config import Settings

log = logging.getLogger("quantra_mcp.hosted")
access_log = logging.getLogger("quantra_mcp.access")

DOCS_URL = "https://github.com/joseprupi/quantra-mcp"
READYZ_TIMEOUT_S = 2.0
_MAX_TRACKED_CLIENTS = 10_000
_LOOPBACK = ("127.0.0.1", "localhost", "::1")


# --- client address ---------------------------------------------------------


def client_address(
    scope_client: tuple[str, int] | None, headers: Headers, trust_proxy: bool
) -> str:
    """The address rate limits and logs are keyed on."""
    if trust_proxy:
        forwarded = headers.get("x-forwarded-for")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first
    if scope_client:
        return scope_client[0]
    return "unknown"


def display_address(addr: str, raw: bool) -> str:
    return addr if raw else "ip:" + hashlib.sha256(addr.encode()).hexdigest()[:12]


# --- rate limiting ----------------------------------------------------------


class TokenBucketLimiter:
    """Per-key token bucket: ``burst`` tokens, refilled at ``rpm / 60`` per second.

    ``rpm == 0`` disables limiting. Keys are evicted least-recently-seen past
    ``max_keys`` so memory stays bounded under address churn.
    """

    def __init__(
        self,
        rpm: int,
        burst: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = _MAX_TRACKED_CLIENTS,
    ) -> None:
        self.rpm = rpm
        self.burst = max(1, burst)
        self.rate = rpm / 60.0
        self._clock = clock
        self._max_keys = max_keys
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def acquire(self, key: str) -> float:
        """Take one token for ``key``. Returns 0.0 when allowed, else the seconds to wait."""
        if self.rpm <= 0:
            return 0.0
        now = self._clock()
        tokens, last = self._buckets.pop(key, (float(self.burst), now))
        tokens = min(float(self.burst), tokens + (now - last) * self.rate)
        if tokens >= 1.0:
            tokens -= 1.0
            wait = 0.0
        else:
            wait = (1.0 - tokens) / self.rate
        self._buckets[key] = (tokens, now)
        while len(self._buckets) > self._max_keys:
            self._buckets.popitem(last=False)
        return wait


# --- concurrency gate -------------------------------------------------------


class ConcurrencyGate:
    """``limit`` concurrent holders; up to ``max_queue`` more may wait; beyond that, refuse."""

    def __init__(self, limit: int, max_queue: int) -> None:
        self.limit = max(1, limit)
        self.max_queue = max(0, max_queue)
        self._sem = asyncio.Semaphore(self.limit)
        self.active = 0
        self.waiting = 0

    def try_enter(self) -> bool:
        """Reserve a place (running or queued). ``False`` means refuse now."""
        if self.active + self.waiting >= self.limit + self.max_queue:
            return False
        self.waiting += 1
        return True

    async def wait(self) -> None:
        try:
            await self._sem.acquire()
        finally:
            self.waiting -= 1
        self.active += 1

    def leave(self) -> None:
        self.active -= 1
        self._sem.release()


# --- the ASGI guard ---------------------------------------------------------


def _is_tool_call(body: bytes) -> bool:
    """True when a JSON-RPC body (single or batch) carries a ``tools/call``."""
    if b"tools/call" not in body:
        return False
    try:
        parsed = json.loads(body)
    except ValueError:
        return False
    items = parsed if isinstance(parsed, list) else [parsed]
    return any(isinstance(m, dict) and m.get("method") == "tools/call" for m in items)


class HostedGuard:
    """Rate limit, body cap and tool-call concurrency around any ASGI app."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: TokenBucketLimiter,
        gate: ConcurrencyGate,
        max_body_bytes: int,
        trust_proxy: bool,
        mcp_path: str = "/mcp",
        exempt_paths: Iterable[str] = ("/", "/healthz"),
    ) -> None:
        self.app = app
        self.limiter = limiter
        self.gate = gate
        self.max_body_bytes = max_body_bytes
        self.trust_proxy = trust_proxy
        self.mcp_path = mcp_path
        self.exempt_paths = frozenset(exempt_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        path = scope.get("path", "")
        addr = client_address(scope.get("client"), headers, self.trust_proxy)
        scope.setdefault("state", {})["quantra_client"] = addr

        if path not in self.exempt_paths:
            wait = self.limiter.acquire(addr)
            if wait > 0:
                retry = max(1, int(wait + 0.999))
                await _reply(
                    send,
                    429,
                    {"error": "rate limit exceeded", "retry_after_s": retry},
                    {"retry-after": str(retry)},
                )
                return

        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                declared = -1
            if declared > self.max_body_bytes:
                await _reply(
                    send,
                    413,
                    {"error": "request body too large", "max_body_bytes": self.max_body_bytes},
                )
                return

        if scope.get("method") not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return

        # Buffer the body once (bounded by the cap) so a single place answers 413 and the
        # concurrency gate can look at the JSON-RPC method.
        body, overflow = await _read_body(receive, self.max_body_bytes)
        if overflow:
            await _reply(
                send,
                413,
                {"error": "request body too large", "max_body_bytes": self.max_body_bytes},
            )
            return
        replay = _replayer(body, receive)
        if path != self.mcp_path or not _is_tool_call(body):
            await self.app(scope, replay, send)
            return
        if not self.gate.try_enter():
            await _reply(
                send,
                503,
                {
                    "error": "server busy: concurrency limit reached",
                    "limit": self.gate.limit,
                    "queue": self.gate.max_queue,
                },
                {"retry-after": "1"},
            )
            return
        await self.gate.wait()
        try:
            await self.app(scope, replay, send)
        finally:
            self.gate.leave()


async def _read_body(receive: Receive, limit: int) -> tuple[bytes, bool]:
    chunks = bytearray()
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunks.extend(message.get("body", b""))
        if len(chunks) > limit:
            return bytes(chunks), True
        if not message.get("more_body", False):
            break
    return bytes(chunks), False


def _replayer(body: bytes, receive: Receive) -> Receive:
    sent = False

    async def replay() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


async def _reply(
    send: Send, status: int, body: dict[str, Any], extra_headers: dict[str, str] | None = None
) -> None:
    payload = json.dumps(body).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode()),
    ]
    for k, v in (extra_headers or {}).items():
        headers.append((k.encode(), v.encode()))
    try:
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": payload})
    except Exception:  # the response may already have started
        log.debug("could not send %s (response already started)", status)


# --- transport security (SDK) -------------------------------------------------


def transport_security(settings: Settings, bind_host: str) -> TransportSecuritySettings | None:
    """What the SDK's ``/mcp`` host/origin check is configured with.

    ``None`` lets the SDK decide: on a loopback bind it protects
    ``localhost``/``127.0.0.1``/``[::1]`` (any port); on any other bind it
    disables the check. With ``QUANTRA_ALLOWED_HOSTS`` the listed hosts are
    accepted with or without a port; ``Origin`` is restricted only when
    ``QUANTRA_ALLOWED_ORIGINS`` is set (``http:*``/``https:*`` = any origin,
    matching the SDK's ``<prefix>:*`` wildcard rule).
    """
    if not settings.allowed_hosts:
        if bind_host not in _LOOPBACK:
            log.warning(
                "QUANTRA_ALLOWED_HOSTS is empty and the server binds %s: "
                "Host header validation (DNS-rebinding protection) is OFF",
                bind_host,
            )
        return None
    hosts: list[str] = []
    for h in settings.allowed_hosts:
        hosts.append(h)
        if ":" not in h or h.startswith("["):
            hosts.append(f"{h}:*")
    origins = list(settings.allowed_origins) or ["http:*", "https:*"]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins
    )


# --- access log (MCP-level middleware) ----------------------------------------


class AccessLog:
    """One line per ``tools/call``: ``ts client tool endpoint status ms``. No bodies."""

    def __init__(self, raw_ip: bool, trust_proxy: bool) -> None:
        self.raw_ip = raw_ip
        self.trust_proxy = trust_proxy

    def _client(self, ctx: ServerRequestContext[Any, Any]) -> str:
        request = ctx.request
        if not isinstance(request, Request):
            return "stdio"
        addr = request.scope.get("state", {}).get("quantra_client") or client_address(
            request.scope.get("client"), request.headers, self.trust_proxy
        )
        return display_address(str(addr), self.raw_ip)

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        if ctx.method != "tools/call":
            return await call_next(ctx)
        params = ctx.params or {}
        tool = str(params.get("name", "?"))
        started = time.perf_counter()
        status = "ok"
        endpoint = "-"
        try:
            result = await call_next(ctx)
        except Exception as exc:
            status = f"exception:{type(exc).__name__}"
            raise
        else:
            endpoint, status = _describe(result)
            return result
        finally:
            access_log.info(
                "client=%s tool=%s endpoint=%s status=%s ms=%d",
                self._client(ctx),
                tool,
                endpoint,
                status,
                int((time.perf_counter() - started) * 1000),
            )


def _describe(result: HandlerResult) -> tuple[str, str]:
    """(endpoint, status) from a CallToolResult without touching request/response bodies."""
    data: Any = result
    if isinstance(result, BaseModel):
        data = result.model_dump(by_alias=True, exclude_none=True)
    if not isinstance(data, dict):
        return "-", "ok"
    if data.get("isError") or data.get("is_error"):
        return "-", "tool_error"
    structured = data.get("structuredContent") or data.get("structured_content") or {}
    if not isinstance(structured, dict):
        return "-", "ok"
    endpoint = str(structured.get("endpoint") or "-")
    if structured.get("ok") is False:
        status = structured.get("status")
        return endpoint, f"engine_{status}" if status else "local_error"
    return endpoint, "ok"


# --- app assembly --------------------------------------------------------------


def _cors_get(response: Response) -> Response:
    response.headers["access-control-allow-origin"] = "*"
    response.headers["access-control-allow-methods"] = "GET, OPTIONS"
    return response


def build_http_app(
    server: MCPServer[Any],
    settings: Settings,
    backend: Backend,
    *,
    bind_host: str = "127.0.0.1",
    public_url: str = "",
    readyz_transport: httpx.AsyncBaseTransport | None = None,
) -> ASGIApp:
    """The ASGI app ``--http`` serves: the SDK's streamable-HTTP app (``/mcp``) with the
    pointer / health routes registered beside it, wrapped in :class:`HostedGuard`."""
    mcp_url = (public_url or settings.public_url or "").rstrip("/")
    mcp_url = f"{mcp_url}/mcp" if mcp_url else "/mcp"

    async def root(_: Request) -> Response:
        return _cors_get(
            JSONResponse(
                {
                    "name": "quantra-mcp",
                    "version": __version__,
                    "mcp": mcp_url,
                    "docs": DOCS_URL,
                    "how": f"add {mcp_url} as a custom MCP connector (streamable HTTP)",
                    "engine": settings.engine_url,
                }
            )
        )

    async def healthz(_: Request) -> Response:
        return _cors_get(JSONResponse({"status": "ok", "version": __version__}))

    async def readyz(_: Request) -> Response:
        try:
            async with httpx.AsyncClient(
                timeout=READYZ_TIMEOUT_S, transport=readyz_transport
            ) as http:
                r = await http.get(f"{backend.base_url}/health")
        except httpx.HTTPError as exc:
            return JSONResponse(
                {"status": "degraded", "engine": f"unreachable: {type(exc).__name__}"},
                status_code=503,
            )
        body: Any
        try:
            body = r.json()
        except ValueError:
            body = r.text[:200]
        ready = r.status_code == 200
        return JSONResponse(
            {"status": "ok" if ready else "degraded", "engine": body},
            status_code=200 if ready else 503,
        )

    # non-decorator form: the SDK's decorator is untyped
    server.custom_route("/", methods=["GET"], name="root")(root)
    server.custom_route("/healthz", methods=["GET"], name="healthz")(healthz)
    server.custom_route("/readyz", methods=["GET"], name="readyz")(readyz)
    mcp_app = server.streamable_http_app(
        host=bind_host,
        transport_security=transport_security(settings, bind_host),
        max_request_body_size=settings.max_body_bytes,
    )
    return HostedGuard(
        mcp_app,
        limiter=TokenBucketLimiter(settings.rate_limit_rpm, settings.rate_limit_burst),
        gate=ConcurrencyGate(settings.max_concurrency, settings.max_queue),
        max_body_bytes=settings.max_body_bytes,
        trust_proxy=settings.trust_proxy,
    )
