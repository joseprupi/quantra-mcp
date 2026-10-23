"""Tier 3: in-memory, per-MCP-session scratch tools (invariant 6: never persisted).

``session_put`` is the writer; ``session`` reads (``get`` / ``list``) and deletes.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from quantra_mcp.errors import LocalValidationError
from quantra_mcp.schema.validate import validate_component
from quantra_mcp.session import ScratchStore, SessionKind
from quantra_mcp.tools._market_source import (
    SOURCE_DESCRIPTION,
    SOURCES_DESCRIPTION,
    MarketDataSource,
    SourceRef,
    check_source,
    check_sources,
)
from quantra_mcp.tools._meta import tool

_COMPONENT = {"curve": "TermStructure", "index": "IndexDef", "market": "RatesMarketData"}

SessionAction = Literal["get", "list", "delete"]


def session_put_impl(
    store: ScratchStore,
    name: str,
    kind: str,
    value: Any,
    market_data_source: Any,
    sources: Any = None,
) -> dict[str, Any]:
    try:
        source = check_source(market_data_source)
        if not sources and isinstance(value, dict):
            prov = value.get("provenance")
            if isinstance(prov, dict) and prov.get("sources"):
                sources = prov["sources"]  # a builder result carries its citations
        cited = check_sources(source, sources)
        item, evicted = store.put(name, kind, value, source, sources=cited)
    except LocalValidationError as exc:
        return {"ok": False, "error": exc.error, "problems": exc.problems}
    problems = [p.as_dict() for p in validate_component(_COMPONENT[kind], item.value)]
    if problems:
        store.delete(name)
        return {
            "ok": False,
            "error": f"value does not match the engine {_COMPONENT[kind]} schema; not stored",
            "problems": problems,
        }
    out: dict[str, Any] = {
        "ok": True,
        "item": item.summary(),
        "market_data_source": item.market_data_source,
        "provenance": {
            "market_data_source": item.market_data_source,
            "sources": item.sources or [],
        },
        "size": len(store),
        "max_items": store.max_items,
    }
    if evicted is not None:
        out["evicted"] = evicted
    return out


def session_get_impl(store: ScratchStore, name: str) -> dict[str, Any]:
    try:
        item = store.get(name)
    except LocalValidationError as exc:
        return {"ok": False, "error": exc.error}
    out: dict[str, Any] = {"ok": True, **item.summary(), "value": item.value}
    attached = store.attached_indices(name)
    if attached:
        out["indices"] = attached
    return out


def session_list_impl(store: ScratchStore) -> dict[str, Any]:
    try:
        items = [i.summary() for i in store.items()]
    except LocalValidationError as exc:
        return {"ok": False, "error": exc.error, "problems": exc.problems}
    return {"ok": True, "items": items, "size": len(store), "max_items": store.max_items}


def session_delete_impl(store: ScratchStore, name: str) -> dict[str, Any]:
    try:
        deleted = store.delete(name)
    except LocalValidationError as exc:
        return {"ok": False, "error": exc.error, "problems": exc.problems}
    return {"ok": True, "name": name, "deleted": deleted, "size": len(store)}


def session_impl(store: ScratchStore, action: str, name: str | None) -> dict[str, Any]:
    if action == "list":
        return session_list_impl(store)
    if name is None:
        return {
            "ok": False,
            "error": f"action {action!r} needs the item's name",
            "problems": [{"path": "/name", "message": "required for get / delete"}],
        }
    if action == "get":
        return session_get_impl(store, name)
    if action == "delete":
        return session_delete_impl(store, name)
    return {
        "ok": False,
        "error": f"action must be get, list or delete, got {action!r}",
        "problems": [{"path": "/action", "message": "unknown action"}],
    }


def register(app: MCPServer, store: ScratchStore) -> None:
    @tool(app, "Store a curve / index / market in session scratch", engine=False, writes=True)
    def session_put(
        name: Annotated[str, Field(description="Free-form handle, e.g. sofr.")],
        kind: Annotated[
            SessionKind,
            Field(
                description="curve (an engine TermStructure or a build_curve result, whose "
                "indices are kept alongside), index (an IndexDef) or market ({curves: [...], "
                "indices: [...]}).",
            ),
        ],
        value: Annotated[
            dict[str, Any],
            Field(description="The object; validated against the engine schema."),
        ],
        market_data_source: Annotated[MarketDataSource, Field(description=SOURCE_DESCRIPTION)],
        sources: Annotated[list[SourceRef] | None, Field(description=SOURCES_DESCRIPTION)] = None,
    ) -> dict[str, Any]:
        """Store a curve, index or market block under a name and reference it later as
        ``{"session": "<name>"}`` in bootstrap_curve and the pricing tools (the stored
        ``market_data_source`` is reported in their ``notes``). In-memory only, private to
        this MCP session (another client of the same server never sees it), least-recently-
        used eviction at QUANTRA_SESSION_MAX_ITEMS (default 64), never persisted. A
        build_curve result carries its own declaration; the two must agree. On a transport
        without a session id (single-exchange streamable HTTP, protocol 2026-07-28) the tool
        answers ``ok: false``: pass the built objects inline instead.
        """
        return session_put_impl(store, name, kind, value, market_data_source, sources)

    @tool(app, "Read, list or delete session scratch", engine=False, writes=True)
    def session(
        action: Annotated[
            SessionAction,
            Field(
                description="get (one item: value plus a summary), list (every item's "
                "summary) or delete (deleted is false if it did not exist).",
            ),
        ],
        name: Annotated[
            str | None,
            Field(description="The item's name; required for get and delete."),
        ] = None,
    ) -> dict[str, Any]:
        """Read, list or delete items stored with session_put. Private to this MCP session,
        in memory, never persisted; a ``get`` miss lists only the caller's own names.
        """
        return session_impl(store, action, name)
