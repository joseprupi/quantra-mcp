# Option quotes to implied forwards, dividends and a Black vol surface

_Generated from the engine's documentation and source at `v0.14.0` (`9df97fb2c17a`) by `scripts/pin_engine.py`. Every statement is an excerpt of that tree with its location `path@tag:Lstart-Lend` and the GitHub permalink `https://github.com/joseprupi/quantraserver/blob/v0.14.0/<path>#L..`; the one-line headings are paraphrases of the excerpt under them. Where the engine documents nothing, the last section says so._

`POST /calibrate-implied-forwards` fits put-call parity per expiry of a listed European option quote sheet (implied forward, implied discount factor, funding spread, dividend yield, a ready dividend curve); `POST /calibrate-vol-surface` inverts the out-of-the-money mids to Black vols and fits AndreasenHuge or one SVI slice per expiry, checks butterfly and calendar arbitrage and reprices every used quote on the returned surface. The excerpts below are the engine's own documentation and source at the pin.

## What the engine does

### 1. What the implied-forwards endpoint returns and where it sits in the chain quotes -> forwards -> surface -> models.

````text
## Implied forwards and dividends from option quotes

`POST /calibrate-implied-forwards` reads a sheet of listed European option
quotes for one underlying on the as-of date and returns, per expiry, the
forward the market implies, the discount factor it implies (the index's
funding), the continuous funding spread against the request's discounting
curve, the continuous dividend yield that reproduces the forward, and a fit
report; plus a ready-to-paste dividend curve (`TermStructure`,
`InterpolatedZero`) under the request's `dividend_curve_id`. It is the first
step of the market-data chain for equity pricing (forwards → vol surface →
models): nothing is priced, and the response says what was dropped and why.
````

Source: `docs/http-api.md@v0.14.0:L1448-L1458`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/docs/http-api.md#L1448-L1458>

### 2. The quote filters, in the order applied, each one a dropped line with that reason; the limits on the filter values.

````text
Filter rules, applied per line in this order, each one a dropped line with
that reason: `strike missing or not > 0`; `missing call or put side`;
`non-finite price`; `negative bid or non-positive ask`; `crossed quote
(bid > ask)`; `open interest below min_open_interest` (only when the
threshold is given; a line without open-interest fields is then dropped);
`relative spread above max_relative_spread` ((ask − bid) / mid on each side);
`duplicate strike` (the first line wins). `max_relative_spread` and
`moneyness_window` must be > 0 and `min_quotes_per_expiry` ≥ 2 (`400`
naming the field).
````

Source: `docs/http-api.md@v0.14.0:L1494-L1502`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/docs/http-api.md#L1494-L1502>

### 3. The put-call-parity fit: weighted least squares of C - P on the strike, two passes (the second inside the moneyness window), the weights, the residuals and the curve-relative quantities r(T), q(T) and the funding spread.

