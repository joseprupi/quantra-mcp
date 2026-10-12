"""Hosted-mode hardening for ``--http``: everything a public, no-auth deployment needs.

Layers, outermost first:

1. :class:`HostedGuard` (raw ASGI middleware around the whole Starlette app):
   a per-MCP-session token bucket over ``tools/call`` and ``resources/read``
   (keyed on the ``Mcp-Session-Id`` header the transport mints at
   ``initialize``, so claude.ai users behind shared egress IPs do not share a
   bucket; the client address is the key only before a session exists), a
   higher per-IP ceiling over every request (abuse protection), a request body
   cap (413), a per-session cap on in-flight tool calls and a global
   concurrency gate on ``tools/call`` with a bounded wait queue. A refused
   ``tools/call`` is answered as a JSON-RPC **tool error result** (``isError``,
   the same ``{ok: false, error, retry_after_s}`` body every failed tool
   returns) so the model sees an actionable message; any other refused request
   gets the plain HTTP status (429 / 503 + ``Retry-After``). The client address
   is the socket peer unless ``QUANTRA_TRUST_PROXY=1``, in which case the first
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
SESSION_HEADER = "mcp-session-id"
#: JSON-RPC methods that spend a token of the per-session bucket; the handshake and the
#: listings (``initialize``, ``notifications/initialized``, ``tools/list``, ...) are free
COUNTED_METHODS = frozenset({"tools/call", "resources/read"})


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


def _inspect(body: bytes) -> tuple[str | None, Any]:
    """``(method, id)`` of a single JSON-RPC message; ``("batch", None)`` for an array
    (batches are refused with the plain HTTP status); ``(None, None)`` when unparsable."""
    try:
        parsed = json.loads(body)
    except ValueError:
        return None, None
    if isinstance(parsed, list):
        return ("batch" if parsed else None), None
    if not isinstance(parsed, dict):
        return None, None
    method = parsed.get("method")
    return (method if isinstance(method, str) else None), parsed.get("id")


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


def rate_key(session_id: str | None, addr: str) -> str:
    """The per-session bucket key: the MCP session when the client has one, else its address."""
    return f"session:{session_id}" if session_id else f"ip:{addr}"


def tool_error_body(status: int, error: str, retry_after_s: int, **extra: Any) -> dict[str, Any]:
    """The structured body of a tool error raised by the hosted layer (same shape as
    a failed tool result: ``ok`` false, ``error``, ``status``, plus the retry hint)."""
    return {
        "ok": False,
        "endpoint": None,
        "status": status,
        "error": error,
        "retry_after_s": retry_after_s,
        "retry_hint": f"wait {retry_after_s} s and retry the same call",
        **extra,
    }


class HostedGuard:
    """Rate limits, body cap and tool-call concurrency around any ASGI app."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: TokenBucketLimiter,
        gate: ConcurrencyGate,
        max_body_bytes: int,
        trust_proxy: bool,
        ip_limiter: TokenBucketLimiter | None = None,
        per_session_limit: int = 0,
        mcp_path: str = "/mcp",
        exempt_paths: Iterable[str] = ("/", "/healthz"),
    ) -> None:
        self.app = app
        self.limiter = limiter
        self.ip_limiter = ip_limiter or TokenBucketLimiter(0, 1)
        self.gate = gate
        self.per_session_limit = max(0, per_session_limit)
        self.max_body_bytes = max_body_bytes
        self.trust_proxy = trust_proxy
        self.mcp_path = mcp_path
        self.exempt_paths = frozenset(exempt_paths)
        #: in-flight (running or queued) tool calls per rate key
        self._in_flight: dict[str, int] = {}

    def in_flight(self, key: str) -> int:
        return self._in_flight.get(key, 0)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        path = scope.get("path", "")
        addr = client_address(scope.get("client"), headers, self.trust_proxy)
        scope.setdefault("state", {})["quantra_client"] = addr
        is_mcp_post = path == self.mcp_path and scope.get("method") in ("POST", "PUT", "PATCH")

        # The per-IP ceiling covers every non-exempt request; an MCP POST is judged
        # below, together with its session bucket, so a refused tool call can be
        # answered as a tool error.
        if path not in self.exempt_paths and not is_mcp_post:
            wait = self.ip_limiter.acquire(addr)
            if wait > 0:
                await _reply_429(send, wait, "ip")
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
        # limits can look at the JSON-RPC method.
        body, overflow = await _read_body(receive, self.max_body_bytes)
        if overflow:
            await _reply(
                send,
                413,
                {"error": "request body too large", "max_body_bytes": self.max_body_bytes},
            )
            return
        replay = _replayer(body, receive)
        if not is_mcp_post:
            await self.app(scope, replay, send)
            return

        method, rpc_id = _inspect(body)
        session_id = headers.get(SESSION_HEADER) or None
        key = rate_key(session_id, addr)
        is_call = method == "tools/call" and rpc_id is not None
        wait = self.ip_limiter.acquire(addr)
        which = "ip"
        if wait <= 0 and (method in COUNTED_METHODS or method == "batch"):
            wait = self.limiter.acquire(key)
            which = "session"
        if wait > 0:
            retry = _retry_seconds(wait)
            self._refused("rate_limit:" + which, key, addr, retry)
            if is_call:
                await _reply_tool_error(
                    send,
                    rpc_id,
                    session_id,
                    tool_error_body(
                        429,
                        f"rate limit exceeded for this {which}: retry in {retry} s",
                        retry,
                        limit=self.limiter.rpm if which == "session" else self.ip_limiter.rpm,
                    ),
                )
            else:
                await _reply_429(send, wait, which)
            return
        if method == "batch" and _is_tool_call(body):
            # batches (removed from the protocol in 2025-06-18) get the plain status
            if not self.gate.try_enter():
                await _reply_503(send, self.gate)
                return
            await self._run_gated(scope, replay, send, key)
            return
        if not is_call:
            await self.app(scope, replay, send)
            return
        if self.per_session_limit and self.in_flight(key) >= self.per_session_limit:
            self._refused("session_cap", key, addr, 1)
            await _reply_tool_error(
                send,
                rpc_id,
                session_id,
                tool_error_body(
                    429,
                    f"this session already has {self.per_session_limit} tool call(s) in "
                    "flight; wait for one to finish, then retry",
                    1,
                    limit=self.per_session_limit,
                ),
            )
            return
        if not self.gate.try_enter():
            self._refused("busy", key, addr, 1)
            await _reply_tool_error(
                send,
                rpc_id,
                session_id,
                tool_error_body(
                    503,
                    "server busy: concurrency limit reached, retry in 1 s",
                    1,
                    limit=self.gate.limit,
                    queue=self.gate.max_queue,
                ),
            )
            return
        await self._run_gated(scope, replay, send, key)

    def _refused(self, why: str, key: str, addr: str, retry: int) -> None:
        access_log.info(
            "client=%s refused=%s key=%s retry_after_s=%d",
            display_address(addr, False),
            why,
            key if key.startswith("session:") else display_address(addr, False),
            retry,
        )

    async def _run_gated(self, scope: Scope, replay: Receive, send: Send, key: str) -> None:
        """Hold the global gate (a place is already reserved) and the session's slot."""
        self._in_flight[key] = self.in_flight(key) + 1
        await self.gate.wait()
        try:
            await self.app(scope, replay, send)
        finally:
            self.gate.leave()
            left = self.in_flight(key) - 1
            if left > 0:
                self._in_flight[key] = left
            else:
                self._in_flight.pop(key, None)


