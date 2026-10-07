"""CDS trade builder (pure): par-spread or flat-hazard credit curve built into the
market, or an existing credit curve referenced by id."""

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
from quantra_mcp.builders.tenor import parse_tenor
from quantra_mcp.presets.registry import CdsConventions, Preset
from quantra_mcp.schema.enums_generated import (
    BusinessDayConvention,
    CdsEngineType,
    DayCounter,
    Frequency,
    ProtectionSide,
)

CDS = "/price-cds"


class ParSpreadQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenor: str | dict[str, Any]
    spread: float = Field(description="Par CDS spread, decimal (0.01 = 100bp).")


class ParSpreadCurve(BaseModel):
    """A credit curve bootstrapped by the engine from par spreads (preset helper conventions)."""

    model_config = ConfigDict(extra="forbid")

    par_spreads: list[ParSpreadQuote] = Field(min_length=1)
    recovery_rate: float | None = Field(default=None, description="Default: preset.")
    id: str | None = None


class FlatHazardCurve(BaseModel):
    """A flat hazard-rate credit curve."""

    model_config = ConfigDict(extra="forbid")

    hazard_rate: float
    recovery_rate: float | None = None
    id: str | None = None


class CdsTrade(BaseModel):
    """One single-name CDS."""

    model_config = ConfigDict(extra="forbid")

    side: ProtectionSide
    notional: float = Field(gt=0)
    running_coupon: float = Field(description="Decimal, e.g. 0.01 = 100bp.")
    start: str = Field(default="as_of", description="Effective date: YYYY-MM-DD or 'as_of'.")
    maturity: str | None = Field(default=None, description="YYYY-MM-DD.")
    tenor: str | None = Field(default=None, description="e.g. '5Y' (engine-resolved from start).")
    upfront: float | None = None
    upfront_date: str | None = None
    protection_start: str | None = Field(default=None, description="Default = start.")
    trade_date: str | None = Field(default=None, description="Default = as_of.")
    frequency: Frequency | None = None
    day_counter: DayCounter | None = None
    business_day_convention: BusinessDayConvention | None = None
    cash_settlement_days: int | None = Field(default=None, ge=0)
    schedule_overrides: ScheduleOverrides | None = None


