"""Server-level (transport-independent) middleware.

:class:`SessionScope` binds the MCP session id of every inbound message for the
rest of the handler chain, so the tools' session scratch resolves to the caller's
own store (:mod:`quantra_mcp.session`). :class:`FailuresAreErrors` turns every
failed tool result (``ok: false``: an engine error, a local validation rejection,
a provenance refusal) into an MCP tool error (``isError``) while keeping the same
JSON body as content, so clients that read the body keep working and clients
that look at the flag see a failure.
"""

from __future__ import annotations

from typing import Any

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.types import CallToolResult

from quantra_mcp.session import SESSIONLESS, bind_session

SESSION_HEADER = "mcp-session-id"


def mcp_session_id(ctx: ServerRequestContext[Any, Any]) -> str | None:
    """The transport's session id for this message, ``None`` on stdio / in-memory.

    Read from the SDK's ``Connection.session_id`` (the ``mcp_session_id`` the
    streamable-HTTP transport minted at ``initialize``, reached through the
    request's ``ServerSession``), falling back to the ``Mcp-Session-Id`` request
    header, which the SDK's session manager used to route the request to that very
    transport, so the two agree by construction.
    """
    connection = getattr(ctx.session, "_connection", None)
    sid = getattr(connection, "session_id", None)
    if isinstance(sid, str) and sid:
        return sid
    headers = getattr(ctx.request, "headers", None)
    if headers is not None:
        try:
            header = headers.get(SESSION_HEADER)
        except Exception:  # pragma: no cover - a headers object without .get
            header = None
        if isinstance(header, str) and header:
            return header
    return None


def session_key(ctx: ServerRequestContext[Any, Any]) -> str | None:
    """What the scratch registry is keyed on for this message: the MCP session id when
    the transport has one; :data:`SESSIONLESS` for an HTTP request without one (the
    2026-07-28 single-exchange era, which carries no identity at all); ``None`` (the
    implicit local session) for stdio and in-process clients."""
    sid = mcp_session_id(ctx)
    if sid:
        return sid
    if getattr(ctx.request, "headers", None) is not None:
        return SESSIONLESS
    return None


class SessionScope:
    """Bind the caller's MCP session for the duration of each message."""

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        with bind_session(session_key(ctx)):
            return await call_next(ctx)


def _is_failure(structured: Any) -> bool:
    return isinstance(structured, dict) and structured.get("ok") is False


class FailuresAreErrors:
    """``tools/call`` results whose body says ``ok: false`` are returned with ``isError``."""

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        result = await call_next(ctx)
        if ctx.method != "tools/call":
            return result
        if isinstance(result, CallToolResult):
            if not result.is_error and _is_failure(result.structured_content):
                return result.model_copy(update={"is_error": True})
            return result
        if isinstance(result, dict):
            structured = result.get("structuredContent", result.get("structured_content"))
            if _is_failure(structured) and not (result.get("isError") or result.get("is_error")):
                return {**result, "isError": True}
        return result
