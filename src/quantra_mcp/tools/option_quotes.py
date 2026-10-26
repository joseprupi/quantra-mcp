"""Tier 4: option quotes -> implied forwards / a Black vol surface (engine >= 0.14.0).

``calibrate_implied_forwards`` (``POST /calibrate-implied-forwards``) and
``calibrate_vol_surface`` (``POST /calibrate-vol-surface``). Thin tools: the quote sheet
is the engine's own ``OptionQuoteSheet`` (sent as given), the market goes through the
same ``market`` resolution as every pricing tool (it must carry the discounting curve, the
underlying's ``EquityUnderlyingSpec`` and its spot quote), the request is validated
against the vendored spec, POSTed and echoed in full. The summary is a selection of the
engine's response (plus per-expiry counts of the dropped-quote reasons the engine gave);
nothing is fitted or computed here. Option quotes are market data: the
``market_data_source`` declaration (and ``sources`` when ``sourced``) is required.

``reuse`` in the summary is the engine's own output placed where the next tool takes it:
the response path of the dividend curve (a ``market.curves`` item) and of the vol surface
(a ``market.vol_surfaces`` item), with its id (``price_structured_note`` /
``price_equity_option`` read them by id).
"""

from __future__ import annotations

import copy
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from quantra_mcp.backend.base import Backend
from quantra_mcp.builders import market as mk
from quantra_mcp.builders.schedule import problem
from quantra_mcp.digest import market_notes
from quantra_mcp.errors import LocalValidationError
from quantra_mcp.schema.enums_generated import VolSurfaceFitMethod
from quantra_mcp.schema.validate import validate_request
from quantra_mcp.session import ScratchStore
from quantra_mcp.tools._market_source import (
    SOURCE_DESCRIPTION,
    SOURCES_DESCRIPTION,
    MarketDataSource,
    SourceRef,
    source_refusal,
    stamp,
)
from quantra_mcp.tools._meta import tool
from quantra_mcp.tools._result import ToolResult, local_error_result, new_request_id, run_post
from quantra_mcp.tools.calendar import CalendarOverride, overrides_to_wire

IMPLIED_FORWARDS = "/calibrate-implied-forwards"
VOL_SURFACE = "/calibrate-vol-surface"

Market = dict[str, Any]

#: per-expiry fields copied into the implied-forwards summary (a selection)
FORWARD_KEYS = (
    "expiry",
    "time",
    "usable",
    "dropped_reason",
    "quotes_used",
    "quotes_dropped",
    "forward",
    "implied_discount_factor",
    "curve_discount_factor",
    "implied_funding_bp",
    "implied_dividend_yield",
    "fit_residual_rmse",
    "max_abs_residual",
)

#: per-expiry fields copied into the vol-surface summary (a selection)
SURFACE_EXPIRY_KEYS = (
    "expiry",
    "time",
    "usable",
    "dropped_reason",
    "forward",
    "quotes_used",
    "quotes_dropped",
    "rmse_vol",
    "max_abs_error_vol",
    "svi",
    "butterfly_min_g",
    "butterfly_violation",
    "butterfly_action",
    "calendar_violations",
    "calendar_max_clip_vol",
    "calendar_action",
    "grid_points_filled",
)

REPRICING_KEYS = ("count", "inside_bid_ask", "max_abs_price_error", "max_abs_vol_error")


def _reasons(dropped: Any) -> dict[str, int]:
    """Count of the engine's ``dropped[].reason`` strings (verbatim) of one expiry."""
    out: dict[str, int] = {}
    if isinstance(dropped, list):
        for d in dropped:
            if isinstance(d, dict):
                reason = str(d.get("reason", "?"))
                out[reason] = out.get(reason, 0) + 1
    return out


