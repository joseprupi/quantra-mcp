# Day counters, compounding and yield conventions

_Generated from the engine's documentation and source at `v0.7.0` (`ab4dd9b50fca`) by `scripts/pin_engine.py`. Every statement is an excerpt of that tree with its location `path@tag:Lstart-Lend`; the one-line headings are paraphrases of the excerpt under them. Where the engine documents nothing, the last section says so._

Day counters and compounding are always what the request states: legs, indices, curves, zero points and bond yields each carry their own. Zero rates sampled from a curve default to the curve's day counter with continuous compounding unless the query says otherwise.

## What the engine does

### 1. Day count conventions available.

````text
/// Day count conventions for accrual and discounting.
enum DayCounter : byte {
    Actual360 = 0,
    Actual365Fixed = 1,
    Actual365NoLeap = 2,
    ActualActual = 3,
    ActualActualISMA = 4,
    ActualActualBond = 5,
    ActualActualISDA = 6,
    ActualActualHistorical = 7,
    ActualActual365 = 8,
    ActualActualAFB = 9,
    ActualActualEuro = 10,
    Business252 = 11,
    One = 12,
    Simple = 13,
    Thirty360 = 14,
}
````

Source: `flatbuffers/fbs/enums.fbs@v0.7.0:L3-L20`

### 2. Compounding conventions available.

````text
/// Compounding convention for rates.
enum Compounding : byte {
    Compounded = 0,
    Continuous = 1,
    Simple = 2,
    SimpleThenCompounded = 3
}
````

Source: `flatbuffers/fbs/enums.fbs@v0.7.0:L183-L189`

### 3. The `Yield` convention block (day counter, compounding, frequency) used for bond yield quotation.

````text
/// Yield/compounding convention specification.
table Yield {
    day_counter:enums.DayCounter = null;
    compounding:enums.Compounding = null;
    frequency:enums.Frequency = null;
}
````

Source: `flatbuffers/fbs/common.fbs@v0.7.0:L14-L19`

### 4. Bond yield, durations and convexity are computed with the request's yield day counter / compounding / frequency.

````cpp
            out.yield = trade.bond->yield(trade.yieldDc, trade.yieldComp, trade.yieldFreq);
````

Source: `src/evaluators/fixed_rate_bond_evaluator.cpp@v0.7.0:L89-L89`

### 5. Zero points: each carries its compounding and frequency; all zero points of one curve must share them.

````text
    /// Compounding convention for the zero rate.
    compounding:enums.Compounding = null;
    /// Frequency (used when compounding != Continuous).
    frequency:enums.Frequency = null;
````

Source: `flatbuffers/fbs/term_structure.fbs@v0.7.0:L215-L218`

### 6. Zero / forward sampling defaults: curve day counter, Continuous (zeros) / Simple (forwards), Annual frequency.

````text
table ZeroRateQuery {
    use_curve_day_counter:bool = true;
    day_counter:enums.DayCounter = Actual365Fixed;
    compounding:enums.Compounding = Continuous;
    frequency:enums.Frequency = Annual;
}
````

Source: `flatbuffers/fbs/curve_query.fbs@v0.7.0:L25-L30`

### 7. Omitted conventions are errors, never defaults.

````text
- **Omitted is not defaulted.** A field the product needs but the request does
  not carry is an error naming the field (`Schedule.calendar is required`) —
  never a silent zero, and never a silently chosen convention. This covers
  schedule and leg conventions, day counters, curve-helper quotes, product
  discriminators (`fra_type`, `cap_floor_type`, CDS `side`), and volatility
  specs. See `versioning.md` for the full list introduced in 0.2.0.
````

Source: `docs/http-api.md@v0.7.0:L16-L21`

### 8. OIS helpers and the overnight leg: the fixed-leg / payment day counter fields are deprecated and ignored (the day count comes from the overnight index).

````text
- `fixed_leg_day_counter` on both helpers and `day_counter` on the overnight
  leg are deprecated: no QuantLib overload consumes them (the day count comes
  from the overnight index). They are accepted-but-ignored and documented as
  such.
````

Source: `docs/versioning.md@v0.7.0:L103-L106`

### 9. FRA: the unused day-counter / calendar / convention fields are accepted but ignored.

````text
- The unused `FRA` day-counter/calendar/convention fields are no longer
  required (accepted-but-ignored, deprecated); discrete equity-barrier
  monitoring is rejected with a clear message (no native QuantLib engine).
````

Source: `docs/versioning.md@v0.7.0:L189-L191`

## Request fields that control it

- `day_counter` on legs, indices, curves and vol bases; `compounding` / `frequency` on zero points and `Yield`; `zero` / `fwd` query blocks on curve queries.

````text
table CurveQuerySpec {
    curve_id:string (required);
    measures:[CurveMeasure] (required);
    grid:DateGridSpec (required);
    zero:ZeroRateQuery;
    fwd:ForwardRateQuery;
    options:QueryOptions;
}
````

Source: `flatbuffers/fbs/curve_query.fbs@v0.7.0:L46-L53`

## Not documented in engine v0.7.0

- The engine does not restate QuantLib's day-count formulas; the enum names are QuantLib's classes of the same name.
