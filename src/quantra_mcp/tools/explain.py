"""Tier 6a: ``explain_method`` -- how the engine computes something, cited to its
own documentation and source at the pinned tag (``quantra://methodology/*``), and
how THIS server derives its analytics (``connector-analytics``, cited to this
repository). No engine call; nothing here is written by hand (see
``scripts/pin_engine.py`` / ``scripts/methodology_gen.py``)."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from quantra_mcp import methodology
from quantra_mcp.errors import LocalValidationError
from quantra_mcp.tools._meta import tool


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
    @tool(app, "Methodology, cited to engine and connector source", engine=False)
    def explain_method(
        topic: Annotated[
            str,
            Field(
                description="npv, fair-rate, greeks-bump-and-reprice, theta, curve-bootstrap, "
                "value-curves, settlement-and-cash-settlement, volatility-types, "
                "calendars-and-overrides, day-counters-and-compounding, schedules-and-stubs, "
                "error-codes (engine pages) or connector-analytics (what this server computes "
                "on top of engine outputs)."
            ),
        ],
    ) -> dict[str, Any]:
        """How the engine computes something, cited to its own docs and source at the pinned tag,
        or how this server derives its analytics (no engine call). Returns the page as
        ``markdown`` (summary, cited excerpts each with a path@tag:Lstart-Lend citation and a
        GitHub permalink, the request fields that control the behaviour, what is NOT documented)
        plus ``citations``, ``links``, ``repository``, ``not_documented`` and ``repos``. Quote
        the citations when you explain a number; never assert a cause the page does not support.
        """
        return explain_method_impl(topic)
