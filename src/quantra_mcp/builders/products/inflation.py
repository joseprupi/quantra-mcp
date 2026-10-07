"""Inflation swap and cap/floor builders (pure, thin, fixture-shaped).

Base fixings are a REQUIRED argument: the engine prices the inflation leg off
the CPI (or YoY) fixing at ``start - observation_lag`` and the first helper
pillars of the inflation curve, and this server has no market-data source to
fetch them from. The tool sets them on the trade's inflation index in the
market and says so in ``notes``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from quantra_mcp.builders.products._common import (
    BuiltTrade,
    ScheduleOverrides,
    check_positive,
    pick,
    schedule_block,
    schedule_notes,
    source_of,
)
from quantra_mcp.builders.schedule import check_date, problem
from quantra_mcp.presets.registry import (
    Preset,
    YoyInflationCapFloorConventions,
    YoyInflationSwapConventions,
    ZcInflationSwapConventions,
)
from quantra_mcp.schema.enums_generated import (
    CapFloorType,
    Frequency,
    SwapType,
    YoYInflationCapFloorEngineType,
)

ZC_INFLATION_SWAP = "/price-zero-coupon-inflation-swap"
YOY_INFLATION_SWAP = "/price-year-on-year-inflation-swap"
YOY_INFLATION_CAP_FLOOR = "/price-year-on-year-inflation-cap-floor"


class Fixing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    value: float


class ZcInflationSwapTrade(BaseModel):
    model_config = ConfigDict(extra="forbid")

    swap_type: SwapType
    notional: float = Field(gt=0)
    fixed_rate: float
    start_date: str = Field(default="as_of", description="YYYY-MM-DD or 'as_of'.")
    maturity_date: str | None = None
    tenor: str | None = None


class YoyInflationSwapTrade(BaseModel):
    model_config = ConfigDict(extra="forbid")

    swap_type: SwapType
    notional: float = Field(gt=0)
    fixed_rate: float
    effective_date: str = Field(default="as_of", description="YYYY-MM-DD or 'as_of'.")
    termination_date: str | None = None
    tenor: str | None = None
    spread: float = 0.0
    frequency: Frequency | None = None
    schedule_overrides: ScheduleOverrides | None = None


class YoyVol(BaseModel):
    """A constant YoY optionlet vol built into the market."""

    model_config = ConfigDict(extra="forbid")

    constant: float
    type: YoYInflationCapFloorEngineType = Field(
        description="Black | Bachelier | UnitDisplacedBlack."
    )
    id: str | None = None


class YoyInflationCapFloorTrade(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cap_floor_type: CapFloorType
    notional: float = Field(gt=0)
    cap_rate: float | None = None
    floor_rate: float | None = None
    effective_date: str = Field(default="as_of")
    termination_date: str | None = None
    tenor: str | None = None
    frequency: Frequency | None = None
    gearing: float | None = None
    spread: float | None = None
    schedule_overrides: ScheduleOverrides | None = None


def fixings_wire(fixings: list[Fixing]) -> list[dict[str, Any]]:
    if not fixings:
        raise problem(
            "/fixings", "fixings: at least one base fixing is required (see the tool docstring)"
        )
    return [
        {"date": check_date(f.date, f"fixings/{i}/date"), "value": float(f.value)}
        for i, f in enumerate(fixings)
    ]


def _period(p: Any) -> dict[str, Any]:
    return {"n": p.n, "unit": str(p.unit)}


def build_zc_inflation_swap(
    preset: Preset,
    trade: ZcInflationSwapTrade,
    start: str,
    maturity: str,
    inflation_index_id: str,
    discounting_curve: str,
    inflation_curve: str,
) -> BuiltTrade:
    conv = preset.trade_block("zc_inflation_swap")
    assert isinstance(conv, ZcInflationSwapConventions)
    d = "trades.zc_inflation_swap"
    notes: list[str] = [f"trade conventions: {source_of(preset, d)}"]
    swap: dict[str, Any] = {
        "swap_type": str(trade.swap_type),
        "notional": check_positive(trade.notional, "notional"),
        "start_date": check_date(start, "start_date"),
        "maturity_date": check_date(maturity, "maturity_date"),
        "fixed_calendar": pick(
            preset, f"{d}.fixed_calendar", conv.fixed_calendar, None, "fixed_calendar", notes
        ),
        "fixed_convention": pick(
            preset, f"{d}.fixed_convention", conv.fixed_convention, None, "fixed_convention", notes
        ),
        "day_counter": pick(
            preset, f"{d}.day_counter", conv.day_counter, None, "day_counter", notes
        ),
        "fixed_rate": float(trade.fixed_rate),
        "inflation_index_id": inflation_index_id,
        "observation_lag": _period(conv.observation_lag),
        "observation_interpolation": pick(
            preset,
            f"{d}.observation_interpolation",
            conv.observation_interpolation,
            None,
            "observation_interpolation",
            notes,
        ),
        "adjust_observation_dates": pick(
            preset,
            f"{d}.adjust_observation_dates",
            conv.adjust_observation_dates,
            None,
            "adjust_observation_dates",
            notes,
        ),
        "inflation_calendar": pick(
            preset,
            f"{d}.inflation_calendar",
            conv.inflation_calendar,
            None,
            "inflation_calendar",
            notes,
        ),
        "inflation_convention": pick(
            preset,
            f"{d}.inflation_convention",
            conv.inflation_convention,
            None,
            "inflation_convention",
            notes,
        ),
    }
    notes.append(
        f"observation_lag={_period(conv.observation_lag)!r} "
        f"from {source_of(preset, f'{d}.observation_lag')}"
    )
    item = {
        "zero_coupon_inflation_swap": swap,
        "discounting_curve": discounting_curve,
        "inflation_curve": inflation_curve,
    }
    return BuiltTrade(item=item, notes=notes)


def build_yoy_inflation_swap(
    preset: Preset,
    trade: YoyInflationSwapTrade,
    effective: str,
    termination: str,
    inflation_index_id: str,
    discounting_curve: str,
    inflation_curve: str,
) -> BuiltTrade:
    conv = preset.trade_block("yoy_inflation_swap")
    assert isinstance(conv, YoyInflationSwapConventions)
    d = "trades.yoy_inflation_swap"
    notes: list[str] = [f"trade conventions: {source_of(preset, d)}"]
    sched_ov = trade.schedule_overrides.wire() if trade.schedule_overrides else {}
    freq = pick(
        preset, f"{d}.frequency", conv.frequency, trade.frequency, "schedule.frequency", notes
    )
    sched = schedule_block(conv.schedule, effective, termination, freq, sched_ov)
    notes += schedule_notes(
        "fixed_schedule/yoy_schedule", conv.schedule, source_of(preset, f"{d}.schedule"), sched_ov
    )
    swap: dict[str, Any] = {
        "swap_type": str(trade.swap_type),
        "notional": check_positive(trade.notional, "notional"),
        "fixed_schedule": sched,
        "fixed_rate": float(trade.fixed_rate),
        "fixed_day_counter": pick(
            preset,
            f"{d}.fixed_day_counter",
            conv.fixed_day_counter,
            None,
            "fixed_day_counter",
            notes,
        ),
        "yoy_schedule": dict(sched),
        "inflation_index_id": inflation_index_id,
        "observation_lag": _period(conv.observation_lag),
        "observation_interpolation": pick(
            preset,
            f"{d}.observation_interpolation",
            conv.observation_interpolation,
            None,
            "observation_interpolation",
            notes,
        ),
        "spread": float(trade.spread),
        "yoy_day_counter": pick(
            preset, f"{d}.yoy_day_counter", conv.yoy_day_counter, None, "yoy_day_counter", notes
        ),
        "payment_calendar": pick(
            preset, f"{d}.payment_calendar", conv.payment_calendar, None, "payment_calendar", notes
        ),
        "payment_convention": pick(
            preset,
            f"{d}.payment_convention",
            conv.payment_convention,
            None,
            "payment_convention",
            notes,
        ),
    }
    notes.append(
        f"observation_lag={_period(conv.observation_lag)!r} "
        f"from {source_of(preset, f'{d}.observation_lag')}; "
        f"spread={float(trade.spread)!r} (argument)"
    )
    item = {
        "year_on_year_inflation_swap": swap,
        "discounting_curve": discounting_curve,
        "inflation_curve": inflation_curve,
    }
    return BuiltTrade(item=item, notes=notes)


def build_yoy_inflation_cap_floor(
    preset: Preset,
    trade: YoyInflationCapFloorTrade,
    effective: str,
    termination: str,
    inflation_index_id: str,
    discounting_curve: str,
    inflation_curve: str,
    vol: str | YoyVol,
) -> BuiltTrade:
    conv = preset.trade_block("yoy_inflation_cap_floor")
    assert isinstance(conv, YoyInflationCapFloorConventions)
    d = "trades.yoy_inflation_cap_floor"
    notes: list[str] = [f"trade conventions: {source_of(preset, d)}"]
    t = trade.cap_floor_type
    if t in (CapFloorType.Cap, CapFloorType.Collar) and trade.cap_rate is None:
        raise problem("/cap_rate", f"{t} needs cap_rate")
    if t in (CapFloorType.Floor, CapFloorType.Collar) and trade.floor_rate is None:
        raise problem("/floor_rate", f"{t} needs floor_rate")
    if t == CapFloorType.Cap and trade.floor_rate is not None:
        raise problem("/floor_rate", "a Cap takes no floor_rate")
    if t == CapFloorType.Floor and trade.cap_rate is not None:
        raise problem("/cap_rate", "a Floor takes no cap_rate")
    sched_ov = trade.schedule_overrides.wire() if trade.schedule_overrides else {}
    freq = pick(
        preset, f"{d}.frequency", conv.frequency, trade.frequency, "schedule.frequency", notes
    )
    cf: dict[str, Any] = {
        "cap_floor_type": str(t),
        "notional": check_positive(trade.notional, "notional"),
        "schedule": schedule_block(conv.schedule, effective, termination, freq, sched_ov),
        "inflation_index_id": inflation_index_id,
        "observation_lag": _period(conv.observation_lag),
        "day_counter": pick(
            preset, f"{d}.day_counter", conv.day_counter, None, "day_counter", notes
        ),
        "payment_convention": pick(
            preset,
            f"{d}.payment_convention",
            conv.payment_convention,
            None,
            "payment_convention",
            notes,
        ),
    }
    if trade.cap_rate is not None:
        cf["cap_rate"] = float(trade.cap_rate)
    if trade.floor_rate is not None:
        cf["floor_rate"] = float(trade.floor_rate)
    if trade.gearing is not None:
        cf["gearing"] = float(trade.gearing)
        notes.append(f"gearing={trade.gearing!r} (explicit)")
    if trade.spread is not None:
        cf["spread"] = float(trade.spread)
        notes.append(f"spread={trade.spread!r} (explicit)")
    notes += schedule_notes("schedule", conv.schedule, source_of(preset, f"{d}.schedule"), sched_ov)
    notes.append(
        f"observation_lag={_period(conv.observation_lag)!r} "
        f"from {source_of(preset, f'{d}.observation_lag')}"
    )
    additions: list[tuple[str, dict[str, Any], str]] = []
    if isinstance(vol, YoyVol):
        vol_id = vol.id or f"yoy_vol_{str(vol.type).lower()}"
        v = conv.vol
        payload = {
            "constant_vol": float(vol.constant),
            "vol_type": str(vol.type),
            "day_counter": str(v.day_counter),
            "calendar": str(v.calendar),
            "business_day_convention": str(v.business_day_convention),
            "observation_lag": _period(v.observation_lag),
        }
        additions.append(
            (
                "vol_surfaces",
                {"id": vol_id, "payload_type": "YoYOptionletVolSpec", "payload": payload},
                "vol surface",
            )
        )
        notes.append(
            f"volatility={vol_id!r}: YoYOptionletVolSpec constant {vol.constant} {vol.type} "
            f"(arguments); conventions from {source_of(preset, f'{d}.vol')}"
            + ("" if vol.id else " [id default]")
        )
    else:
        vol_id = vol
        notes.append(
            f"volatility={vol_id!r} (explicit; must exist in pricing.volatility.vol_surfaces)"
        )
    item = {
        "year_on_year_inflation_cap_floor": cf,
        "discounting_curve": discounting_curve,
        "inflation_curve": inflation_curve,
        "volatility": vol_id,
    }
    return BuiltTrade(item=item, additions=additions, notes=notes)


def inflation_summary(response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict):
        return None
    for key in ("swaps", "cap_floors"):
        if isinstance(response.get(key), list):
            keys = (
                "npv",
                "fair_rate",
                "fair_spread",
                "atm_rate",
                "fixed_leg_npv",
                "inflation_leg_npv",
                "yoy_leg_npv",
                "error",
            )
            return {
                key: [
                    {k: s[k] for k in keys if k in s} for s in response[key] if isinstance(s, dict)
                ]
            }
    return None
