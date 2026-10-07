# Curve construction: traits, interpolators, helpers

_Generated from the engine's documentation and source at `v0.7.0` (`ab4dd9b50fca`) by `scripts/pin_engine.py`. Every statement is an excerpt of that tree with its location `path@tag:Lstart-Lend` and the GitHub permalink `https://github.com/joseprupi/quantraserver/blob/v0.7.0/<path>#L..`; the one-line headings are paraphrases of the excerpt under them. Where the engine documents nothing, the last section says so._

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

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/flatbuffers/fbs/term_structure.fbs#L316-L328>

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

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/docs/versioning.md#L137-L157>

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

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/flatbuffers/fbs/enums.fbs#L22-L29>

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

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/flatbuffers/fbs/term_structure.fbs#L294-L309>

### 5. A helper's quote is selected by presence (`rate` / `price` / `spread` / `quote_id`): supply exactly one; none is an error; a genuine 0 is representable.

````text
- **Presence, not sentinels, selects a variant.** Where several quote forms are
  accepted (`rate` / `price` / `spread` / `quote_id`), supply exactly the one
  you mean; supplying none is an error, and a genuine `0` is representable.
````

Source: `docs/http-api.md@v0.7.0:L22-L24`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/docs/http-api.md#L22-L24>

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

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/docs/versioning.md#L73-L92>

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

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/flatbuffers/fbs/term_structure.fbs#L119-L134>

### 8. The code: `day_counter`, `interpolator` and `bootstrap_trait` are each required on the `TermStructure`; the trait is the explicit family selector (no auto-dispatch from the point types).

````cpp
    if (!ts->day_counter().has_value())
        QUANTRA_INVALID_ARGUMENT("TermStructure.day_counter is required");
    if (!ts->interpolator().has_value())
        QUANTRA_INVALID_ARGUMENT("TermStructure.interpolator is required");
    if (!ts->bootstrap_trait().has_value())
        QUANTRA_INVALID_ARGUMENT("TermStructure.bootstrap_trait is required");

    // The bootstrap trait is the EXPLICIT curve-family selector. It decides how
    // the curve is built from its points; the point types are validated against
    // it (no auto-dispatch, no silent mixing).
    auto trait = ts->bootstrap_trait().value();
````

Source: `src/parsers/term_structure_parser.cpp@v0.7.0:L81-L91`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/src/parsers/term_structure_parser.cpp#L81-L91>

### 9. The code: for `Discount` / `ZeroRate` / `FwdRate` every point is parsed into a QuantLib `RateHelper` (a value point among them is a request error) and the helpers are handed to `buildCurve`.

````cpp
        // Bootstrap traits build a PiecewiseYieldCurve from rate helpers. A
        // ZeroRatePoint or DiscountFactorPoint is direct data, not a helper, so
        // it is a request error.
        TermStructurePointParser pointParser;
        std::vector<std::shared_ptr<RateHelper>> instruments;
        instruments.reserve(points->size());

        for (flatbuffers::uoffset_t i = 0; i < points->size(); i++) {
            auto pw = points->Get(i);
            if (pw->point_type() == quantra::Point_ZeroRatePoint ||
                pw->point_type() == quantra::Point_DiscountFactorPoint ||
                pw->point_type() == quantra::Point_ForwardRatePoint) {
                QUANTRA_INVALID_ARGUMENT(
                    "bootstrap_trait " + bootstrapTraitName(trait) +
                    " builds from rate helpers but received a " +
                    pointTypeName(pw->point_type()));
            }
            auto point = pw->point();
            auto type  = pw->point_type();
            auto helper = pointParser.parse(type, point, quotes, curves, indices, bump);
            if (!helper)
                QUANTRA_INVALID_ARGUMENT("Failed to parse term structure point at index " + std::to_string(i));
            instruments.push_back(helper);
        }

        return buildCurve(ts, instruments);
````

Source: `src/parsers/term_structure_parser.cpp@v0.7.0:L323-L348`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/src/parsers/term_structure_parser.cpp#L323-L348>

### 10. The code: `buildCurve` uses a bootstrap tolerance of 1e-15, the curve's `reference_date` (else the evaluation date) and its `day_counter`, then switches on `interpolator` x `bootstrap_trait`.

