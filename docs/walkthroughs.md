# Walkthroughs

Two end-to-end sequences. The presets they use are described in
[presets.md](presets.md); the tools in [tools.md](tools.md).

## Price a swap in three calls

1. `build_curve(id="EUR6M", preset="EUR_EURIBOR_6M", reference_date="2025-01-15",
   quotes=[{type:"deposit", tenor:"6M", rate:0.0295}, {type:"swap", tenor:"2Y", rate:0.030},
   ..., {type:"swap", tenor:"10Y", rate:0.032}])` turns the strip into an engine curve
   with the preset's helper conventions and index definition (no engine call;
   `notes` lists every convention and its source).
2. `session_put(name="eur", kind="curve", value=<build_curve result>)` keeps it in
   memory so the next call can say `{"session": "eur"}`.
3. `price_vanilla_swap(market={"session": "eur"}, preset="EUR_EURIBOR_6M", as_of="2025-01-15",
   swap_type="Payer", notional=10000000, fixed_rate=0.032, effective_date="spot", tenor="5Y",
   discounting_curve="EUR6M", forwarding_curve="EUR6M", include_flows=true)` builds the
   trade from the preset's `vanilla_swap` block (annual 30/360 fixed vs semiannual
   Act/360 Euribor 6M, ModifiedFollowing on TARGET), asks the engine's
   `/calendar-advance` for `spot` (as_of + 2 business days) and for the 5Y end date
   (both calls are kept in `date_resolution`), validates the request against the
   vendored spec, POSTs `/price-vanilla-swap` and returns the engine's answer
   verbatim plus `summary.swaps[0]` (npv, fair_rate), the exact `request` sent
   and `notes` (every default with its preset source).

The same three steps work for an OIS (`USD_SOFR_OIS` / `EUR_ESTR_OIS` +
`price_ois_swap`), and a vendored example's `pricing` block can be passed as
the `market` directly: `get_example("irs_eur_5y_payer_ois_discounted_multicurve")`
then `price_vanilla_swap(market=example.body.pricing, ...)` rebuilds the
example's trade JSON-equal to the engine fixture (the live suite proves this
for twelve of the pricing tools, one engine fixture each, with the engine's
number matching the catalog's QuantLib reference value).

## Build a curve from a strip

Three tool calls turn a quote strip into bootstrapped discount factors and
zero rates, with every market convention taken from a named preset and
reported back:

1. `build_curve(id="USD_SOFR_OIS", preset="USD_SOFR_OIS", reference_date="2025-01-15",
   quotes=[{type:"ois", tenor:"1M", rate:0.0533}, ..., {type:"ois", tenor:"50Y", rate:0.0295}])`
   returns the engine `TermStructure` plus the `IndexDef` it references and a
   `notes` list such as `ois.payment_lag=2 from preset USD_SOFR_OIS (15 points)`.
   No engine call.
2. `build_query(curve_id="USD_SOFR_OIS", measures=["DF","ZERO"], tenors=["1M","6M","1Y",...,"50Y"],
   calendar="UnitedStatesGovernmentBond", business_day_convention="ModifiedFollowing")`
   returns a `CurveQuerySpec` (zero options default to continuous annual
   zeros in the curve day counter and say so). No engine call.
3. `bootstrap_curve(curves=[<build_curve result>], as_of="2025-01-15", queries=[<build_query result>])`
   POSTs `/bootstrap-curves` and returns the grid; the echoed `request` is the
   complete body. With the 14-pillar SOFR strip of the
   [SOFR post](https://quantra.io/blog/bootstrapping-a-sofr-curve) this request
   is JSON-equal to `quantra://examples/sofr-bootstrap-request` and the 50Y
   discount factor is `0.262755579831`.

`session_put(name="sofr", kind="curve", value=<build_curve result>)` stores the
curve (with its index) in memory so later calls can pass
`{"session": "sofr"}` instead of the full object; the echoed request always
shows the resolved curve. `build_value_curve` makes an interpolated curve from
explicit zero / discount / forward values instead of a strip.