def _expiry_rows(rows: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in rows if isinstance(rows, list) else []:
        if not isinstance(e, dict):
            continue
        row = {k: e[k] for k in keys if k in e}
        reasons = _reasons(e.get("dropped"))
        if reasons:
            row["dropped_reasons"] = reasons
        out.append(row)
    return out


def implied_forwards_summary(response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict) or not isinstance(response.get("expiries"), list):
        return None
    expiries = _expiry_rows(response["expiries"], FORWARD_KEYS)
    out: dict[str, Any] = {
        "underlying_id": response.get("underlying_id"),
        "spot": response.get("spot"),
        "filter_applied": response.get("filter"),
        "expiries_fitted": sum(1 for e in expiries if e.get("usable")),
        "expiries_total": len(expiries),
        "quotes_dropped_total": sum(int(e.get("quotes_dropped") or 0) for e in expiries),
        "expiries": expiries,
    }
    spec = response.get("dividend_curve_spec")
    if isinstance(spec, dict):
        out["reuse"] = {
            "market.curves": "response.dividend_curve_spec",
            "id": spec.get("id"),
            "how": (
                f"add this TermStructure to market.curves and set the underlying's "
                f"EquityUnderlyingSpec.dividend_yield_curve_id to {spec.get('id')!r} "
                "(price_structured_note, price_equity_option dividend_yield); pass "
                "response.expiries as calibrate_vol_surface forwards"
            ),
        }
    return out


def vol_surface_summary(response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict) or not isinstance(response.get("expiries"), list):
        return None
    expiries = _expiry_rows(response["expiries"], SURFACE_EXPIRY_KEYS)
    out: dict[str, Any] = {
        "underlying_id": response.get("underlying_id"),
        "spot": response.get("spot"),
        "method": response.get("method"),
        "forwards_source": response.get("forwards_source"),
        "filter_applied": response.get("filter"),
        "expiries_fitted": sum(1 for e in expiries if e.get("usable")),
        "expiries_total": len(expiries),
        "quotes_dropped_total": sum(int(e.get("quotes_dropped") or 0) for e in expiries),
        "expiries": expiries,
    }
    if isinstance(response.get("andreasen_huge_error"), dict):
        out["andreasen_huge_error"] = response["andreasen_huge_error"]
    rep = response.get("vanilla_repricing")
    if isinstance(rep, dict):
        out["vanilla_repricing"] = {k: rep[k] for k in REPRICING_KEYS if k in rep}
    grid_strikes = response.get("grid_strikes")
    grid_expiries = response.get("grid_expiries")
    out["grid"] = {
        "strikes": len(grid_strikes) if isinstance(grid_strikes, list) else None,
        "expiries": grid_expiries,
    }
    spec = response.get("vol_surface_spec")
    if isinstance(spec, dict):
        out["reuse"] = {
            "market.vol_surfaces": "response.vol_surface_spec",
            "id": spec.get("id"),
            "how": (
                f"add this VolSurfaceSpec to market.vol_surfaces and reference "
                f"{spec.get('id')!r} as a structured note underlying's volatility_id (a "
                "strike x expiry surface makes the note Dupire local vol: set "
                "monte_carlo.steps_per_year) or as price_equity_option vol"
            ),
        }
    return out


async def _calibrate(
    backend: Backend,
    store: ScratchStore,
    endpoint: str,
    market: Market,
    sheet: dict[str, Any],
    discounting_curve: str,
    fields: dict[str, Any],
    as_of: str | None,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
    summarize: Any,
) -> ToolResult:
    rid = request_id or new_request_id()
    try:
        if not isinstance(sheet, dict):
            raise problem("/sheet", "sheet must be an OptionQuoteSheet object")
        pricing, notes = mk.resolve_market(
            store, market, as_of, overrides_to_wire(calendar_overrides)
        )
        digest_notes, market_digests = market_notes(pricing)
        notes += digest_notes
        mk.require_curve(pricing, discounting_curve, "discounting_curve")
        underlying = sheet.get("underlying_id")
        if (
            isinstance(underlying, str)
            and mk.find_item(pricing, "equity_underlyings", underlying) is None
        ):
            raise problem(
                "/sheet/underlying_id",
                f"sheet.underlying_id {underlying!r} is not an EquityUnderlyingSpec of the "
                f"market (equity_underlyings: {mk.ids_in(pricing, 'equity_underlyings')}); add "
                "it with its spot quote id and a dividend_yield_curve_id that resolves",
            )
        quotes = sheet.get("quotes")
        notes.append(
            f"sheet {sheet.get('id')!r} sent as given "
            f"({len(quotes) if isinstance(quotes, list) else '?'} quote lines)"
        )
        notes.append(f"discounting_curve={discounting_curve!r} (argument)")
        body: dict[str, Any] = {
            "pricing": pricing,
            "sheet": copy.deepcopy(sheet),
            "discounting_curve": discounting_curve,
        }
        for k, v in fields.items():
            if v is None:
                notes.append(f"{k} absent: engine default (see the echoed response)")
                continue
            body[k] = copy.deepcopy(v)
            notes.append(f"{k} (argument) sent as given")
    except LocalValidationError as exc:
        return local_error_result(endpoint, None, exc.error, exc.problems, request_id=rid)
    problems = validate_request(endpoint, body)
    if problems:
        return local_error_result(
            endpoint,
            body,
            f"built request does not match the vendored schema for {endpoint} "
            f"({len(problems)} problem{'s' if len(problems) != 1 else ''}); nothing was sent",
            [p.as_dict() for p in problems],
            request_id=rid,
        )
    result = await run_post(backend, endpoint, body, request_id=rid, summarize=summarize)
    result["notes"] = notes
    if market_digests:
        result["market_digests"] = market_digests
    return result


async def calibrate_implied_forwards_impl(
    backend: Backend,
    store: ScratchStore,
    market: Market,
    sheet: dict[str, Any],
    discounting_curve: str,
    dividend_curve_id: str,
    quote_filter: dict[str, Any] | None,
    as_of: str | None,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
) -> ToolResult:
    return await _calibrate(
        backend,
        store,
        IMPLIED_FORWARDS,
        market,
        sheet,
        discounting_curve,
        {"dividend_curve_id": dividend_curve_id, "filter": quote_filter},
        as_of,
        calendar_overrides,
        request_id,
        implied_forwards_summary,
    )


async def calibrate_vol_surface_impl(
    backend: Backend,
    store: ScratchStore,
    market: Market,
    sheet: dict[str, Any],
    discounting_curve: str,
    vol_surface_id: str,
    method: str,
    quote_filter: dict[str, Any] | None,
    forwards: list[dict[str, Any]] | None,
    output_grid: dict[str, Any] | None,
    svi: dict[str, Any] | None,
    as_of: str | None,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
) -> ToolResult:
    return await _calibrate(
        backend,
        store,
        VOL_SURFACE,
        market,
        sheet,
        discounting_curve,
        {
            "vol_surface_id": vol_surface_id,
            "method": str(method),
            "filter": quote_filter,
            "forwards": forwards,
            "output_grid": output_grid,
            "svi": svi,
        },
        as_of,
        calendar_overrides,
        request_id,
        vol_surface_summary,
    )


MARKET_DESCRIPTION = (
    'The market: {"session": name}, an engine pricing block (anything with as_of_date; '
    "used verbatim) or {curves, quotes, equity_underlyings, ...}. Needs the discounting "
    "curve, the sheet's EquityUnderlyingSpec (id = sheet.underlying_id) with its spot quote "
    "and a dividend_yield_curve_id that resolves to a curve of the market (the engine's "
    "registry requires it; it is not read by the fit)."
)

SHEET_DESCRIPTION = (
    "The engine's OptionQuoteSheet, sent as given: {id, underlying_id, option_style "
    "(European), quotes: [{expiry YYYY-MM-DD, strike, call_bid, call_ask, put_bid, put_ask, "
    "call_volume?, put_volume?, call_open_interest?, put_open_interest?}]}. Listed option "
    "quotes are market data: from the user or a cited source, never typed from memory."
)

FILTER_DESCRIPTION = (
    "Optional QuoteFilter {max_relative_spread, min_quotes_per_expiry, moneyness_window, "
    "min_open_interest?}; absent = the engine's defaults, echoed in summary.filter_applied."
)

COMMON_OPTIONAL = {
    "as_of": (
        "Valuation date YYYY-MM-DD; required unless market is a pricing block, which carries "
        "its own."
    ),
    "calendar_overrides": (
        "Per-request holiday corrections: [{calendar, added_holidays?, removed_holidays?}]."
    ),
    "request_id": (
        "X-Request-Id to forward; generated when absent and reported in engine.request_id."
    ),
}


def register(app: MCPServer, backend: Backend, store: ScratchStore) -> None:
    @tool(app, "Implied forwards and dividends from option quotes", engine=True)
    async def calibrate_implied_forwards(
        market: Annotated[Market, Field(description=MARKET_DESCRIPTION)],
        market_data_source: Annotated[MarketDataSource, Field(description=SOURCE_DESCRIPTION)],
        sheet: Annotated[dict[str, Any], Field(description=SHEET_DESCRIPTION)],
        discounting_curve: Annotated[
            str,
            Field(
                description=(
                    "Curve id in the market giving the risk-free DF(T): the reference of the "
                    "implied funding spread and r(T) in the implied dividend yield."
                )
            ),
        ],
        dividend_curve_id: Annotated[
            str, Field(description="Id given to the returned dividend curve (a TermStructure).")
        ],
        filter: Annotated[dict[str, Any] | None, Field(description=FILTER_DESCRIPTION)] = None,
        as_of: Annotated[
            str | None,
            Field(description=COMMON_OPTIONAL["as_of"], pattern="^\\d{4}-\\d{2}-\\d{2}$"),
        ] = None,
        calendar_overrides: Annotated[
            list[CalendarOverride] | None, Field(description=COMMON_OPTIONAL["calendar_overrides"])
        ] = None,
        request_id: Annotated[str | None, Field(description=COMMON_OPTIONAL["request_id"])] = None,
        sources: Annotated[list[SourceRef] | None, Field(description=SOURCES_DESCRIPTION)] = None,
    ) -> ToolResult:
        """Implied forwards and dividend yields from listed European option quotes (POST
        /calibrate-implied-forwards): per expiry the engine filters the lines and fits
        put-call parity C - P = DF F - DF K by weighted least squares (two passes, the
        second inside the moneyness window), giving the implied forward, the implied
        discount factor (the index's funding), the funding spread against
        discounting_curve in bp, the continuous dividend yield and the fit residuals.
        ``summary``: per expiry forward / implied and curve DF / implied_funding_bp /
        implied_dividend_yield / residuals / quotes used and dropped with the engine's
        reasons counted, plus ``reuse``: the returned dividend curve as a market.curves
        item, directly reusable as the dividend curve of price_structured_note and
        price_equity_option (EquityUnderlyingSpec.dividend_yield_curve_id), and
        response.expiries as calibrate_vol_surface ``forwards``. Full response and
        request echoed. Method: explain_method('option-quotes-to-surface').
        """
        if (refusal := source_refusal(market_data_source, sources)) is not None:
            return refusal
        return stamp(
            await calibrate_implied_forwards_impl(
                backend,
                store,
                market,
                sheet,
                discounting_curve,
                dividend_curve_id,
                filter,
                as_of,
                calendar_overrides,
                request_id,
            ),
            market_data_source,
            sources,
        )

    @tool(app, "Black vol surface from option quotes", engine=True)
    async def calibrate_vol_surface(
        market: Annotated[Market, Field(description=MARKET_DESCRIPTION)],
        market_data_source: Annotated[MarketDataSource, Field(description=SOURCE_DESCRIPTION)],
        sheet: Annotated[dict[str, Any], Field(description=SHEET_DESCRIPTION)],
        discounting_curve: Annotated[
            str,
            Field(
                description=(
                    "Curve id in the market: the risk-free DFs the implied-forward fit reads "
                    "when forwards is absent."
                )
            ),
        ],
        vol_surface_id: Annotated[
            str, Field(description="Id given to the returned VolSurfaceSpec.")
        ],
        method: Annotated[
            VolSurfaceFitMethod,
            Field(
                description=(
                    "AndreasenHuge (QuantLib AndreasenHugeVolatilityInterpl, arbitrage-free "
                    "by construction) or SviPerExpiry (QuantLib SviInterpolation per expiry, "
                    "Durrleman butterfly and calendar checks). Explicit: no server default."
                )
            ),
        ],
        filter: Annotated[dict[str, Any] | None, Field(description=FILTER_DESCRIPTION)] = None,
        forwards: Annotated[
            list[dict[str, Any]] | None,
            Field(
                description=(
                    "Optional: the `expiries` array of a calibrate_implied_forwards response, "
                    "as is (expiry, usable, forward, implied_discount_factor are read); absent "
                    "= the engine runs the same fit here (forwards_source 'fitted')."
                )
            ),
        ] = None,
        output_grid: Annotated[
            dict[str, Any] | None,
            Field(
                description=(
                    "Optional VolSurfaceOutputGrid {strikes_as_moneyness?: [..], expiries?: "
                    "[{n, unit}]}; absent = the engine's default grid (0.50..1.50 x spot in 41 "
                    "points, every fitted sheet expiry)."
                )
            ),
        ] = None,
        svi: Annotated[
            dict[str, Any] | None,
            Field(
                description=(
                    "Optional SviFitOptions {enforce_butterfly?, enforce_calendar?} "
                    "(SviPerExpiry only)."
                )
            ),
        ] = None,
        as_of: Annotated[
            str | None,
            Field(description=COMMON_OPTIONAL["as_of"], pattern="^\\d{4}-\\d{2}-\\d{2}$"),
        ] = None,
        calendar_overrides: Annotated[
            list[CalendarOverride] | None, Field(description=COMMON_OPTIONAL["calendar_overrides"])
        ] = None,
        request_id: Annotated[str | None, Field(description=COMMON_OPTIONAL["request_id"])] = None,
        sources: Annotated[list[SourceRef] | None, Field(description=SOURCES_DESCRIPTION)] = None,
    ) -> ToolResult:
        """Black implied-vol surface from listed European option quotes (POST
        /calibrate-vol-surface): implied Black-76 vols of the out-of-the-money mids on the
        per-expiry forwards (pasted or fitted), then AndreasenHuge or SviPerExpiry, sampled
        on a moneyness x expiry grid. ``summary``: method, forwards_source, per expiry
        quotes used and dropped (engine reasons counted), rmse_vol / max_abs_error_vol,
        SVI parameters and the butterfly / calendar findings and actions, the vanilla
        repricing (count, inside_bid_ask, largest price and vol errors), plus ``reuse``:
        the returned VolSurfaceSpec (BlackVolSpec, SmileCube3D) as a market.vol_surfaces
        item, directly reusable as a price_structured_note underlying's volatility_id
        (Dupire local vol; needs monte_carlo.steps_per_year) or price_equity_option vol.
        Full response and request echoed. Method: explain_method('option-quotes-to-surface').
        """
        if (refusal := source_refusal(market_data_source, sources)) is not None:
            return refusal
        return stamp(
            await calibrate_vol_surface_impl(
                backend,
                store,
                market,
                sheet,
                discounting_curve,
                vol_surface_id,
                method,
                filter,
                forwards,
                output_grid,
                svi,
                as_of,
                calendar_overrides,
                request_id,
            ),
            market_data_source,
            sources,
        )
