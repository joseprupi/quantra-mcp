"""Tier 2: the raw passthrough. Any engine endpoint, agent-supplied body."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from quantra_mcp.backend.base import Backend
from quantra_mcp.schema.loader import SpecError, load_spec, normalize_endpoint
from quantra_mcp.schema.validate import validate_request
from quantra_mcp.tools._result import ToolResult, local_error_result, new_request_id, run_post


async def engine_request_impl(
    backend: Backend,
    endpoint: str,
    body: Any,
    validate: bool = True,
    request_id: str | None = None,
) -> ToolResult:
    spec = load_spec()
    try:
        info = spec.endpoint(endpoint)
    except SpecError as exc:
        return local_error_result(normalize_endpoint(endpoint), body, str(exc))
    rid = request_id or new_request_id()
    if not isinstance(body, dict):
        return local_error_result(
            info.path,
            body,
            f"body must be a JSON object, got {type(body).__name__}",
            [{"path": "/", "message": "expected object"}],
            request_id=rid,
        )
    if validate:
        problems = validate_request(info.path, body, spec)
        if problems:
            return local_error_result(
                info.path,
                body,
                f"request does not match the {spec.api_version} schema for {info.path} "
                f"({len(problems)} problem{'s' if len(problems) != 1 else ''}); "
                "fix and retry, or pass validate=false to send anyway",
                [p.as_dict() for p in problems],
                request_id=rid,
            )
    return await run_post(backend, info.path, body, request_id=rid)


def register(app: MCPServer, backend: Backend) -> None:
    @app.tool()
    async def engine_request(
        endpoint: str,
        body: dict[str, Any],
        validate: bool = True,
        request_id: str | None = None,
    ) -> ToolResult:
        """POST a JSON body to any engine endpoint (the raw escape hatch).

        Args:
            endpoint: one of the engine's POST paths (see ``list_endpoints``),
                e.g. ``/price-ois-swap``.
            body: the full request object exactly as the engine expects it
                (``engine_schema`` and the ``quantra://examples/*`` resources
                show the shape). The engine does not default omitted fields.
            validate: check ``body`` against the vendored OpenAPI schema first
                (default True). On failure nothing is sent and ``problems``
                lists each JSON-pointer path with a message.
            request_id: optional ``X-Request-Id`` to forward; one is generated
                when absent and reported in ``engine.request_id``.

        Returns ``{ok, endpoint, request, response, engine}``; on an engine
        error ``ok=false`` with the HTTP ``status`` and the engine's ``error``
        text verbatim (400 = request wrong, 422 = well-formed but unpriceable).
        """
        return await engine_request_impl(backend, endpoint, body, validate, request_id)
