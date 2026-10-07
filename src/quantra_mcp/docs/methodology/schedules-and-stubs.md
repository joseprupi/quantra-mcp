# Schedules: date generation rules, stubs, end-of-month, conventions

_Generated from the engine's documentation and source at `v0.7.0` (`ab4dd9b50fca`) by `scripts/pin_engine.py`. Every statement is an excerpt of that tree with its location `path@tag:Lstart-Lend` and the GitHub permalink `https://github.com/joseprupi/quantraserver/blob/v0.7.0/<path>#L..`; the one-line headings are paraphrases of the excerpt under them. Where the engine documents nothing, the last section says so._

Every leg schedule is a QuantLib `Schedule` built from the request's `calendar`, `frequency`, `convention`, `termination_date_convention`, `date_generation_rule` and `end_of_month` (all required) plus the optional stub anchors `first_date` / `next_to_last_date`. The rule decides from which end the regular periods are counted, and therefore where an odd period (a stub) falls.

## What the engine does

### 1. The `Schedule` table: the required fields and the two optional stub anchors (`first_date` controls the FIRST stub, `next_to_last_date` the LAST, symmetrically; omitted = no stub).

````text
/// Date schedule definition for payment and accrual dates.
table Schedule {
    calendar:enums.Calendar = null;
    effective_date:string;
    termination_date:string;
    frequency:enums.Frequency = null;
    convention:enums.BusinessDayConvention = null;
    termination_date_convention:enums.BusinessDayConvention = null;
    date_generation_rule:enums.DateGenerationRule = null;
    /// Presence-required: absent-vs-false silently changes schedule dates for
    /// month-end-anchored (money-market) schedules.
    end_of_month:bool = null;
    /// Optional. ISO-8601 (YYYY-MM-DD). When present, passed as QuantLib's
    /// firstDate, which controls the FIRST stub coupon: with Backward date
    /// generation a first_date after effective_date creates a short/long first
    /// period; with Forward generation it anchors the regular periods. Must lie
    /// strictly between effective_date and termination_date. Omit for no first
    /// stub (bit-identical to prior behaviour).
    first_date:string;
    /// Optional. ISO-8601 (YYYY-MM-DD). When present, passed as QuantLib's
    /// nextToLastDate, controlling the LAST stub coupon symmetrically to
    /// first_date. Must lie strictly between effective_date and
    /// termination_date (and, when first_date is also present, on or after it).
    /// Omit for no last stub (bit-identical to prior behaviour).
    next_to_last_date:string;
}
````

Source: `flatbuffers/fbs/schedule.fbs@v0.7.0:L5-L30`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/flatbuffers/fbs/schedule.fbs#L5-L30>

### 2. Date generation rules available: Backward, CDS, Forward, OldCDS, ThirdWednesday, Twentieth, TwentiethIMM, Zero.

````text
/// Date generation rule for schedule construction.
enum DateGenerationRule : byte {
    Backward = 0,
    CDS = 1,
    Forward = 2,
    OldCDS = 3,
    ThirdWednesday = 4,
    Twentieth = 5,
    TwentiethIMM = 6,
    Zero = 7
}
````

Source: `flatbuffers/fbs/enums.fbs@v0.7.0:L135-L145`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/flatbuffers/fbs/enums.fbs#L135-L145>

### 3. Business-day conventions available (used for `convention`, `termination_date_convention` and the legs' `payment_convention`).

````text
/// Business day convention for date adjustments.
enum BusinessDayConvention : byte {
    Following = 0,
    HalfMonthModifiedFollowing = 1,
    ModifiedFollowing = 2,
    ModifiedPreceding = 3,
    Nearest = 4,
    Preceding = 5,
    Unadjusted = 6,
}
````

Source: `flatbuffers/fbs/enums.fbs@v0.7.0:L107-L116`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/flatbuffers/fbs/enums.fbs#L107-L116>

### 4. The code: every schedule field is presence-required; an omitted one is a 400 naming the field, never a default.

````cpp
    if (!schedule->calendar().has_value())
        QUANTRA_INVALID_ARGUMENT("Schedule.calendar is required");
    if (!schedule->frequency().has_value())
        QUANTRA_INVALID_ARGUMENT("Schedule.frequency is required");
    if (!schedule->convention().has_value())
        QUANTRA_INVALID_ARGUMENT("Schedule.convention is required");
    if (!schedule->termination_date_convention().has_value())
        QUANTRA_INVALID_ARGUMENT("Schedule.termination_date_convention is required");
    if (!schedule->date_generation_rule().has_value())
        QUANTRA_INVALID_ARGUMENT("Schedule.date_generation_rule is required");
    if (!schedule->end_of_month().has_value())
        QUANTRA_INVALID_ARGUMENT("Schedule.end_of_month is required");
