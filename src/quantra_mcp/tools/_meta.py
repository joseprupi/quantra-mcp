"""Tool registration metadata: a human title, the MCP annotations and a dedented
description for every tool, and the post-registration pass that drops pydantic's
automatic ``title`` keys from the emitted input schemas.

Annotations (MCP ``ToolAnnotations``): ``readOnlyHint`` is true for every tool
except the session writers; ``destructiveHint`` is always false (nothing here
deletes anything outside the caller's own in-memory scratch); ``idempotentHint``
is true for every tool (the engine is stateless, scratch writes are replace-by-
name); ``openWorldHint`` is true for the tools that call the engine.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

#: the ceiling a tool description may reach (pinned by a test)
MAX_DESCRIPTION_CHARS = 1500

_F = TypeVar("_F", bound=Callable[..., Any])


def tool(
    app: MCPServer,
    title: str,
    *,
    engine: bool,
    writes: bool = False,
    name: str | None = None,
) -> Callable[[_F], _F]:
    """Register ``fn`` as a tool with ``title``, annotations and its docstring dedented.

    ``engine``: the tool POSTs / GETs the engine (``openWorldHint``). ``writes``: the
    tool mutates session scratch (``readOnlyHint`` false)."""

    def deco(fn: _F) -> _F:
        description = inspect.cleandoc(fn.__doc__ or "")
        annotations = ToolAnnotations(
            title=title,
            read_only_hint=not writes,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=engine,
        )
        app.tool(
            name=name or fn.__name__, title=title, description=description, annotations=annotations
        )(fn)
        return fn

    return deco


def strip_titles(schema: Any) -> Any:
    """Drop the ``title`` keyword pydantic adds to every object / property schema
    (the argument name already is the title; a property NAMED ``title`` is kept)."""
    if isinstance(schema, dict):
        out: dict[str, Any] = {}
        for key, value in schema.items():
            if key == "title" and not isinstance(value, dict):
                continue
            if key in ("properties", "$defs") and isinstance(value, dict):
                out[key] = {k: strip_titles(v) for k, v in value.items()}
            else:
                out[key] = strip_titles(value)
        return out
    if isinstance(schema, list):
        return [strip_titles(v) for v in schema]
    return schema


def strip_schema_titles(app: MCPServer) -> None:
    """Rewrite every registered tool's input schema without pydantic titles."""
    for registered in app._tool_manager.list_tools():
        registered.parameters = strip_titles(registered.parameters)
