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

Status: **0.1.1** (discovery, calendars, raw passthrough, market construction,
one pricing tool per product, analytics by composition, the engine's 222
example requests as a catalog, prompts; stdio and a hardened streamable-HTTP
mode). Engine contract pinned to **v0.7.0** (`src/quantra_mcp/schema/PIN`);
any engine **>= 0.7.0** that keeps the contract works (older engines reject
`calendar_overrides` and are warned about at startup).

Public hosted instance, no account: **`https://mcp.quantra.io/mcp`** (prices on
the same engine as `api.quantra.io`; see [Security](#security-and-limits)).

## Run an engine

One line, no account:

```bash
docker run -d --name quantra-engine -p 8080:8080 ghcr.io/joseprupi/quantra-server:0.7.0
curl http://localhost:8080/health   # {"status":"healthy"}
```

Or point the server at the public demo with `QUANTRA_ENGINE_URL=https://api.quantra.io`.

## Install

```bash
uvx quantra-mcp --version        # from PyPI, nothing else to install (needs uv)
pipx install quantra-mcp         # or pip install quantra-mcp
docker run --rm -p 8765:8765 -e QUANTRA_ENGINE_URL=http://host.docker.internal:8080 ghcr.io/joseprupi/quantra-mcp:0.1.1
```

From a checkout: `git clone https://github.com/joseprupi/quantra-mcp && cd quantra-mcp && uv sync && uv run quantra-mcp --help`.

## Configure your agent

### claude.ai (custom connector, nothing to install)

Settings → Connectors → **Add custom connector** → URL:

```
https://mcp.quantra.io/mcp
```

Then ask Claude to call `quantra_meta`, or "price a 5Y EUR payer swap with the
EUR_EURIBOR_6M preset". The hosted instance has no authentication and the
limits described under [Security](#security-and-limits).

### For business users (claude.ai)

You do not need to know any of the tool names below. Connect the server (above),
then talk about the trade: paste a screenshot (a Bloomberg SWPM screen), a term
sheet or a ticket and ask "can we price this, and what market data do you need?".
The assistant answers in trade terms: whether it can be priced, which market
data is missing and how to paste it, the conventions it will assume, then the
engine's price reconciled against the number on your screen.

What to paste when asked:

- **The curve.** Licensed vendor curves (Bloomberg ICVS/SWDF, Refinitiv, Markit)
  are never available on the server side. Paste either the discount factors of the
  Curves tab as `date, DF` rows, the zero rates as `date, zero %`, or the par quotes
  of the strip as `tenor, rate %`. CSV, tab- or space-separated, header optional,
  `%` signs and thousands separators allowed; rows that cannot be read are reported
  back, nothing is silently dropped or recomputed.
- **The volatility.** The normal vol in bp (or lognormal %) at the trade's expiry x
  tenor, or the ATM matrix.
- **The valuation date** and anything the screen implies but does not show
  (payment lag, settlement method).

What you get: the engine's NPV / premium and fair rate, every convention that was
assumed and where it comes from, the dates the engine resolved, the difference to
your screen with its likely causes, and, on request, the complete request that was
sent so anyone can replay it.

When you bring a number from another system (any vendor, any product), the
assistant follows one order: identify the trade, ask for the inputs in paste-able
form, confirm conventions, price, put the two numbers side by side, and then
explain each difference from the engine's own documentation (every statement
cited to the engine source at the pinned version) and TEST each candidate cause by
repricing with that one thing changed (settlement method, interpolation, vol type,
a bumped quote, a rolled date). A cause it can neither cite nor demonstrate is
reported as a possibility, not a conclusion. Ask "how is this computed?" at any
point and you get the cited methodology page for that number.

The assistant will never type, estimate, recall or invent market data for your trade
(quotes, discount factors, zero rates, vols, fixings), not as a placeholder, not as a
test run, not labelled as approximate: until you have pasted or dictated it, it tells
you what is needed, in paste-able form, and stops. The shipped examples are
request-shape references only; the assistant never reuses an example's market data for
your trade, and every curve-building and pricing tool requires it to declare where the
numbers came from (pasted by you, read from your file or screenshot, an example you
explicitly asked to run, or a market stored earlier in the session), with no permitted
value for invented data. Engine fixtures whose name or description refers to a vendor
are not shipped at all.

Project instructions you can paste into a claude.ai Project that has the
connector enabled:

```
You are a desk quant helping a trader price and check derivatives with the Quantra
pricing engine connected to this project. Speak in trade and market terms; never
mention tool names, presets, sessions, request bodies, schemas or MCP unless asked
how it works. When I describe a trade or paste a screenshot or ticket: (1) say
whether it can be priced here; (2) list exactly the market data that is missing and
the simplest form I can paste it in (discount factors as date, DF rows; zero rates
as date, zero %; par quotes as tenor, rate %; vols in bp); (3) list every convention
you will assume and its source and ask me to confirm; (4) build the curve from what
I paste, price, and put the engine's number next to the number on my screen (spot
premium vs spot premium, fair rate vs fair rate) with the difference and its likely
causes; (5) always show the engine's own numbers with their assumptions and offer
the full request only if I ask. Never invent or recall a vendor curve; never adjust
an input to make a number match. If the engine refuses, tell me what to supply.
```

### Claude Code

Local engine (stdio):

```bash
claude mcp add quantra -e QUANTRA_ENGINE_URL=http://localhost:8080 -- uvx quantra-mcp
```

Hosted instance (streamable HTTP):

```bash
claude mcp add --transport http quantra-public https://mcp.quantra.io/mcp
```

Project form (`.mcp.json` in the repo root, shared with the team):

```json
{
  "mcpServers": {
    "quantra": {
      "command": "uvx",
      "args": ["quantra-mcp"],
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
      "command": "uvx",
      "args": ["quantra-mcp"],
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
      "command": "uvx",
      "args": ["quantra-mcp"],
      "env": { "QUANTRA_ENGINE_URL": "http://localhost:8080" }
    }
  }
}
```

Or point Cursor at the hosted instance: `{"mcpServers": {"quantra": {"url": "https://mcp.quantra.io/mcp"}}}`.

### Streamable HTTP (serve it yourself)

```bash
QUANTRA_ENGINE_URL=http://localhost:8080 uvx quantra-mcp --http --port 8765
# MCP endpoint: http://127.0.0.1:8765/mcp   pointer: GET /   health: GET /healthz, GET /readyz
```

### Self-host with Docker Compose

`docker-compose.example.yml` runs the pinned engine and the MCP server
together; only port 8765 is published:

```bash
curl -O https://raw.githubusercontent.com/joseprupi/quantra-mcp/main/docker-compose.example.yml
docker compose -f docker-compose.example.yml up -d
curl http://localhost:8765/readyz      # {"status":"ok","engine":{"status":"healthy"}}
claude mcp add --transport http quantra http://localhost:8765/mcp
```

To expose it publicly put a TLS-terminating reverse proxy in front and set
`QUANTRA_TRUST_PROXY=1`, `QUANTRA_ALLOWED_HOSTS=<your hostname>` and
`QUANTRA_PUBLIC_URL=https://<your hostname>` on the `mcp` service.

## Environment

| Env | Default | Meaning |
|---|---|---|
| `QUANTRA_ENGINE_URL` | `http://localhost:8080` | Engine JSON gateway (self-hosted or `https://api.quantra.io`). |
| `QUANTRA_TIMEOUT_S` | `60` | Per-request engine timeout in seconds. |
| `QUANTRA_MCP_LOG` | `info` | stderr log level (`debug`, `info`, `warning`, `error`). stdout is the MCP channel. |
| `QUANTRA_SESSION_MAX_ITEMS` | `64` | Cap on in-memory session scratch items (least-recently-used eviction). |
| `QUANTRA_SESSION_MAX_TOTAL_BYTES` | `33554432` | Cap on the serialized size of all session items together (32 MiB, LRU). |
| `QUANTRA_MAX_CONCURRENCY` | `4` | Maximum simultaneous engine calls: the analytics fan-outs, and in `--http` mode the number of `tools/call` requests served at once. |
| `QUANTRA_REQUIRE_ENGINE` | `0` | `1`: refuse to start when the engine's `/meta` is unreachable (default: warn and serve). |

`--http` mode only:

| Env | Default | Meaning |
|---|---|---|
| `QUANTRA_MAX_QUEUE` | `8` | `tools/call` requests allowed to wait for a concurrency slot; beyond that the request gets 503 with `Retry-After`. |
| `QUANTRA_RATE_LIMIT_RPM` | `60` | Per-client requests per minute (token bucket; `0` disables). Exceeding it gets 429 with `Retry-After`. |
| `QUANTRA_RATE_LIMIT_BURST` | `20` | Token-bucket burst size. |
| `QUANTRA_MAX_BODY_BYTES` | `2097152` | Request body cap (2 MiB); larger bodies get 413. |
| `QUANTRA_TRUST_PROXY` | `0` | `1`: take the client address from the first `X-Forwarded-For` hop. Set only behind your own reverse proxy. |
| `QUANTRA_ALLOWED_HOSTS` | *(empty)* | Comma-separated `Host` header allow-list for `/mcp` (DNS-rebinding protection; a listed host matches with or without a port). Empty on a loopback bind keeps the SDK's localhost defaults; empty on a public bind disables the check and logs a warning. |
| `QUANTRA_ALLOWED_ORIGINS` | *(empty = any)* | Comma-separated `Origin` allow-list for `/mcp`. Unset accepts any origin (the server has no credentials to protect). |
| `QUANTRA_LOG_RAW_IP` | `0` | `1`: log client addresses verbatim instead of a 12-hex sha256 prefix. |
| `QUANTRA_PUBLIC_URL` | *(empty)* | The externally reachable base URL, advertised by `GET /` (e.g. `https://mcp.quantra.io`). |

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
| `curve_from_pasted_table(text, id, kind=discount\|zero\|par, preset?, reference_date?, conventions?, quote_type?, percent?, date_format?, ...)` | A pasted table (CSV / TSV / whitespace, header optional; date or tenor + value; `%` and thousands separators normalised, nothing else) -> `build_value_curve` (discount / zero) or `build_curve` (par quotes); returns the built curve plus `parsed_rows`, `unparsed` (with reasons) and `notes`. No engine call. |
| `build_query(curve_id, measures, tenors? \| range_grid?, calendar?, business_day_convention?, zero?, fwd?)` | A `CurveQuerySpec`; FWD needs explicit `fwd` options. No engine call. |
| `bootstrap_curve(curves, as_of, queries, indices?, calendar_overrides?, request_id?)` | `POST /bootstrap-curves`. `curves` items: `TermStructure`, `build_curve` result or `{"session": name}`; echoed `request` is the resolved body; `summary` = per curve `{id, pillars, first_grid_date, last_grid_date, measures}`. |
| `bootstrap_inflation_curve(body, request_id?)` | `POST /bootstrap-inflation-curves` with a raw body (validated first). |
| `session_put(name, kind, value)` / `session_get(name)` / `session_list()` / `session_delete(name)` | In-memory scratch (`curve`, `index`, `market`), per process, never persisted, LRU-capped. |
| `list_examples(category?, product?)` / `get_example(name)` | The 222 vendored example requests (engine fixtures + the two blog examples) with endpoint, catalog description and QuantLib reference value; `body` is ready for `engine_request`, `body.pricing` is a valid `market`. No engine call. |

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

## Analytics by composition

`swap_dv01`, `key_rate_ladder`, `scenario` and `fair_rate` reprice a swap
through the same `price_vanilla_swap` / `price_ois_swap` path under bumped
markets and report engine outputs plus their differences, never a locally
computed sensitivity. A bump adds `bp / 10 000` to a pillar's quoted rate or
spread (a futures price moves by `-bp / 100`); discount-factor points, bond
clean prices, FX points and `quote_id` pillars are refused by name. Every
result carries `calls`, the complete uniform pricing result of each reprice
(base, bumped, one per pillar, one per scenario) with its echoed `request`, so
each number can be replayed with curl and each difference redone by hand.
`spot` / tenor dates are resolved by the engine once in the base call and
pinned for the reprices, so bumped requests differ from the base only in the
quotes that moved. Reprices fan out concurrently, bounded by
`QUANTRA_MAX_CONCURRENCY` (default 4).

`trade` is the swap to reprice: `product` (`vanilla_swap` | `ois_swap`),
`preset`, `discounting_curve`, `forwarding_curve` and the pricing tool's own
economics (`swap_type`, `notional`, `fixed_rate`, `effective_date`, `tenor` |
`termination_date`, `spread`, `index_id`, overrides, OIS overnight parameters).

| Tool | Does |
|---|---|
| `swap_dv01(market, trade, bump_bp=1.0, scope=all\|discounting\|forwarding)` | Reprices with every pillar of the selected curve(s) bumped. `base_npv`, `bumped_npv`, `dv01 = bumped_npv - base_npv`, `bumped_quotes` (from/to per pillar), `calls` = the two pricing results. |
| `key_rate_ladder(market, trade, bump_bp=1.0, curve?)` | Buckets = the curve's actual pillars in wire order (default: the discounting curve). `ladder` = `[{pillar, quote_from, quote_to, bumped_npv, dv01}]`, `parallel` (all pillars together), `sum_of_buckets`; `calls` = base + parallel + one per pillar. |
| `scenario(market, trade, scenarios)` | `[{name, bumps: [{curve, bp, pillar?}], replace_quotes: [{curve, pillar, value}]}]`; `pillar` is a label (`5Y`, `3x6`, `FUT 2025-03-19`, a value-point date) or a 0-based index, omitted = the whole curve. `table` = `[{name, npv, change = npv - base_npv, edits}]` starting with `base`. |
| `fair_rate(market, trade)` | The engine's `fair_rate` / `fair_spread` from the pricing response; `provided_by_engine` false + a message when the product's response has neither (nothing is solved locally). |

Live on the engine fixture `irs_eur_5y_payer_ois_discounted_multicurve` (10m
5Y EUR payer, OIS-discounted): `swap_dv01` with both curves bumped +1bp, the
`EUR_6M_CURVE` ladder summing to its parallel bump within 2%, a receiver
ladder negative and concentrated at the 5Y pillar, and a zero-bump scenario
reproducing the base NPV (`tests/live/test_live_analytics.py`).

## Reconciliation and methodology

| Tool | What it does |
|---|---|
| `explain_method(topic)` | How the engine computes something, from its own docs and source at the pin: `npv`, `fair-rate`, `greeks-bump-and-reprice`, `theta`, `curve-bootstrap`, `value-curves`, `settlement-and-cash-settlement`, `volatility-types`, `calendars-and-overrides`, `day-counters-and-compounding`, `error-codes`. Every statement is a verbatim excerpt with a `path@tag:Lstart-Lend` citation; each page ends with what the engine does not document. Generated by `scripts/pin_engine.py`, never hand-written. No engine call. |
| `compare_results(external, quantra)` | The user's `{label: number}` next to the first priced item of a result: `abs_diff = quantra - external`, `rel_diff = abs_diff / \|external\|`, label mapping shown (premium / PV -> `npv`, PV01 -> `dv01`, fair rate -> `fair_rate` then `atm_forward`, ...), unmapped labels with the reason, and per metric the methodology topic to consult. Pure presentation, no engine call. |
| `reprice_with(result_or_request, changes, reprice_base=False, validate=True)` | Edit fields of a previous result's echoed request (or an explicit `{endpoint, body}`) by path (`[{path, value}]` or `[{path, bump_bp}]`, e.g. `swaptions[0].swaption.settlement_method`, `pricing.rates.curves[0].interpolator`, `pricing.as_of_date`), validate, reprice; returns `changes_applied`, `request_diff` (every differing leaf), both complete results and the numeric `differences` of the first item (`changed - base`). The generic "test the hypothesis" primitive. |

## Resources

| URI | Content |
|---|---|
| `quantra://docs/http-api` | The engine's HTTP contract at the pin (status codes, headers, calendar overrides). |
| `quantra://docs/versioning` | Engine versioning policy and migration notes at the pin. |
| `quantra://schema/{endpoint}` | Full request/response schema, e.g. `quantra://schema/price-ois-swap`. |
| `quantra://enums/{name}` | Enum values, e.g. `quantra://enums/Calendar` (`quantra://enums` lists names). |
| `quantra://docs/engine-catalog` | The engine's functional parity catalog at the pin (every cataloged example with its description and QuantLib reference value). |
| `quantra://examples` | Index of the 222 vendored examples (name, category, endpoint, title, reference value). |
| `quantra://examples/{category}/{name}` | One example with its metadata and `body`, e.g. `quantra://examples/ir_swaps/irs_eur_5y_payer_ois_discounted_multicurve`. |
| `quantra://examples/{name}` | An example body by name (any category), e.g. `quantra://examples/sofr-ois-swap-request`; a category name lists that category. |
| `quantra://pin` | The engine tag, commit and image this server is pinned to. |
| `quantra://presets/{id}` | A market-convention preset as data with per-field provenance (`quantra://presets` lists them). |
| `quantra://methodology` | Index of the methodology pages (topic, title, summary, citation count) and the metric -> topic map. |
| `quantra://methodology/{topic}` | One methodology page as markdown, e.g. `quantra://methodology/theta`: summary, the engine's cited excerpts, the request fields that control it, what is not documented. |

## Prompts

| Prompt | Guides the agent through |
|---|---|
| `price-a-swap(currency, kind)` | `quantra_meta` -> `list_presets` / `get_preset` -> `build_curve` -> `session_put` -> `price_vanilla_swap` / `price_ois_swap`, reading `request`, `summary`, `notes` and the 400 / 422 distinction. |
| `bootstrap-from-strip(preset)` | `get_preset` -> `build_curve` -> `build_query` -> `bootstrap_curve`; the grid lives in `response`. |
| `holiday-check` | `calendar_holidays` -> diff with the user's list -> `calendar_overrides` -> verify with `calendar_advance` -> pass to every pricing tool. |
| `price-from-screen(product?)` | Screenshot / ticket (any vendor) -> can it be priced -> missing market data in paste-able form -> confirm conventions -> `curve_from_pasted_table` -> `price_*` -> reconcile against the screen (spot premium), business voice. |
| `explore-examples` | `list_examples` -> `get_example` -> `engine_request`; or reuse an example's `pricing` block as the `market` of a pricing tool. |
| `reconcile-external-price` | A number from another system (any vendor, any product): identify the instrument -> inputs in paste-able form -> confirm conventions -> `curve_from_pasted_table` -> `price_*` -> `compare_results` -> `explain_method` per differing metric -> TEST each candidate cause with `reprice_with` -> report demonstrated vs. unexplained. |

## Examples catalog

`src/quantra_mcp/examples/` holds the engine's 221 example requests at the
pinned tag (BSD 3-Clause, license reproduced in `ENGINE-LICENSE.txt`; see the
folder's README for attribution) plus the two SOFR blog examples, indexed in
`INDEX.json` with the endpoint, the catalog description and the QuantLib
reference value of the 192 cataloged cases. The live suite POSTs every one of
them and checks the reference values; the convenience tools are proven by
rebuilding twelve of them JSON-equal from a preset and the example's own
`pricing` block.

## Security and limits

- **No authentication.** `--http` mode and the hosted `mcp.quantra.io` are
  open by design: the engine is stateless, prices public-domain-style
  requests and stores nothing. Do not front a private engine with it without
  a proxy that authenticates.
- **Limits** (all configurable, see the table above): 60 requests/minute per
  client with a burst of 20 (429 + `Retry-After`), 4 concurrent `tools/call`
  plus 8 queued (503 + `Retry-After`), 2 MiB request bodies (413), 60 s per
  engine call. The hosted instance runs these defaults.
- **Host validation.** With `QUANTRA_ALLOWED_HOSTS` set, `/mcp` rejects any
  other `Host` header with 421 (the SDK's DNS-rebinding protection); `Origin`
  is unrestricted unless `QUANTRA_ALLOWED_ORIGINS` is set. `GET /`, `/healthz`
  and `/readyz` are not host-checked and answer with CORS `*`.
- **Session scratch is per process and shared** by every client of an HTTP
  server (bounded by item count and total bytes, LRU). Do not store anything
  you would not show to another user of the same server; the hosted instance
  is public.
- **What is logged.** One stderr line per tool call: timestamp, client address
  as a sha256 prefix (raw only with `QUANTRA_LOG_RAW_IP=1`), tool name, engine
  endpoint, status, duration. Never tool arguments, requests or responses. The
  startup line records the active limits. Nothing is persisted.
- **Numbers come from the engine.** Every result carries the exact request
  sent and the engine's body verbatim; replay it with `curl` against the same
  engine and you get the same bytes. Supported engines: `>= 0.7.0`.

## Development

```bash
uv sync
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src   # the gate
```

Container (what the release workflow publishes):

```bash
docker build -t quantra-mcp:dev .
docker run --rm -p 8765:8765 --add-host=host.docker.internal:host-gateway \
  -e QUANTRA_ENGINE_URL=http://host.docker.internal:18087 quantra-mcp:dev
curl http://localhost:8765/readyz
```

Releases: tag `v<version>` on `main`; `.github/workflows/release.yml` publishes
the image, the PyPI package and the GitHub Release. See `docs/RELEASING.md`.

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
session reference; its M3 section sweeps all 222 vendored examples through
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
  server.py          MCPServer app, transport selection, startup engine check
  hosted.py          --http hardening: guard middleware (429/413/503), / /healthz /readyz, host validation, access log
  config.py          env -> Settings
  backend/           Backend protocol + the engine HTTP client
  schema/            vendored openapi3.json, PIN, generated enums, loader, validator
  presets/           market-convention presets (JSON data + registry; curve + trade blocks)
  builders/          pure builders: tenor parser, curves, schedule, market, bumps, products/<product>
  session.py         in-memory session scratch (LRU, per process)
  tools/             discovery, calendar, raw passthrough, curves, pricing, analytics, examples, session,
                     explain (methodology), reconcile (compare_results, reprice_with)
  resources.py       docs / schema / enums / examples / pin / presets / methodology
  methodology.py     loader for the generated methodology pages (metric -> topic map)
  prompts.py         price-a-swap, bootstrap-from-strip, holiday-check, explore-examples,
                     price-from-screen, reconcile-external-price
  examples_catalog.py  INDEX.json access + oracle checks
  docs/, examples/   vendored engine docs, catalog and the 222 example requests;
                     docs/methodology/ = the generated cited pages + INDEX.json
scripts/             pin_engine.py (+ methodology_gen.py, methodology_rules.py), live_check.py, parity_ql.py
tests/               unit, contract (vendored spec), live (real engine), golden/ requests
Dockerfile, docker-compose.example.yml, docs/RELEASING.md, .github/workflows/{ci,release}.yml
```

## License

BSD-3-Clause. See `LICENSE`.
