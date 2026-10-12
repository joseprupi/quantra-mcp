"""The MCP application: registers tools, resources and prompts; selects the transport."""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.mcpserver import MCPServer

from quantra_mcp import __version__, prompts, resources
from quantra_mcp.backend.base import Backend
from quantra_mcp.backend.engine_http import EngineHttpBackend
from quantra_mcp.config import Settings
from quantra_mcp.errors import EngineError, TransportError
from quantra_mcp.hosted import AccessLog, build_http_app
from quantra_mcp.middleware import FailuresAreErrors, SessionScope
from quantra_mcp.rules import ABSOLUTE_RULE, EXAMPLE_SHAPE_RULE
from quantra_mcp.schema.loader import load_spec, pin
from quantra_mcp.session import ScratchStore, SessionStores
from quantra_mcp.tools import (
    analytics,
    calendar,
    curves,
    discovery,
    examples,
    explain,
    pricing,
    raw,
    reconcile,
)
from quantra_mcp.tools import session as session_tools
from quantra_mcp.tools._meta import finish_schemas

log = logging.getLogger("quantra_mcp")

SERVER_NAME = "quantra"

GUIDANCE = """\
Quantra pricing engine (QuantLib): swaps, bonds, FRAs, caps/floors, swaptions, CDS,
equity options, inflation; curves from par quotes, discount factors or zero rates; DV01,
ladders, scenarios, fair rates; calendars. Every number comes from the engine. With a
business user speak in trade terms, not tool or preset names. For a trade: (1) can it be
priced here; (2) which market data is missing, in paste-able form; (3) every convention
assumed, with its source; (4) the engine's result against the user's number, causes
tested with reprice_with, never asserted; (5) the engine's numbers with their
assumptions. State sign conventions and dates. Turn engine refusals into what the user
can supply; never alter an input to match. "How is this computed?" comes from
explain_method with citations; never claim vendor data or methodology.

Developers: the technical map is the resource quantra://guide/developers.
"""

#: order matters: the rule first, the example-shape rule second, the guidance last
INSTRUCTIONS = f"{ABSOLUTE_RULE}\n{EXAMPLE_SHAPE_RULE}\n{GUIDANCE}"


def configure_logging(level: str) -> None:
    """stderr only: stdout is the MCP stdio channel."""
    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    # httpx logs every request at INFO; keep that for debug only.
    logging.getLogger("httpx").setLevel(
        logging.DEBUG if level.lower() == "debug" else logging.WARNING
    )


def _version_tuple(v: str) -> tuple[int, ...]:
    out: list[int] = []
    for part in v.strip().removeprefix("v").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if not digits:
            break
        out.append(int(digits))
    return tuple(out)


class EngineUnavailable(RuntimeError):
    """Raised at startup when ``QUANTRA_REQUIRE_ENGINE=1`` and ``/meta`` is unreachable."""


async def check_engine(backend: Backend, *, require: bool = False) -> None:
    """Startup probe: warn on stderr if the engine is older than the pin; with ``require``
    an unreachable ``/meta`` aborts startup instead of warning."""
    pinned = pin().version
    try:
        resp = await backend.meta()
    except (EngineError, TransportError) as exc:
        if require:
            raise EngineUnavailable(
                f"engine at {backend.base_url} not reachable ({exc}) and QUANTRA_REQUIRE_ENGINE=1"
            ) from exc
        log.warning(
            "engine at %s not reachable at startup (%s); tools will report errors",
            backend.base_url,
            exc,
        )
        return
    body = resp.body if isinstance(resp.body, dict) else {}
    engine_version = str(body.get("openapi_version") or resp.api_version or "")
    if not engine_version:
        log.warning(
            "engine at %s did not report a version; pinned contract is %s", backend.base_url, pinned
        )
        return
    if _version_tuple(engine_version) < _version_tuple(pinned):
        log.warning(
            "engine at %s reports API %s, older than the pinned contract %s; "
            "features added since (e.g. calendar_overrides) will be rejected",
            backend.base_url,
            engine_version,
            pinned,
        )
    else:
        log.info("engine at %s reports API %s (pin %s)", backend.base_url, engine_version, pinned)


