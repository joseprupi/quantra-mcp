"""Tier 6a: ``explain_method`` -- how the engine computes something, cited to its
own documentation and source at the pinned tag (``quantra://methodology/*``), and
how THIS server derives its analytics (``connector-analytics``, cited to this
repository). No engine call; nothing here is written by hand (see
``scripts/pin_engine.py`` / ``scripts/methodology_gen.py``)."""

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
    return {
        "ok": True,
        **page,
        "repos": methodology.repos(),
        "topics": [t["slug"] for t in methodology.topics()],
    }


def register(app: MCPServer) -> None:
    @app.tool()
    def explain_method(topic: str) -> dict[str, Any]:
        """How the engine computes something, cited to its own docs and source at the
        pinned tag; or how this server derives its analytics (no engine call).

        Args:
            topic: one of ``npv``, ``fair-rate``, ``greeks-bump-and-reprice``, ``theta``,
                ``curve-bootstrap``, ``value-curves``, ``settlement-and-cash-settlement``,
                ``volatility-types``, ``calendars-and-overrides``,
                ``day-counters-and-compounding``, ``schedules-and-stubs``, ``error-codes``
                (engine pages), or ``connector-analytics`` (what swap_dv01, key_rate_ladder,
                scenario, fair_rate and reprice_with compute on top of engine outputs, cited
                into this server's own source).

        Returns the page as ``markdown`` (plain-language summary, the cited excerpts each
        with a ``path@tag:Lstart-Lend`` citation and a GitHub permalink, the request
        fields that control the behaviour, and what is NOT documented), plus
        ``citations``, ``links`` (the permalinks), ``repository`` and ``not_documented``
        as lists; ``repos`` gives the engine and connector repository URLs. Quote the
        citations (or hand over the links) when you explain a number; never assert a
        cause the page does not support.
        """
        return explain_method_impl(topic)