````cpp
 * Put-call parity for European options on the same strike K and expiry T:
 *
 *     C(K) - P(K) = DF * (F - K) = (DF * F) + (-DF) * K
 *
 * i.e. the mid call-minus-put is affine in K with intercept a = DF * F and
 * slope b = -DF. With weights w_i the weighted least-squares line through the
 * points (K_i, y_i = C_i - P_i) is
 *
 *     W    = sum w_i
 *     Kbar = sum w_i K_i / W,   ybar = sum w_i y_i / W
 *     b    = sum w_i (K_i - Kbar) (y_i - ybar) / sum w_i (K_i - Kbar)^2
 *     a    = ybar - b * Kbar
 *
 * and the market-implied quantities are
 *
 *     DF_implied = -b                 (requires b < 0)
 *     F          = a / DF_implied = -a / b   (the strike where C - P = 0)
 *
 * The fit runs twice per expiry: first over every line that survived the
 * quote filters, to obtain a provisional forward F1; then over the lines with
 * K inside [F1 (1 - w), F1 (1 + w)] (w = QuoteFilter.moneyness_window). An
 * expiry with fewer than `min_quotes_per_expiry` pairs after either pass is
 * reported as dropped (not a 400); when no expiry survives the request is a
 * 400 that lists the reasons.
 *
 * Weights: w_i = 1 / ((call_ask_i - call_bid_i) + (put_ask_i - put_bid_i)).
 * If any pair of the expiry has a zero total spread, every pair of that
 * expiry gets weight 1 (equal weights) — a zero spread would otherwise be an
 * infinite weight.
 *
 * Residuals r_i = y_i - (a + b K_i) over the pairs of the final pass:
 * fit_residual_rmse = sqrt(sum r_i^2 / n), max_abs_residual = max |r_i|,
 * both unweighted and in price units.
 *
 * Curve-relative quantities, with T the Act/365F year fraction from the
 * as-of date to the expiry and DF_curve the discounting curve's discount
 * factor at the expiry date:
 *
 *     r(T)              = -ln(DF_curve) / T          (continuous, Act/365F)
 *     q(T)              = r(T) - ln(F / S) / T       (from F = S e^{(r - q) T})
 *     implied_funding   = -ln(DF_implied / DF_curve) / T * 1e4   (bp; > 0 means
 *                         the options imply a higher funding rate than the curve)
 *
 * The returned dividend curve is a list of (date, continuous zero) points:
 * one at the as-of date carrying the first fitted expiry's q (the curve's
 * first node is its reference date, so the short end is flat) and one per
 * fitted expiry with q(T). Discounting on the request curve and dividends on
 * that curve (Act/365F) gives S * DF_div(T) / DF_curve(T) = F exactly at every
 * fitted expiry. Those yields are "equity repo + dividend": whatever the
 * market forward implies, which is what pricing should use.
````

Source: `src/evaluators/implied_forwards_evaluator.h@v0.14.0:L11-L60`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/src/evaluators/implied_forwards_evaluator.h#L11-L60>

### 4. Using the returned dividend curve (repo + dividend, flat short end, exact forwards at the fitted expiries).

````text
### Using the dividend curve

`dividend_curve_spec` is a complete `TermStructure`: append it to
`pricing.rates.curves` and set the underlying's `dividend_yield_curve_id` to
its id. It has a node at the as-of date carrying the first fitted expiry's
yield (the curve's first node is its reference date, so the short end is
flat) and one node per fitted expiry with `q(T)`, continuous, Act/365F,
linear between nodes. Pricing on the request's discounting curve and this
dividend curve gives `S · DF_div(T) / DF_curve(T) = F` exactly at every
fitted expiry — a call/put pair priced by `/price-equity-option` reproduces
the forward through parity (the contract test checks it). The yields are
"equity repo + dividend": whatever the market forward implies relative to
the chosen discounting curve, which is what pricing should carry; the
`implied_funding_bp` column shows how much of it is funding rather than
dividends. Between fitted expiries the curve interpolates the zero yield;
beyond the last one it is flat.
````

Source: `docs/http-api.md@v0.14.0:L1586-L1601`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/docs/http-api.md#L1586-L1601>

### 5. Implied vols: the out-of-the-money side of every line, Black-76 inversion on the forward, the drop reasons and the minimum quote counts per method.

````text
**Implied vols.** Per expiry with forward `F` and implied discount factor
`DF` (from `forwards` or the internal fit), every line that passes the
per-line rules contributes its out-of-the-money side: the call for
`K ≥ F`, the put below. Its mid is inverted with Black-76 on the forward
(`blackFormulaImpliedStdDev(type, K, F, mid, DF)`, `σ = stdDev / √T`, `T`
Act/365F). Dropped with a reason: a mid not above the intrinsic value, a mid
below the quote resolution `1e-5 x spot` (0.24 points on a 24,000 index:
such a price carries no vol information, any vol prices inside its bid/ask),
a mid outside the Black bounds (no vol solves). An expiry needs
`max(filter.min_quotes_per_expiry, 5)` used quotes for `SviPerExpiry`
(five parameters) and `filter.min_quotes_per_expiry` for `AndreasenHuge`;
with fewer it is reported unusable. `400` when no expiry is usable.
````

