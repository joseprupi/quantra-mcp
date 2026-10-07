# Curve construction: traits, interpolators, helpers

_Generated from the engine's documentation and source at `v0.7.0` (`ab4dd9b50fca`) by `scripts/pin_engine.py`. Every statement is an excerpt of that tree with its location `path@tag:Lstart-Lend`; the one-line headings are paraphrases of the excerpt under them. Where the engine documents nothing, the last section says so._

A curve is a `TermStructure` whose required `bootstrap_trait` selects the family: `Discount` / `ZeroRate` / `FwdRate` bootstrap a QuantLib `PiecewiseYieldCurve` from rate helpers; `InterpolatedZero` / `InterpolatedDiscount` / `InterpolatedFwd` interpolate explicit values (see value-curves). Nothing is inferred from the point types.

## What the engine does

### 1. The `TermStructure` table: id, day counter, interpolator, the family selector (absent => rejected, no auto-dispatch), points, reference date.

````text
/// Term structure (yield curve) definition.
table TermStructure {
    id:string;
    day_counter:enums.DayCounter = null;
    interpolator:enums.Interpolator = null;
    /// Curve family selector. Present ⇒ selects how the curve is built from its
    /// points (Discount/ZeroRate/FwdRate bootstrap from rate helpers;
    /// InterpolatedZero interpolates explicit zero-rate points). Absent ⇒ the
    /// request is rejected — there is no auto-dispatch by point type.
    bootstrap_trait:enums.BootstrapTrait = null;
    points:[PointsWrapper];
    reference_date:string;
}
````

Source: `flatbuffers/fbs/term_structure.fbs@v0.7.0:L316-L328`

### 2. The six families and what each builds.

````text
### Curve construction is now trait-driven (six explicit families)

`TermStructure.bootstrap_trait` is **required** and selects the curve family;
the server no longer guesses from the point types, and each trait validates the
points it receives (mismatch → 400):

- `Discount` / `ZeroRate` / `FwdRate` — bootstrap a `PiecewiseYieldCurve` from
  rate helpers (deposits, swaps, …).
- `InterpolatedZero` — interpolate zero rates directly from `ZeroRatePoint`s.
- `InterpolatedDiscount` — interpolate discount factors from the new
  `DiscountFactorPoint`s.
- `InterpolatedFwd` — interpolate instantaneous forwards from the new
  `ForwardRatePoint`s (Linear / BackwardFlat / ForwardFlat only — QuantLib
  cannot integrate a log-interpolated forward).

The three interpolated families are genuinely distinct curves (interpolating
zeros, discount factors or forwards off the same points yields different values
between nodes). **Explicit zero-rate curves that previously sent
`bootstrap_trait: Discount` must send `InterpolatedZero`.** Zero-rate points in
one curve must share one `compounding`/`frequency` (a mixed set was silently
mis-built before).
````

Source: `docs/versioning.md@v0.7.0:L137-L157`

### 3. Interpolators available: BackwardFlat, ForwardFlat, Linear, LogCubic, LogLinear.

````text
/// Curve interpolation methods.
enum Interpolator : byte {
    BackwardFlat = 0,
    ForwardFlat = 1,
    Linear = 2,
    LogCubic = 3,
    LogLinear = 4,
}
````

Source: `flatbuffers/fbs/enums.fbs@v0.7.0:L22-L29`

### 4. Helper (point) types a curve may carry.

````text
/// Union of all supported curve point types.
union Point {
    DepositHelper,
    FRAHelper,
    FutureHelper,
    SwapHelper,
    BondHelper,
    OISHelper,
    DatedOISHelper,
    ZeroRatePoint,
    TenorBasisSwapHelper,
    FxSwapHelper,
    CrossCcyBasisHelper,
    DiscountFactorPoint,
    ForwardRatePoint
}
````

Source: `flatbuffers/fbs/term_structure.fbs@v0.7.0:L294-L309`

### 5. A helper's quote is selected by presence (`rate` / `price` / `spread` / `quote_id`): supply exactly one; none is an error; a genuine 0 is representable.

