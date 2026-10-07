"""The uniform tool result shape and the one place that calls the backend.

Success::

    {"ok": true, "endpoint": "/calendar-holidays", "request": {...sent...},
     "response": {...engine body verbatim...}, "summary": {...selection...},
     "engine": {"api_version": "0.7.0", "request_id": "qmcp-..."}}

Engine error (non-2xx)::

    {"ok": false, "endpoint": ..., "status": 400, "error": "<engine text verbatim>",
     "response": {...engine error body...}, "request": {...sent...}, "engine": {...}}

Local rejection (never reached the engine)::

    {"ok": false, "endpoint": ..., "status": null, "error": "...",
     "problems": [{"path": "/json/pointer", "message": "..."}],
     "request": {...would have been sent...}, "engine": {"api_version": null, "request_id": ...}}

``summary`` is a selection of response fields, never a computation.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from quantra_mcp.backend.base import Backend
from quantra_mcp.errors import EngineError, LocalValidationError, TransportError

log = logging.getLogger(__name__)

Summarizer = Callable[[Any], dict[str, Any] | None]

ToolResult = dict[str, Any]


def new_request_id() -> str:
    return f"qmcp-{uuid.uuid4().hex}"


def _engine_block(api_version: str | None, request_id: str | None) -> dict[str, Any]:
    return {"api_version": api_version, "request_id": request_id}


def local_error_result(
    endpoint: str,
    request: Any,
    error: str,
    problems: list[dict[str, str]] | None = None,
    request_id: str | None = None,
) -> ToolResult:
    return {
        "ok": False,
        "endpoint": endpoint,
        "status": None,
        "error": error,
        "problems": problems or [],
        "request": request,
        "engine": _engine_block(None, request_id),
    }


async def run_post(
    backend: Backend,
    endpoint: str,
    body: Any,
    *,
    request_id: str | None = None,
    summarize: Summarizer | None = None,
) -> ToolResult:
    """POST ``body`` to ``endpoint`` and wrap the outcome in the uniform shape."""
    rid = request_id or new_request_id()
    try:
        resp = await backend.post(endpoint, body, request_id=rid)
    except EngineError as exc:
        log.info("engine %s -> HTTP %s: %s", endpoint, exc.status, exc.error)
        return {
            "ok": False,
            "endpoint": endpoint,
            "status": exc.status,
            "error": exc.error,
            "response": exc.body,
            "request": body,
            "engine": _engine_block(exc.headers.get("x-quantra-api-version"), rid),
        }
    except TransportError as exc:
        log.warning("engine %s unreachable: %s", endpoint, exc.error)
        return local_error_result(
            endpoint, body, f"engine unreachable: {exc.error}", request_id=rid
        )
    except LocalValidationError as exc:  # a backend may validate too
        return local_error_result(endpoint, body, exc.error, exc.problems, request_id=rid)

    result: ToolResult = {
        "ok": True,
        "endpoint": endpoint,
        "request": body,
        "response": resp.body,
    }
    if summarize is not None:
        summary = summarize(resp.body)
        if summary is not None:
            result["summary"] = summary
    result["engine"] = _engine_block(resp.api_version, resp.request_id or rid)
    return result


async def run_get(backend: Backend, path: str) -> ToolResult:
    try:
        resp = await backend.get(path)
    except EngineError as exc:
        return {
            "ok": False,
            "endpoint": path,
            "status": exc.status,
            "error": exc.error,
            "response": exc.body,
            "request": None,
            "engine": _engine_block(exc.headers.get("x-quantra-api-version"), None),
        }
    except TransportError as exc:
        return local_error_result(path, None, f"engine unreachable: {exc.error}")
    return {
        "ok": True,
        "endpoint": path,
        "request": None,
        "response": resp.body,
        "engine": _engine_block(resp.api_version, resp.request_id),
    }