````

Source: `src/parsers/schedule_parser.cpp@v0.7.0:L43-L54`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/src/parsers/schedule_parser.cpp#L43-L54>

### 5. The code: the stub anchors are optional and default to QuantLib's own `Date()` (no stub); when present they must lie strictly inside (effective_date, termination_date).

````cpp
    // Optional stub-period control. Absent fields default to QuantLib::Date(),
    // which is exactly the Schedule constructor's own default, i.e. no stub.
    // Present fields are validated to lie
    // strictly inside (effective_date, termination_date) — QuantLib's own
    // requirement — so the caller gets a named 400 instead of an opaque error.
    QuantLib::Date firstDate;
    QuantLib::Date nextToLastDate;
    const bool hasFirst = schedule->first_date() != NULL;
    const bool hasNextToLast = schedule->next_to_last_date() != NULL;
````

Source: `src/parsers/schedule_parser.cpp@v0.7.0:L77-L85`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/src/parsers/schedule_parser.cpp#L77-L85>

### 6. The code: some rules (e.g. `Zero`) reject stub anchors; QuantLib's reason is surfaced as a named 400.

````cpp
    // With a stub date present, some DateGeneration rules (e.g. Zero) reject the
    // firstDate/nextToLastDate arguments outright. QuantLib signals this by
    // throwing; without a guard that raw error maps to an opaque 500. Surface it
    // as a named 400 carrying QuantLib's reason. Requests with no stub dates
    // take the unwrapped path below.
````

Source: `src/parsers/schedule_parser.cpp@v0.7.0:L121-L125`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/src/parsers/schedule_parser.cpp#L121-L125>

### 7. The code: the QuantLib `Schedule` constructor call, argument by argument (effective, termination, period from `frequency`, calendar, convention, termination-date convention, date-generation rule, end-of-month).

````cpp
    return std::make_shared<QuantLib::Schedule>(
        effective,
        termination,
        period,
        CalendarToQL(schedule->calendar().value()),
        ConventionToQL(schedule->convention().value()),
        ConventionToQL(schedule->termination_date_convention().value()),
        DateGenerationToQL(schedule->date_generation_rule().value()),
        schedule->end_of_month().value());
````

Source: `src/parsers/schedule_parser.cpp@v0.7.0:L150-L158`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/src/parsers/schedule_parser.cpp#L150-L158>

### 8. The code: each rule maps one-to-one onto `QuantLib::DateGeneration::Rule`.

````cpp
QuantLib::DateGeneration::Rule DateGenerationToQL(const quantra::enums::DateGenerationRule dateGeneration)
{

    switch (dateGeneration)
    {
    case quantra::enums::DateGenerationRule_Backward:
        return QuantLib::DateGeneration::Backward;
    case quantra::enums::DateGenerationRule_Forward:
        return QuantLib::DateGeneration::Forward;
    case quantra::enums::DateGenerationRule_Zero:
        return QuantLib::DateGeneration::Zero;
    case quantra::enums::DateGenerationRule_ThirdWednesday:
        return QuantLib::DateGeneration::ThirdWednesday;
    case quantra::enums::DateGenerationRule_Twentieth:
        return QuantLib::DateGeneration::Twentieth;
    case quantra::enums::DateGenerationRule_TwentiethIMM:
        return QuantLib::DateGeneration::TwentiethIMM;
    case quantra::enums::DateGenerationRule_OldCDS:
        return QuantLib::DateGeneration::OldCDS;
    case quantra::enums::DateGenerationRule_CDS:
        return QuantLib::DateGeneration::CDS;
    }

    QUANTRA_ERROR("Date Generation not found");
}
````

Source: `src/common/enum_convert.cpp@v0.7.0:L263-L287`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/src/common/enum_convert.cpp#L263-L287>

### 9. 0.7.0 note: stub periods via `first_date` / `next_to_last_date` on every schedule-carrying product.

````text
- **Stub periods**: optional `first_date` / `next_to_last_date` on the shared
  `Schedule`, enabling short/long first and last coupons on every
  schedule-carrying product.
````

Source: `docs/versioning.md@v0.7.0:L237-L239`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/docs/versioning.md#L237-L239>

### 10. Documented usage (engine catalog): a 2-month short first coupon from Backward generation with a `first_date` two months after the effective date; an 18-month long first coupon likewise.