def build_cds(
    preset: Preset,
    trade: CdsTrade,
    effective: str,
    termination: str,
    as_of: str,
    discounting_curve: str,
    credit_curve: str | ParSpreadCurve | FlatHazardCurve,
    recovery_rate: float | None,
    model: str,
) -> BuiltTrade:
    conv = preset.trade_block("cds")
    assert isinstance(conv, CdsConventions)
    d = "trades.cds"
    notes: list[str] = [f"trade conventions: {source_of(preset, d)}"]
    sched_ov = trade.schedule_overrides.wire() if trade.schedule_overrides else {}
    freq = pick(
        preset, f"{d}.frequency", conv.frequency, trade.frequency, "schedule.frequency", notes
    )
    protection_start = trade.protection_start or effective
    trade_date = trade.trade_date or as_of
    notes.append(
        f"protection_start={protection_start!r} "
        + ("(explicit)" if trade.protection_start else "(default: = effective date)")
    )
    notes.append(
        f"trade_date={trade_date!r} " + ("(explicit)" if trade.trade_date else "(default: = as_of)")
    )
    cds: dict[str, Any] = {
        "side": str(trade.side),
        "notional": check_positive(trade.notional, "notional"),
        "running_coupon": float(trade.running_coupon),
        "schedule": schedule_block(conv.schedule, effective, termination, freq, sched_ov),
    }
    if trade.upfront is not None:
        cds["upfront"] = float(trade.upfront)
        notes.append(f"upfront={trade.upfront!r} (explicit)")
        if trade.upfront_date is not None:
            cds["upfront_date"] = check_date(trade.upfront_date, "upfront_date")
    elif trade.upfront_date is not None:
        raise problem("/upfront_date", "upfront_date needs upfront")
    cds["day_counter"] = pick(
        preset, f"{d}.day_counter", conv.day_counter, trade.day_counter, "day_counter", notes
    )
    cds["business_day_convention"] = pick(
        preset,
        f"{d}.business_day_convention",
        conv.business_day_convention,
        trade.business_day_convention,
        "business_day_convention",
        notes,
    )
    cds["settles_accrual"] = pick(
        preset, f"{d}.settles_accrual", conv.settles_accrual, None, "settles_accrual", notes
    )
    cds["pays_at_default_time"] = pick(
        preset,
        f"{d}.pays_at_default_time",
        conv.pays_at_default_time,
        None,
        "pays_at_default_time",
        notes,
    )
    cds["rebates_accrual"] = pick(
        preset, f"{d}.rebates_accrual", conv.rebates_accrual, None, "rebates_accrual", notes
    )
    cds["protection_start"] = check_date(protection_start, "protection_start")
    cds["last_period_day_counter"] = pick(
        preset,
        f"{d}.last_period_day_counter",
        conv.last_period_day_counter,
        None,
        "last_period_day_counter",
        notes,
    )
    cds["trade_date"] = check_date(trade_date, "trade_date")
    cds["cash_settlement_days"] = pick(
        preset,
        f"{d}.cash_settlement_days",
        conv.cash_settlement_days,
        trade.cash_settlement_days,
        "cash_settlement_days",
        notes,
    )
    notes += schedule_notes("schedule", conv.schedule, source_of(preset, f"{d}.schedule"), sched_ov)

    additions: list[tuple[str, dict[str, Any], str]] = []
    cc = conv.credit_curve
    if isinstance(credit_curve, str):
        curve_id = credit_curve
        if recovery_rate is not None:
            raise problem(
                "/recovery_rate",
                "recovery_rate applies to a credit curve the tool builds; the referenced "
                "curve carries its own",
            )
        notes.append(
            f"credit_curve_id={curve_id!r} (explicit; must exist in pricing.credit.credit_curves)"
        )
    else:
        rr = recovery_rate if recovery_rate is not None else credit_curve.recovery_rate
        rr = pick(
            preset,
            f"{d}.credit_curve.recovery_rate",
            cc.recovery_rate,
            rr,
            "credit_curve.recovery_rate",
            notes,
        )
        spec: dict[str, Any] = {
            "id": "",
            "reference_date": as_of,
            "calendar": pick(
                preset,
                f"{d}.credit_curve.calendar",
                cc.calendar,
                None,
                "credit_curve.calendar",
                notes,
            ),
            "day_counter": pick(
                preset,
                f"{d}.credit_curve.day_counter",
                cc.day_counter,
                None,
                "credit_curve.day_counter",
                notes,
            ),
            "recovery_rate": rr,
            "curve_interpolator": pick(
                preset,
                f"{d}.credit_curve.curve_interpolator",
                cc.curve_interpolator,
                None,
                "credit_curve.curve_interpolator",
                notes,
            ),
        }
        if isinstance(credit_curve, ParSpreadCurve):
            curve_id = credit_curve.id or "credit_par_spreads"
            spec["id"] = curve_id
            hc = cc.helper_conventions
            spec["helper_conventions"] = {k: v for k, v in hc.model_dump(mode="json").items()}
            hc_src = source_of(preset, f"{d}.credit_curve.helper_conventions")
            notes += [
                f"credit_curve.helper_conventions.{k}={v!r} from {hc_src}"
                for k, v in spec["helper_conventions"].items()
            ]
            spec["quotes"] = [
                {
                    "quote_type": "ParSpread",
                    "quoted_par_spread": float(q.spread),
                    "tenor": parse_tenor(q.tenor, f"credit_curve/par_spreads/{i}/tenor"),
                }
                for i, q in enumerate(credit_curve.par_spreads)
            ]
            notes.append(
                f"credit_curve={curve_id!r}: {len(spec['quotes'])} ParSpread quotes, "
                "reference_date=as_of" + ("" if credit_curve.id else " [id default]")
            )
        else:
            curve_id = credit_curve.id or "credit_flat_hazard"
            spec["id"] = curve_id
            spec["flat_hazard_rate"] = float(credit_curve.hazard_rate)
            notes.append(
                f"credit_curve={curve_id!r}: flat_hazard_rate {credit_curve.hazard_rate} "
                "(argument), reference_date=as_of" + ("" if credit_curve.id else " [id default]")
            )
        additions.append(("credit_curves", spec, "credit curve"))

    try:
        engine_type: str | None = str(CdsEngineType(model))
    except ValueError:
        engine_type = None
    if engine_type is not None:
        model_id = f"cds_{engine_type.lower()}"
        additions.append(
            (
                "models",
                {
                    "id": model_id,
                    "payload_type": "CdsModelSpec",
                    "payload": {"engine_type": engine_type},
                },
                "model",
            )
        )
        notes.append(
            f"model={model_id!r}: CdsModelSpec engine_type {engine_type} (argument) [id default]"
        )
    else:
        model_id = model
        notes.append(f"model={model_id!r} (explicit; must exist in pricing.volatility.models)")
    item = {
        "cds": cds,
        "discounting_curve": discounting_curve,
        "credit_curve_id": curve_id,
        "model": model_id,
    }
    return BuiltTrade(item=item, additions=additions, notes=notes)


def cds_summary(response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict) or not isinstance(response.get("cds_list"), list):
        return None
    keys = ("npv", "fair_spread", "fair_upfront", "default_leg_npv", "premium_leg_npv", "error")
    return {
        "cds_list": [
            {k: c[k] for k in keys if k in c} for c in response["cds_list"] if isinstance(c, dict)
        ]
    }
