# DV01, gamma, vega: bump-and-reprice vs analytic

_Generated from the engine's documentation and source at `v0.8.2` (`7b26e53ae084`) by `scripts/pin_engine.py`. Every statement is an excerpt of that tree with its location `path@tag:Lstart-Lend` and the GitHub permalink `https://github.com/joseprupi/quantraserver/blob/v0.8.2/<path>#L..`; the one-line headings are paraphrases of the excerpt under them. Where the engine documents nothing, the last section says so._

Swaption sensitivities exist in two forms selected by request flags: analytic (`swaption_pricing_details`: QuantLib's Black / Bachelier calculator built with discount 1, the swap annuity applied explicitly) and bump-and-reprice (`swaption_pricing_rebump`: +/-1bp parallel curve bump, +/-1bp vol bump, 1-calendar-day market roll). Since engine 0.8.0 both report the same units (currency per bp, gamma per bp^2, vega per bp of vol, theta per calendar day), every greek is an optional response field present only when computed, and the rebump dv01 / gamma / vega / theta take precedence when both flags are set. Equity greeks are analytic. Bonds report duration and convexity under `bond_pricing_details`. Swaps, FRAs, caps and CDS carry no sensitivities in the response.

## What the engine does

### 1. Units and definitions of every swaption greek under each flag, as the engine documents them: the two flags report the same units and are directly comparable; dv01 is the forward-rate delta (analytic) or a parallel curve bump (rebump).

````text
## Swaption greeks: units and definitions

`POST /price-swaption` can return greeks under two request flags in
`pricing.options`. Both report the same units, so the numbers are directly
comparable:

| Field | `swaption_pricing_details` (analytic) | `swaption_pricing_rebump` (bump-and-reprice) |
| --- | --- | --- |
| `delta` | dNPV for a 1bp move of the forward swap rate, annuity held fixed (currency per bp) | not produced (field absent) |
| `dv01` | equals `delta` | central difference of a +/-1bp parallel bump of the discounting and forwarding curves (currency per bp) |
| `gamma` | d2NPV/dF2 x 1e-8 (currency per bp^2) | second difference of the +/-1bp parallel curve bump |
| `vega` | dNPV for a 1bp (1e-4) move of the quoted vol (lognormal for Black, normal for Bachelier) | central difference of a +/-1bp vol bump |
| `theta` | pure time decay with forward, vol and annuity held fixed (-1/2 sigma^2 F^2 Gamma for Black, -1/2 sigma_N^2 Gamma for Bachelier), per calendar day | NPV(as_of + 1 calendar day) - NPV(as_of) after a clean one-day roll of the market (see below) |
````

Source: `docs/http-api.md@v0.8.2:L206-L218`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.8.2/docs/http-api.md#L206-L218>

### 2. Which fields each flag fills, that every greek is optional (`= null`, present exactly when computed; none with neither flag), and the precedence when both flags are set (rebump dv01 / gamma / vega / theta; analytic delta, atm_forward, annuity kept).

````text
The analytic values come from QuantLib's `BlackCalculator` /
`BachelierCalculator` built with discount `1`, multiplied by the swap annuity.
`atm_forward`, `annuity` and `delta` are filled only by
`swaption_pricing_details`; `implied_volatility` is reported whenever the
engine provides one. Every greek is an optional field (`= null` in the
schema): it is present exactly when it was computed. With
`swaption_pricing_rebump` alone, `delta`, `atm_forward` and `annuity` are
absent; with neither flag no greek is present at all.

The rebump roll rebuilds the market at `as_of_date + 1` with every explicit
`reference_date` (curves, helper curves, vol surfaces) shifted by the same
day, so the result equals `NPV(as_of_date + 1, reference dates + 1) -
NPV(as_of_date)` obtained from two plain requests. Date-anchored market data
(zero/discount/forward points pinned to a date, index fixings) is not shifted:
it describes the market, not the observer. The `used_atm_forward`,
`used_cube_node_atm` and `used_spread_from_atm` diagnostics are likewise
optional and absent when the vol surface carries no ATM level (or does not use
spread-from-ATM strikes).

Precedence: when both flags are set, the rebump `dv01`, `gamma`, `vega` and
`theta` overwrite the analytic ones; `delta`, `atm_forward` and `annuity` are
kept from the analytic pass.
````

Source: `docs/http-api.md@v0.8.2:L220-L241`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.8.2/docs/http-api.md#L220-L241>

### 3. Rebump bump sizes: a 1bp parallel curve bump (DV01 / gamma), a 1bp absolute vol bump (vega) and a 1-day evaluation-date roll (theta), fixed in one place.

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

Source: `src/domain/swaption_rebump.h@v0.8.2:L16-L27`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.8.2/src/domain/swaption_rebump.h#L16-L27>

### 4. The three pre-built market snapshots: curve up, curve down (parallel +/- bump at as-of) and roll (no bump, as-of + roll days). The vol-bump legs reuse the base curves.

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

Source: `src/evaluators/swaption_evaluator.h@v0.8.2:L91-L104`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.8.2/src/evaluators/swaption_evaluator.h#L91-L104>

### 5. Rebump definitions: dv01 = (NPV(+1bp) - NPV(-1bp)) / 2; gamma = NPV(+1bp) - 2 NPV + NPV(-1bp); vega = (NPV(vol+1bp) - NPV(vol-1bp)) / 2; theta = NPV(tomorrow) - NPV.

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

Source: `src/evaluators/swaption_evaluator.cpp@v0.8.2:L686-L698`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.8.2/src/evaluators/swaption_evaluator.cpp#L686-L698>

### 6. Analytic path (`swaption_pricing_details`): delta, gamma, vega, theta from QuantLib's `BachelierCalculator` (Normal vol) or `BlackCalculator` (with the displacement added to strike and forward), built with discount 1 and multiplied by the annuity and 1bp (1bp^2 for gamma, /365 for theta); dv01 = delta. Absent when the option is expired or has no vol.

````cpp
            if (*row.annuity != 0.0 && stdDev > 0.0 && timeToExpiry > 0.0) {
                // Analytic greeks in the same units as the rebump path:
                //   delta = dNPV/dF for a 1bp move of the forward swap rate
                //   gamma = d2NPV/dF2 per bp^2
                //   vega  = dNPV/dsigma for a 1bp (1e-4) move of the quoted vol
                //   theta = currency per calendar day of pure time decay,
                //           forward, vol and annuity held fixed.
                // The calculator is built with discount = 1 so every output is
                // "per unit annuity"; the annuity is applied explicitly. For
                // theta this matters: QuantLib's theta(spot, T) carries a
                // log(discount)/T * value term that is only meaningful when the
                // discount is a true discount factor, not an annuity. With
                // discount = 1 and spot = forward it reduces to the driftless
                // decay (-1/2 sigma^2 F^2 Gamma for Black, -1/2 sigma_N^2
                // Gamma for Bachelier).
                QuantLib::Option::Type optType =
                    (swaption->underlying()->type() == QuantLib::Swap::Payer)
                        ? QuantLib::Option::Call
                        : QuantLib::Option::Put;
                const double annuity = *row.annuity;
                const double bp = 1.0e-4;
                if (volEntry.qlVolType == QuantLib::Normal) {
                    QuantLib::BachelierCalculator calc(
                        optType, strike, *row.atmForward, stdDev, 1.0);
                    row.delta = annuity * calc.deltaForward() * bp;
                    row.gamma = annuity * calc.gammaForward() * bp * bp;
                    row.vega = annuity * calc.vega(timeToExpiry) * bp;
                    row.theta = annuity * calc.theta(*row.atmForward, timeToExpiry) / 365.0;
                } else {
                    const double displacement = volEntry.displacement;
                    const double shiftedForward = *row.atmForward + displacement;
                    QuantLib::BlackCalculator calc(
                        optType, strike + displacement, shiftedForward, stdDev, 1.0);
                    row.delta = annuity * calc.deltaForward() * bp;
                    row.gamma = annuity * calc.gammaForward() * bp * bp;
                    row.vega = annuity * calc.vega(timeToExpiry) * bp;
                    row.theta = annuity * calc.theta(shiftedForward, timeToExpiry) / 365.0;
                }
            }
            // Analytic DV01: the forward-rate delta per 1bp (annuity held
            // fixed). The rebump DV01 below bumps the whole curve instead and
            // overwrites this value when both flags are set. Absent (like the
            // other analytic greeks) when the option is expired or has no vol.
            if (row.delta) row.dv01 = row.delta;
````

Source: `src/evaluators/swaption_evaluator.cpp@v0.8.2:L640-L683`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.8.2/src/evaluators/swaption_evaluator.cpp#L640-L683>

### 7. What changed in 0.8.0 for the details greeks: the 0.7.0 values were in the calculators' raw units (delta and vega 10,000 times larger, gamma 1e8 times, theta computed with the annuity where a discount factor was expected).

````text
### Swaption `swaption_pricing_details` greeks: units corrected

The analytic greeks were written in the raw units of QuantLib's Black and
Bachelier calculators, and `theta` was computed with the swap annuity passed
where the calculator expects a discount factor, which made the number
meaningless. All details greeks are now in the same units as the rebump
greeks:

| Field | Unit (both modes) | Change from 0.7.0 (details mode) |
| --- | --- | --- |
| `delta` | currency per 1 bp move of the forward | divided by 10,000 |
| `gamma` | currency per bp² | divided by 1e8 |
| `vega` | currency per 1 bp of volatility | divided by 10,000 |
| `theta` | currency per calendar day | recomputed: time decay of the option premium with forward, volatility and annuity held fixed |
| `dv01` | currency per 1 bp | unchanged (equals `delta`) |

The values match native QuantLib computed with the same definitions.
````

Source: `docs/versioning.md@v0.8.2:L96-L112`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.8.2/docs/versioning.md#L96-L112>

### 8. 0.8.2: under `swaption_pricing_rebump` every curve pillar is shifted exactly once; a curve written with `quote_id` pillars no longer reports about twice the dv01 and four times the gamma of the same curve written inline.

````text
## Version 0.8.2 (October 2026) — rebump greeks bump curves once

`v0.8.2` is a patch release: no schema change, no change to any plain pricing
result.

### Fixes

- **Rebump greeks shift each curve quote exactly once.** With
  `swaption_pricing_rebump`, the engine rebuilds the curves with every quote
  moved by ±1bp and reprices. A curve pillar written with `quote_id` received
  that shift twice: once when the request's `quotes[]` were loaded and again
  when the pillar resolved its quote. Curves written entirely with `quote_id`
  pillars therefore reported a rebump `dv01` about twice and a `gamma` about
  four times the true value, and a curve mixing inline and `quote_id` pillars
  got a lopsided shock (only the `quote_id` pillars moved 2bp). `vega` and
  `theta`, plain pricing, `swaption_pricing_details` and curves written with
  inline rates were never affected. Every pillar is now shifted once, so the
  two spellings of the same market produce identical greeks.
````

Source: `docs/versioning.md@v0.8.2:L29-L46`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.8.2/docs/versioning.md#L29-L46>

### 9. The response schema: every greek is `= null` (absent unless computed) and each field states its unit and the flag that fills it.

````text
    /// Analytic forward delta (swaption_pricing_details): change in NPV for a 1bp
    /// move of the forward swap rate, annuity held fixed (Black/Bachelier
    /// deltaForward x annuity x 1e-4). Currency per bp. Absent unless
    /// swaption_pricing_details is set (the rebump path does not compute it).
    delta:double = null;
    /// Vega: change in NPV for a 1bp (1e-4) move of the quoted volatility
    /// (lognormal vol for Black, normal vol for Bachelier). Currency per bp of vol.
    /// swaption_pricing_details: analytic (calculator vega x annuity x 1e-4).
    /// swaption_pricing_rebump: central difference of a +/-1bp vol bump.
    /// When both flags are set the rebump value is reported. Absent when neither
    /// flag is set.
    vega:double = null;
    /// Gamma per bp^2. swaption_pricing_details: analytic d2NPV/dF2 of the forward
    /// swap rate x 1e-8, annuity held fixed. swaption_pricing_rebump: second
    /// difference of a +/-1bp parallel curve bump (npvUp - 2 npv + npvDown).
    /// When both flags are set the rebump value is reported. Absent when neither
    /// flag is set.
    gamma:double = null;
    /// Theta in currency per calendar day. swaption_pricing_details: pure time
    /// decay of the option premium with forward, volatility and annuity held fixed
    /// (-1/2 sigma^2 F^2 Gamma for Black, -1/2 sigma_N^2 Gamma for Bachelier,
    /// per year, divided by 365). swaption_pricing_rebump: NPV(as_of + 1 calendar
    /// day) - NPV(as_of) from a full market roll. When both flags are set the
    /// rebump value is reported. Absent when neither flag is set.
    theta:double = null;
    /// DV01 in currency per bp. swaption_pricing_details: equals `delta` (forward
    /// rate moved 1bp, annuity held fixed). swaption_pricing_rebump: central
    /// difference of a +/-1bp parallel bump of the discounting and forwarding
    /// curves (forward and annuity both move). When both flags are set the rebump
    /// value is reported. Absent when neither flag is set.
    dv01:double = null;
````

Source: `flatbuffers/fbs/swaption_response.fbs@v0.8.2:L19-L49`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.8.2/flatbuffers/fbs/swaption_response.fbs#L19-L49>

### 10. Equity options: delta, gamma, vega, theta, rho are the QuantLib option's analytic greeks times `quantity`.

````cpp
    out.delta = safeGreek([&]() { return oneAssetOption->delta() * quantity; });
    out.gamma = safeGreek([&]() { return oneAssetOption->gamma() * quantity; });
    out.vega = safeGreek([&]() { return oneAssetOption->vega() * quantity; });
    out.theta = safeGreek([&]() { return oneAssetOption->theta() * quantity; });
    out.rho = safeGreek([&]() { return oneAssetOption->rho() * quantity; });
````

Source: `src/evaluators/equity_option_evaluator.cpp@v0.8.2:L248-L252`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.8.2/src/evaluators/equity_option_evaluator.cpp#L248-L252>

### 11. Bonds (`bond_pricing_details`): yield, modified and Macaulay duration, convexity and BPS via QuantLib `BondFunctions` at the settlement date.

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
            out.convexity =
                QuantLib::BondFunctions::convexity(*trade.bond, interestRate, ctx.settlement);
            out.bps = QuantLib::BondFunctions::bps(*trade.bond, *discountCurve, ctx.settlement);
````

Source: `src/evaluators/fixed_rate_bond_evaluator.cpp@v0.8.2:L88-L104`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.8.2/src/evaluators/fixed_rate_bond_evaluator.cpp#L88-L104>

## Request fields that control it

- `pricing.options`: `bond_pricing_details`, `bond_pricing_flows`, `swaption_pricing_details`, `swaption_pricing_rebump` (each with its units and what it fills).

````text
table PricingOptions {
    /// Include bond analytics (duration, convexity). Used by: FixedRateBond, FloatingRateBond.
    bond_pricing_details:bool = false;
    /// Include cash flow details. Used by: FixedRateBond, FloatingRateBond.
    bond_pricing_flows:bool = false;
    /// Include analytic swaption greeks (delta/vega/gamma/theta/DV01) from the
    /// Black/Bachelier calculator: rate sensitivities per 1bp (gamma per bp^2),
    /// vega per 1bp of vol, theta per calendar day. Also fills atm_forward,
    /// annuity and implied_volatility. See SwaptionResponse field comments.
    swaption_pricing_details:bool = false;
    /// Include bump-and-reprice swaption analytics: dv01 and gamma from a +/-1bp
    /// parallel curve bump, vega from a +/-1bp vol bump, theta from a 1-calendar-
    /// day clean market roll (evaluation date AND every explicit reference_date
    /// move one day; date-anchored points and fixings stay). Same units as the
    /// analytic greeks. When both flags are set the rebump dv01/gamma/vega/theta
    /// overwrite the analytic ones; delta, atm_forward and annuity are only
    /// produced by swaption_pricing_details. Greeks not computed are absent.
    swaption_pricing_rebump:bool = false;
}
````

Source: `flatbuffers/fbs/pricing.fbs@v0.8.2:L55-L73`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.8.2/flatbuffers/fbs/pricing.fbs#L55-L73>

## Not documented in engine v0.8.2

- Swaps, FRAs, caps / floors, CDS and inflation products carry no sensitivity fields in the response. This server's `swap_dv01`, `key_rate_ladder`, `scenario` and `reprice_with` tools obtain them by repricing under bumped quotes (differences of engine NPVs, inputs shown).
- The rebump dv01 is per 1bp parallel move of the curves the request names (not a key-rate or a 100bp number) and moves forward and annuity together; the analytic dv01 moves the forward swap rate alone with the annuity held fixed, so the two dv01s differ by construction even on the same market.
- Known limit stated by the engine: on a SABR-calibrated surface the rebump roll moves the curves only.
