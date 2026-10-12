"""Tier 0: discovery tools. Everything here is a verbatim GET or a spec lookup."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from quantra_mcp.backend.base import Backend
from quantra_mcp.schema.loader import Spec, SpecError, load_spec, normalize_endpoint, pin
from quantra_mcp.tools._meta import tool
from quantra_mcp.tools._result import ToolResult, run_get

MAX_DEPTH = 8


def endpoint_schema(endpoint: str, depth: int = 3, spec: Spec | None = None) -> dict[str, Any]:
    """Request + response schema for one endpoint, refs inlined to ``depth``."""
    spec = spec or load_spec()
    depth = max(0, min(int(depth), MAX_DEPTH))
    info = spec.endpoint(endpoint)
    request = spec.request_schema(endpoint)
    return {
        "ok": True,
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
        "ok": True,
        "api_version": spec.api_version,
        "pin": pin().tag,
        "count": len(spec.endpoints),
        "endpoints": [
            {"endpoint": e.path, "summary": e.summary, "tag": e.tag, "description": e.description}
            for e in spec.endpoints
        ],
        "system": ["GET /health", "GET /status", "GET /meta"],
        "hint": "engine_schema(endpoint) returns one endpoint's request and response schema",
    }


def enum_listing(name: str, spec: Spec | None = None) -> dict[str, Any]:
    spec = spec or load_spec()
    values = spec.enum_values(name)  # raises SpecError with the available names
    short = name.removeprefix("quantra_enums_")
    return {
        "ok": True,
        "enum": short,
        "count": len(values),
        "values": values,
        "api_version": spec.api_version,
    }


def engine_schema_impl(endpoint: str | None, depth: int = 3) -> dict[str, Any]:
    if endpoint is None:
        return endpoints_listing()
    try:
        return endpoint_schema(endpoint, depth)
    except SpecError as exc:
        return {"ok": False, "error": str(exc), "endpoint": normalize_endpoint(endpoint)}


def register(app: MCPServer, backend: Backend) -> None:
    @tool(app, "Engine metadata (GET /meta)", engine=True)
    async def quantra_meta() -> ToolResult:
        """Engine metadata verbatim from GET /meta: API version, QuantLib version, product
        and endpoint lists. Call it first; it also proves the engine is reachable (the
        server's own /readyz covers liveness for operators). ``response`` is the engine
        body unchanged; ``engine.api_version`` comes from the response header, or from
        the body's ``openapi_version`` when the header is absent.
        """
        return await run_get(backend, "/meta")

    @tool(app, "Engine endpoints and schemas", engine=False)
    def engine_schema(
        endpoint: Annotated[
            str | None,
            Field(
                description="One of the engine's POST paths, e.g. /price-ois-swap (leading "
                "slash optional). Omit it to list every endpoint with a one-line summary; an "
                "unknown name answers with the valid endpoints.",
            ),
        ] = None,
        depth: Annotated[
            int,
            Field(
                ge=0,
                le=MAX_DEPTH,
                description="How many levels of $ref to inline (0..8). Deeper refs are left "
                "as {'$ref': '<Name>', 'unresolved': true}.",
            ),
        ] = 3,
    ) -> dict[str, Any]:
        """Request and response JSON schema of one engine endpoint, or (without
        ``endpoint``) the list of the 24 POST endpoints, from the vendored OpenAPI spec
        (no engine call; compare ``api_version`` with ``quantra_meta`` to detect a version
        mismatch). For one endpoint: the spec's top-level ``required`` list, the top-level
        field names and both schemas. The engine's rule: a field the product needs that
        is omitted is an error, never a default.
        """
        return engine_schema_impl(endpoint, depth)

    @tool(app, "Engine enum values", engine=False)
    def list_enums(
        name: Annotated[
            str,
            Field(
                description="An engine enum name: Calendar, DayCounter, Frequency, "
                "BusinessDayConvention, TimeUnit, Compounding, Interpolator, BootstrapTrait, "
                "... An unknown name answers with every available enum.",
            ),
        ],
    ) -> dict[str, Any]:
        """Values of an engine enum from the vendored spec (no engine call). Use it for any
        field typed as an enum (calendars, day counters, conventions, frequencies, ...).
        """
        try:
            return enum_listing(name)
        except SpecError as exc:
            return {"ok": False, "error": str(exc), "available": load_spec().enum_names}
