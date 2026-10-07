# NPV: what the engine's `npv` is

_Generated from the engine's documentation and source at `v0.7.0` (`ab4dd9b50fca`) by `scripts/pin_engine.py`. Every statement is an excerpt of that tree with its location `path@tag:Lstart-Lend`; the one-line headings are paraphrases of the excerpt under them. Where the engine documents nothing, the last section says so._

Every `npv` the engine returns is QuantLib's `NPV()` of the instrument it built from the request, discounted on the curve the request names. The engine adds no adjustment of its own; the sign is QuantLib's for the side the request states.

## What the engine does

### 1. A fixed-vs-IBOR swap is built as a QuantLib `VanillaSwap` for the stated `swap_type` and priced with a `DiscountingSwapEngine` on the discounting curve; `npv`, the fair rate / spread and the leg NPVs and BPS are read off that object.

````cpp
    auto swap = std::make_shared<QuantLib::VanillaSwap>(
        trade.swapType,
        trade.fixed.notional,
        trade.fixed.schedule,
        trade.fixed.rate,
        trade.fixed.dayCounter,
        trade.ibor.schedule,
        iborIndex,
        trade.ibor.spread,
        trade.ibor.dayCounter);
    swap->setPricingEngine(
        std::make_shared<QuantLib::DiscountingSwapEngine>(discHandle));

    VanillaSwapPerSwap out;
    out.npv = swap->NPV();
    out.fairRate = swap->fairRate();
    out.fairSpread = swap->fairSpread();
    out.fixedLegNpv = swap->fixedLegNPV();
    out.floatingLegNpv = swap->floatingLegNPV();
    out.fixedLegBps = swap->fixedLegBPS();
    out.floatingLegBps = swap->floatingLegBPS();
    out.hasCmsLeg = false;
````

Source: `src/evaluators/vanilla_swap_evaluator.cpp@v0.7.0:L224-L245`

### 2. If the fixed and floating notionals differ, the fixed leg's notional is used (a warning, not an error).

````cpp
    if (trade.fixed.notional != trade.ibor.notional) {
        // Mismatched notionals are not an error: the fixed leg's notional wins.
        std::cout << "Warning: Fixed and floating notionals differ. Using fixed notional."
                  << std::endl;
    }
````

Source: `src/evaluators/vanilla_swap_evaluator.cpp@v0.7.0:L218-L222`

### 3. An OIS swap is priced the same way (`DiscountingSwapEngine` on the discounting curve); `npv`, fair rate / spread, leg BPS and leg NPVs are QuantLib's.

````cpp
        std::make_shared<QuantLib::DiscountingSwapEngine>(discHandle));

    OisSwapPerSwap out;
    out.npv = swap->NPV();
    out.fairRate = swap->fairRate();
    out.fairSpread = swap->fairSpread();
    out.fixedLegBps = swap->fixedLegBPS();
    out.overnightLegBps = swap->overnightLegBPS();
    out.fixedLegNpv = swap->fixedLegNPV();
    out.overnightLegNpv = swap->overnightLegNPV();
````

Source: `src/evaluators/ois_swap_evaluator.cpp@v0.7.0:L120-L129`

### 4. The swap response fields: `npv`, `fair_rate`, `fair_spread`, per-leg `bps` and `npv`, optional flows.

