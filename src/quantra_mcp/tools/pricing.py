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

from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

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
    CdsEngineType,
    DayCounter,
    EquityOptionType,
    ExerciseType,
    FRAType,
    Frequency,
    IrModelType,
    ProtectionSide,
    RateAveragingType,
    SettlementMethod,
    SettlementType,
    SwapType,
)
from quantra_mcp.schema.validate import validate_request
from quantra_mcp.session import ScratchStore
from quantra_mcp.tools._dates import DateResolutionFailed, DateResolver
from quantra_mcp.tools._market_source import SOURCE_DESCRIPTION, MarketDataSource, stamp
from quantra_mcp.tools._meta import tool
from quantra_mcp.tools._result import ToolResult, local_error_result, new_request_id, run_post
from quantra_mcp.tools.calendar import CalendarOverride, overrides_to_wire

Builder = Callable[[dict[str, Any], DateResolver], Awaitable[list[BuiltTrade]]]
Summarizer = Callable[[Any], dict[str, Any] | None]

Market = dict[str, Any]


# --------------------------------------------------------------------------
# the shared pipeline
# --------------------------------------------------------------------------


async def _price(
    backend: Backend,
    store: ScratchStore,
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
    store: ScratchStore,
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
    store: ScratchStore,
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
    store: ScratchStore,
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
    store: ScratchStore,
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
    store: ScratchStore,
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
    store: ScratchStore,
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
    store: ScratchStore,
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
    store: ScratchStore,
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
    store: ScratchStore,
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
    store: ScratchStore,
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
    store: ScratchStore,
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
    store: ScratchStore,
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
    store: ScratchStore,
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
    store: ScratchStore,
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


def register(app: MCPServer, backend: Backend, store: ScratchStore) -> None:
    @tool(app, "Price a vanilla swap", engine=True)
    async def price_vanilla_swap(
        market: Annotated[
            Market,
            Field(
                description=(
                    'The market: {"session": name} (a stored market or curve), an engine pricing '
                    "block (anything with as_of_date; used verbatim) or {curves: [...], indices: "
                    "[...]} of build_curve results / TermStructure objects, plus optional "
                    "swap_indices, coupon_pricers, vol_surfaces, models, credit_curves, quotes, "
                    "equity_underlyings, inflation_indices, inflation_curves, options."
                )
            ),
        ],
        market_data_source: Annotated[MarketDataSource, Field(description=SOURCE_DESCRIPTION)],
        preset: Annotated[
            str,
            Field(
                description=(
                    "A preset with a vanilla_swap block (EUR_EURIBOR_6M, EUR_EURIBOR_3M, "
                    "GBP_SONIA_SWAP)."
                )
            ),
        ],
        swap_type: Annotated[
            SwapType, Field(description="Payer (pay fixed, receive floating) or Receiver.")
        ],
        notional: Annotated[
            float, Field(description="Notional amount, > 0, in the preset's currency.")
        ],
        fixed_rate: Annotated[float, Field(description="Fixed rate as a decimal (0.032 = 3.2%).")],
        effective_date: Annotated[
            str,
            Field(
                description=(
                    "YYYY-MM-DD, or 'spot' = as_of + the preset's settlement days (resolved by "
                    "the engine's /calendar-advance and reported in date_resolution)."
                ),
                pattern="^(\\d{4}-\\d{2}-\\d{2}|spot)$",
            ),
        ],
        discounting_curve: Annotated[
            str, Field(description="Id of the discounting curve in the market.")
        ],
        forwarding_curve: Annotated[
            str,
            Field(
                description=(
                    "Id of the curve projecting the floating index (may equal the discounting "
                    "curve)."
                )
            ),
        ],
        termination_date: Annotated[
            str | None,
            Field(
                description="Maturity YYYY-MM-DD; alternative to tenor.",
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        tenor: Annotated[
            str | None,
            Field(
                description=(
                    "Tenor from the start date, e.g. 5Y (resolved by the engine, Unadjusted; the "
                    "schedule applies its own conventions); alternative to the explicit end date."
                ),
                pattern="^\\d+[DWMY]$",
            ),
        ] = None,
        spread: Annotated[
            float, Field(description="Spread added to the floating rate, decimal (0.001 = 10bp).")
        ] = 0.0,
        index_id: Annotated[
            str | None,
            Field(
                description="Id of the floating index in the market; default: the preset's index."
            ),
        ] = None,
        fixed_leg_overrides: Annotated[
            FixedLegOverrides | None,
            Field(
                description=(
                    "Overrides of the preset's fixed leg: frequency, day_counter, "
                    "payment_convention, schedule rules."
                )
            ),
        ] = None,
        floating_leg_overrides: Annotated[
            FloatingLegOverrides | None,
            Field(
                description=(
                    "Overrides of the preset's floating leg: frequency, day_counter, "
                    "payment_convention, schedule rules."
                )
            ),
        ] = None,
        additional_trades: Annotated[
            list[ps.VanillaSwapTrade] | None,
            Field(
                description=(
                    "More trades of the same product in the same request; the engine answers per "
                    "item."
                )
            ),
        ] = None,
        as_of: Annotated[
            str | None,
            Field(
                description=(
                    "Valuation date YYYY-MM-DD (pricing.as_of_date); required unless market is a "
                    "pricing block, which carries its own."
                ),
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        include_flows: Annotated[
            bool, Field(description="Also return the cash flows (pricing.options.*_flows).")
        ] = False,
        calendar_overrides: Annotated[
            list[CalendarOverride] | None,
            Field(
                description=(
                    "Per-request holiday corrections (engine >= 0.7.0): [{calendar, "
                    "added_holidays?, removed_holidays?}]; applies to curve helpers, indices and "
                    "schedules alike."
                )
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
        """Price a fixed-vs-IBOR swap (POST /price-vanilla-swap). The preset's vanilla_swap block
        supplies both legs' schedules, frequencies, day counters and payment conventions; every
        default applied is in ``notes`` with its source, the engine-resolved dates in
        ``date_resolution``. ``summary.swaps``: npv, fair_rate, fair_spread, leg npvs.
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

    @tool(app, "Price an OIS swap", engine=True)
    async def price_ois_swap(
        market: Annotated[
            Market,
            Field(
                description=(
                    'The market: {"session": name} (a stored market or curve), an engine pricing '
                    "block (anything with as_of_date; used verbatim) or {curves: [...], indices: "
                    "[...]} of build_curve results / TermStructure objects, plus optional "
                    "swap_indices, coupon_pricers, vol_surfaces, models, credit_curves, quotes, "
                    "equity_underlyings, inflation_indices, inflation_curves, options."
                )
            ),
        ],
        market_data_source: Annotated[MarketDataSource, Field(description=SOURCE_DESCRIPTION)],
        preset: Annotated[
            str,
            Field(
                description=(
                    "A preset with an ois_swap block (USD_SOFR_OIS: payment lag 2; EUR_ESTR_OIS: "
                    "payment lag 0; GBP_SONIA_OIS)."
                )
            ),
        ],
        swap_type: Annotated[
            SwapType, Field(description="Payer (pay fixed, receive floating) or Receiver.")
        ],
        notional: Annotated[
            float, Field(description="Notional amount, > 0, in the preset's currency.")
        ],
        fixed_rate: Annotated[float, Field(description="Fixed rate as a decimal (0.032 = 3.2%).")],
        effective_date: Annotated[
            str,
            Field(
                description=(
                    "YYYY-MM-DD, or 'spot' = as_of + the preset's settlement days (resolved by "
                    "the engine's /calendar-advance and reported in date_resolution)."
                ),
                pattern="^(\\d{4}-\\d{2}-\\d{2}|spot)$",
            ),
        ],
        discounting_curve: Annotated[
            str, Field(description="Id of the discounting curve in the market.")
        ],
        forwarding_curve: Annotated[
            str,
            Field(
                description=(
                    "Id of the curve projecting the floating index (may equal the discounting "
                    "curve)."
                )
            ),
        ],
        termination_date: Annotated[
            str | None,
            Field(
                description="Maturity YYYY-MM-DD; alternative to tenor.",
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        tenor: Annotated[
            str | None,
            Field(
                description=(
                    "Tenor from the start date, e.g. 5Y (resolved by the engine, Unadjusted; the "
                    "schedule applies its own conventions); alternative to the explicit end date."
                ),
                pattern="^\\d+[DWMY]$",
            ),
        ] = None,
        spread: Annotated[
            float, Field(description="Spread added to the floating rate, decimal (0.001 = 10bp).")
        ] = 0.0,
        index_id: Annotated[
            str | None,
            Field(description="Id of the overnight index in the market; default: the preset's."),
        ] = None,
        payment_lag: Annotated[
            int | None,
            Field(
                description=(
                    "Overnight-leg payment lag in business days; default: the preset's (noted)."
                )
            ),
        ] = None,
        averaging_method: Annotated[
            RateAveragingType | None,
            Field(
                description=(
                    "Compound or Simple averaging of the overnight rate; default: the preset's."
                )
            ),
        ] = None,
        lookback_days: Annotated[
            int | None, Field(description="Observation lookback in days; default: the preset's.")
        ] = None,
        lockout_days: Annotated[
            int | None, Field(description="Rate lockout in days; default: the preset's.")
        ] = None,
        apply_observation_shift: Annotated[
            bool | None,
            Field(description="Observation shift with the lookback; default: the preset's."),
        ] = None,
        telescopic_value_dates: Annotated[
            bool | None,
            Field(
                description="Telescopic value dates on the overnight leg; default: the preset's."
            ),
        ] = None,
        fixed_leg_overrides: Annotated[
            FixedLegOverrides | None,
            Field(
                description=(
                    "Overrides of the preset's fixed leg: frequency, day_counter, "
                    "payment_convention, schedule rules."
                )
            ),
        ] = None,
        overnight_leg_overrides: Annotated[
            FloatingLegOverrides | None,
            Field(
                description=(
                    "Overrides of the preset's overnight leg: frequency, day_counter, "
                    "payment_convention, schedule rules."
                )
            ),
        ] = None,
        additional_trades: Annotated[
            list[ps.OisSwapTrade] | None,
            Field(
                description=(
                    "More trades of the same product in the same request; the engine answers per "
                    "item."
                )
            ),
        ] = None,
        as_of: Annotated[
            str | None,
            Field(
                description=(
                    "Valuation date YYYY-MM-DD (pricing.as_of_date); required unless market is a "
                    "pricing block, which carries its own."
                ),
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        include_flows: Annotated[
            bool, Field(description="Also return the cash flows (pricing.options.*_flows).")
        ] = False,
        calendar_overrides: Annotated[
            list[CalendarOverride] | None,
            Field(
                description=(
                    "Per-request holiday corrections (engine >= 0.7.0): [{calendar, "
                    "added_holidays?, removed_holidays?}]; applies to curve helpers, indices and "
                    "schedules alike."
                )
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
        """Price an OIS (fixed vs compounded overnight) swap (POST /price-ois-swap). The preset's
        ois_swap block supplies the schedules, day counters, payment conventions and the
        overnight-leg parameters (each overridable). ``notes`` lists every default with its
        source; ``summary.swaps``: npv, fair_rate, leg npvs.
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

    @tool(app, "Price a fixed-rate bond", engine=True)
    async def price_fixed_rate_bond(
        market: Annotated[
            Market,
            Field(
                description=(
                    'The market: {"session": name} (a stored market or curve), an engine pricing '
                    "block (anything with as_of_date; used verbatim) or {curves: [...], indices: "
                    "[...]} of build_curve results / TermStructure objects, plus optional "
                    "swap_indices, coupon_pricers, vol_surfaces, models, credit_curves, quotes, "
                    "equity_underlyings, inflation_indices, inflation_curves, options."
                )
            ),
        ],
        market_data_source: Annotated[MarketDataSource, Field(description=SOURCE_DESCRIPTION)],
        preset: Annotated[
            str, Field(description="A preset with a fixed_rate_bond block (EUR_FIXED_BOND).")
        ],
        face_amount: Annotated[float, Field(description="Face amount, > 0.")],
        coupon_rate: Annotated[float, Field(description="Annual coupon rate as a decimal.")],
        issue_date: Annotated[
            str,
            Field(
                description=(
                    "Issue date YYYY-MM-DD, or 'spot' = as_of + the preset's settlement days "
                    "(engine-resolved)."
                ),
                pattern="^(\\d{4}-\\d{2}-\\d{2}|spot)$",
            ),
        ],
        discounting_curve: Annotated[
            str, Field(description="Id of the discounting curve in the market.")
        ],
        maturity_date: Annotated[
            str | None,
            Field(
                description="Maturity YYYY-MM-DD; alternative to tenor.",
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        tenor: Annotated[
            str | None,
            Field(
                description=(
                    "Tenor from the effective date, e.g. 10Y (engine-resolved, Unadjusted); "
                    "alternative to maturity_date."
                ),
                pattern="^\\d+[DWMY]$",
            ),
        ] = None,
        effective_date: Annotated[
            str | None, Field(description="First accrual date YYYY-MM-DD; default = issue_date.")
        ] = None,
        overrides: Annotated[
            pb.BondOverrides | None,
            Field(
                description=(
                    "Overrides of the preset's bond conventions: settlement_days, frequency, "
                    "accrual_day_counter, payment_convention, redemption, notionals, schedule "
                    "rules."
                )
            ),
        ] = None,
        yield_overrides: Annotated[
            pb.YieldOverrides | None,
            Field(
                description=(
                    "How the yield is quoted back: day_counter, compounding, frequency (default: "
                    "the preset's)."
                )
            ),
        ] = None,
        additional_trades: Annotated[
            list[pb.FixedRateBondTrade] | None,
            Field(
                description=(
                    "More trades of the same product in the same request; the engine answers per "
                    "item."
                )
            ),
        ] = None,
        as_of: Annotated[
            str | None,
            Field(
                description=(
                    "Valuation date YYYY-MM-DD (pricing.as_of_date); required unless market is a "
                    "pricing block, which carries its own."
                ),
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        include_details: Annotated[
            bool,
            Field(description="pricing.options.bond_pricing_details (duration, convexity, ...)."),
        ] = False,
        include_flows: Annotated[
            bool, Field(description="Also return the cash flows (pricing.options.*_flows).")
        ] = False,
        calendar_overrides: Annotated[
            list[CalendarOverride] | None,
            Field(
                description=(
                    "Per-request holiday corrections (engine >= 0.7.0): [{calendar, "
                    "added_holidays?, removed_holidays?}]; applies to curve helpers, indices and "
                    "schedules alike."
                )
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
        """Price a fixed-rate bond (POST /price-fixed-rate-bond). The preset's fixed_rate_bond
        block supplies settlement days, schedule, accrual day counter, redemption and the yield
        quotation. ``summary.bonds``: npv, clean / dirty price, accrued, yield, durations.
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

    @tool(app, "Price a floating-rate bond", engine=True)
    async def price_floating_rate_bond(
        market: Annotated[
            Market,
            Field(
                description=(
                    'The market: {"session": name} (a stored market or curve), an engine pricing '
                    "block (anything with as_of_date; used verbatim) or {curves: [...], indices: "
                    "[...]} of build_curve results / TermStructure objects, plus optional "
                    "swap_indices, coupon_pricers, vol_surfaces, models, credit_curves, quotes, "
                    "equity_underlyings, inflation_indices, inflation_curves, options."
                )
            ),
        ],
        market_data_source: Annotated[MarketDataSource, Field(description=SOURCE_DESCRIPTION)],
        preset: Annotated[
            str, Field(description="A preset with a floating_rate_bond block (EUR_EURIBOR_6M).")
        ],
        face_amount: Annotated[float, Field(description="Face amount, > 0.")],
        issue_date: Annotated[
            str,
            Field(
                description=(
                    "Issue date YYYY-MM-DD, or 'spot' = as_of + the preset's settlement days "
                    "(engine-resolved)."
                ),
                pattern="^(\\d{4}-\\d{2}-\\d{2}|spot)$",
            ),
        ],
        discounting_curve: Annotated[
            str, Field(description="Id of the discounting curve in the market.")
        ],
        forwarding_curve: Annotated[
            str,
            Field(
                description=(
                    "Id of the curve projecting the floating index (may equal the discounting "
                    "curve)."
                )
            ),
        ],
        maturity_date: Annotated[
            str | None,
            Field(
                description="Maturity YYYY-MM-DD; alternative to tenor.",
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        tenor: Annotated[
            str | None,
            Field(
                description=(
                    "Tenor from the effective date (engine-resolved, Unadjusted); alternative to "
                    "maturity_date."
                ),
                pattern="^\\d+[DWMY]$",
            ),
        ] = None,
        effective_date: Annotated[
            str | None, Field(description="First accrual date YYYY-MM-DD; default = issue_date.")
        ] = None,
        spread: Annotated[float, Field(description="Coupon spread over the index, decimal.")] = 0.0,
        index_id: Annotated[
            str | None,
            Field(
                description="Id of the floating index in the market; default: the preset's index."
            ),
        ] = None,
        fixing_days: Annotated[
            int | None, Field(description="Index fixing days; default: the preset's (noted).")
        ] = None,
        in_arrears: Annotated[
            bool | None, Field(description="Fix in arrears; default: the preset's (noted).")
        ] = None,
        coupon_pricer: Annotated[
            str | None,
            Field(
                description=(
                    "Id of a coupon pricer in the market; when omitted the tool adds the preset's "
                    "zero-vol BlackIborCouponPricer (an Ibor coupon needs one)."
                )
            ),
        ] = None,
        overrides: Annotated[
            pb.BondOverrides | None,
            Field(
                description=(
                    "Overrides of the preset's bond conventions: settlement_days, frequency, "
                    "accrual_day_counter, payment_convention, redemption, notionals, schedule "
                    "rules."
                )
            ),
        ] = None,
        additional_trades: Annotated[
            list[pb.FloatingRateBondTrade] | None,
            Field(
                description=(
                    "More trades of the same product in the same request; the engine answers per "
                    "item."
                )
            ),
        ] = None,
        as_of: Annotated[
            str | None,
            Field(
                description=(
                    "Valuation date YYYY-MM-DD (pricing.as_of_date); required unless market is a "
                    "pricing block, which carries its own."
                ),
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        include_details: Annotated[
            bool,
            Field(description="pricing.options.bond_pricing_details (duration, convexity, ...)."),
        ] = False,
        include_flows: Annotated[
            bool, Field(description="Also return the cash flows (pricing.options.*_flows).")
        ] = False,
        calendar_overrides: Annotated[
            list[CalendarOverride] | None,
            Field(
                description=(
                    "Per-request holiday corrections (engine >= 0.7.0): [{calendar, "
                    "added_holidays?, removed_holidays?}]; applies to curve helpers, indices and "
                    "schedules alike."
                )
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
        """Price a floating-rate note (POST /price-floating-rate-bond). As price_fixed_rate_bond,
        plus the index spread, fixing days and in-arrears flag from the preset; an Ibor coupon
        needs a coupon pricer, so the preset's zero-vol BlackIborCouponPricer is added unless
        ``coupon_pricer`` names one. ``summary.bonds``: npv, clean / dirty price, accrued,
        yield.
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

    @tool(app, "Price a zero-coupon bond", engine=True)
    async def price_zero_coupon_bond(
        market: Annotated[
            Market,
            Field(
                description=(
                    'The market: {"session": name} (a stored market or curve), an engine pricing '
                    "block (anything with as_of_date; used verbatim) or {curves: [...], indices: "
                    "[...]} of build_curve results / TermStructure objects, plus optional "
                    "swap_indices, coupon_pricers, vol_surfaces, models, credit_curves, quotes, "
                    "equity_underlyings, inflation_indices, inflation_curves, options."
                )
            ),
        ],
        market_data_source: Annotated[MarketDataSource, Field(description=SOURCE_DESCRIPTION)],
        preset: Annotated[
            str,
            Field(
                description=(
                    "A preset with a zero_coupon_bond block (EUR_FIXED_BOND: settlement T+3 on "
                    "TARGET)."
                )
            ),
        ],
        face_amount: Annotated[float, Field(description="Face amount, > 0.")],
        discounting_curve: Annotated[
            str, Field(description="Id of the discounting curve in the market.")
        ],
        maturity_date: Annotated[
            str | None,
            Field(
                description="Maturity YYYY-MM-DD; alternative to tenor.",
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        tenor: Annotated[
            str | None,
            Field(
                description=(
                    "Tenor counted by the engine (Unadjusted) from issue_date when given, else "
                    "from as_of; alternative to maturity_date."
                ),
                pattern="^\\d+[DWMY]$",
            ),
        ] = None,
        issue_date: Annotated[
            str | None,
            Field(
                description="YYYY-MM-DD, 'as_of', or omitted (engine: null date).",
                pattern="^(\\d{4}-\\d{2}-\\d{2}|as_of)$",
            ),
        ] = None,
        settlement_days: Annotated[
            int | None, Field(description="Settlement days; default: the preset's (noted).")
        ] = None,
        redemption: Annotated[
            float | None, Field(description="Redemption per 100; default: the preset's (noted).")
        ] = None,
        yield_overrides: Annotated[
            pb.YieldOverrides | None,
            Field(
                description=(
                    "How the yield is quoted back: day_counter, compounding, frequency (default: "
                    "the preset's)."
                )
            ),
        ] = None,
        additional_trades: Annotated[
            list[pb.ZeroCouponBondTrade] | None,
            Field(
                description=(
                    "More trades of the same product in the same request; the engine answers per "
                    "item."
                )
            ),
        ] = None,
        as_of: Annotated[
            str | None,
            Field(
                description=(
                    "Valuation date YYYY-MM-DD (pricing.as_of_date); required unless market is a "
                    "pricing block, which carries its own."
                ),
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        include_details: Annotated[
            bool,
            Field(description="pricing.options.bond_pricing_details (duration, convexity, ...)."),
        ] = False,
        calendar_overrides: Annotated[
            list[CalendarOverride] | None,
            Field(
                description=(
                    "Per-request holiday corrections (engine >= 0.7.0): [{calendar, "
                    "added_holidays?, removed_holidays?}]; applies to curve helpers, indices and "
                    "schedules alike."
                )
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
        """Price a zero-coupon bond (POST /price-zero-coupon-bond). The preset's zero_coupon_bond
        block supplies settlement days, calendar, redemption and the yield quotation.
        ``summary.bonds``: npv, clean / dirty price, yield.
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

    @tool(app, "Price a callable fixed-rate bond", engine=True)
    async def price_callable_fixed_rate_bond(
        market: Annotated[
            Market,
            Field(
                description=(
                    'The market: {"session": name} (a stored market or curve), an engine pricing '
                    "block (anything with as_of_date; used verbatim) or {curves: [...], indices: "
                    "[...]} of build_curve results / TermStructure objects, plus optional "
                    "swap_indices, coupon_pricers, vol_surfaces, models, credit_curves, quotes, "
                    "equity_underlyings, inflation_indices, inflation_curves, options."
                )
            ),
        ],
        market_data_source: Annotated[MarketDataSource, Field(description=SOURCE_DESCRIPTION)],
        preset: Annotated[
            str,
            Field(description="A preset with a callable_fixed_rate_bond block (EUR_FIXED_BOND)."),
        ],
        face_amount: Annotated[float, Field(description="Face amount, > 0.")],
        coupon_rate: Annotated[float, Field(description="Annual coupon rate as a decimal.")],
        issue_date: Annotated[
            str,
            Field(
                description=(
                    "Issue date YYYY-MM-DD, or 'spot' = as_of + the preset's settlement days "
                    "(engine-resolved)."
                ),
                pattern="^(\\d{4}-\\d{2}-\\d{2}|spot)$",
            ),
        ],
        discounting_curve: Annotated[
            str, Field(description="Id of the discounting curve in the market.")
        ],
        call_schedule: Annotated[
            list[pb.Callability],
            Field(
                description=(
                    "[{date, price, type: Call|Put}] with increasing dates; clean price per 100 "
                    "of face."
                )
            ),
        ],
        model: Annotated[
            str | HullWhiteModel,
            Field(
                description=(
                    "{a, sigma, lattice_steps?, id?} (explicit Hull-White; lattice_steps default "
                    "from the preset) or the id of a SwaptionModelSpec in the market."
                )
            ),
        ],
        maturity_date: Annotated[
            str | None,
            Field(
                description="Maturity YYYY-MM-DD; alternative to tenor.",
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        tenor: Annotated[
            str | None,
            Field(
                description=(
                    "Tenor from the effective date (engine-resolved, Unadjusted); alternative to "
                    "maturity_date."
                ),
                pattern="^\\d+[DWMY]$",
            ),
        ] = None,
        effective_date: Annotated[
            str | None, Field(description="First accrual date YYYY-MM-DD; default = issue_date.")
        ] = None,
        tree_steps: Annotated[
            int | None,
            Field(description="Engine lattice steps for the bond; default: the preset's."),
        ] = None,
        overrides: Annotated[
            pb.BondOverrides | None,
            Field(
                description=(
                    "Overrides of the preset's bond conventions: settlement_days, frequency, "
                    "accrual_day_counter, payment_convention, redemption, notionals, schedule "
                    "rules."
                )
            ),
        ] = None,
        additional_trades: Annotated[
            list[pb.CallableBondTrade] | None,
            Field(
                description=(
                    "More trades of the same product in the same request; the engine answers per "
                    "item."
                )
            ),
        ] = None,
        as_of: Annotated[
            str | None,
            Field(
                description=(
                    "Valuation date YYYY-MM-DD (pricing.as_of_date); required unless market is a "
                    "pricing block, which carries its own."
                ),
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        calendar_overrides: Annotated[
            list[CalendarOverride] | None,
            Field(
                description=(
                    "Per-request holiday corrections (engine >= 0.7.0): [{calendar, "
                    "added_holidays?, removed_holidays?}]; applies to curve helpers, indices and "
                    "schedules alike."
                )
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
        """Price a callable / puttable fixed-rate bond on a Hull-White lattice (POST
        /price-callable-fixed-rate-bond). Bond conventions, lattice and tree steps from the
        preset's callable_fixed_rate_bond block; the Hull-White a / sigma are explicit.
        ``summary.bonds``: npv, clean / dirty price, yield.
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

    @tool(app, "Price a FRA", engine=True)
    async def price_fra(
        market: Annotated[
            Market,
            Field(
                description=(
                    'The market: {"session": name} (a stored market or curve), an engine pricing '
                    "block (anything with as_of_date; used verbatim) or {curves: [...], indices: "
                    "[...]} of build_curve results / TermStructure objects, plus optional "
                    "swap_indices, coupon_pricers, vol_surfaces, models, credit_curves, quotes, "
                    "equity_underlyings, inflation_indices, inflation_curves, options."
                )
            ),
        ],
        market_data_source: Annotated[MarketDataSource, Field(description=SOURCE_DESCRIPTION)],
        preset: Annotated[
            str, Field(description="A preset with a fra block (EUR_EURIBOR_3M, EUR_EURIBOR_6M).")
        ],
        notional: Annotated[
            float, Field(description="Notional amount, > 0, in the preset's currency.")
        ],
        strike: Annotated[float, Field(description="Agreed forward rate, decimal.")],
        side: Annotated[FRAType, Field(description="Long (pay fixed) or Short.")],
        discounting_curve: Annotated[
            str, Field(description="Id of the discounting curve in the market.")
        ],
        forwarding_curve: Annotated[
            str,
            Field(
                description=(
                    "Id of the curve projecting the floating index (may equal the discounting "
                    "curve)."
                )
            ),
        ],
        months_to_start: Annotated[
            int | None,
            Field(
                description=(
                    "e.g. 3 for a 3x6: the engine resolves spot = as_of + settlement days, then "
                    "spot + 3M with the preset's calendar and convention (echoed)."
                )
            ),
        ] = None,
        months_to_end: Annotated[
            int | None, Field(description="e.g. 6 for a 3x6 (engine-resolved as months_to_start).")
        ] = None,
        start_date: Annotated[
            str | None,
            Field(
                description="Explicit start YYYY-MM-DD; alternative to the months.",
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        maturity_date: Annotated[
            str | None,
            Field(
                description="Explicit maturity YYYY-MM-DD; alternative to the months.",
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        index_id: Annotated[
            str | None,
            Field(
                description="Id of the floating index in the market; default: the preset's index."
            ),
        ] = None,
        day_counter: Annotated[
            DayCounter | None, Field(description="Day counter; default: the preset's.")
        ] = None,
        business_day_convention: Annotated[
            BusinessDayConvention | None,
            Field(description="Business day convention; default: the preset's."),
        ] = None,
        additional_trades: Annotated[
            list[pr.FraTrade] | None,
            Field(
                description=(
                    "More trades of the same product in the same request; the engine answers per "
                    "item."
                )
            ),
        ] = None,
        as_of: Annotated[
            str | None,
            Field(
                description=(
                    "Valuation date YYYY-MM-DD (pricing.as_of_date); required unless market is a "
                    "pricing block, which carries its own."
                ),
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        calendar_overrides: Annotated[
            list[CalendarOverride] | None,
            Field(
                description=(
                    "Per-request holiday corrections (engine >= 0.7.0): [{calendar, "
                    "added_holidays?, removed_holidays?}]; applies to curve helpers, indices and "
                    "schedules alike."
                )
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
        """Price a forward rate agreement (POST /price-fra). Dates are resolved by the engine from
        spot with the preset's calendar and convention (echoed in ``date_resolution``).
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

    @tool(app, "Price a cap / floor", engine=True)
    async def price_cap_floor(
        market: Annotated[
            Market,
            Field(
                description=(
                    'The market: {"session": name} (a stored market or curve), an engine pricing '
                    "block (anything with as_of_date; used verbatim) or {curves: [...], indices: "
                    "[...]} of build_curve results / TermStructure objects, plus optional "
                    "swap_indices, coupon_pricers, vol_surfaces, models, credit_curves, quotes, "
                    "equity_underlyings, inflation_indices, inflation_curves, options."
                )
            ),
        ],
        market_data_source: Annotated[MarketDataSource, Field(description=SOURCE_DESCRIPTION)],
        preset: Annotated[
            str,
            Field(
                description=(
                    "A preset with a cap_floor block (EUR_EURIBOR_3M quarterly, EUR_EURIBOR_6M "
                    "semiannual)."
                )
            ),
        ],
        cap_floor_type: Annotated[CapFloorType, Field(description="Cap, Floor or Collar.")],
        notional: Annotated[
            float, Field(description="Notional amount, > 0, in the preset's currency.")
        ],
        strike: Annotated[float, Field(description="Strike as a decimal.")],
        effective_date: Annotated[
            str,
            Field(
                description=(
                    "YYYY-MM-DD, or 'spot' = as_of + the preset's settlement days (resolved by "
                    "the engine's /calendar-advance and reported in date_resolution)."
                ),
                pattern="^(\\d{4}-\\d{2}-\\d{2}|spot)$",
            ),
        ],
        discounting_curve: Annotated[
            str, Field(description="Id of the discounting curve in the market.")
        ],
        forwarding_curve: Annotated[
            str,
            Field(
                description=(
                    "Id of the curve projecting the floating index (may equal the discounting "
                    "curve)."
                )
            ),
        ],
        vol: Annotated[
            str | ConstantVol,
            Field(
                description=(
                    "{constant: 0.2, type: Lognormal|Normal|ShiftedLognormal, displacement?, id?} "
                    "(an OptionletVolSpec the tool adds with the preset's base conventions) or "
                    "the id of a surface already in the market."
                )
            ),
        ],
        model: Annotated[
            IrModelType | str,
            Field(
                description=(
                    "Black, Bachelier, ShiftedBlack or HullWhiteLattice (a CapFloorModelSpec the "
                    "tool adds, id <type>_model), or the id of a model in the market."
                )
            ),
        ],
        termination_date: Annotated[
            str | None,
            Field(
                description="Maturity YYYY-MM-DD; alternative to tenor.",
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        tenor: Annotated[
            str | None,
            Field(
                description=(
                    "Tenor from the start date, e.g. 5Y (resolved by the engine, Unadjusted; the "
                    "schedule applies its own conventions); alternative to the explicit end date."
                ),
                pattern="^\\d+[DWMY]$",
            ),
        ] = None,
        index_id: Annotated[
            str | None,
            Field(
                description="Id of the floating index in the market; default: the preset's index."
            ),
        ] = None,
        frequency: Annotated[
            Frequency | None, Field(description="Coupon frequency; default: the preset's.")
        ] = None,
        day_counter: Annotated[
            DayCounter | None, Field(description="Day counter; default: the preset's.")
        ] = None,
        business_day_convention: Annotated[
            BusinessDayConvention | None,
            Field(description="Business day convention; default: the preset's."),
        ] = None,
        schedule_overrides: Annotated[
            ScheduleOverrides | None,
            Field(
                description=(
                    "Overrides of the preset's schedule rules (calendar, convention, termination "
                    "convention, date generation, end of month)."
                )
            ),
        ] = None,
        include_details: Annotated[
            bool, Field(description="Per-caplet breakdown in the response.")
        ] = False,
        additional_trades: Annotated[
            list[pr.CapFloorTrade] | None,
            Field(
                description=(
                    "More trades of the same product in the same request; the engine answers per "
                    "item."
                )
            ),
        ] = None,
        as_of: Annotated[
            str | None,
            Field(
                description=(
                    "Valuation date YYYY-MM-DD (pricing.as_of_date); required unless market is a "
                    "pricing block, which carries its own."
                ),
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        calendar_overrides: Annotated[
            list[CalendarOverride] | None,
            Field(
                description=(
                    "Per-request holiday corrections (engine >= 0.7.0): [{calendar, "
                    "added_holidays?, removed_holidays?}]; applies to curve helpers, indices and "
                    "schedules alike."
                )
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
        """Price an interest-rate cap, floor or collar (POST /price-cap-floor). The preset's
        cap_floor block supplies the schedule, frequency, day counter and the OptionletVolSpec
        base conventions; the vol surface and model the tool builds are added to the market
        under explicit or default ids (noted). ``summary.cap_floors``: npv, atm_rate,
        implied_volatility.
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

    @tool(app, "Price a swaption", engine=True)
    async def price_swaption(
        market: Annotated[
            Market,
            Field(
                description=(
                    'The market: {"session": name} (a stored market or curve), an engine pricing '
                    "block (anything with as_of_date; used verbatim) or {curves: [...], indices: "
                    "[...]} of build_curve results / TermStructure objects, plus optional "
                    "swap_indices, coupon_pricers, vol_surfaces, models, credit_curves, quotes, "
                    "equity_underlyings, inflation_indices, inflation_curves, options."
                )
            ),
        ],
        market_data_source: Annotated[MarketDataSource, Field(description=SOURCE_DESCRIPTION)],
        preset: Annotated[
            str,
            Field(
                description=(
                    "A preset with swaption + vanilla_swap (or ois_swap) blocks (EUR_EURIBOR_6M, "
                    "USD_SOFR_OIS)."
                )
            ),
        ],
        underlying: Annotated[
            ps.VanillaSwapTrade | ps.OisSwapTrade,
            Field(
                description=(
                    "The swap exercised into: swap_type, notional, fixed_rate, effective_date "
                    "('spot' = exercise date + the preset's settlement days, engine-resolved), "
                    "termination_date | tenor, spread, index_id, overrides."
                )
            ),
        ],
        discounting_curve: Annotated[
            str, Field(description="Id of the discounting curve in the market.")
        ],
        forwarding_curve: Annotated[
            str,
            Field(
                description=(
                    "Id of the curve projecting the floating index (may equal the discounting "
                    "curve)."
                )
            ),
        ],
        vol: Annotated[
            str | ConstantVol | AtmMatrixVol | RawSwaptionVol,
            Field(
                description=(
                    "{constant, type, displacement?, id?}, {expiries, tenors, vols, type, id?} "
                    "(ATM matrix), {payload_type, payload, id?} (SmileCube / SabrParams / "
                    "SabrCalibrate given raw) or the id of a surface in the market; built "
                    "surfaces are SwaptionVolSpec with the preset's swap_index_id."
                )
            ),
        ],
        model: Annotated[
            IrModelType | HullWhiteModel | str,
            Field(
                description=(
                    "Black, ShiftedBlack or Bachelier (a SwaptionModelSpec the tool adds, id "
                    "<type>_model), {a, sigma, lattice_steps, id?} (explicit HullWhiteLattice) or "
                    "the id of a model in the market."
                )
            ),
        ],
        exercise_date: Annotated[
            str | None,
            Field(
                description="Exercise date YYYY-MM-DD for European / American.",
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        exercise_dates: Annotated[
            list[str] | None, Field(description="Exercise dates YYYY-MM-DD for Bermudan.")
        ] = None,
        exercise_type: Annotated[
            ExerciseType, Field(description="European, Bermudan or American.")
        ] = ExerciseType.European,
        settlement_type: Annotated[
            SettlementType,
            Field(
                description=(
                    "Physical (method defaults to the preset's, PhysicalOTC) or Cash (give "
                    "settlement_method)."
                )
            ),
        ] = SettlementType.Physical,
        settlement_method: Annotated[
            SettlementMethod | None,
            Field(
                description=(
                    "PhysicalOTC, PhysicalCleared, CollateralizedCashPrice or ParYieldCurve; "
                    "default from the preset for Physical."
                )
            ),
        ] = None,
        underlying_type: Annotated[
            Literal["VanillaSwap", "OisSwap"],
            Field(
                description=(
                    "VanillaSwap or OisSwap (the preset's matching swap block builds the "
                    "underlying)."
                )
            ),
        ] = "VanillaSwap",
        additional_trades: Annotated[
            list[pw.SwaptionTrade] | None,
            Field(
                description=(
                    "More trades of the same product in the same request; the engine answers per "
                    "item."
                )
            ),
        ] = None,
        as_of: Annotated[
            str | None,
            Field(
                description=(
                    "Valuation date YYYY-MM-DD (pricing.as_of_date); required unless market is a "
                    "pricing block, which carries its own."
                ),
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        include_details: Annotated[
            bool, Field(description="pricing.options.swaption_pricing_details (delta, vega, ...).")
        ] = False,
        include_diagnostics: Annotated[
            bool, Field(description="Per-SABR-surface diagnostics in the response.")
        ] = False,
        calendar_overrides: Annotated[
            list[CalendarOverride] | None,
            Field(
                description=(
                    "Per-request holiday corrections (engine >= 0.7.0): [{calendar, "
                    "added_holidays?, removed_holidays?}]; applies to curve helpers, indices and "
                    "schedules alike."
                )
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
        """Price a European, Bermudan or American swaption on a vanilla or OIS underlying (POST
        /price-swaption). The underlying is built from the preset's swap block, the vol surface
        (SwaptionVolSpec with the preset's swap_index_id) and the model are added to the market
        under explicit or default ids (noted). ``summary.swaptions``: npv, implied_volatility,
        atm_forward, annuity.
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

    @tool(app, "Price a CDS", engine=True)
    async def price_cds(
        market: Annotated[
            Market,
            Field(
                description=(
                    'The market: {"session": name} (a stored market or curve), an engine pricing '
                    "block (anything with as_of_date; used verbatim) or {curves: [...], indices: "
                    "[...]} of build_curve results / TermStructure objects, plus optional "
                    "swap_indices, coupon_pricers, vol_surfaces, models, credit_curves, quotes, "
                    "equity_underlyings, inflation_indices, inflation_curves, options."
                )
            ),
        ],
        market_data_source: Annotated[MarketDataSource, Field(description=SOURCE_DESCRIPTION)],
        side: Annotated[ProtectionSide, Field(description="Buyer (buy protection) or Seller.")],
        notional: Annotated[
            float, Field(description="Notional amount, > 0, in the preset's currency.")
        ],
        running_coupon: Annotated[
            float, Field(description="Running coupon as a decimal (0.01 = 100bp).")
        ],
        discounting_curve: Annotated[
            str, Field(description="Id of the discounting curve in the market.")
        ],
        credit_curve: Annotated[
            str | pc.ParSpreadCurve | pc.FlatHazardCurve,
            Field(
                description=(
                    "{par_spreads: [{tenor, spread}], recovery_rate?, id?} (bootstrapped by the "
                    "engine with the preset's helper conventions), {hazard_rate, recovery_rate?, "
                    "id?} (flat) or the id of a credit curve in the market."
                )
            ),
        ],
        preset: Annotated[
            str,
            Field(
                description=(
                    "A preset with a cds block (EUR_CDS: quarterly TwentiethIMM, Following, "
                    "Actual360, MidPoint)."
                )
            ),
        ] = "EUR_CDS",
        start: Annotated[
            str,
            Field(
                description="Effective date YYYY-MM-DD or 'as_of'.",
                pattern="^(\\d{4}-\\d{2}-\\d{2}|as_of)$",
            ),
        ] = "as_of",
        maturity: Annotated[
            str | None,
            Field(
                description="Maturity YYYY-MM-DD; alternative to tenor.",
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        tenor: Annotated[
            str | None,
            Field(
                description=(
                    "Tenor from start (engine-resolved, Unadjusted); alternative to maturity."
                ),
                pattern="^\\d+[DWMY]$",
            ),
        ] = None,
        recovery_rate: Annotated[
            float | None,
            Field(
                description=(
                    "Recovery rate for a curve the tool builds; default: the preset's (0.4)."
                )
            ),
        ] = None,
        model: Annotated[
            CdsEngineType | str,
            Field(
                description=(
                    "MidPoint or ISDA (a CdsModelSpec the tool adds, id cds_<type>) or the id of "
                    "a model in the market."
                )
            ),
        ] = "MidPoint",
        upfront: Annotated[
            float | None, Field(description="Upfront payment as a decimal of notional, if any.")
        ] = None,
        upfront_date: Annotated[
            str | None,
            Field(description="Upfront payment date YYYY-MM-DD.", pattern="^\\d{4}-\\d{2}-\\d{2}$"),
        ] = None,
        protection_start: Annotated[
            str | None,
            Field(
                description="Protection start YYYY-MM-DD; default = start.",
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        trade_date: Annotated[
            str | None,
            Field(
                description="Trade date YYYY-MM-DD; default = as_of.",
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        frequency: Annotated[
            Frequency | None, Field(description="Premium frequency; default: the preset's.")
        ] = None,
        day_counter: Annotated[
            DayCounter | None, Field(description="Day counter; default: the preset's.")
        ] = None,
        business_day_convention: Annotated[
            BusinessDayConvention | None,
            Field(description="Business day convention; default: the preset's."),
        ] = None,
        cash_settlement_days: Annotated[
            int | None, Field(description="Cash settlement days; default: the preset's.")
        ] = None,
        schedule_overrides: Annotated[
            ScheduleOverrides | None,
            Field(
                description=(
                    "Overrides of the preset's schedule rules (calendar, convention, termination "
                    "convention, date generation, end of month)."
                )
            ),
        ] = None,
        additional_trades: Annotated[
            list[pc.CdsTrade] | None,
            Field(
                description=(
                    "More trades of the same product in the same request; the engine answers per "
                    "item."
                )
            ),
        ] = None,
        as_of: Annotated[
            str | None,
            Field(
                description=(
                    "Valuation date YYYY-MM-DD (pricing.as_of_date); required unless market is a "
                    "pricing block, which carries its own."
                ),
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        calendar_overrides: Annotated[
            list[CalendarOverride] | None,
            Field(
                description=(
                    "Per-request holiday corrections (engine >= 0.7.0): [{calendar, "
                    "added_holidays?, removed_holidays?}]; applies to curve helpers, indices and "
                    "schedules alike."
                )
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
        """Price a single-name CDS (POST /price-cds). The preset's cds block supplies the premium
        schedule (TwentiethIMM), accrual flags, the credit-curve helper conventions and the
        engine type; a credit curve given as par spreads is bootstrapped by the engine.
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

    @tool(app, "Price an equity option", engine=True)
    async def price_equity_option(
        spot: Annotated[
            float | str,
            Field(
                description=(
                    "Spot price (a Price quote the tool adds) or the id of a quote in the market."
                )
            ),
        ],
        market_data_source: Annotated[MarketDataSource, Field(description=SOURCE_DESCRIPTION)],
        strike: Annotated[float, Field(description="Strike price.")],
        expiry: Annotated[
            str, Field(description="Expiry date YYYY-MM-DD.", pattern="^\\d{4}-\\d{2}-\\d{2}$")
        ],
        option_type: Annotated[EquityOptionType, Field(description="Call or Put.")],
        vol: Annotated[
            str | pe.EquityConstantVol,
            Field(
                description=(
                    "{constant: 0.2, id?} (a constant BlackVolSpec the tool adds) or the id of a "
                    "surface in the market."
                )
            ),
        ],
        rate_curve: Annotated[
            str | pe.FlatRate,
            Field(
                description=(
                    "{rate, end_date, id?} (a flat continuous zero curve from as_of to end_date, "
                    "added) or the id of a curve in the market."
                )
            ),
        ],
        dividend_yield: Annotated[
            str | pe.FlatRate,
            Field(
                description=(
                    "Same shape as rate_curve ({rate: 0.0, end_date} for no dividends); the "
                    "engine requires a dividend curve on every underlying."
                )
            ),
        ],
        preset: Annotated[
            str, Field(description="A preset with an equity_option block (EUR_EQUITY).")
        ] = "EUR_EQUITY",
        exercise: Annotated[
            Literal["European", "American", "Bermudan"],
            Field(
                description=(
                    "European, American (window exercise_start..expiry) or Bermudan "
                    "(exercise_dates, last = expiry)."
                )
            ),
        ] = "European",
        exercise_start: Annotated[
            str | None,
            Field(
                description="American: window start YYYY-MM-DD; default = as_of.",
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        exercise_dates: Annotated[
            list[str] | None,
            Field(description="Bermudan exercise dates YYYY-MM-DD, the last = expiry."),
        ] = None,
        quantity: Annotated[float, Field(description="Number of options.")] = 1.0,
        underlying_id: Annotated[
            str, Field(description="Id of the EquityUnderlying the tool adds.")
        ] = "EQ",
        trade_id: Annotated[
            str | None, Field(description="Id of the option in the request.")
        ] = None,
        model: Annotated[
            str | pe.EquityModel | None,
            Field(
                description=(
                    "{type: BlackScholesAnalytic|BinomialCRR, binomial_steps?, id?} or the id of "
                    "a model in the market; default BlackScholesAnalytic (id bs_analytic)."
                )
            ),
        ] = None,
        discrete_dividends: Annotated[
            list[pe.DiscreteDividend] | None,
            Field(description="[{ex_date, amount}] cash dividends on the underlying."),
        ] = None,
        market: Annotated[
            Market | None,
            Field(
                description=(
                    "Optional: a pricing block or market with curves, quotes or surfaces to "
                    "reference by id; as_of is required without it."
                )
            ),
        ] = None,
        as_of: Annotated[
            str | None,
            Field(
                description=(
                    "Valuation date YYYY-MM-DD (pricing.as_of_date); required unless market is a "
                    "pricing block, which carries its own."
                ),
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        additional_trades: Annotated[
            list[pe.EquityOptionTrade] | None,
            Field(
                description=(
                    "More trades of the same product in the same request; the engine answers per "
                    "item."
                )
            ),
        ] = None,
        calendar_overrides: Annotated[
            list[CalendarOverride] | None,
            Field(
                description=(
                    "Per-request holiday corrections (engine >= 0.7.0): [{calendar, "
                    "added_holidays?, removed_holidays?}]; applies to curve helpers, indices and "
                    "schedules alike."
                )
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
        """Price a vanilla equity option (POST /price-equity-option). Flat rate and dividend curves
        are added as two-point InterpolatedZero curves, the vol as a constant BlackVolSpec, with
        the preset's equity_option conventions. ``summary.options``: npv, delta, gamma, vega,
        theta, rho.
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

    @tool(app, "Price a zero-coupon inflation swap", engine=True)
    async def price_zc_inflation_swap(
        market: Annotated[
            Market,
            Field(
                description=(
                    'The market: {"session": name} (a stored market or curve), an engine pricing '
                    "block (anything with as_of_date; used verbatim) or {curves: [...], indices: "
                    "[...]} of build_curve results / TermStructure objects, plus optional "
                    "swap_indices, coupon_pricers, vol_surfaces, models, credit_curves, quotes, "
                    "equity_underlyings, inflation_indices, inflation_curves, options."
                )
            ),
        ],
        market_data_source: Annotated[MarketDataSource, Field(description=SOURCE_DESCRIPTION)],
        inflation_index_id: Annotated[
            str,
            Field(description="Id of the inflation index in pricing.inflation.inflation_indices."),
        ],
        fixings: Annotated[
            list[pi.Fixing],
            Field(
                description=(
                    "Monthly index fixings [{date, value}]; REQUIRED (the engine needs the fixing "
                    "at start minus the observation lag and the curve helpers the recent history; "
                    "this server has no market-data source)."
                )
            ),
        ],
        swap_type: Annotated[
            SwapType, Field(description="Payer pays fixed, Receiver receives it.")
        ],
        notional: Annotated[
            float, Field(description="Notional amount, > 0, in the preset's currency.")
        ],
        fixed_rate: Annotated[float, Field(description="Fixed zero-coupon rate, decimal.")],
        discounting_curve: Annotated[
            str, Field(description="Id of the discounting curve in the market.")
        ],
        inflation_curve: Annotated[
            str,
            Field(description="Id of the inflation curve in pricing.inflation.inflation_curves."),
        ],
        preset: Annotated[
            str, Field(description="A preset with a zc_inflation_swap block (EUR_HICP).")
        ] = "EUR_HICP",
        start_date: Annotated[
            str,
            Field(
                description="Start YYYY-MM-DD or 'as_of'.", pattern="^(\\d{4}-\\d{2}-\\d{2}|as_of)$"
            ),
        ] = "as_of",
        maturity_date: Annotated[
            str | None,
            Field(
                description="Maturity YYYY-MM-DD; alternative to tenor.",
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        tenor: Annotated[
            str | None,
            Field(
                description=(
                    "Tenor from start_date (engine-resolved); alternative to maturity_date."
                ),
                pattern="^\\d+[DWMY]$",
            ),
        ] = None,
        additional_trades: Annotated[
            list[pi.ZcInflationSwapTrade] | None,
            Field(
                description=(
                    "More trades of the same product in the same request; the engine answers per "
                    "item."
                )
            ),
        ] = None,
        as_of: Annotated[
            str | None,
            Field(
                description=(
                    "Valuation date YYYY-MM-DD (pricing.as_of_date); required unless market is a "
                    "pricing block, which carries its own."
                ),
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        include_flows: Annotated[
            bool, Field(description="Also return the cash flows (pricing.options.*_flows).")
        ] = False,
        calendar_overrides: Annotated[
            list[CalendarOverride] | None,
            Field(
                description=(
                    "Per-request holiday corrections (engine >= 0.7.0): [{calendar, "
                    "added_holidays?, removed_holidays?}]; applies to curve helpers, indices and "
                    "schedules alike."
                )
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
        """Price a zero-coupon inflation swap (POST /price-zero-coupon-inflation-swap). The market
        must carry the inflation index and a ZeroInflation curve (pricing.inflation; see the
        inflation examples); ``fixings`` are set on the index and said so in ``notes``. The
        preset's zc_inflation_swap block supplies the observation lag and interpolation,
        calendars and day counter.
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

    @tool(app, "Price a year-on-year inflation swap", engine=True)
    async def price_yoy_inflation_swap(
        market: Annotated[
            Market,
            Field(
                description=(
                    'The market: {"session": name} (a stored market or curve), an engine pricing '
                    "block (anything with as_of_date; used verbatim) or {curves: [...], indices: "
                    "[...]} of build_curve results / TermStructure objects, plus optional "
                    "swap_indices, coupon_pricers, vol_surfaces, models, credit_curves, quotes, "
                    "equity_underlyings, inflation_indices, inflation_curves, options."
                )
            ),
        ],
        market_data_source: Annotated[MarketDataSource, Field(description=SOURCE_DESCRIPTION)],
        inflation_index_id: Annotated[
            str,
            Field(description="Id of the inflation index in pricing.inflation.inflation_indices."),
        ],
        fixings: Annotated[
            list[pi.Fixing],
            Field(
                description=(
                    "Monthly YoY index fixings [{date, value}]; REQUIRED (see "
                    "price_zc_inflation_swap)."
                )
            ),
        ],
        swap_type: Annotated[
            SwapType, Field(description="Payer (pay fixed, receive floating) or Receiver.")
        ],
        notional: Annotated[
            float, Field(description="Notional amount, > 0, in the preset's currency.")
        ],
        fixed_rate: Annotated[float, Field(description="Fixed rate as a decimal (0.032 = 3.2%).")],
        discounting_curve: Annotated[
            str, Field(description="Id of the discounting curve in the market.")
        ],
        inflation_curve: Annotated[
            str,
            Field(description="Id of the inflation curve in pricing.inflation.inflation_curves."),
        ],
        preset: Annotated[
            str, Field(description="A preset with a yoy_inflation_swap block (EUR_HICP).")
        ] = "EUR_HICP",
        effective_date: Annotated[
            str,
            Field(
                description="Start YYYY-MM-DD or 'as_of'.", pattern="^(\\d{4}-\\d{2}-\\d{2}|as_of)$"
            ),
        ] = "as_of",
        termination_date: Annotated[
            str | None,
            Field(
                description="Maturity YYYY-MM-DD; alternative to tenor.",
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        tenor: Annotated[
            str | None,
            Field(
                description=(
                    "Tenor from the start date, e.g. 5Y (resolved by the engine, Unadjusted; the "
                    "schedule applies its own conventions); alternative to the explicit end date."
                ),
                pattern="^\\d+[DWMY]$",
            ),
        ] = None,
        spread: Annotated[float, Field(description="Spread added to the YoY rate, decimal.")] = 0.0,
        frequency: Annotated[
            Frequency | None,
            Field(description="Shared fixed / YoY leg frequency; default: the preset's (annual)."),
        ] = None,
        schedule_overrides: Annotated[
            ScheduleOverrides | None,
            Field(
                description=(
                    "Overrides of the preset's schedule rules (calendar, convention, termination "
                    "convention, date generation, end of month)."
                )
            ),
        ] = None,
        additional_trades: Annotated[
            list[pi.YoyInflationSwapTrade] | None,
            Field(
                description=(
                    "More trades of the same product in the same request; the engine answers per "
                    "item."
                )
            ),
        ] = None,
        as_of: Annotated[
            str | None,
            Field(
                description=(
                    "Valuation date YYYY-MM-DD (pricing.as_of_date); required unless market is a "
                    "pricing block, which carries its own."
                ),
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        include_flows: Annotated[
            bool, Field(description="Also return the cash flows (pricing.options.*_flows).")
        ] = False,
        calendar_overrides: Annotated[
            list[CalendarOverride] | None,
            Field(
                description=(
                    "Per-request holiday corrections (engine >= 0.7.0): [{calendar, "
                    "added_holidays?, removed_holidays?}]; applies to curve helpers, indices and "
                    "schedules alike."
                )
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
        """Price a year-on-year inflation swap (POST /price-year-on-year-inflation-swap). The
        market must carry the YoY inflation index and a YoYInflation curve; fixed and YoY legs
        share the preset's schedule (annual by default) and ``spread`` is added to the YoY rate.
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

    @tool(app, "Price a year-on-year inflation cap / floor", engine=True)
    async def price_yoy_inflation_cap_floor(
        market: Annotated[
            Market,
            Field(
                description=(
                    'The market: {"session": name} (a stored market or curve), an engine pricing '
                    "block (anything with as_of_date; used verbatim) or {curves: [...], indices: "
                    "[...]} of build_curve results / TermStructure objects, plus optional "
                    "swap_indices, coupon_pricers, vol_surfaces, models, credit_curves, quotes, "
                    "equity_underlyings, inflation_indices, inflation_curves, options."
                )
            ),
        ],
        market_data_source: Annotated[MarketDataSource, Field(description=SOURCE_DESCRIPTION)],
        inflation_index_id: Annotated[
            str,
            Field(description="Id of the inflation index in pricing.inflation.inflation_indices."),
        ],
        fixings: Annotated[
            list[pi.Fixing],
            Field(
                description=(
                    "Monthly YoY index fixings [{date, value}]; REQUIRED (see "
                    "price_zc_inflation_swap)."
                )
            ),
        ],
        cap_floor_type: Annotated[CapFloorType, Field(description="Cap, Floor or Collar.")],
        notional: Annotated[
            float, Field(description="Notional amount, > 0, in the preset's currency.")
        ],
        discounting_curve: Annotated[
            str, Field(description="Id of the discounting curve in the market.")
        ],
        inflation_curve: Annotated[
            str,
            Field(description="Id of the inflation curve in pricing.inflation.inflation_curves."),
        ],
        vol: Annotated[
            str | pi.YoyVol,
            Field(
                description=(
                    "{constant: 0.01, type: Black|Bachelier|UnitDisplacedBlack, id?} (a "
                    "YoYOptionletVolSpec the tool adds with the preset's conventions) or the id "
                    "of a surface in the market."
                )
            ),
        ],
        preset: Annotated[
            str, Field(description="A preset with a yoy_inflation_cap_floor block (EUR_HICP).")
        ] = "EUR_HICP",
        cap_rate: Annotated[
            float | None, Field(description="Cap strike, decimal (Cap / Collar).")
        ] = None,
        floor_rate: Annotated[
            float | None, Field(description="Floor strike, decimal (Floor / Collar).")
        ] = None,
        effective_date: Annotated[
            str,
            Field(
                description="Start YYYY-MM-DD or 'as_of'.", pattern="^(\\d{4}-\\d{2}-\\d{2}|as_of)$"
            ),
        ] = "as_of",
        termination_date: Annotated[
            str | None,
            Field(
                description="Maturity YYYY-MM-DD; alternative to tenor.",
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        tenor: Annotated[
            str | None,
            Field(
                description=(
                    "Tenor from the start date, e.g. 5Y (resolved by the engine, Unadjusted; the "
                    "schedule applies its own conventions); alternative to the explicit end date."
                ),
                pattern="^\\d+[DWMY]$",
            ),
        ] = None,
        frequency: Annotated[
            Frequency | None, Field(description="Coupon frequency; default: the preset's.")
        ] = None,
        gearing: Annotated[
            float | None, Field(description="Gearing on the YoY rate; default: the preset's.")
        ] = None,
        spread: Annotated[
            float | None,
            Field(description="Spread on the YoY rate, decimal; default: the preset's."),
        ] = None,
        schedule_overrides: Annotated[
            ScheduleOverrides | None,
            Field(
                description=(
                    "Overrides of the preset's schedule rules (calendar, convention, termination "
                    "convention, date generation, end of month)."
                )
            ),
        ] = None,
        additional_trades: Annotated[
            list[pi.YoyInflationCapFloorTrade] | None,
            Field(
                description=(
                    "More trades of the same product in the same request; the engine answers per "
                    "item."
                )
            ),
        ] = None,
        as_of: Annotated[
            str | None,
            Field(
                description=(
                    "Valuation date YYYY-MM-DD (pricing.as_of_date); required unless market is a "
                    "pricing block, which carries its own."
                ),
                pattern="^\\d{4}-\\d{2}-\\d{2}$",
            ),
        ] = None,
        calendar_overrides: Annotated[
            list[CalendarOverride] | None,
            Field(
                description=(
                    "Per-request holiday corrections (engine >= 0.7.0): [{calendar, "
                    "added_holidays?, removed_holidays?}]; applies to curve helpers, indices and "
                    "schedules alike."
                )
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
        """Price a year-on-year inflation cap, floor or collar (POST
        /price-year-on-year-inflation-cap-floor). The YoYOptionletVolSpec the tool builds from
        ``vol`` is added with the preset's conventions; ``cap_rate`` for Cap / Collar,
        ``floor_rate`` for Floor / Collar.
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
