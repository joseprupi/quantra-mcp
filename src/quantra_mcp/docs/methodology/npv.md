# NPV: what the engine's `npv` is

_Generated from the engine's documentation and source at `v0.12.0` (`5b1c016739b1`) by `scripts/pin_engine.py`. Every statement is an excerpt of that tree with its location `path@tag:Lstart-Lend` and the GitHub permalink `https://github.com/joseprupi/quantraserver/blob/v0.12.0/<path>#L..`; the one-line headings are paraphrases of the excerpt under them. Where the engine documents nothing, the last section says so._

Every `npv` the engine returns is QuantLib's `NPV()` of the instrument it built from the request, discounted on the curve the request names. The engine adds no adjustment of its own; the sign is QuantLib's for the side the request states.

## What the engine does

### 1. A fixed-vs-IBOR swap is two `SwapLeg`s, each built on its own and valued on its own discounting curve; the swap's `npv` is the sum of the two signed leg values (`legs[0]` fixed, `legs[1]` floating).

````cpp
    const BuiltSwapLeg fixed = BuildSwapLeg(trade.fixedLeg, "VanillaSwap.fixed_leg", reg, ctx);
    const BuiltSwapLeg floating =
        BuildSwapLeg(trade.floatingLeg, "VanillaSwap.floating_leg", reg, ctx);

    VanillaSwapPerSwap out;
    out.includeFlows = includeFlows;
    out.legs.push_back(SignedLegResult(trade.fixedLeg, fixed, ctx, includeFlows));
    out.legs.push_back(SignedLegResult(trade.floatingLeg, floating, ctx, includeFlows));
    out.npv = out.legs[0].npv + out.legs[1].npv;
````

Source: `src/evaluators/vanilla_swap_evaluator.cpp@v0.12.0:L19-L27`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/src/evaluators/vanilla_swap_evaluator.cpp#L19-L27>

### 2. A leg is valued as QuantLib's `DiscountingSwapEngine` values a leg: `CashFlows::npv` as of the discount curve's reference date, flows on that date excluded; the leg's value is then signed -1 when the party pays it (`Pay`), +1 when it receives it.

````cpp
    // Valued exactly as QuantLib's DiscountingSwapEngine values a leg: as of
    // the discount curve's reference date (= the evaluation date), flows on
    // that date excluded (includeSettlementDateFlows = false).
    const QuantLib::Date valueDate = out.discount->referenceDate();
    out.npvOwn = QuantLib::CashFlows::npv(out.full, *out.discount, false, valueDate, valueDate);
    out.bpsOwn = QuantLib::CashFlows::bps(out.coupons, *out.discount, false, valueDate, valueDate);
    return out;
}

