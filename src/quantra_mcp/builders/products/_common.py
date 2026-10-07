"""Shared pieces of the product builders (pure).

Every builder returns a :class:`BuiltTrade`: the engine item for the
endpoint's list, the market additions it needs (vol surfaces, models, credit
curves, ...) as ``(section, item)`` pairs for :func:`builders.market.add_item`,
and ``notes`` naming every default and its source. Dates arrive already
resolved (explicit or by the engine's ``/calendar-advance`` in the tool layer).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from quantra_mcp.builders.schedule import problem, schedule_block, schedule_notes
from quantra_mcp.presets.registry import Preset, VolBaseConventions
from quantra_mcp.schema.enums_generated import (
    BusinessDayConvention,
    Calendar,
    DateGenerationRule,
    DayCounter,
    Frequency,
    IrModelType,
    VolatilityType,
)


@dataclass(slots=True)
class BuiltTrade:
    item: dict[str, Any]
    additions: list[tuple[str, dict[str, Any], str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def source_of(preset: Preset, dotted: str) -> str:
    """``preset <id> (<dotted>)`` plus the provenance when it is field-specific."""
    prov = preset.field_provenance
    parts = dotted.split(".")
    for n in range(len(parts), 0, -1):
        key = ".".join(parts[:n])
        if key in prov:
            return f"preset {preset.id} {dotted} [{prov[key]}]"
    return f"preset {preset.id} {dotted}"


def conv_notes(preset: Preset, prefix: str, dotted: str, block: BaseModel) -> list[str]:
    """One note per field of a convention block: ``<prefix>.<field>=<value> from preset ...``."""
    src = source_of(preset, dotted)
    return [
        f"{prefix}.{k}={v!r} from {src}"
        for k, v in block.model_dump(mode="json", by_alias=True).items()
        if not isinstance(v, dict)
    ]


def pick(
    preset: Preset,
    dotted: str,
    preset_value: Any,
    override: Any,
    label: str,
    notes: list[str],
) -> Any:
    """``override`` when given (noted as explicit) else the preset value (noted with source)."""
    if override is not None:
        value = (
            str(override)
            if not isinstance(override, bool | int | float | dict | list)
            else override
        )
        notes.append(f"{label}={value!r} (explicit argument)")
        return value
    value = str(preset_value) if not isinstance(preset_value, bool | int | float) else preset_value
    notes.append(f"{label}={value!r} from {source_of(preset, dotted)}")
    return value


# --------------------------------------------------------------------------
# shared agent-facing argument models
# --------------------------------------------------------------------------


class ScheduleOverrides(BaseModel):
    """Replace schedule conventions of one leg (every field optional)."""

    model_config = ConfigDict(extra="forbid")

    calendar: Calendar | None = None
    convention: BusinessDayConvention | None = None
    termination_date_convention: BusinessDayConvention | None = None
    date_generation_rule: DateGenerationRule | None = None
    end_of_month: bool | None = None
    first_date: str | None = Field(default=None, description="YYYY-MM-DD stub anchor.")
    next_to_last_date: str | None = Field(default=None, description="YYYY-MM-DD stub anchor.")

    def wire(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in self.model_dump(mode="json").items():
            if v is not None:
                out[k] = v
        return out


class FixedLegOverrides(BaseModel):
    """Replace fixed-leg conventions (every field optional)."""

    model_config = ConfigDict(extra="forbid")

    frequency: Frequency | None = None
    day_counter: DayCounter | None = None
    payment_convention: BusinessDayConvention | None = None
    notionals: list[float] | None = Field(
        default=None, description="Per-period notionals (amortizing); engine field."
    )
    schedule: ScheduleOverrides | None = None


class FloatingLegOverrides(BaseModel):
    """Replace floating-leg conventions (every field optional)."""

    model_config = ConfigDict(extra="forbid")

    frequency: Frequency | None = None
    day_counter: DayCounter | None = None
    payment_convention: BusinessDayConvention | None = None
    notionals: list[float] | None = None
    fixing_days: int | None = Field(default=None, ge=0)
    in_arrears: bool | None = None
    schedule: ScheduleOverrides | None = None


class ConstantVol(BaseModel):
    """A constant volatility surface the tool builds into the market."""

    model_config = ConfigDict(extra="forbid")

    constant: float = Field(
        description="Vol level, e.g. 0.2 (20% lognormal) or 0.008 (80bp normal)."
    )
    type: VolatilityType = Field(description="Lognormal | Normal | ShiftedLognormal.")
    displacement: float = Field(
        default=0.0, description="Shift; only meaningful for ShiftedLognormal."
    )
    id: str | None = Field(default=None, description="Surface id in the market (default noted).")


class AtmMatrixVol(BaseModel):
    """An ATM swaption vol matrix (rows = expiries, cols = tenors), built into the market."""

    model_config = ConfigDict(extra="forbid")

    expiries: list[str | dict[str, Any]]
    tenors: list[str | dict[str, Any]]
    vols: list[list[float]] = Field(
        description="Row-major: one row per expiry, one value per tenor."
    )
    type: VolatilityType
    displacement: float = 0.0
    id: str | None = None


class RawSwaptionVol(BaseModel):
    """Any other swaption vol payload (SmileCube / SabrParams / SabrCalibrate), given raw;
    the tool wraps it in ``SwaptionVolSpec`` with the preset's ``swap_index_id``."""

    model_config = ConfigDict(extra="forbid")

    payload_type: Literal[
        "SwaptionVolConstantSpec",
        "SwaptionVolAtmMatrixSpec",
        "SwaptionVolSmileCubeSpec",
        "SwaptionSabrParamsSpec",
        "SwaptionSabrCalibrateSpec",
    ]
    payload: dict[str, Any]
    id: str | None = None


