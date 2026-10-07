"""QuantLib parity for the curve presets: engine DFs vs an independent QuantLib build.

For each preset in ``tests/golden/strips.json`` the request is built with the
server's own builders, POSTed to the engine, and the same curve is built in
QuantLib-python with the preset's conventions (mirroring the constructor
calls the engine makes, see ``src/parsers/term_structure_point_parser.cpp`` at
the pin). Discount factors on the engine's grid dates must agree to 1e-10 (the
engine serialises 12 decimals, so the floor is ~5e-13).

This script is the only place QuantLib is imported; the server never computes
a number. Run it with a Python that has QuantLib::

    QUANTRA_ENGINE_URL=http://localhost:18088 PYTHONPATH=src python3 scripts/parity_ql.py
    QUANTRA_ENGINE_URL=http://localhost:18088 uv run --with QuantLib python scripts/parity_ql.py

Only ``pydantic`` and the standard library are needed besides QuantLib.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

import QuantLib as ql

from quantra_mcp.builders import curves as cb
from quantra_mcp.presets.registry import Preset, curve_preset_ids, get_preset

STRIPS_PATH = Path(__file__).resolve().parents[1] / "tests" / "golden" / "strips.json"
TOL = 1e-10

CALENDARS = {
    "TARGET": ql.TARGET(),
    "UnitedKingdom": ql.UnitedKingdom(),
    "UnitedStates": ql.UnitedStates(ql.UnitedStates.Settlement),
    "UnitedStatesGovernmentBond": ql.UnitedStates(ql.UnitedStates.GovernmentBond),
}
DAY_COUNTERS = {
    "Actual360": ql.Actual360(),
    "Actual365Fixed": ql.Actual365Fixed(),
    "Thirty360": ql.Thirty360(ql.Thirty360.BondBasis),  # enum_convert.cpp at the pin
}
FREQUENCIES = {"Annual": ql.Annual, "Semiannual": ql.Semiannual, "Quarterly": ql.Quarterly}
CONVENTIONS = {
    "Following": ql.Following,
    "ModifiedFollowing": ql.ModifiedFollowing,
    "Preceding": ql.Preceding,
    "Unadjusted": ql.Unadjusted,
}
CURRENCIES = {"USD": ql.USDCurrency(), "EUR": ql.EURCurrency(), "GBP": ql.GBPCurrency()}
AVERAGING = {"Compound": ql.RateAveraging.Compound, "Simple": ql.RateAveraging.Simple}
UNITS = {"Days": ql.Days, "Weeks": ql.Weeks, "Months": ql.Months, "Years": ql.Years}


def ql_date(iso: str) -> ql.Date:
    y, m, d = (int(x) for x in iso.split("-"))
    return ql.Date(d, m, y)


def ql_period(p: dict[str, Any]) -> ql.Period:
    return ql.Period(int(p["n"]), UNITS[str(p["unit"])])


def ql_index(ix: dict[str, Any]) -> ql.InterestRateIndex:
    """Mirror index_registry_builder.h: generic OvernightIndex / IborIndex from the IndexDef."""
    if ix["index_type"] == "Overnight":
        return ql.OvernightIndex(
            ix["name"],
            int(ix["fixing_days"]),
            CURRENCIES[ix["currency"]],
            CALENDARS[ix["calendar"]],
            DAY_COUNTERS[ix["day_counter"]],
        )
    return ql.IborIndex(
        ix["name"],
        ql_period(ix["tenor"]),
        int(ix["fixing_days"]),
        CURRENCIES[ix["currency"]],
        CALENDARS[ix["calendar"]],
        CONVENTIONS[ix["business_day_convention"]],
        bool(ix["end_of_month"]),
        DAY_COUNTERS[ix["day_counter"]],
    )


def ql_helper(pt: dict[str, Any], index: ql.InterestRateIndex) -> ql.RateHelper:
    """Mirror term_structure_point_parser.cpp constructor calls, argument for argument."""
    kind, p = pt["point_type"], pt["point"]
    q = ql.QuoteHandle(ql.SimpleQuote(float(p["rate"]))) if "rate" in p else None
    if kind == "DepositHelper":
        assert q is not None
        return ql.DepositRateHelper(
            q,
            ql_period(p["tenor"]),
            int(p["fixing_days"]),
            CALENDARS[p["calendar"]],
            CONVENTIONS[p["business_day_convention"]],
            True,
            DAY_COUNTERS[p["day_counter"]],
        )
    if kind == "FRAHelper":
        assert q is not None
        return ql.FraRateHelper(
            q,
            int(p["months_to_start"]),
            int(p["months_to_end"]),
            int(p["fixing_days"]),
            CALENDARS[p["calendar"]],
            CONVENTIONS[p["business_day_convention"]],
            True,
            DAY_COUNTERS[p["day_counter"]],
        )
    if kind == "FutureHelper":
        price = (
            float(p["futures_price"]) if "futures_price" in p else 100.0 * (1.0 - float(p["rate"]))
        )
        return ql.FuturesRateHelper(
            ql.QuoteHandle(ql.SimpleQuote(price)),
            ql_date(p["future_start_date"]),
            int(p["future_months"]),
            CALENDARS[p["calendar"]],
            CONVENTIONS[p["business_day_convention"]],
            True,
            DAY_COUNTERS[p["day_counter"]],
            ql.QuoteHandle(ql.SimpleQuote(float(p["convexity_adjustment"]))),
        )
    if kind == "SwapHelper":
        assert q is not None
        return ql.SwapRateHelper(
            q,
            ql_period(p["tenor"]),
            CALENDARS[p["calendar"]],
            FREQUENCIES[p["sw_fixed_leg_frequency"]],
            CONVENTIONS[p["sw_fixed_leg_convention"]],
            DAY_COUNTERS[p["sw_fixed_leg_day_counter"]],
            index,
            ql.QuoteHandle(ql.SimpleQuote(float(p["spread"]))),
            ql.Period(int(p["fwd_start_days"]), ql.Days),
        )
    if kind == "OISHelper":
        assert q is not None
        # Positional up to averagingMethod exactly as the SOFR post; the overnight
        # parameters go by keyword (the bindings reject positional None for the
        # optional endOfMonth / fixedPaymentFrequency slots). Wire 0 lookback =
        # QuantLib Null, which is the binding default, so it is simply not passed.
        kwargs: dict[str, Any] = {
            "lockoutDays": int(p["lockout_days"]),
            "applyObservationShift": bool(p["apply_observation_shift"]),
        }
        if int(p["lookback_days"]) > 0:
            kwargs["lookbackDays"] = int(p["lookback_days"])
        return ql.OISRateHelper(
            int(p["settlement_days"]),
            ql_period(p["tenor"]),
            q,
            index,
            ql.YieldTermStructureHandle(),  # no exogenous discount curve
            False,  # telescopicValueDates
            int(p["payment_lag"]),
            CONVENTIONS[p["fixed_leg_convention"]],  # payment convention
            FREQUENCIES[p["fixed_leg_frequency"]],  # payment frequency
            CALENDARS[p["calendar"]],  # payment calendar
            ql.Period(0, ql.Days),  # forwardStart
            0.0,  # overnightSpread
            ql.Pillar.LastRelevantDate,
            ql.Date(),
            AVERAGING[p["averaging_method"]],
            **kwargs,
        )
    raise ValueError(f"unsupported point type {kind}")


def ql_curve(curve: dict[str, Any], index: ql.InterestRateIndex) -> ql.YieldTermStructure:
    assert curve["bootstrap_trait"] == "Discount" and curve["interpolator"] == "LogLinear", curve
    helpers = [ql_helper(pt, index) for pt in curve["points"]]
    return ql.PiecewiseLogLinearDiscount(
        ql_date(curve["reference_date"]), helpers, DAY_COUNTERS[curve["day_counter"]]
    )


def post(engine_url: str, body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        engine_url.rstrip("/") + "/bootstrap-curves",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data: dict[str, Any] = json.loads(resp.read())
        return data


def check_preset(engine_url: str, preset: Preset, strip: dict[str, Any]) -> tuple[float, int]:
    quotes = [cb.CurveQuote.model_validate(q) for q in strip["quotes"]]
    built = cb.build_curve(preset.id, preset, quotes, strip["reference_date"])
    query = cb.build_query(
        preset.id,
        ["DF"],
        strip["grid"],
        None,
        preset.index.calendar,
        preset.index.business_day_convention,
    )
    body = cb.bootstrap_request(
        [built.curve], built.indices, strip["reference_date"], [query.query]
    )
    result = post(engine_url, body)["results"][0]
    engine_dfs = next(s for s in result["series"] if s.get("measure", "DF") == "DF")["values"]
    grid = result["grid_dates"]

    ql.Settings.instance().evaluationDate = ql_date(strip["reference_date"])
    index = ql_index(built.indices[0])
    curve = ql_curve(built.curve, index)
    curve.enableExtrapolation()
    worst = 0.0
    for iso, engine_df in zip(grid, engine_dfs, strict=True):
        ql_df = curve.discount(ql_date(iso))
        worst = max(worst, abs(ql_df - engine_df))
    return worst, len(grid)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--engine", default=os.environ.get("QUANTRA_ENGINE_URL", ""))
    ap.add_argument("--presets", nargs="*", default=curve_preset_ids())
    args = ap.parse_args()
    if not args.engine:
        print("QUANTRA_ENGINE_URL (or --engine) is required", file=sys.stderr)
        return 2
    strips = {k: v for k, v in json.loads(STRIPS_PATH.read_text()).items() if k[0] != "_"}
    print(f"QuantLib {ql.__version__}  engine {args.engine}  tolerance {TOL:g}")
    print(f"{'preset':<16} {'grid':>4} {'max|DF diff|':>14}  result")
    failed = False
    for pid in args.presets:
        try:
            worst, n = check_preset(args.engine, get_preset(pid), strips[pid])
        except Exception as exc:  # report and continue
            failed = True
            print(f"{pid:<16} {'-':>4} {'-':>14}  ERROR {exc}")
            continue
        ok = worst <= TOL
        failed |= not ok
        print(f"{pid:<16} {n:>4} {worst:>14.3e}  {'PASS' if ok else 'FAIL'}")
    print("result:", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
