# Swaption settlement type and method

_Generated from the engine's documentation and source at `v0.14.0` (`9df97fb2c17a`) by `scripts/pin_engine.py`. Every statement is an excerpt of that tree with its location `path@tag:Lstart-Lend` and the GitHub permalink `https://github.com/joseprupi/quantraserver/blob/v0.14.0/<path>#L..`; the one-line headings are paraphrases of the excerpt under them. Where the engine documents nothing, the last section says so._

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

    /// Underlying swap instrument (see `SwaptionUnderlying`). Required; in
    /// JSON `"underlying_type": "VanillaSwap"` precedes `"underlying"`.
    underlying:SwaptionUnderlying;
}
````

Source: `flatbuffers/fbs/swaption.fbs@v0.14.0:L20-L38`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/flatbuffers/fbs/swaption.fbs#L20-L38>

### 2. Settlement type values.

````text
/// Swaption settlement: physical (deliver swap) or cash.
enum SettlementType : byte {
    Physical = 0,
    Cash = 1
}
````

Source: `flatbuffers/fbs/enums.fbs@v0.14.0:L250-L254`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/flatbuffers/fbs/enums.fbs#L250-L254>

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

Source: `flatbuffers/fbs/enums.fbs@v0.14.0:L256-L262`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/flatbuffers/fbs/enums.fbs#L256-L262>

### 4. Mapping to QuantLib: each method maps one-to-one onto `QuantLib::Settlement::Method`.

````cpp
QuantLib::Settlement::Method SettlementMethodToQL(const quantra::enums::SettlementMethod method) {
    switch (method) {
        case quantra::enums::SettlementMethod_PhysicalOTC: return QuantLib::Settlement::PhysicalOTC;
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

Source: `src/common/enum_convert.cpp@v0.14.0:L217-L229`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/src/common/enum_convert.cpp#L217-L229>

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

Source: `src/evaluators/swaption_evaluator.cpp@v0.14.0:L156-L170`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/src/evaluators/swaption_evaluator.cpp#L156-L170>

### 6. Bonds: `pricing.settlement_date` is the bond settlement date.

````text
    /// Settlement date (YYYY-MM-DD). Used by: FixedRateBond, FloatingRateBond.
    settlement_date:string;
````

Source: `flatbuffers/fbs/pricing.fbs@v0.14.0:L82-L83`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/flatbuffers/fbs/pricing.fbs#L82-L83>

## Request fields that control it

- `swaption.settlement_type`, `swaption.settlement_method`.

````text
    /// Settlement type for swaption payoff handling. Presence-required: an
    /// omitted type is a 400, never the alphabetical-0 default (Physical).
    settlement_type:enums.SettlementType = null;
    /// Settlement method (physical/other) per market convention.
    settlement_method:enums.SettlementMethod = PhysicalOTC;
````

Source: `flatbuffers/fbs/swaption.fbs@v0.14.0:L25-L29`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/flatbuffers/fbs/swaption.fbs#L25-L29>

## Not documented in engine v0.14.0

- The engine does not document what each cash-settlement method computes; it is QuantLib's semantics (CollateralizedCashPrice discounts the annuity on the discount curve; ParYieldCurve uses the par-yield annuity). Test the effect directly with `reprice_with` on `swaptions[0].swaption.settlement_method`.
- `settlement_method` is the one swaption convention with a wire default; a cash-settled trade must state its method or it is priced as PhysicalOTC.
- The engine source at this tag and the QuantLib source it is built on are readable with search_source / read_source (cite `<repo>:<path>@<tag>:L<a>-L<b>`); a point this page does not document is to be read there, never answered from memory.