````text
| **EUR 5Y bond with a SHORT first coupon (2-month stub)**<br><sub>The at-par 5Y EUR bond (3.10% annual 30/360) but issued mid-period: Backward date generation with a first_date two months after the effective date creates a short first coupon (2025-01-17 to 2025-03-17). Exercises Schedule.first_date — the first coupon accrues over ~2 months instead of a full year, so it is visibly smaller than the regular annual coupons.</sub> | `short first coupon`, `Schedule.first_date`, `Backward generation`, `annual 30/360` | [frb_eur_5y_short_first_coupon.json](../../examples/data/bonds/frb_eur_5y_short_first_coupon.json) | 999,968.43 |
| **EUR 5Y bond with a LONG first coupon (18-month stub)**<br><sub>The same 5Y EUR bond with a long first coupon: Backward date generation with a first_date 18 months after the effective date (2025-01-17 to 2026-07-17), so the first coupon accrues over ~18 months and is visibly larger than the regular annual coupons. Exercises the long-stub side of Schedule.first_date.</sub> | `long first coupon`, `Schedule.first_date`, `Backward generation`, `annual 30/360` | [frb_eur_5y_long_first_coupon.json](../../examples/data/bonds/frb_eur_5y_long_first_coupon.json) | 999,623.61 |
````

Source: `tests/functional/CATALOG.md@v0.7.0:L77-L78`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/tests/functional/CATALOG.md#L77-L78>

### 11. Documented usage (engine catalog): a short first period on both swap legs with Forward generation anchored by `first_date`.

````text
| **EUR 5Y payer swap with a short first period on both legs**<br><sub>The baseline 5Y EUR payer swap (fixed 30/360 annual vs Euribor 6M) with a short first period on both legs: Forward date generation with a first_date two months after the effective date (2025-01-17 to 2025-03-17) applied identically to the fixed and floating schedules. Exercises Schedule.first_date on a swap where both legs share the same stub anchor.</sub> | `short first period`, `Schedule.first_date`, `both legs stubbed`, `payer swap` | [irs_eur_5y_short_first_both_legs.json](../../examples/data/ir_swaps/irs_eur_5y_short_first_both_legs.json) | -47,079.51 |
````

Source: `tests/functional/CATALOG.md@v0.7.0:L47-L47`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/tests/functional/CATALOG.md#L47-L47>

## Request fields that control it

- `schedule.date_generation_rule`, `schedule.end_of_month` (presence-required), `schedule.first_date`, `schedule.next_to_last_date` on every leg schedule.

````text
    /// Presence-required: absent-vs-false silently changes schedule dates for
    /// month-end-anchored (money-market) schedules.
    end_of_month:bool = null;
    /// Optional. ISO-8601 (YYYY-MM-DD). When present, passed as QuantLib's
    /// firstDate, which controls the FIRST stub coupon: with Backward date
    /// generation a first_date after effective_date creates a short/long first
    /// period; with Forward generation it anchors the regular periods. Must lie
    /// strictly between effective_date and termination_date. Omit for no first
    /// stub (bit-identical to prior behaviour).
    first_date:string;
    /// Optional. ISO-8601 (YYYY-MM-DD). When present, passed as QuantLib's
    /// nextToLastDate, controlling the LAST stub coupon symmetrically to
    /// first_date. Must lie strictly between effective_date and
    /// termination_date (and, when first_date is also present, on or after it).
    /// Omit for no last stub (bit-identical to prior behaviour).
````

Source: `flatbuffers/fbs/schedule.fbs@v0.7.0:L14-L28`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/flatbuffers/fbs/schedule.fbs#L14-L28>

## Not documented in engine v0.7.0

- Where Forward vs Backward places the odd period is not stated in engine prose; it is QuantLib's `Schedule` semantics for the rule the code passes through: `Backward` counts regular periods back from the termination date, so an off-grid tenor leaves the short (or, with `first_date`, long) period at the FRONT (a front stub, the market default for odd-dated swaps); `Forward` counts from the effective date, so the odd period falls at the END (a back stub). For an on-grid tenor (a spot-start 5Y annual / semiannual swap) the two rules generate identical dates. Test it with `reprice_with` on `swaps[0].<leg>.schedule.date_generation_rule`.
- `termination_date_convention` is not described in engine prose; from source it is QuantLib's `terminationDateConvention`: the business-day rule applied to the termination date alone (`convention` adjusts every other date).
- `end_of_month` is documented only as presence-required; the behaviour is QuantLib's: when true and the effective date is the last business day of its month, every generated date is rolled to month end.
- `Zero` (a single period), `ThirdWednesday` (IMM dates), `Twentieth` / `TwentiethIMM` / `CDS` / `OldCDS` (credit roll dates) are QuantLib's rules of the same name; the engine documents no behaviour of its own for them.
- This server's swap presets default `date_generation_rule` to `Backward` on the OIS / vanilla swap trade blocks (market standard, see the preset's `field_provenance`); the engine's own swap fixtures use `Forward`. The leg overrides (`<leg>_overrides.schedule.date_generation_rule`) set it per trade.
