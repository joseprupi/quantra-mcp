# Fair rate, fair spread, ATM forward

_Generated from the engine's documentation and source at `v0.7.0` (`ab4dd9b50fca`) by `scripts/pin_engine.py`. Every statement is an excerpt of that tree with its location `path@tag:Lstart-Lend`; the one-line headings are paraphrases of the excerpt under them. Where the engine documents nothing, the last section says so._

The engine reports QuantLib's `fairRate()` / `fairSpread()` for swaps, the curve-implied forward for FRAs, the par spread for CDS, and the ATM forward swap rate and annuity for swaptions (only when `swaption_pricing_details` is on).

## What the engine does

### 1. Vanilla swap: `fair_rate` and `fair_spread` are `VanillaSwap::fairRate()` / `fairSpread()`.

````cpp
    out.fairRate = swap->fairRate();
    out.fairSpread = swap->fairSpread();
````

Source: `src/evaluators/vanilla_swap_evaluator.cpp@v0.7.0:L239-L240`

### 2. With a notionals vector the engine reproduces the same formula explicitly: fair rate = fixed rate - NPV / (fixed-leg BPS / 1bp); fair spread likewise on the floating leg.

````cpp
    // fairRate/fairSpread via VanillaSwap's own formula (basisPoint = 1e-4):
    //   fairRate   = fixedRate - NPV / (fixedLegBPS / basisPoint)
    //   fairSpread = spread    - NPV / (floatLegBPS / basisPoint)
    // The leg BPS values already carry the payer sign, so this matches
    // VanillaSwap exactly for an all-equal notionals vector.
    constexpr double basisPoint = 1.0e-4;
    out.fairRate = (out.fixedLegBps != 0.0)
        ? trade.fixed.rate - out.npv / (out.fixedLegBps / basisPoint)
        : 0.0;
    out.fairSpread = (out.floatingLegBps != 0.0)
        ? trade.ibor.spread - out.npv / (out.floatingLegBps / basisPoint)
````

Source: `src/evaluators/vanilla_swap_evaluator.cpp@v0.7.0:L330-L340`

### 3. A swap with a CMS leg has no fair rate / spread; the engine reports 0.0 there by design.

````cpp
    // fair_rate/fair_spread are not defined for a CMS swap; the legacy
    // pricing service emitted 0.0 here, so keep parity.
    out.fairRate = 0.0;
    out.fairSpread = 0.0;
````

Source: `src/evaluators/vanilla_swap_evaluator.cpp@v0.7.0:L452-L455`

### 4. OIS swap: `fair_rate` / `fair_spread` are `OvernightIndexedSwap::fairRate()` / `fairSpread()`.

````cpp
    out.fairRate = swap->fairRate();
    out.fairSpread = swap->fairSpread();
````

Source: `src/evaluators/ois_swap_evaluator.cpp@v0.7.0:L124-L125`

### 5. CDS: `fair_spread` is the par spread in decimal, absent when QuantLib cannot express it (e.g. zero running coupon); `fair_upfront` is the upfront for the par spread.

````text
    /// Par spread in decimal. Absent when QuantLib cannot express a fair
    /// spread for the trade (e.g. a zero-running-coupon CDS).
    fair_spread:double = null;
    /// Upfront for par spread.
    fair_upfront:double;
````

Source: `flatbuffers/fbs/cds_response.fbs@v0.7.0:L8-L12`

### 6. FRA: `forward_rate` is the implied forward from the forwarding curve.

````text
    /// The implied forward rate from the curve.
    forward_rate:double;
````

Source: `flatbuffers/fbs/fra_response.fbs@v0.7.0:L6-L7`

### 7. Swaption: `atm_forward` is the ATM forward swap rate and `annuity` the underlying swap's annuity (PV01-style), both diagnostics.

````text
    /// ATM forward swap rate used for pricing diagnostics.
    atm_forward:double;
    /// Underlying swap annuity (PV01-style quantity).
    annuity:double;
````

Source: `flatbuffers/fbs/swaption_response.fbs@v0.7.0:L12-L15`

### 8. Those swaption diagnostics are read from QuantLib's additional results only when `swaption_pricing_details` is set.

````cpp
        if (ctx.options.swaptionPricingDetails) {
            row.impliedVolatility = resultOrDefault(swaption, "impliedVolatility", row.impliedVolatility);
            row.atmForward = resultOrDefault(swaption, "atmForward", 0.0);
            row.annuity = resultOrDefault(swaption, "annuity", 0.0);
````

Source: `src/evaluators/swaption_evaluator.cpp@v0.7.0:L621-L624`

## Request fields that control it

- `pricing.options.swaption_pricing_details` turns the swaption analytics on.

````text
    /// Include detailed swaption analytics (delta/vega/gamma/theta/DV01).
    swaption_pricing_details:bool = false;
````

Source: `flatbuffers/fbs/pricing.fbs@v0.7.0:L60-L61`

## Not documented in engine v0.7.0

- A fair rate for bonds is not a concept the engine exposes; bonds report `yield` (see day-counters-and-compounding).
- Which curve projects the fair rate is not stated in prose: from source the swap is built on the index whose curve is `forwarding_curve` and discounted on `discounting_curve`, so the fair rate is the par rate under exactly those two curves.
