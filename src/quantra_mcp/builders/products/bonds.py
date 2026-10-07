"""Bond trade builders: fixed-rate, floating-rate, zero-coupon, callable (pure)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from quantra_mcp.builders.products._common import (
    BuiltTrade,
    HullWhiteModel,
    ScheduleOverrides,
    check_positive,
    default_model_id,
    pick,
    schedule_block,
    schedule_notes,
    source_of,
)
from quantra_mcp.builders.schedule import check_date, problem
from quantra_mcp.presets.registry import (
    CallableBondConventions,
    FixedRateBondConventions,
    FloatingRateBondConventions,
    Preset,
    YieldConventions,
    ZeroCouponBondConventions,
)
from quantra_mcp.schema.enums_generated import (
    BusinessDayConvention,
    CallabilityType,
    DayCounter,
    Frequency,
)

FIXED_RATE_BOND = "/price-fixed-rate-bond"
FLOATING_RATE_BOND = "/price-floating-rate-bond"
ZERO_COUPON_BOND = "/price-zero-coupon-bond"
CALLABLE_FIXED_RATE_BOND = "/price-callable-fixed-rate-bond"


class BondOverrides(BaseModel):
    """Replace bond conventions (every field optional)."""

    model_config = ConfigDict(extra="forbid")

    settlement_days: int | None = Field(default=None, ge=0)
    frequency: Frequency | None = None
    accrual_day_counter: DayCounter | None = None
    payment_convention: BusinessDayConvention | None = None
    redemption: float | None = None
    notionals: list[float] | None = None
    schedule: ScheduleOverrides | None = None


class YieldOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day_counter: DayCounter | None = None
    compounding: str | None = None
    frequency: Frequency | None = None


class FixedRateBondTrade(BaseModel):
    """One fixed-rate bond."""

    model_config = ConfigDict(extra="forbid")

    face_amount: float = Field(gt=0)
    coupon_rate: float = Field(description="Annual coupon rate, e.g. 0.031.")
    issue_date: str = Field(description="YYYY-MM-DD or 'spot' (as_of + preset settlement days).")
    maturity_date: str | None = None
    tenor: str | None = None
    effective_date: str | None = Field(
        default=None, description="First accrual date; default = issue_date."
    )
    overrides: BondOverrides | None = None
    yield_overrides: YieldOverrides | None = None


class FloatingRateBondTrade(BaseModel):
    """One floating-rate note."""

    model_config = ConfigDict(extra="forbid")

    face_amount: float = Field(gt=0)
    issue_date: str
    maturity_date: str | None = None
    tenor: str | None = None
    effective_date: str | None = None
    spread: float = 0.0
    index_id: str | None = None
    fixing_days: int | None = Field(default=None, ge=0)
    in_arrears: bool | None = None
    overrides: BondOverrides | None = None


class ZeroCouponBondTrade(BaseModel):
    """One zero-coupon bond."""

    model_config = ConfigDict(extra="forbid")

    face_amount: float = Field(gt=0)
    maturity_date: str | None = None
    tenor: str | None = None
    issue_date: str | None = Field(default=None, description="YYYY-MM-DD, 'as_of' or omitted.")
    settlement_days: int | None = Field(default=None, ge=0)
    redemption: float | None = None
    yield_overrides: YieldOverrides | None = None


class Callability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    price: float = Field(gt=0, description="Clean call/put price per 100 of face.")
    type: CallabilityType = CallabilityType.Call


class CallableBondTrade(BaseModel):
    """One callable / puttable fixed-rate bond."""

    model_config = ConfigDict(extra="forbid")

    face_amount: float = Field(gt=0)
    coupon_rate: float
    issue_date: str
    maturity_date: str | None = None
    tenor: str | None = None
    effective_date: str | None = None
    call_schedule: list[Callability] = Field(min_length=1)
    overrides: BondOverrides | None = None
    tree_steps: int | None = Field(default=None, gt=0)


def _yield_block(
    preset: Preset, dotted: str, conv: YieldConventions, ov: YieldOverrides | None, notes: list[str]
) -> dict[str, Any]:
    return {
        "day_counter": pick(
            preset,
            f"{dotted}.day_counter",
            conv.day_counter,
            ov.day_counter if ov else None,
            "yield.day_counter",
            notes,
        ),
        "compounding": pick(
            preset,
            f"{dotted}.compounding",
            conv.compounding,
            ov.compounding if ov else None,
            "yield.compounding",
            notes,
        ),
        "frequency": pick(
            preset,
            f"{dotted}.frequency",
            conv.frequency,
            ov.frequency if ov else None,
            "yield.frequency",
            notes,
        ),
    }


def _coupon_bond_body(
    preset: Preset,
    dotted: str,
    conv: FixedRateBondConventions | CallableBondConventions | FloatingRateBondConventions,
    face: float,
    issue: str,
    effective: str,
    maturity: str,
    ov: BondOverrides | None,
    notes: list[str],
) -> dict[str, Any]:
    sched_ov = ov.schedule.wire() if ov and ov.schedule else {}
    body: dict[str, Any] = {
        "settlement_days": pick(
            preset,
            f"{dotted}.settlement_days",
            conv.settlement_days,
            ov.settlement_days if ov else None,
            "settlement_days",
            notes,
        ),
        "face_amount": face,
    }
    freq = pick(
        preset,
        f"{dotted}.frequency",
        conv.frequency,
        ov.frequency if ov else None,
        "schedule.frequency",
        notes,
    )
    body["_schedule"] = schedule_block(conv.schedule, effective, maturity, freq, sched_ov)
    notes += schedule_notes(
        "schedule", conv.schedule, source_of(preset, f"{dotted}.schedule"), sched_ov
    )
    body["accrual_day_counter"] = pick(
        preset,
        f"{dotted}.accrual_day_counter",
        conv.accrual_day_counter,
        ov.accrual_day_counter if ov else None,
        "accrual_day_counter",
        notes,
    )
    body["payment_convention"] = pick(
        preset,
        f"{dotted}.payment_convention",
        conv.payment_convention,
        ov.payment_convention if ov else None,
        "payment_convention",
        notes,
    )
    body["redemption"] = pick(
        preset,
        f"{dotted}.redemption",
        conv.redemption,
        ov.redemption if ov else None,
        "redemption",
        notes,
    )
    body["issue_date"] = issue
    notes.append(f"issue_date={issue!r}; schedule.effective_date={effective!r}")
    if ov and ov.notionals is not None:
        body["notionals"] = list(ov.notionals)
        notes.append(f"notionals={ov.notionals!r} (explicit argument; per-period)")
    return body


def build_fixed_rate_bond(
    preset: Preset,
    trade: FixedRateBondTrade,
    issue: str,
    effective: str,
    maturity: str,
    discounting_curve: str,
) -> BuiltTrade:
    conv = preset.trade_block("fixed_rate_bond")
    assert isinstance(conv, FixedRateBondConventions)
    d = "trades.fixed_rate_bond"
    notes: list[str] = [f"trade conventions: {source_of(preset, d)}"]
    body = _coupon_bond_body(
        preset,
        d,
        conv,
        check_positive(trade.face_amount, "face_amount"),
        issue,
        effective,
        maturity,
        trade.overrides,
        notes,
    )
    schedule = body.pop("_schedule")
    bond: dict[str, Any] = {
        "settlement_days": body["settlement_days"],
        "face_amount": body["face_amount"],
        "rate": float(trade.coupon_rate),
        "accrual_day_counter": body["accrual_day_counter"],
        "payment_convention": body["payment_convention"],
        "redemption": body["redemption"],
        "issue_date": body["issue_date"],
        "schedule": schedule,
    }
    if "notionals" in body:
        bond["notionals"] = body["notionals"]
    item = {
        "fixed_rate_bond": bond,
        "discounting_curve": discounting_curve,
        "yield": _yield_block(preset, f"{d}.yield", conv.yield_, trade.yield_overrides, notes),
    }
    return BuiltTrade(item=item, notes=notes)


def build_floating_rate_bond(
    preset: Preset,
    trade: FloatingRateBondTrade,
    issue: str,
    effective: str,
    maturity: str,
    index_id: str,
    discounting_curve: str,
    forwarding_curve: str,
    coupon_pricer: str | None,
    as_of: str,
) -> BuiltTrade:
    conv = preset.trade_block("floating_rate_bond")
    assert isinstance(conv, FloatingRateBondConventions)
    d = "trades.floating_rate_bond"
    notes: list[str] = [f"trade conventions: {source_of(preset, d)}"]
    body = _coupon_bond_body(
        preset,
        d,
        conv,
        check_positive(trade.face_amount, "face_amount"),
        issue,
        effective,
        maturity,
        trade.overrides,
        notes,
    )
    schedule = body.pop("_schedule")
    bond: dict[str, Any] = {
        "settlement_days": body["settlement_days"],
        "face_amount": body["face_amount"],
        "schedule": schedule,
        "index": {"id": index_id},
        "accrual_day_counter": body["accrual_day_counter"],
        "payment_convention": body["payment_convention"],
        "fixing_days": pick(
            preset, f"{d}.fixing_days", conv.fixing_days, trade.fixing_days, "fixing_days", notes
        ),
        "spread": float(trade.spread),
        "in_arrears": pick(
            preset, f"{d}.in_arrears", conv.in_arrears, trade.in_arrears, "in_arrears", notes
        ),
        "redemption": body["redemption"],
        "issue_date": body["issue_date"],
    }
    if "notionals" in body:
        bond["notionals"] = body["notionals"]
    notes.append(f"spread={float(trade.spread)!r} (argument; default 0.0)")
    additions: list[tuple[str, dict[str, Any], str]] = []
    if coupon_pricer is None:
        cp = conv.coupon_pricer
        pricer = {
            "id": cp.id,
            "black_ibor_coupon_pricer": {
                "optionlet_volatility": {
                    "settlement_days": cp.settlement_days,
                    "calendar": str(cp.calendar),
                    "business_day_convention": str(cp.business_day_convention),
                    "volatility": cp.volatility,
                    "day_counter": str(cp.day_counter),
                }
            },
        }
        additions.append(("coupon_pricers", pricer, "coupon pricer"))
        coupon_pricer = cp.id
        notes.append(
            f"coupon_pricer={cp.id!r}: a zero-vol BlackIborCouponPricer ({cp.volatility}, "
            f"{cp.calendar}, {cp.day_counter}) from {source_of(preset, f'{d}.coupon_pricer')} "
            "(an Ibor coupon needs a pricer; pass coupon_pricer=<id> to use one from the market)"
        )
    else:
        notes.append(
            f"coupon_pricer={coupon_pricer!r} (explicit; must exist in "
            "pricing.rates.coupon_pricers)"
        )
    item = {
        "floating_rate_bond": bond,
        "discounting_curve": discounting_curve,
        "forwarding_curve": forwarding_curve,
        "coupon_pricer": coupon_pricer,
    }
    _ = as_of
    return BuiltTrade(item=item, additions=additions, notes=notes)


def build_zero_coupon_bond(
    preset: Preset,
    trade: ZeroCouponBondTrade,
    maturity: str,
    issue: str | None,
    discounting_curve: str,
) -> BuiltTrade:
    conv = preset.trade_block("zero_coupon_bond")
    assert isinstance(conv, ZeroCouponBondConventions)
    d = "trades.zero_coupon_bond"
    notes: list[str] = [f"trade conventions: {source_of(preset, d)}"]
    bond: dict[str, Any] = {
        "settlement_days": pick(
            preset,
            f"{d}.settlement_days",
            conv.settlement_days,
            trade.settlement_days,
            "settlement_days",
            notes,
        ),
        "calendar": pick(preset, f"{d}.calendar", conv.calendar, None, "calendar", notes),
        "face_amount": check_positive(trade.face_amount, "face_amount"),
        "maturity_date": check_date(maturity, "maturity_date"),
        "payment_convention": pick(
            preset,
            f"{d}.payment_convention",
            conv.payment_convention,
            None,
            "payment_convention",
            notes,
        ),
        "redemption": pick(
            preset, f"{d}.redemption", conv.redemption, trade.redemption, "redemption", notes
        ),
    }
    if issue is not None:
        bond["issue_date"] = issue
        notes.append(f"issue_date={issue!r}")
    else:
        notes.append("issue_date not sent (engine: QuantLib null date)")
    item = {
        "zero_coupon_bond": bond,
        "discounting_curve": discounting_curve,
        "yield": _yield_block(preset, f"{d}.yield", conv.yield_, trade.yield_overrides, notes),
    }
    return BuiltTrade(item=item, notes=notes)


def build_callable_bond(
    preset: Preset,
    trade: CallableBondTrade,
    issue: str,
    effective: str,
    maturity: str,
    discounting_curve: str,
    model: str | HullWhiteModel,
) -> BuiltTrade:
    conv = preset.trade_block("callable_fixed_rate_bond")
    assert isinstance(conv, CallableBondConventions)
    d = "trades.callable_fixed_rate_bond"
    notes: list[str] = [f"trade conventions: {source_of(preset, d)}"]
    body = _coupon_bond_body(
        preset,
        d,
        conv,
        check_positive(trade.face_amount, "face_amount"),
        issue,
        effective,
        maturity,
        trade.overrides,
        notes,
    )
    schedule = body.pop("_schedule")
    calls: list[dict[str, Any]] = []
    for i, c in enumerate(trade.call_schedule):
        calls.append(
            {
                "date": check_date(c.date, f"call_schedule/{i}/date"),
                "price": float(c.price),
                "callability_type": str(c.type),
            }
        )
    bond: dict[str, Any] = {
        "settlement_days": body["settlement_days"],
        "face_amount": body["face_amount"],
        "rate": float(trade.coupon_rate),
        "accrual_day_counter": body["accrual_day_counter"],
        "payment_convention": body["payment_convention"],
        "redemption": body["redemption"],
        "issue_date": body["issue_date"],
        "schedule": schedule,
        "call_schedule": calls,
    }
    additions: list[tuple[str, dict[str, Any], str]] = []
    if isinstance(model, HullWhiteModel):
        model_id = model.id or default_model_id("HullWhiteLattice")
        steps = pick(
            preset,
            f"{d}.lattice_steps",
            conv.lattice_steps,
            model.lattice_steps,
            "model.lattice_steps",
            notes,
        )
        spec = {
            "id": model_id,
            "payload_type": "SwaptionModelSpec",
            "payload": {
                "model_type": "HullWhiteLattice",
                "lattice_steps": steps,
                "param_mode": "Explicit",
                "hw_a": float(model.a),
                "hw_sigma": float(model.sigma),
            },
        }
        additions.append(("models", spec, "model"))
        notes.append(
            f"model={model_id!r}: HullWhiteLattice Explicit a={model.a} sigma={model.sigma} "
            "(arguments)" + ("" if model.id else " [id default]")
        )
    else:
        model_id = model
        notes.append(
            f"model={model_id!r} (explicit; must be a SwaptionModelSpec in "
            "pricing.volatility.models)"
        )
    tree_steps = pick(
        preset, f"{d}.tree_steps", conv.tree_steps, trade.tree_steps, "tree_steps", notes
    )
    item = {
        "callable_fixed_rate_bond": bond,
        "discounting_curve": discounting_curve,
        "model": model_id,
        "tree_steps": tree_steps,
    }
    return BuiltTrade(item=item, additions=additions, notes=notes)


def bond_summary(response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict) or not isinstance(response.get("bonds"), list):
        return None
    keys = (
        "npv",
        "clean_price",
        "dirty_price",
        "accrued_amount",
        "yield",
        "macaulay_duration",
        "modified_duration",
        "settlement_date",
        "error",
    )
    return {
        "bonds": [
            {k: b[k] for k in keys if k in b} for b in response["bonds"] if isinstance(b, dict)
        ]
    }


def check_callables_sorted(calls: list[Callability]) -> None:
    dates = [c.date for c in calls]
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise problem("/call_schedule", "call_schedule dates must be strictly increasing")