def make_backend(settings: Settings) -> EngineHttpBackend:
    return EngineHttpBackend(
        settings.engine_url, settings.timeout_s, user_agent=f"quantra-mcp/{__version__}"
    )


def build_server(
    settings: Settings | None = None,
    backend: Backend | None = None,
    store: ScratchStore | None = None,
    *,
    close_backend: bool | None = None,
) -> MCPServer:
    """Create the app. ``backend`` defaults to the HTTP engine at ``settings.engine_url``;
    ``store`` defaults to a fresh per-MCP-session scratch registry capped by the settings.
    ``close_backend`` (default: only when the backend was created here) closes the
    backend when the server's lifespan ends."""
    settings = settings or Settings.from_env()
    owned = backend is None if close_backend is None else close_backend
    the_store: ScratchStore = store or SessionStores(
        settings.session_max_items, settings.session_max_total_bytes, settings.session_max_sessions
    )
    the_backend: Backend = backend or make_backend(settings)

    @asynccontextmanager
    async def lifespan(_: MCPServer[Any]) -> AsyncIterator[dict[str, Any]]:
        await check_engine(the_backend, require=settings.require_engine)
        try:
            yield {"backend": the_backend}
        finally:
            if owned:
                await the_backend.aclose()

    spec = load_spec()
    app: MCPServer[Any] = MCPServer(
        SERVER_NAME,
        title="Quantra pricing engine",
        instructions=INSTRUCTIONS
        + f"\nVendored contract: engine {pin().tag} (API {spec.api_version}).",
        version=__version__,
        website_url="https://quantra.io",
        lifespan=lifespan,
        log_level="WARNING",
    )
    # outermost first: the access log sees the final (flagged) result
    app.middleware.append(AccessLog(settings.log_raw_ip, settings.trust_proxy))
    app.middleware.append(FailuresAreErrors())
    app.middleware.append(SessionScope())
    discovery.register(app, the_backend)
    calendar.register(app, the_backend)
    raw.register(app, the_backend)
    curves.register(app, the_backend, the_store)
    pricing.register(app, the_backend, the_store)
    analytics.register(app, the_backend, the_store, settings.max_concurrency)
    examples.register(app)
    explain.register(app)
    reconcile.register(app, the_backend)
    session_tools.register(app, the_store)
    resources.register(app)
    prompts.register(app)
    finish_schemas(app)
    return app


def run(
    settings: Settings, *, http: bool = False, host: str = "127.0.0.1", port: int = 8765
) -> None:
    backend = make_backend(settings)
    app = build_server(settings, backend=backend, close_backend=True)
    if http:
        import uvicorn

        log.info(
            "streamable HTTP on http://%s:%d/mcp -> engine %s (per-session rate %d rpm burst "
            "%d, per-IP ceiling %d rpm, concurrency %d+%d queued (%d per session), "
            "body cap %d bytes, trust_proxy=%s, allowed_hosts=%s)",
            host,
            port,
            settings.engine_url,
            settings.rate_limit_rpm,
            settings.rate_limit_burst,
            settings.rate_limit_ip_rpm,
            settings.max_concurrency,
            settings.max_queue,
            settings.max_concurrency_per_session,
            settings.max_body_bytes,
            settings.trust_proxy,
            ",".join(settings.allowed_hosts) or "(none)",
        )
        asgi = build_http_app(app, settings, backend, bind_host=host)
        config = uvicorn.Config(asgi, host=host, port=port, log_level="warning")
        uvicorn.Server(config).run()
    else:
        log.info("stdio transport -> engine %s", settings.engine_url)
        app.run("stdio")
