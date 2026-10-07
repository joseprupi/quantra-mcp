# Swaption settlement type and method

_Generated from the engine's documentation and source at `v0.7.0` (`ab4dd9b50fca`) by `scripts/pin_engine.py`. Every statement is an excerpt of that tree with its location `path@tag:Lstart-Lend` and the GitHub permalink `https://github.com/joseprupi/quantraserver/blob/v0.7.0/<path>#L..`; the one-line headings are paraphrases of the excerpt under them. Where the engine documents nothing, the last section says so._

`settlement_type` (Physical | Cash) is required. `settlement_method` (PhysicalOTC | PhysicalCleared | CollateralizedCashPrice | ParYieldCurve) has a wire default of PhysicalOTC and is passed straight to QuantLib's `Settlement::Method`; the engine documents no formula of its own.

## What the engine does

### 1. The swaption table: exercise and settlement types are presence-required; the settlement method defaults to PhysicalOTC.

````text
/// Swaption instrument definition.
table Swaption {
    /// Exercise style: European, Bermudan, or American. Presence-required: an
    /// omitted style is a 400, never the alphabetical-0 default (European).
    exercise_type:enums.ExerciseType = null;
    /// Settlement type for swaption payoff handling. Presence-required: an
    /// omitted type is a 400, never the alphabetical-0 default (Physical).
    settlement_type:enums.SettlementType = null;
    /// Settlement method (physical/other) per market convention.
    settlement_method:enums.SettlementMethod = PhysicalOTC;
    /// Single exercise date (ISO date string). Required for European and American.
    exercise_date:string;
    /// Bermudan exercise date set (ISO date strings). Required for Bermudan; must contain at least 2 dates.
    exercise_dates:[string];

    /// Underlying swap instrument.
    underlying:SwaptionUnderlying;
}
````

Source: `flatbuffers/fbs/swaption.fbs@v0.7.0:L10-L27`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/flatbuffers/fbs/swaption.fbs#L10-L27>

### 2. Settlement type values.

````text
/// Swaption settlement: physical (deliver swap) or cash.
enum SettlementType : byte {
    Physical = 0,
    Cash = 1
}
````

Source: `flatbuffers/fbs/enums.fbs@v0.7.0:L232-L236`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/flatbuffers/fbs/enums.fbs#L232-L236>

### 3. Settlement method values.

````text
/// Swaption settlement method when settlement_type is Cash.
enum SettlementMethod : byte {
    PhysicalOTC = 0,
    PhysicalCleared = 1,
    CollateralizedCashPrice = 2,
    ParYieldCurve = 3
}
````

Source: `flatbuffers/fbs/enums.fbs@v0.7.0:L238-L244`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/flatbuffers/fbs/enums.fbs#L238-L244>

### 4. Mapping to QuantLib: each method maps one-to-one onto `QuantLib::Settlement::Method`.

````cpp
QuantLib::Settlement::Method SettlementMethodToQL(const quantra::enums::SettlementMethod method)
{
    switch (method)
    {
    case quantra::enums::SettlementMethod_PhysicalOTC:
        return QuantLib::Settlement::PhysicalOTC;
    case quantra::enums::SettlementMethod_PhysicalCleared:
        return QuantLib::Settlement::PhysicalCleared;
    case quantra::enums::SettlementMethod_CollateralizedCashPrice:
        return QuantLib::Settlement::CollateralizedCashPrice;
    case quantra::enums::SettlementMethod_ParYieldCurve:
        return QuantLib::Settlement::ParYieldCurve;
    }

    QUANTRA_ERROR("Settlement method not found");
}
````

Source: `src/common/enum_convert.cpp@v0.7.0:L320-L335`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/src/common/enum_convert.cpp#L320-L335>

### 5. An unknown settlement type fails closed (never silently Physical).

````cpp
    QuantLib::Settlement::Type settlementType;
    switch (inst.settlementType) {
        case quantra::enums::SettlementType_Physical:
            settlementType = QuantLib::Settlement::Physical;
            break;
        case quantra::enums::SettlementType_Cash:
            settlementType = QuantLib::Settlement::Cash;
            break;
        default:
            // Fail closed: an unknown settlement type must not
            // silently price as Physical (mirrors the exercise-type switch).
            QUANTRA_INVALID_ARGUMENT(
                "Swaption.settlement_type is not a known settlement type: " +
                std::to_string(static_cast<int>(inst.settlementType)));
    }
````

Source: `src/evaluators/swaption_evaluator.cpp@v0.7.0:L164-L178`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/src/evaluators/swaption_evaluator.cpp#L164-L178>

### 6. Bonds: `pricing.settlement_date` is the bond settlement date.

````text
    /// Settlement date (YYYY-MM-DD). Used by: FixedRateBond, FloatingRateBond.
    settlement_date:string;
````

Source: `flatbuffers/fbs/pricing.fbs@v0.7.0:L71-L72`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/flatbuffers/fbs/pricing.fbs#L71-L72>

## Request fields that control it

- `swaption.settlement_type`, `swaption.settlement_method`.

````text
    /// Settlement type for swaption payoff handling. Presence-required: an
    /// omitted type is a 400, never the alphabetical-0 default (Physical).
    settlement_type:enums.SettlementType = null;
    /// Settlement method (physical/other) per market convention.
    settlement_method:enums.SettlementMethod = PhysicalOTC;
````

Source: `flatbuffers/fbs/swaption.fbs@v0.7.0:L15-L19`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/flatbuffers/fbs/swaption.fbs#L15-L19>

## Not documented in engine v0.7.0

- The engine does not document what each cash-settlement method computes; it is QuantLib's semantics (CollateralizedCashPrice discounts the annuity on the discount curve; ParYieldCurve uses the par-yield annuity). Test the effect directly with `reprice_with` on `swaptions[0].swaption.settlement_method`.
- `settlement_method` is the one swaption convention with a wire default; a cash-settled trade must state its method or it is priced as PhysicalOTC.