class HullWhiteModel(BaseModel):
    """An explicit Hull-White lattice model (``SwaptionModelSpec`` HullWhiteLattice)."""

    model_config = ConfigDict(extra="forbid")

    a: float = Field(description="Mean reversion.")
    sigma: float = Field(description="Short-rate volatility.")
    lattice_steps: int | None = Field(default=None, gt=0, description="Default from the preset.")
    id: str | None = None


def vol_base(
    conv: VolBaseConventions,
    as_of: str,
    vol_type: str | None,
    displacement: float | None,
    constant: float | None,
    shape: str | None,
) -> dict[str, Any]:
    """An ``IrVolBaseSpec`` (fixture key order). ``reference_date`` = the pricing as_of."""
    base: dict[str, Any] = {
        "reference_date": as_of,
        "calendar": str(conv.calendar),
        "business_day_convention": str(conv.business_day_convention),
        "day_counter": str(conv.day_counter),
    }
    if shape is not None:
        base["shape"] = shape
    if vol_type is not None:
        base["volatility_type"] = vol_type
    if displacement is not None:
        base["displacement"] = displacement
    if constant is not None:
        base["constant_vol"] = constant
    return base


def model_type_or_id(model: str, allowed: type[IrModelType]) -> tuple[str | None, str | None]:
    """``model`` is either an id in the market or a model type -> (type, None) | (None, id)."""
    try:
        return str(allowed(model)), None
    except ValueError:
        return None, model


def default_model_id(model_type: str) -> str:
    """``Black`` -> ``black_model``, ``ShiftedBlack`` -> ``shifted_black_model``."""
    out = "".join(("_" + c.lower()) if c.isupper() else c for c in model_type).lstrip("_")
    return f"{out}_model"


def check_positive(value: float, field_name: str) -> float:
    if not value > 0:
        raise problem(f"/{field_name}", f"{field_name} must be > 0, got {value!r}")
    return float(value)


__all__ = [
    "AtmMatrixVol",
    "BuiltTrade",
    "ConstantVol",
    "FixedLegOverrides",
    "FloatingLegOverrides",
    "HullWhiteModel",
    "RawSwaptionVol",
    "ScheduleOverrides",
    "check_positive",
    "conv_notes",
    "default_model_id",
    "model_type_or_id",
    "pick",
    "problem",
    "schedule_block",
    "schedule_notes",
    "source_of",
    "vol_base",
]
