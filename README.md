# quantra-mcp

An [MCP](https://modelcontextprotocol.io) server that lets any MCP-capable agent
(Claude Desktop, Claude Code, Cursor, custom agents) price swaps, bonds, options,
CDS and inflation trades, bootstrap curves, calibrate volatilities and query
business calendars on a [Quantra](https://quantra.io) pricing engine
(QuantLib-based, open source), with market conventions supplied by named presets.

It is a **client** of the engine's JSON API. **Every number comes from the
engine; this server computes nothing.** Each tool result echoes the exact
request that was sent and the engine's response verbatim.

```
agent ──(MCP: stdio / streamable HTTP)──▶ quantra-mcp ──(HTTP/JSON)──▶ Quantra engine
                                                                       (localhost:8080
                                                                        or api.quantra.io)
```

Status: **0.1.0.dev0**, phase M3 (discovery, calendars, raw passthrough,
market construction, one pricing tool per product, the engine's 221 example
requests as a catalog, prompts).
Engine contract pinned to **v0.7.0** (`src/quantra_mcp/schema/PIN`); any
engine `>= 0.7.0` that keeps the contract works.

## Run an engine

One line, no account:

```bash
docker run -d --name quantra-engine -p 8080:8080 ghcr.io/joseprupi/quantra-server:0.7.0
curl http://localhost:8080/health   # {"status":"healthy"}
```

Or point the server at the public demo with `QUANTRA_ENGINE_URL=https://api.quantra.io`.

## Install

Until the first PyPI release, run from a checkout:

```bash
git clone https://github.com/joseprupi/quantra-mcp && cd quantra-mcp
uv sync
uv run quantra-mcp --help
```

(After release: `uvx quantra-mcp`.)

## Configure your agent

### Claude Code

```bash
claude mcp add quantra -e QUANTRA_ENGINE_URL=http://localhost:8080 -- uv --directory /path/to/quantra-mcp run quantra-mcp
```

Project form (`.mcp.json` in the repo root, shared with the team):

```json
{
  "mcpServers": {
    "quantra": {
      "command": "uv",
      "args": ["--directory", "/path/to/quantra-mcp", "run", "quantra-mcp"],
      "env": { "QUANTRA_ENGINE_URL": "http://localhost:8080" }
    }
  }
}
```

### Claude Desktop

`claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "quantra": {
      "command": "uv",
      "args": ["--directory", "/path/to/quantra-mcp", "run", "quantra-mcp"],
      "env": { "QUANTRA_ENGINE_URL": "http://localhost:8080" }
    }
  }
}
```

### Cursor

`.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global):

```json
{
  "mcpServers": {
    "quantra": {
      "command": "uv",
      "args": ["--directory", "/path/to/quantra-mcp", "run", "quantra-mcp"],
      "env": { "QUANTRA_ENGINE_URL": "http://localhost:8080" }
    }
  }
}
```

### Streamable HTTP (remote agents)

```bash
QUANTRA_ENGINE_URL=http://localhost:8080 uv run quantra-mcp --http --port 8765
# MCP endpoint: http://127.0.0.1:8765/mcp
```

## Environment

| Env | Default | Meaning |
|---|---|---|
| `QUANTRA_ENGINE_URL` | `http://localhost:8080` | Engine JSON gateway (self-hosted or `https://api.quantra.io`). |
| `QUANTRA_TIMEOUT_S` | `60` | Per-request timeout in seconds. |
| `QUANTRA_MCP_LOG` | `info` | stderr log level (`debug`, `info`, `warning`, `error`). stdout is the MCP channel. |
| `QUANTRA_SESSION_MAX_ITEMS` | `64` | Cap on in-memory session scratch items (least-recently-used eviction). |

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

### Presets

Presets are data files under `src/quantra_mcp/presets/` (also served as
`quantra://presets/{id}`). A preset supplies the index definition, the curve
day counter / interpolator / trait and one convention block per helper type;
the builder adds only the quote and the tenor. Every field has a provenance;
fields whose source is not an in-repo file are marked
`market standard (ISDA/CCP), not from an in-repo source` in `field_provenance`.

| Preset | Index | Helpers | Conventions | Provenance |
|---|---|---|---|---|
| `USD_SOFR_OIS` | `USD_SOFR` (Overnight, UnitedStatesGovernmentBond, Act/360, fixing 0) | ois | T+2, Annual, ModifiedFollowing, payment lag 2, Compound, no lookback/lockout; curve Act/365F LogLinear Discount | SOFR post conventions table + the byte-verified gold example (50Y DF 0.262755579831) |
| `EUR_ESTR_OIS` | `EUR_ESTR` (Overnight, TARGET, Act/360, fixing 0) | ois | T+2, Annual, ModifiedFollowing, payment lag 1, Compound | index: orchestrator `_KNOWN_OVERNIGHT_INDICES`; OIS block: market standard (ISDA/CCP), not from an in-repo source |
| `GBP_SONIA_OIS` | `GBP_SONIA` (Overnight, UnitedKingdom, Act/365F, fixing 0) | ois | T+0, Annual, ModifiedFollowing, payment lag 0, Compound | index: seeded SONIA; OIS block: market standard (ISDA/CCP), not from an in-repo source |
| `GBP_SONIA_SWAP` | `GBP_SONIA` | deposit, swap | deposit fixing 0 / UK / MF / Act/365F; swap Annual / MF / Act/365F on UK, float index SONIA | the platform's seeded "GBP SONIA OIS (BoE, daily public)" curve (`seed_demo_entities.py`) |
| `EUR_EURIBOR_6M` | `EURIBOR_6M` (Ibor 6M, TARGET, Act/360, MF, fixing 2) | deposit, fra, swap | deposits/FRAs with the Euribor conventions; swap Annual / 30/360 / MF on TARGET | index: orchestrator `_KNOWN_IBOR_INDICES`; swap fixed leg: market standard (ISDA/CCP), not from an in-repo source |
| `EUR_EURIBOR_3M` | `EURIBOR_3M` (Ibor 3M, TARGET, Act/360, MF, fixing 2) | deposit, fra, future, swap | as above plus 3M futures (`future_months` 3, convexity adjustment stated) | as above; `future_months`: market standard (ISDA/CCP), not from an in-repo source |

Bond presets (`UST_BOND`, `GILT_BOND`) are deferred: the `BondHelper`
conventions are not fully sourced from an in-repo file yet.

Presets also carry `trades` blocks, the conventions the pricing tools apply
(schedule rules, leg frequencies and day counters, vol-surface base
conventions, credit-curve helper conventions, ...). Every trade block is
sourced from a named engine fixture at the pin (`field_provenance`):

| Preset | Trade blocks | Source fixtures |
|---|---|---|
| `EUR_EURIBOR_6M` | `vanilla_swap`, `floating_rate_bond`, `fra`, `cap_floor`, `swaption` | `irs_eur_5y_payer_ois_discounted_multicurve`, `frn_eur_5y_euribor6m_flat_semiannual`, `fra_eur_6x12_long_euribor6m`, `cap_eur_10y_euribor6m_semiannual`, `swpt_eur_1y5y_payer_near_atm_physical` |
| `EUR_EURIBOR_3M` | `vanilla_swap`, `fra`, `cap_floor` | `irs_eur_4y_payer_vs_euribor3m_quarterly`, `fra_eur_3x6_long_at_forward`, `cap_eur_5y_itm_strike_2pct` |
| `USD_SOFR_OIS` | `ois_swap` (payment lag 2) | the SOFR post's OIS example; `ois_usd_3y_payer_sofr` with `payment_lag=0` |
| `EUR_ESTR_OIS` | `ois_swap` (payment lag 0) | `ois_eur_5y_payer_estr` |
| `EUR_FIXED_BOND` (trade-only) | `fixed_rate_bond`, `zero_coupon_bond`, `callable_fixed_rate_bond` | `frb_eur_5y_at_par_annual_30360`, `zcb_eur_10y_discount`, `cfrb_eur_8y_5pct_call_100_itm` |
| `EUR_CDS` (trade-only) | `cds` (quarterly TwentiethIMM, MidPoint, recovery 40%) | `cds_eur_5y_buyer_100bp_spread_curve` |
| `EUR_EQUITY` (trade-only) | `equity_option` | `eqopt_eur_call_atm_1y` |
| `EUR_HICP` (trade-only) | `zc_inflation_swap`, `yoy_inflation_swap`, `yoy_inflation_cap_floor` | `zciis_eur_5y_payer_linear_obs`, `yyiis_eur_5y_payer_annual`, `yoy_cf_eur_5y_cap_150bp_black_bites` |

## Tools

| Tool | Does |
|---|---|
| `quantra_meta` | `GET /meta` verbatim: API version, QuantLib version, products, endpoints. Call first. |
| `quantra_health` | `GET /health`. |
| `list_endpoints` | The 24 POST endpoints with one-line descriptions (from the vendored spec). |
| `engine_schema(endpoint, depth=3)` | Request + response schema for one endpoint, `$ref`s inlined to `depth`, top-level required fields. |
| `list_enums(name)` | Values of an engine enum (`Calendar`, `DayCounter`, `Frequency`, `BusinessDayConvention`, `TimeUnit`, ...). |
| `calendar_holidays(calendar, start_date, end_date, include_weekends=False, calendar_overrides=None)` | `POST /calendar-holidays`. |
| `calendar_business_days(calendar, start_date, end_date, include_start=True, include_end=True, calendar_overrides=None)` | `POST /calendar-business-days`. |
| `calendar_advance(calendar, date, tenor_number, tenor_unit, convention, end_of_month=False, calendar_overrides=None)` | `POST /calendar-advance`. |
| `engine_request(endpoint, body, validate=True, request_id=None)` | POST any endpoint. Validates `body` against the vendored spec first (readable JSON-pointer errors); forwards `X-Request-Id`. |
| `list_presets()` / `get_preset(id)` | The market-convention presets (data + provenance). No engine call. |
| `build_curve(id, preset, quotes, reference_date, trait?, interpolator?, day_counter?)` | Quote strip -> `{curve, indices, preset, notes}`; sorted by maturity, duplicate tenors rejected, every convention noted with its source. No engine call. |
| `build_value_curve(id, kind, points, reference_date, preset? \| conventions?, compounding?, frequency?, interpolator?)` | Explicit zero / discount / forward values -> an `Interpolated*` curve (discount: first point must be 1.0 at the reference date). No engine call. |
| `build_query(curve_id, measures, tenors? \| range_grid?, calendar?, business_day_convention?, zero?, fwd?)` | A `CurveQuerySpec`; FWD needs explicit `fwd` options. No engine call. |
| `bootstrap_curve(curves, as_of, queries, indices?, calendar_overrides?, request_id?)` | `POST /bootstrap-curves`. `curves` items: `TermStructure`, `build_curve` result or `{"session": name}`; echoed `request` is the resolved body; `summary` = per curve `{id, pillars, first_grid_date, last_grid_date, measures}`. |
| `bootstrap_inflation_curve(body, request_id?)` | `POST /bootstrap-inflation-curves` with a raw body (validated first). |
| `session_put(name, kind, value)` / `session_get(name)` / `session_list()` / `session_delete(name)` | In-memory scratch (`curve`, `index`, `market`), per process, never persisted, LRU-capped. |
| `list_examples(category?, product?)` / `get_example(name)` | The 223 vendored example requests (engine fixtures + the two blog examples) with endpoint, catalog description and QuantLib reference value; `body` is ready for `engine_request`, `body.pricing` is a valid `market`. No engine call. |

### Pricing tools (one per product)

Every pricing tool takes `market` (`{"session": name}`, an engine `pricing`
block used verbatim, or `{curves: [...], indices: [...], ...}` of build_curve
results), a `preset` whose trade block supplies every convention, the trade
economics, optional `additional_trades` (same product, same request) and
`calendar_overrides`. Dates the server cannot compute are resolved by the
engine, one `/calendar-advance` call each, and reported in `date_resolution`:
`effective_date: "spot"` = as_of + the preset's settlement days (Following),
`tenor: "5Y"` = effective date + tenor (Unadjusted; the schedule applies its
own conventions), FRA `3x6` = spot + 3 / 6 months with the FRA convention.
Objects the tool builds (vol surfaces, models, credit curves, coupon pricers,
flat curves, quotes) are added to the market under an explicit or default id
(noted); an id already in the market with different content is a local error.

| Tool | Endpoint | Builds from the preset |
|---|---|---|
| `price_vanilla_swap(market, preset, swap_type, notional, fixed_rate, effective_date, discounting_curve, forwarding_curve, termination_date \| tenor, spread=0, index_id?, fixed_leg_overrides?, floating_leg_overrides?, include_flows?)` | `/price-vanilla-swap` | both legs' schedules, frequencies, day counters, payment conventions |
| `price_ois_swap(... payment_lag?, averaging_method?, lookback_days?, lockout_days?, apply_observation_shift?, telescopic_value_dates?, overnight_leg_overrides?)` | `/price-ois-swap` | as above plus the overnight-leg parameters (each defaults to the preset's value) |
| `price_fixed_rate_bond(market, preset, face_amount, coupon_rate, issue_date, discounting_curve, maturity_date \| tenor, effective_date?, overrides?, yield_overrides?, include_details?, include_flows?)` | `/price-fixed-rate-bond` | settlement days, schedule, accrual day counter, redemption, yield quotation |
| `price_floating_rate_bond(... spread, index_id?, fixing_days?, in_arrears?, coupon_pricer?)` | `/price-floating-rate-bond` | as above; adds the preset's zero-vol `BlackIborCouponPricer` unless `coupon_pricer` names one |
| `price_zero_coupon_bond(market, preset, face_amount, discounting_curve, maturity_date \| tenor, issue_date?, settlement_days?, redemption?, include_details?)` | `/price-zero-coupon-bond` | settlement days (T+3), calendar, redemption, yield quotation |
| `price_callable_fixed_rate_bond(... call_schedule=[{date, price, type}], model={a, sigma, lattice_steps?} \| id, tree_steps?)` | `/price-callable-fixed-rate-bond` | bond conventions, lattice and tree steps; explicit Hull-White a / sigma |
| `price_fra(market, preset, notional, strike, side, discounting_curve, forwarding_curve, months_to_start + months_to_end \| start_date + maturity_date, index_id?)` | `/price-fra` | day counter, calendar, convention; dates from spot via the engine |
| `price_cap_floor(market, preset, cap_floor_type, notional, strike, effective_date, discounting_curve, forwarding_curve, vol={constant, type, displacement?, id?} \| id, model=Black\|Bachelier\|ShiftedBlack\|HullWhiteLattice \| id, termination_date \| tenor, include_details?)` | `/price-cap-floor` | schedule, frequency, day counter, OptionletVolSpec base conventions |
| `price_swaption(market, preset, underlying=<VanillaSwapTrade \| OisSwapTrade>, discounting_curve, forwarding_curve, vol=<constant \| atm_matrix \| raw payload \| id>, model=Black\|ShiftedBlack\|Bachelier \| {a, sigma, lattice_steps} \| id, exercise_date \| exercise_dates, exercise_type, settlement_type, settlement_method?, include_details?, include_diagnostics?)` | `/price-swaption` | underlying from the preset's swap block, `swap_index_id`, settlement method, SwaptionVolSpec base conventions |
| `price_cds(market, side, notional, running_coupon, discounting_curve, credit_curve={par_spreads} \| {hazard_rate} \| id, preset="EUR_CDS", start="as_of", maturity \| tenor, recovery_rate?, model=MidPoint\|ISDA \| id, upfront?, ...)` | `/price-cds` | premium schedule (TwentiethIMM), accrual flags, credit-curve helper conventions, engine type |
| `price_equity_option(spot, strike, expiry, option_type, vol={constant, id?} \| id, rate_curve={rate, end_date} \| id, dividend_yield={rate, end_date} \| id, preset="EUR_EQUITY", exercise=European\|American\|Bermudan, model?, discrete_dividends?, market?, as_of)` | `/price-equity-option` | flat curves as two-point InterpolatedZero curves, BlackVolSpec base, settlement |
| `price_zc_inflation_swap(market, inflation_index_id, fixings, swap_type, notional, fixed_rate, discounting_curve, inflation_curve, preset="EUR_HICP", start_date="as_of", maturity_date \| tenor)` | `/price-zero-coupon-inflation-swap` | observation lag and interpolation, calendars, day counter |
| `price_yoy_inflation_swap(... effective_date="as_of", termination_date \| tenor, spread=0)` | `/price-year-on-year-inflation-swap` | shared fixed / YoY schedule, day counters, payment conventions |
| `price_yoy_inflation_cap_floor(... cap_floor_type, cap_rate?, floor_rate?, vol={constant, type} \| id)` | `/price-year-on-year-inflation-cap-floor` | schedule, day counter, YoYOptionletVolSpec conventions |
| `calibrate_swaption_vol(body)` / `calibrate_swaption_model(body)` / `sample_vol_surface(body)` | `/calibrate-swaption-vol`, `/calibrate-swaption-model`, `/sample-vol-surfaces` | raw request bodies, validated against the spec and forwarded (see the `vol` examples); uniform result |

Inflation tools require `fixings` (the CPI / YoY history) as an explicit
argument: the engine prices the inflation leg off the fixing at start minus the
observation lag and the curve helpers need the recent history, and this server
has no market-data source; the tool sets them on the index and says so in
`notes`.

`calendar` / `tenor_unit` / `convention` are typed with the engine's own enums.
`calendar_overrides` is the engine 0.7.0 per-request holiday override list
(`[{calendar, added_holidays?, removed_holidays?}]`).

Every tool returns the same shape:

```json
{
  "ok": true,
  "endpoint": "/calendar-holidays",
  "request":  { "...exactly what was sent..." },
  "response": { "...engine body verbatim..." },
  "summary":  { "count": 1, "first": "2024-06-14", "last": "2024-06-14" },
  "engine":   { "api_version": "0.7.0", "request_id": "qmcp-..." }
}
```

On an engine error `ok` is `false` with the HTTP `status` and the engine's
`error` text verbatim (`400` = the request is wrong, `422` = well-formed but
unpriceable); `request` is still echoed so the agent can fix and retry. A
request rejected locally (bad date, schema mismatch) has `status: null` and a
`problems` list of `{path, message}`.

## Resources

| URI | Content |
|---|---|
| `quantra://docs/http-api` | The engine's HTTP contract at the pin (status codes, headers, calendar overrides). |
| `quantra://docs/versioning` | Engine versioning policy and migration notes at the pin. |
| `quantra://schema/{endpoint}` | Full request/response schema, e.g. `quantra://schema/price-ois-swap`. |
| `quantra://enums/{name}` | Enum values, e.g. `quantra://enums/Calendar` (`quantra://enums` lists names). |
| `quantra://docs/engine-catalog` | The engine's functional parity catalog at the pin (every cataloged example with its description and QuantLib reference value). |
| `quantra://examples` | Index of the 223 vendored examples (name, category, endpoint, title, reference value). |
| `quantra://examples/{category}/{name}` | One example with its metadata and `body`, e.g. `quantra://examples/ir_swaps/irs_eur_5y_payer_ois_discounted_multicurve`. |
| `quantra://examples/{name}` | An example body by name (any category), e.g. `quantra://examples/sofr-ois-swap-request`; a category name lists that category. |
| `quantra://pin` | The engine tag, commit and image this server is pinned to. |
| `quantra://presets/{id}` | A market-convention preset as data with per-field provenance (`quantra://presets` lists them). |

## Prompts

| Prompt | Guides the agent through |
|---|---|
| `price-a-swap(currency, kind)` | `quantra_meta` -> `list_presets` / `get_preset` -> `build_curve` -> `session_put` -> `price_vanilla_swap` / `price_ois_swap`, reading `request`, `summary`, `notes` and the 400 / 422 distinction. |
| `bootstrap-from-strip(preset)` | `get_preset` -> `build_curve` -> `build_query` -> `bootstrap_curve`; the grid lives in `response`. |
| `holiday-check` | `calendar_holidays` -> diff with the user's list -> `calendar_overrides` -> verify with `calendar_advance` -> pass to every pricing tool. |
| `explore-examples` | `list_examples` -> `get_example` -> `engine_request`; or reuse an example's `pricing` block as the `market` of a pricing tool. |

## Examples catalog

`src/quantra_mcp/examples/` holds the engine's 221 example requests at the
pinned tag (BSD 3-Clause, license reproduced in `ENGINE-LICENSE.txt`; see the
folder's README for attribution) plus the two SOFR blog examples, indexed in
`INDEX.json` with the endpoint, the catalog description and the QuantLib
reference value of the 192 cataloged cases. The live suite POSTs every one of
them and checks the reference values; the convenience tools are proven by
rebuilding twelve of them JSON-equal from a preset and the example's own
`pricing` block.

## Development

```bash
uv sync
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src   # the gate
```

Live acceptance (needs a real engine; the CI `live` job does the same):

```bash
docker run -d --name quantra-mcp-engine -p 18087:8080 ghcr.io/joseprupi/quantra-server:0.7.0
QUANTRA_ENGINE_URL=http://localhost:18087 uv run pytest tests/live -m live
QUANTRA_ENGINE_URL=http://localhost:18087 uv run python scripts/live_check.py
```

`live_check.py` runs every tool and asserts each tool's `response` is
byte-equal to a direct HTTP replay of its echoed `request`; its M2 section
builds the SOFR strip through `build_curve` and asserts the request is
JSON-equal to the gold example and the 50Y DF matches the oracle, round-trips
a discount value curve, bootstraps every preset (monotone DFs) and resolves a
session reference; its M3 section sweeps all 223 vendored examples through
`engine_request` (status as indexed, reference values matched) and runs every
pricing tool against its mapped fixture (built request JSON-equal, engine
number equal to the oracle). `--sweep-table` prints one row per example.

QuantLib parity (the only place QuantLib is imported; needs a Python with
`QuantLib` and `pydantic`, e.g. the engine's QuantLib 1.41):

```bash
QUANTRA_ENGINE_URL=http://localhost:18087 PYTHONPATH=src python3 scripts/parity_ql.py
# or: QUANTRA_ENGINE_URL=http://localhost:18087 uv run --with QuantLib python scripts/parity_ql.py
```

It rebuilds every preset's canonical strip (`tests/golden/strips.json`) in
QuantLib with the preset's conventions and asserts max |DF diff| <= 1e-10 on
the engine's grid dates.

Golden request files under `tests/golden/` (curves) and
`tests/golden/products/` (one full pricing request per tool) are regenerated
with `QUANTRA_REGEN_GOLDENS=1 uv run pytest tests/unit/test_builders.py
tests/unit/test_pricing_tools.py` after an intentional builder change.

Bump the engine pin (copies the spec, docs, catalog, license and every example
request from an engine checkout at a tag, regenerates the enums and
`examples/INDEX.json`, writes `PIN`):

```bash
uv run python scripts/pin_engine.py --engine-repo /path/to/quantraserver --tag v0.7.0
```

## Layout

```
src/quantra_mcp/
  server.py          MCPServer app, transport selection
  config.py          env -> Settings
  backend/           Backend protocol + the engine HTTP client
  schema/            vendored openapi3.json, PIN, generated enums, loader, validator
  presets/           market-convention presets (JSON data + registry; curve + trade blocks)
  builders/          pure builders: tenor parser, curves, schedule, market, products/<product>
  session.py         in-memory session scratch (LRU, per process)
  tools/             discovery, calendar, raw passthrough, curves, pricing, examples, session
  resources.py       docs / schema / enums / examples / pin / presets
  prompts.py         price-a-swap, bootstrap-from-strip, holiday-check, explore-examples
  examples_catalog.py  INDEX.json access + oracle checks
  docs/, examples/   vendored engine docs, catalog and the 223 example requests
scripts/             pin_engine.py, live_check.py, parity_ql.py
tests/               unit, contract (vendored spec), live (real engine), golden/ requests
```

## License

BSD-3-Clause. See `LICENSE`.
