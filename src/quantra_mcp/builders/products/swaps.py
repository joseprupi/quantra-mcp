"""Vanilla (fixed vs IBOR) and OIS swap trade builders (pure)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from quantra_mcp.builders.products._common import (
    BuiltTrade,
    FixedLegOverrides,
    FloatingLegOverrides,
    check_positive,
    pick,
    schedule_block,
    schedule_notes,
    source_of,
)
from quantra_mcp.presets.registry import OisSwapConventions, Preset, VanillaSwapConventions
from quantra_mcp.schema.enums_generated import RateAveragingType, SwapType

VANILLA_SWAP = "/price-vanilla-swap"
OIS_SWAP = "/price-ois-swap"


class VanillaSwapTrade(BaseModel):
    """One fixed-vs-IBOR swap (the flat tool arguments, for batching)."""

    model_config = ConfigDict(extra="forbid")

    swap_type: SwapType
    notional: float = Field(gt=0)
    fixed_rate: float
    effective_date: str = Field(
        description="YYYY-MM-DD, or 'spot' (as_of + preset settlement days)."
    )
    termination_date: str | None = None
    tenor: str | None = Field(default=None, description="e.g. '5Y' instead of termination_date.")
    spread: float = 0.0
    index_id: str | None = Field(
        default=None, description="Floating index id in the market (default: the preset's)."
    )
    fixed_leg_overrides: FixedLegOverrides | None = None
    floating_leg_overrides: FloatingLegOverrides | None = None


class OisSwapTrade(BaseModel):
    """One OIS (fixed vs compounded overnight) swap."""

    model_config = ConfigDict(extra="forbid")

    swap_type: SwapType
    notional: float = Field(gt=0)
    fixed_rate: float
    effective_date: str
    termination_date: str | None = None
    tenor: str | None = None
    spread: float = 0.0
    index_id: str | None = None
    payment_lag: int | None = Field(
        default=None, ge=0, description="Business days; preset default."
    )
    averaging_method: RateAveragingType | None = None
    lookback_days: int | None = Field(default=None, ge=0)
    lockout_days: int | None = Field(default=None, ge=0)
    apply_observation_shift: bool | None = None
    telescopic_value_dates: bool | None = None
    fixed_leg_overrides: FixedLegOverrides | None = None
    overnight_leg_overrides: FloatingLegOverrides | None = None


def _fixed_leg(
    preset: Preset,
    dotted: str,
    conv: VanillaSwapConventions | OisSwapConventions,
    notional: float,
    rate: float,
    effective: str,
    termination: str,
    ov: FixedLegOverrides | None,
    notes: list[str],
) -> dict[str, Any]:
    f = conv.fixed_leg
    sched_ov = ov.schedule.wire() if ov and ov.schedule else {}
    freq = pick(
        preset,
        f"{dotted}.fixed_leg.frequency",
        f.frequency,
        ov.frequency if ov else None,
        "fixed_leg.frequency",
        notes,
    )
    leg: dict[str, Any] = {
        "notional": notional,
        "schedule": schedule_block(conv.schedule, effective, termination, freq, sched_ov),
        "rate": rate,
        "day_counter": pick(
            preset,
            f"{dotted}.fixed_leg.day_counter",
            f.day_counter,
            ov.day_counter if ov else None,
            "fixed_leg.day_counter",
            notes,
        ),
        "payment_convention": pick(
            preset,
            f"{dotted}.fixed_leg.payment_convention",
            f.payment_convention,
            ov.payment_convention if ov else None,
            "fixed_leg.payment_convention",
            notes,
        ),
    }
    if ov and ov.notionals is not None:
        leg["notionals"] = list(ov.notionals)
        notes.append(f"fixed_leg.notionals={ov.notionals!r} (explicit argument; per-period)")
    notes += schedule_notes(
        "fixed_leg.schedule", conv.schedule, source_of(preset, f"{dotted}.schedule"), sched_ov
    )
    return leg


def _float_leg(
    preset: Preset,
    dotted: str,
    conv: VanillaSwapConventions | OisSwapConventions,
    leg_key: str,
    notional: float,
    index_id: str,
    spread: float,
    effective: str,
    termination: str,
    ov: FloatingLegOverrides | None,
    notes: list[str],
) -> dict[str, Any]:
    f = getattr(conv, leg_key)
    sched_ov = ov.schedule.wire() if ov and ov.schedule else {}
    freq = pick(
        preset,
        f"{dotted}.{leg_key}.frequency",
        f.frequency,
        ov.frequency if ov else None,
        f"{leg_key}.frequency",
        notes,
    )
    leg: dict[str, Any] = {
        "notional": notional,
        "schedule": schedule_block(conv.schedule, effective, termination, freq, sched_ov),
        "index": {"id": index_id},
        "spread": spread,
        "day_counter": pick(
            preset,
            f"{dotted}.{leg_key}.day_counter",
            f.day_counter,
            ov.day_counter if ov else None,
            f"{leg_key}.day_counter",
            notes,
        ),
        "payment_convention": pick(
            preset,
            f"{dotted}.{leg_key}.payment_convention",
            f.payment_convention,
            ov.payment_convention if ov else None,
            f"{leg_key}.payment_convention",
            notes,
        ),
    }
    if ov and ov.notionals is not None:
        leg["notionals"] = list(ov.notionals)
        notes.append(f"{leg_key}.notionals={ov.notionals!r} (explicit argument; per-period)")
    if ov and ov.fixing_days is not None:
        leg["fixing_days"] = ov.fixing_days
        notes.append(f"{leg_key}.fixing_days={ov.fixing_days} (explicit argument)")
    else:
        notes.append(f"{leg_key}.fixing_days not sent: the engine uses the index's fixing_days")
    if ov and ov.in_arrears is not None:
        leg["in_arrears"] = ov.in_arrears
        notes.append(f"{leg_key}.in_arrears={ov.in_arrears} (explicit argument)")
    notes += schedule_notes(
        f"{leg_key}.schedule", conv.schedule, source_of(preset, f"{dotted}.schedule"), sched_ov
    )
    return leg


def build_vanilla_swap(
    preset: Preset,
    trade: VanillaSwapTrade,
    effective: str,
    termination: str,
    index_id: str,
    discounting_curve: str,
    forwarding_curve: str,
) -> BuiltTrade:
    conv = preset.trade_block("vanilla_swap")
    assert isinstance(conv, VanillaSwapConventions)
    notes: list[str] = [f"trade conventions: {source_of(preset, 'trades.vanilla_swap')}"]
    notional = check_positive(trade.notional, "notional")
    fixed = _fixed_leg(
        preset,
        "trades.vanilla_swap",
        conv,
        notional,
        float(trade.fixed_rate),
        effective,
        termination,
        trade.fixed_leg_overrides,
        notes,
    )
    floating = _float_leg(
        preset,
        "trades.vanilla_swap",
        conv,
        "floating_leg",
        notional,
        index_id,
        float(trade.spread),
        effective,
        termination,
        trade.floating_leg_overrides,
        notes,
    )
    notes.append(f"floating_leg.spread={float(trade.spread)!r} (argument; default 0.0)")
    item = {
        "vanilla_swap": {
            "swap_type": str(trade.swap_type),
            "fixed_leg": fixed,
            "floating_leg": floating,
        },
        "discounting_curve": discounting_curve,
        "forwarding_curve": forwarding_curve,
    }
    return BuiltTrade(item=item, notes=notes)


def build_ois_swap(
    preset: Preset,
    trade: OisSwapTrade,
    effective: str,
    termination: str,
    index_id: str,
    discounting_curve: str,
    forwarding_curve: str,
) -> BuiltTrade:
    conv = preset.trade_block("ois_swap")
    assert isinstance(conv, OisSwapConventions)
    d = "trades.ois_swap"
    notes: list[str] = [f"trade conventions: {source_of(preset, d)}"]
    notional = check_positive(trade.notional, "notional")
    fixed = _fixed_leg(
        preset,
        d,
        conv,
        notional,
        float(trade.fixed_rate),
        effective,
        termination,
        trade.fixed_leg_overrides,
        notes,
    )
    overnight = _float_leg(
        preset,
        d,
        conv,
        "overnight_leg",
        notional,
        index_id,
        float(trade.spread),
        effective,
        termination,
        trade.overnight_leg_overrides,
        notes,
    )
    o = conv.overnight_leg
    overnight["payment_calendar"] = pick(
        preset,
        f"{d}.overnight_leg.payment_calendar",
        o.payment_calendar,
        None,
        "overnight_leg.payment_calendar",
        notes,
    )
    overnight["averaging_method"] = pick(
        preset,
        f"{d}.overnight_leg.averaging_method",
        o.averaging_method,
        trade.averaging_method,
        "overnight_leg.averaging_method",
        notes,
    )
    overnight["lockout_days"] = pick(
        preset,
        f"{d}.overnight_leg.lockout_days",
        o.lockout_days,
        trade.lockout_days,
        "overnight_leg.lockout_days",
        notes,
    )
    overnight["apply_observation_shift"] = pick(
        preset,
        f"{d}.overnight_leg.apply_observation_shift",
        o.apply_observation_shift,
        trade.apply_observation_shift,
        "overnight_leg.apply_observation_shift",
        notes,
    )
    overnight["telescopic_value_dates"] = pick(
        preset,
        f"{d}.overnight_leg.telescopic_value_dates",
        o.telescopic_value_dates,
        trade.telescopic_value_dates,
        "overnight_leg.telescopic_value_dates",
        notes,
    )
    overnight["payment_lag"] = pick(
        preset,
        f"{d}.overnight_leg.payment_lag",
        o.payment_lag,
        trade.payment_lag,
        "overnight_leg.payment_lag",
        notes,
    )
    overnight["lookback_days"] = pick(
        preset,
        f"{d}.overnight_leg.lookback_days",
        o.lookback_days,
        trade.lookback_days,
        "overnight_leg.lookback_days",
        notes,
    )
    notes.append(f"overnight_leg.spread={float(trade.spread)!r} (argument; default 0.0)")
    notes.append(
        "overnight_leg.lookback_days=0 means no lookback (QuantLib null), per engine 0.6.0"
    )
    item = {
        "ois_swap": {
            "swap_type": str(trade.swap_type),
            "fixed_leg": fixed,
            "overnight_leg": overnight,
        },
        "discounting_curve": discounting_curve,
        "forwarding_curve": forwarding_curve,
    }
    return BuiltTrade(item=item, notes=notes)


def swap_summary(response: Any) -> dict[str, Any] | None:
    """Per swap: npv, fair_rate, fair_spread, leg npvs (a selection; nothing computed)."""
    if not isinstance(response, dict) or not isinstance(response.get("swaps"), list):
        return None
    keys = (
        "npv",
        "fair_rate",
        "fair_spread",
        "fixed_leg_npv",
        "floating_leg_npv",
        "overnight_leg_npv",
        "error",
    )
    return {
        "swaps": [
            {k: s[k] for k in keys if k in s} for s in response["swaps"] if isinstance(s, dict)
        ]
    }


__all__ = [
    "OIS_SWAP",
    "VANILLA_SWAP",
    "OisSwapTrade",
    "VanillaSwapTrade",
    "build_ois_swap",
    "build_vanilla_swap",
    "swap_summary",
]