def _retry_seconds(wait: float) -> int:
    return max(1, int(wait + 0.999))


async def _reply_429(send: Send, wait: float, which: str) -> None:
    retry = _retry_seconds(wait)
    await _reply(
        send,
        429,
        {"error": "rate limit exceeded", "scope": which, "retry_after_s": retry},
        {"retry-after": str(retry)},
    )


async def _reply_503(send: Send, gate: ConcurrencyGate) -> None:
    await _reply(
        send,
        503,
        {
            "error": "server busy: concurrency limit reached",
            "limit": gate.limit,
            "queue": gate.max_queue,
        },
        {"retry-after": "1"},
    )


async def _reply_tool_error(
    send: Send, rpc_id: Any, session_id: str | None, structured: dict[str, Any]
) -> None:
    """Answer a ``tools/call`` with a JSON-RPC ``CallToolResult`` carrying ``isError``:
    HTTP 200, ``application/json`` (a single response the streamable-HTTP client accepts),
    ``Retry-After`` set as well for clients that look at headers."""
    result = {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "result": {
            "content": [{"type": "text", "text": json.dumps(structured)}],
            "structuredContent": structured,
            "isError": True,
        },
    }
    headers = {"retry-after": str(structured.get("retry_after_s", 1))}
    if session_id:
        headers[SESSION_HEADER] = session_id
    await _reply(send, 200, result, headers)


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
    flagged = bool(data.get("isError") or data.get("is_error"))
    structured = data.get("structuredContent") or data.get("structured_content") or {}
    if not isinstance(structured, dict):
        return "-", ("tool_error" if flagged else "ok")
    endpoint = str(structured.get("endpoint") or "-")
    if structured.get("ok") is False:
        status = structured.get("status")
        return endpoint, f"engine_{status}" if status else "local_error"
    return endpoint, ("tool_error" if flagged else "ok")


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
        ip_limiter=TokenBucketLimiter(settings.rate_limit_ip_rpm, ip_burst(settings)),
        gate=ConcurrencyGate(settings.max_concurrency, settings.max_queue),
        per_session_limit=settings.max_concurrency_per_session,
        max_body_bytes=settings.max_body_bytes,
        trust_proxy=settings.trust_proxy,
    )


def ip_burst(settings: Settings) -> int:
    """The per-IP bucket's burst: ten seconds of its rate, never below the session burst."""
    return max(settings.rate_limit_burst, settings.rate_limit_ip_rpm // 6)
