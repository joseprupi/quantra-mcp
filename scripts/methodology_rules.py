"""Curated extraction rules for the ``quantra://methodology/*`` pages.

Nothing here is a claim about finance. Each topic lists WHERE in the engine's
own documentation and source (at the pinned tag) a behaviour is stated; the
generator (``pin_engine.py``) reads those files with ``git show``, cuts the
cited lines out verbatim, and renders a page in which every statement is the
excerpt plus a one-line paraphrase and a ``path@tag:Lstart-Lend`` citation.
Where the engine documents nothing the topic says so under "Not documented".

A ``cite`` locates an excerpt by content, not by fixed line numbers, so a
re-pin keeps working while the rendered citation carries the real lines:

* ``path``: file in the engine repository.
* ``start``: regex of the first excerpt line (``re.search``); ``nth`` picks the
  n-th match (1-based) when the pattern repeats.
* ``end``: regex of the last excerpt line, searched from ``start`` on
  (inclusive); or an int = number of lines.
"""

from __future__ import annotations

from typing import Any

Cite = dict[str, Any]
Statement = dict[str, Any]
Topic = dict[str, Any]

HTTP_API = "docs/http-api.md"
VERSIONING = "docs/versioning.md"
REBUMP_H = "src/domain/swaption_rebump.h"
SWPT_CPP = "src/evaluators/swaption_evaluator.cpp"
SWPT_H = "src/evaluators/swaption_evaluator.h"
EQ_CPP = "src/evaluators/equity_option_evaluator.cpp"
VS_CPP = "src/evaluators/vanilla_swap_evaluator.cpp"
OIS_CPP = "src/evaluators/ois_swap_evaluator.cpp"
FRB_CPP = "src/evaluators/fixed_rate_bond_evaluator.cpp"
ENUM_CONV = "src/common/enum_convert.cpp"
CAL_OVR_H = "src/common/calendar_overrides.h"
ENUMS_FBS = "flatbuffers/fbs/enums.fbs"
TS_FBS = "flatbuffers/fbs/term_structure.fbs"
QUERY_FBS = "flatbuffers/fbs/curve_query.fbs"
PRICING_FBS = "flatbuffers/fbs/pricing.fbs"
SWPT_FBS = "flatbuffers/fbs/swaption.fbs"
SWPT_RESP_FBS = "flatbuffers/fbs/swaption_response.fbs"
SWPT_REQ_FBS = "flatbuffers/fbs/price_swaption_request.fbs"
VOL_FBS = "flatbuffers/fbs/volatility.fbs"
COMMON_FBS = "flatbuffers/fbs/common.fbs"
SCHEDULE_FBS = "flatbuffers/fbs/schedule.fbs"
VS_RESP_FBS = "flatbuffers/fbs/vanilla_swap_response.fbs"
OIS_RESP_FBS = "flatbuffers/fbs/ois_swap_response.fbs"
CDS_RESP_FBS = "flatbuffers/fbs/cds_response.fbs"
FRA_RESP_FBS = "flatbuffers/fbs/fra_response.fbs"
FRB_RESP_FBS = "flatbuffers/fbs/fixed_rate_bond_response.fbs"
EQ_RESP_FBS = "flatbuffers/fbs/equity_option_response.fbs"
CATALOG = "tests/functional/CATALOG.md"
TS_PARSER_CPP = "src/parsers/term_structure_parser.cpp"
TS_POINT_CPP = "src/parsers/term_structure_point_parser.cpp"
SCHED_PARSER_CPP = "src/parsers/schedule_parser.cpp"

ENGINE_REPO_URL = "https://github.com/joseprupi/quantraserver"
CONNECTOR_REPO_URL = "https://github.com/joseprupi/quantra-mcp"

# this server's own source (the connector-analytics page cites the working tree)
BUMPS_PY = "src/quantra_mcp/builders/bumps.py"
ANALYTICS_PY = "src/quantra_mcp/tools/analytics.py"
RECONCILE_PY = "src/quantra_mcp/tools/reconcile.py"


def c(path: str, start: str, end: str | int, nth: int = 1) -> Cite:
    return {"path": path, "start": start, "end": end, "nth": nth}


def s(say: str, cite: Cite) -> Statement:
    return {"say": say, "cite": cite}


#: Response metric -> methodology topic to consult (used by compare_results and
#: explain_method). Keys are engine response field names.
METRIC_TOPICS: dict[str, str] = {
    "npv": "npv",
    "fixed_leg_npv": "npv",
    "floating_leg_npv": "npv",
    "overnight_leg_npv": "npv",
    "default_leg_npv": "npv",
    "premium_leg_npv": "npv",
    "clean_price": "npv",
    "dirty_price": "npv",
    "spot_value": "npv",
    "fair_rate": "fair-rate",
    "fair_spread": "fair-rate",
    "fair_upfront": "fair-rate",
    "atm_forward": "fair-rate",
    "used_atm_forward": "fair-rate",
    "forward_rate": "fair-rate",
    "annuity": "fair-rate",
    "fixed_leg_bps": "fair-rate",
    "floating_leg_bps": "fair-rate",
    "overnight_leg_bps": "fair-rate",
    "bps": "fair-rate",
    "dv01": "greeks-bump-and-reprice",
    "delta": "greeks-bump-and-reprice",
    "gamma": "greeks-bump-and-reprice",
    "vega": "greeks-bump-and-reprice",
    "rho": "greeks-bump-and-reprice",
    "macaulay_duration": "greeks-bump-and-reprice",
    "modified_duration": "greeks-bump-and-reprice",
    "convexity": "greeks-bump-and-reprice",
    "theta": "theta",
    "implied_volatility": "volatility-types",
    "used_volatility": "volatility-types",
    "yield": "day-counters-and-compounding",
    "accrued_amount": "day-counters-and-compounding",
    "accrued_days": "day-counters-and-compounding",
}


