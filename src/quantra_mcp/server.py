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
from quantra_mcp.schema.loader import load_spec, pin
from quantra_mcp.session import SessionStore
from quantra_mcp.tools import analytics, calendar, curves, discovery, examples, pricing, raw
from quantra_mcp.tools import session as session_tools

log = logging.getLogger("quantra_mcp")

SERVER_NAME = "quantra"

INSTRUCTIONS = """\
quantra-mcp fronts a Quantra pricing engine (QuantLib-based JSON API). Every
number in a tool result comes from the engine; this server computes nothing.
Start with quantra_meta (engine version and products). Use list_endpoints,
engine_schema and list_enums to discover request shapes, the
quantra://examples/* resources for complete working requests, and
engine_request to POST any endpoint. To build a yield curve from a quote
strip: list_presets -> build_curve(preset, quotes) -> build_query ->
bootstrap_curve; build_value_curve makes a curve from explicit zero / discount
/ forward values; session_put stores a built curve so later calls can pass
{"session": "<name>"}. To price: price_vanilla_swap / price_ois_swap /
price_fixed_rate_bond / price_floating_rate_bond / price_zero_coupon_bond /
price_callable_fixed_rate_bond / price_fra / price_cap_floor / price_swaption /
price_cds / price_equity_option / price_zc_inflation_swap /
price_yoy_inflation_swap / price_yoy_inflation_cap_floor take a `market`
({"session": name} | an engine pricing block | {curves, indices, ...}), a
`preset` whose trade block supplies every convention, and the trade
economics; dates like effective_date "spot" or a tenor are resolved by the
engine's /calendar-advance and reported in `date_resolution`.
Analytics by composition: swap_dv01 (bump every quote of the trade's curves,
reprice, dv01 = bumped - base), key_rate_ladder (one reprice per actual
pillar of a curve + a parallel bump; sum_of_buckets vs parallel.dv01),
scenario (named bump / replace-quote variants, NPV table) and fair_rate (the
engine's fair_rate field); every result's `calls` holds the complete
pricing result of each reprice, so each is replayable by curl.
list_examples / get_example give 223 complete, verified engine requests (the
engine's own fixtures with their QuantLib reference values); an example's
`pricing` block is a valid `market`. Every result echoes the exact request sent (`request`,
session references resolved) and the engine body verbatim (`response`); on an
engine error `ok` is false and `error` is the engine's text (400 = request
wrong, 422 = well-formed but unpriceable). The engine does not default omitted
fields; every convention a builder applies is listed in `notes` with its
preset source.
"""


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


async def check_engine(backend: Backend) -> None:
    """Best-effort startup probe: warn on stderr if the engine is older than the pin."""
    pinned = pin().version
    try:
        resp = await backend.meta()
    except (EngineError, TransportError) as exc:
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


def build_server(
    settings: Settings | None = None,
    backend: Backend | None = None,
    store: SessionStore | None = None,
) -> MCPServer:
    """Create the app. ``backend`` defaults to the HTTP engine at ``settings.engine_url``;
    ``store`` defaults to a fresh in-memory session store capped by the settings."""
    settings = settings or Settings.from_env()
    owned = backend is None
    the_store = store or SessionStore(settings.session_max_items)
    the_backend: Backend = backend or EngineHttpBackend(
        settings.engine_url, settings.timeout_s, user_agent=f"quantra-mcp/{__version__}"
    )

    @asynccontextmanager
    async def lifespan(_: MCPServer[Any]) -> AsyncIterator[dict[str, Any]]:
        await check_engine(the_backend)
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
    discovery.register(app, the_backend)
    calendar.register(app, the_backend)
    raw.register(app, the_backend)
    curves.register(app, the_backend, the_store)
    pricing.register(app, the_backend, the_store)
    analytics.register(app, the_backend, the_store, settings.max_concurrency)
    examples.register(app)
    session_tools.register(app, the_store)
    resources.register(app)
    prompts.register(app)
    return app


def run(
    settings: Settings, *, http: bool = False, host: str = "127.0.0.1", port: int = 8765
) -> None:
    app = build_server(settings)
    if http:
        log.info(
            "streamable HTTP on http://%s:%d/mcp -> engine %s", host, port, settings.engine_url
        )
        app.run("streamable-http", host=host, port=port)
    else:
        log.info("stdio transport -> engine %s", settings.engine_url)
        app.run("stdio")
