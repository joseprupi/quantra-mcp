# Volatility types: Normal, Lognormal, ShiftedLognormal

_Generated from the engine's documentation and source at `v0.7.0` (`ab4dd9b50fca`) by `scripts/pin_engine.py`. Every statement is an excerpt of that tree with its location `path@tag:Lstart-Lend`; the one-line headings are paraphrases of the excerpt under them. Where the engine documents nothing, the last section says so._

Interest-rate vols carry a required `volatility_type`; `displacement` matters only for ShiftedLognormal. Normal vols price through QuantLib's Bachelier formula, (shifted) lognormal through Black with the shift added to strike and forward. Equity / FX vols are always lognormal. Vols are absolute decimals.

## What the engine does

### 1. Volatility type values.

````text
/// Volatility quote type: Normal, Lognormal, or ShiftedLognormal.
enum VolatilityType : byte {
    Normal = 0,
    Lognormal = 1,
    ShiftedLognormal = 2
}
````

Source: `flatbuffers/fbs/enums.fbs@v0.7.0:L252-L257`

### 2. The IR vol base: `volatility_type` is presence-required (an omitted type is a 400, never Normal by default); `displacement` only for ShiftedLognormal.

````text
/// IR volatility base (Normal, Lognormal, ShiftedLognormal). Supports caps/floors and swaptions.
table IrVolBaseSpec {
    reference_date:string;
    calendar:enums.Calendar = null;
    business_day_convention:enums.BusinessDayConvention = null;
    day_counter:enums.DayCounter = null;
    shape:enums.VolSurfaceShape = Constant;
    /// Quotation convention of the supplied vols. Presence-required: an omitted
    /// type is a 400, never the alphabetical-0 default (Normal), which would
    /// price a lognormal-quoted surface as normal vols.
    volatility_type:enums.VolatilityType = null;
    /// Only meaningful for ShiftedLognormal.
    displacement:double = 0.0;
    constant_vol:double;
    /// Optional: resolve constant_vol from pricing.quotes.
    quote_id:string;
}
````

Source: `flatbuffers/fbs/volatility.fbs@v0.7.0:L27-L43`

### 3. Equity / FX vol base: always lognormal, no displacement.

````text
/// Equity/FX volatility base (always lognormal, no displacement).
table BlackVolBaseSpec {
    reference_date:string;
    calendar:enums.Calendar = null;
    business_day_convention:enums.BusinessDayConvention = null;
    day_counter:enums.DayCounter = null;
    shape:enums.VolSurfaceShape = Constant;
    constant_vol:double;
    /// Optional: resolve constant_vol from pricing.quotes.
    quote_id:string;
}
````

Source: `flatbuffers/fbs/volatility.fbs@v0.7.0:L45-L55`

### 4. Analytic swaption greeks: Normal => `BachelierCalculator`; otherwise `BlackCalculator` on (strike + displacement, forward + displacement).

````cpp
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
````

Source: `src/evaluators/swaption_evaluator.cpp@v0.7.0:L635-L650`

### 5. Reporting back: a QuantLib lognormal vol with a non-zero displacement is labelled ShiftedLognormal.

````cpp
quantra::enums::VolatilityType VolatilityTypeToFb(QuantLib::VolatilityType type, double displacement)
{
    if (type == QuantLib::Normal)
    {
        return quantra::enums::VolatilityType_Normal;
    }
    if (displacement != 0.0)
    {
        return quantra::enums::VolatilityType_ShiftedLognormal;
    }
    return quantra::enums::VolatilityType_Lognormal;
}
````

Source: `src/common/enum_convert.cpp@v0.7.0:L552-L563`

### 6. Response: `used_volatility` is the vol actually queried from the surface; `implied_volatility` may be best-effort for non-constant surfaces.

````text
    /// Implied volatility reported by the engine. For non-constant vol setups this may be a best-effort value.
    implied_volatility:double;
````

Source: `flatbuffers/fbs/swaption_response.fbs@v0.7.0:L10-L11`

### 7. Units as the engine's own catalog states them: a flat 80bp normal vol, a 15% shifted-lognormal vol with a 2% displacement.

````text
| **EUR 5Y cap on normal (Bachelier) vols, 80bp**<br><sub>The baseline 5Y quarterly Euribor 3M cap quoted the post-2015 market way: a flat 80bp normal (Bachelier) optionlet vol priced with the Bachelier engine instead of Black. Exercises the Normal volatility type and the model/vol-type pairing end to end.</sub> | `cap`, `Bachelier engine`, `normal vol 80bp`, `Euribor3M`, `vol-type variation` | [cap_eur_5y_bachelier_normal_vol_80bp.json](../../examples/data/cap_floor/cap_eur_5y_bachelier_normal_vol_80bp.json) | 19,603.29 |
| **EUR 5Y cap on shifted-lognormal vols (2% shift)**<br><sub>The baseline 5Y quarterly Euribor 3M cap priced with the shifted Black model: a flat 15% shifted-lognormal optionlet vol with a 2% displacement, the standard quoting convention from the negative-rate era. Exercises the ShiftedLognormal vol type and a non-zero displacement flowing into the Black engine.</sub> | `cap`, `shifted Black`, `displacement 2%`, `shifted-lognormal vol 15%`, `Euribor3M` | [cap_eur_5y_shifted_black_displacement_2pct.json](../../examples/data/cap_floor/cap_eur_5y_shifted_black_displacement_2pct.json) | 18,540.66 |
````

Source: `tests/functional/CATALOG.md@v0.7.0:L105-L106`

## Request fields that control it

- `volatility_type`, `displacement`, `constant_vol` (or a matrix / cube payload) on the vol surface's `base`.

````text
    /// Quotation convention of the supplied vols. Presence-required: an omitted
    /// type is a 400, never the alphabetical-0 default (Normal), which would
    /// price a lognormal-quoted surface as normal vols.
    volatility_type:enums.VolatilityType = null;
    /// Only meaningful for ShiftedLognormal.
    displacement:double = 0.0;
````

Source: `flatbuffers/fbs/volatility.fbs@v0.7.0:L34-L39`

## Not documented in engine v0.7.0

- Vol units are not stated in a schema comment; the catalog's wording (80bp normal = 0.008, 15% lognormal = 0.15 in the example files) is the documented usage: absolute decimals, never percent or bp numbers.
- Normal-to-lognormal conversion is not performed by the engine; a vendor quoting the other type must be re-quoted by the user (or tested with `reprice_with` on `volatility_type` + the matching vol level).