TOPICS: list[Topic] = [
    {
        "slug": "npv",
        "title": "NPV: what the engine's `npv` is",
        "summary": (
            "Every `npv` the engine returns is QuantLib's `NPV()` of the instrument it built "
            "from the request, discounted on the curve the request names. The engine adds no "
            "adjustment of its own; the sign is QuantLib's for the side the request states."
        ),
        "statements": [
            s(
                "A fixed-vs-IBOR swap is built as a QuantLib `VanillaSwap` for the stated "
                "`swap_type` and priced with a `DiscountingSwapEngine` on the discounting curve; "
                "`npv`, the fair rate / spread and the leg NPVs and BPS are read off that object.",
                c(
                    VS_CPP,
                    r"auto swap = std::make_shared<QuantLib::VanillaSwap>\(",
                    r"hasCmsLeg = false",
                ),
            ),
            s(
                "If the fixed and floating notionals differ, the fixed leg's notional is used "
                "(a warning, not an error).",
                c(VS_CPP, r"if \(trade.fixed.notional != trade.ibor.notional\)", 5),
            ),
            s(
                "An OIS swap is priced the same way (`DiscountingSwapEngine` on the discounting "
                "curve); `npv`, fair rate / spread, leg BPS and leg NPVs are QuantLib's.",
                c(
                    OIS_CPP,
                    r"std::make_shared<QuantLib::DiscountingSwapEngine>\(discHandle\)\);",
                    r"out.overnightLegNpv",
                ),
            ),
            s(
                "The swap response fields: `npv`, `fair_rate`, `fair_spread`, per-leg `bps` and "
                "`npv`, optional flows.",
                c(VS_RESP_FBS, r"^table VanillaSwapResponse", r"floating_leg_flows"),
            ),
            s(
                "A swaption's `npv` is \"the present value of the swaption under the selected "
                'model and market inputs".',
                c(SWPT_RESP_FBS, r"Present value of the swaption", 2),
            ),
            s(
                "An equity option's `npv` is the instrument's `NPV()` multiplied by the trade "
                "`quantity`; the greeks are scaled the same way.",
                c(EQ_CPP, r"const double npv = instrument->NPV\(\);", r"out.rho = safeGreek"),
            ),
            s(
                "A fixed-rate bond's `npv` is the bond's `NPV()`; clean / dirty price, accrued "
                "and yield are only computed when `bond_pricing_details` is set.",
                c(
                    FRB_CPP,
                    r"out.npv = trade.bond->NPV\(\);",
                    r"out.accruedAmount = trade.bond->accruedAmount\(\);",
                ),
            ),
            s(
                "A CDS response carries `npv`, the protection (`default_leg_npv`) and premium leg "
                "NPVs; `fair_spread` is absent when QuantLib cannot express one.",
                c(CDS_RESP_FBS, r"^table CDSValues", r"premium_leg_npv"),
            ),
            s(
                "A FRA response: `npv`, the curve-implied `forward_rate`, the value at settlement "
                "and the settlement date.",
                c(FRA_RESP_FBS, r"^table FRAResponse", r"settlement_date:string;"),
            ),
        ],
        "fields": [
            s(
                "`pricing.as_of_date` is the valuation date for everything in the request.",
                c(PRICING_FBS, r"Valuation date \(YYYY-MM-DD\)", 2),
            ),
            s(
                "`pricing.settlement_date` applies to bonds only.",
                c(PRICING_FBS, r"Settlement date \(YYYY-MM-DD\)", 2),
            ),
            s(
                "`discounting_curve` / `forwarding_curve` name curves in `pricing.rates.curves` "
                "by id (shown for swaptions; swaps and FRAs use the same two references).",
                c(SWPT_REQ_FBS, r"Reference to curve in pricing.rates.curves by id", 4),
            ),
        ],
        "gaps": [
            "Sign convention: not documented in engine prose. From source, the value is "
            "QuantLib's `NPV()` for the instrument built with the request's `swap_type` / "
            "`side` / option type, so the sign is QuantLib's for that side (a payer swap's "
            "NPV rises when rates rise). Test it with `reprice_with` flipping the side.",
            "Premium vs forward premium: the engine returns one present value as of "
            "`as_of_date`; it does not document a forward (deferred) premium field.",
        ],
    },
    {
        "slug": "fair-rate",
        "title": "Fair rate, fair spread, ATM forward",
        "summary": (
            "The engine reports QuantLib's `fairRate()` / `fairSpread()` for swaps, the "
            "curve-implied forward for FRAs, the par spread for CDS, and the ATM forward swap "
            "rate and annuity for swaptions (only when `swaption_pricing_details` is on)."
        ),
        "statements": [
            s(
                "Vanilla swap: `fair_rate` and `fair_spread` are `VanillaSwap::fairRate()` / "
                "`fairSpread()`.",
                c(VS_CPP, r"out.fairRate = swap->fairRate\(\);", 2),
            ),
            s(
                "With a notionals vector the engine reproduces the same formula explicitly: "
                "fair rate = fixed rate - NPV / (fixed-leg BPS / 1bp); fair spread likewise on "
                "the floating leg.",
                c(
                    VS_CPP,
                    r"fairRate/fairSpread via VanillaSwap's own formula",
                    r"trade.ibor.spread - out.npv",
                ),
            ),
            s(
                "A swap with a CMS leg has no fair rate / spread; the engine reports 0.0 there "
                "by design.",
                c(VS_CPP, r"fair_rate/fair_spread are not defined for a CMS swap", 4),
            ),
            s(
                "OIS swap: `fair_rate` / `fair_spread` are `OvernightIndexedSwap::fairRate()` / "
                "`fairSpread()`.",
                c(OIS_CPP, r"out.fairRate = swap->fairRate\(\);", 2),
            ),
            s(
                "CDS: `fair_spread` is the par spread in decimal, absent when QuantLib cannot "
                "express it (e.g. zero running coupon); `fair_upfront` is the upfront for the "
                "par spread.",
                c(CDS_RESP_FBS, r"Par spread in decimal", r"fair_upfront:double;"),
            ),
            s(
                "FRA: `forward_rate` is the implied forward from the forwarding curve.",
                c(FRA_RESP_FBS, r"The implied forward rate from the curve", 2),
            ),
            s(
                "Swaption: `atm_forward` is the ATM forward swap rate and `annuity` the "
                "underlying swap's annuity (PV01-style), both diagnostics.",
                c(SWPT_RESP_FBS, r"ATM forward swap rate used for pricing diagnostics", 4),
            ),
            s(
                "Those swaption diagnostics are read from QuantLib's additional results only "
                "when `swaption_pricing_details` is set.",
                c(SWPT_CPP, r"if \(ctx.options.swaptionPricingDetails\)", 4),
            ),
        ],
        "fields": [
            s(
                "`pricing.options.swaption_pricing_details` turns the swaption analytics on.",
                c(PRICING_FBS, r"Include detailed swaption analytics", 2),
            ),
        ],
        "gaps": [
            "A fair rate for bonds is not a concept the engine exposes; bonds report `yield` "
            "(see day-counters-and-compounding).",
            "Which curve projects the fair rate is not stated in prose: from source the swap is "
            "built on the index whose curve is `forwarding_curve` and discounted on "
            "`discounting_curve`, so the fair rate is the par rate under exactly those two curves.",
        ],
    },
    {
        "slug": "greeks-bump-and-reprice",
        "title": "DV01, gamma, vega: bump-and-reprice vs analytic",
        "summary": (
            "Swaption sensitivities exist in two forms selected by request flags: analytic "
            "(`swaption_pricing_details`, QuantLib's Black / Bachelier calculator) and "
            "bump-and-reprice (`swaption_pricing_rebump`: 1bp parallel curve bump, 1bp vol "
            "bump, 1-day roll). Equity greeks are analytic. Bonds report duration and "
            "convexity under `bond_pricing_details`. Swaps, FRAs, caps and CDS carry no "
            "sensitivities in the response."
        ),
        "statements": [
            s(
                "Rebump bump sizes: a 1bp parallel curve bump (DV01 / gamma), a 1bp absolute "
                "vol bump (vega) and a 1-day evaluation-date roll (theta), fixed in one place.",
                c(REBUMP_H, r"Perturbation sizes for the swaption rebump greeks", r"^};"),
            ),
            s(
                "The three pre-built market snapshots: curve up, curve down (parallel +/- bump "
                "at as-of) and roll (no bump, as-of + roll days). The vol-bump legs reuse the "
                "base curves.",
                c(SWPT_H, r"The fixed set of pre-built market snapshots", r"bool present = false;"),
            ),
            s(
                "Rebump definitions: dv01 = (NPV(+1bp) - NPV(-1bp)) / 2; gamma = NPV(+1bp) "
                "- 2 NPV + NPV(-1bp); vega = (NPV(vol+1bp) - NPV(vol-1bp)) / 2; theta = "
                "NPV(tomorrow) - NPV.",
                c(
                    SWPT_CPP,
                    r"if \(ctx.options.swaptionPricingRebump\)",
                    r"row.theta = npvTomorrow - npv;",
                ),
            ),
            s(
                "Analytic path (`swaption_pricing_details`): delta, vega, gamma, theta from "
                "QuantLib's `BachelierCalculator` (Normal vol) or `BlackCalculator` (with the "
                "displacement added to strike and forward), and dv01 = delta x 1bp.",
                c(
                    SWPT_CPP,
                    r"if \(row.annuity != 0.0 && stdDev > 0.0 && timeToExpiry > 0.0\)",
                    r"row.dv01 = row.delta \* 1.0e-4;",
                ),
            ),
            s(
                'The response documents `dv01` as "may be analytic or rebump-based depending on '
                'request flags" and delta / vega / gamma / theta as "from pricing details / '
                'rebump logic".',
                c(
                    SWPT_RESP_FBS,
                    r"Option delta from pricing details/rebump logic",
                    r"dv01:double;",
                ),
            ),
            s(
                "Equity options: delta, gamma, vega, theta, rho are the QuantLib option's "
                "analytic greeks times `quantity`.",
                c(EQ_CPP, r"out.delta = safeGreek", r"out.rho = safeGreek"),
            ),
            s(
                "Bonds (`bond_pricing_details`): yield, modified and Macaulay duration, "
                "convexity and BPS via QuantLib `BondFunctions` at the settlement date.",
                c(
                    FRB_CPP,
                    r"if \(out.hasDetails\)",
                    r"\*trade.bond, \*discountCurve, ctx.settlement\);",
                ),
            ),
        ],
        "fields": [
            s(
                "`pricing.options`: `bond_pricing_details`, `bond_pricing_flows`, "
                "`swaption_pricing_details`, `swaption_pricing_rebump`.",
                c(PRICING_FBS, r"^table PricingOptions", r"^}"),
            ),
        ],
        "gaps": [
            "Swaps, FRAs, caps / floors, CDS and inflation products carry no sensitivity "
            "fields in the response. This server's `swap_dv01`, `key_rate_ladder`, `scenario` "
            "and `reprice_with` tools obtain them by repricing under bumped quotes "
            "(differences of engine NPVs, inputs shown).",
            "When both swaption flags are set the rebump block runs after the analytic one "
            "and overwrites dv01 / gamma / vega / theta (source order above).",
            "Units: the rebump dv01 is per 1bp parallel move of the curves the request names "
            "(not a key-rate or a 100bp number); the vega bump is 1e-4 absolute in the vol's "
            "own quotation units.",
        ],
    },
    {
        "slug": "theta",
        "title": "Theta: exact definition",
        "summary": (
            "Two definitions exist, selected by request flags. Rebump theta "
            "(`swaption_pricing_rebump`) is NPV(as-of + 1 day) - NPV(as-of) with the same "
            "market, in currency. Analytic theta (`swaption_pricing_details`, equity options) "
            "is QuantLib's calculator theta."
        ),
        "statements": [
            s(
                "Rebump theta = NPV repriced with the evaluation date rolled by `rollDays` "
                "minus the base NPV.",
                c(SWPT_CPP, r"const double npvTomorrow = priceWithRebump\(0.0, 0.0, 1\);", 2),
            ),
            s(
                "`rollDays` is 1 day.",
                c(REBUMP_H, r"Eval-date roll in days for the theta leg", 2),
            ),
            s(
                'The roll leg is "bump 0 @ asOf + rollDays": no curve or vol bump, only the '
                "evaluation date moves.",
                c(SWPT_H, r"- roll:\s+bump 0 @ asOf \+ rollDays", 1),
            ),
            s(
                "The theta-leg instrument is rebuilt so that evaluation-date-dependent pieces "
                "behave correctly under the rolled date.",
                c(SWPT_H, r"eval-date-dependent pieces \(American exercise", 3),
            ),
            s(
                "Analytic theta (details flag): `calc.theta(forward, timeToExpiry)` from "
                "QuantLib's Bachelier calculator for Normal vol, or the Black calculator with "
                "the displacement for (shifted) lognormal.",
                c(SWPT_CPP, r"row.theta = calc.theta\(row.atmForward, timeToExpiry\);", 1),
            ),
            s(
                "Equity option theta: the QuantLib option's `theta()` times `quantity`.",
                c(EQ_CPP, r"out.theta = safeGreek", 1),
            ),
            s(
                "Response fields that do not apply are omitted rather than reported as a "
                "sentinel (0.5.0 note).",
                c(VERSIONING, r"are now \*\*omitted when inapplicable\*\*", 3),
            ),
        ],
        "fields": [
            s(
                "`pricing.options.swaption_pricing_rebump` selects the rebump definition; "
                "`swaption_pricing_details` the analytic one.",
                c(PRICING_FBS, r"Include detailed swaption analytics", 4),
            ),
        ],
        "gaps": [
            "Units are not stated in engine prose. From source: rebump theta is a currency "
            "amount over one calendar day of evaluation-date roll (the curves are rebuilt at "
            "the new date from the same quotes); analytic theta is QuantLib's calculator "
            "value (a rate of change per year in QuantLib's convention). Divide / multiply "
            "accordingly before comparing with a vendor's daily theta.",
            "Whether the roll re-fixes any coupon or moves across a payment is not documented; "
            "test with `reprice_with` on `pricing.as_of_date` to see the one-day change "
            "directly.",
        ],
    },
    {
        "slug": "curve-bootstrap",
        "title": "Curve construction: traits, interpolators, helpers",
        "summary": (
            "A curve is a `TermStructure` whose required `bootstrap_trait` selects the "
            "family: `Discount` / `ZeroRate` / `FwdRate` bootstrap a QuantLib "
            "`PiecewiseYieldCurve` from rate helpers; `InterpolatedZero` / "
            "`InterpolatedDiscount` / `InterpolatedFwd` interpolate explicit values (see "
            "value-curves). Nothing is inferred from the point types."
        ),
        "statements": [
            s(
                "The `TermStructure` table: id, day counter, interpolator, the family selector "
                "(absent => rejected, no auto-dispatch), points, reference date.",
                c(TS_FBS, r"^/// Term structure \(yield curve\) definition\.", r"^}"),
            ),
            s(
                "The six families and what each builds.",
                c(
                    VERSIONING,
                    r"### Curve construction is now trait-driven",
                    r"mis-built before\)\.",
                ),
            ),
            s(
                "Interpolators available: BackwardFlat, ForwardFlat, Linear, LogCubic, LogLinear.",
                c(ENUMS_FBS, r"^/// Curve interpolation methods\.", r"^}"),
            ),
            s(
                "Helper (point) types a curve may carry.",
                c(TS_FBS, r"^/// Union of all supported curve point types\.", r"^}"),
            ),
            s(
                "A helper's quote is selected by presence (`rate` / `price` / `spread` / "
                "`quote_id`): supply exactly one; none is an error; a genuine 0 is representable.",
                c(HTTP_API, r"Presence, not sentinels, selects a variant", 3),
            ),
            s(
                "OIS helpers carry explicit overnight conventions (payment lag, averaging, "
                "lookback, lockout, observation shift); a zero payment lag was the silent "
                "pre-0.6.0 behaviour and shifts the long end measurably.",
                c(
                    VERSIONING,
                    r"### OIS helpers and the OIS swap leg",
                    r"from a literal zero-day lookback on indices with fixing days\)\.",
                ),
            ),
            s(
                "On OIS helpers the fixed-leg calendar / frequency / convention feed QuantLib's "
                "payment slots; the fixed-leg day counter is deprecated and ignored.",
                c(
                    TS_FBS,
                    r"Required\. Feeds QuantLib's PAYMENT calendar slot",
                    r"fixed_leg_day_counter:enums.DayCounter = null;",
                ),
            ),
            s(
                "The code: `day_counter`, `interpolator` and `bootstrap_trait` are each required "
                "on the `TermStructure`; the trait is the explicit family selector (no "
                "auto-dispatch from the point types).",
                c(
                    TS_PARSER_CPP,
                    r"if \(!ts->day_counter\(\)\.has_value\(\)\)",
                    r"auto trait = ts->bootstrap_trait\(\)\.value\(\);",
                ),
            ),
            s(
                "The code: for `Discount` / `ZeroRate` / `FwdRate` every point is parsed into a "
                "QuantLib `RateHelper` (a value point among them is a request error) and the "
                "helpers are handed to `buildCurve`.",
                c(
                    TS_PARSER_CPP,
                    r"// Bootstrap traits build a PiecewiseYieldCurve from rate helpers\. A",
                    r"return buildCurve\(ts, instruments\);",
                ),
            ),
            s(
                "The code: `buildCurve` uses a bootstrap tolerance of 1e-15, the curve's "
                "`reference_date` (else the evaluation date) and its `day_counter`, then "
                "switches on `interpolator` x `bootstrap_trait`.",
                c(
                    TS_PARSER_CPP,
                    r"^std::shared_ptr<YieldTermStructure> TermStructureParser::buildCurve\(",
                    r"switch \(ts->interpolator\(\)\.value\(\)\) \{",
                ),
            ),
            s(
                "The code: the `LogLinear` branch instantiates "
                "`QuantLib::PiecewiseYieldCurve<Discount | ZeroYield | ForwardRate, LogLinear>` "
                "(reference date, helpers, day counter, tolerance) for `bootstrap_trait` "
                "`Discount` / `ZeroRate` / `FwdRate`; `BackwardFlat`, `ForwardFlat` and "
                "`Linear` follow the same pattern with their own QuantLib interpolator class.",
                c(
                    TS_PARSER_CPP,
                    r"case enums::Interpolator_LogLinear:",
                    r'QUANTRA_INVALID_ARGUMENT\("Unsupported BootstrapTrait for LogLinear"\);',
                ),
            ),
            s(
                "The code: `LogCubic` maps to QuantLib's `MonotonicLogCubic`; an unknown "
                "`interpolator` or trait combination is a request error, never a default.",
                c(
                    TS_PARSER_CPP,
                    r"case enums::Interpolator_LogCubic:",
                    r'QUANTRA_INVALID_ARGUMENT\("Unsupported Interpolator"\);',
                ),
            ),
            s(
                "The code: an `OISHelper` becomes `QuantLib::OISRateHelper(settlement_days, "
                "tenor, quote, overnight index, deps.discount_curve, telescopic=false, "
                "payment_lag, fixed_leg_convention, fixed_leg_frequency, calendar, forward "
                "start 0, overnight spread 0, LastRelevantDate pillar, averaging_method, "
                "QuantLib defaults for end-of-month / fixed frequency / fixed calendar, "
                "lookback_days (wire 0 = QuantLib Null = off), lockout_days, "
                "apply_observation_shift)`.",
                c(
                    TS_POINT_CPP,
                    r'// Wire 0 = "no lookback"\. QuantLib encodes the off-state as',
                    r"requireBool\(point->apply_observation_shift\(\),",
                ),
            ),
            s(
                "The code: a `SwapHelper` becomes `QuantLib::SwapRateHelper(quote, tenor, "
                "calendar, sw_fixed_leg_frequency, sw_fixed_leg_convention, "
                "sw_fixed_leg_day_counter, ibor index, spread, fwd_start_days, "
                "deps.discount_curve)`; the index forwarding is always the curve being built.",
                c(
                    TS_POINT_CPP,
                    r"return std::make_shared<SwapRateHelper>\(",
                    r"^        \);$",
                ),
            ),
        ],
        "fields": [
            s(
                "`bootstrap_trait`, `interpolator`, `day_counter`, `reference_date`, `points` on "
                "every `TermStructure`.",
                c(TS_FBS, r"^table TermStructure", r"^}"),
            ),
        ],
        "gaps": [
            "Bootstrap accuracy, iteration limits and extrapolation policy are not documented "
            "in engine prose at this tag; from source (`buildCurve` above) the bootstrap "
            "tolerance is 1e-15 and everything else is QuantLib's `PiecewiseYieldCurve` "
            "default (a bond beyond the last pillar is rejected, see error-codes / the "
            "example `fixed_rate_bond_beyond_pillar_request`).",
            "The pillar dates the engine reports for a bootstrapped curve are the helpers' "
            "tenor dates, not necessarily the helper maturity nodes (this server's live "
            "finding, CHANGELOG 0.1.1); rebuilding a curve from sampled discount factors at "
            "those dates does not reproduce it between nodes.",
        ],
    },
    {
        "slug": "value-curves",
        "title": "Value curves: zero, discount-factor and forward points",
        "summary": (
            "A curve given as values rather than par quotes interpolates ONE quantity: zero "
            "rates (`InterpolatedZero`), discount factors (`InterpolatedDiscount`) or "
            "instantaneous continuously-compounded forwards (`InterpolatedFwd`). The three "
            "disagree between nodes for the same market, so which table was pasted matters."
        ),
        "statements": [
            s(
                "Zero-rate point: date or tenor, calendar / convention, the zero rate, its "
                "compounding and frequency.",
                c(TS_FBS, r"^/// Zero rate point for direct curve construction", r"^}"),
            ),
            s(
                "Discount-factor point: feeds a QuantLib `InterpolatedDiscountCurve`; the "
                "factor must be in (0, 1] and the first point (reference date) must be exactly "
                "1.0.",
                c(TS_FBS, r"^/// Discount-factor point for direct curve construction", r"^}"),
            ),
            s(
                "Forward-rate point: the INSTANTANEOUS continuously-compounded forward f(t), "
                "not a period forward; a different interpolated quantity, so off-node values "
                "differ.",
                c(TS_FBS, r"^/// Forward-rate point for direct curve construction", r"^}"),
            ),
            s(
                "The three interpolated families are distinct curves; explicit zero curves must "
                "say `InterpolatedZero`; all zero points in one curve share one compounding / "
                "frequency; `InterpolatedFwd` allows Linear / BackwardFlat / ForwardFlat only.",
                c(
                    VERSIONING,
                    r"- `InterpolatedZero` — interpolate zero rates directly",
                    r"mis-built before\)\.",
                ),
            ),
            s(
                "Sampling zeros back out: by default on the curve's day counter, continuous "
                "compounding; forwards default to simple compounding and the instantaneous "
                "definition.",
                c(
                    QUERY_FBS,
                    r"^/// Zero rate query parameters\.",
                    r"use_grid_calendar_for_advance:bool = true;",
                ),
            ),
        ],
        "fields": [
            s(
                "`bootstrap_trait` = `InterpolatedZero` | `InterpolatedDiscount` | "
                "`InterpolatedFwd`; `interpolator`; per-point `compounding` / `frequency` "
                "(zero points only).",
                c(ENUMS_FBS, r"^/// Bootstrap trait for curve construction\.", r"^}"),
            ),
        ],
        "gaps": [
            "The engine does not document how a vendor's own curve interpolates; when a pasted "
            "table reprices a trade differently from the vendor, interpolation between the "
            "pasted nodes is the first hypothesis to test (`reprice_with` on `interpolator`, "
            "or paste the other quantity).",
        ],
    },
    {
        "slug": "settlement-and-cash-settlement",
        "title": "Swaption settlement type and method",
        "summary": (
            "`settlement_type` (Physical | Cash) is required. `settlement_method` "
            "(PhysicalOTC | PhysicalCleared | CollateralizedCashPrice | ParYieldCurve) has a "
            "wire default of PhysicalOTC and is passed straight to QuantLib's "
            "`Settlement::Method`; the engine documents no formula of its own."
        ),
        "statements": [
            s(
                "The swaption table: exercise and settlement types are presence-required; the "
                "settlement method defaults to PhysicalOTC.",
                c(SWPT_FBS, r"^/// Swaption instrument definition\.", r"^}"),
            ),
            s(
                "Settlement type values.",
                c(ENUMS_FBS, r"^/// Swaption settlement: physical", r"^}"),
            ),
            s(
                "Settlement method values.",
                c(
                    ENUMS_FBS,
                    r"^/// Swaption settlement method when settlement_type is Cash",
                    r"^}",
                ),
            ),
            s(
                "Mapping to QuantLib: each method maps one-to-one onto "
                "`QuantLib::Settlement::Method`.",
                c(ENUM_CONV, r"^QuantLib::Settlement::Method SettlementMethodToQL", r"^}"),
            ),
            s(
                "An unknown settlement type fails closed (never silently Physical).",
                c(SWPT_CPP, r"QuantLib::Settlement::Type settlementType;", r"^    }$"),
            ),
            s(
                "Bonds: `pricing.settlement_date` is the bond settlement date.",
                c(PRICING_FBS, r"Settlement date \(YYYY-MM-DD\)", 2),
            ),
        ],
        "fields": [
            s(
                "`swaption.settlement_type`, `swaption.settlement_method`.",
                c(SWPT_FBS, r"Settlement type for swaption payoff handling", 5),
            ),
        ],
        "gaps": [
            "The engine does not document what each cash-settlement method computes; it is "
            "QuantLib's semantics (CollateralizedCashPrice discounts the annuity on the "
            "discount curve; ParYieldCurve uses the par-yield annuity). Test the effect "
            "directly with `reprice_with` on `swaptions[0].swaption.settlement_method`.",
            "`settlement_method` is the one swaption convention with a wire default; a "
            "cash-settled trade must state its method or it is priced as PhysicalOTC.",
        ],
    },
    {
        "slug": "volatility-types",
        "title": "Volatility types: Normal, Lognormal, ShiftedLognormal",
        "summary": (
            "Interest-rate vols carry a required `volatility_type`; `displacement` matters "
            "only for ShiftedLognormal. Normal vols price through QuantLib's Bachelier "
            "formula, (shifted) lognormal through Black with the shift added to strike and "
            "forward. Equity / FX vols are always lognormal. Vols are absolute decimals."
        ),
        "statements": [
            s(
                "Volatility type values.",
                c(
                    ENUMS_FBS,
                    r"^/// Volatility quote type: Normal, Lognormal, or ShiftedLognormal",
                    r"^}",
                ),
            ),
            s(
                "The IR vol base: `volatility_type` is presence-required (an omitted type is a "
                "400, never Normal by default); `displacement` only for ShiftedLognormal.",
                c(
                    VOL_FBS,
                    r"^/// IR volatility base \(Normal, Lognormal, ShiftedLognormal\)",
                    r"^}",
                ),
            ),
            s(
                "Equity / FX vol base: always lognormal, no displacement.",
                c(VOL_FBS, r"^/// Equity/FX volatility base", r"^}"),
            ),
            s(
                "Analytic swaption greeks: Normal => `BachelierCalculator`; otherwise "
                "`BlackCalculator` on (strike + displacement, forward + displacement).",
                c(
                    SWPT_CPP,
                    r"if \(volEntry.qlVolType == QuantLib::Normal\)",
                    r"row.theta = calc.theta\(row.atmForward \+ displacement, timeToExpiry\);",
                ),
            ),
            s(
                "Reporting back: a QuantLib lognormal vol with a non-zero displacement is "
                "labelled ShiftedLognormal.",
                c(ENUM_CONV, r"^quantra::enums::VolatilityType VolatilityTypeToFb", r"^}"),
            ),
            s(
                "Response: `used_volatility` is the vol actually queried from the surface; "
                "`implied_volatility` may be best-effort for non-constant surfaces.",
                c(SWPT_RESP_FBS, r"Implied volatility reported by the engine", 2),
            ),
            s(
                "Units as the engine's own catalog states them: a flat 80bp normal vol, a 15% "
                "shifted-lognormal vol with a 2% displacement.",
                c(CATALOG, r"EUR 5Y cap on normal \(Bachelier\) vols, 80bp", 2),
            ),
        ],
        "fields": [
            s(
                "`volatility_type`, `displacement`, `constant_vol` (or a matrix / cube payload) "
                "on the vol surface's `base`.",
                c(VOL_FBS, r"Quotation convention of the supplied vols", 6),
            ),
        ],
        "gaps": [
            "Vol units are not stated in a schema comment; the catalog's wording (80bp normal "
            "= 0.008, 15% lognormal = 0.15 in the example files) is the documented usage: "
            "absolute decimals, never percent or bp numbers.",
            "Normal-to-lognormal conversion is not performed by the engine; a vendor quoting "
            "the other type must be re-quoted by the user (or tested with `reprice_with` on "
            "`volatility_type` + the matching vol level).",
        ],
    },
    {
        "slug": "calendars-and-overrides",
        "title": "Calendars, business-day rules and per-request holiday overrides",
        "summary": (
            "Every date adjustment uses the calendar and business-day convention the request "
            "states (nothing is defaulted). A request may add or remove holidays for a "
            "calendar for its own duration via `calendar_overrides`; nothing is stored."
        ),
        "statements": [
            s(
                "Business-day conventions available.",
                c(ENUMS_FBS, r"^/// Business day convention for date adjustments\.", r"^}"),
            ),
            s(
                "Date generation rules available.",
                c(ENUMS_FBS, r"^/// Date generation rule for schedule construction\.", r"^}"),
            ),
            s(
                "Schedules: `end_of_month` is presence-required; `first_date` / "
                "`next_to_last_date` control the first / last stub.",
                c(
                    SCHEDULE_FBS,
                    r"Presence-required: absent-vs-false silently changes schedule dates",
                    r"Omit for no last stub",
                ),
            ),
            s(
                "Where `calendar_overrides` goes and its shape.",
                c(HTTP_API, r"^## Calendar holiday overrides", r"^\| `removed_holidays` \|"),
            ),
            s(
                "Semantics: an override applies to every use of that calendar in the request; "
                "an already-true override is accepted with no effect.",
                c(HTTP_API, r"^### Semantics", r"entry with no dates and an empty list\."),
            ),
            s(
                "Rejected override requests (400 naming the field path).",
                c(
                    HTTP_API,
                    r"^### Rejected requests",
                    r"report per-item errors inside a `200` response\.",
                ),
            ),
            s(
                "Notes: `UnitedStates` == `UnitedStatesSettlement`; an added holiday can move a "
                "fixing before as-of (422 unless the fixing is supplied); cached curves are kept "
                "per override set.",
                c(HTTP_API, r"^### Notes", r"unaffected\."),
            ),
            s(
                "Implementation: overrides are applied to QuantLib's calendar state for one "
                "request and reset afterwards (a worker handles one request at a time).",
                c(
                    CAL_OVR_H,
                    r"Per-request calendar holiday overrides\.",
                    r"a worker processes one request at a time\.",
                ),
            ),
        ],
        "fields": [
            s(
                "`pricing.calendar_overrides` (or top-level on the calendar endpoints): "
                "`[{calendar, added_holidays, removed_holidays}]`.",
                c(PRICING_FBS, r"Per-request holiday overrides \(optional\)", 3),
            ),
        ],
        "gaps": [],
    },
    {
        "slug": "day-counters-and-compounding",
        "title": "Day counters, compounding and yield conventions",
        "summary": (
            "Day counters and compounding are always what the request states: legs, indices, "
            "curves, zero points and bond yields each carry their own. Zero rates sampled "
            "from a curve default to the curve's day counter with continuous compounding "
            "unless the query says otherwise."
        ),
        "statements": [
            s(
                "Day count conventions available.",
                c(ENUMS_FBS, r"^/// Day count conventions for accrual and discounting\.", r"^}"),
            ),
            s(
                "Compounding conventions available.",
                c(ENUMS_FBS, r"^/// Compounding convention for rates\.", r"^}"),
            ),
            s(
                "The `Yield` convention block (day counter, compounding, frequency) used for "
                "bond yield quotation.",
                c(COMMON_FBS, r"^/// Yield/compounding convention specification\.", r"^}"),
            ),
            s(
                "Bond yield, durations and convexity are computed with the request's yield "
                "day counter / compounding / frequency.",
                c(
                    FRB_CPP,
                    r"out.yield = trade.bond->yield\(trade.yieldDc, trade.yieldComp",
                    1,
                ),
            ),
            s(
                "Zero points: each carries its compounding and frequency; all zero points of one "
                "curve must share them.",
                c(TS_FBS, r"Compounding convention for the zero rate\.", 4),
            ),
            s(
                "Zero / forward sampling defaults: curve day counter, Continuous (zeros) / "
                "Simple (forwards), Annual frequency.",
                c(QUERY_FBS, r"^table ZeroRateQuery", r"^}"),
            ),
            s(
                "Omitted conventions are errors, never defaults.",
                c(HTTP_API, r"\*\*Omitted is not defaulted\.\*\*", 6),
            ),
            s(
                "OIS helpers and the overnight leg: the fixed-leg / payment day counter fields "
                "are deprecated and ignored (the day count comes from the overnight index).",
                c(
                    VERSIONING,
                    r"`fixed_leg_day_counter` on both helpers and `day_counter` on the overnight",
                    4,
                ),
            ),
            s(
                "FRA: the unused day-counter / calendar / convention fields are accepted but "
                "ignored.",
                c(VERSIONING, r"The unused `FRA` day-counter/calendar/convention fields", 3),
            ),
        ],
        "fields": [
            s(
                "`day_counter` on legs, indices, curves and vol bases; `compounding` / "
                "`frequency` on zero points and `Yield`; `zero` / `fwd` query blocks on curve "
                "queries.",
                c(QUERY_FBS, r"^table CurveQuerySpec", r"^}"),
            ),
        ],
        "gaps": [
            "The engine does not restate QuantLib's day-count formulas; the enum names are "
            "QuantLib's classes of the same name.",
        ],
    },
    {
        "slug": "schedules-and-stubs",
        "title": "Schedules: date generation rules, stubs, end-of-month, conventions",
        "summary": (
            "Every leg schedule is a QuantLib `Schedule` built from the request's "
            "`calendar`, `frequency`, `convention`, `termination_date_convention`, "
            "`date_generation_rule` and `end_of_month` (all required) plus the optional stub "
            "anchors `first_date` / `next_to_last_date`. The rule decides from which end the "
            "regular periods are counted, and therefore where an odd period (a stub) falls."
        ),
        "statements": [
            s(
                "The `Schedule` table: the required fields and the two optional stub anchors "
                "(`first_date` controls the FIRST stub, `next_to_last_date` the LAST, "
                "symmetrically; omitted = no stub).",
                c(
                    SCHEDULE_FBS,
                    r"^/// Date schedule definition for payment and accrual dates\.",
                    r"^}",
                ),
            ),
            s(
                "Date generation rules available: Backward, CDS, Forward, OldCDS, "
                "ThirdWednesday, Twentieth, TwentiethIMM, Zero.",
                c(ENUMS_FBS, r"^/// Date generation rule for schedule construction\.", r"^}"),
            ),
            s(
                "Business-day conventions available (used for `convention`, "
                "`termination_date_convention` and the legs' `payment_convention`).",
                c(ENUMS_FBS, r"^/// Business day convention for date adjustments\.", r"^}"),
            ),
            s(
                "The code: every schedule field is presence-required; an omitted one is a 400 "
                "naming the field, never a default.",
                c(
                    SCHED_PARSER_CPP,
                    r"if \(!schedule->calendar\(\)\.has_value\(\)\)",
                    r'QUANTRA_INVALID_ARGUMENT\("Schedule\.end_of_month is required"\);',
                ),
            ),
            s(
                "The code: the stub anchors are optional and default to QuantLib's own "
                "`Date()` (no stub); when present they must lie strictly inside "
                "(effective_date, termination_date).",
                c(
                    SCHED_PARSER_CPP,
                    r"// Optional stub-period control\. Absent fields default to",
                    r"const bool hasNextToLast = schedule->next_to_last_date\(\) != NULL;",
                ),
            ),
            s(
                "The code: some rules (e.g. `Zero`) reject stub anchors; QuantLib's reason is "
                "surfaced as a named 400.",
                c(
                    SCHED_PARSER_CPP,
                    r"// With a stub date present, some DateGeneration rules",
                    r"// take the unwrapped path below\.",
                ),
            ),
            s(
                "The code: the QuantLib `Schedule` constructor call, argument by argument "
                "(effective, termination, period from `frequency`, calendar, convention, "
                "termination-date convention, date-generation rule, end-of-month).",
                c(
                    SCHED_PARSER_CPP,
                    r"return std::make_shared<QuantLib::Schedule>\(",
                    r"schedule->end_of_month\(\)\.value\(\)\);",
                    nth=2,
                ),
            ),
            s(
                "The code: each rule maps one-to-one onto `QuantLib::DateGeneration::Rule`.",
                c(ENUM_CONV, r"^QuantLib::DateGeneration::Rule DateGenerationToQL", r"^}"),
            ),
            s(
                "0.7.0 note: stub periods via `first_date` / `next_to_last_date` on every "
                "schedule-carrying product.",
                c(VERSIONING, r"^- \*\*Stub periods\*\*: optional `first_date`", 3),
            ),
            s(
                "Documented usage (engine catalog): a 2-month short first coupon from Backward "
                "generation with a `first_date` two months after the effective date; an "
                "18-month long first coupon likewise.",
                c(CATALOG, r"EUR 5Y bond with a SHORT first coupon \(2-month stub\)", 2),
            ),
            s(
                "Documented usage (engine catalog): a short first period on both swap legs "
                "with Forward generation anchored by `first_date`.",
                c(CATALOG, r"EUR 5Y payer swap with a short first period on both legs", 1),
            ),
        ],
        "fields": [
            s(
                "`schedule.date_generation_rule`, `schedule.end_of_month` (presence-required), "
                "`schedule.first_date`, `schedule.next_to_last_date` on every leg schedule.",
                c(
                    SCHEDULE_FBS,
                    r"Presence-required: absent-vs-false silently changes schedule dates",
                    r"Omit for no last stub",
                ),
            ),
        ],
        "gaps": [
            "Where Forward vs Backward places the odd period is not stated in engine prose; "
            "it is QuantLib's `Schedule` semantics for the rule the code passes through: "
            "`Backward` counts regular periods back from the termination date, so an "
            "off-grid tenor leaves the short (or, with `first_date`, long) period at the FRONT "
            "(a front stub, the market default for odd-dated swaps); `Forward` counts from "
            "the effective date, so the odd period falls at the END (a back stub). For an "
            "on-grid tenor (a spot-start 5Y annual / semiannual swap) the two rules generate "
            "identical dates. Test it with `reprice_with` on "
            "`swaps[0].<leg>.schedule.date_generation_rule`.",
            "`termination_date_convention` is not described in engine prose; from source it is "
            "QuantLib's `terminationDateConvention`: the business-day rule applied to the "
            "termination date alone (`convention` adjusts every other date).",
            "`end_of_month` is documented only as presence-required; the behaviour is "
            "QuantLib's: when true and the effective date is the last business day of its "
            "month, every generated date is rolled to month end.",
            "`Zero` (a single period), `ThirdWednesday` (IMM dates), `Twentieth` / "
            "`TwentiethIMM` / `CDS` / `OldCDS` (credit roll dates) are QuantLib's rules of the "
            "same name; the engine documents no behaviour of its own for them.",
            "This server's swap presets default `date_generation_rule` to `Backward` on the "
            "OIS / vanilla swap trade blocks (market standard, see the preset's "
            "`field_provenance`); the engine's own swap fixtures use `Forward`. The leg "
            "overrides (`<leg>_overrides.schedule.date_generation_rule`) set it per trade.",
        ],
    },
    {
        "slug": "error-codes",
        "title": "Error codes and what they mean",
        "summary": (
            "400 = the request is wrong (missing field, bad date, unsupported combination); "
            "422 = well-formed but unpriceable (a QuantLib-level failure); 404 = a referenced "
            "id is not in the request's pricing block. Retrying without changing the payload "
            "never helps. The `error` field carries the cause."
        ),
        "statements": [
            s(
                "Request rules: JSON content type, non-empty body, ISO dates, omitted is not "
                "defaulted, presence selects a variant.",
                c(
                    HTTP_API,
                    r"^## Request rules",
                    r"supplying none is an error, and a genuine `0` is representable\.",
                ),
            ),
            s(
                "Status code table.",
                c(HTTP_API, r"^## Status codes", r"will not help\."),
            ),
            s(
                "Error body: `error` is the field to read; `code` / `code_name` are the gRPC "
                "status; `message` repeats `error`.",
                c(HTTP_API, r"^## Error body", r"`message` repeats `error`\."),
            ),
            s(
                "Which omissions are 400s (0.5.0 list).",
                c(
                    VERSIONING,
                    r"^### Every request value and convention must be explicit",
                    r"\(were silently first-wins\)\.",
                ),
            ),
            s(
                "Headers: `X-Quantra-Api-Version` on every POST response; `X-Request-Id` echoed "
                "when sent.",
                c(HTTP_API, r"^## Headers", r"the engine log lines that produced it\."),
            ),
        ],
        "fields": [],
        "gaps": [],
    },
]


