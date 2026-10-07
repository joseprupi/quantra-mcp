"""Swaption trade builder (pure): underlying from the preset's swap block, vol and
model built into the market or referenced by id."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from quantra_mcp.builders.products._common import (
    AtmMatrixVol,
    BuiltTrade,
    ConstantVol,
    HullWhiteModel,
    RawSwaptionVol,
    default_model_id,
    model_type_or_id,
    pick,
    source_of,
    vol_base,
)
from quantra_mcp.builders.products.swaps import (
    OisSwapTrade,
    VanillaSwapTrade,
    build_ois_swap,
    build_vanilla_swap,
)
from quantra_mcp.builders.schedule import check_date, problem
from quantra_mcp.builders.tenor import parse_tenor
from quantra_mcp.presets.registry import Preset, SwaptionConventions
from quantra_mcp.schema.enums_generated import (
    ExerciseType,
    IrModelType,
    SettlementMethod,
    SettlementType,
)

SWAPTION = "/price-swaption"


class SwaptionTrade(BaseModel):
    """One swaption. ``underlying`` is the swap the option exercises into: its
    ``effective_date`` may be ``spot`` = exercise date + preset settlement days."""

    model_config = ConfigDict(extra="forbid")

    exercise_date: str | None = Field(default=None, description="European / American: YYYY-MM-DD.")
    exercise_dates: list[str] | None = Field(default=None, description="Bermudan exercise dates.")
    exercise_type: ExerciseType = ExerciseType.European
    settlement_type: SettlementType = SettlementType.Physical
    settlement_method: SettlementMethod | None = Field(
        default=None, description="Default: preset's physical method; required for Cash."
    )
    underlying: VanillaSwapTrade | OisSwapTrade = Field(
        description="VanillaSwapTrade (default) or OisSwapTrade; preset must carry that block."
    )
    underlying_type: str = Field(default="VanillaSwap", description="VanillaSwap | OisSwap.")


def check_exercise(trade: SwaptionTrade) -> None:
    if trade.exercise_type == ExerciseType.Bermudan:
        if not trade.exercise_dates:
            raise problem("/exercise_dates", "Bermudan needs exercise_dates (>= 1 date)")
        for i, d in enumerate(trade.exercise_dates):
            check_date(d, f"exercise_dates/{i}")
    elif trade.exercise_date is None:
        raise problem("/exercise_date", f"{trade.exercise_type} needs exercise_date")
    else:
        check_date(trade.exercise_date, "exercise_date")
    if trade.settlement_type == SettlementType.Cash and trade.settlement_method is None:
        raise problem(
            "/settlement_method",
            "Cash settlement needs settlement_method (CollateralizedCashPrice | ParYieldCurve)",
        )
    if trade.underlying_type not in ("VanillaSwap", "OisSwap"):
        raise problem("/underlying_type", "underlying_type must be VanillaSwap or OisSwap")


def _period(value: str | dict[str, Any], field: str) -> dict[str, Any]:
    return parse_tenor(value, field)


def build_swaption(
    preset: Preset,
    trade: SwaptionTrade,
    effective: str,
    termination: str,
    index_id: str,
    discounting_curve: str,
    forwarding_curve: str,
    vol: str | ConstantVol | AtmMatrixVol | RawSwaptionVol,
    model: str | HullWhiteModel,
    as_of: str,
) -> BuiltTrade:
    conv = preset.trade_block("swaption")
    assert isinstance(conv, SwaptionConventions)
    d = "trades.swaption"
    notes: list[str] = [f"swaption conventions: {source_of(preset, d)}"]
    if trade.underlying_type == "OisSwap":
        if not isinstance(trade.underlying, OisSwapTrade):
            raise problem("/underlying", "underlying_type OisSwap needs an OisSwapTrade underlying")
        under = build_ois_swap(
            preset,
            trade.underlying,
            effective,
            termination,
            index_id,
            discounting_curve,
            forwarding_curve,
        )
        underlying = under.item["ois_swap"]
    else:
        if not isinstance(trade.underlying, VanillaSwapTrade):
            raise problem(
                "/underlying", "underlying_type VanillaSwap needs a VanillaSwapTrade underlying"
            )
        under = build_vanilla_swap(
            preset,
            trade.underlying,
            effective,
            termination,
            index_id,
            discounting_curve,
            forwarding_curve,
        )
        underlying = under.item["vanilla_swap"]
    notes += [f"underlying.{n}" for n in under.notes]

    method = pick(
        preset,
        f"{d}.physical_settlement_method",
        conv.physical_settlement_method,
        trade.settlement_method,
        "settlement_method",
        notes,
    )
    swaption: dict[str, Any] = {
        "exercise_type": str(trade.exercise_type),
        "settlement_type": str(trade.settlement_type),
        "settlement_method": method,
    }
    if trade.exercise_type == ExerciseType.Bermudan:
        swaption["exercise_dates"] = list(trade.exercise_dates or [])
    else:
        swaption["exercise_date"] = trade.exercise_date
    swaption["underlying_type"] = trade.underlying_type
    swaption["underlying"] = underlying

    additions: list[tuple[str, dict[str, Any], str]] = []
    swap_index_id = pick(
        preset, f"{d}.swap_index_id", conv.swap_index_id, None, "vol.swap_index_id", notes
    )
    base_src = source_of(preset, f"{d}.vol_base")
    if isinstance(vol, ConstantVol):
        vol_id = vol.id or f"swpt_vol_{str(vol.type).lower()}_{vol.constant:g}".replace(".", "p")
        payload = {
            "swap_index_id": swap_index_id,
            "payload_type": "SwaptionVolConstantSpec",
            "payload": {
                "base": vol_base(
                    conv.vol_base,
                    as_of,
                    str(vol.type),
                    float(vol.displacement),
                    float(vol.constant),
                    "Constant",
                )
            },
        }
        notes.append(
            f"volatility={vol_id!r}: SwaptionVolConstantSpec {vol.constant} {vol.type} "
            f"displacement {vol.displacement} (arguments); base from {base_src}"
            + ("" if vol.id else " [id default]")
        )
    elif isinstance(vol, AtmMatrixVol):
        vol_id = vol.id or "swaption_atm"
        rows = len(vol.expiries)
        cols = len(vol.tenors)
        if len(vol.vols) != rows or any(len(r) != cols for r in vol.vols):
            raise problem(
                "/vol/vols", f"vols must be {rows} rows (expiries) x {cols} cols (tenors)"
            )
        payload = {
            "swap_index_id": swap_index_id,
            "payload_type": "SwaptionVolAtmMatrixSpec",
            "payload": {
                "base": vol_base(
                    conv.vol_base, as_of, str(vol.type), float(vol.displacement), None, None
                ),
                "expiries": [_period(e, f"vol/expiries/{i}") for i, e in enumerate(vol.expiries)],
                "tenors": [_period(t, f"vol/tenors/{i}") for i, t in enumerate(vol.tenors)],
                "vols": {
                    "n_rows": rows,
                    "n_cols": cols,
                    "values": [float(v) for r in vol.vols for v in r],
                },
            },
        }
        notes.append(
            f"volatility={vol_id!r}: SwaptionVolAtmMatrixSpec {rows}x{cols} {vol.type} "
            f"(arguments); base from {base_src}" + ("" if vol.id else " [id default]")
        )
    elif isinstance(vol, RawSwaptionVol):
        vol_id = vol.id or "swaption_vol"
        payload = {
            "swap_index_id": swap_index_id,
            "payload_type": vol.payload_type,
            "payload": dict(vol.payload),
        }
        notes.append(
            f"volatility={vol_id!r}: {vol.payload_type} payload given raw "
            "(wrapped with swap_index_id)" + ("" if vol.id else " [id default]")
        )
    else:
        vol_id = vol
        payload = None
        notes.append(
            f"volatility={vol_id!r} (explicit; must exist in pricing.volatility.vol_surfaces)"
        )
    if payload is not None:
        additions.append(
            (
                "vol_surfaces",
                {"id": vol_id, "payload_type": "SwaptionVolSpec", "payload": payload},
                "vol surface",
            )
        )

    if isinstance(model, HullWhiteModel):
        model_id = model.id or default_model_id("HullWhiteLattice")
        if model.lattice_steps is None:
            raise problem(
                "/model/lattice_steps", "HullWhiteModel for a swaption needs lattice_steps"
            )
        spec = {
            "id": model_id,
            "payload_type": "SwaptionModelSpec",
            "payload": {
                "model_type": "HullWhiteLattice",
                "lattice_steps": model.lattice_steps,
                "param_mode": "Explicit",
                "hw_a": float(model.a),
                "hw_sigma": float(model.sigma),
            },
        }
        additions.append(("models", spec, "model"))
        notes.append(
            f"model={model_id!r}: HullWhiteLattice Explicit a={model.a} sigma={model.sigma} "
            f"lattice_steps={model.lattice_steps} (arguments)"
            + ("" if model.id else " [id default]")
        )
    else:
        model_type, maybe_id = model_type_or_id(model, IrModelType)
        if model_type == "HullWhiteLattice":
            raise problem(
                "/model",
                "HullWhiteLattice needs {a, sigma, lattice_steps} (HullWhiteModel), "
                "not just the name",
            )
        if model_type is not None:
            model_id = default_model_id(model_type)
            additions.append(
                (
                    "models",
                    {
                        "id": model_id,
                        "payload_type": "SwaptionModelSpec",
                        "payload": {"model_type": model_type},
                    },
                    "model",
                )
            )
            notes.append(
                f"model={model_id!r}: SwaptionModelSpec {model_type} (argument) [id default]"
            )
        else:
            assert maybe_id is not None
            model_id = maybe_id
            notes.append(f"model={model_id!r} (explicit; must exist in pricing.volatility.models)")
    item = {
        "swaption": swaption,
        "discounting_curve": discounting_curve,
        "forwarding_curve": forwarding_curve,
        "volatility": vol_id,
        "model": model_id,
    }
    return BuiltTrade(item=item, additions=additions, notes=notes)


def swaption_summary(response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict) or not isinstance(response.get("swaptions"), list):
        return None
    keys = (
        "npv",
        "implied_volatility",
        "atm_forward",
        "annuity",
        "used_volatility",
        "used_strike",
        "error",
    )
    return {
        "swaptions": [
            {k: s[k] for k in keys if k in s} for s in response["swaptions"] if isinstance(s, dict)
        ]
    }
