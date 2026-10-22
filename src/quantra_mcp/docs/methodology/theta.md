# Theta: exact definition

_Generated from the engine's documentation and source at `v0.12.0` (`5b1c016739b1`) by `scripts/pin_engine.py`. Every statement is an excerpt of that tree with its location `path@tag:Lstart-Lend` and the GitHub permalink `https://github.com/joseprupi/quantraserver/blob/v0.12.0/<path>#L..`; the one-line headings are paraphrases of the excerpt under them. Where the engine documents nothing, the last section says so._

Two definitions exist, selected by request flags, both in currency per calendar day since engine 0.8.0. Rebump theta (`swaption_pricing_rebump`) is NPV(as_of + 1 calendar day) - NPV(as_of) after a clean one-day roll of the whole market: the evaluation date and every explicit curve and vol `reference_date` move one day, date-anchored points and fixings stay. Analytic theta (`swaption_pricing_details`) is the pure time decay of the option premium with forward, vol and annuity held fixed, per calendar day. Equity options report QuantLib's calculator theta times quantity.

## What the engine does

### 1. Both thetas as the engine documents them (definition and unit under each flag).

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

Source: `docs/http-api.md@v0.12.0:L1371-L1383`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/docs/http-api.md#L1371-L1383>

### 2. The rebump roll moves every explicit `reference_date` (curves, helper curves, vol surfaces) with the evaluation date; date-anchored market data (zero / discount / forward points pinned to a date, index fixings) is not shifted, so theta equals the difference of two plain requests at as_of and as_of + 1 with the reference dates moved.

````text
The rebump roll rebuilds the market at `as_of_date + 1` with every explicit
`reference_date` (curves, helper curves, vol surfaces) shifted by the same
day, so the result equals `NPV(as_of_date + 1, reference dates + 1) -
NPV(as_of_date)` obtained from two plain requests. Date-anchored market data
(zero/discount/forward points pinned to a date, index fixings) is not shifted:
it describes the market, not the observer. The `used_atm_forward`,
`used_cube_node_atm` and `used_spread_from_atm` diagnostics are likewise
optional and absent when the vol surface carries no ATM level (or does not use
spread-from-ATM strikes).
````

Source: `docs/http-api.md@v0.12.0:L1394-L1402`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/docs/http-api.md#L1394-L1402>

### 3. Rebump theta = NPV repriced with the evaluation date rolled by `rollDays` minus the base NPV.

````cpp
            const double npvTomorrow = priceWithRebump(0.0, 0.0, 1);
            row.theta = npvTomorrow - npv;
````

Source: `src/evaluators/swaption_evaluator.cpp@v0.12.0:L707-L708`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/src/evaluators/swaption_evaluator.cpp#L707-L708>

### 4. `rollDays` is 1 day.

````cpp
    /// Eval-date roll in days for the theta leg.
    int rollDays = 1;
````

Source: `src/domain/swaption_rebump.h@v0.12.0:L25-L26`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/src/domain/swaption_rebump.h#L25-L26>

### 5. The roll leg is "bump 0 @ asOf + rollDays": no curve or vol bump, only the date moves.

````cpp
///   - roll:      bump 0 @ asOf + rollDays              (theta leg)
````

Source: `src/evaluators/swaption_evaluator.h@v0.12.0:L99-L99`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/src/evaluators/swaption_evaluator.h#L99-L99>

### 6. The theta-leg instrument is rebuilt so that evaluation-date-dependent pieces behave correctly under the rolled date.

````cpp
/// eval-date-dependent pieces (American exercise reference date, Bermudan
/// date validation) are evaluated at construction time, so the same instrument
/// rebuilds correctly under the rolled evaluation date of the theta leg.
````

Source: `src/evaluators/swaption_evaluator.h@v0.12.0:L56-L58`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/src/evaluators/swaption_evaluator.h#L56-L58>

### 7. What changed in 0.8.0: in 0.7.0 the roll left explicit reference dates where they were, so a request whose curves carry a `reference_date` (value curves, the common shape) reported theta as exactly zero, which was then dropped; rebump theta values change for every request from 0.8.0 on.

````text
### Swaption `swaption_pricing_rebump` theta: the roll now moves the whole market

Rebump `theta` is `NPV(tomorrow) − NPV(today)` with the market rolled one
calendar day. In 0.7.0 the roll moved the evaluation date but left explicit
curve and volatility `reference_date`s where they were, so a request whose
curves carry a `reference_date` (the common shape) reported `theta` as exactly
zero, which was then dropped, and helper-curve requests produced a partial
roll without option time decay. The roll now shifts every explicit reference
date and re-references the volatility entry, so `theta` includes both the
curve roll and the option's time decay. Rebump theta values therefore change
for every request. Known limit: SABR-calibrated surfaces roll curves only.
````

Source: `docs/versioning.md@v0.12.0:L479-L489`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/docs/versioning.md#L479-L489>

### 8. Analytic theta (details flag): the calculator's `theta(forward, timeToExpiry)` built with discount 1, times the annuity, divided by 365 (per calendar day); Bachelier for Normal vol, Black with the displacement for (shifted) lognormal.

````cpp
                    row.theta = annuity * calc.theta(*row.atmForward, timeToExpiry) / 365.0;
````

Source: `src/evaluators/swaption_evaluator.cpp@v0.12.0:L677-L677`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/src/evaluators/swaption_evaluator.cpp#L677-L677>

### 9. Equity option theta: the QuantLib option's `theta()` times `quantity`.

````cpp
    out.theta = safeGreek([&]() { return oneAssetOption->theta() * quantity; });
````

Source: `src/evaluators/equity_option_evaluator.cpp@v0.12.0:L251-L251`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/src/evaluators/equity_option_evaluator.cpp#L251-L251>

### 10. A theta that is exactly zero is reported as `0.0`; an absent theta means not computed (the flag was not set, or the option is expired / has no vol), never zero.

````text
## Response fields at their default value

Every scalar response field is serialized even when it equals its schema
default, so a `double` that is exactly `0.0` (for example a bond's
`accrued_amount` on a coupon date or a holiday `count`) is present in the JSON.
Fields declared optional (`= null` in the schema) are the exception: they are
absent until the engine sets them. A scalar that the engine computes only under
a request flag or condition (the swaption greeks, `atm_forward`, `annuity`,
the Hull-White `used_hw_*` diagnostics, `cms_swap_rate`, the CDS
`fair_spread`, every swap endpoint's `fair_rate` / `fair_spread`, the
`used_cms_*` block and the flow `kind`) is
declared optional for that reason, so its absence always means "not computed"
and a reported `0.0` always means "computed and equal to zero".
````

Source: `docs/http-api.md@v0.12.0:L1408-L1420`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/docs/http-api.md#L1408-L1420>

## Request fields that control it

- `pricing.options.swaption_pricing_rebump` selects the rebump definition; `swaption_pricing_details` the analytic one; with both set the rebump theta is reported.

````text
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
````

Source: `flatbuffers/fbs/pricing.fbs@v0.12.0:L62-L74`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/flatbuffers/fbs/pricing.fbs#L62-L74>

## Not documented in engine v0.12.0

- Whether the one-day roll re-fixes a coupon or crosses a payment date is not stated; test with `reprice_with` on `pricing.as_of_date` (and the curves' `reference_date`) to see the one-day change directly.
- Known limit stated by the engine: on a SABR-calibrated surface the roll moves the curves only.
- The engine source at this tag and the QuantLib source it is built on are readable with search_source / read_source (cite `<repo>:<path>@<tag>:L<a>-L<b>`); a point this page does not document is to be read there, never answered from memory.
