"""FRA and cap/floor trade builders (pure)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from quantra_mcp.builders.products._common import (
    BuiltTrade,
    ConstantVol,
    ScheduleOverrides,
    check_positive,
    default_model_id,
    model_type_or_id,
    pick,
    schedule_block,
    schedule_notes,
    source_of,
    vol_base,
)
from quantra_mcp.builders.schedule import check_date, problem
from quantra_mcp.presets.registry import CapFloorConventions, FraTradeConventions, Preset
from quantra_mcp.schema.enums_generated import (
    BusinessDayConvention,
    CapFloorType,
    DayCounter,
    FRAType,
    Frequency,
    IrModelType,
)

FRA = "/price-fra"
CAP_FLOOR = "/price-cap-floor"


class FraTrade(BaseModel):
    """One forward rate agreement. Give ``months_to_start``/``months_to_end`` (3x6 = 3, 6;
    dates resolved by the engine from spot) or explicit ``start_date``/``maturity_date``."""

    model_config = ConfigDict(extra="forbid")

    side: FRAType = Field(description="Long = pay fixed (buy the FRA), Short = receive fixed.")
    notional: float = Field(gt=0)
    strike: float
    months_to_start: int | None = Field(default=None, ge=0)
    months_to_end: int | None = Field(default=None, gt=0)
    start_date: str | None = None
    maturity_date: str | None = None
    index_id: str | None = None
    day_counter: DayCounter | None = None
    calendar: str | None = None
    business_day_convention: BusinessDayConvention | None = None


class CapFloorTrade(BaseModel):
    """One cap, floor or collar on an IBOR index."""

    model_config = ConfigDict(extra="forbid")

    cap_floor_type: CapFloorType
    notional: float = Field(gt=0)
    strike: float
    effective_date: str
    termination_date: str | None = None
    tenor: str | None = None
    index_id: str | None = None
    frequency: Frequency | None = None
    day_counter: DayCounter | None = None
    business_day_convention: BusinessDayConvention | None = None
    schedule_overrides: ScheduleOverrides | None = None
    include_details: bool = False


def build_fra(
    preset: Preset,
    trade: FraTrade,
    start: str,
    maturity: str,
    index_id: str,
    discounting_curve: str,
    forwarding_curve: str,
) -> BuiltTrade:
    conv = preset.trade_block("fra")
    assert isinstance(conv, FraTradeConventions)
    d = "trades.fra"
    notes: list[str] = [f"trade conventions: {source_of(preset, d)}"]
    fra: dict[str, Any] = {
        "fra_type": str(trade.side),
        "notional": check_positive(trade.notional, "notional"),
        "start_date": check_date(start, "start_date"),
        "maturity_date": check_date(maturity, "maturity_date"),
        "strike": float(trade.strike),
        "index": {"id": index_id},
        "day_counter": pick(
            preset, f"{d}.day_counter", conv.day_counter, trade.day_counter, "day_counter", notes
        ),
        "calendar": pick(preset, f"{d}.calendar", conv.calendar, trade.calendar, "calendar", notes),
        "business_day_convention": pick(
            preset,
            f"{d}.business_day_convention",
            conv.business_day_convention,
            trade.business_day_convention,
            "business_day_convention",
            notes,
        ),
    }
    item = {
        "fra": fra,
        "discounting_curve": discounting_curve,
        "forwarding_curve": forwarding_curve,
    }
    return BuiltTrade(item=item, notes=notes)


def build_cap_floor(
    preset: Preset,
    trade: CapFloorTrade,
    effective: str,
    termination: str,
    index_id: str,
    discounting_curve: str,
    forwarding_curve: str,
    vol: str | ConstantVol,
    model: str,
    as_of: str,
) -> BuiltTrade:
    conv = preset.trade_block("cap_floor")
    assert isinstance(conv, CapFloorConventions)
    d = "trades.cap_floor"
    notes: list[str] = [f"trade conventions: {source_of(preset, d)}"]
    sched_ov = trade.schedule_overrides.wire() if trade.schedule_overrides else {}
    freq = pick(
        preset, f"{d}.frequency", conv.frequency, trade.frequency, "schedule.frequency", notes
    )
    cf: dict[str, Any] = {
        "cap_floor_type": str(trade.cap_floor_type),
        "notional": check_positive(trade.notional, "notional"),
        "strike": float(trade.strike),
        "schedule": schedule_block(conv.schedule, effective, termination, freq, sched_ov),
        "index": {"id": index_id},
        "day_counter": pick(
            preset, f"{d}.day_counter", conv.day_counter, trade.day_counter, "day_counter", notes
        ),
        "business_day_convention": pick(
            preset,
            f"{d}.business_day_convention",
            conv.business_day_convention,
            trade.business_day_convention,
            "business_day_convention",
            notes,
        ),
    }
    notes += schedule_notes("schedule", conv.schedule, source_of(preset, f"{d}.schedule"), sched_ov)
    additions: list[tuple[str, dict[str, Any], str]] = []
    if isinstance(vol, ConstantVol):
        vol_id = vol.id or f"vol_{str(vol.type).lower()}_{vol.constant:g}".replace(".", "p")
        surface = {
            "id": vol_id,
            "payload_type": "OptionletVolSpec",
            "payload": {
                "base": vol_base(
                    conv.vol_base,
                    as_of,
                    str(vol.type),
                    float(vol.displacement),
                    float(vol.constant),
                    None,
                )
            },
        }
        additions.append(("vol_surfaces", surface, "vol surface"))
        notes.append(
            f"volatility={vol_id!r}: OptionletVolSpec constant {vol.constant} {vol.type} "
            f"displacement {vol.displacement} (arguments); base reference_date=as_of, "
            f"calendar/convention/day_counter from {source_of(preset, f'{d}.vol_base')}"
            + ("" if vol.id else " [id default]")
        )
    else:
        vol_id = vol
        notes.append(
            f"volatility={vol_id!r} (explicit; must exist in pricing.volatility.vol_surfaces)"
        )
    model_type, model_id = model_type_or_id(model, IrModelType)
    if model_type is not None:
        model_id = default_model_id(model_type)
        additions.append(
            (
                "models",
                {
                    "id": model_id,
                    "payload_type": "CapFloorModelSpec",
                    "payload": {"model_type": model_type},
                },
                "model",
            )
        )
        notes.append(f"model={model_id!r}: CapFloorModelSpec {model_type} (argument) [id default]")
    else:
        notes.append(f"model={model_id!r} (explicit; must exist in pricing.volatility.models)")
    item: dict[str, Any] = {
        "cap_floor": cf,
        "discounting_curve": discounting_curve,
        "forwarding_curve": forwarding_curve,
        "volatility": vol_id,
        "model": model_id,
    }
    if trade.include_details:
        item["include_details"] = True
    return BuiltTrade(item=item, additions=additions, notes=notes)


def fra_summary(response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict) or not isinstance(response.get("fras"), list):
        return None
    keys = ("npv", "forward_rate", "spot_value", "settlement_date", "error")
    return {
        "fras": [{k: f[k] for k in keys if k in f} for f in response["fras"] if isinstance(f, dict)]
    }


def cap_floor_summary(response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict) or not isinstance(response.get("cap_floors"), list):
        return None
    keys = ("npv", "atm_rate", "implied_volatility", "error")
    return {
        "cap_floors": [
            {k: c[k] for k in keys if k in c} for c in response["cap_floors"] if isinstance(c, dict)
        ]
    }


def check_fra_dates(trade: FraTrade) -> None:
    by_months = trade.months_to_start is not None or trade.months_to_end is not None
    by_dates = trade.start_date is not None or trade.maturity_date is not None
    if by_months == by_dates:
        raise problem(
            "/months_to_start",
            "give months_to_start + months_to_end (resolved from spot by the engine) OR "
            "start_date + maturity_date",
        )
    if by_months and (
        trade.months_to_start is None
        or trade.months_to_end is None
        or trade.months_to_start >= trade.months_to_end
    ):
        raise problem("/months_to_end", "need 0 <= months_to_start < months_to_end (3x6 = 3, 6)")
    if by_dates and (trade.start_date is None or trade.maturity_date is None):
        raise problem("/start_date", "start_date and maturity_date go together")
