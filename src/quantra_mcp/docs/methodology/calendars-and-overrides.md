# Calendars, business-day rules and per-request holiday overrides

_Generated from the engine's documentation and source at `v0.7.0` (`ab4dd9b50fca`) by `scripts/pin_engine.py`. Every statement is an excerpt of that tree with its location `path@tag:Lstart-Lend` and the GitHub permalink `https://github.com/joseprupi/quantraserver/blob/v0.7.0/<path>#L..`; the one-line headings are paraphrases of the excerpt under them. Where the engine documents nothing, the last section says so._

Every date adjustment uses the calendar and business-day convention the request states (nothing is defaulted). A request may add or remove holidays for a calendar for its own duration via `calendar_overrides`; nothing is stored.

## What the engine does

### 1. Business-day conventions available.

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

### 2. Date generation rules available.

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

### 3. Schedules: `end_of_month` is presence-required; `first_date` / `next_to_last_date` control the first / last stub.

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

### 4. Where `calendar_overrides` goes and its shape.

````text
## Calendar holiday overrides

A request may carry its own holiday corrections in an optional
`calendar_overrides` field. The overrides apply while that request is processed
and are discarded afterwards: nothing is stored on the server, and every request
that needs an override must carry it. A request without the field behaves
exactly as before.

### Where the field goes

- **Inside `pricing`**, beside `as_of_date`, on every endpoint whose request has
  a `pricing` block (pricing, curve bootstrapping, calibration, sampling).
- **At the top level** of `/calendar-holidays`, `/calendar-advance` and
  `/calendar-business-days`, which have no `pricing` block.

The shape is the same in both places: a list with one entry per calendar.

| Field | Meaning |
| --- | --- |
| `calendar` | The calendar to override. Required. |
| `added_holidays` | Dates (`YYYY-MM-DD`) that must be holidays. Optional. |
| `removed_holidays` | Dates (`YYYY-MM-DD`) that must be business days. Optional. |
````

Source: `docs/http-api.md@v0.7.0:L77-L98`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/docs/http-api.md#L77-L98>

### 5. Semantics: an override applies to every use of that calendar in the request; an already-true override is accepted with no effect.

````text
### Semantics

- `added_holidays` asserts "this date is a holiday"; `removed_holidays` asserts
  "this date is a business day".
- An override applies to **every use of that calendar in the request**: indices,
  curve helpers, schedules, and query grids. It cannot be scoped to one
  instrument or one curve.
- **An override that is already true is accepted and has no effect** — adding a
  date that is already a holiday (or a weekend), or removing a date that is
  already a business day. A client's override list therefore keeps working if a
  later server version already includes that holiday.
- An override for a calendar the request does not use is accepted. So are an
  entry with no dates and an empty list.
````

Source: `docs/http-api.md@v0.7.0:L143-L155`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/docs/http-api.md#L143-L155>

### 6. Rejected override requests (400 naming the field path).

````text
### Rejected requests

Each of these is a `400` whose message names the field path, including indexes
— for example
`pricing.calendar_overrides[0].removed_holidays[0]: 2024-06-15 is a weekend day; a weekend cannot be turned into a business day`.
On the calendar endpoints the path has no `pricing.` prefix.

- An entry without `calendar`.
- A date that does not parse as `YYYY-MM-DD`, or is outside the supported date
  range.
- The same date in both `added_holidays` and `removed_holidays` of one entry.
- A duplicate date inside one list.
- The same calendar in two entries.
- `BespokeCalendar` or `NullCalendar` as the `calendar`: these cannot be
  overridden.
- A weekend date in `removed_holidays`: a weekend cannot be turned into a
  business day.

The whole field is validated before anything is applied, and an invalid field
fails the whole request with `400` — including on endpoints that otherwise
report per-item errors inside a `200` response.
````

Source: `docs/http-api.md@v0.7.0:L157-L177`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/docs/http-api.md#L157-L177>

### 7. Notes: `UnitedStates` == `UnitedStatesSettlement`; an added holiday can move a fixing before as-of (422 unless the fixing is supplied); cached curves are kept per override set.

````text
### Notes

- **`UnitedStates` and `UnitedStatesSettlement` are the same calendar.** An
  override on one applies to both, and listing both in one request is rejected
  as a duplicate. `UnitedStatesNYSE` and the other United States calendars are
  separate.
- **Fixings.** An added holiday can move an index fixing date. If it moves one
  before `as_of_date`, the request fails with the usual missing-fixing error
  (`422`) unless that fixing is supplied.
- **Query grids on `NullCalendar`.** A grid with `business_days_only` set and
  `NullCalendar` as its calendar uses a weekends-only calendar internally. To
  affect it, override `WeekendsOnly`.
- **Caching.** Cached curves and calibrations are kept per override set.
  Requests with different overrides never share a cached result, the order of
  entries and dates does not matter, and requests without overrides are
  unaffected.
````

Source: `docs/http-api.md@v0.7.0:L179-L194`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/docs/http-api.md#L179-L194>

### 8. Implementation: overrides are applied to QuantLib's calendar state for one request and reset afterwards (a worker handles one request at a time).

````cpp
 * Per-request calendar holiday overrides.
 *
 * A request may assert "this date is a holiday" / "this date is a business
 * day" for a calendar. QuantLib keeps added/removed holidays on the calendar's
 * shared implementation, i.e. process-global per calendar, so the overrides are
 * applied for the duration of one request and reset afterwards — the same
 * set/reset pattern as the evaluation date (EvalDateGuard). This is safe because
 * a worker processes one request at a time.
````

Source: `src/common/calendar_overrides.h@v0.7.0:L5-L12`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/src/common/calendar_overrides.h#L5-L12>

## Request fields that control it

- `pricing.calendar_overrides` (or top-level on the calendar endpoints): `[{calendar, added_holidays, removed_holidays}]`.

````text
    /// Per-request holiday overrides (optional). Applied to every use of the
    /// named calendars in this request. Used by: ALL.
    calendar_overrides:[CalendarOverride];
````

Source: `flatbuffers/fbs/pricing.fbs@v0.7.0:L84-L86`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.7.0/flatbuffers/fbs/pricing.fbs#L84-L86>

## Not documented in engine v0.7.0

- Nothing further: the excerpts above cover the behaviour the server relies on.
