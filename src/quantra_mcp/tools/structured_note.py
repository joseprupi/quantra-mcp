"""Tier 4: ``price_structured_note`` (engine >= 0.12.0, ``POST /price-structured-note``).

A thin tool: the notes are taken in the engine's own ``StructuredNote`` shape (no
preset, no convention defaults of this server: every field the engine needs is the
caller's), the market goes through the same ``market`` resolution as every other
pricing tool (``correlation_matrices`` -> ``pricing.correlations.matrices`` and
``fx_spots`` -> ``pricing.fx.spots`` in the market-object form), the request is
validated against the vendored spec, POSTed, echoed in full, and the summary is a
selection of the engine's response fields. Nothing is computed here.
"""

from __future__ import annotations

import copy
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from quantra_mcp.backend.base import Backend
from quantra_mcp.builders.products._common import BuiltTrade
from quantra_mcp.session import ScratchStore
from quantra_mcp.tools._dates import DateResolver
from quantra_mcp.tools._market_source import SOURCE_DESCRIPTION, MarketDataSource, stamp
from quantra_mcp.tools._meta import tool
from quantra_mcp.tools._result import ToolResult, local_error_result
from quantra_mcp.tools.calendar import CalendarOverride
from quantra_mcp.tools.pricing import Market, _price

STRUCTURED_NOTE = "/price-structured-note"

#: response fields copied into ``summary.notes[]`` (a selection, never a computation)
SUMMARY_KEYS = (
    "trade_id",
    "npv",
    "npv_pct_of_notional",
    "standard_error",
    "paths",
    "seed",
    "status",
    "legs",
    "p_barrier_breach",
    "expected_life_years",
    "event_probabilities",
    "greeks",
    "terms",
    "seasoning",
    "model",
)


def structured_note_summary(response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict) or not isinstance(response.get("notes"), list):
        return None
    return {
        "notes": [
            {k: n[k] for k in SUMMARY_KEYS if k in n}
            for n in response["notes"]
            if isinstance(n, dict)
        ]
    }


async def price_structured_note_impl(
    backend: Backend,
    store: ScratchStore,
    market: Market,
    notes: list[dict[str, Any]],
    greeks: dict[str, Any] | None,
    diagnostics: dict[str, Any] | None,
    as_of: str | None,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
) -> ToolResult:
    if not notes or not all(isinstance(n, dict) for n in notes):
        return local_error_result(
            STRUCTURED_NOTE,
            None,
            "notes must be a non-empty list of StructuredNote objects",
            [{"path": "/notes", "message": "at least one StructuredNote object"}],
            request_id=request_id,
        )

    async def build(pricing: dict[str, Any], r: DateResolver) -> list[BuiltTrade]:
        out: list[BuiltTrade] = []
        for i, n in enumerate(notes):
            out.append(
                BuiltTrade(
                    item=copy.deepcopy(n),
                    notes=[f"notes[{i}] (trade_id {n.get('trade_id')!r}) sent as given"],
                )
            )
        return out

    top: dict[str, Any] = {}
    if greeks is not None:
        top["greeks"] = greeks
    if diagnostics is not None:
        top["diagnostics"] = diagnostics
    return await _price(
        backend,
        store,
        STRUCTURED_NOTE,
        "notes",
        market,
        as_of,
        calendar_overrides,
        request_id,
        build,
        structured_note_summary,
        top_level=top,
    )


def register(app: MCPServer, backend: Backend, store: ScratchStore) -> None:
    @tool(app, "Price a structured note (Monte Carlo)", engine=True)
    async def price_structured_note(
        market: Annotated[
            Market,
            Field(
                description=(
                    'The market: {"session": name}, an engine pricing block (anything with '
                    "as_of_date; used verbatim) or {curves, quotes, vol_surfaces, "
                    "equity_underlyings, fx_spots, correlation_matrices, ...}. Needs the note "
                    "currency discount curve, per underlying a spot quote, a dividend curve, an "
                    "EquityUnderlyingSpec with currency and a BlackVolSpec surface; a "
                    "correlation matrix for two or more underlyings; FX spot, FX vol and the "
                    "underlying-currency curve for a quanto underlying."
                )
            ),
        ],
        market_data_source: Annotated[MarketDataSource, Field(description=SOURCE_DESCRIPTION)],
        notes: Annotated[
            list[dict[str, Any]],
            Field(
                description=(
                    "One or more notes in the engine's StructuredNote shape (trade_id, currency, "
                    "notional, underlyings, performance_rule, events, coupon_rule, capital, "
                    "maturity_payment_date, discounting_curve, funding_spread_bp, "
                    "correlation_id, upside, monte_carlo); see engine_schema "
                    "/price-structured-note. Sent as given."
                ),
                min_length=1,
            ),
        ],
        greeks: Annotated[
            dict[str, Any] | None,
            Field(
                description=(
                    "Optional StructuredNoteGreeksSpec ({} = engine defaults, or spot_bump, "
                    "vol_bump, correlation_bump, funding_bump_bp, rate_bump_bp): bump-and-"
                    "reprice on common random numbers; costs 1 + 3N + 3 simulations for N "
                    "underlyings, size the paths to the engine deadline."
                )
            ),
        ] = None,
        diagnostics: Annotated[
            dict[str, Any] | None,
            Field(
                description=(
                    "Optional StructuredNoteDiagnosticsSpec: market, decomposition, checks, "
                    "distribution, timing (booleans) and sample_paths (<= 100, with "
                    "distribution)."
                )
            ),
        ] = None,
        as_of: Annotated[
            str | None,
            Field(
                description=(
                    "Valuation date YYYY-MM-DD; required unless market is a pricing block, "
                    "which carries its own."
                ),
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        calendar_overrides: Annotated[
            list[CalendarOverride] | None,
            Field(
                description="Per-request holiday corrections: [{calendar, added_holidays?, "
                "removed_holidays?}]."
            ),
        ] = None,
        request_id: Annotated[
            str | None,
            Field(
                description=(
                    "X-Request-Id to forward; generated when absent and reported in "
                    "engine.request_id."
                )
            ),
        ] = None,
    ) -> ToolResult:
        """Price structured notes by Monte Carlo (POST /price-structured-note): autocallables,
        Phoenix, reverse convertibles, bonus / twin-win shapes as configurations of one payoff
        on an equity-index basket (worst-of / best-of / average), quanto underlyings,
        seasoning from historical levels. The notes are sent exactly as given (no defaults of
        this server); the full request is echoed. ``summary.notes[]``: npv,
        npv_pct_of_notional, standard_error, paths, seed, status, legs, p_barrier_breach,
        expected_life_years, event_probabilities, greeks (when requested) and the engine's
        terms / seasoning / model echoes (check ``terms`` before trusting a price). The
        diagnostic blocks stay in ``response``. Method: explain_method('structured-notes').
        """
        return stamp(
            await price_structured_note_impl(
                backend,
                store,
                market,
                notes,
                greeks,
                diagnostics,
                as_of,
                calendar_overrides,
                request_id,
            ),
            market_data_source,
        )