````text
table VanillaSwapResponse {
    npv:double;
    fair_rate:double;
    fair_spread:double;
    fixed_leg_bps:double;
    floating_leg_bps:double;
    fixed_leg_npv:double;
    floating_leg_npv:double;
    /// Optional detailed flows per leg.
    fixed_leg_flows:[SwapLegFlow];
    floating_leg_flows:[SwapLegFlow];
````

Source: `flatbuffers/fbs/vanilla_swap_response.fbs@v0.7.0:L37-L47`

### 5. A swaption's `npv` is "the present value of the swaption under the selected model and market inputs".

````text
    /// Present value of the swaption under the selected model and market inputs.
    npv:double;
````

Source: `flatbuffers/fbs/swaption_response.fbs@v0.7.0:L8-L9`

### 6. An equity option's `npv` is the instrument's `NPV()` multiplied by the trade `quantity`; the greeks are scaled the same way.

````cpp
    const double npv = instrument->NPV();
    const double quantity = trade.quantity;

    EquityOptionPerTrade out;
    out.tradeId = trade.tradeId;
    out.npv = npv * quantity;
    out.delta = safeGreek([&]() { return oneAssetOption->delta() * quantity; });
    out.gamma = safeGreek([&]() { return oneAssetOption->gamma() * quantity; });
    out.vega = safeGreek([&]() { return oneAssetOption->vega() * quantity; });
    out.theta = safeGreek([&]() { return oneAssetOption->theta() * quantity; });
    out.rho = safeGreek([&]() { return oneAssetOption->rho() * quantity; });
````

Source: `src/evaluators/equity_option_evaluator.cpp@v0.7.0:L248-L258`

### 7. A fixed-rate bond's `npv` is the bond's `NPV()`; clean / dirty price, accrued and yield are only computed when `bond_pricing_details` is set.

````cpp
        out.npv = trade.bond->NPV();
        out.hasDetails = reg.options.bondPricingDetails;

        if (out.hasDetails) {
            out.yield = trade.bond->yield(trade.yieldDc, trade.yieldComp, trade.yieldFreq);
            out.cleanPrice = trade.bond->cleanPrice();
            out.dirtyPrice = trade.bond->dirtyPrice();
            out.accruedAmount = trade.bond->accruedAmount();
````

Source: `src/evaluators/fixed_rate_bond_evaluator.cpp@v0.7.0:L85-L92`

### 8. A CDS response carries `npv`, the protection (`default_leg_npv`) and premium leg NPVs; `fair_spread` is absent when QuantLib cannot express one.

````text
table CDSValues {
    npv:double;
    /// Par spread in decimal. Absent when QuantLib cannot express a fair
    /// spread for the trade (e.g. a zero-running-coupon CDS).
    fair_spread:double = null;
    /// Upfront for par spread.
    fair_upfront:double;
    /// NPV of protection leg.
    default_leg_npv:double;
    /// NPV of premium leg.
    premium_leg_npv:double;
````

Source: `flatbuffers/fbs/cds_response.fbs@v0.7.0:L6-L16`

### 9. A FRA response: `npv`, the curve-implied `forward_rate`, the value at settlement and the settlement date.

````text
table FRAResponse {
    npv:double;
    /// The implied forward rate from the curve.
    forward_rate:double;
    /// Value at settlement.
    spot_value:double;
    /// When the FRA settles.
    settlement_date:string;
````

Source: `flatbuffers/fbs/fra_response.fbs@v0.7.0:L4-L11`

## Request fields that control it

- `pricing.as_of_date` is the valuation date for everything in the request.

````text
    /// Valuation date (YYYY-MM-DD). Used by: ALL.
    as_of_date:string (required);
````

Source: `flatbuffers/fbs/pricing.fbs@v0.7.0:L68-L69`

- `pricing.settlement_date` applies to bonds only.

````text
    /// Settlement date (YYYY-MM-DD). Used by: FixedRateBond, FloatingRateBond.
    settlement_date:string;
````

Source: `flatbuffers/fbs/pricing.fbs@v0.7.0:L71-L72`

- `discounting_curve` / `forwarding_curve` name curves in `pricing.rates.curves` by id (shown for swaptions; swaps and FRAs use the same two references).

````text
    /// Reference to curve in pricing.rates.curves by id.
    discounting_curve:string;
    /// Reference to curve in pricing.rates.curves for forward rates by id.
    forwarding_curve:string;
````

Source: `flatbuffers/fbs/price_swaption_request.fbs@v0.7.0:L10-L13`

## Not documented in engine v0.7.0

- Sign convention: not documented in engine prose. From source, the value is QuantLib's `NPV()` for the instrument built with the request's `swap_type` / `side` / option type, so the sign is QuantLib's for that side (a payer swap's NPV rises when rates rise). Test it with `reprice_with` flipping the side.
- Premium vs forward premium: the engine returns one present value as of `as_of_date`; it does not document a forward (deferred) premium field.
