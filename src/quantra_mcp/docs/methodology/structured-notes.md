# Structured notes: Monte Carlo set-up, payoff rules, quanto, seasoning, greeks

_Generated from the engine's documentation and source at `v0.14.0` (`9df97fb2c17a`) by `scripts/pin_engine.py`. Every statement is an excerpt of that tree with its location `path@tag:Lstart-Lend` and the GitHub permalink `https://github.com/joseprupi/quantraserver/blob/v0.14.0/<path>#L..`; the one-line headings are paraphrases of the excerpt under them. Where the engine documents nothing, the last section says so._

`POST /price-structured-note` prices notes on equity-index baskets by Monte Carlo with one declarative payoff (observation events with optional autocall and coupon triggers, a coupon carry rule, a capital-protection block); every product shape is a configuration of those fields. The excerpts below are the engine's own documentation and source at the pin.

## What the engine does

### 1. What the endpoint prices and where its market data comes from.

````text
## Structured notes

`POST /price-structured-note` prices notes on baskets of equity indices by
Monte Carlo: autocallables (classic, snowball, step-down), Phoenix coupons
with or without memory, low-hurdle finals, reverse convertibles, European or
daily-close capital barriers with geared puts, and upside participation.
There is one declarative payoff: a strip of observation events, each with
an optional autocall trigger, an optional coupon trigger and a coupon, a
coupon carry rule and a capital-protection block; every product shape is a
configuration of those fields, never a product of its own. Market data is
the shared `pricing` block: `EquityUnderlyingSpec` (spot, dividend curve,
currency), `VolSurfaceSpec` with a `BlackVolSpec` payload (constant, term or
strike x expiry surface), yield curves, `FxSpotSpec` and the correlation
matrices of "Correlation matrices". Nothing product-specific is in
````

Source: `docs/http-api.md@v0.14.0:L1095-L1108`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/docs/http-api.md#L1095-L1108>

### 2. The `StructuredNote` fields and their rules, as the engine documents them.

````text
`StructuredNote` (one per entry of `notes`, priced on its own; paths are not
shared between notes):

