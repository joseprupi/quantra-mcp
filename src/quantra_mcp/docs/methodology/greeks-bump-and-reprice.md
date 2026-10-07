# DV01, gamma, vega: bump-and-reprice vs analytic

_Generated from the engine's documentation and source at `v0.7.0` (`ab4dd9b50fca`) by `scripts/pin_engine.py`. Every statement is an excerpt of that tree with its location `path@tag:Lstart-Lend`; the one-line headings are paraphrases of the excerpt under them. Where the engine documents nothing, the last section says so._

Swaption sensitivities exist in two forms selected by request flags: analytic (`swaption_pricing_details`, QuantLib's Black / Bachelier calculator) and bump-and-reprice (`swaption_pricing_rebump`: 1bp parallel curve bump, 1bp vol bump, 1-day roll). Equity greeks are analytic. Bonds report duration and convexity under `bond_pricing_details`. Swaps, FRAs, caps and CDS carry no sensitivities in the response.

## What the engine does

### 1. Rebump bump sizes: a 1bp parallel curve bump (DV01 / gamma), a 1bp absolute vol bump (vega) and a 1-day evaluation-date roll (theta), fixed in one place.

````cpp
/// Perturbation sizes for the swaption rebump greeks. The defaults reproduce
/// exactly the literal constants the pricer used before snapshots were lifted
/// into the mapper: a 1bp parallel curve bump, a 1bp vol bump, and a 1-day
/// eval-date roll.
struct SwaptionRebumpConfig {
    /// Parallel curve bump in absolute units (1bp) for the DV01/gamma legs.
    double curveBump = 1.0e-4;
    /// Vol bump in absolute units (1bp) for the vega leg.
    double volBump = 1.0e-4;
    /// Eval-date roll in days for the theta leg.
    int rollDays = 1;
};
````

Source: `src/domain/swaption_rebump.h@v0.7.0:L16-L27`

### 2. The three pre-built market snapshots: curve up, curve down (parallel +/- bump at as-of) and roll (no bump, as-of + roll days). The vol-bump legs reuse the base curves.

````cpp
/// The fixed set of pre-built market snapshots the rebump greeks need. The
/// mapper populates these (and sets `present`) only when the request asks for
/// rebump greeks; otherwise they are left default and unused. The two vol-bump
/// legs (curveBump==0, rollDays==0) deliberately have no snapshot here — they
/// price against the base registry curves at asOf, which are already bootstrapped
/// identically.
///   - curveUp:   parallel +curveBump @ asOf            (DV01/gamma up leg)
///   - curveDown: parallel -curveBump @ asOf            (DV01/gamma down leg)
///   - roll:      bump 0 @ asOf + rollDays              (theta leg)
struct SwaptionRebumpMarkets {
    SwaptionRebumpedMarket curveUp;
    SwaptionRebumpedMarket curveDown;
    SwaptionRebumpedMarket roll;
    bool present = false;
````

Source: `src/evaluators/swaption_evaluator.h@v0.7.0:L95-L108`

### 3. Rebump definitions: dv01 = (NPV(+1bp) - NPV(-1bp)) / 2; gamma = NPV(+1bp) - 2 NPV + NPV(-1bp); vega = (NPV(vol+1bp) - NPV(vol-1bp)) / 2; theta = NPV(tomorrow) - NPV.

````cpp
        if (ctx.options.swaptionPricingRebump) {
            const double bump = 1.0e-4; // 1bp
            const double npvUp = priceWithRebump(bump, 0.0, 0);
            const double npvDown = priceWithRebump(-bump, 0.0, 0);
            row.dv01 = (npvUp - npvDown) / 2.0;
            row.gamma = (npvUp - 2.0 * npv + npvDown);

            const double volUp = priceWithRebump(0.0, bump, 0);
            const double volDown = priceWithRebump(0.0, -bump, 0);
            row.vega = (volUp - volDown) / 2.0;

            const double npvTomorrow = priceWithRebump(0.0, 0.0, 1);
            row.theta = npvTomorrow - npv;
````

Source: `src/evaluators/swaption_evaluator.cpp@v0.7.0:L656-L668`

### 4. Analytic path (`swaption_pricing_details`): delta, vega, gamma, theta from QuantLib's `BachelierCalculator` (Normal vol) or `BlackCalculator` (with the displacement added to strike and forward), and dv01 = delta x 1bp.

````cpp
            if (row.annuity != 0.0 && stdDev > 0.0 && timeToExpiry > 0.0) {
                QuantLib::Option::Type optType =
                    (swaption->underlying()->type() == QuantLib::Swap::Payer)
                        ? QuantLib::Option::Call
                        : QuantLib::Option::Put;
                if (volEntry.qlVolType == QuantLib::Normal) {
                    QuantLib::BachelierCalculator calc(
                        optType, strike, row.atmForward, stdDev, row.annuity);
                    row.delta = calc.deltaForward();
                    row.vega = calc.vega(timeToExpiry);
                    row.gamma = calc.gammaForward();
                    row.theta = calc.theta(row.atmForward, timeToExpiry);
                } else {
                    const double displacement = volEntry.displacement;
                    QuantLib::BlackCalculator calc(
                        optType, strike + displacement, row.atmForward + displacement,
                        stdDev, row.annuity);
                    row.delta = calc.deltaForward();
                    row.vega = calc.vega(timeToExpiry);
                    row.gamma = calc.gammaForward();
                    row.theta = calc.theta(row.atmForward + displacement, timeToExpiry);
                }
            }
            row.dv01 = row.delta * 1.0e-4;
````

Source: `src/evaluators/swaption_evaluator.cpp@v0.7.0:L630-L653`

### 5. The response documents `dv01` as "may be analytic or rebump-based depending on request flags" and delta / vega / gamma / theta as "from pricing details / rebump logic".

````text
    /// Option delta from pricing details/rebump logic.
    delta:double;
    /// Option vega from pricing details/rebump logic.
    vega:double;
    /// Option gamma from pricing details/rebump logic.
    gamma:double;
    /// Option theta from pricing details/rebump logic.
    theta:double;
    /// 1bp rate sensitivity. May be analytic or rebump-based depending on request flags.
    dv01:double;
````

Source: `flatbuffers/fbs/swaption_response.fbs@v0.7.0:L16-L25`

### 6. Equity options: delta, gamma, vega, theta, rho are the QuantLib option's analytic greeks times `quantity`.

````cpp
    out.delta = safeGreek([&]() { return oneAssetOption->delta() * quantity; });
    out.gamma = safeGreek([&]() { return oneAssetOption->gamma() * quantity; });
    out.vega = safeGreek([&]() { return oneAssetOption->vega() * quantity; });
    out.theta = safeGreek([&]() { return oneAssetOption->theta() * quantity; });
    out.rho = safeGreek([&]() { return oneAssetOption->rho() * quantity; });
````

Source: `src/evaluators/equity_option_evaluator.cpp@v0.7.0:L254-L258`

### 7. Bonds (`bond_pricing_details`): yield, modified and Macaulay duration, convexity and BPS via QuantLib `BondFunctions` at the settlement date.

````cpp
        if (out.hasDetails) {
            out.yield = trade.bond->yield(trade.yieldDc, trade.yieldComp, trade.yieldFreq);
            out.cleanPrice = trade.bond->cleanPrice();
            out.dirtyPrice = trade.bond->dirtyPrice();
            out.accruedAmount = trade.bond->accruedAmount();
            out.accruedDays = QuantLib::BondFunctions::accruedDays(*trade.bond);

            QuantLib::InterestRate interestRate(
                out.yield, trade.yieldDc, trade.yieldComp, trade.yieldFreq);

            out.modifiedDuration = QuantLib::BondFunctions::duration(
                *trade.bond, interestRate, QuantLib::Duration::Modified, ctx.settlement);
            out.macaulayDuration = QuantLib::BondFunctions::duration(
                *trade.bond, interestRate, QuantLib::Duration::Macaulay, ctx.settlement);
            out.convexity = QuantLib::BondFunctions::convexity(
                *trade.bond, interestRate, ctx.settlement);
            out.bps = QuantLib::BondFunctions::bps(
                *trade.bond, *discountCurve, ctx.settlement);
````

Source: `src/evaluators/fixed_rate_bond_evaluator.cpp@v0.7.0:L88-L105`

## Request fields that control it

- `pricing.options`: `bond_pricing_details`, `bond_pricing_flows`, `swaption_pricing_details`, `swaption_pricing_rebump`.

````text
table PricingOptions {
    /// Include bond analytics (duration, convexity). Used by: FixedRateBond, FloatingRateBond.
    bond_pricing_details:bool = false;
    /// Include cash flow details. Used by: FixedRateBond, FloatingRateBond.
    bond_pricing_flows:bool = false;
    /// Include detailed swaption analytics (delta/vega/gamma/theta/DV01).
    swaption_pricing_details:bool = false;
    /// Include curve-rebump swaption analytics (Bloomberg-style).
    swaption_pricing_rebump:bool = false;
}
````

Source: `flatbuffers/fbs/pricing.fbs@v0.7.0:L55-L64`

## Not documented in engine v0.7.0

- Swaps, FRAs, caps / floors, CDS and inflation products carry no sensitivity fields in the response. This server's `swap_dv01`, `key_rate_ladder`, `scenario` and `reprice_with` tools obtain them by repricing under bumped quotes (differences of engine NPVs, inputs shown).
- When both swaption flags are set the rebump block runs after the analytic one and overwrites dv01 / gamma / vega / theta (source order above).
- Units: the rebump dv01 is per 1bp parallel move of the curves the request names (not a key-rate or a 100bp number); the vega bump is 1e-4 absolute in the vol's own quotation units.