````cpp
std::shared_ptr<YieldTermStructure> TermStructureParser::buildCurve(
    const quantra::TermStructure* ts,
    std::vector<std::shared_ptr<RateHelper>>& instruments)
{
    double tolerance = 1.0e-15;
    
    Date ref;
    if (ts->reference_date()) {
        ref = DateToQL(ts->reference_date()->str());
    } else {
        ref = Settings::instance().evaluationDate();
    }
    
    DayCounter dc = DayCounterToQL(ts->day_counter().value());

    switch (ts->interpolator().value()) {
````

Source: `src/parsers/term_structure_parser.cpp@v0.7.0:L361-L376`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/src/parsers/term_structure_parser.cpp#L361-L376>

### 11. The code: the `LogLinear` branch instantiates `QuantLib::PiecewiseYieldCurve<Discount | ZeroYield | ForwardRate, LogLinear>` (reference date, helpers, day counter, tolerance) for `bootstrap_trait` `Discount` / `ZeroRate` / `FwdRate`; `BackwardFlat`, `ForwardFlat` and `Linear` follow the same pattern with their own QuantLib interpolator class.

````cpp
    case enums::Interpolator_LogLinear:
        switch (ts->bootstrap_trait().value()) {
        case enums::BootstrapTrait_Discount:
            return std::make_shared<PiecewiseYieldCurve<Discount, LogLinear>>(
                ref, instruments, dc,
                PiecewiseYieldCurve<Discount, LogLinear>::bootstrap_type(tolerance));
        case enums::BootstrapTrait_ZeroRate:
            return std::make_shared<PiecewiseYieldCurve<ZeroYield, LogLinear>>(
                ref, instruments, dc,
                PiecewiseYieldCurve<ZeroYield, LogLinear>::bootstrap_type(tolerance));
        case enums::BootstrapTrait_FwdRate:
            return std::make_shared<PiecewiseYieldCurve<ForwardRate, LogLinear>>(
                ref, instruments, dc,
                PiecewiseYieldCurve<ForwardRate, LogLinear>::bootstrap_type(tolerance));
        default:
            QUANTRA_INVALID_ARGUMENT("Unsupported BootstrapTrait for LogLinear");
````

Source: `src/parsers/term_structure_parser.cpp@v0.7.0:L434-L449`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/src/parsers/term_structure_parser.cpp#L434-L449>

### 12. The code: `LogCubic` maps to QuantLib's `MonotonicLogCubic`; an unknown `interpolator` or trait combination is a request error, never a default.

````cpp
    case enums::Interpolator_LogCubic:
        switch (ts->bootstrap_trait().value()) {
        case enums::BootstrapTrait_Discount:
            return std::make_shared<PiecewiseYieldCurve<Discount, LogCubic>>(
                ref, instruments, dc, MonotonicLogCubic());
        case enums::BootstrapTrait_ZeroRate:
            return std::make_shared<PiecewiseYieldCurve<ZeroYield, LogCubic>>(
                ref, instruments, dc, MonotonicLogCubic());
        case enums::BootstrapTrait_FwdRate:
            return std::make_shared<PiecewiseYieldCurve<ForwardRate, LogCubic>>(
                ref, instruments, dc, MonotonicLogCubic());
        default:
            QUANTRA_INVALID_ARGUMENT("Unsupported BootstrapTrait for LogCubic");
        }
        break;

    default:
        QUANTRA_INVALID_ARGUMENT("Unsupported Interpolator");
````

Source: `src/parsers/term_structure_parser.cpp@v0.7.0:L453-L470`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/src/parsers/term_structure_parser.cpp#L453-L470>

### 13. The code: an `OISHelper` becomes `QuantLib::OISRateHelper(settlement_days, tenor, quote, overnight index, deps.discount_curve, telescopic=false, payment_lag, fixed_leg_convention, fixed_leg_frequency, calendar, forward start 0, overnight spread 0, LastRelevantDate pillar, averaging_method, QuantLib defaults for end-of-month / fixed frequency / fixed calendar, lookback_days (wire 0 = QuantLib Null = off), lockout_days, apply_observation_shift)`.

````cpp
        // Wire 0 = "no lookback". QuantLib encodes the off-state as
        // Null<Natural>, NOT as a zero-day lookback: an explicit 0 would force
        // the coupon fixing delay to 0 even when the index carries a non-zero
        // intrinsic fixing delay (different fixing dates, and it disables the
        // telescopic formula), so the two are not interchangeable.
        const int lookbackWire =
            requireNonNegativeInt(point->lookback_days(), "OISHelper.lookback_days");
        const Natural lookbackDays = lookbackWire == 0
            ? Null<Natural>()
            : static_cast<Natural>(lookbackWire);

        // fixed_leg_frequency / fixed_leg_convention / calendar feed QuantLib's
        // PAYMENT frequency / convention / calendar slots (see the schema
        // doc-comments). fixed_leg_day_counter is accepted-but-unused: no
        // OISRateHelper overload takes a day counter.
        return std::make_shared<OISRateHelper>(
            requireInt(point->settlement_days(), "OISHelper.settlement_days"),
            requirePeriod(point->tenor(), "OISHelper.tenor"),
            q,
            on,
            discount,
            false, // telescopicValueDates: helper-internal performance toggle
            requireNonNegativeInt(point->payment_lag(), "OISHelper.payment_lag"),
            ConventionToQL(requireEnum(point->fixed_leg_convention(),
                                       "OISHelper.fixed_leg_convention")),
            FrequencyToQL(requireEnum(point->fixed_leg_frequency(),
                                      "OISHelper.fixed_leg_frequency")),
            CalendarToQL(requireEnum(point->calendar(), "OISHelper.calendar")),
            0 * Days, // forwardStart: spot-starting (tenor helper)
            0.0,      // overnightSpread
            QuantLib::Pillar::LastRelevantDate,
            Date(),   // customPillarDate: unused with LastRelevantDate
            RateAveragingToQL(requireEnum(point->averaging_method(),
                                          "OISHelper.averaging_method")),
            ext::nullopt, // endOfMonth: QuantLib default
            ext::nullopt, // fixedPaymentFrequency: paymentFrequency drives both legs
            Calendar(),   // fixedCalendar: QuantLib default (index calendar)
            lookbackDays,
            static_cast<Natural>(requireNonNegativeInt(
                point->lockout_days(), "OISHelper.lockout_days")),
            requireBool(point->apply_observation_shift(),
````

Source: `src/parsers/term_structure_point_parser.cpp@v0.7.0:L396-L436`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/src/parsers/term_structure_point_parser.cpp#L396-L436>

### 14. The code: a `SwapHelper` becomes `QuantLib::SwapRateHelper(quote, tenor, calendar, sw_fixed_leg_frequency, sw_fixed_leg_convention, sw_fixed_leg_day_counter, ibor index, spread, fwd_start_days, deps.discount_curve)`; the index forwarding is always the curve being built.

````cpp
        return std::make_shared<SwapRateHelper>(
            q,
            requirePeriod(point->tenor(), "SwapHelper.tenor"),
            CalendarToQL(point->calendar().value()),
            FrequencyToQL(point->sw_fixed_leg_frequency().value()),
            ConventionToQL(point->sw_fixed_leg_convention().value()),
            DayCounterToQL(point->sw_fixed_leg_day_counter().value()),
            ibor,
            spread,
            point->fwd_start_days() * Days,
            discount
        );
````

Source: `src/parsers/term_structure_point_parser.cpp@v0.7.0:L277-L288`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/src/parsers/term_structure_point_parser.cpp#L277-L288>

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

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/flatbuffers/fbs/term_structure.fbs#L317-L328>

## Not documented in engine v0.7.0

- Bootstrap accuracy, iteration limits and extrapolation policy are not documented in engine prose at this tag; from source (`buildCurve` above) the bootstrap tolerance is 1e-15 and everything else is QuantLib's `PiecewiseYieldCurve` default (a bond beyond the last pillar is rejected, see error-codes / the example `fixed_rate_bond_beyond_pillar_request`).
- The pillar dates the engine reports for a bootstrapped curve are the helpers' tenor dates, not necessarily the helper maturity nodes (this server's live finding, CHANGELOG 0.1.1); rebuilding a curve from sampled discount factors at those dates does not reproduce it between nodes.