Source: `docs/http-api.md@v0.14.0:L1664-L1675`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/docs/http-api.md#L1664-L1675>

### 6. The Black-76 inversion as the engine calls it (QuantLib `blackFormulaImpliedStdDev`).

````cpp
std::optional<double> impliedVol(
    QuantLib::Option::Type type, double K, double F, double price, double DF, double T) {
    try {
        const double sd = QuantLib::blackFormulaImpliedStdDev(
            type, K, F, price, DF, 0.0, QuantLib::Null<Real>(), 1e-12, 1000);
        const double v = sd / std::sqrt(T);
        if (!std::isfinite(v) || !(v > 0.0)) return std::nullopt;
        return v;
    } catch (...) {
        return std::nullopt;
    }
}
````

Source: `src/evaluators/vol_surface_calibration_evaluator.cpp@v0.14.0:L84-L95`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/src/evaluators/vol_surface_calibration_evaluator.cpp#L84-L95>

### 7. AndreasenHuge: calibration set, curves from the implied discount factors and forwards, QuantLib settings, the price floor for grid inversion and the reported calibration error.

````text
**AndreasenHuge.** Every used quote becomes a calibration pair of QuantLib's
`AndreasenHugeVolatilityInterpl` (the out-of-the-money `VanillaOption` and a
quote carrying its implied vol; interpolation `CubicSpline`, calibration
type `CallPut`, 500 grid points, Levenberg-Marquardt), on a risk-free curve
built from the implied discount factors and a dividend curve with
`DF_q(T) = F · DF / S` (both log-linear, Act/365F), so the model's forwards
and discounting are the sheet's. The method calibrates a piecewise local
volatility expiry by expiry to the Black prices of those vols on a one-step
finite-difference scheme (Andreasen & Huge, *Volatility Interpolation*,
2010) and is free of static arbitrage by construction; no weights, the
calibration matches prices. `AndreasenHugeVolatilityAdapter` then gives the
Black vol at every grid point by inverting the model price. A point is
inverted only where the model's out-of-the-money price is at least
`1e-4 x spot` (the finite-difference price carries noise of about
`1e-7 x spot`, which below that moves the implied vol by more than a tenth
of a vol point and inverts to arbitrary vols where the true price is ~0);
the other points are filled flat from the nearest inverted strike of the
same expiry and counted in `grid_points_filled`. On the flat 20 % synthetic
sheet this keeps every grid vol within 0.1 vol point of 20 %.
`andreasen_huge_error` reports QuantLib's calibration error (min, max, mean
in vol units over the calibration set).
````

Source: `docs/http-api.md@v0.14.0:L1677-L1697`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/docs/http-api.md#L1677-L1697>

### 8. The QuantLib construction: `AndreasenHugeVolatilityInterpl` (CubicSpline, CallPut, 500 points) and `AndreasenHugeVolatilityAdapter`.

