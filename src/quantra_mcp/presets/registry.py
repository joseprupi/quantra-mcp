"""Market-convention presets: JSON data files validated by a pydantic model.

A preset is *data*, never code. A curve preset carries every convention the
curve builders need (index definition, curve day counter / interpolator /
trait, one block per supported helper type with every engine field except the
quote and the tenor); a preset may also (or only) carry ``trades``: one
convention block per product the pricing tools build (schedule rules, leg
day counters and frequencies, vol-surface base conventions, credit-curve
helper conventions, ...). Every preset has a ``provenance`` line and, for
fields or blocks whose source differs from it, a ``field_provenance`` map
(longest dotted prefix wins). Nothing here talks to the engine or computes
anything.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from quantra_mcp.schema.enums_generated import (
    BootstrapTrait,
    BusinessDayConvention,
    Calendar,
    CdsEngineType,
    CdsHelperModel,
    Compounding,
    CPIInterpolationType,
    DateGenerationRule,
    DayCounter,
    EquitySettlementType,
    Frequency,
    Interpolator,
    RateAveragingType,
    SettlementMethod,
    TimeUnit,
)

PRESETS_DIR = Path(__file__).resolve().parent

MARKET_STANDARD = "market standard (ISDA/CCP), not from an in-repo source"

HelperType = Literal["deposit", "fra", "future", "swap", "ois"]
HELPER_TYPES: tuple[HelperType, ...] = ("deposit", "fra", "future", "swap", "ois")

#: engine ``point_type`` per preset helper block
POINT_TYPE: dict[str, str] = {
    "deposit": "DepositHelper",
    "fra": "FRAHelper",
    "future": "FutureHelper",
    "swap": "SwapHelper",
    "ois": "OISHelper",
}


class PresetError(LookupError):
    """An unknown preset id or a preset file that fails validation."""


class Period(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n: int
    unit: TimeUnit


class IndexSpec(BaseModel):
    """Exactly the engine ``IndexDef`` convention fields (no fixings)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    index_type: Literal["Ibor", "Overnight"]
    currency: str
    tenor: Period
    fixing_days: int = Field(ge=0)
    calendar: Calendar
    day_counter: DayCounter
    business_day_convention: BusinessDayConvention
    end_of_month: bool


class CurveSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day_counter: DayCounter
    interpolator: Interpolator
    bootstrap_trait: BootstrapTrait


class DepositConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixing_days: int = Field(ge=0)
    calendar: Calendar
    business_day_convention: BusinessDayConvention
    day_counter: DayCounter


class FraConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixing_days: int = Field(ge=0)
    calendar: Calendar
    business_day_convention: BusinessDayConvention
    day_counter: DayCounter


class FutureConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    future_months: int = Field(gt=0)
    calendar: Calendar
    business_day_convention: BusinessDayConvention
    day_counter: DayCounter
    convexity_adjustment: float


class SwapConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calendar: Calendar
    sw_fixed_leg_frequency: Frequency
    sw_fixed_leg_convention: BusinessDayConvention
    sw_fixed_leg_day_counter: DayCounter
    spread: float
    fwd_start_days: int


class OisConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settlement_days: int = Field(ge=0)
    calendar: Calendar
    fixed_leg_frequency: Frequency
    fixed_leg_convention: BusinessDayConvention
    payment_lag: int = Field(ge=0)
    averaging_method: RateAveragingType
    lookback_days: int = Field(ge=0)
    lockout_days: int = Field(ge=0)
    apply_observation_shift: bool


class HelperBlocks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deposit: DepositConventions | None = None
    fra: FraConventions | None = None
    future: FutureConventions | None = None
    swap: SwapConventions | None = None
    ois: OisConventions | None = None

    def block(self, helper_type: str) -> BaseModel | None:
        value = getattr(self, helper_type, None)
        return value if isinstance(value, BaseModel) else None

    @property
    def available(self) -> list[str]:
        return [t for t in HELPER_TYPES if self.block(t) is not None]


