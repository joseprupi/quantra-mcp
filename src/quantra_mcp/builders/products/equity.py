"""Equity option trade builder (pure): spot quote, flat rate / dividend curves,
constant Black vol and model built into the market, or referenced by id."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from quantra_mcp.builders.products._common import BuiltTrade, check_positive, source_of
from quantra_mcp.builders.schedule import check_date, problem
from quantra_mcp.presets.registry import EquityOptionConventions, Preset
from quantra_mcp.schema.enums_generated import EquityModelType, EquityOptionType

EQUITY_OPTION = "/price-equity-option"


class FlatRate(BaseModel):
    """A flat continuously-compounded rate as a two-point InterpolatedZero curve
    (as_of -> end_date). ``end_date`` is explicit: the server does no date arithmetic."""

    model_config = ConfigDict(extra="forbid")

    rate: float
    end_date: str = Field(description="YYYY-MM-DD, beyond the option expiry.")
    id: str | None = None


class EquityConstantVol(BaseModel):
    model_config = ConfigDict(extra="forbid")

    constant: float = Field(gt=0, description="Black vol, e.g. 0.2.")
    id: str | None = None


class EquityModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: EquityModelType = EquityModelType.BlackScholesAnalytic
    binomial_steps: int | None = Field(default=None, gt=0)
    id: str | None = None


class DiscreteDividend(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ex_date: str
    amount: float


class EquityOptionTrade(BaseModel):
    """One vanilla equity option."""

    model_config = ConfigDict(extra="forbid")

    option_type: EquityOptionType
    strike: float = Field(gt=0)
    expiry: str = Field(
        description="YYYY-MM-DD (European expiry / American end / last Bermudan date)."
    )
    exercise: str = Field(default="European", description="European | American | Bermudan.")
    exercise_start: str | None = Field(default=None, description="American: default = as_of.")
    exercise_dates: list[str] | None = Field(default=None, description="Bermudan dates.")
    quantity: float = 1.0
    trade_id: str | None = None


def _flat_curve(
    conv: EquityOptionConventions, curve_id: str, rate: float, as_of: str, end: str
) -> dict[str, Any]:
    def point(date: str) -> dict[str, Any]:
        return {
            "point_type": "ZeroRatePoint",
            "point": {
                "zero_rate": float(rate),
                "calendar": str(conv.calendar),
                "business_day_convention": str(conv.business_day_convention),
                "compounding": str(conv.compounding),
                "frequency": str(conv.frequency),
                "date": date,
            },
        }

    return {
        "id": curve_id,
        "day_counter": str(conv.day_counter),
        "interpolator": str(conv.interpolator),
        "reference_date": as_of,
        "bootstrap_trait": "InterpolatedZero",
        "points": [point(as_of), point(end)],
    }


def build_equity_option(
    preset: Preset,
    trade: EquityOptionTrade,
    as_of: str,
    underlying_id: str,
    spot: float | str,
    rate_curve: str | FlatRate,
    dividend_yield: str | FlatRate,
    vol: str | EquityConstantVol,
    model: str | EquityModel,
    discrete_dividends: list[DiscreteDividend] | None,
) -> BuiltTrade:
    conv = preset.trade_block("equity_option")
    assert isinstance(conv, EquityOptionConventions)
    d = "trades.equity_option"
    src = source_of(preset, d)
    notes: list[str] = [f"equity conventions: {src}"]
    additions: list[tuple[str, dict[str, Any], str]] = []

    if isinstance(spot, str):
        spot_quote_id = spot
        notes.append(f"spot quote {spot_quote_id!r} (explicit id; must exist in pricing.quotes)")
    else:
        spot_quote_id = f"{underlying_id}_SPOT"
        additions.append(
            (
                "quotes",
                {
                    "id": spot_quote_id,
                    "kind": "Price",
                    "value": check_positive(float(spot), "spot"),
                    "quote_type": "Curve",
                },
                "spot quote",
            )
        )
        notes.append(
            f"spot={spot!r} as quote {spot_quote_id!r} (kind Price, quote_type Curve) [id default]"
        )

    if isinstance(rate_curve, FlatRate):
        rate_id = rate_curve.id or f"{str(conv.currency).lower()}_risk_free"
        end = check_date(rate_curve.end_date, "rate_curve/end_date")
        additions.append(
            ("curves", _flat_curve(conv, rate_id, rate_curve.rate, as_of, end), "curve")
        )
        notes.append(
            f"rate_curve={rate_id!r}: flat {rate_curve.rate} continuous "
            f"({conv.compounding}/{conv.frequency}) InterpolatedZero {conv.interpolator} "
            f"{conv.day_counter} from as_of to {end} ({src})"
            + ("" if rate_curve.id else " [id default]")
        )
    else:
        rate_id = rate_curve
        notes.append(f"rate_curve={rate_id!r} (explicit; must exist in pricing.rates.curves)")

    if isinstance(dividend_yield, FlatRate):
        div_id = dividend_yield.id or f"{underlying_id.lower()}_dividend_yield"
        end = check_date(dividend_yield.end_date, "dividend_yield/end_date")
        additions.append(
            ("curves", _flat_curve(conv, div_id, dividend_yield.rate, as_of, end), "curve")
        )
        notes.append(
            f"dividend_yield={div_id!r}: flat {dividend_yield.rate} InterpolatedZero from as_of "
            f"to {end} ({src})" + ("" if dividend_yield.id else " [id default]")
        )
    else:
        div_id = dividend_yield
        notes.append(
            f"dividend_yield={div_id!r} (explicit curve id; must exist in pricing.rates.curves)"
        )

    if isinstance(vol, EquityConstantVol):
        vol_id = vol.id or f"{underlying_id.lower()}_vol_{vol.constant:g}".replace(".", "p")
        base = {
            "reference_date": as_of,
            "calendar": str(conv.calendar),
            "business_day_convention": str(conv.business_day_convention),
            "day_counter": str(conv.day_counter),
            "shape": "Constant",
            "constant_vol": float(vol.constant),
        }
        additions.append(
            (
                "vol_surfaces",
                {"id": vol_id, "payload_type": "BlackVolSpec", "payload": {"base": base}},
                "vol surface",
            )
        )
        notes.append(
            f"volatility={vol_id!r}: BlackVolSpec constant {vol.constant}; base "
            f"reference_date=as_of, {conv.calendar}/{conv.business_day_convention}/"
            f"{conv.day_counter} ({src})" + ("" if vol.id else " [id default]")
        )
    else:
        vol_id = vol
        notes.append(
            f"volatility={vol_id!r} (explicit; must exist in pricing.volatility.vol_surfaces)"
        )

    if isinstance(model, EquityModel):
        model_id = model.id or (
            "bs_analytic" if model.type == EquityModelType.BlackScholesAnalytic else "binomial_crr"
        )
        payload: dict[str, Any] = {"model_type": str(model.type)}
        if model.binomial_steps is not None:
            payload["binomial_steps"] = model.binomial_steps
        additions.append(
            (
                "models",
                {"id": model_id, "payload_type": "EquityVanillaModelSpec", "payload": payload},
                "model",
            )
        )
        notes.append(
            f"model={model_id!r}: EquityVanillaModelSpec {model.type}"
            + (f" binomial_steps={model.binomial_steps}" if model.binomial_steps else "")
            + ("" if model.id else " [id default]")
        )
    else:
        model_id = model
        notes.append(f"model={model_id!r} (explicit; must exist in pricing.volatility.models)")

    underlying: dict[str, Any] = {
        "id": underlying_id,
        "currency": str(conv.currency),
        "spot_quote_id": spot_quote_id,
        "dividend_yield_curve_id": div_id,
    }
    if discrete_dividends:
        underlying["discrete_dividends"] = [
            {
                "ex_date": check_date(x.ex_date, f"discrete_dividends/{i}/ex_date"),
                "amount": float(x.amount),
            }
            for i, x in enumerate(discrete_dividends)
        ]
        notes.append(f"discrete_dividends: {len(discrete_dividends)} cash dividends (arguments)")
    additions.append(("equity_underlyings", underlying, "equity underlying"))
    notes.append(f"equity underlying {underlying_id!r}: currency {conv.currency} ({src})")

    expiry = check_date(trade.expiry, "expiry")
    ex = trade.exercise
    exercise: dict[str, Any]
    if ex == "European":
        exercise_type = "EquityEuropeanExercise"
        exercise = {"expiry_date": expiry}
    elif ex == "American":
        exercise_type = "EquityAmericanExercise"
        start = (
            check_date(trade.exercise_start, "exercise_start") if trade.exercise_start else as_of
        )
        exercise = {"start_date": start, "end_date": expiry}
        notes.append(
            f"American exercise window {start} -> {expiry}"
            + ("" if trade.exercise_start else " (start default: as_of)")
        )
    elif ex == "Bermudan":
        exercise_type = "EquityBermudanExercise"
        if not trade.exercise_dates:
            raise problem("/exercise_dates", "Bermudan needs exercise_dates")
        dates = [check_date(x, f"exercise_dates/{i}") for i, x in enumerate(trade.exercise_dates)]
        if dates[-1] != expiry:
            raise problem(
                "/exercise_dates", f"the last Bermudan exercise date must equal expiry ({expiry})"
            )
        exercise = {"exercise_dates": dates}
    else:
        raise problem("/exercise", "exercise must be European, American or Bermudan")
    trade_id = trade.trade_id or f"{underlying_id}_{str(trade.option_type).upper()}_{expiry}"
    notes.append(f"trade_id={trade_id!r}" + ("" if trade.trade_id else " [default]"))
    option = {
        "trade_id": trade_id,
        "underlying_id": underlying_id,
        "quantity": float(trade.quantity),
        "settlement": str(conv.settlement),
        "payoff_type": "EquityPlainVanillaPayoff",
        "payoff": {"option_type": str(trade.option_type), "strike": float(trade.strike)},
        "exercise_type": exercise_type,
        "exercise": exercise,
    }
    notes.append(f"settlement={conv.settlement!r} from {src}; payoff EquityPlainVanillaPayoff")
    item = {"option": option, "discounting_curve": rate_id, "volatility": vol_id, "model": model_id}
    return BuiltTrade(item=item, additions=additions, notes=notes)


def equity_summary(response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict) or not isinstance(response.get("options"), list):
        return None
    keys = ("trade_id", "npv", "delta", "gamma", "vega", "theta", "rho", "used_spot", "error")
    return {
        "options": [
            {k: o[k] for k in keys if k in o} for o in response["options"] if isinstance(o, dict)
        ]
    }
