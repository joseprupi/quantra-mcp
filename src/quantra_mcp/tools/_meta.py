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


#: descriptions of the nested model fields (trade blocks, overrides, bumps, grids) by
#: field name; applied to every ``$defs`` property that has none of its own. Top-level
#: tool arguments carry their own ``Field(description=...)`` and never use this.
NESTED_DESCRIPTIONS: dict[str, str] = {
    "accrual_day_counter": "Accrual day counter (engine DayCounter).",
    "amount": "Cash amount.",
    "apply_observation_shift": "Observation shift with the lookback (overnight leg).",
    "averaging_method": "Compound or Simple averaging of the overnight rate.",
    "binomial_steps": "Steps of the binomial tree (BinomialCRR).",
    "bp": "Bump in basis points added to the quote(s); a futures price moves by -bp/100.",
    "bumps": "[{curve, bp, pillar?}]; without pillar every pillar of that curve moves.",
    "business_day_convention": "Engine BusinessDayConvention.",
    "business_days_only": "Only business days on the grid.",
    "calendar": "Engine Calendar.",
    "call_schedule": "[{date, price, type: Call|Put}], increasing dates; clean price per 100.",
    "cap_floor_type": "Cap, Floor or Collar.",
    "cap_rate": "Cap strike, decimal.",
    "cash_settlement_days": "Cash settlement days.",
    "compounding": "Engine Compounding (Continuous, Simple, Compounded, ...).",
    "constant": "The constant volatility as a decimal (0.2 = 20%; normal vols in rate units).",
    "convention": "Engine BusinessDayConvention of the schedule.",
    "coupon_rate": "Annual coupon rate as a decimal.",
    "curve": "Curve id in the market.",
    "date": "Date YYYY-MM-DD.",
    "date_generation_rule": "Engine DateGenerationRule (Backward, Forward, Zero, ...).",
    "day_counter": "Engine DayCounter.",
    "discounting_curve": "Id of the discounting curve in the market.",
    "displacement": "Shift for ShiftedLognormal vols, decimal.",
    "effective_date": "YYYY-MM-DD, or 'spot' = as_of (or exercise) + the preset's settlement days.",
    "end_date": "End date YYYY-MM-DD.",
    "end_of_month": "Apply the end-of-month rule.",
    "ex_date": "Ex-dividend date YYYY-MM-DD.",
    "exercise_type": "European, Bermudan or American.",
    "expiries": "Expiry tenors of the matrix rows, e.g. ['1M', '1Y', '5Y'].",
    "face_amount": "Face amount, > 0.",
    "fixed_leg_overrides": "Overrides of the preset's fixed leg conventions.",
    "fixed_rate": "Fixed rate as a decimal.",
    "fixing_days": "Index fixing days.",
    "floating_leg_overrides": "Overrides of the preset's floating leg conventions.",
    "floor_rate": "Floor strike, decimal.",
    "forward_type": "Period (tenor forwards) or Instantaneous (eps forwards).",
    "forwarding_curve": "Id of the curve projecting the floating index.",
    "frequency": "Engine Frequency (Annual, Semiannual, Quarterly, ...).",
    "gearing": "Gearing on the YoY rate.",
    "hazard_rate": "Flat hazard rate, decimal.",
    "id": "Id under which the object is added to the market (default noted in the result).",
    "in_arrears": "Fix in arrears.",
    "include_details": "Per-item breakdown in the response.",
    "index_id": "Id of the index in the market; default: the preset's.",
    "instantaneous_eps_number": "Instantaneous forward epsilon, number of units.",
    "instantaneous_eps_time_unit": "Instantaneous forward epsilon, engine TimeUnit.",
    "issue_date": "Issue date YYYY-MM-DD, or 'spot' = as_of + the preset's settlement days.",
    "lockout_days": "Rate lockout in days (overnight leg).",
    "lookback_days": "Observation lookback in days (overnight leg).",
    "maturity_date": "Maturity YYYY-MM-DD; alternative to tenor.",
    "months_to_end": "Months from spot to the FRA end (6 for a 3x6).",
    "months_to_start": "Months from spot to the FRA start (3 for a 3x6).",
    "name": "Scenario name (unique, not 'base').",
    "notional": "Notional amount, > 0.",
    "notionals": "Per-period notionals (amortising); default: the face amount throughout.",
    "option_type": "Call or Put.",
    "overnight_leg_overrides": "Overrides of the preset's overnight leg conventions.",
    "overrides": "Overrides of the preset's bond conventions.",
    "par_spreads": "[{tenor, spread}] CDS par spreads, decimal, bootstrapped by the engine.",
    "payload": "The raw engine vol-surface payload object.",
    "payload_type": "The engine payload type of the raw surface.",
    "payment_convention": "Engine BusinessDayConvention of the payments.",
    "payment_lag": "Payment lag in business days (overnight leg).",
    "pillar": "Pillar label ('5Y', '3x6', a value-point date) or 0-based index.",
    "quantity": "Number of options.",
    "rate": "Rate as a decimal.",
    "recovery_rate": "Recovery rate, decimal (0.4 = 40%).",
    "redemption": "Redemption per 100 of face.",
    "replace_quotes": "[{curve, pillar, value}]: set a pillar's quote to an explicit value.",
    "schedule": "Overrides of the preset's schedule rules.",
    "schedule_overrides": "Overrides of the preset's schedule rules.",
    "settlement_days": "Settlement days.",
    "settlement_type": "Physical or Cash.",
    "side": "Side of the trade (Buyer / Seller, Long / Short).",
    "spread": "Spread as a decimal.",
    "start_date": "Start date YYYY-MM-DD.",
    "step_number": "Grid step, number of units.",
    "step_time_unit": "Grid step, engine TimeUnit.",
    "strike": "Strike as a decimal (or price for equity).",
    "swap_type": "Payer (pay fixed) or Receiver.",
    "telescopic_value_dates": "Telescopic value dates on the overnight leg.",
    "tenor": "Tenor, e.g. 5Y (engine-resolved); alternative to the explicit end date.",
    "tenors": "Swap tenors of the matrix columns, e.g. ['2Y', '5Y', '10Y'].",
    "termination_date": "Maturity YYYY-MM-DD; alternative to tenor.",
    "termination_date_convention": "Engine BusinessDayConvention of the termination date.",
    "trade_id": "Id of the trade in the request.",
    "tree_steps": "Engine lattice steps for the bond.",
    "type": (
        "Vol type (Lognormal, Normal, ShiftedLognormal; Black, Bachelier, UnitDisplacedBlack "
        "for YoY) or Call / Put for a callability."
    ),
    "upfront": "Upfront payment as a decimal of notional.",
    "upfront_date": "Upfront payment date YYYY-MM-DD.",
    "use_curve_day_counter": "Use the curve's day counter for the zero rates.",
    "use_grid_calendar_for_advance": "Roll the grid dates with the grid calendar.",
    "value": "The value (quote, rate, discount factor or forward as the kind says).",
    "yield_overrides": "How the yield is quoted back: day_counter, compounding, frequency.",
}


def describe_nested(schema: Any) -> Any:
    """Give every ``$defs`` property without a description the one its field name has."""
    if not isinstance(schema, dict):
        return schema
    defs = schema.get("$defs")
    if isinstance(defs, dict):
        for model in defs.values():
            props = model.get("properties") if isinstance(model, dict) else None
            if not isinstance(props, dict):
                continue
            for field_name, prop in props.items():
                if isinstance(prop, dict) and "description" not in prop:
                    text = NESTED_DESCRIPTIONS.get(field_name)
                    if text is not None:
                        prop["description"] = text
    return schema


def finish_schemas(app: MCPServer) -> None:
    """Rewrite every registered tool's input schema: no pydantic titles, every nested
    field described."""
    for registered in app._tool_manager.list_tools():
        registered.parameters = describe_nested(strip_titles(registered.parameters))
