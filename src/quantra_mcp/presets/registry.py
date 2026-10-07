"""Market-convention presets: JSON data files validated by a pydantic model.

A preset is *data*, never code. It carries every convention the curve
builders need (index definition, curve day counter / interpolator / trait,
one block per supported helper type with every engine field except the quote
and the tenor) plus a ``provenance`` line and, for fields whose source differs
from the preset-level provenance, a ``field_provenance`` map. Nothing here
talks to the engine or computes anything.
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
    DayCounter,
    Frequency,
    Interpolator,
    RateAveragingType,
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


class Preset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    provenance: str
    currency: str
    index: IndexSpec
    curve: CurveSpec
    helpers: HelperBlocks
    field_provenance: dict[str, str] = Field(default_factory=dict)

    def as_data(self) -> dict[str, Any]:
        """The preset exactly as served to agents (enums as strings)."""
        return self.model_dump(mode="json", exclude_none=True)

    def provenance_of(self, dotted_field: str) -> str:
        """Provenance for one field: the field-level entry or the preset line."""
        return self.field_provenance.get(dotted_field, self.provenance)


def _load_file(path: Path) -> Preset:
    try:
        data = json.loads(path.read_text())
        preset = Preset.model_validate(data)
    except Exception as exc:
        raise PresetError(f"preset file {path.name} is invalid: {exc}") from exc
    if preset.id != path.stem:
        raise PresetError(f"preset file {path.name} declares id {preset.id!r}")
    if not preset.helpers.available:
        raise PresetError(f"preset {preset.id} defines no helper block")
    return preset


@lru_cache(maxsize=1)
def _registry() -> dict[str, Preset]:
    return {p.stem: _load_file(p) for p in sorted(PRESETS_DIR.glob("*.json"))}


def preset_ids() -> list[str]:
    return list(_registry())


def get_preset(preset_id: str) -> Preset:
    try:
        return _registry()[preset_id]
    except KeyError:
        raise PresetError(
            f"unknown preset {preset_id!r}; available presets: {', '.join(preset_ids())}"
        ) from None


def list_presets() -> list[dict[str, Any]]:
    """One summary row per preset (full data via :func:`get_preset`)."""
    return [
        {
            "id": p.id,
            "currency": p.currency,
            "index": p.index.id,
            "helpers": p.helpers.available,
            "curve": p.curve.model_dump(mode="json"),
            "description": p.description,
            "provenance": p.provenance,
        }
        for p in _registry().values()
    ]
