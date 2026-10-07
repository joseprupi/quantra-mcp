"""Curve builders: a quote strip + a preset -> an engine ``TermStructure``.

Everything here is pure. The output is the exact fragment that goes on the
wire; every convention comes from the preset (or an explicit override) and is
reported in ``notes`` so nothing is implicit. The engine does all the maths.
The single local numeric rule is the engine's own documented one: the first
discount-factor point of an ``InterpolatedDiscount`` curve must be ``1.0`` at
the reference date (checked here so the error names the point).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from quantra_mcp.builders.tenor import approx_days, parse_tenor, tenor_label
from quantra_mcp.errors import LocalValidationError
from quantra_mcp.presets.registry import (
    POINT_TYPE,
    HelperType,
    Preset,
)
from quantra_mcp.schema.enums_generated import (
    BootstrapTrait,
    BusinessDayConvention,
    Calendar,
    Compounding,
    DayCounter,
    Frequency,
    Interpolator,
    TimeUnit,
)

BOOTSTRAP_CURVES = "/bootstrap-curves"

ValueKind = Literal["zero", "discount", "forward"]
Measure = Literal["DF", "ZERO", "FWD"]


# --------------------------------------------------------------------------
# agent-facing input models
# --------------------------------------------------------------------------


class CurveQuote(BaseModel):
    """One market quote of a bootstrap strip.

    ``type`` selects the engine helper; the preset supplies every convention.
    deposit / swap / ois: ``tenor`` + ``rate``.
    fra: ``months_to_start`` + ``months_to_end`` + ``rate`` (e.g. 3x6 = 3, 6).
    future: ``future_start_date`` (YYYY-MM-DD, the IMM start) + ``price`` (IMM
    price, e.g. 96.5) or ``rate``; ``convexity_adjustment`` optional (the
    preset's explicit value otherwise).
    """

    model_config = ConfigDict(extra="forbid")

    type: HelperType
    tenor: str | dict[str, Any] | None = Field(
        default=None, description="'1W', '3M', '18M', '2Y' or {n, unit}; deposit/swap/ois."
    )
    rate: float | None = Field(default=None, description="Decimal rate, e.g. 0.0375 for 3.75%.")
    months_to_start: int | None = Field(default=None, description="FRA only, e.g. 3 for a 3x6.")
    months_to_end: int | None = Field(default=None, description="FRA only, e.g. 6 for a 3x6.")
    future_start_date: str | None = Field(
        default=None, description="Future only: YYYY-MM-DD start (IMM) date."
    )
    price: float | None = Field(
        default=None, description="Future only: IMM price (100 - rate*100), e.g. 96.5."
    )
    convexity_adjustment: float | None = Field(
        default=None, description="Future only; overrides the preset's explicit value."
    )


class ValuePoint(BaseModel):
    """One explicit curve value at a date or a tenor from the reference date."""

    model_config = ConfigDict(extra="forbid")

    date: str | None = Field(default=None, description="YYYY-MM-DD; or give ``tenor``.")
    tenor: str | dict[str, Any] | None = Field(
        default=None, description="'1Y', '18M', ... from the reference date; '0D' = reference."
    )
    value: float = Field(
        description="Zero rate (decimal), discount factor in (0, 1], or instantaneous "
        "continuously-compounded forward rate, per ``kind``."
    )


class ValueCurveConventions(BaseModel):
    """Conventions a value curve needs when no preset is named."""

    model_config = ConfigDict(extra="forbid")

    day_counter: DayCounter
    calendar: Calendar
    business_day_convention: BusinessDayConvention


class ZeroQuery(BaseModel):
    """Zero-rate query options (engine ``ZeroRateQuery``)."""

    model_config = ConfigDict(extra="forbid")

    use_curve_day_counter: bool = True
    day_counter: DayCounter | None = None
    compounding: Compounding = Compounding.Continuous
    frequency: Frequency = Frequency.Annual


class FwdQuery(BaseModel):
    """Forward-rate query options (engine ``ForwardRateQuery``).

    ``forward_type`` ``Period`` needs ``tenor`` (e.g. 3M forwards);
    ``Instantaneous`` needs ``instantaneous_eps_number`` / ``_time_unit``.
    """

    model_config = ConfigDict(extra="forbid")

    forward_type: Literal["Instantaneous", "Period"]
    tenor: str | dict[str, Any] | None = None
    instantaneous_eps_number: int | None = None
    instantaneous_eps_time_unit: TimeUnit | None = None
    compounding: Compounding
    frequency: Frequency
    use_curve_day_counter: bool = True
    day_counter: DayCounter | None = None
    use_grid_calendar_for_advance: bool = True


class RangeGrid(BaseModel):
    """Engine ``RangeGrid``: sample from start to end with a step."""

    model_config = ConfigDict(extra="forbid")

    start_date: str | None = None
    end_date: str
    step_number: int = Field(gt=0)
    step_time_unit: TimeUnit
    business_days_only: bool = False
    calendar: Calendar | None = None
    business_day_convention: BusinessDayConvention | None = None


# --------------------------------------------------------------------------
# results
# --------------------------------------------------------------------------


@dataclass(slots=True)
class BuiltCurve:
    curve: dict[str, Any]
    indices: list[dict[str, Any]]
    preset: str | None
    notes: list[str] = field(default_factory=list)

    def as_result(self) -> dict[str, Any]:
        return {
            "ok": True,
            "curve": self.curve,
            "indices": self.indices,
            "preset": self.preset,
            "notes": self.notes,
        }


@dataclass(slots=True)
class BuiltQuery:
    query: dict[str, Any]
    notes: list[str] = field(default_factory=list)

    def as_result(self) -> dict[str, Any]:
        return {"ok": True, "query": self.query, "notes": self.notes}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def check_date(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise LocalValidationError(
            f"{field_name}: expected a YYYY-MM-DD date, got {value!r}",
            [{"path": "/" + field_name, "message": "expected YYYY-MM-DD"}],
        )
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise LocalValidationError(
            f"{field_name}: {value!r} is not a valid YYYY-MM-DD date ({exc})",
            [{"path": "/" + field_name, "message": str(exc)}],
        ) from exc
    if len(value) != 10:
        raise LocalValidationError(
            f"{field_name}: expected YYYY-MM-DD, got {value!r}",
            [{"path": "/" + field_name, "message": "expected YYYY-MM-DD"}],
        )
    return value


def _enum_str(value: Any) -> Any:
    return str(value) if isinstance(value, str) else value


def _conv(model: BaseModel) -> dict[str, Any]:
    """A preset convention block as plain wire values, in declaration order."""
    return {k: _enum_str(v) for k, v in model.model_dump(mode="json").items()}


def index_def(preset: Preset) -> dict[str, Any]:
    """The preset's index as an engine ``IndexDef`` (gold-example key order)."""
    ix, _, _ = preset.require_curve()
    return {
        "id": ix.id,
        "name": ix.name,
        "index_type": ix.index_type,
        "currency": ix.currency,
        "tenor": {"n": ix.tenor.n, "unit": str(ix.tenor.unit)},
        "fixing_days": ix.fixing_days,
        "calendar": str(ix.calendar),
        "day_counter": str(ix.day_counter),
        "business_day_convention": str(ix.business_day_convention),
        "end_of_month": ix.end_of_month,
    }


def _problem(path: str, message: str) -> LocalValidationError:
    return LocalValidationError(message, [{"path": path, "message": message}])


def _require_rate(q: CurveQuote, i: int) -> float:
    if q.rate is None:
        raise _problem(f"/quotes/{i}/rate", f"quotes[{i}] ({q.type}): rate is required")
    return float(q.rate)


def _forbid(q: CurveQuote, i: int, *fields: str) -> None:
    for f in fields:
        if getattr(q, f) is not None:
            raise _problem(
                f"/quotes/{i}/{f}", f"quotes[{i}] ({q.type}): {f} does not apply to a {q.type}"
            )


def _helper_point(
    q: CurveQuote, i: int, preset: Preset, reference: dt.date
) -> tuple[dict[str, Any], float, str]:
    """-> (point body, ordering key in approx days, human label for messages)."""
    ix, _, helpers = preset.require_curve()
    block = helpers.block(q.type)
    if block is None:
        raise _problem(
            f"/quotes/{i}/type",
            f"quotes[{i}]: preset {preset.id} has no {q.type!r} helper conventions "
            f"(available: {', '.join(helpers.available)})",
        )
    conv = _conv(block)
    if q.type in ("deposit", "swap", "ois"):
        _forbid(q, i, "months_to_start", "months_to_end", "future_start_date", "price")
        _forbid(q, i, "convexity_adjustment")
        if q.tenor is None:
            raise _problem(f"/quotes/{i}/tenor", f"quotes[{i}] ({q.type}): tenor is required")
        tenor = parse_tenor(q.tenor, f"quotes/{i}/tenor")
        if tenor["n"] == 0:
            raise _problem(f"/quotes/{i}/tenor", f"quotes[{i}] ({q.type}): tenor must be > 0")
        rate = _require_rate(q, i)
        # same maturity under two spellings (1Y / 12M) is the same pillar
        key = f"{q.type} {tenor_label(tenor)} (~{approx_days(tenor):g} days)"
        if q.type == "deposit":
            point = {"rate": rate, "tenor": tenor, **conv}
        elif q.type == "swap":
            point = {
                "rate": rate,
                "tenor": tenor,
                "float_index": {"id": ix.id},
                **conv,
            }
        else:  # ois: gold-example key order
            point = {
                "rate": rate,
                "settlement_days": conv["settlement_days"],
                "overnight_index": {"id": ix.id},
                "tenor": tenor,
                "calendar": conv["calendar"],
                "fixed_leg_convention": conv["fixed_leg_convention"],
                "fixed_leg_frequency": conv["fixed_leg_frequency"],
                "payment_lag": conv["payment_lag"],
                "averaging_method": conv["averaging_method"],
                "lookback_days": conv["lookback_days"],
                "lockout_days": conv["lockout_days"],
                "apply_observation_shift": conv["apply_observation_shift"],
            }
        return point, approx_days(tenor), key
    if q.type == "fra":
        _forbid(q, i, "tenor", "future_start_date", "price", "convexity_adjustment")
        if q.months_to_start is None or q.months_to_end is None:
            raise _problem(
                f"/quotes/{i}",
                f"quotes[{i}] (fra): months_to_start and months_to_end are required (3x6 = 3, 6)",
            )
        if not 0 <= q.months_to_start < q.months_to_end:
            raise _problem(
                f"/quotes/{i}/months_to_end",
                f"quotes[{i}] (fra): need 0 <= months_to_start < months_to_end",
            )
        rate = _require_rate(q, i)
        point = {
            "rate": rate,
            "months_to_start": q.months_to_start,
            "months_to_end": q.months_to_end,
            **conv,
        }
        key = f"fra {q.months_to_start}x{q.months_to_end}"
        return point, q.months_to_end * 365.25 / 12.0, key
    # future
    _forbid(q, i, "tenor", "months_to_start", "months_to_end")
    if q.future_start_date is None:
        raise _problem(
            f"/quotes/{i}/future_start_date",
            f"quotes[{i}] (future): future_start_date (YYYY-MM-DD) is required",
        )
    start = check_date(q.future_start_date, f"quotes/{i}/future_start_date")
    if q.price is None and q.rate is None:
        raise _problem(
            f"/quotes/{i}", f"quotes[{i}] (future): one of price (IMM price) or rate is required"
        )
    if q.price is not None and q.rate is not None:
        raise _problem(f"/quotes/{i}", f"quotes[{i}] (future): give either price or rate, not both")
    fut: dict[str, Any] = {}
    if q.price is not None:
        fut["futures_price"] = float(q.price)
    elif q.rate is not None:
        fut["rate"] = float(q.rate)
    fut["future_start_date"] = start
    fut["future_months"] = conv["future_months"]
    fut["calendar"] = conv["calendar"]
    fut["business_day_convention"] = conv["business_day_convention"]
    fut["day_counter"] = conv["day_counter"]
    fut["convexity_adjustment"] = (
        float(q.convexity_adjustment)
        if q.convexity_adjustment is not None
        else conv["convexity_adjustment"]
    )
    days = (dt.date.fromisoformat(start) - reference).days + conv["future_months"] * 365.25 / 12
    return fut, float(days), f"future {start}"


def _convention_notes(preset: Preset, used: dict[str, int], notes: list[str]) -> None:
    _, _, helpers = preset.require_curve()
    for helper_type, count in used.items():
        block = helpers.block(helper_type)
        assert block is not None
        for key, value in _conv(block).items():
            dotted = f"helpers.{helper_type}.{key}"
            prov = preset.field_provenance.get(dotted)
            suffix = f" [{prov}]" if prov else ""
            notes.append(
                f"{helper_type}.{key}={value!r} from preset {preset.id} "
                f"({count} point{'s' if count != 1 else ''}){suffix}"
            )


# --------------------------------------------------------------------------
# build_curve
# --------------------------------------------------------------------------


def build_curve(
    curve_id: str,
    preset: Preset,
    quotes: list[CurveQuote],
    reference_date: str,
    trait: BootstrapTrait | None = None,
    interpolator: Interpolator | None = None,
    day_counter: DayCounter | None = None,
) -> BuiltCurve:
    """A quote strip -> ``{"curve": TermStructure, "indices": [IndexDef]}``.

    Points are sorted by maturity; a duplicate (type, tenor) is a local error.
    Every engine field is set explicitly from the preset; overrides are noted.
    """
    if not curve_id or not isinstance(curve_id, str):
        raise _problem("/id", "id: a non-empty curve id is required")
    _, c, _ = preset.require_curve()
    ref = check_date(reference_date, "reference_date")
    ref_date = dt.date.fromisoformat(ref)
    if not quotes:
        raise _problem("/quotes", "quotes: at least one quote is required")

    notes: list[str] = [f"preset {preset.id}: {preset.provenance}"]
    built: list[tuple[float, int, dict[str, Any]]] = []
    seen: dict[tuple[str, float], tuple[int, str]] = {}
    used: dict[str, int] = {}
    for i, q in enumerate(quotes):
        point, order_key, label = _helper_point(q, i, preset, ref_date)
        identity = (q.type, round(order_key, 6))
        if identity in seen:
            j, other = seen[identity]
            raise _problem(
                f"/quotes/{i}",
                f"quotes[{i}] ({label}) duplicates quotes[{j}] ({other}): same helper type and "
                "maturity; each pillar may appear once",
            )
        seen[identity] = (i, label)
        used[q.type] = used.get(q.type, 0) + 1
        built.append((order_key, i, {"point_type": POINT_TYPE[q.type], "point": point}))
    built.sort(key=lambda t: (t[0], t[1]))
    if [b[1] for b in built] != list(range(len(built))):
        notes.append("quotes were re-ordered by maturity")

    dc = str(day_counter) if day_counter is not None else str(c.day_counter)
    interp = str(interpolator) if interpolator is not None else str(c.interpolator)
    tr = str(trait) if trait is not None else str(c.bootstrap_trait)
    if tr not in ("Discount", "ZeroRate", "FwdRate"):
        raise _problem(
            "/trait",
            f"trait {tr!r} is a value-curve family; a bootstrapped strip needs Discount, "
            "ZeroRate or FwdRate (use build_value_curve for explicit points)",
        )
    for name, value, override in (
        ("day_counter", dc, day_counter is not None),
        ("interpolator", interp, interpolator is not None),
        ("bootstrap_trait", tr, trait is not None),
    ):
        notes.append(
            f"curve.{name}={value!r} "
            + (
                f"(explicit override of preset {preset.id})"
                if override
                else f"from preset {preset.id}"
            )
        )
    ix = index_def(preset)
    ix_prov = [
        f"{k.removeprefix('index.')}: {v}"
        for k, v in preset.field_provenance.items()
        if k.startswith("index.")
    ]
    notes.append(
        f"index {ix['id']} ({ix['name']}, {ix['index_type']}, {ix['calendar']}, "
        f"{ix['day_counter']}, fixing_days={ix['fixing_days']}, end_of_month={ix['end_of_month']}) "
        f"from preset {preset.id}" + (f" [{'; '.join(ix_prov)}]" if ix_prov else "")
    )
    _convention_notes(preset, used, notes)
    if "swap" in used:
        notes.append(
            f"swap helpers reference float_index {ix['id']}; the engine projects it off the "
            "curve being bootstrapped (single-curve)"
        )
    if "ois" in used:
        notes.append(
            f"ois helpers reference overnight_index {ix['id']}; lookback_days=0 means no lookback "
            "(QuantLib null), per engine 0.6.0"
        )

    curve = {
        "id": curve_id,
        "reference_date": ref,
        "day_counter": dc,
        "interpolator": interp,
        "bootstrap_trait": tr,
        "points": [b[2] for b in built],
    }
    return BuiltCurve(curve=curve, indices=[ix], preset=preset.id, notes=notes)


# --------------------------------------------------------------------------
# build_value_curve
# --------------------------------------------------------------------------

_VALUE_TRAIT: dict[str, str] = {
    "zero": "InterpolatedZero",
    "discount": "InterpolatedDiscount",
    "forward": "InterpolatedFwd",
}
_VALUE_POINT_TYPE: dict[str, str] = {
    "zero": "ZeroRatePoint",
    "discount": "DiscountFactorPoint",
    "forward": "ForwardRatePoint",
}
_VALUE_FIELD: dict[str, str] = {
    "zero": "zero_rate",
    "discount": "discount_factor",
    "forward": "forward_rate",
}
_DEFAULT_INTERPOLATOR: dict[str, str] = {
    "zero": "Linear",
    "discount": "LogLinear",
    "forward": "Linear",
}
_FWD_INTERPOLATORS = ("Linear", "BackwardFlat", "ForwardFlat")


def build_value_curve(
    curve_id: str,
    kind: ValueKind,
    points: list[ValuePoint],
    reference_date: str,
    preset: Preset | None = None,
    conventions: ValueCurveConventions | None = None,
    compounding: Compounding | None = None,
    frequency: Frequency | None = None,
    interpolator: Interpolator | None = None,
) -> BuiltCurve:
    """Explicit points -> an ``Interpolated{Zero,Discount,Fwd}`` ``TermStructure``.

    Conventions (curve day counter, point calendar and convention) come from
    ``preset`` (its curve day counter + index calendar/convention) or from
    ``conventions``. Point order is kept as given (the engine anchors the curve
    at the first point). For ``discount`` the first point must be the reference
    date with value exactly ``1.0`` (the engine's rule, checked here).
    """
    if not curve_id or not isinstance(curve_id, str):
        raise _problem("/id", "id: a non-empty curve id is required")
    ref = check_date(reference_date, "reference_date")
    if kind not in _VALUE_TRAIT:
        raise _problem("/kind", f"kind must be one of zero|discount|forward, got {kind!r}")
    if (preset is None) == (conventions is None):
        raise _problem(
            "/conventions",
            "give exactly one of preset or conventions (day_counter, calendar, "
            "business_day_convention)",
        )
    if not points:
        raise _problem("/points", "points: at least one point is required")

    notes: list[str] = []
    if preset is not None:
        p_index, p_curve, _ = preset.require_curve()
        dc = str(p_curve.day_counter)
        cal = str(p_index.calendar)
        bdc = str(p_index.business_day_convention)
        src = (
            f"preset {preset.id} (curve.day_counter, index.calendar, index.business_day_convention)"
        )
        notes.append(f"preset {preset.id}: {preset.provenance}")
    else:
        assert conventions is not None
        dc, cal, bdc = (
            str(conventions.day_counter),
            str(conventions.calendar),
            str(conventions.business_day_convention),
        )
        src = "explicit conventions"
    notes.append(f"curve.day_counter={dc!r} from {src}")
    notes.append(f"points.calendar={cal!r}, points.business_day_convention={bdc!r} from {src}")

    trait = _VALUE_TRAIT[kind]
    interp = str(interpolator) if interpolator is not None else _DEFAULT_INTERPOLATOR[kind]
    if kind == "forward" and interp not in _FWD_INTERPOLATORS:
        raise _problem(
            "/interpolator",
            f"InterpolatedFwd supports {_FWD_INTERPOLATORS} only (engine 0.5.0), got {interp!r}",
        )
    notes.append(f"curve.bootstrap_trait={trait!r} selected by kind={kind!r} (engine 0.5.0 family)")
    notes.append(
        f"curve.interpolator={interp!r} "
        + ("(explicit)" if interpolator is not None else f"default for kind={kind!r}")
    )
    if kind == "zero":
        comp = str(compounding) if compounding is not None else "Continuous"
        freq = str(frequency) if frequency is not None else "Annual"
        notes.append(
            f"zero points: compounding={comp!r}, frequency={freq!r} "
            + ("(explicit)" if compounding is not None or frequency is not None else "(default)")
            + "; all zero points of one curve share them (engine 0.5.0 rule)"
        )
    elif compounding is not None or frequency is not None:
        raise _problem(
            "/compounding", f"compounding/frequency apply to kind='zero' only (got kind={kind!r})"
        )

    value_field = _VALUE_FIELD[kind]
    wire_points: list[dict[str, Any]] = []
    for i, p in enumerate(points):
        if (p.date is None) == (p.tenor is None):
            raise _problem(f"/points/{i}", f"points[{i}]: give exactly one of date or tenor")
        body: dict[str, Any] = {}
        if p.date is not None:
            body["date"] = check_date(p.date, f"points/{i}/date")
        else:
            body["tenor"] = parse_tenor(p.tenor, f"points/{i}/tenor")
        body["calendar"] = cal
        body["business_day_convention"] = bdc
        body[value_field] = float(p.value)
        if kind == "zero":
            body["compounding"] = comp
            body["frequency"] = freq
        wire_points.append({"point_type": _VALUE_POINT_TYPE[kind], "point": body})

    if kind == "discount":
        first = wire_points[0]["point"]
        at_ref = first.get("date") == ref or (
            isinstance(first.get("tenor"), dict) and first["tenor"]["n"] == 0
        )
        if not at_ref or first["discount_factor"] != 1.0:
            raise _problem(
                "/points/0",
                "discount curves: the first point must be the reference date "
                f"({ref} or tenor '0D') with discount_factor exactly 1.0 (engine rule for "
                f"InterpolatedDiscount); got {first}",
            )
        notes.append("checked: first discount point is 1.0 at the reference date")

    curve = {
        "id": curve_id,
        "reference_date": ref,
        "day_counter": dc,
        "interpolator": interp,
        "bootstrap_trait": trait,
        "points": wire_points,
    }
    return BuiltCurve(curve=curve, indices=[], preset=preset.id if preset else None, notes=notes)


# --------------------------------------------------------------------------
# build_query
# --------------------------------------------------------------------------


def build_query(
    curve_id: str,
    measures: list[Measure],
    tenors: list[str | dict[str, Any]] | None = None,
    range_grid: RangeGrid | None = None,
    calendar: Calendar | None = None,
    business_day_convention: BusinessDayConvention | None = None,
    zero: ZeroQuery | None = None,
    fwd: FwdQuery | None = None,
) -> BuiltQuery:
    """-> an engine ``CurveQuerySpec`` (TenorGrid or RangeGrid)."""
    if not curve_id:
        raise _problem("/curve_id", "curve_id is required")
    if not measures:
        raise _problem("/measures", "measures: at least one of DF, ZERO, FWD")
    ms = [str(m) for m in measures]
    bad = [m for m in ms if m not in ("DF", "ZERO", "FWD")]
    if bad:
        raise _problem("/measures", f"measures must be DF|ZERO|FWD, got {bad}")
    if (tenors is None) == (range_grid is None):
        raise _problem("/grid", "give exactly one of tenors (TenorGrid) or range_grid (RangeGrid)")
    notes: list[str] = []
    if tenors is not None:
        if not tenors:
            raise _problem("/tenors", "tenors: at least one tenor")
        if calendar is None or business_day_convention is None:
            raise _problem(
                "/calendar",
                "TenorGrid needs calendar and business_day_convention (how each tenor is "
                "rolled from the reference date); use the preset's index calendar/convention",
            )
        grid: dict[str, Any] = {
            "tenors": [parse_tenor(t, f"tenors/{i}") for i, t in enumerate(tenors)],
            "calendar": str(calendar),
            "business_day_convention": str(business_day_convention),
        }
        grid_spec = {"grid_type": "TenorGrid", "grid": grid}
    else:
        assert range_grid is not None
        rg: dict[str, Any] = {}
        if range_grid.start_date is not None:
            rg["start_date"] = check_date(range_grid.start_date, "range_grid/start_date")
        rg["end_date"] = check_date(range_grid.end_date, "range_grid/end_date")
        rg["step_number"] = range_grid.step_number
        rg["step_time_unit"] = str(range_grid.step_time_unit)
        rg["business_days_only"] = range_grid.business_days_only
        if range_grid.calendar is not None:
            rg["calendar"] = str(range_grid.calendar)
        if range_grid.business_day_convention is not None:
            rg["business_day_convention"] = str(range_grid.business_day_convention)
        grid_spec = {"grid_type": "RangeGrid", "grid": rg}
        if calendar is not None or business_day_convention is not None:
            raise _problem(
                "/calendar", "with range_grid put calendar/business_day_convention inside it"
            )
    query: dict[str, Any] = {"curve_id": curve_id, "measures": ms, "grid": grid_spec}
    if "ZERO" in ms:
        z = zero or ZeroQuery()
        zq: dict[str, Any] = {"use_curve_day_counter": z.use_curve_day_counter}
        if z.day_counter is not None:
            zq["day_counter"] = str(z.day_counter)
        zq["compounding"] = str(z.compounding)
        zq["frequency"] = str(z.frequency)
        query["zero"] = zq
        notes.append(
            f"zero={zq} "
            + (
                "(explicit)"
                if zero is not None
                else "(default: continuous annual zeros in the curve day counter)"
            )
        )
    elif zero is not None:
        raise _problem("/zero", "zero options given but ZERO is not in measures")
    if "FWD" in ms:
        if fwd is None:
            raise _problem(
                "/fwd",
                "FWD needs fwd options: forward_type 'Period' + tenor (e.g. 3M simple forwards) "
                "or 'Instantaneous' + instantaneous_eps_number/_time_unit, plus compounding and "
                "frequency; no default is applied",
            )
        fq: dict[str, Any] = {"forward_type": fwd.forward_type}
        if fwd.forward_type == "Period":
            if fwd.tenor is None:
                raise _problem("/fwd/tenor", "fwd.forward_type 'Period' needs fwd.tenor")
            fq["tenor"] = parse_tenor(fwd.tenor, "fwd/tenor")
        else:
            if fwd.instantaneous_eps_number is None or fwd.instantaneous_eps_time_unit is None:
                raise _problem(
                    "/fwd",
                    "fwd.forward_type 'Instantaneous' needs instantaneous_eps_number and "
                    "instantaneous_eps_time_unit (e.g. 1 Days)",
                )
            fq["instantaneous_eps_number"] = fwd.instantaneous_eps_number
            fq["instantaneous_eps_time_unit"] = str(fwd.instantaneous_eps_time_unit)
        fq["compounding"] = str(fwd.compounding)
        fq["frequency"] = str(fwd.frequency)
        fq["use_curve_day_counter"] = fwd.use_curve_day_counter
        if fwd.day_counter is not None:
            fq["day_counter"] = str(fwd.day_counter)
        fq["use_grid_calendar_for_advance"] = fwd.use_grid_calendar_for_advance
        query["fwd"] = fq
        notes.append(f"fwd={fq} (explicit)")
    elif fwd is not None:
        raise _problem("/fwd", "fwd options given but FWD is not in measures")
    return BuiltQuery(query=query, notes=notes)


# --------------------------------------------------------------------------
# bootstrap request assembly (used by the tool; still pure)
# --------------------------------------------------------------------------


def merge_indices(indices: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Dedupe identical ``IndexDef``s by id (first wins); conflicting ids are an error."""
    out: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    notes: list[str] = []
    for i, ix in enumerate(indices):
        if not isinstance(ix, dict) or not isinstance(ix.get("id"), str):
            raise _problem(f"/indices/{i}", f"indices[{i}]: expected an IndexDef object with an id")
        prev = by_id.get(ix["id"])
        if prev is None:
            by_id[ix["id"]] = ix
            out.append(ix)
        elif prev == ix:
            notes.append(
                f"index {ix['id']} supplied more than once with identical fields; sent once"
            )
        else:
            raise _problem(
                f"/indices/{i}",
                f"index id {ix['id']!r} supplied twice with different definitions; the engine "
                "rejects duplicate ids and the server will not pick one",
            )
    return out, notes


def bootstrap_request(
    curves: list[dict[str, Any]],
    indices: list[dict[str, Any]],
    as_of: str,
    queries: list[dict[str, Any]],
    calendar_overrides: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The ``/bootstrap-curves`` body (gold-example key order)."""
    pricing: dict[str, Any] = {
        "as_of_date": check_date(as_of, "as_of"),
        "rates": {"indices": indices, "curves": curves},
    }
    if calendar_overrides is not None:
        pricing["calendar_overrides"] = calendar_overrides
    return {"pricing": pricing, "queries": queries}


def bootstrap_summary(response: Any) -> dict[str, Any] | None:
    """Per-curve ``{id, pillars, first_grid_date, last_grid_date, measures, error?}``.

    A selection of response fields. The engine's JSON omits ``series[].measure``
    when it equals the schema default (``DF``, the first ``CurveMeasure`` value),
    so an absent key is reported as ``DF``; nothing else is inferred.
    """
    if not isinstance(response, dict) or not isinstance(response.get("results"), list):
        return None
    out: list[dict[str, Any]] = []
    for r in response["results"]:
        if not isinstance(r, dict):
            continue
        grid = r.get("grid_dates") or []
        row: dict[str, Any] = {
            "id": r.get("id"),
            "pillars": len(r["pillar_dates"]) if isinstance(r.get("pillar_dates"), list) else None,
            "first_grid_date": grid[0] if grid else None,
            "last_grid_date": grid[-1] if grid else None,
            "measures": [
                s.get("measure", "DF") for s in r.get("series") or [] if isinstance(s, dict)
            ],
        }
        if r.get("error") is not None:
            row["error"] = r["error"]
        out.append(row)
    return {"curves": out}
