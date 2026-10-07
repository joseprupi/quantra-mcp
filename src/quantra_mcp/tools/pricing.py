"""Tier 4: pricing convenience tools, one per product.

Every tool: takes a ``market`` (``{"session": name}`` | an engine ``pricing``
block | ``{curves, indices, ...}`` from the M2 builders), a ``preset`` whose
trade block supplies every convention, and the trade economics; builds the
engine request (dates the server cannot compute are resolved by ONE engine
``/calendar-advance`` call each, see ``_dates``), validates it against the
vendored spec, POSTs it and returns the uniform result plus ``notes`` (every
default and its source), ``date_resolution`` (the calendar calls made) and a
per-item ``summary`` selected from the engine's response. Nothing is computed
here. ``additional_trades`` batches more trades of the same product into the
same request (the engine prices a list and answers per item).
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.mcpserver import MCPServer

from quantra_mcp.backend.base import Backend
from quantra_mcp.builders import market as mk
from quantra_mcp.builders.products import bonds as pb
from quantra_mcp.builders.products import cds as pc
from quantra_mcp.builders.products import equity as pe
from quantra_mcp.builders.products import inflation as pi
from quantra_mcp.builders.products import rates_options as pr
from quantra_mcp.builders.products import swaps as ps
from quantra_mcp.builders.products import swaption as pw
from quantra_mcp.builders.products._common import (
    AtmMatrixVol,
    BuiltTrade,
    ConstantVol,
    FixedLegOverrides,
    FloatingLegOverrides,
    HullWhiteModel,
    RawSwaptionVol,
    ScheduleOverrides,
)
from quantra_mcp.builders.schedule import check_date, parse_end, parse_start, problem
from quantra_mcp.errors import LocalValidationError
from quantra_mcp.presets.registry import (
    CallableBondConventions,
    CapFloorConventions,
    CdsConventions,
    FixedRateBondConventions,
    FloatingRateBondConventions,
    FraTradeConventions,
    OisSwapConventions,
    Preset,
    PresetError,
    SwaptionConventions,
    VanillaSwapConventions,
    YoyInflationCapFloorConventions,
    YoyInflationSwapConventions,
    ZcInflationSwapConventions,
    ZeroCouponBondConventions,
    get_preset,
)
from quantra_mcp.schema.enums_generated import (
    BusinessDayConvention,
    CapFloorType,
    DayCounter,
    EquityOptionType,
    ExerciseType,
    FRAType,
    Frequency,
    ProtectionSide,
    RateAveragingType,
    SettlementMethod,
    SettlementType,
    SwapType,
)
from quantra_mcp.schema.validate import validate_request
from quantra_mcp.session import SessionStore
from quantra_mcp.tools._dates import DateResolutionFailed, DateResolver
from quantra_mcp.tools._market_source import MarketDataSource, stamp
from quantra_mcp.tools._result import ToolResult, local_error_result, new_request_id, run_post
from quantra_mcp.tools.calendar import CalendarOverride, overrides_to_wire

CALIBRATE_SWAPTION_VOL = "/calibrate-swaption-vol"
CALIBRATE_SWAPTION_MODEL = "/calibrate-swaption-model"
SAMPLE_VOL_SURFACES = "/sample-vol-surfaces"

Builder = Callable[[dict[str, Any], DateResolver], Awaitable[list[BuiltTrade]]]
Summarizer = Callable[[Any], dict[str, Any] | None]

Market = dict[str, Any]


# --------------------------------------------------------------------------
# the shared pipeline
# --------------------------------------------------------------------------


async def _price(
    backend: Backend,
    store: SessionStore,
    endpoint: str,
    list_key: str,
    market: Market,
    as_of: str | None,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
    build: Builder,
    summarize: Summarizer,
    top_level: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> ToolResult:
    rid = request_id or new_request_id()
    try:
        pricing, notes = mk.resolve_market(
            store, market, as_of, overrides_to_wire(calendar_overrides)
        )
        resolver = DateResolver(backend, pricing.get("calendar_overrides"))
        built = await build(pricing, resolver)
        items: list[dict[str, Any]] = []
        for b in built:
            for section, item, what in b.additions:
                mk.add_item(pricing, section, item, b.notes, what)
            items.append(b.item)
            notes += b.notes
        notes += resolver.notes
        if options:
            opts = pricing.setdefault("options", {})
            for k, v in options.items():
                if opts.get(k) not in (None, v):
                    raise problem(f"/{k}", f"market.options.{k} conflicts with the argument")
                opts[k] = v
                notes.append(f"pricing.options.{k}={v!r} (argument)")
        body: dict[str, Any] = {"pricing": pricing, list_key: items}
        for k, v in (top_level or {}).items():
            body[k] = v
            notes.append(f"{k}={v!r} (argument)")
    except DateResolutionFailed as exc:
        out = dict(exc.result)
        out["error"] = f"{exc.label}: {out.get('error')} (resolving a date via /calendar-advance)"
        out["engine"] = {**out.get("engine", {}), "request_id": rid}
        return out
    except PresetError as exc:
        return local_error_result(endpoint, None, str(exc), request_id=rid)
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
    if resolver.calls:
        result["date_resolution"] = resolver.calls
    return result


def _index_for(
    preset: Preset, pricing: dict[str, Any], index_id: str | None, notes: list[str]
) -> str:
    if index_id is not None:
        notes.append(f"index={index_id!r} (explicit argument)")
    elif preset.index is not None:
        index_id = preset.index.id
        notes.append(f"index={index_id!r} from preset {preset.id} index.id")
    else:
        raise problem(
            "/index_id",
            f"preset {preset.id} has no index; pass index_id (an IndexDef id in the market)",
        )
    mk.require_index(pricing, index_id, "index_id")
    return index_id


def _curves(pricing: dict[str, Any], discounting: str, forwarding: str | None) -> None:
    mk.require_curve(pricing, discounting, "discounting_curve")
    if forwarding is not None:
        mk.require_curve(pricing, forwarding, "forwarding_curve")


async def _swap_dates(
    r: DateResolver,
    preset: Preset,
    settlement_days: int,
    calendar: str,
    source: str,
    as_of: str,
    effective_date: str,
    termination_date: str | None,
    tenor: str | None,
    i: int,
) -> tuple[str, str]:
    tag = f"trades[{i}]." if i else ""
    eff = await r.start(
        f"{tag}effective_date",
        parse_start(effective_date),
        as_of,
        settlement_days,
        calendar,
        source,
    )
    end = await r.end(f"{tag}termination_date", parse_end(termination_date, tenor), eff, calendar)
    return eff, end


def _src(preset: Preset, dotted: str) -> str:
    return f"preset {preset.id} {dotted}"


# --------------------------------------------------------------------------
# tool implementations (one per product)
# --------------------------------------------------------------------------


async def price_vanilla_swap_impl(
    backend: Backend,
    store: SessionStore,
    market: Market,
    preset: str,
    trades: list[ps.VanillaSwapTrade],
    discounting_curve: str,
    forwarding_curve: str,
    as_of: str | None,
    include_flows: bool,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
) -> ToolResult:
    async def build(pricing: dict[str, Any], r: DateResolver) -> list[BuiltTrade]:
        p = get_preset(preset)
        conv = p.trade_block("vanilla_swap")
        assert isinstance(conv, VanillaSwapConventions)
        _curves(pricing, discounting_curve, forwarding_curve)
        out: list[BuiltTrade] = []
        for i, t in enumerate(trades):
            notes: list[str] = []
            index_id = _index_for(p, pricing, t.index_id, notes)
            eff, end = await _swap_dates(
                r,
                p,
                conv.settlement_days,
                str(conv.schedule.calendar),
                _src(p, "trades.vanilla_swap.settlement_days"),
                pricing["as_of_date"],
                t.effective_date,
                t.termination_date,
                t.tenor,
                i,
            )
            b = ps.build_vanilla_swap(p, t, eff, end, index_id, discounting_curve, forwarding_curve)
            b.notes = notes + b.notes
            out.append(b)
        return out

    return await _price(
        backend,
        store,
        ps.VANILLA_SWAP,
        "swaps",
        market,
        as_of,
        calendar_overrides,
        request_id,
        build,
        ps.swap_summary,
        {"include_flows": include_flows} if include_flows else None,
    )


async def price_ois_swap_impl(
    backend: Backend,
    store: SessionStore,
    market: Market,
    preset: str,
    trades: list[ps.OisSwapTrade],
    discounting_curve: str,
    forwarding_curve: str,
    as_of: str | None,
    include_flows: bool,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
) -> ToolResult:
    async def build(pricing: dict[str, Any], r: DateResolver) -> list[BuiltTrade]:
        p = get_preset(preset)
        conv = p.trade_block("ois_swap")
        assert isinstance(conv, OisSwapConventions)
        _curves(pricing, discounting_curve, forwarding_curve)
        out: list[BuiltTrade] = []
        for i, t in enumerate(trades):
            notes: list[str] = []
            index_id = _index_for(p, pricing, t.index_id, notes)
            eff, end = await _swap_dates(
                r,
                p,
                conv.settlement_days,
                str(conv.schedule.calendar),
                _src(p, "trades.ois_swap.settlement_days"),
                pricing["as_of_date"],
                t.effective_date,
                t.termination_date,
                t.tenor,
                i,
            )
            b = ps.build_ois_swap(p, t, eff, end, index_id, discounting_curve, forwarding_curve)
            b.notes = notes + b.notes
            out.append(b)
        return out

    return await _price(
        backend,
        store,
        ps.OIS_SWAP,
        "swaps",
        market,
        as_of,
        calendar_overrides,
        request_id,
        build,
        ps.swap_summary,
        {"include_flows": include_flows} if include_flows else None,
    )


async def _bond_dates(
    r: DateResolver,
    p: Preset,
    dotted: str,
    settlement_days: int,
    calendar: str,
    as_of: str,
    t: Any,
    i: int,
) -> tuple[str, str, str]:
    tag = f"trades[{i}]." if i else ""
    issue = await r.start(
        f"{tag}issue_date",
        parse_start(t.issue_date, "issue_date"),
        as_of,
        settlement_days,
        calendar,
        _src(p, f"{dotted}.settlement_days"),
    )
    if t.effective_date is not None:
        eff = check_date(t.effective_date, "effective_date")
        r.notes.append(f"{tag}effective_date={eff!r} (explicit)")
    else:
        eff = issue
        r.notes.append(f"{tag}effective_date={eff!r} (default: = issue_date)")
    end = await r.end(
        f"{tag}maturity_date", parse_end(t.maturity_date, t.tenor, "maturity_date"), eff, calendar
    )
    return issue, eff, end


def _bond_options(include_details: bool, include_flows: bool) -> dict[str, Any] | None:
    opts: dict[str, Any] = {}
    if include_details:
        opts["bond_pricing_details"] = True
    if include_flows:
        opts["bond_pricing_flows"] = True
    return opts or None


async def price_fixed_rate_bond_impl(
    backend: Backend,
    store: SessionStore,
    market: Market,
    preset: str,
    trades: list[pb.FixedRateBondTrade],
    discounting_curve: str,
    as_of: str | None,
    include_details: bool,
    include_flows: bool,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
) -> ToolResult:
    async def build(pricing: dict[str, Any], r: DateResolver) -> list[BuiltTrade]:
        p = get_preset(preset)
        conv = p.trade_block("fixed_rate_bond")
        assert isinstance(conv, FixedRateBondConventions)
        _curves(pricing, discounting_curve, None)
        out: list[BuiltTrade] = []
        for i, t in enumerate(trades):
            issue, eff, end = await _bond_dates(
                r,
                p,
                "trades.fixed_rate_bond",
                conv.settlement_days,
                str(conv.schedule.calendar),
                pricing["as_of_date"],
                t,
                i,
            )
            out.append(pb.build_fixed_rate_bond(p, t, issue, eff, end, discounting_curve))
        return out

    return await _price(
        backend,
        store,
        pb.FIXED_RATE_BOND,
        "bonds",
        market,
        as_of,
        calendar_overrides,
        request_id,
        build,
        pb.bond_summary,
        None,
        _bond_options(include_details, include_flows),
    )


async def price_floating_rate_bond_impl(
    backend: Backend,
    store: SessionStore,
    market: Market,
    preset: str,
    trades: list[pb.FloatingRateBondTrade],
    discounting_curve: str,
    forwarding_curve: str,
    coupon_pricer: str | None,
    as_of: str | None,
    include_details: bool,
    include_flows: bool,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
) -> ToolResult:
    async def build(pricing: dict[str, Any], r: DateResolver) -> list[BuiltTrade]:
        p = get_preset(preset)
        conv = p.trade_block("floating_rate_bond")
        assert isinstance(conv, FloatingRateBondConventions)
        _curves(pricing, discounting_curve, forwarding_curve)
        if coupon_pricer is None and mk.ids_in(pricing, "coupon_pricers"):
            raise problem(
                "/coupon_pricer",
                f"the market already has coupon pricers "
                f"{mk.ids_in(pricing, 'coupon_pricers')}; pass coupon_pricer=<id>",
            )
        if (
            coupon_pricer is not None
            and mk.find_item(pricing, "coupon_pricers", coupon_pricer) is None
        ):
            raise problem(
                "/coupon_pricer",
                f"coupon_pricer {coupon_pricer!r} is not in pricing.rates.coupon_pricers",
            )
        out: list[BuiltTrade] = []
        for i, t in enumerate(trades):
            notes: list[str] = []
            index_id = _index_for(p, pricing, t.index_id, notes)
            issue, eff, end = await _bond_dates(
                r,
                p,
                "trades.floating_rate_bond",
                conv.settlement_days,
                str(conv.schedule.calendar),
                pricing["as_of_date"],
                t,
                i,
            )
            b = pb.build_floating_rate_bond(
                p,
                t,
                issue,
                eff,
                end,
                index_id,
                discounting_curve,
                forwarding_curve,
                coupon_pricer,
                pricing["as_of_date"],
            )
            b.notes = notes + b.notes
            out.append(b)
        return out

    return await _price(
        backend,
        store,
        pb.FLOATING_RATE_BOND,
        "bonds",
        market,
        as_of,
        calendar_overrides,
        request_id,
        build,
        pb.bond_summary,
        None,
        _bond_options(include_details, include_flows),
    )


async def price_zero_coupon_bond_impl(
    backend: Backend,
    store: SessionStore,
    market: Market,
    preset: str,
    trades: list[pb.ZeroCouponBondTrade],
    discounting_curve: str,
    as_of: str | None,
    include_details: bool,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
) -> ToolResult:
    async def build(pricing: dict[str, Any], r: DateResolver) -> list[BuiltTrade]:
        p = get_preset(preset)
        conv = p.trade_block("zero_coupon_bond")
        assert isinstance(conv, ZeroCouponBondConventions)
        _curves(pricing, discounting_curve, None)
        as_of_date = pricing["as_of_date"]
        out: list[BuiltTrade] = []
        for i, t in enumerate(trades):
            tag = f"trades[{i}]." if i else ""
            issue: str | None
            if t.issue_date is None:
                issue = None
            elif t.issue_date.strip().lower() == "as_of":
                issue = as_of_date
                r.notes.append(f"{tag}issue_date={issue!r} (= as_of)")
            else:
                issue = check_date(t.issue_date, "issue_date")
                r.notes.append(f"{tag}issue_date={issue!r} (explicit)")
            anchor = issue or as_of_date
            end = await r.end(
                f"{tag}maturity_date",
                parse_end(t.maturity_date, t.tenor, "maturity_date"),
                anchor,
                str(conv.calendar),
            )
            if t.tenor is not None:
                r.notes.append(
                    f"{tag}maturity_date: tenor counted from "
                    f"{'issue_date' if issue else 'as_of'} {anchor}"
                )
            out.append(pb.build_zero_coupon_bond(p, t, end, issue, discounting_curve))
        return out

    return await _price(
        backend,
        store,
        pb.ZERO_COUPON_BOND,
        "bonds",
        market,
        as_of,
        calendar_overrides,
        request_id,
        build,
        pb.bond_summary,
        None,
        _bond_options(include_details, False),
    )


async def price_callable_fixed_rate_bond_impl(
    backend: Backend,
    store: SessionStore,
    market: Market,
    preset: str,
    trades: list[pb.CallableBondTrade],
    discounting_curve: str,
    model: str | HullWhiteModel,
    as_of: str | None,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
) -> ToolResult:
    async def build(pricing: dict[str, Any], r: DateResolver) -> list[BuiltTrade]:
        p = get_preset(preset)
        conv = p.trade_block("callable_fixed_rate_bond")
        assert isinstance(conv, CallableBondConventions)
        _curves(pricing, discounting_curve, None)
        if isinstance(model, str) and mk.find_item(pricing, "models", model) is None:
            raise problem(
                "/model",
                f"model {model!r} is not in pricing.volatility.models; "
                "pass {a, sigma} to build one",
            )
        out: list[BuiltTrade] = []
        for i, t in enumerate(trades):
            pb.check_callables_sorted(t.call_schedule)
            issue, eff, end = await _bond_dates(
                r,
                p,
                "trades.callable_fixed_rate_bond",
                conv.settlement_days,
                str(conv.schedule.calendar),
                pricing["as_of_date"],
                t,
                i,
            )
            out.append(pb.build_callable_bond(p, t, issue, eff, end, discounting_curve, model))
        return out

    return await _price(
        backend,
        store,
        pb.CALLABLE_FIXED_RATE_BOND,
        "bonds",
        market,
        as_of,
        calendar_overrides,
        request_id,
        build,
        pb.bond_summary,
    )


async def price_fra_impl(
    backend: Backend,
    store: SessionStore,
    market: Market,
    preset: str,
    trades: list[pr.FraTrade],
    discounting_curve: str,
    forwarding_curve: str,
    as_of: str | None,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
) -> ToolResult:
    async def build(pricing: dict[str, Any], r: DateResolver) -> list[BuiltTrade]:
        p = get_preset(preset)
        conv = p.trade_block("fra")
        assert isinstance(conv, FraTradeConventions)
        _curves(pricing, discounting_curve, forwarding_curve)
        as_of_date = pricing["as_of_date"]
        out: list[BuiltTrade] = []
        for i, t in enumerate(trades):
            tag = f"trades[{i}]." if i else ""
            pr.check_fra_dates(t)
            notes: list[str] = []
            index_id = _index_for(p, pricing, t.index_id, notes)
            cal = str(t.calendar or conv.calendar)
            bdc = str(t.business_day_convention or conv.business_day_convention)
            if t.months_to_start is not None and t.months_to_end is not None:
                spot = await r.spot(
                    f"{tag}spot",
                    as_of_date,
                    conv.settlement_days,
                    cal,
                    _src(p, "trades.fra.settlement_days"),
                )
                start = await r.advance(
                    f"{tag}start_date", cal, spot, t.months_to_start, "Months", bdc
                )
                end = await r.advance(
                    f"{tag}maturity_date", cal, spot, t.months_to_end, "Months", bdc
                )
            else:
                assert t.start_date is not None and t.maturity_date is not None
                start, end = (
                    check_date(t.start_date, "start_date"),
                    check_date(t.maturity_date, "maturity_date"),
                )
                r.notes.append(f"{tag}start_date={start!r}, maturity_date={end!r} (explicit)")
            b = pr.build_fra(p, t, start, end, index_id, discounting_curve, forwarding_curve)
            b.notes = notes + b.notes
            out.append(b)
        return out

    return await _price(
        backend,
        store,
        pr.FRA,
        "fras",
        market,
        as_of,
        calendar_overrides,
        request_id,
        build,
        pr.fra_summary,
    )


async def price_cap_floor_impl(
    backend: Backend,
    store: SessionStore,
    market: Market,
    preset: str,
    trades: list[pr.CapFloorTrade],
    discounting_curve: str,
    forwarding_curve: str,
    vol: str | ConstantVol,
    model: str,
    as_of: str | None,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
) -> ToolResult:
    async def build(pricing: dict[str, Any], r: DateResolver) -> list[BuiltTrade]:
        p = get_preset(preset)
        conv = p.trade_block("cap_floor")
        assert isinstance(conv, CapFloorConventions)
        _curves(pricing, discounting_curve, forwarding_curve)
        _require_ref(pricing, "vol_surfaces", vol, "vol")
        out: list[BuiltTrade] = []
        for i, t in enumerate(trades):
            notes: list[str] = []
            index_id = _index_for(p, pricing, t.index_id, notes)
            eff, end = await _swap_dates(
                r,
                p,
                conv.settlement_days,
                str(conv.schedule.calendar),
                _src(p, "trades.cap_floor.settlement_days"),
                pricing["as_of_date"],
                t.effective_date,
                t.termination_date,
                t.tenor,
                i,
            )
            b = pr.build_cap_floor(
                p,
                t,
                eff,
                end,
                index_id,
                discounting_curve,
                forwarding_curve,
                vol,
                model,
                pricing["as_of_date"],
            )
            b.notes = notes + b.notes
            out.append(b)
        _require_model(pricing, out)
        return out

    return await _price(
        backend,
        store,
        pr.CAP_FLOOR,
        "cap_floors",
        market,
        as_of,
        calendar_overrides,
        request_id,
        build,
        pr.cap_floor_summary,
    )


def _require_ref(pricing: dict[str, Any], section: str, ref: Any, role: str) -> None:
    if isinstance(ref, str) and mk.find_item(pricing, section, ref) is None:
        raise problem(
            f"/{role}", f"{role} {ref!r} is not in pricing {section}: {mk.ids_in(pricing, section)}"
        )


def _require_model(pricing: dict[str, Any], built: list[BuiltTrade]) -> None:
    for b in built:
        model_id = b.item.get("model")
        if model_id is None:
            continue
        added = any(s == "models" and it.get("id") == model_id for s, it, _ in b.additions)
        if not added and mk.find_item(pricing, "models", str(model_id)) is None:
            raise problem(
                "/model",
                f"model {model_id!r} is neither a model type nor a model id in "
                f"pricing.volatility.models: {mk.ids_in(pricing, 'models')}",
            )


async def price_swaption_impl(
    backend: Backend,
    store: SessionStore,
    market: Market,
    preset: str,
    trades: list[pw.SwaptionTrade],
    discounting_curve: str,
    forwarding_curve: str,
    vol: str | ConstantVol | AtmMatrixVol | RawSwaptionVol,
    model: str | HullWhiteModel,
    as_of: str | None,
    include_details: bool,
    include_diagnostics: bool,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
) -> ToolResult:
    async def build(pricing: dict[str, Any], r: DateResolver) -> list[BuiltTrade]:
        p = get_preset(preset)
        conv = p.trade_block("swaption")
        assert isinstance(conv, SwaptionConventions)
        _curves(pricing, discounting_curve, forwarding_curve)
        _require_ref(pricing, "vol_surfaces", vol, "vol")
        out: list[BuiltTrade] = []
        for i, t in enumerate(trades):
            tag = f"trades[{i}]." if i else ""
            pw.check_exercise(t)
            notes: list[str] = []
            u = t.underlying
            index_id = _index_for(p, pricing, u.index_id, notes)
            swap_block = p.trade_block(
                "ois_swap" if t.underlying_type == "OisSwap" else "vanilla_swap"
            )
            assert isinstance(swap_block, VanillaSwapConventions | OisSwapConventions)
            cal = str(swap_block.schedule.calendar)
            start = parse_start(u.effective_date)
            if start.spot:
                if t.exercise_type != ExerciseType.European or t.exercise_date is None:
                    raise problem(
                        "/underlying/effective_date",
                        "'spot' for the underlying needs a European exercise_date; give an "
                        "explicit effective_date otherwise",
                    )
                eff = await r.advance(
                    f"{tag}underlying.effective_date",
                    cal,
                    t.exercise_date,
                    conv.settlement_days,
                    "Days",
                    "Following",
                )
                r.notes.append(
                    f"{tag}underlying.effective_date: 'spot' = exercise_date + settlement_days "
                    f"{conv.settlement_days} from {_src(p, 'trades.swaption.settlement_days')}"
                )
            else:
                assert start.date is not None
                eff = start.date
                r.notes.append(f"{tag}underlying.effective_date={eff!r} (explicit)")
            end = await r.end(
                f"{tag}underlying.termination_date",
                parse_end(u.termination_date, u.tenor),
                eff,
                cal,
            )
            b = pw.build_swaption(
                p,
                t,
                eff,
                end,
                index_id,
                discounting_curve,
                forwarding_curve,
                vol,
                model,
                pricing["as_of_date"],
            )
            b.notes = notes + b.notes
            out.append(b)
        _require_model(pricing, out)
        return out

    opts = {"swaption_pricing_details": True} if include_details else None
    return await _price(
        backend,
        store,
        pw.SWAPTION,
        "swaptions",
        market,
        as_of,
        calendar_overrides,
        request_id,
        build,
        pw.swaption_summary,
        {"include_diagnostics": True} if include_diagnostics else None,
        opts,
    )


async def price_cds_impl(
    backend: Backend,
    store: SessionStore,
    market: Market,
    preset: str,
    trades: list[pc.CdsTrade],
    discounting_curve: str,
    credit_curve: str | pc.ParSpreadCurve | pc.FlatHazardCurve,
    recovery_rate: float | None,
    model: str,
    as_of: str | None,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
) -> ToolResult:
    async def build(pricing: dict[str, Any], r: DateResolver) -> list[BuiltTrade]:
        p = get_preset(preset)
        conv = p.trade_block("cds")
        assert isinstance(conv, CdsConventions)
        _curves(pricing, discounting_curve, None)
        _require_ref(pricing, "credit_curves", credit_curve, "credit_curve")
        as_of_date = pricing["as_of_date"]
        out: list[BuiltTrade] = []
        for i, t in enumerate(trades):
            tag = f"trades[{i}]." if i else ""
            cal = str(conv.schedule.calendar)
            if t.start.strip().lower() == "as_of":
                eff = as_of_date
                r.notes.append(f"{tag}effective_date={eff!r} (= as_of)")
            else:
                eff = check_date(t.start, "start")
                r.notes.append(f"{tag}effective_date={eff!r} (explicit)")
            end = await r.end(
                f"{tag}termination_date", parse_end(t.maturity, t.tenor, "maturity"), eff, cal
            )
            out.append(
                pc.build_cds(
                    p,
                    t,
                    eff,
                    end,
                    as_of_date,
                    discounting_curve,
                    credit_curve,
                    recovery_rate,
                    model,
                )
            )
        _require_model(pricing, out)
        return out

    return await _price(
        backend,
        store,
        pc.CDS,
        "cds_list",
        market,
        as_of,
        calendar_overrides,
        request_id,
        build,
        pc.cds_summary,
    )


async def price_equity_option_impl(
    backend: Backend,
    store: SessionStore,
    market: Market | None,
    preset: str,
    trades: list[pe.EquityOptionTrade],
    underlying_id: str,
    spot: float | str,
    rate_curve: str | pe.FlatRate,
    dividend_yield: str | pe.FlatRate,
    vol: str | pe.EquityConstantVol,
    model: str | pe.EquityModel,
    discrete_dividends: list[pe.DiscreteDividend] | None,
    as_of: str | None,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
) -> ToolResult:
    if market is None:
        # an equity option needs no curves beyond the flat ones the tool builds
        if as_of is None:
            return local_error_result(
                pe.EQUITY_OPTION,
                None,
                "as_of is required when no market is given",
                request_id=request_id,
            )
        try:
            market = {"as_of_date": check_date(as_of, "as_of")}
        except LocalValidationError as exc:
            return local_error_result(
                pe.EQUITY_OPTION, None, exc.error, exc.problems, request_id=request_id
            )

    async def build(pricing: dict[str, Any], r: DateResolver) -> list[BuiltTrade]:
        p = get_preset(preset)
        _require_ref(pricing, "curves", rate_curve, "rate_curve")
        _require_ref(pricing, "curves", dividend_yield, "dividend_yield")
        _require_ref(pricing, "vol_surfaces", vol, "vol")
        _require_ref(pricing, "models", model, "model")
        _require_ref(pricing, "quotes", spot, "spot")
        out: list[BuiltTrade] = []
        for t in trades:
            out.append(
                pe.build_equity_option(
                    p,
                    t,
                    pricing["as_of_date"],
                    underlying_id,
                    spot,
                    rate_curve,
                    dividend_yield,
                    vol,
                    model,
                    discrete_dividends,
                )
            )
        return out

    return await _price(
        backend,
        store,
        pe.EQUITY_OPTION,
        "options",
        market,
        as_of,
        calendar_overrides,
        request_id,
        build,
        pe.equity_summary,
    )


def _set_fixings(
    pricing: dict[str, Any], index_id: str, fixings: list[pi.Fixing], notes: list[str]
) -> None:
    ix = mk.find_item(pricing, "inflation_indices", index_id)
    if ix is None:
        raise problem(
            "/inflation_index_id",
            f"inflation index {index_id!r} is not in pricing.inflation.inflation_indices: "
            f"{mk.ids_in(pricing, 'inflation_indices')}",
        )
    wire = pi.fixings_wire(fixings)
    if ix.get("fixings") == wire:
        notes.append(
            f"fixings: the market's index {index_id!r} already carries the same {len(wire)} fixings"
        )
    else:
        ix["fixings"] = wire
        notes.append(
            f"fixings: set {len(wire)} fixings on inflation index {index_id!r} "
            "(argument; replaces any the market carried)"
        )


async def _inflation_dates(
    r: DateResolver,
    start: str,
    end_date: str | None,
    tenor: str | None,
    cal: str,
    as_of: str,
    i: int,
    end_field: str,
) -> tuple[str, str]:
    tag = f"trades[{i}]." if i else ""
    if start.strip().lower() == "as_of":
        eff = as_of
        r.notes.append(f"{tag}start_date={eff!r} (= as_of)")
    else:
        eff = check_date(start, "start_date")
        r.notes.append(f"{tag}start_date={eff!r} (explicit)")
    end = await r.end(f"{tag}{end_field}", parse_end(end_date, tenor, end_field), eff, cal)
    return eff, end


async def price_zc_inflation_swap_impl(
    backend: Backend,
    store: SessionStore,
    market: Market,
    preset: str,
    trades: list[pi.ZcInflationSwapTrade],
    inflation_index_id: str,
    fixings: list[pi.Fixing],
    discounting_curve: str,
    inflation_curve: str,
    as_of: str | None,
    include_flows: bool,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
) -> ToolResult:
    async def build(pricing: dict[str, Any], r: DateResolver) -> list[BuiltTrade]:
        p = get_preset(preset)
        conv = p.trade_block("zc_inflation_swap")
        assert isinstance(conv, ZcInflationSwapConventions)
        _curves(pricing, discounting_curve, None)
        _require_ref(pricing, "inflation_curves", inflation_curve, "inflation_curve")
        notes: list[str] = []
        _set_fixings(pricing, inflation_index_id, fixings, notes)
        out: list[BuiltTrade] = []
        for i, t in enumerate(trades):
            eff, end = await _inflation_dates(
                r,
                t.start_date,
                t.maturity_date,
                t.tenor,
                str(conv.fixed_calendar),
                pricing["as_of_date"],
                i,
                "maturity_date",
            )
            b = pi.build_zc_inflation_swap(
                p, t, eff, end, inflation_index_id, discounting_curve, inflation_curve
            )
            if i == 0:
                b.notes = notes + b.notes
            out.append(b)
        return out

    return await _price(
        backend,
        store,
        pi.ZC_INFLATION_SWAP,
        "swaps",
        market,
        as_of,
        calendar_overrides,
        request_id,
        build,
        pi.inflation_summary,
        {"include_flows": include_flows},
    )


async def price_yoy_inflation_swap_impl(
    backend: Backend,
    store: SessionStore,
    market: Market,
    preset: str,
    trades: list[pi.YoyInflationSwapTrade],
    inflation_index_id: str,
    fixings: list[pi.Fixing],
    discounting_curve: str,
    inflation_curve: str,
    as_of: str | None,
    include_flows: bool,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
) -> ToolResult:
    async def build(pricing: dict[str, Any], r: DateResolver) -> list[BuiltTrade]:
        p = get_preset(preset)
        conv = p.trade_block("yoy_inflation_swap")
        assert isinstance(conv, YoyInflationSwapConventions)
        _curves(pricing, discounting_curve, None)
        _require_ref(pricing, "inflation_curves", inflation_curve, "inflation_curve")
        notes: list[str] = []
        _set_fixings(pricing, inflation_index_id, fixings, notes)
        out: list[BuiltTrade] = []
        for i, t in enumerate(trades):
            eff, end = await _inflation_dates(
                r,
                t.effective_date,
                t.termination_date,
                t.tenor,
                str(conv.schedule.calendar),
                pricing["as_of_date"],
                i,
                "termination_date",
            )
            b = pi.build_yoy_inflation_swap(
                p, t, eff, end, inflation_index_id, discounting_curve, inflation_curve
            )
            if i == 0:
                b.notes = notes + b.notes
            out.append(b)
        return out

    return await _price(
        backend,
        store,
        pi.YOY_INFLATION_SWAP,
        "swaps",
        market,
        as_of,
        calendar_overrides,
        request_id,
        build,
        pi.inflation_summary,
        {"include_flows": include_flows},
    )


async def price_yoy_inflation_cap_floor_impl(
    backend: Backend,
    store: SessionStore,
    market: Market,
    preset: str,
    trades: list[pi.YoyInflationCapFloorTrade],
    inflation_index_id: str,
    fixings: list[pi.Fixing],
    discounting_curve: str,
    inflation_curve: str,
    vol: str | pi.YoyVol,
    as_of: str | None,
    calendar_overrides: list[CalendarOverride] | None,
    request_id: str | None,
) -> ToolResult:
    async def build(pricing: dict[str, Any], r: DateResolver) -> list[BuiltTrade]:
        p = get_preset(preset)
        conv = p.trade_block("yoy_inflation_cap_floor")
        assert isinstance(conv, YoyInflationCapFloorConventions)
        _curves(pricing, discounting_curve, None)
        _require_ref(pricing, "inflation_curves", inflation_curve, "inflation_curve")
        _require_ref(pricing, "vol_surfaces", vol, "vol")
        notes: list[str] = []
        _set_fixings(pricing, inflation_index_id, fixings, notes)
        out: list[BuiltTrade] = []
        for i, t in enumerate(trades):
            eff, end = await _inflation_dates(
                r,
                t.effective_date,
                t.termination_date,
                t.tenor,
                str(conv.schedule.calendar),
                pricing["as_of_date"],
                i,
                "termination_date",
            )
            b = pi.build_yoy_inflation_cap_floor(
                p, t, eff, end, inflation_index_id, discounting_curve, inflation_curve, vol
            )
            if i == 0:
                b.notes = notes + b.notes
            out.append(b)
        return out

    return await _price(
        backend,
        store,
        pi.YOY_INFLATION_CAP_FLOOR,
        "cap_floors",
        market,
        as_of,
        calendar_overrides,
        request_id,
        build,
        pi.inflation_summary,
    )


# --------------------------------------------------------------------------
# raw-shaped (validated + forwarded) tools
# --------------------------------------------------------------------------


def _calibration_summary(response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict):
        return None
    keys = ("model_id", "vol_id", "hw_a", "hw_sigma", "rmse", "num_helpers", "grid_points")
    out = {k: response[k] for k in keys if k in response}
    diag = response.get("diagnostics")
    if isinstance(diag, dict):
        out["diagnostics_keys"] = sorted(diag)
        if isinstance(diag.get("calibration"), dict):
            out["calibration"] = diag["calibration"]
    return out or None


def _vol_sample_summary(response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict) or not isinstance(response.get("results"), list):
        return None
    rows = []
    for r in response["results"]:
        if isinstance(r, dict):
            row = {
                k: r[k]
                for k in ("vol_id", "ql_vol_type", "n_expiries", "n_tenors", "n_strikes", "error")
                if k in r
            }
            vols = r.get("vols")
            if isinstance(vols, list):
                row["n_vols"] = len(vols)
            rows.append(row)
    return {"results": rows}


async def raw_impl(
    backend: Backend, endpoint: str, body: Any, summarize: Summarizer, request_id: str | None
) -> ToolResult:
    rid = request_id or new_request_id()
    if not isinstance(body, dict):
        return local_error_result(endpoint, body, "body must be a JSON object", request_id=rid)
    problems = validate_request(endpoint, body)
    if problems:
        return local_error_result(
            endpoint,
            body,
            f"request does not match the vendored schema for {endpoint} "
            f"({len(problems)} problem{'s' if len(problems) != 1 else ''}); nothing was sent",
            [p.as_dict() for p in problems],
            request_id=rid,
        )
    return await run_post(
        backend, endpoint, copy.deepcopy(body), request_id=rid, summarize=summarize
    )


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------

_MARKET_DOC = """market: ``{"session": name}`` (a stored market or curve), an engine
            ``pricing`` block (e.g. a vendored example's ``pricing``; used verbatim)
            or ``{curves: [...], indices: [...], ...}`` from build_curve results."""


def register(app: MCPServer, backend: Backend, store: SessionStore) -> None:
    @app.tool()
    async def price_vanilla_swap(
        market: Market,
        market_data_source: MarketDataSource,
        preset: str,
        swap_type: SwapType,
        notional: float,
        fixed_rate: float,
        effective_date: str,
        discounting_curve: str,
        forwarding_curve: str,
        termination_date: str | None = None,
        tenor: str | None = None,
        spread: float = 0.0,
        index_id: str | None = None,
        fixed_leg_overrides: FixedLegOverrides | None = None,
        floating_leg_overrides: FloatingLegOverrides | None = None,
        additional_trades: list[ps.VanillaSwapTrade] | None = None,
        as_of: str | None = None,
        include_flows: bool = False,
        calendar_overrides: list[CalendarOverride] | None = None,
        request_id: str | None = None,
    ) -> ToolResult:
        """Price a fixed-vs-IBOR swap (POST /price-vanilla-swap).

        Args:
            market_data_source: where the market numbers in this call come from. ``user_pasted``
                (the user pasted or typed the numbers in this conversation), ``user_file`` (the
                user attached a file/screenshot the numbers were read from), ``engine_example``
                (an engine example's pricing block, only when the user explicitly asked to run
                an example), ``session`` (a market previously stored in this session, which
                itself came from one of the above). There is no value for estimated, recalled or
                placeholder data. If you would have to invent numbers, do not call this tool:
                ask the user for the data.
            market: ``{"session": name}``, an engine ``pricing`` block (used verbatim)
                or ``{curves: [...], indices: [...]}`` (build_curve results allowed).
            preset: a preset with a ``vanilla_swap`` block (``EUR_EURIBOR_6M``,
                ``EUR_EURIBOR_3M``): schedule, fixed and floating leg conventions.
            swap_type: ``Payer`` (pay fixed) or ``Receiver``.
            notional: constant notional (> 0).
            fixed_rate: decimal, e.g. 0.032.
            effective_date: ``YYYY-MM-DD`` or ``spot`` (as_of + the preset's settlement
                days, resolved by the engine's /calendar-advance).
            discounting_curve, forwarding_curve: curve ids in the market.
            termination_date: ``YYYY-MM-DD``; or give ``tenor`` (``5Y``, resolved by the
                engine from the effective date, Unadjusted).
            spread: floating-leg spread (decimal, default 0.0).
            index_id: floating index id in the market (default: the preset's index id).
            fixed_leg_overrides, floating_leg_overrides: replace conventions
                (frequency, day_counter, payment_convention, notionals, schedule rules).
            additional_trades: more swaps for the same request (same preset/market).
            as_of: ``YYYY-MM-DD``; required unless ``market`` is a pricing block.
            include_flows: ask the engine for per-leg cash flows.

        Result: uniform shape + ``notes`` (every convention with its source),
        ``date_resolution`` (the /calendar-advance calls) and ``summary.swaps``
        (npv, fair_rate, leg npvs selected from the response).
        """
        first = ps.VanillaSwapTrade(
            swap_type=swap_type,
            notional=notional,
            fixed_rate=fixed_rate,
            effective_date=effective_date,
            termination_date=termination_date,
            tenor=tenor,
            spread=spread,
            index_id=index_id,
            fixed_leg_overrides=fixed_leg_overrides,
            floating_leg_overrides=floating_leg_overrides,
        )
        return stamp(
            await price_vanilla_swap_impl(
                backend,
                store,
                market,
                preset,
                [first, *(additional_trades or [])],
                discounting_curve,
                forwarding_curve,
                as_of,
                include_flows,
                calendar_overrides,
                request_id,
            ),
            market_data_source,
        )

    @app.tool()
    async def price_ois_swap(
        market: Market,
        market_data_source: MarketDataSource,
        preset: str,
        swap_type: SwapType,
        notional: float,
        fixed_rate: float,
        effective_date: str,
        discounting_curve: str,
        forwarding_curve: str,
        termination_date: str | None = None,
        tenor: str | None = None,
        spread: float = 0.0,
        index_id: str | None = None,
        payment_lag: int | None = None,
        averaging_method: RateAveragingType | None = None,
        lookback_days: int | None = None,
        lockout_days: int | None = None,
        apply_observation_shift: bool | None = None,
        telescopic_value_dates: bool | None = None,
        fixed_leg_overrides: FixedLegOverrides | None = None,
        overnight_leg_overrides: FloatingLegOverrides | None = None,
        additional_trades: list[ps.OisSwapTrade] | None = None,
        as_of: str | None = None,
        include_flows: bool = False,
        calendar_overrides: list[CalendarOverride] | None = None,
        request_id: str | None = None,
    ) -> ToolResult:
        """Price an OIS (fixed vs compounded overnight) swap (POST /price-ois-swap).

        Args:
            market_data_source: where the market numbers in this call come from. ``user_pasted``
                (the user pasted or typed the numbers in this conversation), ``user_file`` (the
                user attached a file/screenshot the numbers were read from), ``engine_example``
                (an engine example's pricing block, only when the user explicitly asked to run
                an example), ``session`` (a market previously stored in this session, which
                itself came from one of the above). There is no value for estimated, recalled or
                placeholder data. If you would have to invent numbers, do not call this tool:
                ask the user for the data.
            market: as in price_vanilla_swap.
            preset: a preset with an ``ois_swap`` block (``USD_SOFR_OIS``: payment lag 2;
                ``EUR_ESTR_OIS``: payment lag 0).
            swap_type, notional, fixed_rate, effective_date, termination_date | tenor,
                spread, index_id, discounting_curve, forwarding_curve: as in
                price_vanilla_swap (the overnight index id defaults to the preset's).
            payment_lag, averaging_method, lookback_days, lockout_days,
                apply_observation_shift, telescopic_value_dates: overnight-leg
                parameters; each defaults to the preset's value (noted).
            fixed_leg_overrides, overnight_leg_overrides, additional_trades, as_of,
                include_flows: as in price_vanilla_swap.
        """
        first = ps.OisSwapTrade(
            swap_type=swap_type,
            notional=notional,
            fixed_rate=fixed_rate,
            effective_date=effective_date,
            termination_date=termination_date,
            tenor=tenor,
            spread=spread,
            index_id=index_id,
            payment_lag=payment_lag,
            averaging_method=averaging_method,
            lookback_days=lookback_days,
            lockout_days=lockout_days,
            apply_observation_shift=apply_observation_shift,
            telescopic_value_dates=telescopic_value_dates,
            fixed_leg_overrides=fixed_leg_overrides,
            overnight_leg_overrides=overnight_leg_overrides,
        )
        return stamp(
            await price_ois_swap_impl(
                backend,
                store,
                market,
                preset,
                [first, *(additional_trades or [])],
                discounting_curve,
                forwarding_curve,
                as_of,
                include_flows,
                calendar_overrides,
                request_id,
            ),
            market_data_source,
        )

    @app.tool()
    async def price_fixed_rate_bond(
        market: Market,
        market_data_source: MarketDataSource,
        preset: str,
        face_amount: float,
        coupon_rate: float,
        issue_date: str,
        discounting_curve: str,
        maturity_date: str | None = None,
        tenor: str | None = None,
        effective_date: str | None = None,
        overrides: pb.BondOverrides | None = None,
        yield_overrides: pb.YieldOverrides | None = None,
        additional_trades: list[pb.FixedRateBondTrade] | None = None,
        as_of: str | None = None,
        include_details: bool = False,
        include_flows: bool = False,
        calendar_overrides: list[CalendarOverride] | None = None,
        request_id: str | None = None,
    ) -> ToolResult:
        """Price a fixed-rate bond (POST /price-fixed-rate-bond).

        Args:
            market_data_source: where the market numbers in this call come from. ``user_pasted``
                (the user pasted or typed the numbers in this conversation), ``user_file`` (the
                user attached a file/screenshot the numbers were read from), ``engine_example``
                (an engine example's pricing block, only when the user explicitly asked to run
                an example), ``session`` (a market previously stored in this session, which
                itself came from one of the above). There is no value for estimated, recalled or
                placeholder data. If you would have to invent numbers, do not call this tool:
                ask the user for the data.
            market: as in price_vanilla_swap.
            preset: a preset with a ``fixed_rate_bond`` block (``EUR_FIXED_BOND``).
            face_amount: > 0. coupon_rate: annual decimal coupon.
            issue_date: ``YYYY-MM-DD`` or ``spot`` (as_of + preset settlement days).
            maturity_date: ``YYYY-MM-DD``, or ``tenor`` (engine-resolved from the
                effective date, Unadjusted).
            effective_date: first accrual date (default: = issue_date).
            discounting_curve: curve id in the market.
            overrides: settlement_days, frequency, accrual_day_counter,
                payment_convention, redemption, notionals, schedule rules.
            yield_overrides: how the yield is quoted (day_counter/compounding/frequency).
            include_details / include_flows: pricing.options.bond_pricing_details / _flows.

        ``summary.bonds``: npv, clean/dirty price, accrued, yield, durations.
        """
        first = pb.FixedRateBondTrade(
            face_amount=face_amount,
            coupon_rate=coupon_rate,
            issue_date=issue_date,
            maturity_date=maturity_date,
            tenor=tenor,
            effective_date=effective_date,
            overrides=overrides,
            yield_overrides=yield_overrides,
        )
        return stamp(
            await price_fixed_rate_bond_impl(
                backend,
                store,
                market,
                preset,
                [first, *(additional_trades or [])],
                discounting_curve,
                as_of,
                include_details,
                include_flows,
                calendar_overrides,
                request_id,
            ),
            market_data_source,
        )

    @app.tool()
    async def price_floating_rate_bond(
        market: Market,
        market_data_source: MarketDataSource,
        preset: str,
        face_amount: float,
        issue_date: str,
        discounting_curve: str,
        forwarding_curve: str,
        maturity_date: str | None = None,
        tenor: str | None = None,
        effective_date: str | None = None,
        spread: float = 0.0,
        index_id: str | None = None,
        fixing_days: int | None = None,
        in_arrears: bool | None = None,
        coupon_pricer: str | None = None,
        overrides: pb.BondOverrides | None = None,
        additional_trades: list[pb.FloatingRateBondTrade] | None = None,
        as_of: str | None = None,
        include_details: bool = False,
        include_flows: bool = False,
        calendar_overrides: list[CalendarOverride] | None = None,
        request_id: str | None = None,
    ) -> ToolResult:
        """Price a floating-rate note (POST /price-floating-rate-bond).

        Args:
            market_data_source: where the market numbers in this call come from. ``user_pasted``
                (the user pasted or typed the numbers in this conversation), ``user_file`` (the
                user attached a file/screenshot the numbers were read from), ``engine_example``
                (an engine example's pricing block, only when the user explicitly asked to run
                an example), ``session`` (a market previously stored in this session, which
                itself came from one of the above). There is no value for estimated, recalled or
                placeholder data. If you would have to invent numbers, do not call this tool:
                ask the user for the data.
            preset: a preset with a ``floating_rate_bond`` block (``EUR_EURIBOR_6M``).
            face_amount, issue_date, maturity_date | tenor, effective_date, overrides,
                include_details, include_flows: as in price_fixed_rate_bond.
            spread: coupon spread over the index (decimal). index_id: default preset's.
            fixing_days, in_arrears: default from the preset (noted).
            coupon_pricer: id of a coupon pricer in the market; when omitted the tool
                adds the preset's zero-vol BlackIborCouponPricer (an Ibor coupon needs one).
        """
        first = pb.FloatingRateBondTrade(
            face_amount=face_amount,
            issue_date=issue_date,
            maturity_date=maturity_date,
            tenor=tenor,
            effective_date=effective_date,
            spread=spread,
            index_id=index_id,
            fixing_days=fixing_days,
            in_arrears=in_arrears,
            overrides=overrides,
        )
        return stamp(
            await price_floating_rate_bond_impl(
                backend,
                store,
                market,
                preset,
                [first, *(additional_trades or [])],
                discounting_curve,
                forwarding_curve,
                coupon_pricer,
                as_of,
                include_details,
                include_flows,
                calendar_overrides,
                request_id,
            ),
            market_data_source,
        )

    @app.tool()
    async def price_zero_coupon_bond(
        market: Market,
        market_data_source: MarketDataSource,
        preset: str,
        face_amount: float,
        discounting_curve: str,
        maturity_date: str | None = None,
        tenor: str | None = None,
        issue_date: str | None = None,
        settlement_days: int | None = None,
        redemption: float | None = None,
        yield_overrides: pb.YieldOverrides | None = None,
        additional_trades: list[pb.ZeroCouponBondTrade] | None = None,
        as_of: str | None = None,
        include_details: bool = False,
        calendar_overrides: list[CalendarOverride] | None = None,
        request_id: str | None = None,
    ) -> ToolResult:
        """Price a zero-coupon bond (POST /price-zero-coupon-bond).

        Args:
            market_data_source: where the market numbers in this call come from. ``user_pasted``
                (the user pasted or typed the numbers in this conversation), ``user_file`` (the
                user attached a file/screenshot the numbers were read from), ``engine_example``
                (an engine example's pricing block, only when the user explicitly asked to run
                an example), ``session`` (a market previously stored in this session, which
                itself came from one of the above). There is no value for estimated, recalled or
                placeholder data. If you would have to invent numbers, do not call this tool:
                ask the user for the data.
            preset: a preset with a ``zero_coupon_bond`` block (``EUR_FIXED_BOND``,
                settlement T+3 on TARGET).
            maturity_date: ``YYYY-MM-DD``, or ``tenor`` counted (by the engine, Unadjusted)
                from ``issue_date`` when given else from as_of.
            issue_date: ``YYYY-MM-DD``, ``as_of`` or omitted (engine: null date).
            settlement_days, redemption: default from the preset (noted).
            include_details: pricing.options.bond_pricing_details (duration, convexity).
        """
        first = pb.ZeroCouponBondTrade(
            face_amount=face_amount,
            maturity_date=maturity_date,
            tenor=tenor,
            issue_date=issue_date,
            settlement_days=settlement_days,
            redemption=redemption,
            yield_overrides=yield_overrides,
        )
        return stamp(
            await price_zero_coupon_bond_impl(
                backend,
                store,
                market,
                preset,
                [first, *(additional_trades or [])],
                discounting_curve,
                as_of,
                include_details,
                calendar_overrides,
                request_id,
            ),
            market_data_source,
        )

    @app.tool()
    async def price_callable_fixed_rate_bond(
        market: Market,
        market_data_source: MarketDataSource,
        preset: str,
        face_amount: float,
        coupon_rate: float,
        issue_date: str,
        discounting_curve: str,
        call_schedule: list[pb.Callability],
        model: str | HullWhiteModel,
        maturity_date: str | None = None,
        tenor: str | None = None,
        effective_date: str | None = None,
        tree_steps: int | None = None,
        overrides: pb.BondOverrides | None = None,
        additional_trades: list[pb.CallableBondTrade] | None = None,
        as_of: str | None = None,
        calendar_overrides: list[CalendarOverride] | None = None,
        request_id: str | None = None,
    ) -> ToolResult:
        """Price a callable / puttable fixed-rate bond on a Hull-White lattice
        (POST /price-callable-fixed-rate-bond).

        Args:
            market_data_source: where the market numbers in this call come from. ``user_pasted``
                (the user pasted or typed the numbers in this conversation), ``user_file`` (the
                user attached a file/screenshot the numbers were read from), ``engine_example``
                (an engine example's pricing block, only when the user explicitly asked to run
                an example), ``session`` (a market previously stored in this session, which
                itself came from one of the above). There is no value for estimated, recalled or
                placeholder data. If you would have to invent numbers, do not call this tool:
                ask the user for the data.
            preset: a preset with a ``callable_fixed_rate_bond`` block (``EUR_FIXED_BOND``).
            call_schedule: ``[{date, price, type: Call|Put}]`` with increasing dates
                (clean price per 100 of face).
            model: ``{a, sigma, lattice_steps?, id?}`` (explicit Hull-White, lattice_steps
                default from the preset) or the id of a SwaptionModelSpec in the market.
            tree_steps: engine lattice steps for the bond (default from the preset).
            Other arguments: as in price_fixed_rate_bond.
        """
        first = pb.CallableBondTrade(
            face_amount=face_amount,
            coupon_rate=coupon_rate,
            issue_date=issue_date,
            maturity_date=maturity_date,
            tenor=tenor,
            effective_date=effective_date,
            call_schedule=call_schedule,
            overrides=overrides,
            tree_steps=tree_steps,
        )
        return stamp(
            await price_callable_fixed_rate_bond_impl(
                backend,
                store,
                market,
                preset,
                [first, *(additional_trades or [])],
                discounting_curve,
                model,
                as_of,
                calendar_overrides,
                request_id,
            ),
            market_data_source,
        )

    @app.tool()
    async def price_fra(
        market: Market,
        market_data_source: MarketDataSource,
        preset: str,
        notional: float,
        strike: float,
        side: FRAType,
        discounting_curve: str,
        forwarding_curve: str,
        months_to_start: int | None = None,
        months_to_end: int | None = None,
        start_date: str | None = None,
        maturity_date: str | None = None,
        index_id: str | None = None,
        day_counter: DayCounter | None = None,
        business_day_convention: BusinessDayConvention | None = None,
        additional_trades: list[pr.FraTrade] | None = None,
        as_of: str | None = None,
        calendar_overrides: list[CalendarOverride] | None = None,
        request_id: str | None = None,
    ) -> ToolResult:
        """Price a forward rate agreement (POST /price-fra).

        Args:
            market_data_source: where the market numbers in this call come from. ``user_pasted``
                (the user pasted or typed the numbers in this conversation), ``user_file`` (the
                user attached a file/screenshot the numbers were read from), ``engine_example``
                (an engine example's pricing block, only when the user explicitly asked to run
                an example), ``session`` (a market previously stored in this session, which
                itself came from one of the above). There is no value for estimated, recalled or
                placeholder data. If you would have to invent numbers, do not call this tool:
                ask the user for the data.
            preset: a preset with a ``fra`` block (``EUR_EURIBOR_3M``, ``EUR_EURIBOR_6M``).
            notional: > 0. strike: agreed forward rate (decimal).
            side: ``Long`` (pay fixed) or ``Short``.
            months_to_start, months_to_end: e.g. 3, 6 for a 3x6; the engine resolves
                spot = as_of + settlement days, then spot + 3M / 6M with the preset's
                calendar and convention (three /calendar-advance calls, all echoed).
            start_date, maturity_date: explicit alternative to the months.
            index_id, day_counter, business_day_convention: default from the preset.

        ``summary.fras``: npv, forward_rate, spot_value, settlement_date.
        """
        first = pr.FraTrade(
            side=side,
            notional=notional,
            strike=strike,
            months_to_start=months_to_start,
            months_to_end=months_to_end,
            start_date=start_date,
            maturity_date=maturity_date,
            index_id=index_id,
            day_counter=day_counter,
            calendar=None,
            business_day_convention=business_day_convention,
        )
        return stamp(
            await price_fra_impl(
                backend,
                store,
                market,
                preset,
                [first, *(additional_trades or [])],
                discounting_curve,
                forwarding_curve,
                as_of,
                calendar_overrides,
                request_id,
            ),
            market_data_source,
        )

    @app.tool()
    async def price_cap_floor(
        market: Market,
        market_data_source: MarketDataSource,
        preset: str,
        cap_floor_type: CapFloorType,
        notional: float,
        strike: float,
        effective_date: str,
        discounting_curve: str,
        forwarding_curve: str,
        vol: str | ConstantVol,
        model: str,
        termination_date: str | None = None,
        tenor: str | None = None,
        index_id: str | None = None,
        frequency: Frequency | None = None,
        day_counter: DayCounter | None = None,
        business_day_convention: BusinessDayConvention | None = None,
        schedule_overrides: ScheduleOverrides | None = None,
        include_details: bool = False,
        additional_trades: list[pr.CapFloorTrade] | None = None,
        as_of: str | None = None,
        calendar_overrides: list[CalendarOverride] | None = None,
        request_id: str | None = None,
    ) -> ToolResult:
        """Price an interest-rate cap, floor or collar (POST /price-cap-floor).

        Args:
            market_data_source: where the market numbers in this call come from. ``user_pasted``
                (the user pasted or typed the numbers in this conversation), ``user_file`` (the
                user attached a file/screenshot the numbers were read from), ``engine_example``
                (an engine example's pricing block, only when the user explicitly asked to run
                an example), ``session`` (a market previously stored in this session, which
                itself came from one of the above). There is no value for estimated, recalled or
                placeholder data. If you would have to invent numbers, do not call this tool:
                ask the user for the data.
            preset: a preset with a ``cap_floor`` block (``EUR_EURIBOR_3M`` quarterly,
                ``EUR_EURIBOR_6M`` semiannual).
            cap_floor_type: ``Cap`` | ``Floor`` | ``Collar``. strike: decimal.
            effective_date, termination_date | tenor: as in price_vanilla_swap.
            vol: ``{constant: 0.2, type: Lognormal|Normal|ShiftedLognormal, displacement?,
                id?}`` (an OptionletVolSpec the tool adds to the market, base conventions
                from the preset) or the id of a surface already in the market.
            model: ``Black`` | ``Bachelier`` | ``ShiftedBlack`` | ``HullWhiteLattice`` (a
                CapFloorModelSpec the tool adds, id ``<type>_model``) or a model id.
            include_details: per-caplet breakdown.
            frequency, day_counter, business_day_convention, schedule_overrides:
                default from the preset (noted).

        ``summary.cap_floors``: npv, atm_rate, implied_volatility.
        """
        first = pr.CapFloorTrade(
            cap_floor_type=cap_floor_type,
            notional=notional,
            strike=strike,
            effective_date=effective_date,
            termination_date=termination_date,
            tenor=tenor,
            index_id=index_id,
            frequency=frequency,
            day_counter=day_counter,
            business_day_convention=business_day_convention,
            schedule_overrides=schedule_overrides,
            include_details=include_details,
        )
        return stamp(
            await price_cap_floor_impl(
                backend,
                store,
                market,
                preset,
                [first, *(additional_trades or [])],
                discounting_curve,
                forwarding_curve,
                vol,
                model,
                as_of,
                calendar_overrides,
                request_id,
            ),
            market_data_source,
        )

    @app.tool()
    async def price_swaption(
        market: Market,
        market_data_source: MarketDataSource,
        preset: str,
        underlying: ps.VanillaSwapTrade | ps.OisSwapTrade,
        discounting_curve: str,
        forwarding_curve: str,
        vol: str | ConstantVol | AtmMatrixVol | RawSwaptionVol,
        model: str | HullWhiteModel,
        exercise_date: str | None = None,
        exercise_dates: list[str] | None = None,
        exercise_type: ExerciseType = ExerciseType.European,
        settlement_type: SettlementType = SettlementType.Physical,
        settlement_method: SettlementMethod | None = None,
        underlying_type: str = "VanillaSwap",
        additional_trades: list[pw.SwaptionTrade] | None = None,
        as_of: str | None = None,
        include_details: bool = False,
        include_diagnostics: bool = False,
        calendar_overrides: list[CalendarOverride] | None = None,
        request_id: str | None = None,
    ) -> ToolResult:
        """Price a swaption (POST /price-swaption).

        Args:
            market_data_source: where the market numbers in this call come from. ``user_pasted``
                (the user pasted or typed the numbers in this conversation), ``user_file`` (the
                user attached a file/screenshot the numbers were read from), ``engine_example``
                (an engine example's pricing block, only when the user explicitly asked to run
                an example), ``session`` (a market previously stored in this session, which
                itself came from one of the above). There is no value for estimated, recalled or
                placeholder data. If you would have to invent numbers, do not call this tool:
                ask the user for the data.
            preset: a preset with ``swaption`` + ``vanilla_swap`` blocks (``EUR_EURIBOR_6M``).
            underlying: the swap exercised into, as a VanillaSwapTrade (swap_type,
                notional, fixed_rate, effective_date, termination_date | tenor, ...).
                ``effective_date: "spot"`` = exercise_date + preset settlement days
                (engine-resolved). For an OIS underlying set underlying_type ``OisSwap``.
            exercise_date: European / American. exercise_dates: Bermudan.
            settlement_type: ``Physical`` (method default from the preset, PhysicalOTC) or
                ``Cash`` (give settlement_method: CollateralizedCashPrice | ParYieldCurve).
            vol: ``{constant, type, displacement?, id?}``, ``{expiries, tenors, vols,
                type, id?}`` (ATM matrix), ``{payload_type, payload, id?}`` (SmileCube /
                SabrParams / SabrCalibrate given raw) or a surface id in the market. Built
                surfaces are SwaptionVolSpec with the preset's swap_index_id.
            model: ``Black`` | ``ShiftedBlack`` | ``Bachelier`` (SwaptionModelSpec added,
                id ``<type>_model``), ``{a, sigma, lattice_steps, id?}`` (HullWhiteLattice
                explicit) or a model id in the market.
            include_details: pricing.options.swaption_pricing_details (delta/vega/...).
            include_diagnostics: per-SABR-surface diagnostics in the response.

        ``summary.swaptions``: npv, implied_volatility, atm_forward, annuity.
        """
        first = pw.SwaptionTrade(
            exercise_date=exercise_date,
            exercise_dates=exercise_dates,
            exercise_type=exercise_type,
            settlement_type=settlement_type,
            settlement_method=settlement_method,
            underlying=underlying,
            underlying_type=underlying_type,
        )
        return stamp(
            await price_swaption_impl(
                backend,
                store,
                market,
                preset,
                [first, *(additional_trades or [])],
                discounting_curve,
                forwarding_curve,
                vol,
                model,
                as_of,
                include_details,
                include_diagnostics,
                calendar_overrides,
                request_id,
            ),
            market_data_source,
        )

    @app.tool()
    async def price_cds(
        market: Market,
        market_data_source: MarketDataSource,
        side: ProtectionSide,
        notional: float,
        running_coupon: float,
        discounting_curve: str,
        credit_curve: str | pc.ParSpreadCurve | pc.FlatHazardCurve,
        preset: str = "EUR_CDS",
        start: str = "as_of",
        maturity: str | None = None,
        tenor: str | None = None,
        recovery_rate: float | None = None,
        model: str = "MidPoint",
        upfront: float | None = None,
        upfront_date: str | None = None,
        protection_start: str | None = None,
        trade_date: str | None = None,
        frequency: Frequency | None = None,
        day_counter: DayCounter | None = None,
        business_day_convention: BusinessDayConvention | None = None,
        cash_settlement_days: int | None = None,
        schedule_overrides: ScheduleOverrides | None = None,
        additional_trades: list[pc.CdsTrade] | None = None,
        as_of: str | None = None,
        calendar_overrides: list[CalendarOverride] | None = None,
        request_id: str | None = None,
    ) -> ToolResult:
        """Price a single-name CDS (POST /price-cds).

        Args:
            market_data_source: where the market numbers in this call come from. ``user_pasted``
                (the user pasted or typed the numbers in this conversation), ``user_file`` (the
                user attached a file/screenshot the numbers were read from), ``engine_example``
                (an engine example's pricing block, only when the user explicitly asked to run
                an example), ``session`` (a market previously stored in this session, which
                itself came from one of the above). There is no value for estimated, recalled or
                placeholder data. If you would have to invent numbers, do not call this tool:
                ask the user for the data.
            side: ``Buyer`` (buy protection) or ``Seller``. notional: > 0.
            running_coupon: decimal (0.01 = 100bp).
            credit_curve: ``{par_spreads: [{tenor, spread}], recovery_rate?, id?}``
                (bootstrapped by the engine with the preset's helper conventions),
                ``{hazard_rate, recovery_rate?, id?}`` (flat) or a credit curve id in
                the market.
            preset: a preset with a ``cds`` block (default ``EUR_CDS``: quarterly
                TwentiethIMM, Following, Actual360, MidPoint).
            start: effective date ``YYYY-MM-DD`` or ``as_of`` (default).
            maturity: ``YYYY-MM-DD``; or ``tenor`` (engine-resolved, Unadjusted).
            recovery_rate: for a curve the tool builds (default: preset, 0.4).
            model: ``MidPoint`` | ``ISDA`` (CdsModelSpec added, id ``cds_<type>``) or a
                model id in the market.
            upfront / upfront_date, protection_start (default = start), trade_date
                (default = as_of), frequency, day_counter, business_day_convention,
                cash_settlement_days, schedule_overrides: optional; defaults noted.

        ``summary.cds_list``: npv, fair_spread, fair_upfront, leg npvs.
        """
        first = pc.CdsTrade(
            side=side,
            notional=notional,
            running_coupon=running_coupon,
            start=start,
            maturity=maturity,
            tenor=tenor,
            upfront=upfront,
            upfront_date=upfront_date,
            protection_start=protection_start,
            trade_date=trade_date,
            frequency=frequency,
            day_counter=day_counter,
            business_day_convention=business_day_convention,
            cash_settlement_days=cash_settlement_days,
            schedule_overrides=schedule_overrides,
        )
        return stamp(
            await price_cds_impl(
                backend,
                store,
                market,
                preset,
                [first, *(additional_trades or [])],
                discounting_curve,
                credit_curve,
                recovery_rate,
                model,
                as_of,
                calendar_overrides,
                request_id,
            ),
            market_data_source,
        )

    @app.tool()
    async def price_equity_option(
        spot: float | str,
        market_data_source: MarketDataSource,
        strike: float,
        expiry: str,
        option_type: EquityOptionType,
        vol: str | pe.EquityConstantVol,
        rate_curve: str | pe.FlatRate,
        dividend_yield: str | pe.FlatRate,
        preset: str = "EUR_EQUITY",
        exercise: str = "European",
        exercise_start: str | None = None,
        exercise_dates: list[str] | None = None,
        quantity: float = 1.0,
        underlying_id: str = "EQ",
        trade_id: str | None = None,
        model: str | pe.EquityModel | None = None,
        discrete_dividends: list[pe.DiscreteDividend] | None = None,
        market: Market | None = None,
        as_of: str | None = None,
        additional_trades: list[pe.EquityOptionTrade] | None = None,
        calendar_overrides: list[CalendarOverride] | None = None,
        request_id: str | None = None,
    ) -> ToolResult:
        """Price a vanilla equity option (POST /price-equity-option).

        Args:
            market_data_source: where the market numbers in this call come from. ``user_pasted``
                (the user pasted or typed the numbers in this conversation), ``user_file`` (the
                user attached a file/screenshot the numbers were read from), ``engine_example``
                (an engine example's pricing block, only when the user explicitly asked to run
                an example), ``session`` (a market previously stored in this session, which
                itself came from one of the above). There is no value for estimated, recalled or
                placeholder data. If you would have to invent numbers, do not call this tool:
                ask the user for the data.
            spot: spot price (a Price quote the tool adds) or a quote id in the market.
            strike, expiry (``YYYY-MM-DD``), option_type ``Call`` | ``Put``.
            vol: ``{constant: 0.2, id?}`` (constant BlackVolSpec added) or a surface id.
            rate_curve: ``{rate, end_date, id?}`` (flat continuous zero curve from as_of to
                ``end_date``, added) or a curve id in the market.
            dividend_yield: same shape (``{rate: 0.0, end_date}`` for no dividends); the
                engine requires a dividend curve id on every underlying.
            preset: a preset with an ``equity_option`` block (default ``EUR_EQUITY``).
            exercise: ``European`` | ``American`` (window exercise_start..expiry;
                start default = as_of) | ``Bermudan`` (exercise_dates, last = expiry).
            model: ``{type: BlackScholesAnalytic|BinomialCRR, binomial_steps?, id?}`` or a
                model id; default BlackScholesAnalytic (id ``bs_analytic``).
            discrete_dividends: ``[{ex_date, amount}]`` cash dividends on the underlying.
            market: optional; a pricing block / market with curves, quotes or
                surfaces to reference by id. ``as_of`` is required without it.

        ``summary.options``: npv, delta, gamma, vega, theta, rho.
        """
        first = pe.EquityOptionTrade(
            option_type=option_type,
            strike=strike,
            expiry=expiry,
            exercise=exercise,
            exercise_start=exercise_start,
            exercise_dates=exercise_dates,
            quantity=quantity,
            trade_id=trade_id,
        )
        the_model: str | pe.EquityModel = model if model is not None else pe.EquityModel()
        return stamp(
            await price_equity_option_impl(
                backend,
                store,
                market,
                preset,
                [first, *(additional_trades or [])],
                underlying_id,
                spot,
                rate_curve,
                dividend_yield,
                vol,
                the_model,
                discrete_dividends,
                as_of,
                calendar_overrides,
                request_id,
            ),
            market_data_source,
        )

    @app.tool()
    async def price_zc_inflation_swap(
        market: Market,
        market_data_source: MarketDataSource,
        inflation_index_id: str,
        fixings: list[pi.Fixing],
        swap_type: SwapType,
        notional: float,
        fixed_rate: float,
        discounting_curve: str,
        inflation_curve: str,
        preset: str = "EUR_HICP",
        start_date: str = "as_of",
        maturity_date: str | None = None,
        tenor: str | None = None,
        additional_trades: list[pi.ZcInflationSwapTrade] | None = None,
        as_of: str | None = None,
        include_flows: bool = False,
        calendar_overrides: list[CalendarOverride] | None = None,
        request_id: str | None = None,
    ) -> ToolResult:
        """Price a zero-coupon inflation swap (POST /price-zero-coupon-inflation-swap).

        The market must carry the inflation index and a ZeroInflation curve
        (``pricing.inflation``; see the ``inflation`` examples). ``fixings`` is
        REQUIRED: the engine needs the CPI fixing at start minus the observation lag
        (and the curve helpers need the recent history) and this server has no
        market-data source; the tool sets them on the index and says so in ``notes``.

        Args:
            market_data_source: where the market numbers in this call come from. ``user_pasted``
                (the user pasted or typed the numbers in this conversation), ``user_file`` (the
                user attached a file/screenshot the numbers were read from), ``engine_example``
                (an engine example's pricing block, only when the user explicitly asked to run
                an example), ``session`` (a market previously stored in this session, which
                itself came from one of the above). There is no value for estimated, recalled or
                placeholder data. If you would have to invent numbers, do not call this tool:
                ask the user for the data.
            inflation_index_id: id in pricing.inflation.inflation_indices.
            fixings: ``[{date: "2024-12-01", value: 126.16}, ...]`` monthly CPI levels.
            swap_type: ``Payer`` pays fixed. notional, fixed_rate: decimal.
            start_date: ``YYYY-MM-DD`` or ``as_of``. maturity_date | tenor.
            preset: a preset with a ``zc_inflation_swap`` block (default ``EUR_HICP``).
        """
        first = pi.ZcInflationSwapTrade(
            swap_type=swap_type,
            notional=notional,
            fixed_rate=fixed_rate,
            start_date=start_date,
            maturity_date=maturity_date,
            tenor=tenor,
        )
        return stamp(
            await price_zc_inflation_swap_impl(
                backend,
                store,
                market,
                preset,
                [first, *(additional_trades or [])],
                inflation_index_id,
                fixings,
                discounting_curve,
                inflation_curve,
                as_of,
                include_flows,
                calendar_overrides,
                request_id,
            ),
            market_data_source,
        )

    @app.tool()
    async def price_yoy_inflation_swap(
        market: Market,
        market_data_source: MarketDataSource,
        inflation_index_id: str,
        fixings: list[pi.Fixing],
        swap_type: SwapType,
        notional: float,
        fixed_rate: float,
        discounting_curve: str,
        inflation_curve: str,
        preset: str = "EUR_HICP",
        effective_date: str = "as_of",
        termination_date: str | None = None,
        tenor: str | None = None,
        spread: float = 0.0,
        frequency: Frequency | None = None,
        schedule_overrides: ScheduleOverrides | None = None,
        additional_trades: list[pi.YoyInflationSwapTrade] | None = None,
        as_of: str | None = None,
        include_flows: bool = False,
        calendar_overrides: list[CalendarOverride] | None = None,
        request_id: str | None = None,
    ) -> ToolResult:
        """Price a year-on-year inflation swap (POST /price-year-on-year-inflation-swap).

        The market must carry the YoY inflation index and a YoYInflation curve.
        ``fixings`` (YoY rates) is REQUIRED for the same reason as in
        price_zc_inflation_swap. Fixed and YoY legs share the preset's schedule
        (annual by default); ``spread`` is added to the YoY rate.

        Args:
            market_data_source: where the market numbers in this call come from. ``user_pasted``
                (the user pasted or typed the numbers in this conversation), ``user_file`` (the
                user attached a file/screenshot the numbers were read from), ``engine_example``
                (an engine example's pricing block, only when the user explicitly asked to run
                an example), ``session`` (a market previously stored in this session, which
                itself came from one of the above). There is no value for estimated, recalled or
                placeholder data. If you would have to invent numbers, do not call this tool:
                ask the user for the data.
        """
        first = pi.YoyInflationSwapTrade(
            swap_type=swap_type,
            notional=notional,
            fixed_rate=fixed_rate,
            effective_date=effective_date,
            termination_date=termination_date,
            tenor=tenor,
            spread=spread,
            frequency=frequency,
            schedule_overrides=schedule_overrides,
        )
        return stamp(
            await price_yoy_inflation_swap_impl(
                backend,
                store,
                market,
                preset,
                [first, *(additional_trades or [])],
                inflation_index_id,
                fixings,
                discounting_curve,
                inflation_curve,
                as_of,
                include_flows,
                calendar_overrides,
                request_id,
            ),
            market_data_source,
        )

    @app.tool()
    async def price_yoy_inflation_cap_floor(
        market: Market,
        market_data_source: MarketDataSource,
        inflation_index_id: str,
        fixings: list[pi.Fixing],
        cap_floor_type: CapFloorType,
        notional: float,
        discounting_curve: str,
        inflation_curve: str,
        vol: str | pi.YoyVol,
        preset: str = "EUR_HICP",
        cap_rate: float | None = None,
        floor_rate: float | None = None,
        effective_date: str = "as_of",
        termination_date: str | None = None,
        tenor: str | None = None,
        frequency: Frequency | None = None,
        gearing: float | None = None,
        spread: float | None = None,
        schedule_overrides: ScheduleOverrides | None = None,
        additional_trades: list[pi.YoyInflationCapFloorTrade] | None = None,
        as_of: str | None = None,
        calendar_overrides: list[CalendarOverride] | None = None,
        request_id: str | None = None,
    ) -> ToolResult:
        """Price a year-on-year inflation cap / floor / collar
        (POST /price-year-on-year-inflation-cap-floor).

        ``fixings`` REQUIRED as in price_yoy_inflation_swap. ``vol``:
        ``{constant: 0.01, type: Black|Bachelier|UnitDisplacedBlack, id?}`` (a
        YoYOptionletVolSpec the tool adds with the preset's conventions) or a surface
        id. ``cap_rate`` for Cap/Collar, ``floor_rate`` for Floor/Collar.

        Args:
            market_data_source: where the market numbers in this call come from. ``user_pasted``
                (the user pasted or typed the numbers in this conversation), ``user_file`` (the
                user attached a file/screenshot the numbers were read from), ``engine_example``
                (an engine example's pricing block, only when the user explicitly asked to run
                an example), ``session`` (a market previously stored in this session, which
                itself came from one of the above). There is no value for estimated, recalled or
                placeholder data. If you would have to invent numbers, do not call this tool:
                ask the user for the data.
        """
        first = pi.YoyInflationCapFloorTrade(
            cap_floor_type=cap_floor_type,
            notional=notional,
            cap_rate=cap_rate,
            floor_rate=floor_rate,
            effective_date=effective_date,
            termination_date=termination_date,
            tenor=tenor,
            frequency=frequency,
            gearing=gearing,
            spread=spread,
            schedule_overrides=schedule_overrides,
        )
        return stamp(
            await price_yoy_inflation_cap_floor_impl(
                backend,
                store,
                market,
                preset,
                [first, *(additional_trades or [])],
                inflation_index_id,
                fixings,
                discounting_curve,
                inflation_curve,
                vol,
                as_of,
                calendar_overrides,
                request_id,
            ),
            market_data_source,
        )

    @app.tool()
    async def calibrate_swaption_vol(
        body: dict[str, Any], request_id: str | None = None
    ) -> ToolResult:
        """Calibrate a SABR swaption cube (POST /calibrate-swaption-vol) from a raw
        ``CalibrateSwaptionVolRequest`` body (``pricing`` with a SwaptionSabrCalibrateSpec
        surface, ``vol_id``, ``discounting_curve_id``, ``forwarding_curve_id``). Validated
        against the vendored spec, then forwarded; see the ``vol`` examples
        (``sabrcal_*``) for complete bodies. ``summary`` selects the calibration block.
        """
        return await raw_impl(
            backend, CALIBRATE_SWAPTION_VOL, body, _calibration_summary, request_id
        )

    @app.tool()
    async def calibrate_swaption_model(
        body: dict[str, Any], request_id: str | None = None
    ) -> ToolResult:
        """Calibrate a Hull-White model to swaption vols (POST /calibrate-swaption-model)
        from a raw ``CalibrateSwaptionModelRequest`` body (``pricing`` with a
        SwaptionModelSpec in ``Calibrate`` mode and its ``hw_calibration`` block,
        ``model_id``). Validated, then forwarded; see the ``hwcal_*`` examples.
        ``summary``: hw_a, hw_sigma, rmse, num_helpers.
        """
        return await raw_impl(
            backend, CALIBRATE_SWAPTION_MODEL, body, _calibration_summary, request_id
        )

    @app.tool()
    async def sample_vol_surface(body: dict[str, Any], request_id: str | None = None) -> ToolResult:
        """Sample volatility surfaces on a grid (POST /sample-vol-surfaces) from a raw
        ``SampleVolSurfacesRequest`` body (``pricing`` with the surfaces, ``queries``).
        Validated, then forwarded; see the ``volsample_*`` examples. ``summary``: per
        result vol_id, vol type, grid sizes.
        """
        return await raw_impl(backend, SAMPLE_VOL_SURFACES, body, _vol_sample_summary, request_id)
