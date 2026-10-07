"""Tier 0: discovery tools. Everything here is a verbatim GET or a spec lookup."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from quantra_mcp.backend.base import Backend
from quantra_mcp.schema.loader import Spec, SpecError, load_spec, normalize_endpoint, pin
from quantra_mcp.tools._result import ToolResult, run_get

MAX_DEPTH = 8


def endpoint_schema(endpoint: str, depth: int = 3, spec: Spec | None = None) -> dict[str, Any]:
    """Request + response schema for one endpoint, refs inlined to ``depth``."""
    spec = spec or load_spec()
    depth = max(0, min(int(depth), MAX_DEPTH))
    info = spec.endpoint(endpoint)
    request = spec.request_schema(endpoint)
    return {
        "endpoint": info.path,
        "summary": info.summary,
        "description": info.description,
        "api_version": spec.api_version,
        "depth": depth,
        "required": spec.required_fields(endpoint),
        "top_level_fields": list(request.get("properties", {})),
        "request_schema_name": info.request_schema.removeprefix("quantra_"),
        "request_schema": spec.resolve(request, depth),
        "response_schema_name": info.response_schema.removeprefix("quantra_"),
        "response_schema": spec.resolve(spec.response_schema(endpoint), depth),
        "note": (
            "Unresolved refs appear as {'$ref': '<Name>', 'unresolved': true}; "
            "call engine_schema with a larger depth or read quantra://schema/<endpoint>. "
            "The engine does not default omitted fields."
        ),
    }


def endpoints_listing(spec: Spec | None = None) -> dict[str, Any]:
    spec = spec or load_spec()
    return {
        "api_version": spec.api_version,
        "pin": pin().tag,
        "count": len(spec.endpoints),
        "endpoints": [
            {"endpoint": e.path, "summary": e.summary, "tag": e.tag, "description": e.description}
            for e in spec.endpoints
        ],
        "system": ["GET /health", "GET /status", "GET /meta"],
    }


def enum_listing(name: str, spec: Spec | None = None) -> dict[str, Any]:
    spec = spec or load_spec()
    values = spec.enum_values(name)  # raises SpecError with the available names
    short = name.removeprefix("quantra_enums_")
    return {"enum": short, "count": len(values), "values": values, "api_version": spec.api_version}


def register(app: MCPServer, backend: Backend) -> None:
    @app.tool()
    async def quantra_meta() -> ToolResult:
        """Engine metadata, verbatim from GET /meta.

        Call this first: it tells you the engine's API version, QuantLib
        version, product list and endpoint list. The ``response`` field is the
        engine body unchanged.
        """
        return await run_get(backend, "/meta")

    @app.tool()
    async def quantra_health() -> ToolResult:
        """Engine liveness, verbatim from GET /health."""
        return await run_get(backend, "/health")

    @app.tool()
    def list_endpoints() -> dict[str, Any]:
        """The engine's POST endpoints (24 at the pinned version) with one-line descriptions.

        Taken from the vendored OpenAPI spec, not from the live engine; compare
        with ``quantra_meta`` to detect a version mismatch.
        """
        return endpoints_listing()

    @app.tool()
    def engine_schema(endpoint: str, depth: int = 3) -> dict[str, Any]:
        """Request and response JSON schema for one engine endpoint.

        Args:
            endpoint: one of the 24 POST paths, e.g. ``/price-ois-swap``
                (leading slash optional). Unknown names return an error that
                lists the valid endpoints.
            depth: how many levels of ``$ref`` to inline (0..8, default 3).
                Deeper refs are left as ``{"$ref": "<Name>", "unresolved": true}``.

        Returns the spec's top-level ``required`` list, the top-level field
        names, and both schemas. Remember the engine's rule: a field the
        product needs that is omitted is an error, never a default.
        """
        try:
            return endpoint_schema(endpoint, depth)
        except SpecError as exc:
            return {"ok": False, "error": str(exc), "endpoint": normalize_endpoint(endpoint)}

    @app.tool()
    def list_enums(name: str) -> dict[str, Any]:
        """Values of an engine enum from the vendored spec.

        Args:
            name: e.g. ``Calendar``, ``DayCounter``, ``Frequency``,
                ``BusinessDayConvention``, ``TimeUnit``, ``Compounding``,
                ``Interpolator``, ``BootstrapTrait``. An unknown name returns
                an error listing every available enum.
        """
        try:
            return enum_listing(name)
        except SpecError as exc:
            return {"ok": False, "error": str(exc), "available": load_spec().enum_names}
