"""The convenience-tool <-> engine-fixture mapping shared by the unit, live and
live_check layers.

For every pricing tool: the fixture it must reproduce, the tool arguments in
two forms (``live``: ``spot`` / tenor dates resolved by the engine; ``explicit``:
the fixture's dates spelled out, so the request builds hermetically) and how to
read the primary number out of the response. The ``market`` is the fixture's
own ``pricing`` block minus whatever the tool builds itself.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from quantra_mcp import examples_catalog as cat


def fixture_body(name: str) -> dict[str, Any]:
    return cat.load_body(cat.find(name))


def market_without(pricing: dict[str, Any], *keys: str) -> dict[str, Any]:
    """The pricing block minus the sections the tool is expected to add back."""
    p = copy.deepcopy(pricing)
    for k in keys:
        if "." in k:
            a, b = k.split(".")
            p[a].pop(b, None)
        else:
            p.pop(k, None)
    return p


@dataclass(frozen=True)
class ProductCase:
    tool: str
    fixture: str
    live: dict[str, Any]
    explicit: dict[str, Any]
    number: Callable[[Any], float]
    calendar_calls: int  # /calendar-advance calls the live form makes


def _npv(key: str) -> Callable[[Any], float]:
    return lambda r: float(r[key][0]["npv"])


def product_cases() -> list[ProductCase]:
    fx = {n: fixture_body(n) for n in FIXTURES}
    cases: list[ProductCase] = []

    p = fx["irs_eur_5y_payer_ois_discounted_multicurve"]["pricing"]
    base = dict(
        market=p,
        preset="EUR_EURIBOR_6M",
        swap_type="Payer",
        notional=10_000_000.0,
        fixed_rate=0.032,
        index_id="EUR_6M",
        discounting_curve="EUR_OIS",
        forwarding_curve="EUR_6M_CURVE",
    )
    cases.append(
        ProductCase(
            "price_vanilla_swap",
            "irs_eur_5y_payer_ois_discounted_multicurve",
            {**base, "effective_date": "spot", "tenor": "5Y"},
            {**base, "effective_date": "2025-01-17", "termination_date": "2030-01-17"},
            _npv("swaps"),
            2,
        )
    )

    p = fx["ois_usd_3y_payer_sofr"]["pricing"]
    base = dict(
        market=p,
        preset="USD_SOFR_OIS",
        swap_type="Payer",
        notional=10_000_000.0,
        fixed_rate=0.0405,
        payment_lag=0,
        discounting_curve="USD_SOFR_CURVE",
        forwarding_curve="USD_SOFR_CURVE",
    )
    cases.append(
        ProductCase(
            "price_ois_swap",
            "ois_usd_3y_payer_sofr",
            {**base, "effective_date": "spot", "tenor": "3Y"},
            {**base, "effective_date": "2025-01-17", "termination_date": "2028-01-17"},
            _npv("swaps"),
            2,
        )
    )

    p = fx["frb_eur_5y_at_par_annual_30360"]["pricing"]
    base = dict(
        market=p,
        preset="EUR_FIXED_BOND",
        face_amount=1_000_000.0,
        coupon_rate=0.031,
        discounting_curve="discount",
    )
    cases.append(
        ProductCase(
            "price_fixed_rate_bond",
            "frb_eur_5y_at_par_annual_30360",
            {**base, "issue_date": "spot", "tenor": "5Y"},
            {**base, "issue_date": "2025-01-17", "maturity_date": "2030-01-17"},
            _npv("bonds"),
            2,
        )
    )

    p = market_without(
        fx["frn_eur_5y_euribor6m_flat_semiannual"]["pricing"], "rates.coupon_pricers"
    )
    base = dict(
        market=p,
        preset="EUR_EURIBOR_6M",
        face_amount=1_000_000.0,
        index_id="EUR_6M",
        discounting_curve="discount",
        forwarding_curve="discount",
    )
    cases.append(
        ProductCase(
            "price_floating_rate_bond",
            "frn_eur_5y_euribor6m_flat_semiannual",
            {**base, "issue_date": "spot", "tenor": "5Y"},
            {**base, "issue_date": "2025-01-17", "maturity_date": "2030-01-17"},
            _npv("bonds"),
            2,
        )
    )

    p = market_without(fx["zcb_eur_10y_discount"]["pricing"], "options")
    base = dict(
        market=p,
        preset="EUR_FIXED_BOND",
        face_amount=1_000_000.0,
        issue_date="as_of",
        discounting_curve="discount",
        include_details=True,
    )
    cases.append(
        ProductCase(
            "price_zero_coupon_bond",
            "zcb_eur_10y_discount",
            {**base, "tenor": "10Y"},
            {**base, "maturity_date": "2035-01-15"},
            _npv("bonds"),
            1,
        )
    )

    p = fx["fra_eur_3x6_long_at_forward"]["pricing"]
    base = dict(
        market=p,
        preset="EUR_EURIBOR_3M",
        notional=1_000_000.0,
        strike=0.031319,
        side="Long",
        index_id="EUR_3M",
        discounting_curve="discount",
        forwarding_curve="discount",
    )
    cases.append(
        ProductCase(
            "price_fra",
            "fra_eur_3x6_long_at_forward",
            {**base, "months_to_start": 3, "months_to_end": 6},
            {**base, "start_date": "2025-04-17", "maturity_date": "2025-07-17"},
            _npv("fras"),
            3,
        )
    )

    p = market_without(fx["cap_eur_5y_itm_strike_2pct"]["pricing"], "volatility")
    base = dict(
        market=p,
        preset="EUR_EURIBOR_3M",
        cap_floor_type="Cap",
        notional=1_000_000.0,
        strike=0.02,
        index_id="EUR_3M",
        discounting_curve="discount",
        forwarding_curve="discount",
        vol={"constant": 0.2, "type": "Lognormal", "id": "vol_lognormal_20pct"},
        model="Black",
    )
    cases.append(
        ProductCase(
            "price_cap_floor",
            "cap_eur_5y_itm_strike_2pct",
            {**base, "effective_date": "spot", "tenor": "5Y"},
            {**base, "effective_date": "2025-01-17", "termination_date": "2030-01-17"},
            _npv("cap_floors"),
            2,
        )
    )

    p = market_without(fx["swpt_eur_1y5y_payer_near_atm_physical"]["pricing"], "volatility")
    under = {
        "swap_type": "Payer",
        "notional": 1_000_000.0,
        "fixed_rate": 0.031,
        "index_id": "EUR_6M",
    }
    base = dict(
        market=p,
        preset="EUR_EURIBOR_6M",
        exercise_date="2026-01-15",
        discounting_curve="discount",
        forwarding_curve="discount",
        vol={"constant": 0.2, "type": "Lognormal", "id": "swpt_vol_lognormal_20pct"},
        model="Black",
    )
    cases.append(
        ProductCase(
            "price_swaption",
            "swpt_eur_1y5y_payer_near_atm_physical",
            {**base, "underlying": {**under, "effective_date": "spot", "tenor": "5Y"}},
            {
                **base,
                "underlying": {
                    **under,
                    "effective_date": "2026-01-19",
                    "termination_date": "2031-01-19",
                },
            },
            _npv("swaptions"),
            2,
        )
    )

    p = market_without(fx["cds_eur_5y_buyer_100bp_spread_curve"]["pricing"], "credit", "volatility")
    base = dict(
        market=p,
        preset="EUR_CDS",
        side="Buyer",
        notional=10_000_000.0,
        running_coupon=0.01,
        discounting_curve="discount",
        model="MidPoint",
        credit_curve={
            "par_spreads": [
                {"tenor": "1Y", "spread": 0.008},
                {"tenor": "3Y", "spread": 0.0095},
                {"tenor": "5Y", "spread": 0.011},
                {"tenor": "7Y", "spread": 0.012},
                {"tenor": "10Y", "spread": 0.013},
            ],
            "id": "acme_ig_eur",
        },
    )
    cases.append(
        ProductCase(
            "price_cds",
            "cds_eur_5y_buyer_100bp_spread_curve",
            {**base, "tenor": "5Y"},
            {**base, "maturity": "2030-01-15"},
            _npv("cds_list"),
            1,
        )
    )

    eq = dict(
        as_of="2025-01-15",
        spot=100.0,
        strike=100.0,
        expiry="2026-01-15",
        option_type="Call",
        vol={"constant": 0.2, "id": "acme_vol_20pct"},
        rate_curve={"rate": 0.03, "end_date": "2035-01-15", "id": "eur_risk_free"},
        dividend_yield={"rate": 0.0, "end_date": "2035-01-15", "id": "acme_dividend_yield"},
        underlying_id="ACME",
        trade_id="ACME_CALL_ATM_1Y",
    )
    cases.append(
        ProductCase("price_equity_option", "eqopt_eur_call_atm_1y", eq, eq, _npv("options"), 0)
    )

    zc = fx["zciis_eur_5y_payer_linear_obs"]
    base = dict(
        market=zc["pricing"],
        inflation_index_id="EUHICP",
        fixings=zc["pricing"]["inflation"]["inflation_indices"][0]["fixings"],
        swap_type="Payer",
        notional=1_000_000.0,
        fixed_rate=0.021,
        discounting_curve="DISC",
        inflation_curve="HICP_ZC",
    )
    cases.append(
        ProductCase(
            "price_zc_inflation_swap",
            "zciis_eur_5y_payer_linear_obs",
            {**base, "tenor": "5Y"},
            {**base, "maturity_date": "2030-01-15"},
            _npv("swaps"),
            1,
        )
    )

    yy = fx["yyiis_eur_5y_payer_annual"]
    base = dict(
        market=yy["pricing"],
        inflation_index_id="EUHICP_YY",
        fixings=yy["pricing"]["inflation"]["inflation_indices"][0]["fixings"],
        swap_type="Payer",
        notional=1_000_000.0,
        fixed_rate=0.0215,
        discounting_curve="DISC",
        inflation_curve="HICP_YY",
    )
    cases.append(
        ProductCase(
            "price_yoy_inflation_swap",
            "yyiis_eur_5y_payer_annual",
            {**base, "tenor": "5Y"},
            {**base, "termination_date": "2030-01-15"},
            _npv("swaps"),
            1,
        )
    )
    return cases


FIXTURES = [
    "irs_eur_5y_payer_ois_discounted_multicurve",
    "ois_usd_3y_payer_sofr",
    "frb_eur_5y_at_par_annual_30360",
    "frn_eur_5y_euribor6m_flat_semiannual",
    "zcb_eur_10y_discount",
    "fra_eur_3x6_long_at_forward",
    "cap_eur_5y_itm_strike_2pct",
    "swpt_eur_1y5y_payer_near_atm_physical",
    "cds_eur_5y_buyer_100bp_spread_curve",
    "eqopt_eur_call_atm_1y",
    "zciis_eur_5y_payer_linear_obs",
    "yyiis_eur_5y_payer_annual",
]


def blog_ois_args(built_curve: dict[str, Any]) -> dict[str, Any]:
    """price_ois_swap arguments that must reproduce ``sofr-ois-swap-request`` from a
    curve built by ``build_curve(USD_SOFR_OIS, <SOFR strip>)``."""
    return dict(
        market={"curves": [built_curve]},
        as_of="2025-01-15",
        preset="USD_SOFR_OIS",
        swap_type="Payer",
        notional=10_000_000.0,
        fixed_rate=0.03,
        effective_date="spot",
        tenor="5Y",
        discounting_curve="USD_SOFR_OIS",
        forwarding_curve="USD_SOFR_OIS",
    )


def blog_ois_args_explicit(built_curve: dict[str, Any]) -> dict[str, Any]:
    args = blog_ois_args(built_curve)
    args.pop("tenor")
    args.update(effective_date="2025-01-17", termination_date="2030-01-17")
    return args


def usd_ois_swaption_args(fixture: str, settlement_method: str) -> dict[str, Any]:
    """price_swaption arguments that must reproduce the engine's USD SOFR OIS swaption
    fixture ``swaption_ois_request`` (cash settled, ParYieldCurve): 1M x 10Y payer,
    strike 3.367463%, Bachelier normal vol 102.67bp, as-of 2024-08-14. The market is the
    fixture's own pricing block minus the vol surface and model the tool builds back. The
    fixture carries no QuantLib reference value, so it stays out of ``FIXTURES``."""
    return dict(
        market=market_without(fixture_body(fixture)["pricing"], "volatility"),
        preset="USD_SOFR_OIS",
        underlying_type="OisSwap",
        underlying={
            "swap_type": "Payer",
            "notional": 1_000_000.0,
            "fixed_rate": 0.03367463,
            "effective_date": "spot",
            "tenor": "10Y",
        },
        exercise_date="2024-09-16",
        settlement_type="Cash",
        settlement_method=settlement_method,
        discounting_curve="USD_SOFR",
        forwarding_curve="USD_SOFR",
        vol={"constant": 0.010267, "type": "Normal", "id": "usd_sofr_swo_vol"},
        model="Bachelier",
    )


def usd_ois_swaption_args_explicit(fixture: str, settlement_method: str) -> dict[str, Any]:
    args = usd_ois_swaption_args(fixture, settlement_method)
    args["underlying"] = {
        **args["underlying"],
        "effective_date": "2024-09-18",
        "termination_date": "2034-09-18",
    }
    args["underlying"].pop("tenor")
    return args


USD_OIS_SWAPTION_FIXTURES = {
    "swaption_ois_request": "ParYieldCurve",
}
