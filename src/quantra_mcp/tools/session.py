"""Tier 3: in-memory session scratch tools (invariant 6: never persisted)."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from quantra_mcp.errors import LocalValidationError
from quantra_mcp.schema.validate import validate_component
from quantra_mcp.session import SessionKind, SessionStore
from quantra_mcp.tools._market_source import MarketDataSource, check_source

_COMPONENT = {"curve": "TermStructure", "index": "IndexDef", "market": "RatesMarketData"}


def session_put_impl(
    store: SessionStore, name: str, kind: str, value: Any, market_data_source: Any
) -> dict[str, Any]:
    try:
        source = check_source(market_data_source)
        item, evicted = store.put(name, kind, value, source)
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
        "size": len(store),
        "max_items": store.max_items,
    }
    if evicted is not None:
        out["evicted"] = evicted
    return out


def session_get_impl(store: SessionStore, name: str) -> dict[str, Any]:
    try:
        item = store.get(name)
    except LocalValidationError as exc:
        return {"ok": False, "error": exc.error}
    out: dict[str, Any] = {"ok": True, **item.summary(), "value": item.value}
    attached = store.attached_indices(name)
    if attached:
        out["indices"] = attached
    return out


def session_list_impl(store: SessionStore) -> dict[str, Any]:
    return {
        "ok": True,
        "items": [i.summary() for i in store.items()],
        "size": len(store),
        "max_items": store.max_items,
    }


def session_delete_impl(store: SessionStore, name: str) -> dict[str, Any]:
    return {"ok": True, "name": name, "deleted": store.delete(name), "size": len(store)}


def register(app: MCPServer, store: SessionStore) -> None:
    @app.tool()
    def session_put(
        name: str, kind: SessionKind, value: dict[str, Any], market_data_source: MarketDataSource
    ) -> dict[str, Any]:
        """Store a curve, index or market block under a name for later calls.

        Args:
            name: free-form handle, e.g. ``sofr``.
            kind: ``curve`` (an engine TermStructure or a build_curve result,
                whose indices are kept alongside), ``index`` (an IndexDef) or
                ``market`` (``{curves: [...], indices: [...]}``).
            value: the object; it is validated against the engine schema.
            market_data_source: where the numbers in ``value`` come from. ``user_pasted``
                (the user pasted or typed the numbers in this conversation), ``user_file``
                (the user attached a file/screenshot the numbers were read from),
                ``engine_example`` (an engine example's pricing block, only when the user
                explicitly asked to run an example), ``session`` (a market previously
                stored in this session, which itself came from one of the above). There is
                no value for estimated, recalled or placeholder data. If you would have to
                invent numbers, do not call this tool: ask the user for the data. A
                build_curve result already carries its declaration; the two must agree.

        In-memory only, per server process, least-recently-used eviction at
        ``QUANTRA_SESSION_MAX_ITEMS`` (default 64). Reference it later as
        ``{"session": "<name>"}`` in ``bootstrap_curve`` (and pricing tools); the stored
        ``market_data_source`` is reported in their ``notes``.
        """
        return session_put_impl(store, name, kind, value, market_data_source)

    @app.tool()
    def session_get(name: str) -> dict[str, Any]:
        """Return a stored session item (``value`` plus a summary)."""
        return session_get_impl(store, name)

    @app.tool()
    def session_list() -> dict[str, Any]:
        """List stored session items (name, kind, stored_at, a short summary)."""
        return session_list_impl(store)

    @app.tool()
    def session_delete(name: str) -> dict[str, Any]:
        """Delete a stored session item; ``deleted`` is false if it did not exist."""
        return session_delete_impl(store, name)
