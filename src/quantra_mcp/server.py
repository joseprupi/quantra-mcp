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
from quantra_mcp.schema.loader import load_spec, pin
from quantra_mcp.session import SessionStore
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

log = logging.getLogger("quantra_mcp")

SERVER_NAME = "quantra"

INSTRUCTIONS = """\
You are connected to a Quantra pricing engine (QuantLib-based). Through it you can
price interest-rate swaps (fixed vs IBOR, overnight-index such as SOFR / ESTR /
SONIA), fixed, floating, zero-coupon and callable bonds, FRAs, caps and floors,
swaptions (European, Bermudan, American; on a vanilla or an OIS underlying; normal or
lognormal vol; physical or cash settlement), credit default swaps, equity options,
inflation swaps and inflation caps/floors; build discount curves from par quotes,
from discount factors or from zero rates; compute DV01, key-rate ladders, scenarios
and fair rates; and check business calendars. Every number you report comes from the
engine; nothing is computed on this side.

How to work with a business user (a trader, a risk manager, a treasurer):
- Speak in trade and market terms: notional, strike, expiry, the curve, the vol, the
  settlement style, day counts. Do not mention tool names, presets, sessions, request
  bodies, schemas or MCP unless the user asks how it works.
- When the user describes a trade, pastes a screenshot or a ticket (Bloomberg SWPM,
  a term sheet), answer in this order: (1) can it be priced here, yes or no, and why;
  (2) exactly which market data is missing and in what simple form they can paste it
  ("the discount factors from the Curves tab as date, DF rows", "the par quotes of the
  SOFR curve as tenor, rate %", "the normal vol in bp at 1M x 10Y"); (3) every
  convention you will assume, with its source, before pricing; (4) the engine's result
  reconciled against the number on the screen, premium against spot premium (the price
  paid today, not the forward premium), fair rate against fair rate, with the
  difference and its likely causes; (5) always the engine's own numbers together with
  the assumptions they rest on; the complete request only on demand.
- Licensed vendor curves and surfaces (Bloomberg ICVS / SWDF, Refinitiv, Markit, a
  bank's internal curve) are never available on this side and must not be invented
  or recalled from memory: the user has to paste them. A pasted table of discount
  factors, zero rates or par quotes is enough; it is read as given (percent signs and
  thousands separators are the only things normalised) and every row that cannot be
  read is reported back.
- State the sign convention of a value (positive = in favour of the side the user
  named), the valuation date and the dates the engine resolved (spot, maturity).
- If the engine refuses a request, translate its message into what the user can
  supply or change; never alter an input to make a number match.
- Market conventions come in named sets per currency and index (USD SOFR OIS, EUR
  Euribor 6M / 3M, EUR ESTR OIS, GBP SONIA, and trade-only sets for bonds, CDS, equity
  and inflation); each field carries its source.
- When the user brings a number from another system (a vendor screen, a counterparty,
  a spreadsheet, a risk report), follow the reconcile-external-price order: identify
  the instrument, list the inputs needed in paste-able form, confirm conventions, build
  the market from what was pasted, price, put the two numbers side by side
  (compare_results), then for each difference consult explain_method for that metric
  and TEST the candidate cause with reprice_with (flip the settlement method, swap
  zeros for discount factors, change the vol type or interpolator, bump a quote, roll
  the date) so the explanation is demonstrated, not asserted.
- "How is this computed?" is answered from explain_method (quantra://methodology/*):
  the engine's own documentation and source at the pinned tag, each statement with its
  path@tag:line citation. Where the page says the engine does not document something,
  say so. Never claim access to vendor data or vendor methodology. Never assert a cause
  for a difference without either a citation or a reprice that demonstrates it.

ABSOLUTE RULE on market data: Never type, estimate, recall or invent market data (quotes,
discount factors, zero rates, vols, fixings). Not as a placeholder, not as a test run, not
labelled as approximate. Market data for a user's trade exists only when the user has
pasted or dictated it in this conversation. Until then: say what is needed, in paste-able
form, and stop. Do not call any curve-building or pricing tool. Every curve-building and
pricing tool requires a market_data_source declaration (user_pasted, user_file,
engine_example, session); none of its values describes estimated, recalled or placeholder
data, so if you would have to invent numbers there is nothing you may declare: ask instead.
The 222 vendored engine examples are request-SHAPE references only. When pricing a trade
the user brought (a screenshot, a ticket, a description), the market data (curves, vols,
fixings) MUST come from the user, as pasted values or quotes they provide. Never reuse an
example's market data for the user's trade, even when the example looks like the same
instrument, and never say "the engine already ships this trade". An example's own market
data may be run only when the user explicitly asks to run that example, and then it is
reported as the example's data, never as a price of the user's trade. If the user has not
supplied market data, ask for it in paste-able form and stop there.

For developers (the technical map): quantra_meta reports the engine version and
products; list_endpoints / engine_schema / list_enums describe request shapes;
quantra://examples/* and list_examples / get_example give 222 complete verified
engine requests (an example's `pricing` block is a valid `market`); engine_request
POSTs any endpoint. Curves: list_presets / get_preset -> build_curve(preset, quotes)
or curve_from_pasted_table(text, kind=discount|zero|par) or build_value_curve ->
build_query -> bootstrap_curve; session_put stores a built curve so later calls pass
{"session": "<name>"}. Pricing: one price_<product> tool per product takes a `market`
({"session": name} | an engine pricing block | {curves, indices, ...}), a `preset`
whose trade block supplies every convention, and the trade economics; "spot" and
tenor dates are resolved by the engine's /calendar-advance and reported in
`date_resolution`. Analytics by composition: swap_dv01, key_rate_ladder, scenario,
fair_rate (each reprice's full result is in `calls`; DV01 `method` centered | up | down,
centered by default = (NPV(+bp) - NPV(-bp)) / 2). Reconciliation: compare_results
(external {label: number} vs a result; abs / rel differences; topic per metric),
explain_method(topic) (the cited methodology page) and reprice_with(result, changes)
(edit fields of an echoed request by path, reprice, diff both requests and the numeric
fields). The connector-side computations (bumps, DV01 differences, scenario changes,
reprice diffs) are documented and cited into this server's own source at
quantra://methodology/connector-analytics, which also lists which result fields are
engine outputs and which are differences computed here. Every citation carries a GitHub
permalink; the two repositories are the engine https://github.com/joseprupi/quantraserver
(cited at the pinned tag) and this server https://github.com/joseprupi/quantra-mcp (cited
at a commit); both URLs are also the first entry of the quantra://methodology index.
Every result echoes the exact
request sent (`request`) and the engine body verbatim (`response`); on an engine error
`ok` is false and `error` is the engine's text (400 = request wrong, 422 = well-formed
but unpriceable). The engine does not default omitted fields; every convention a
builder applies is listed in `notes` with its preset source.
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
    store: SessionStore | None = None,
    *,
    close_backend: bool | None = None,
) -> MCPServer:
    """Create the app. ``backend`` defaults to the HTTP engine at ``settings.engine_url``;
    ``store`` defaults to a fresh in-memory session store capped by the settings.
    ``close_backend`` (default: only when the backend was created here) closes the
    backend when the server's lifespan ends."""
    settings = settings or Settings.from_env()
    owned = backend is None if close_backend is None else close_backend
    the_store = store or SessionStore(settings.session_max_items, settings.session_max_total_bytes)
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
    app.middleware.append(AccessLog(settings.log_raw_ip, settings.trust_proxy))
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
    return app


def run(
    settings: Settings, *, http: bool = False, host: str = "127.0.0.1", port: int = 8765
) -> None:
    backend = make_backend(settings)
    app = build_server(settings, backend=backend, close_backend=True)
    if http:
        import uvicorn

        log.info(
            "streamable HTTP on http://%s:%d/mcp -> engine %s (rate %d rpm burst %d, "
            "concurrency %d+%d queued, body cap %d bytes, trust_proxy=%s, allowed_hosts=%s)",
            host,
            port,
            settings.engine_url,
            settings.rate_limit_rpm,
            settings.rate_limit_burst,
            settings.max_concurrency,
            settings.max_queue,
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