#: The connector-analytics page: what THIS server computes on top of engine outputs,
#: cited into this repository's own source (working tree at generation time, cited at
#: the short sha of HEAD). Rendered by ``methodology_gen.render_connector_topic``.
CONNECTOR_TOPIC: Topic = {
    "slug": "connector-analytics",
    "title": "Connector analytics: swap_dv01, key_rate_ladder, scenario, fair_rate, reprice_with",
    "summary": (
        "These five tools never price anything themselves. Every NPV they report is an engine "
        "output of a complete pricing request (echoed in `calls[*].result.request` / "
        "`base.request` / `changed.request`). What this server adds is (1) the edit of the "
        "request (a quote bumped by `bump_bp / 10 000`, a quote replaced, a field set) and "
        "(2) a subtraction, a halving or a sum of those engine NPVs. Every such derived number "
        "sits next to the raw per-call NPVs so it can be redone by hand."
    ),
    "ledger": [
        (
            "`calls[*].npv`, `npvs.*`, `ladder[*].npv_up` / `npv_down`, `table[*].npv`, "
            "`base` / `changed` results",
            "engine output (NPV of the request shown)",
        ),
        ("`fair_rate`, `fair_spread`", "engine output, read from the response; never solved here"),
        (
            "`swap_dv01.dv01`",
            "connector arithmetic: (npv_up - npv_down) / 2 (centered), "
            "npv_up - base_npv (up) or base_npv - npv_down (down)",
        ),
        (
            "`key_rate_ladder.ladder[*].dv01`, `parallel.dv01`",
            "connector arithmetic: the same difference per pillar / for all pillars",
        ),
        ("`key_rate_ladder.sum_of_buckets`", "connector arithmetic: sum of the ladder dv01s"),
        ("`scenario.table[*].change`", "connector arithmetic: npv - base_npv"),
        ("`reprice_with.differences.fields.*.difference`", "connector arithmetic: changed - base"),
        (
            "`compare_results.rows[*].abs_diff` / `rel_diff`",
            "connector arithmetic: quantra - external; abs_diff / |external|",
        ),
        (
            "bumped quotes (`bumped_quotes`, `calls[*].edits`, `changes_applied`)",
            "connector "
            "edit of the request: quote + bump_bp / 10 000 (futures price - bump_bp / 100), or the "
            "value set",
        ),
    ],
    "statements": [
        s(
            "A bump is a pure edit of the engine `TermStructure`: `bump_bp / 10_000` is added "
            "to the pillar's quoted rate or spread; a futures pillar moves by `-bump_bp / 100`; "
            "nothing else is computed.",
            c(
                BUMPS_PY,
                r'^"""Quote bumps for the analytics tools',
                r"are rejected with a message that names them\.",
            ),
        ),
        s(
            "Which field is bumped per point type, and which point types cannot be bumped in bp "
            "(discount factors, FX points; bond clean prices and `quote_id` pillars are refused "
            "in `_quote`).",
            c(
                BUMPS_PY,
                r"^#: point_type -> \(quote field, scale applied to bump_bp\)",
                r"^}$",
                nth=1,
            ),
        ),
        s(
            "Unbumpable point types and the reason reported.",
            c(BUMPS_PY, r"^_UNBUMPABLE: dict\[str, str\] = \{", r"^}$"),
        ),
        s(
            "The bump arithmetic itself (`after = before + bump_bp * scale`), recorded as "
            "`{curve, pillar, point_type, field, from, to, bump_bp}`.",
            c(
                BUMPS_PY,
                r"scale = -0\.01 if p\.field == \"futures_price\" else BP",
                r"\"bump_bp\": bump_bp,",
            ),
        ),
        s(
            "A `replace_quotes` entry sets one pillar's quote to the given value (no arithmetic).",
            c(BUMPS_PY, r"^def replace_quote\(", r"point\[p\.field\] = float\(value\)"),
        ),
        s(
            "Every reprice goes through the SAME pricing tool the trade would normally use "
            "(`price_vanilla_swap` / `price_ois_swap` internals), so each `calls[*].result` is "
            "a complete, replayable pricing result.",
            c(
                ANALYTICS_PY,
                r"^async def _price_spec\(",
                r"\"\"\"Reprice ``spec`` on a \(possibly bumped\) pricing block",
            ),
        ),
        s(
            "The NPV read from each engine response is `swaps[0].npv`, unchanged.",
            c(ANALYTICS_PY, r"^def _npv\(result: ToolResult\)", r"^    return None$"),
        ),
        s(
            "`spot` / tenor dates are resolved by the engine once (base call) and pinned for "
            "every reprice, so bumped requests differ from the base only in the quotes moved.",
            c(ANALYTICS_PY, r"^def _pin_dates\(", r"^    return spec$"),
        ),
        s(
            "DV01 methods: which bumped reprices each needs (`centered` = up and down; `up`; "
            "`down`).",
            c(ANALYTICS_PY, r"^def _sides\(", r"return \[\(\"down\", -bump_bp\)\]"),
        ),
        s(
            "The DV01 arithmetic: centered = (npv_up - npv_down) / 2; up = npv_up - base_npv; "
            "down = base_npv - npv_down. Nothing else.",
            c(ANALYTICS_PY, r"^def dv01_of\(", r"return base_npv - npvs\[\"down\"\]"),
        ),
        s(
            "`swap_dv01`: every pillar of the selected curve(s) is bumped on each side the "
            "method needs; the per-call NPVs are reported as `npvs`, the derived number as "
            "`dv01` with its `dv01_definition`.",
            c(
                ANALYTICS_PY,
                r"for label, signed in _sides\(method, bump_bp\):",
                r"bumped_quotes=quotes,",
            ),
        ),
        s(
            "`key_rate_ladder`: the buckets are the curve's ACTUAL pillars in wire order; one "
            "reprice per pillar and side plus a parallel reprice per side.",
            c(
                ANALYTICS_PY,
                r"sides = _sides\(method, bump_bp\)",
                r"f\"pillar:\{p\.label\}:\{side\}\", r\.with_curves",
            ),
        ),
        s(
            "`key_rate_ladder` rows: `dv01` per pillar from that pillar's own up / down NPVs; "
            "`sum_of_buckets` is their sum; `parallel.dv01` is the same difference with every "
            "pillar bumped together.",
            c(
                ANALYTICS_PY,
                r"by_label = \{c\[\"label\"\]: c for c in r\.calls\[1:\]\}",
                r"parallel\[\"dv01\"\] = dv01_of\(method, r\.base_npv, par_npvs\)",
            ),
        ),
        s(
            "`scenario`: `change = npv - base_npv` per named market variant; `edits` counts the "
            "quotes moved (listed in `calls[*].edits`).",
            c(
                ANALYTICS_PY,
                r"rows: list\[dict\[str, Any\]\] = \[",
                r"definitions=\{\"change\": \"npv - base_npv",
            ),
        ),
        s(
            "`fair_rate`: `fair_rate` / `fair_spread` are read from the engine response; when "
            "absent the tool says so and solves nothing.",
            c(
                ANALYTICS_PY,
                r"swap = _swap0\(base\[\"result\"\]\) or \{\}",
                r"message=None if fields else",
            ),
        ),
        s(
            "Reprices fan out concurrently, bounded by `QUANTRA_MAX_CONCURRENCY`; a failed "
            "reprice fails the whole result with its engine error.",
            c(ANALYTICS_PY, r"^    async def fan_out\(", r"^        return None$"),
        ),
        s(
            "`reprice_with`: a change is either `value` (set, any JSON) or `bump_bp` (add "
            "`bump_bp / 10_000` to an existing numeric field); what was done is recorded "
            "as `{path, kind, before, after}`.",
            c(RECONCILE_PY, r"^def apply_change\(", r"\"after\": after,"),
        ),
        s(
            "`reprice_with.request_diff`: every leaf that differs between the two requests.",
            c(
                RECONCILE_PY,
                r"^def request_diff\(",
                r"\"\"\"Every leaf that differs between two JSON values",
            ),
        ),
        s(
            "`reprice_with.differences`: the numeric top-level fields of the first priced item, "
            "`difference = changed - base` (subtraction only).",
            c(
                RECONCILE_PY,
                r"^def numeric_differences\(",
                r"\"arithmetic\": \"difference = changed - base \(subtraction only\)\",",
            ),
        ),
        s(
            "`compare_results`: `abs_diff = quantra - external`, `rel_diff = abs_diff / "
            "|external|` (null when external is 0).",
            c(
                RECONCILE_PY,
                r"row\[\"abs_diff\"\] = value - ext",
                r"row\[\"rel_diff\"\] = \(value - ext\) / abs\(ext\) if ext != 0 else None",
            ),
        ),
    ],
    "gaps": [
        "No sensitivity is analytic here: a DV01 is a finite difference of two engine NPVs "
        "1bp apart (or base and one bump); convexity is what makes `up`, `down` and `centered` "
        "differ, and what makes `sum_of_buckets` differ from `parallel.dv01`.",
        "A bumped pillar is re-bootstrapped by the engine with its neighbours fixed, so a "
        "key-rate bucket reshapes the forwards around that pillar; a small bucket may carry "
        "either sign.",
        "Bumps are applied to the quotes of the curve as given in the market (par rates, "
        "spreads, futures prices, zero / forward values). Discount-factor value curves cannot "
        "be bumped in bp; paste a zero or forward table, or use `reprice_with` on a specific "
        "field instead.",
        "Nothing here is a vendor's definition of DV01 / PV01 / key-rate; when a vendor "
        "reports a one-sided or a 1bp-up number, choose `method` accordingly and compare like "
        "with like.",
    ],
}
