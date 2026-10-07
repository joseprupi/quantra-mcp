# Value curves: zero, discount-factor and forward points

_Generated from the engine's documentation and source at `v0.7.0` (`ab4dd9b50fca`) by `scripts/pin_engine.py`. Every statement is an excerpt of that tree with its location `path@tag:Lstart-Lend` and the GitHub permalink `https://github.com/joseprupi/quantraserver/blob/v0.7.0/<path>#L..`; the one-line headings are paraphrases of the excerpt under them. Where the engine documents nothing, the last section says so._

A curve given as values rather than par quotes interpolates ONE quantity: zero rates (`InterpolatedZero`), discount factors (`InterpolatedDiscount`) or instantaneous continuously-compounded forwards (`InterpolatedFwd`). The three disagree between nodes for the same market, so which table was pasted matters.

## What the engine does

### 1. Zero-rate point: date or tenor, calendar / convention, the zero rate, its compounding and frequency.

````text
/// Zero rate point for direct curve construction (no bootstrapping).
table ZeroRatePoint {
    /// Maturity date for the zero rate (YYYY-MM-DD).
    date:string;
    /// Alternative: tenor from reference date.
    tenor:Period;
    calendar:enums.Calendar = null;
    business_day_convention:enums.BusinessDayConvention = null;
    /// Zero rate for this maturity. Required.
    zero_rate:double = null;
    /// Compounding convention for the zero rate.
    compounding:enums.Compounding = null;
    /// Frequency (used when compounding != Continuous).
    frequency:enums.Frequency = null;
}
````

Source: `flatbuffers/fbs/term_structure.fbs@v0.7.0:L205-L219`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/flatbuffers/fbs/term_structure.fbs#L205-L219>

### 2. Discount-factor point: feeds a QuantLib `InterpolatedDiscountCurve`; the factor must be in (0, 1] and the first point (reference date) must be exactly 1.0.

````text
/// Discount-factor point for direct curve construction (no bootstrapping).
/// Feeds a QuantLib InterpolatedDiscountCurve: the discount factors are
/// interpolated directly, which differs from interpolating zero rates.
table DiscountFactorPoint {
    /// Maturity date for the discount factor (YYYY-MM-DD).
    date:string;
    /// Alternative: tenor from reference date.
    tenor:Period;
    calendar:enums.Calendar = null;
    business_day_convention:enums.BusinessDayConvention = null;
    /// Discount factor to this maturity. Required; must be in (0, 1]. The first
    /// point (reference date) must carry a discount factor of exactly 1.0.
    discount_factor:double = null;
}
````

Source: `flatbuffers/fbs/term_structure.fbs@v0.7.0:L221-L234`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/flatbuffers/fbs/term_structure.fbs#L221-L234>

### 3. Forward-rate point: the INSTANTANEOUS continuously-compounded forward f(t), not a period forward; a different interpolated quantity, so off-node values differ.

````text
/// Forward-rate point for direct curve construction (no bootstrapping).
/// Feeds a QuantLib InterpolatedForwardCurve: the INSTANTANEOUS,
/// continuously-compounded forward rate f(t) is interpolated directly (this is
/// the quantity InterpolatedForwardCurve integrates to obtain zero rates and
/// discount factors — NOT a simple/discrete forward over a period). This
/// interpolates a DIFFERENT quantity than InterpolatedZero or
/// InterpolatedDiscount, so off-node values differ for the same market data.
table ForwardRatePoint {
    /// Node date for the forward rate (YYYY-MM-DD).
    date:string;
    /// Alternative: tenor from reference date.
    tenor:Period;
    calendar:enums.Calendar = null;
    business_day_convention:enums.BusinessDayConvention = null;
    /// Instantaneous, continuously-compounded forward rate at this node.
    /// Required. May be negative in some markets; only non-finite is rejected.
    forward_rate:double = null;
}
````

Source: `flatbuffers/fbs/term_structure.fbs@v0.7.0:L236-L253`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/flatbuffers/fbs/term_structure.fbs#L236-L253>

### 4. The three interpolated families are distinct curves; explicit zero curves must say `InterpolatedZero`; all zero points in one curve share one compounding / frequency; `InterpolatedFwd` allows Linear / BackwardFlat / ForwardFlat only.

````text
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

Source: `docs/versioning.md@v0.7.0:L145-L157`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/docs/versioning.md#L145-L157>

### 5. Sampling zeros back out: by default on the curve's day counter, continuous compounding; forwards default to simple compounding and the instantaneous definition.

````text
/// Zero rate query parameters.
table ZeroRateQuery {
    use_curve_day_counter:bool = true;
    day_counter:enums.DayCounter = Actual365Fixed;
    compounding:enums.Compounding = Continuous;
    frequency:enums.Frequency = Annual;
}

/// Forward rate query parameters.
table ForwardRateQuery {
    use_curve_day_counter:bool = true;
    day_counter:enums.DayCounter = Actual365Fixed;
    compounding:enums.Compounding = Simple;
    frequency:enums.Frequency = Annual;
    forward_type:ForwardType = Instantaneous;
    instantaneous_eps_number:int = 1;
    instantaneous_eps_time_unit:enums.TimeUnit = Days;
    tenor:Period;
    use_grid_calendar_for_advance:bool = true;
````

Source: `flatbuffers/fbs/curve_query.fbs@v0.7.0:L24-L42`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/flatbuffers/fbs/curve_query.fbs#L24-L42>

## Request fields that control it

- `bootstrap_trait` = `InterpolatedZero` | `InterpolatedDiscount` | `InterpolatedFwd`; `interpolator`; per-point `compounding` / `frequency` (zero points only).

````text
/// Bootstrap trait for curve construction.
enum BootstrapTrait : byte {
    Discount = 0,
    FwdRate = 1,
    InterpolatedDiscount = 2,
    InterpolatedFwd = 3,
    InterpolatedZero = 4,
    ZeroRate = 5
}
````

Source: `flatbuffers/fbs/enums.fbs@v0.7.0:L37-L45`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/flatbuffers/fbs/enums.fbs#L37-L45>

## Not documented in engine v0.7.0

- The engine does not document how a vendor's own curve interpolates; when a pasted table reprices a trade differently from the vendor, interpolation between the pasted nodes is the first hypothesis to test (`reprice_with` on `interpolator`, or paste the other quantity).
