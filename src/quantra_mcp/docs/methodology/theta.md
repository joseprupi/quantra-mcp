# Theta: exact definition

_Generated from the engine's documentation and source at `v0.7.0` (`ab4dd9b50fca`) by `scripts/pin_engine.py`. Every statement is an excerpt of that tree with its location `path@tag:Lstart-Lend` and the GitHub permalink `https://github.com/joseprupi/quantraserver/blob/v0.7.0/<path>#L..`; the one-line headings are paraphrases of the excerpt under them. Where the engine documents nothing, the last section says so._

Two definitions exist, selected by request flags. Rebump theta (`swaption_pricing_rebump`) is NPV(as-of + 1 day) - NPV(as-of) with the same market, in currency. Analytic theta (`swaption_pricing_details`, equity options) is QuantLib's calculator theta.

## What the engine does

### 1. Rebump theta = NPV repriced with the evaluation date rolled by `rollDays` minus the base NPV.

````cpp
            const double npvTomorrow = priceWithRebump(0.0, 0.0, 1);
            row.theta = npvTomorrow - npv;
````

Source: `src/evaluators/swaption_evaluator.cpp@v0.7.0:L667-L668`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/src/evaluators/swaption_evaluator.cpp#L667-L668>

### 2. `rollDays` is 1 day.

````cpp
    /// Eval-date roll in days for the theta leg.
    int rollDays = 1;
````

Source: `src/domain/swaption_rebump.h@v0.7.0:L25-L26`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/src/domain/swaption_rebump.h#L25-L26>

### 3. The roll leg is "bump 0 @ asOf + rollDays": no curve or vol bump, only the evaluation date moves.

````cpp
///   - roll:      bump 0 @ asOf + rollDays              (theta leg)
````

Source: `src/evaluators/swaption_evaluator.h@v0.7.0:L103-L103`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/src/evaluators/swaption_evaluator.h#L103-L103>

### 4. The theta-leg instrument is rebuilt so that evaluation-date-dependent pieces behave correctly under the rolled date.

````cpp
/// eval-date-dependent pieces (American exercise reference date, Bermudan
/// date validation) are evaluated at construction time, so the same instrument
/// rebuilds correctly under the rolled evaluation date of the theta leg.
````

Source: `src/evaluators/swaption_evaluator.h@v0.7.0:L58-L60`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/src/evaluators/swaption_evaluator.h#L58-L60>

### 5. Analytic theta (details flag): `calc.theta(forward, timeToExpiry)` from QuantLib's Bachelier calculator for Normal vol, or the Black calculator with the displacement for (shifted) lognormal.

````cpp
                    row.theta = calc.theta(row.atmForward, timeToExpiry);
````

Source: `src/evaluators/swaption_evaluator.cpp@v0.7.0:L641-L641`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/src/evaluators/swaption_evaluator.cpp#L641-L641>

### 6. Equity option theta: the QuantLib option's `theta()` times `quantity`.

````cpp
    out.theta = safeGreek([&]() { return oneAssetOption->theta() * quantity; });
````

Source: `src/evaluators/equity_option_evaluator.cpp@v0.7.0:L257-L257`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/src/evaluators/equity_option_evaluator.cpp#L257-L257>

### 7. Response fields that do not apply are omitted rather than reported as a sentinel (0.5.0 note).

````text
  vanilla-swap CMS diagnostics are now **omitted when inapplicable** instead of
  reporting a `-1.0` / `-1` sentinel (which was ambiguous for a legitimate
  negative value). The `has_cms_swap_rate` flag is removed — the `cms_swap_rate`
````

Source: `docs/versioning.md@v0.7.0:L176-L178`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/docs/versioning.md#L176-L178>

## Request fields that control it

- `pricing.options.swaption_pricing_rebump` selects the rebump definition; `swaption_pricing_details` the analytic one.

````text
    /// Include detailed swaption analytics (delta/vega/gamma/theta/DV01).
    swaption_pricing_details:bool = false;
    /// Include curve-rebump swaption analytics (Bloomberg-style).
    swaption_pricing_rebump:bool = false;
````

Source: `flatbuffers/fbs/pricing.fbs@v0.7.0:L60-L63`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/flatbuffers/fbs/pricing.fbs#L60-L63>

## Not documented in engine v0.7.0

- Units are not stated in engine prose. From source: rebump theta is a currency amount over one calendar day of evaluation-date roll (the curves are rebuilt at the new date from the same quotes); analytic theta is QuantLib's calculator value (a rate of change per year in QuantLib's convention). Divide / multiply accordingly before comparing with a vendor's daily theta.
- Whether the roll re-fixes any coupon or moves across a payment is not documented; test with `reprice_with` on `pricing.as_of_date` to see the one-day change directly.
