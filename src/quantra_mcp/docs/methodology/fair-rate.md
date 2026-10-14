# Fair rate, fair spread, ATM forward

_Generated from the engine's documentation and source at `v0.11.0` (`e2c160dc83e3`) by `scripts/pin_engine.py`. Every statement is an excerpt of that tree with its location `path@tag:Lstart-Lend` and the GitHub permalink `https://github.com/joseprupi/quantraserver/blob/v0.11.0/<path>#L..`; the one-line headings are paraphrases of the excerpt under them. Where the engine documents nothing, the last section says so._

The engine reports QuantLib's `fairRate()` / `fairSpread()` for swaps, the curve-implied forward for FRAs, the par spread for CDS, and the ATM forward swap rate and annuity for swaptions (only when `swaption_pricing_details` is on).

## What the engine does

### 1. Vanilla swap: `fair_rate` = fixed rate - NPV / (fixed leg's signed annuity / 1bp), QuantLib `VanillaSwap`'s own formula; absent when the annuity is zero or the value is not finite.

````cpp
    // VanillaSwap::fairRate: fixedRate - NPV / (fixedLegBPS / basisPoint),
    // the leg BPS carrying the payer sign. A zero annuity has no fair rate.
    const double fixedBps = SignedBps(trade.fixedLeg, fixed);
    if (fixedBps != 0.0) {
        const double fairRate = trade.fixedLeg.level() - out.npv / (fixedBps / kBasisPoint);
        if (std::isfinite(fairRate)) out.fairRate = fairRate;
````

Source: `src/evaluators/vanilla_swap_evaluator.cpp@v0.11.0:L29-L34`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.11.0/src/evaluators/vanilla_swap_evaluator.cpp#L29-L34>

### 2. Vanilla swap: `fair_spread` is the same linear solve on the floating leg's signed annuity (the IBOR forwards or CMS coupons held).

````cpp
    // VanillaSwap::fairSpread: spread - NPV / (floatingLegBPS / basisPoint),
    // the same linear solve on the floating leg's signed annuity (the IBOR
    // forwards or CMS coupons unchanged; a capped / floored CMS leg keeps its
    // strikes, so there the result is the first-order fair spread).
    const double floatingBps = SignedBps(trade.floatingLeg, floating);
    if (floatingBps != 0.0) {
        const double fairSpread = trade.floatingLeg.level() - out.npv / (floatingBps / kBasisPoint);
        if (std::isfinite(fairSpread)) out.fairSpread = fairSpread;
````

Source: `src/evaluators/vanilla_swap_evaluator.cpp@v0.11.0:L36-L43`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.11.0/src/evaluators/vanilla_swap_evaluator.cpp#L36-L43>

### 3. When `fair_rate` and `fair_spread` are present (engine 0.10.0+): `fair_rate` iff exactly one leg carries `FixedCoupons`, `fair_spread` iff a leg carries IBOR, overnight or CMS coupons; both are optional fields.

````text
    /// The fixed rate that zeroes `npv`, the floating leg and every exchange
    /// held fixed. Absent when the fixed leg's annuity is zero (nothing to
    /// solve for) or the value is not finite.
    fair_rate:double = null;
    /// The spread on the floating leg (`IborCoupons` or `CmsCoupons`) that
    /// zeroes `npv`, the fixed leg and every exchange held fixed (the
    /// floating coupons' forwards unchanged). Present on every priced vanilla
    /// swap whose floating leg's annuity is non-zero and the value finite,
    /// as for every swap endpoint: `fair_rate` iff exactly one leg carries
    /// `FixedCoupons`, `fair_spread` iff a leg carries `IborCoupons`,
    /// `OvernightCoupons` or `CmsCoupons`. For a CMS leg with a `cap` or
    /// `floor` the cap / floor strikes are held where the request put them
    /// (the linear solve on the coupon annuity), so repricing a capped or
    /// floored CMS leg at `fair_spread` zeroes `npv` only to first order.
    fair_spread:double = null;
````

Source: `flatbuffers/fbs/vanilla_swap_response.fbs@v0.11.0:L31-L45`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.11.0/flatbuffers/fbs/vanilla_swap_response.fbs#L31-L45>

### 4. OIS swap: `fair_rate` / `fair_spread` by `OvernightIndexedSwap`'s formulas on the fixed / overnight leg's signed annuity.

````cpp
    // OvernightIndexedSwap::fairRate: fixedRate - NPV / (fixedLegBPS /
    // basisPoint), the leg BPS carrying the payer sign.
    const double fixedBps = SignedBps(trade.fixedLeg, fixed);
    if (fixedBps != 0.0) {
        const double fairRate = trade.fixedLeg.level() - out.npv / (fixedBps / kBasisPoint);
        if (std::isfinite(fairRate)) out.fairRate = fairRate;
    }
    // OvernightIndexedSwap::fairSpread: spread - NPV / (overnightLegBPS /
    // basisPoint), the same linear solve on the overnight leg's signed annuity.
    const double overnightBps = SignedBps(trade.overnightLeg, overnight);
    if (overnightBps != 0.0) {
        const double fairSpread =
            trade.overnightLeg.level() - out.npv / (overnightBps / kBasisPoint);
        if (std::isfinite(fairSpread)) out.fairSpread = fairSpread;
````

Source: `src/evaluators/ois_swap_evaluator.cpp@v0.11.0:L29-L42`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.11.0/src/evaluators/ois_swap_evaluator.cpp#L29-L42>

### 5. Swap leg BPS (`fixed_leg_bps`, ...) are no longer response fields from engine 0.10.0; the annuity exists only inside the fair-rate formula above.

````text
`NotionalExchange`). The per-leg fields `fixed_leg_npv`, `floating_leg_npv`,
`overnight_leg_npv`, `leg1_npv`, `leg2_npv`, the `*_bps` fields and the
per-leg `*_flows` lists are removed. One rule on every swap endpoint:
````

Source: `docs/versioning.md@v0.11.0:L129-L131`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.11.0/docs/versioning.md#L129-L131>

### 6. CDS: `fair_spread` is the par spread in decimal, absent when QuantLib cannot express it (e.g. zero running coupon); `fair_upfront` is the upfront for the par spread.

````text
    /// Par spread in decimal. Absent when QuantLib cannot express a fair
    /// spread for the trade (e.g. a zero-running-coupon CDS).
    fair_spread:double = null;
    /// Upfront for par spread.
    fair_upfront:double;
````

Source: `flatbuffers/fbs/cds_response.fbs@v0.11.0:L8-L12`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.11.0/flatbuffers/fbs/cds_response.fbs#L8-L12>

### 7. FRA: `forward_rate` is the implied forward from the forwarding curve.

````text
    /// The implied forward rate from the curve.
    forward_rate:double;
````

Source: `flatbuffers/fbs/fra_response.fbs@v0.11.0:L6-L7`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.11.0/flatbuffers/fbs/fra_response.fbs#L6-L7>

### 8. Swaption: `atm_forward` is the ATM forward swap rate and `annuity` the underlying swap's annuity (PV01-style), both diagnostics.

````text
    /// ATM forward swap rate used for pricing diagnostics. Present only with
    /// swaption_pricing_details.
    atm_forward:double = null;
    /// Underlying swap annuity (PV01-style quantity). Present only with
````

Source: `flatbuffers/fbs/swaption_response.fbs@v0.11.0:L13-L16`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.11.0/flatbuffers/fbs/swaption_response.fbs#L13-L16>

### 9. Those swaption diagnostics are read from QuantLib's additional results only when `swaption_pricing_details` is set.

````cpp
        if (ctx.options.swaptionPricingDetails) {
            {
                const double iv = resultOrDefault(
                    swaption, "impliedVolatility",
````

Source: `src/evaluators/swaption_evaluator.cpp@v0.11.0:L636-L639`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.11.0/src/evaluators/swaption_evaluator.cpp#L636-L639>

## Request fields that control it

- `pricing.options.swaption_pricing_details` turns the swaption analytics on.

````text
    /// Include analytic swaption greeks (delta/vega/gamma/theta/DV01) from the
    /// Black/Bachelier calculator: rate sensitivities per 1bp (gamma per bp^2),
    /// vega per 1bp of vol, theta per calendar day. Also fills atm_forward,
    /// annuity and implied_volatility. See SwaptionResponse field comments.
    swaption_pricing_details:bool = false;
````

Source: `flatbuffers/fbs/pricing.fbs@v0.11.0:L61-L65`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.11.0/flatbuffers/fbs/pricing.fbs#L61-L65>

## Not documented in engine v0.11.0

- A fair rate for bonds is not a concept the engine exposes; bonds report `yield` (see day-counters-and-compounding).
- Which curve projects the fair rate is not stated in prose: from source the swap is built on the index whose curve is `forwarding_curve` and discounted on `discounting_curve`, so the fair rate is the par rate under exactly those two curves.
- The engine source at this tag and the QuantLib source it is built on are readable with search_source / read_source (cite `<repo>:<path>@<tag>:L<a>-L<b>`); a point this page does not document is to be read there, never answered from memory.