# --------------------------------------------------------------------------
# trade convention blocks (one per product the pricing tools build)
# --------------------------------------------------------------------------


class ScheduleConventions(BaseModel):
    """Engine ``Schedule`` minus the dates and the frequency."""

    model_config = ConfigDict(extra="forbid")

    calendar: Calendar
    convention: BusinessDayConvention
    termination_date_convention: BusinessDayConvention
    date_generation_rule: DateGenerationRule
    end_of_month: bool


class FixedLegConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frequency: Frequency
    day_counter: DayCounter
    payment_convention: BusinessDayConvention


class FloatingLegConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frequency: Frequency
    day_counter: DayCounter
    payment_convention: BusinessDayConvention


class OvernightLegConventions(BaseModel):
    """Engine ``OisFloatingLeg`` conventions (the overnight parameters are exposed as
    tool arguments; these are their preset defaults)."""

    model_config = ConfigDict(extra="forbid")

    frequency: Frequency
    day_counter: DayCounter
    payment_convention: BusinessDayConvention
    payment_calendar: Calendar
    payment_lag: int = Field(ge=0)
    averaging_method: RateAveragingType
    lookback_days: int = Field(ge=0)
    lockout_days: int = Field(ge=0)
    apply_observation_shift: bool
    telescopic_value_dates: bool


class VolBaseConventions(BaseModel):
    """Calendar / convention / day counter of a vol surface ``base`` block."""

    model_config = ConfigDict(extra="forbid")

    calendar: Calendar
    business_day_convention: BusinessDayConvention
    day_counter: DayCounter


class YieldConventions(BaseModel):
    """Engine ``Yield`` block (how a bond yield is quoted back)."""

    model_config = ConfigDict(extra="forbid")

    day_counter: DayCounter
    compounding: Compounding
    frequency: Frequency


class VanillaSwapConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settlement_days: int = Field(ge=0)
    schedule: ScheduleConventions
    fixed_leg: FixedLegConventions
    floating_leg: FloatingLegConventions


class OisSwapConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settlement_days: int = Field(ge=0)
    schedule: ScheduleConventions
    fixed_leg: FixedLegConventions
    overnight_leg: OvernightLegConventions


class FixedRateBondConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settlement_days: int = Field(ge=0)
    schedule: ScheduleConventions
    frequency: Frequency
    accrual_day_counter: DayCounter
    payment_convention: BusinessDayConvention
    redemption: float
    yield_: YieldConventions = Field(alias="yield")


class CallableBondConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settlement_days: int = Field(ge=0)
    schedule: ScheduleConventions
    frequency: Frequency
    accrual_day_counter: DayCounter
    payment_convention: BusinessDayConvention
    redemption: float
    lattice_steps: int = Field(gt=0)
    tree_steps: int = Field(gt=0)


class CouponPricerConventions(BaseModel):
    """A zero-vol ``BlackIborCouponPricer`` (what a plain Ibor coupon needs)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    settlement_days: int = Field(ge=0)
    calendar: Calendar
    business_day_convention: BusinessDayConvention
    volatility: float
    day_counter: DayCounter


class FloatingRateBondConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settlement_days: int = Field(ge=0)
    schedule: ScheduleConventions
    frequency: Frequency
    accrual_day_counter: DayCounter
    payment_convention: BusinessDayConvention
    fixing_days: int = Field(ge=0)
    in_arrears: bool
    redemption: float
    coupon_pricer: CouponPricerConventions


class ZeroCouponBondConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settlement_days: int = Field(ge=0)
    calendar: Calendar
    payment_convention: BusinessDayConvention
    redemption: float
    yield_: YieldConventions = Field(alias="yield")


class FraTradeConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settlement_days: int = Field(ge=0)
    day_counter: DayCounter
    calendar: Calendar
    business_day_convention: BusinessDayConvention


class CapFloorConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settlement_days: int = Field(ge=0)
    schedule: ScheduleConventions
    frequency: Frequency
    day_counter: DayCounter
    business_day_convention: BusinessDayConvention
    vol_base: VolBaseConventions


class SwaptionConventions(BaseModel):
    """The underlying swap takes the preset's ``vanilla_swap`` (or ``ois_swap``) block."""

    model_config = ConfigDict(extra="forbid")

    settlement_days: int = Field(ge=0)
    swap_index_id: str
    physical_settlement_method: SettlementMethod
    vol_base: VolBaseConventions


class CdsHelperConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settlement_days: int = Field(ge=0)
    frequency: Frequency
    business_day_convention: BusinessDayConvention
    date_generation_rule: DateGenerationRule
    last_period_day_counter: DayCounter
    settles_accrual: bool
    pays_at_default_time: bool
    rebates_accrual: bool
    helper_model: CdsHelperModel


class CreditCurveConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calendar: Calendar
    day_counter: DayCounter
    curve_interpolator: Interpolator
    recovery_rate: float
    helper_conventions: CdsHelperConventions


class CdsConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule: ScheduleConventions
    frequency: Frequency
    day_counter: DayCounter
    business_day_convention: BusinessDayConvention
    settles_accrual: bool
    pays_at_default_time: bool
    rebates_accrual: bool
    last_period_day_counter: DayCounter
    cash_settlement_days: int = Field(ge=0)
    credit_curve: CreditCurveConventions
    engine_type: CdsEngineType


class EquityOptionConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str
    calendar: Calendar
    business_day_convention: BusinessDayConvention
    day_counter: DayCounter
    interpolator: Interpolator
    compounding: Compounding
    frequency: Frequency
    settlement: EquitySettlementType


class ZcInflationSwapConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixed_calendar: Calendar
    fixed_convention: BusinessDayConvention
    day_counter: DayCounter
    observation_lag: Period
    observation_interpolation: CPIInterpolationType
    adjust_observation_dates: bool
    inflation_calendar: Calendar
    inflation_convention: BusinessDayConvention


class YoyInflationSwapConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule: ScheduleConventions
    frequency: Frequency
    fixed_day_counter: DayCounter
    yoy_day_counter: DayCounter
    payment_calendar: Calendar
    payment_convention: BusinessDayConvention
    observation_lag: Period
    observation_interpolation: CPIInterpolationType


class YoyVolConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day_counter: DayCounter
    calendar: Calendar
    business_day_convention: BusinessDayConvention
    observation_lag: Period


class YoyInflationCapFloorConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule: ScheduleConventions
    frequency: Frequency
    day_counter: DayCounter
    payment_convention: BusinessDayConvention
    observation_lag: Period
    vol: YoyVolConventions


class TradeBlocks(BaseModel):
    """One optional convention block per product."""

    model_config = ConfigDict(extra="forbid")

    vanilla_swap: VanillaSwapConventions | None = None
    ois_swap: OisSwapConventions | None = None
    fixed_rate_bond: FixedRateBondConventions | None = None
    callable_fixed_rate_bond: CallableBondConventions | None = None
    floating_rate_bond: FloatingRateBondConventions | None = None
    zero_coupon_bond: ZeroCouponBondConventions | None = None
    fra: FraTradeConventions | None = None
    cap_floor: CapFloorConventions | None = None
    swaption: SwaptionConventions | None = None
    cds: CdsConventions | None = None
    equity_option: EquityOptionConventions | None = None
    zc_inflation_swap: ZcInflationSwapConventions | None = None
    yoy_inflation_swap: YoyInflationSwapConventions | None = None
    yoy_inflation_cap_floor: YoyInflationCapFloorConventions | None = None

    def block(self, product: str) -> BaseModel | None:
        value = getattr(self, product, None)
        return value if isinstance(value, BaseModel) else None

    @property
    def available(self) -> list[str]:
        return [t for t in TRADE_TYPES if self.block(t) is not None]


TRADE_TYPES: tuple[str, ...] = tuple(TradeBlocks.model_fields)