````text
- **Presence, not sentinels, selects a variant.** Where several quote forms are
  accepted (`rate` / `price` / `spread` / `quote_id`), supply exactly the one
  you mean; supplying none is an error, and a genuine `0` is representable.
````

Source: `docs/http-api.md@v0.7.0:L22-L24`

### 6. OIS helpers carry explicit overnight conventions (payment lag, averaging, lookback, lockout, observation shift); a zero payment lag was the silent pre-0.6.0 behaviour and shifts the long end measurably.

````text
### OIS helpers and the OIS swap leg: conventions are now explicit and required

`OISHelper` and `DatedOISHelper` gain five required fields, mirroring the
priced `OisFloatingLeg` field-for-field:

- `payment_lag` — business days between accrual end and coupon payment
  (US SOFR market standard: 2). Must be >= 0.
- `averaging_method` — `Compound` or `Simple` averaging of the overnight
  fixings.
- `lookback_days` — observation lookback; `0` = no lookback. Must be >= 0.
- `lockout_days` — end-of-period fixing lockout; `0` = no lockout. Must
  be >= 0.
- `apply_observation_shift` — whether lookback uses the shifted date's
  day-count weight.

On the priced `OisFloatingLeg`, the same fields (plus `payment_convention`
and `payment_calendar`) are now required rather than silently defaulted, and
the old `lookback_days = -1` "none" sentinel is gone — `0` means no lookback.
Internally a wire `0` maps to QuantLib's null lookback (measurably different
from a literal zero-day lookback on indices with fixing days).
````

Source: `docs/versioning.md@v0.7.0:L73-L92`

### 7. On OIS helpers the fixed-leg calendar / frequency / convention feed QuantLib's payment slots; the fixed-leg day counter is deprecated and ignored.

````text
    /// Required. Feeds QuantLib's PAYMENT calendar slot on the bootstrapped
    /// OIS (payment-date adjustment on both legs).
    calendar:enums.Calendar = null;
    /// Required. Feeds QuantLib's PAYMENT frequency slot on the bootstrapped
    /// OIS (the coupon frequency of both legs unless overridden by QuantLib's
    /// fixed-frequency override, which is not exposed here).
    fixed_leg_frequency:enums.Frequency = null;
    /// Required. Feeds QuantLib's PAYMENT business-day convention slot on the
    /// bootstrapped OIS.
    fixed_leg_convention:enums.BusinessDayConvention = null;
    /// DEPRECATED / accepted-but-unused. No QuantLib OISRateHelper overload
    /// accepts a fixed-leg or payment day counter (the day counts come from the
    /// overnight index and QuantLib's internal fixed-leg default), so this field
    /// is ignored if present and may be omitted. Kept optional for backward
    /// compatibility.
    fixed_leg_day_counter:enums.DayCounter = null;
````

Source: `flatbuffers/fbs/term_structure.fbs@v0.7.0:L119-L134`

## Request fields that control it

- `bootstrap_trait`, `interpolator`, `day_counter`, `reference_date`, `points` on every `TermStructure`.

````text
table TermStructure {
    id:string;
    day_counter:enums.DayCounter = null;
    interpolator:enums.Interpolator = null;
    /// Curve family selector. Present ⇒ selects how the curve is built from its
    /// points (Discount/ZeroRate/FwdRate bootstrap from rate helpers;
    /// InterpolatedZero interpolates explicit zero-rate points). Absent ⇒ the
    /// request is rejected — there is no auto-dispatch by point type.
    bootstrap_trait:enums.BootstrapTrait = null;
    points:[PointsWrapper];
    reference_date:string;
}
````

Source: `flatbuffers/fbs/term_structure.fbs@v0.7.0:L317-L328`

## Not documented in engine v0.7.0

- Bootstrap accuracy, iteration limits and extrapolation policy are not documented at this tag; the engine uses QuantLib's `PiecewiseYieldCurve` defaults (a bond beyond the last pillar is rejected, see error-codes / the example `fixed_rate_bond_beyond_pillar_request`).
- The pillar dates the engine reports for a bootstrapped curve are the helpers' tenor dates, not necessarily the helper maturity nodes (this server's live finding, CHANGELOG 0.1.1); rebuilding a curve from sampled discount factors at those dates does not reproduce it between nodes.