````cpp
    if (in.method == VolSurfaceFitMethod::AndreasenHuge) {
        std::vector<Date> curveDates{asOf};
        std::vector<QuantLib::DiscountFactor> rDfs{1.0}, qDfs{1.0};
        QuantLib::AndreasenHugeVolatilityInterpl::CalibrationSet set;
        for (const auto& w : work) {
            curveDates.push_back(w.expiry);
            rDfs.push_back(w.df);
            qDfs.push_back(w.forward * w.df / spot);
            auto exercise = std::make_shared<QuantLib::EuropeanExercise>(w.expiry);
            for (const auto& u : w.used) {
                auto payoff = std::make_shared<QuantLib::PlainVanillaPayoff>(
                    u.isCall ? QuantLib::Option::Call : QuantLib::Option::Put, u.strike);
                set.emplace_back(
                    std::make_shared<QuantLib::VanillaOption>(payoff, exercise),
                    std::make_shared<QuantLib::SimpleQuote>(u.vol));
            }
        }
        Handle<QuantLib::YieldTermStructure> rTS(
            std::make_shared<QuantLib::DiscountCurve>(curveDates, rDfs, dc));
        Handle<QuantLib::YieldTermStructure> qTS(
            std::make_shared<QuantLib::DiscountCurve>(curveDates, qDfs, dc));
        Handle<QuantLib::Quote> spotH(std::make_shared<QuantLib::SimpleQuote>(spot));
        std::shared_ptr<QuantLib::AndreasenHugeVolatilityInterpl> ah;
        std::shared_ptr<QuantLib::AndreasenHugeVolatilityAdapter> adapter;
        try {
            ah = std::make_shared<QuantLib::AndreasenHugeVolatilityInterpl>(
                set, spotH, rTS, qTS, QuantLib::AndreasenHugeVolatilityInterpl::CubicSpline,
                QuantLib::AndreasenHugeVolatilityInterpl::CallPut, 500);
            adapter = std::make_shared<QuantLib::AndreasenHugeVolatilityAdapter>(ah);
            const auto err = ah->calibrationError();
            AndreasenHugeErrorData ed;
            ed.minVol = std::get<0>(err);
            ed.maxVol = std::get<1>(err);
            ed.avgVol = std::get<2>(err);
            out.andreasenHugeError = ed;
````

Source: `src/evaluators/vol_surface_calibration_evaluator.cpp@v0.14.0:L456-L490`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/src/evaluators/vol_surface_calibration_evaluator.cpp#L456-L490>

### 9. SviPerExpiry: QuantLib `SviInterpolation` per expiry, the raw SVI total variance, Durrleman's butterfly condition and the penalized refit.

````text
**SviPerExpiry.** Per usable expiry QuantLib's `SviInterpolation`
(Levenberg-Marquardt on the vega-weighted implied-vol residuals, several
deterministic starts; the parameter transforms keep `b ≥ 0`, `|ρ| < 1`,
`σ > 0`, `a + b σ √(1 − ρ²) ≥ 0`, `b (1 + |ρ|) ≤ 4`) fits the raw SVI
total variance

```
w(k) = a + b (ρ (k − m) + sqrt((k − m)² + σ²)),   k = ln(K / F)
```

Butterfly check: Durrleman's `g(k) = (1 − k w′/(2w))² − (w′²/4)(1/w + 1/4)
+ w″/2` on 201 points of the output strike range; `min g < 0` is a
violation (`butterfly_violation`, `butterfly_min_g`). With
`enforce_butterfly` the five parameters are refitted from the fitted ones
by Levenberg-Marquardt on the same weighted residuals plus a penalty
`100 · max(0, −g(k_j))` per point; `butterfly_action` carries `min g`
before and after (`"refit with penalty: min g -0.41 -> 0.0012"`). Grid vols
are `sqrt(w(k) / T)`.
````

Source: `docs/http-api.md@v0.14.0:L1699-L1716`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/docs/http-api.md#L1699-L1716>

### 10. SVI total variance and Durrleman's g(k) as coded.

````cpp
double sviTotalVariance(const SviParams& p, double k) {
    const double d = k - p.m;
    return p.a + p.b * (p.rho * d + std::sqrt(d * d + p.sigma * p.sigma));
}

double sviDurrlemanG(const SviParams& p, double k) {
    const double d = k - p.m;
    const double R = std::sqrt(d * d + p.sigma * p.sigma);
    const double w = sviTotalVariance(p, k);
    if (!(w > 0.0)) return -std::numeric_limits<double>::infinity();
    const double w1 = p.b * (p.rho + d / R);
    const double w2 = p.b * p.sigma * p.sigma / (R * R * R);
    const double t1 = 1.0 - k * w1 / (2.0 * w);
    return t1 * t1 - (w1 * w1 / 4.0) * (1.0 / w + 0.25) + w2 / 2.0;
````

Source: `src/evaluators/vol_surface_calibration_evaluator.cpp@v0.14.0:L182-L195`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/src/evaluators/vol_surface_calibration_evaluator.cpp#L182-L195>

### 11. The SVI fit call (QuantLib `SviInterpolation`, Levenberg-Marquardt, end criteria).

````cpp
    if (in.method == VolSurfaceFitMethod::SviPerExpiry) {
        const auto endCriteria =
            std::make_shared<QuantLib::EndCriteria>(500, 100, 1e-12, 1e-10, 1e-10);
        for (auto& w : work) {
            auto& e = reportOf(w.expiry);
            std::vector<double> strikes, vols;
            for (const auto& u : w.used) {
                strikes.push_back(u.strike);
                vols.push_back(u.vol);
            }
            SviParams p;
            try {
                const auto lm = std::make_shared<QuantLib::LevenbergMarquardt>(1e-10, 1e-10, 1e-10);
                QuantLib::SviInterpolation interp(
                    strikes.begin(), strikes.end(), vols.begin(), w.time, w.forward,
                    QuantLib::Null<Real>(), QuantLib::Null<Real>(), QuantLib::Null<Real>(),
                    QuantLib::Null<Real>(), QuantLib::Null<Real>(), false, false, false, false,
                    false, true, endCriteria, lm);
                interp.update();
````

Source: `src/evaluators/vol_surface_calibration_evaluator.cpp@v0.14.0:L567-L585`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/src/evaluators/vol_surface_calibration_evaluator.cpp#L567-L585>

### 12. Calendar check (both methods; clipped only for SVI with enforce_calendar).

````text
**Calendar check (both methods).** At every grid strike total variance
`σ² T` must not decrease from one grid expiry to the next; a point where
`w_i < w_{i−1} + 1e-8` is counted in `calendar_violations`. `SviPerExpiry`
with `enforce_calendar` clips it to `w_{i−1} + 1e-8` (`calendar_action`,
`calendar_max_clip_vol` = largest vol change); `AndreasenHuge` reports only
(the surface is arbitrage-free by construction; a residual count is within
the implied-vol inversion tolerance).
````

Source: `docs/http-api.md@v0.14.0:L1718-L1724`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/docs/http-api.md#L1718-L1724>

### 13. Fit error and the vanilla repricing report: the returned surface rebuilt as the pricers read it, every used quote repriced, inside the bid/ask or not.

````text
**Fit error and repricing.** `rmse_vol` / `max_abs_error_vol` per expiry
compare the calibrated object (the SVI slice, the Andreasen-Huge surface)
with the market implied vols of the used quotes, in vol units (0.01 = one
vol point), unweighted. `vanilla_repricing` then rebuilds the returned
surface exactly as the pricers read it (`BlackVarianceSurface`,
NullCalendar, Act/365F, bilinear in total variance, extrapolation allowed)
and reprices every used quote on it: `model_vol`, `model_price` (Black on
the same `F` and `DF`), `inside_bid_ask`, `price_error = model − mid`,
`vol_error = model_vol − market_vol`, with `count`, `inside_bid_ask`,
`max_abs_price_error`, `max_abs_vol_error` on top. This is the acceptance
check of the surface, grid interpolation included: a quote between grid
strikes reads an interpolated vol. On the synthetic SVI sheet (strikes every
2.5 % of spot, 1 % bid/ask) the default grid reprices every used quote
inside its bid/ask under both methods; the coarser 5 % grid read bilinearly
misses about a quarter of them, which is why the default is 2.5 %.
````

Source: `docs/http-api.md@v0.14.0:L1726-L1740`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/docs/http-api.md#L1726-L1740>

### 14. The repricing surface as coded (`BlackVarianceSurface`, NullCalendar, bilinear, extrapolation).

````cpp
    // 5. Vanilla repricing on the returned surface.
    // ------------------------------------------------------------------
    {
        QuantLib::Matrix m(nStr, nExp);
        for (std::size_t i = 0; i < nExp; ++i)
            for (std::size_t j = 0; j < nStr; ++j)
                m[j][i] = out.gridVols[i * nStr + j];
        auto surface = std::make_shared<QuantLib::BlackVarianceSurface>(
            asOf, QuantLib::NullCalendar(), gridDates, out.gridStrikes, m, dc);
        surface->setInterpolation<QuantLib::Bilinear>();
        surface->enableExtrapolation();
````

Source: `src/evaluators/vol_surface_calibration_evaluator.cpp@v0.14.0:L716-L726`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/src/evaluators/vol_surface_calibration_evaluator.cpp#L716-L726>

### 15. Using the surface: where to paste it and how to keep the forwards consistent.

````text
### Using the surface

Append `vol_surface_spec` to `pricing.volatility.vol_surfaces` and
reference its id from the trade: `volatility` on an equity option (the
contract test prices the used quotes this way, inside their bid/ask, next
to the implied dividend curve of `/calibrate-implied-forwards`), or
`underlyings[].volatility_id` on a structured note, where a strike x expiry
surface makes the simulation Dupire local-vol and requires
`monte_carlo.steps_per_year` (see "Structured notes"). Keep the forwards
consistent: price on the discounting curve the quotes were fitted against
and on the dividend curve the implied-forward step returned; otherwise the
surface's strikes sit at other forward moneyness than the market's. Vols
beyond the grid are extrapolated flat in total variance at the edge
(`allow_extrapolation: true`).
````

Source: `docs/http-api.md@v0.14.0:L1794-L1807`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/docs/http-api.md#L1794-L1807>

## Request fields that control it

- `OptionQuote`: one line of the sheet.

````text
table OptionQuote {
    /// Expiry date (YYYY-MM-DD).
    expiry:string (required);
    /// Strike in underlying price units. Must be > 0.
    strike:double = null;
    call_bid:double = null;
    call_ask:double = null;
    put_bid:double = null;
    put_ask:double = null;
    /// Traded volume on the quote date (contracts). Informational; echoed
    /// nowhere, reserved for filters.
    call_volume:double = null;
    put_volume:double = null;
    /// Open interest (contracts). Used by `QuoteFilter.min_open_interest`.
    call_open_interest:double = null;
    put_open_interest:double = null;
}
````

Source: `flatbuffers/fbs/option_quotes.fbs@v0.14.0:L15-L31`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/flatbuffers/fbs/option_quotes.fbs#L15-L31>

- `QuoteFilter`: the filter values.

````text
table QuoteFilter {
    /// Maximum (ask - bid) / mid on EACH side (call and put) of a line.
    /// Must be > 0.
    max_relative_spread:double = 0.05;
    /// Minimum number of call/put pairs an expiry needs AFTER both filter
    /// passes to be fitted; an expiry with fewer is reported as dropped
    /// (not a 400, unless no expiry survives). Must be >= 2.
    min_quotes_per_expiry:uint = 4;
    /// Second-pass window: after a first fit over every surviving pair gives
    /// a provisional forward F1, only strikes inside
    /// [F1 * (1 - w), F1 * (1 + w)] enter the final fit. Must be > 0.
    moneyness_window:double = 0.25;
    /// Optional: drop a line whose call OR put open interest is below this
    /// (a line without open-interest fields is kept when the threshold is
    /// absent and dropped when it is present).
    min_open_interest:double = null;
}
````

Source: `flatbuffers/fbs/option_quotes.fbs@v0.14.0:L57-L73`

GitHub: <https://github.com/joseprupi/quantraserver/blob/v0.14.0/flatbuffers/fbs/option_quotes.fbs#L57-L73>

## Not documented in engine v0.14.0

- Nothing further: the excerpts above cover the behaviour the server relies on.
- The engine source at this tag and the QuantLib source it is built on are readable with search_source / read_source (cite `<repo>:<path>@<tag>:L<a>-L<b>`); a point this page does not document is to be read there, never answered from memory.
