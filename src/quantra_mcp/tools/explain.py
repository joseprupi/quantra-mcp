"""Tier 6a: ``explain_method`` -- how the engine computes something, cited to its
own documentation and source at the pinned tag (``quantra://methodology/*``).
No engine call; nothing here is written by hand (see ``scripts/pin_engine.py``)."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from quantra_mcp import methodology
from quantra_mcp.errors import LocalValidationError


def explain_method_impl(topic: str) -> dict[str, Any]:
    try:
        page = methodology.get_topic(topic)
    except LocalValidationError as exc:
        return {
            "ok": False,
            "error": exc.error,
            "problems": exc.problems,
            "topics": methodology.topics(),
        }
    return {"ok": True, **page, "topics": [t["slug"] for t in methodology.topics()]}


def register(app: MCPServer) -> None:
    @app.tool()
    def explain_method(topic: str) -> dict[str, Any]:
        """How the engine computes something, cited to its own docs and source at the
        pinned tag (no engine call).

        Args:
            topic: one of ``npv``, ``fair-rate``, ``greeks-bump-and-reprice``, ``theta``,
                ``curve-bootstrap``, ``value-curves``, ``settlement-and-cash-settlement``,
                ``volatility-types``, ``calendars-and-overrides``,
                ``day-counters-and-compounding``, ``error-codes``.

        Returns the page as ``markdown`` (plain-language summary, the engine's own
        excerpts each with a ``path@tag:Lstart-Lend`` citation, the request fields that
        control the behaviour, and what the engine does NOT document), plus
        ``citations`` and ``not_documented`` as lists. Quote the citations when you
        explain a number; never assert a cause the page does not support.
        """
        return explain_method_impl(topic)