class Preset(BaseModel):
    """A curve preset (``index`` + ``curve`` + ``helpers``), a trade preset
    (``trades``) or both."""

    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    provenance: str
    currency: str
    index: IndexSpec | None = None
    curve: CurveSpec | None = None
    helpers: HelperBlocks | None = None
    trades: TradeBlocks | None = None
    field_provenance: dict[str, str] = Field(default_factory=dict)

    @property
    def is_curve_preset(self) -> bool:
        return self.index is not None

    def require_curve(self) -> tuple[IndexSpec, CurveSpec, HelperBlocks]:
        if self.index is None or self.curve is None or self.helpers is None:
            raise PresetError(
                f"preset {self.id} has no curve conventions (index / curve / helpers); "
                f"it only defines trade blocks: {self.trade_types}"
            )
        return self.index, self.curve, self.helpers

    @property
    def trade_types(self) -> list[str]:
        return self.trades.available if self.trades is not None else []

    def trade_block(self, product: str) -> BaseModel:
        block = self.trades.block(product) if self.trades is not None else None
        if block is None:
            raise PresetError(
                f"preset {self.id} has no {product!r} trade conventions "
                f"(it defines: {', '.join(self.trade_types) or 'none'}); "
                f"presets with {product!r}: {', '.join(presets_with_trade(product)) or 'none'}"
            )
        return block

    def as_data(self) -> dict[str, Any]:
        """The preset exactly as served to agents (enums as strings)."""
        return self.model_dump(mode="json", exclude_none=True, by_alias=True)

    def provenance_of(self, dotted_field: str) -> str:
        """Provenance for one field: the longest matching ``field_provenance`` prefix
        (``trades.cds`` covers ``trades.cds.schedule.calendar``) or the preset line."""
        parts = dotted_field.split(".")
        for n in range(len(parts), 0, -1):
            key = ".".join(parts[:n])
            if key in self.field_provenance:
                return self.field_provenance[key]
        return self.provenance


def _load_file(path: Path) -> Preset:
    try:
        data = json.loads(path.read_text())
        preset = Preset.model_validate(data)
    except Exception as exc:
        raise PresetError(f"preset file {path.name} is invalid: {exc}") from exc
    if preset.id != path.stem:
        raise PresetError(f"preset file {path.name} declares id {preset.id!r}")
    curve_parts = (preset.index is not None, preset.curve is not None, preset.helpers is not None)
    if any(curve_parts) and not all(curve_parts):
        raise PresetError(f"preset {preset.id}: index, curve and helpers go together")
    if preset.helpers is not None and not preset.helpers.available:
        raise PresetError(f"preset {preset.id} defines no helper block")
    if preset.helpers is None and not preset.trade_types:
        raise PresetError(f"preset {preset.id} defines neither helpers nor trades")
    return preset


@lru_cache(maxsize=1)
def _registry() -> dict[str, Preset]:
    return {p.stem: _load_file(p) for p in sorted(PRESETS_DIR.glob("*.json"))}


def preset_ids() -> list[str]:
    return list(_registry())


def curve_preset_ids() -> list[str]:
    """Presets that can build a curve (index + curve + helpers)."""
    return [p.id for p in _registry().values() if p.is_curve_preset]


def get_preset(preset_id: str) -> Preset:
    try:
        return _registry()[preset_id]
    except KeyError:
        raise PresetError(
            f"unknown preset {preset_id!r}; available presets: {', '.join(preset_ids())}"
        ) from None


def presets_with_trade(product: str) -> list[str]:
    return [p.id for p in _registry().values() if product in p.trade_types]


def list_presets() -> list[dict[str, Any]]:
    """One summary row per preset (full data via :func:`get_preset`)."""
    return [
        {
            "id": p.id,
            "currency": p.currency,
            "index": p.index.id if p.index is not None else None,
            "helpers": p.helpers.available if p.helpers is not None else [],
            "curve": p.curve.model_dump(mode="json") if p.curve is not None else None,
            "trades": p.trade_types,
            "description": p.description,
            "provenance": p.provenance,
        }
        for p in _registry().values()
    ]