| Field | Meaning |
| --- | --- |
| `trade_id` | Echoed in the response. Required. |
| `currency` | ISO code of the note: coupons, redemption and NPV. Required. |
| `notional` | Coupons and the redemption are fractions of it. Presence-required, `> 0`. |
| `underlyings[]` | The basket, at least one entry, ids distinct. Each: `underlying_id` (an `EquityUnderlyingSpec`, whose `currency` is required here), `volatility_id` (a `VolSurfaceSpec` with a `BlackVolSpec` payload: constant or term vols step exactly between observation dates; a strike x expiry surface makes the simulation Dupire local-vol and requires `monte_carlo.steps_per_year`; `POST /calibrate-vol-surface` returns such a surface from listed option quotes, ready to paste — see "Volatility surface from option quotes"), `initial_level` (the strike fixing every performance `S(t) / initial_level` is measured against; presence-required, `> 0`), `quanto` (present iff the underlying's currency differs from the note's: `discounting_curve` = the rate curve of the underlying's currency, `fx_spot_id` = an `FxSpotSpec` quoting the two currencies in either direction, `fx_volatility_id` = a constant or term `BlackVolSpec`, `fx_underlying_correlation` in `[-1, 1]`, presence-required), `historical_levels[]` (`{date, value}` closes on past dated observation dates; a past observation without one is a `400` naming the date). |
| `performance_rule` | `WorstOf`, `BestOf` or `Average` of the underlyings' performances. Presence-required. |
| `events[]` | Strictly increasing `observation_date`s; the last is the final observation. `autocall_trigger` present: `perf >= trigger` ends the note (notional plus this coupon plus the memory on `payment_date`). `coupon_trigger` present: `perf >= trigger` earns `coupon` plus the memory, paid on `payment_date` or held (`pay_only_on_call`). Both present (Phoenix with a call): the coupon test runs first. Neither: an accrual-only event, `coupon` joins the memory, nothing is observed or paid (`coupon_rule.memory` must be true, `payment_date` absent). `payment_date` is required iff a trigger is present and must be on or after the observation date; `coupon` is required with `coupon_trigger` and on accrual-only events. The final event must carry a trigger, unless it is marked `is_final: true`: a maturity-only observation where only the capital protection block (and `upside`) is tested, with no coupon test and no call test (the memory is not paid there); `coupon`, `coupon_trigger` and `autocall_trigger` must then be absent, `payment_date` is required and must equal `maturity_payment_date`, and the event must be the last one. A final observation that also tests a coupon or a call (an 80 % hurdle, a 100 % call) keeps its trigger; `is_final` is for a final date that only decides the redemption (a trigger note with upside). |
| `coupon_rule` | `memory` (a missed coupon is added to a memory paid with the next coupon; the memory is a sum of amounts) and `pay_only_on_call` (true = Athena / snowball: earned coupons are held until a call or the final event; false = Phoenix: paid on their `payment_date`). Both presence-required. |
| `capital` | `barrier` in `(0, 1]`, `monitoring` (`European`: tested on the final observation; `DailyClose`: breached when the basket performance is below the barrier on any simulated close), `put_strike` (default 1), `participation` (default 1); when breached the redemption is `1 - participation x max(put_strike - perf_final, 0)`. `knocked_in` is presence-required with `DailyClose` when an event is on or before the valuation date, absent otherwise. |
| `upside` | Optional: `participation x max(perf_final - strike, 0)`, capped at `cap` when present, added to the redemption when the note is not called. |
| `maturity_payment_date` | Payment date of the redemption when the note is not called; on or after the final observation. Required. |
| `discounting_curve`, `funding_spread_bp` | The note-currency curve and a flat continuously compounded zero spread added on top for discounting (default 0). |
| `correlation_id` | A `CorrelationMatrixSpec` whose labels are exactly the basket's ids. Required with two or more underlyings; must be absent with one (a 1 x 1 unit matrix is used). |
| `monte_carlo` | `paths` (presence-required, even with `antithetic`), `seed` (default 42; `> 0` with Sobol), `sequence` (`Sobol` default, or `PseudoRandom`), `antithetic` (default true), `brownian_bridge` (default true; must be false with `PseudoRandom`), `steps_per_year` (required with a surface vol or a `DailyClose` barrier, must be absent otherwise). |
````

Source: `docs/http-api.md@v0.14.0:L1165-L1182`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/docs/http-api.md#L1165-L1182>

### 3. Response fields: price, standard error, legs, event probabilities, terms, seasoning and model echoes.

````text
One row per note, in request order.

| Field | Meaning |
| --- | --- |
| `npv`, `npv_pct_of_notional`, `standard_error` | The price in the note currency, as a percentage of the notional, and its Monte Carlo standard error (absent when nothing was simulated). |
| `paths`, `seed`, `status` | Paths simulated (0 for a `Called` / `Matured` note), the seed, `Alive` / `Called` / `Matured`. |
| `legs` | `bond_redemption`, `coupons`, `capital_loss` (`<= 0`), each `{pv, standard_error}`; the three sum to `npv`. `upside` is in `bond_redemption`. |
| `event_probabilities[]` | Per simulated event: `p_call` (present iff `autocall_trigger`), `p_coupon` (present iff a coupon test), both "alive at this event and the test holds". |
| `p_barrier_breach`, `expected_life_years` | Breach probability; `E[t]` of the call (or the final observation), Actual/365 (Fixed) from the valuation date. |
| `terms` | Every booked field as understood, defaults filled, each event flagged `accrual_only` / `final` (the last event) / `is_final` (the request's maturity-only marker) / `resolved_from_history`. Check it before trusting the price. |
| `seasoning` | How the past was resolved: per past event the levels, performances, basket performance, call and coupon outcomes, coupon paid, memory after; `memory_carried` into the simulation; `next_event_index`; for a decided note `known_payment_date` and `known_payment`. |
| `model` | Per underlying `vol_shape`, `local_vol`, `quanto_drift` (rate units, at the first observation); `observation_dates_only`, `time_steps`, `time_grid`, `sequence_dimension`. |
| `greeks` | The bump-and-reprice sensitivities below, present iff `greeks` was in the request. |
| `market`, `decomposition`, `checks`, `distribution`, `timing` | The diagnostic blocks below, each present iff its flag was set and something was simulated. |
````

Source: `docs/http-api.md@v0.14.0:L1210-L1223`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/docs/http-api.md#L1210-L1223>

### 4. Pricing conventions: process per underlying (quanto drift), time grid, random numbers, discounting with the funding spread, seasoning, standard errors.

````text
### Pricing conventions

- Model: per underlying a `GeneralizedBlackScholesProcess(spot, dividend',
  r_note, vol)` where `r_note` is `discounting_curve` without the funding
  spread and `dividend'` is the underlying's dividend curve, or for a quanto
  underlying the `QuantoTermStructure(dividend, riskFree = r_note, foreign =
  quanto.discounting_curve, sigma_S at initial_level, sigma_FX at the FX
  spot, rho)`, so the drift is `r_local - q - rho sigma_S sigma_FX`. `rho` is
  QuantLib's convention: the correlation with the FX rate quoted as
  underlying-currency units per note-currency unit (CHF per USD for a CHF
  index in a USD note); the other quoting direction flips its sign. Positive
  `rho` lowers the drift. The processes are correlated by the
  `StochasticProcessArray` (spectral pseudo-square-root).
- Grid: Actual/365 (Fixed) years from the valuation date to every future
  dated observation (accrual-only events are not observed); with
  `steps_per_year` each interval is split into `ceil(length x
  steps_per_year)` equal log-Euler steps; without it the steps are the
  exact lognormal ones. A `DailyClose` barrier is tested on the simulated
  closes only, with no continuity correction (the barrier is discrete by
  contract); set `steps_per_year` to the trading days per year.
- Random numbers: `Sobol` + `brownian_bridge` = `SobolBrownianBridgeRsg`
  (Diagonal ordering, JoeKuoD7 direction integers); `Sobol` without the
  bridge = `SobolRsg` with JoeKuoD7 through the inverse normal;
  `PseudoRandom` = Mersenne Twister. The dimension is `underlyings x steps`,
  consumed time-major; the antithetic path negates every draw and the pair
  is one sample of the standard error. The Python reference
  (`tests/contract/ql_reference.py`) reproduces the stream, so the gate
  compares the two to the print precision.
- Discounting: every cash flow at its payment date on
  `ZeroSpreadedTermStructure(discounting_curve, funding_spread_bp / 1e4,
  Continuous, Annual, Actual/365 (Fixed))`; the processes drift on the plain
  curve.
- Seasoning: events on or before the valuation date are resolved from
  `historical_levels` through the same walk; a note the past already decided
  is not simulated (`npv` = the known payment discounted when it is still
  ahead, 0 otherwise, `paths = 0`). A past Phoenix coupon paid after the
  valuation date is added to an `Alive` note's NPV with zero standard error.
- Standard errors are the sample standard error of the (antithetic)
  estimator; with Sobol it is an indication, not a confidence bound.
````

Source: `docs/http-api.md@v0.14.0:L1357-L1395`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/docs/http-api.md#L1357-L1395>

### 5. The payoff walk, one event on one path: accrual-only events add to the memory; the call test, the coupon test (its own trigger or the call test), memory and pay-only-on-call handling; at the final event the barrier test, the geared put loss and the upside.

````cpp
EventOutcome stepEvent(
    const ObservationEventData& ev,
    bool isFinal,
    double perf,
    const StructuredNoteTrade& trade,
    WalkState& st) {
    EventOutcome o;
    if (ev.accrualOnly()) {
        st.memory += ev.couponOrZero();
        return o;
    }
    // An `is_final` event (maturity-only observation) has no coupon test and
    // no call test: the memory stays unpaid and only the capital block below
    // runs (the mapper guarantees it is the last event).
    if (!ev.isFinal) {
        const bool callNow = ev.autocallTrigger.has_value() && perf >= *ev.autocallTrigger;
        // Coupon test: its own trigger, or (autocall only) the call test itself.
        const bool earned = ev.couponTrigger ? perf >= *ev.couponTrigger : callNow;
        o.couponEarned = earned;
        if (earned) {
            const double amount = ev.couponOrZero() + st.memory;
            st.memory = 0.0;
            if (callNow || isFinal || !trade.couponRule.payOnlyOnCall) {
                o.couponPaid += amount;
            } else {
                st.memory = amount; // held until the next call or the final event
            }
        } else if (trade.couponRule.memory) {
            st.memory += ev.couponOrZero();
        }
        if (ev.autocallTrigger) {
            o.called = callNow;
            if (callNow) {
                o.couponPaid += st.memory; // everything held
                st.memory = 0.0;
                o.redemptionPaid = 1.0;
                st.called = true;
                return o;
            }
        }
    }
    if (isFinal) {
        const CapitalProtectionData& cap = trade.capital;
        const bool breach = cap.monitoring == CapitalBarrierMonitoring::European
                                ? perf < cap.barrier
                                : st.knockedIn;
        o.breached = breach;
        o.finalRedemption = 1.0;
        if (breach) {
            o.finalLoss = -cap.participation * std::max(cap.putStrike - perf, 0.0);
        }
        if (trade.upside) {
            double u = trade.upside->participation * std::max(perf - trade.upside->strike, 0.0);
            if (trade.upside->cap) u = std::min(u, *trade.upside->cap);
            o.finalUpside = u;
        }
    }
    return o;
}

double basketPerformance(BasketPerformanceRule rule, const std::vector<double>& perfs) {
````

Source: `src/evaluators/structured_note_evaluator.cpp@v0.14.0:L63-L123`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/src/evaluators/structured_note_evaluator.cpp#L63-L123>

### 6. Discounting: a `ZeroSpreadedTermStructure` of the funding spread on the note curve (Continuous, Annual, Actual/365 (Fixed)).

````cpp
/// The funding-spreaded discount curve of the note: ZeroSpreadedTermStructure
/// (curve, spread, Continuous, Annual, Actual/365 (Fixed)), extrapolating.
std::shared_ptr<QuantLib::YieldTermStructure> spreadedCurve(
    const Handle<QuantLib::YieldTermStructure>& curve, double spread) {
    auto ts = std::make_shared<QuantLib::ZeroSpreadedTermStructure>(
        curve, Handle<QuantLib::Quote>(std::make_shared<QuantLib::SimpleQuote>(spread)),
        QuantLib::Continuous, QuantLib::Annual, QuantLib::Actual365Fixed());
    ts->enableExtrapolation();
    return ts;
````

Source: `src/evaluators/structured_note_evaluator.cpp@v0.14.0:L171-L179`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/src/evaluators/structured_note_evaluator.cpp#L171-L179>

### 7. Quanto: the underlying's dividend curve becomes QuantLib's `QuantoTermStructure` (note curve as risk-free, the underlying currency's curve as foreign, sigma_S at the initial level, sigma_FX at the FX spot, rho).

````cpp
    // QuantoTermStructure(underlyingDividendTS, riskFreeTS, foreignRiskFreeTS,
    //                     underlyingBlackVolTS, strike,
    //                     exchRateBlackVolTS, exchRateATMlevel, correlation):
    // zero yield = q + r_rf - r_foreign + rho * sigma_S(strike) * sigma_FX(fxSpot).
    // riskFreeTS = note curve, foreignRiskFreeTS = underlying's curve, so a
    // process on the note curve drifts at r_local - q - rho * sigma_S * sigma_FX.
    auto quanto = std::make_shared<QuantLib::QuantoTermStructure>(
        dividend, noteRiskFree, localRiskFree, equityBlackVol, strike, fxBlackVol, fxSpot, rho);
````

Source: `src/market/quanto_builder.cpp@v0.14.0:L36-L43`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/src/market/quanto_builder.cpp#L36-L43>

### 8. Random sequence generators: Sobol with Brownian bridge (Diagonal, JoeKuoD7), Sobol through the inverse normal, or pseudo-random.

````cpp
        case PathEngineConfig::Sequence::Sobol:
            if (config.brownianBridge) {
                return simulate(
                    processes, grid,
                    SobolBridgeGenerator(
                        factors, steps, QuantLib::SobolBrownianGenerator::Diagonal, config.seed,
                        QuantLib::SobolRsg::JoeKuoD7),
                    config, nAccumulators, functor);
            }
            return simulate(
                processes, grid,
                SobolGenerator(
                    QuantLib::SobolRsg(dimension, config.seed, QuantLib::SobolRsg::JoeKuoD7)),
                config, nAccumulators, functor);
        case PathEngineConfig::Sequence::PseudoRandom:
            return simulate(
                processes, grid,
                QuantLib::PseudoRandom::make_sequence_generator(dimension, config.seed), config,
````

Source: `src/mc/path_engine.cpp@v0.14.0:L259-L276`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/src/mc/path_engine.cpp#L259-L276>

### 9. Antithetic variates: `paths` must be even; a pair is one sample.

````cpp
    const QuantLib::Size samples = config.antithetic ? config.paths / 2 : config.paths;
````

Source: `src/mc/path_engine.cpp@v0.14.0:L108-L108`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/src/mc/path_engine.cpp#L108-L108>

### 10. Seasoning: events on or before the valuation date are resolved from `historical_levels` through the same walk.

````cpp
    // --- seasoning: resolve the past through the same walk ----------------------
    WalkState state;
    state.knockedIn = trade.capital.knockedIn.value_or(false);
````

Source: `src/evaluators/structured_note_evaluator.cpp@v0.14.0:L616-L618`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/src/evaluators/structured_note_evaluator.cpp#L616-L618>

### 11. Greeks: bump-and-reprice on common random numbers (the same seed, paths, grid and sequence as the price), and the number of simulations they cost.

````text
`greeks` in the request (`{}` for the defaults, or any of `spot_bump`,
`vol_bump`, `correlation_bump`, `funding_bump_bp`, `rate_bump_bp`, each
finite and `> 0`) turns on bump-and-reprice sensitivities. Every bumped run
is a full simulation on **common random numbers**: the same seed, paths,
grid and random sequence as the price, so the per-path difference between
the bumped and the base run is what is averaged, and its standard error is
small even where the price's is not. Cost: `1 + 2N + N + 3` simulations for
````

Source: `docs/http-api.md@v0.14.0:L1312-L1318`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/docs/http-api.md#L1312-L1318>

### 12. The deadline that bounds a Greeks request.

````text
price. The JSON gateway's RPC deadline is 10 s and a deployment adds its
own (`QUANTRA_REQUEST_BUDGET_MS`, the proxy's route timeout; the engine
checks the budget before every extra run and returns `504` when it has
````

Source: `docs/http-api.md@v0.14.0:L1322-L1324`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/docs/http-api.md#L1322-L1324>

### 13. Units, standard errors and the definition of every sensitivity.

````text
sensitivity is in the note currency,
scaled to the unit move, and carries `<name>_standard_error`, the Monte
Carlo standard error of the per-sample difference (the path engine's
estimator on the differences, not a difference of two standard errors). A
`greeks` block on a note whose past already decided it (`Called` /
`Matured`) is a `400`: there is nothing to bump. The bump sizes used are
echoed in the block.

| Field | Definition |
| --- | --- |
| `underlyings[].delta` | NPV change per +1 % of the spot: central difference, the spot quote bumped to `spot x (1 +/- spot_bump)`, `(V+ - V-) / (2 spot_bump) x 0.01`. Only the spot moves: the initial level (strike fixing), the dividend curve and the volatility stay (sticky strike for a surface, whose local vol is unchanged in absolute strike). |
| `underlyings[].gamma` | Change of `delta` per +1 % of the spot: `(V+ - 2 V0 + V-) / spot_bump^2 x 0.01^2`. |
| `underlyings[].vega` | NPV change per +1 vol point: the underlying's Black volatility rebuilt with every value shifted by `+vol_bump` (the constant, every node of a term vol, every entry of a strike x expiry surface, from which the Dupire local vol is re-derived), `(V(sigma + vol_bump) - V0) / vol_bump x 0.01`. For a quanto underlying the shifted vol also enters the quanto drift `-rho sigma_S sigma_FX`. |
| `correlation_sensitivity` | NPV change per +0.01 on every off-diagonal correlation: every entry shifted by `+correlation_bump` and clipped to `[-1, 1]`, `(V(rho + bump) - V0) / correlation_bump x 0.01`. Absent for a single underlying. A worst-of structure is long correlation (its coupons and calls become likelier, its put cheaper), so it is normally positive. |
| `correlation_bump_repaired`, `correlation_bumped` | When the bumped matrix is not positive semi-definite (smallest eigenvalue below `-1e-10`) it is repaired before the run: `V diag(max(lambda, 0)) V^T` (`SymmetricSchurDecomposition`), rescaled to unit diagonal; the flag says so and `correlation_bumped` is the matrix actually used (row-major, underlying order, after clipping and any repair). A 2 x 2 matrix never needs the repair; a singular 3 x 3 one bumped uniformly does. Absent / `false` for a single underlying. |
| `funding_dv01` | NPV change per +1 bp of funding spread: `funding_spread_bp + funding_bump_bp` on the discounting only (the processes still drift on the plain curve), `(V(s + bump) - V0) / funding_bump_bp`. Negative for any note that pays. |
| `rho` | NPV change per +1 bp parallel move of `discounting_curve`: a continuously compounded zero spread of `+rate_bump_bp` (`ZeroSpreadedTermStructure`, Actual/365 (Fixed)) on the note curve, used both as the processes' risk-free curve and under the funding spread for discounting, `(V(r + bump) - V0) / rate_bump_bp`. A note-currency underlying's forward rises with it; a quanto underlying's drift `r_local - q - rho sigma_S sigma_FX` does not move (its quanto curve is rebuilt on the shifted note curve, which cancels), so for a quanto basket `rho` is mostly the discounting. |
````

Source: `docs/http-api.md@v0.14.0:L1327-L1343`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/docs/http-api.md#L1327-L1343>

### 14. What the engine rejects (400 naming the field, 404 for an unknown id).

````text
Each is a `400` naming the field (`notes[<i>].<path>: ...`): an empty
`notes`, `underlyings` or `events`; a missing, non-positive or non-finite
`notional` or `initial_level`; a duplicate `underlying_id`; a missing
`performance_rule`, `coupon_rule.memory` / `pay_only_on_call`,
`capital.barrier` (or outside `(0, 1]`) / `monitoring`, `monte_carlo.paths`;
events not strictly increasing; a final event without a trigger and
without `is_final`; an `is_final` event with a `coupon`, `coupon_trigger`
or `autocall_trigger`, without a `payment_date`, with a `payment_date`
other than `maturity_payment_date`, or that is not the last event; an
accrual-only event with a `payment_date`, without a `coupon` or without
`coupon_rule.memory`; a `coupon_trigger` without a `coupon`; a trigger
without a `payment_date`, or one before the observation date; a negative
trigger, coupon, `put_strike` or `participation`; `maturity_payment_date`
before the final observation; a duplicate `historical_levels` date, a level
after the valuation date, or a past observation without a level; a `quanto`
block on a note-currency underlying or none on a foreign one, its
correlation missing or outside `[-1, 1]`, an FX spot of another pair, an FX
vol that is a surface; an `EquityUnderlyingSpec` without `currency`;
`correlation_id` missing with two or more underlyings or present with one,
or whose labels are not exactly the basket; a `DailyClose` barrier or a
surface vol without `steps_per_year`, `steps_per_year` with constant / term
vols and a European barrier, or 0; `knocked_in` missing when required or
present otherwise; odd `paths` with `antithetic`; Sobol `seed` 0;
`brownian_bridge` with `PseudoRandom`; `upside.strike` / `participation`
missing or `cap <= 0`; a surface whose local vol cannot be evaluated along
the paths (names the vol ids and the remedy); `diagnostics.sample_paths`
above 100, without `distribution`, or above `paths`. Missing required
strings and tables (`trade_id`, `currency`, `volatility_id`,
`discounting_curve`, `coupon_rule`, `capital`, `maturity_payment_date`,
`monte_carlo`, ...) are rejected by the JSON parser. A `greeks` bump that
is not finite and `> 0` (`PriceStructuredNoteRequest.greeks.<field>`), or
`greeks` on a note whose past already decided it (`notes[<i>]: no Greeks
for a note that is not alive`), is a `400`. An unknown underlying, vol,
curve, FX spot or correlation id is a `404`.
````

Source: `docs/http-api.md@v0.14.0:L1399-L1432`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/docs/http-api.md#L1399-L1432>

### 15. Known limits of this version.

````text
### Known limits

Composite (non-quanto) foreign underlyings are not supported (the FX rate
is not simulated); the funding spread is flat (use a funding curve as
`discounting_curve` for a term structure); best-of / average baskets have
no weights; issuer-callable notes (Longstaff-Schwartz), lookbacks, coupon
floors, FX-settled notes and scripted payoffs are out of scope; paths are
not shared across the notes of one request; the correlation sensitivity is
one uniform off-diagonal bump (no per-pair Greeks); delta and vega are per
underlying (no cross-gammas, no vol-of-vol). The correlation matrix of the
price is validated (symmetric, unit diagonal, positive semi-definite) and
never repaired; only the bumped matrix of the Greeks is repaired, and the
````

Source: `docs/http-api.md@v0.14.0:L1434-L1445`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/docs/http-api.md#L1434-L1445>

## Request fields that control it

- `events[]`: observation date, payment date, autocall / coupon triggers, coupon.

````text
table ObservationEvent {
    /// Date the basket is observed (closing levels), ISO. Required. For an
    /// accrual-only event (no triggers) nothing is observed: the date is
    /// nominal and only orders the event among the others.
    observation_date:string (required);
    /// Date a coupon or call triggered here is paid, ISO, on or after
    /// `observation_date`. Required (400) whenever `autocall_trigger` or
    /// `coupon_trigger` is present; must be absent for an accrual-only
    /// event.
    payment_date:string;
    /// Autocall trigger as a fraction of the initial level (1.0 = 100%);
    /// perf >= trigger ends the note. Absent = the note cannot be called at
    /// this date.
    autocall_trigger:double = null;
    /// Coupon trigger as a fraction of the initial level; perf >= trigger
    /// earns the coupon. Absent = no coupon test of its own (with an
    /// `autocall_trigger` the coupon is earned by the call test; with
    /// neither trigger the event is accrual-only).
    coupon_trigger:double = null;
    /// Coupon of this period as a fraction of notional (0.07 = 7%).
    /// Required (400) when `coupon_trigger` is present or the event is
    /// accrual-only. With only an `autocall_trigger` it may be absent: the
    /// event then carries no coupon of its own and a call pays the notional
    /// plus the memory only.
    coupon:double = null;
    /// True: this event is the final observation and only the capital
    /// protection block (and `upside`, if present) is tested there: no
    /// coupon test, no call test, unpaid coupons in the memory are not paid.
    /// `coupon`, `coupon_trigger` and `autocall_trigger` must be absent (400
    /// naming the field); `payment_date` is required and must equal
    /// `StructuredNote.maturity_payment_date` (400 otherwise); the event
    /// must be the last one (400), so there is at most one. Absent (or
    /// false): the rules above stand, the last event must carry a trigger
    /// and a final event with a trigger behaves as today.
    is_final:bool = null;
}
````

Source: `flatbuffers/fbs/structured_note.fbs@v0.14.0:L111-L146`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/flatbuffers/fbs/structured_note.fbs#L111-L146>

- `coupon_rule`: memory and pay-only-on-call.

````text
table CouponRule {
    /// True: a coupon not paid at its event (test failed, or held because
    /// `pay_only_on_call`) is added to a memory that is paid in full with
    /// the next coupon that is paid; the memory is a sum of coupon amounts,
    /// so events may carry different coupons. False: a missed coupon is
    /// lost. Presence-required.
    memory:bool = null;
    /// True (Athena / snowball): coupons are paid only when the note ends
    /// (a call, or the final event); an earned coupon at a non-calling
    /// event is held in the memory. False (Phoenix): an earned coupon is
    /// paid on its event's `payment_date`. Presence-required.
    pay_only_on_call:bool = null;
}
````

Source: `flatbuffers/fbs/structured_note.fbs@v0.14.0:L149-L161`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/flatbuffers/fbs/structured_note.fbs#L149-L161>

- `capital`: barrier, monitoring, put strike, participation, knocked_in.

````text
table CapitalProtection {
    /// Barrier as a fraction of the initial level (0.6 = 60%); the capital
    /// is at risk when perf < barrier. Presence-required and in (0, 1].
    barrier:double = null;
    /// When the barrier is tested. Presence-required.
    monitoring:enums.CapitalBarrierMonitoring = null;
    /// Strike of the embedded put as a fraction of the initial level
    /// (1.0 = loss measured from 100%, the "1-for-1" of a factsheet).
    put_strike:double = 1.0;
    /// Gearing of the loss (1.0 = 1-for-1).
    participation:double = 1.0;
    /// Whether a DailyClose barrier has already been breached before the
    /// valuation date. Presence-required (400) when `monitoring` is
    /// DailyClose and any event precedes the valuation date (the engine
    /// cannot infer it from `historical_levels`, which hold observation
    /// dates only); must be absent otherwise.
    knocked_in:bool = null;
}
````

Source: `flatbuffers/fbs/structured_note.fbs@v0.14.0:L169-L186`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/flatbuffers/fbs/structured_note.fbs#L169-L186>

- `underlyings[].quanto`: the quanto block.

````text
table QuantoSpec {
    /// Id of the discount curve of the UNDERLYING's currency in
    /// pricing.rates.curves: the risk-neutral drift of the underlying in its
    /// own currency (r_local in the quanto adjustment). The note-currency
    /// curve is `StructuredNote.discounting_curve`. Required.
    discounting_curve:string (required);
    /// Id of the `FxSpotSpec` in pricing.fx.spots for the underlying's
    /// currency against the note currency, in either direction (the engine
    /// inverts when base/quote are the other way round). Required.
    fx_spot_id:string (required);
    /// Id of the `VolSurfaceSpec` (BlackVolSpec payload, Constant or term)
    /// in pricing.volatility.vol_surfaces holding the volatility of that FX
    /// rate; used only for the quanto drift adjustment. Required.
    fx_volatility_id:string (required);
    /// Correlation between the underlying and the FX rate (underlying
    /// currency per unit of note currency, i.e. the rate that rises when
    /// the note currency strengthens against the underlying's currency), in
    /// [-1, 1]. A scalar input: the FX rate is not simulated and this
    /// correlation is not read from the correlation matrix. Presence-
    /// required (a bare double would silently mean 0 = no adjustment).
    fx_underlying_correlation:double = null;
}
````

Source: `flatbuffers/fbs/structured_note.fbs@v0.14.0:L13-L34`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/flatbuffers/fbs/structured_note.fbs#L13-L34>

- `monte_carlo`: paths, seed, sequence, antithetic, Brownian bridge, steps per year.

````text
table MonteCarloSpec {
    /// Number of simulated paths (antithetic pairs count as two paths).
    /// Presence-required and > 0; a power of two is recommended with Sobol.
    paths:uint = null;
    /// Seed of the random sequence (Sobol scrambling / Mersenne Twister).
    seed:ulong = 42;
    /// Random-number source.
    sequence:enums.RandomSequence = Sobol;
    /// Antithetic variates (every Gaussian draw is also used negated).
    antithetic:bool = true;
    /// Brownian-bridge ordering of the draws (recommended with Sobol).
    brownian_bridge:bool = true;
    /// Time steps per year of the simulation grid between observation
    /// dates. Required (400) when any underlying's volatility is a strike x
    /// expiry surface (local-vol evolution) or the capital barrier is
    /// monitored DailyClose; otherwise must be absent and the grid is the
    /// observation dates only (exact lognormal steps). Between observation
    /// dates the processes evolve with log-Euler steps of 1/steps_per_year
    /// years. A DailyClose barrier is tested on the simulated closes only:
    /// set this to the index's trading days per year (about 252); fewer
    /// steps under-sample the barrier and bias the price. No continuity
    /// correction (Broadie-Glasserman-Kou) is applied, because the barrier
    /// is discrete by contract.
    steps_per_year:uint = null;
}
````

Source: `flatbuffers/fbs/structured_note.fbs@v0.14.0:L205-L229`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/flatbuffers/fbs/structured_note.fbs#L205-L229>

## Not documented in engine v0.14.0

- Nothing further: the excerpts above cover the behaviour the server relies on.
- The engine source at this tag and the QuantLib source it is built on are readable with search_source / read_source (cite `<repo>:<path>@<tag>:L<a>-L<b>`); a point this page does not document is to be read there, never answered from memory.