SwapLegPerLeg SignedLegResult(
    const SwapLegData& leg,
    const BuiltSwapLeg& built,
    const PricingContext& ctx,
    bool includeFlows) {
    SwapLegPerLeg out;
    out.npv = leg.sign() * built.npvOwn;
````

Source: `src/evaluators/swap_leg_pricer.cpp@v0.12.0:L250-L265`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/src/evaluators/swap_leg_pricer.cpp#L250-L265>

### 3. An OIS swap is priced the same way: fixed leg and overnight leg built and valued separately, `npv` = sum of the signed leg values.

````cpp
    const BuiltSwapLeg fixed = BuildSwapLeg(trade.fixedLeg, "OisSwap.fixed_leg", reg, ctx);
    const BuiltSwapLeg overnight =
        BuildSwapLeg(trade.overnightLeg, "OisSwap.overnight_leg", reg, ctx);

    OisSwapPerSwap out;
    out.includeFlows = includeFlows;
    out.legs.push_back(SignedLegResult(trade.fixedLeg, fixed, ctx, includeFlows));
    out.legs.push_back(SignedLegResult(trade.overnightLeg, overnight, ctx, includeFlows));
    out.npv = out.legs[0].npv + out.legs[1].npv;
````

Source: `src/evaluators/ois_swap_evaluator.cpp@v0.12.0:L19-L27`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/src/evaluators/ois_swap_evaluator.cpp#L19-L27>

### 4. The swap response: `npv` (sum of the legs, positive in favour of the reported party), optional `fair_rate` / `fair_spread`, and `legs[]` with each leg's signed `npv` and optional flows. No per-leg BPS since engine 0.10.0.

````text
table VanillaSwapResponse {
    /// Total present value in the legs' currency, positive in favour of the
    /// reported party.
    npv:double;
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
    /// One result per leg, in request order (`fixed_leg` then
    /// `floating_leg`): the leg's signed NPV and, with `include_flows`, its
    /// flows.
    legs:[SwapLegResult] (required);
````

Source: `flatbuffers/fbs/vanilla_swap_response.fbs@v0.12.0:L27-L49`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/flatbuffers/fbs/vanilla_swap_response.fbs#L27-L49>

### 5. A swaption's `npv` is "the present value of the swaption under the selected model and market inputs".

````text
    /// Present value of the swaption under the selected model and market inputs.
    npv:double;
````

Source: `flatbuffers/fbs/swaption_response.fbs@v0.12.0:L8-L9`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/flatbuffers/fbs/swaption_response.fbs#L8-L9>

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

Source: `src/evaluators/equity_option_evaluator.cpp@v0.12.0:L242-L252`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/src/evaluators/equity_option_evaluator.cpp#L242-L252>

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

Source: `src/evaluators/fixed_rate_bond_evaluator.cpp@v0.12.0:L85-L92`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/src/evaluators/fixed_rate_bond_evaluator.cpp#L85-L92>

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

Source: `flatbuffers/fbs/cds_response.fbs@v0.12.0:L6-L16`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/flatbuffers/fbs/cds_response.fbs#L6-L16>

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

Source: `flatbuffers/fbs/fra_response.fbs@v0.12.0:L4-L11`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/flatbuffers/fbs/fra_response.fbs#L4-L11>

## Request fields that control it

- `pricing.as_of_date` is the valuation date for everything in the request.

````text
    /// Valuation date (YYYY-MM-DD). Used by: ALL.
    as_of_date:string (required);
````

Source: `flatbuffers/fbs/pricing.fbs@v0.12.0:L79-L80`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/flatbuffers/fbs/pricing.fbs#L79-L80>

- `pricing.settlement_date` applies to bonds only.

````text
    /// Settlement date (YYYY-MM-DD). Used by: FixedRateBond, FloatingRateBond.
    settlement_date:string;
````

Source: `flatbuffers/fbs/pricing.fbs@v0.12.0:L82-L83`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/flatbuffers/fbs/pricing.fbs#L82-L83>

- Swaps (engine 0.10.0+): each leg names its own `discounting_curve` (and, on a floating leg, its `forwarding_curve`) in `pricing.rates.curves` by id.

````text
    /// Id of the curve in `pricing.rates.curves` that discounts every flow of
    /// this leg (coupons and exchanges). For a collateralised cross-currency
    /// trade, the currency's collateral-adjusted curve. A swaption underlying
    /// needs one discount curve: both of its legs must name the same id.
    /// Required.
    discounting_curve:string (required);
````

Source: `flatbuffers/fbs/swap_leg.fbs@v0.12.0:L101-L106`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/flatbuffers/fbs/swap_leg.fbs#L101-L106>

- A swaption takes its curves from the underlying swap's legs (both legs must name the same discounting curve).

````text
/// One swaption to price with its vol and model references. The curves come
/// from the underlying swap's legs (`SwapLeg.discounting_curve`, which both
/// legs must name identically, and the floating / overnight leg's
/// `forwarding_curve`); every reference is by id into `pricing`.
````

Source: `flatbuffers/fbs/price_swaption_request.fbs@v0.12.0:L7-L10`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.12.0/flatbuffers/fbs/price_swaption_request.fbs#L7-L10>

## Not documented in engine v0.12.0

- Sign convention: not documented in engine prose. From source, the value is QuantLib's `NPV()` for the instrument built with the request's `pay_receive` / `side` / option type (a swap response states it: positive in favour of the reported party), so the sign is QuantLib's for that side (a payer swap's NPV rises when rates rise). Test it with `reprice_with` flipping the side.
- Premium vs forward premium: the engine returns one present value as of `as_of_date`; it does not document a forward (deferred) premium field.
- The engine source at this tag and the QuantLib source it is built on are readable with search_source / read_source (cite `<repo>:<path>@<tag>:L<a>-L<b>`); a point this page does not document is to be read there, never answered from memory.
