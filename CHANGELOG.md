# Changelog

## 0.1.13 (2026-10-22)

Sourced market data (operator decision 2026-10-22).

- **`market_data_source=sourced`**: numbers obtained in the conversation from a trusted
  public publisher (central bank, treasury, FRED, exchange, index administrator,
  regulator filing, fund sponsor; any other site only after asking the user) with a web
  or code tool, or from a market-data connector the user connected. Recalled, estimated
  or invented numbers stay without a permitted value.
- **`sources`** (optional on every tool that takes `market_data_source`, 24 tools):
  `[{what, value, as_of, url | connector, derived?}]`; REQUIRED and non-empty with
  `sourced`; every item needs `what`, a YYYY-MM-DD `as_of` and an http(s) `url` or a
  `connector`. A bad or missing citation is refused before anything is sent (`ok: false`,
  MCP tool error, one problem per field). Every result carries
  `provenance: {market_data_source, meaning, sources}`; `session_put` stores the
  citations with the item (and takes a builder result's), session references note them,
  `reprice_with` carries those of the result it reprices. One shared helper
  (`tools/_market_source.py`: `check_sources`, `source_refusal`, `stamp`).
- **ABSOLUTE RULE rewritten** (still first, still absolute on memory / estimates /
  invention): data only when pasted / attached, fetched from a trusted publisher (other
  sites after asking) or from a connected data tool; every obtained value tabled with
  URL or connector and as-of date; derived values computed with a code tool, method
  shown; proxies labelled with their bias; no fetch tool or connector -> ask for a paste.
  Example-shape rule and guidance shortened to keep INSTRUCTIONS under 2,000 characters.
  Prompt, developers guide, README and docs/clients.md / docs/tools.md follow.

## 0.1.12 (2026-10-22)

Engine pin v0.11.0 -> **v0.12.0** (`5b1c016`), QuantLib stays 1.44 (M5.10): structured notes.

- **Pin.** Spec, docs, catalog and examples re-vendored by `pin_engine.py`: 27 POST
  endpoints (+`/price-structured-note`), the additive `pricing.correlations` block and
  4 new enums; 247 examples (+8 structured notes: autocallables, a reverse convertible,
  greeks and diagnostics requests). `ROUTE_BY_PRODUCT` maps `structured_note`; the
  structured-note catalog cell (npv, standard_error, three leg `pv`s, p_barrier_breach,
  expected_life_years, memory_carried) is mapped to the response paths it names
  (`legs.<bucket>.pv`, `seasoning.memory_carried`); `oracle_check` reads dotted paths.
  `schema/SOURCES.lock` refreshed (engine 363 files at v0.12.0).
- **`price_structured_note`** (tool 42): thin; the notes in the engine's `StructuredNote`
  shape are sent as given (no preset, no defaults of this server), optional `greeks` /
  `diagnostics`, REQUIRED `market_data_source`; the full request is echoed; `summary.notes[]`
  selects npv, npv_pct_of_notional, standard_error, paths, seed, status, legs,
  p_barrier_breach, expected_life_years, event_probabilities, greeks and the terms /
  seasoning / model echoes; engine errors verbatim. The `market` object form accepts
  `correlation_matrices` (-> `pricing.correlations.matrices`) and `fx_spots`
  (-> `pricing.fx.spots`). `engine_request` accepts `/price-structured-note`.
- **Methodology.** Pages regenerated at v0.12.0 (14 topics); new `structured-notes` page
  (18 citations: endpoint scope, `StructuredNote` fields, response, pricing conventions,
  the payoff walk, funding-spread discounting, `QuantoTermStructure`, Sobol / bridge /
  pseudo-random generators, antithetic pairs, seasoning, greeks, rejected requests, known
  limits). The connector page's hand-cited engine lines re-pointed (versioning.md L130 ->
  L245).
- **Instructions.** One line in the guidance names Monte Carlo structured notes; the
  absolute market-data rule is unchanged and first; total stays under 2,000 chars.
- Live on engine 0.12.0: every structured-note fixture prices via `engine_request` and via
  `price_structured_note`, both JSON-equal to a direct replay (timing diagnostics
  excluded); A/B of the 239 earlier examples 0.11.0 vs 0.12.0: all JSON-identical.

## 0.1.11 (2026-10-14)

Curve tables pasted from a terminal screen are read as given (no retyping).

- **Tenor labels.** One shared parser (`builders/tenor.py`, used by every tool that reads
  a tenor) accepts optional whitespace between number and unit and a closed,
  case-insensitive list of unit spellings: D/DY/DAY/DAYS, W/WK/WKS/WEEK/WEEKS,
  M/MO/MOS/MTH/MTHS/MONTH/MONTHS, Y/YR/YRS/YEAR/YEARS (`1 WK`, `18 MO`, `10 YR`). Nothing
  outside the list is guessed (`MO` is months). `ON` / `O/N` / `TN` / `T/N` / `SN` / `S/N`
  are reported with the reason "overnight/tomorrow-next tenors are not supported as curve
  pillars".
- **Extra columns.** `curve_from_pasted_table` reads tables with several value columns
  (e.g. Term, Market Rate, Shift, Shifted Rate, Zero Rate, Discount; tab, `;`, `|`, comma,
  aligned or single-space headers): the header column matching the kind (par: par / market
  rate / ... / rate; zero: zero / spot / yield / rate; discount: discount / df) is taken
  and named in `notes`. Several matching columns, or several numeric columns with no
  named one, refuse the table with the columns listed; nothing is picked silently.
- **Structured rows.** New optional `rows: [{tenor | date, value, type?}]`, exclusive with
  `text` (exactly one), same validation, notes and digest.
- Tool description: paste the table as the user gave it; never retype or reformat.

## 0.1.10 (2026-10-14)

Engine pin v0.8.2 -> **v0.11.0** (`e2c160d`), QuantLib 1.41 -> **1.44** (M5.9b).

- **Pin.** Spec, docs, catalog and examples re-vendored by `pin_engine.py`: 26 POST
  endpoints (+`/price-fx-forward`, `/price-cross-currency-swap`, reachable through
  `engine_request`); 239 examples (+6 FX forward, +6 cross-currency swap, +4 FX
  collateral curves; the vendor-named fixture stays excluded), 210 oracles. `QUANTLIB_PIN`
  1.44; `SOURCES.lock` re-recorded for engine `v0.11.0` (340 files) and QuantLib `v1.44`
  (2,443 files). The catalog's per-leg values (`npv=` repeated per leg from 0.10.0) are
  parsed as `legs[i].*` oracles; `fields` oracles read the first priced item.
- **Swap leg model (engine 0.10.0, wire-breaking upstream).** `price_vanilla_swap`,
  `price_ois_swap`, the `price_swaption` underlying and every analytic composed on them
  (`swap_dv01`, `key_rate_ladder`, `scenario`, `fair_rate`) emit `SwapLeg` legs
  (`pay_receive`, `currency` from the index, typed `coupons`, curves on the legs; no
  `swap_type`, no request-level curve ids). Tool arguments are unchanged. Oracle: every
  pre-0.10.0 request golden and 20 preset x side x override builds, run through the
  engine's `tools/migrate_swap_requests`, are JSON-equal to the new builder output
  (25/25). The vendored SOFR blog pricing example is stored converted by the same tool.
- **Responses.** Swap summaries carry `leg_npvs` (`legs[i].npv`); `fair_rate` /
  `fair_spread` only when returned. `compare_results`: leg labels map to `legs[i].npv`;
  PV01 / BPV / annuity map to a swaption's `annuity` and, on a swap, are listed unmapped
  with the reason (engine 0.10.0 removed every `*_bps` field) and a pointer to
  `swap_dv01`. `reprice_with` differences include `legs[i].npv` / `npv_reporting`.
- **Methodology** regenerated at v0.11.0 (13 pages, 139 citations): the swap NPV and
  fair-rate statements re-pointed to the `SwapLeg` evaluators and `swap_leg_pricer.cpp`;
  the PV01 note now cites the 0.10.0 removal and the internal annuity. Preset provenance
  re-pointed to QuantLib v1.44 headers (cited definitions verified unchanged since 1.41).
- **A/B 0.8.2 vs 0.11.0 over all examples**: 173 identical; 12 CMS swaps whose
  `fair_rate` / `fair_spread` went 0.0 -> computed (versioning.md 0.10.0 "Fixed"); 38
  moved as the QuantLib upgrade page states (2.2 CDS 10, 2.4 inflation 17, 2.5 Hull-White
  joint calibration 3, 2.7 bond yield/duration/convexity 6, 2.9 noise 2); 16 new
  (no 0.8.2 baseline); leg fields mapped as 0.10.0 shape (`*_leg_npv` -> `legs[i].npv`,
  `*_bps` removed). No other number moved.

## 0.1.9 (2026-10-13)

Source access (M5.9a): the assistant can READ the engine source at the pinned tag and
the QuantLib source the engine is built on, and cite them, instead of answering "from
general knowledge" where the methodology pages stop.

- **Three read-only tools** (41 tools now). `search_source(query, repo=engine|quantlib|both,
  regex?, path_prefix?, max_results, case_sensitive?)`: hits sorted by path then line with
  one line of context each side, a `citation` (`<repo>:<path>@<tag>:L<n>-L<n>`) and a
  GitHub `permalink`; case-insensitive by default, regex matched per line (pattern cap 256
  characters), queries under 3 characters refused, a scan byte budget (96 MiB) and a time
  guard (10 s) reported as `truncated` / `truncated_reason`. `read_source(repo, path,
  start_line, end_line?, max_lines<=400)`: the lines verbatim with `total_lines`, `ref`,
  the citation and the permalink (`#La-Lb`); traversal (`..`, absolute paths, symlinks)
  and binary files refused. `list_source(repo, path_prefix, max_entries)`: directories
  (with file counts) then files (with sizes) plus the GitHub `tree_url`. Pure Python, no
  shelling out, no engine call.
- **Vendored trees.** `scripts/fetch_sources.py` downloads the two GitHub tarballs at the
  pinned refs (engine `v0.8.2`; QuantLib `v1.41`, the `ARG QUANTLIB_VERSION` of the
  engine's Dockerfile at that tag, recorded in `schema/QUANTLIB_PIN` by `pin_engine.py`
  and checked live against `/meta` `dependencies.quantlib`), verifies their sha256 against
  `schema/SOURCES.lock` (recorded on the first fetch; a later fetch of the same tag must
  match) and extracts only text files of the allowed trees (engine: `src/`, `jsonserver/`,
  `flatbuffers/*.fbs`, `docs/`, the functional CATALOG, LICENSE, README; QuantLib: `ql/`,
  LICENSE, README) into `vendor/sources/` (gitignored): 309 + 2,380 files, 29.4 MB. The
  container image fetches them in its build stage (network at build time only) and ships
  them under `/app/vendor/sources` (`QUANTRA_SOURCES_DIR`). Without the trees the three
  tools return an error naming the script.
- **Behaviour.** A third rule in the server instructions (after the two market-data rules,
  the whole text still under 2,000 characters): explain_method first; where its page lacks
  the point, or the engine instantiates a QuantLib class, search and read that class in
  the engine and in QuantLib at the pin and cite `path@tag:lines`; "from memory", "general
  knowledge" and "not verified here" are banned while the file is readable; the only
  negative is "not found in the engine or QuantLib source at this pin" after a search
  returned nothing. The same sentence is in `explain_method`'s description, and every
  engine methodology page closes its "Not documented" section with the pointer to the
  readable source (pages regenerated at v0.8.2, citations unchanged). `quantra://pin`
  reports the QuantLib version, tag and the fetched trees; `quantra://guide/developers`
  documents the tools, the refs and both BSD-3-Clause licences.
- **Tests.** Hermetic fixture trees under `tests/fixtures/sources/` through the real tool
  code; live-ish tests over the fetched trees (skipped when absent): `PiecewiseYieldCurve`
  in the engine finds `term_structure_parser.cpp` at a line inside an existing methodology
  citation, `class PiecewiseYieldCurve` in QuantLib finds `piecewiseyieldcurve.hpp` whose
  first 60 lines carry the class doc, `IterativeBootstrap` finds `iterativebootstrap.hpp`
  and `read_source` on the `calculate()` definition returns the bootstrap loop with its
  permalink; `tests/live` asserts `/meta` `dependencies.quantlib` == `QUANTLIB_PIN`;
  `live_check.py` runs the three tools and the fetch check.
- Deviation from the brief: QuantLib's git tag is `v1.41` (`QuantLib-v1.41` is the name of
  the release tarball, not a tag; GitHub returns 404 for it), so the pin, the lock, the
  citations and the permalinks use `v1.41`.

## 0.1.8 (2026-10-12)

Engine pin bump v0.7.0 -> v0.8.2 (M5.8). Requests are unchanged; what the engine returns is.

- **Pin.** `scripts/pin_engine.py --tag v0.8.2` (`7b26e53a`): spec 0.8.2 (0 paths / schemas
  added; only the `PricingOptions` and `SwaptionResponse` descriptions changed), enums
  regenerated, engine docs and catalog revendored, one new fixture
  (`swaption/swpt_eur_1y5y_payer_near_atm_physical_quote_ids`, 223 examples; the
  vendor-named exclusion stands). CATALOG reference values unchanged, oracles 194/194.
- **Goldens.** The request goldens are byte-identical (builders untouched). Every golden
  request and every vendored example was replayed on 0.7.0 and 0.8.2 side by side and each
  differing value falls in one of four classes: (1) a scalar at its schema default now
  present (numeric zeros such as a zero-coupon bond's `accrued_amount`, and enum-valued
  scalars at member 0 such as `measure: "DF"`, `used_settlement: "Physical"`,
  `vol_kind: "Constant"`); (2) `swaption_pricing_details` greeks rescaled to bp units
  (delta and vega / 1e4, gamma / 1e8, theta recomputed per calendar day); (3) rebump
  `theta` now present and changed (the roll moves every explicit `reference_date`);
  (4) rebump `dv01` / `gamma` on `quote_id` curves halved / quartered (0.8.2 bumps each
  pillar once). Plain NPVs, fair rates, DFs, flows and bond prices are byte-identical. The
  `used_atm_forward` / `used_cube_node_atm` `-1.0` sentinels of surfaces without an ATM
  level are now absent (optional fields, 0.8.0).
- **`price_swaption(include_rebump)`.** The tool can now request
  `swaption_pricing_rebump`; `summary.swaptions` carries dv01 / delta / gamma / vega /
  theta exactly when the engine computed them (an absent greek was not computed, `0.0` is
  a computed zero). Live: a swaption on the engine's zero-rate value curve returns a
  non-zero rebump theta (absent on 0.7.0).
- **Methodology at v0.8.2.** All 137 citations re-resolved by anchor text; 7 rules
  re-pointed after the engine's clang-format and the greek rewrite (the `PricingOptions`
  comments, the analytic-greek block and the analytic theta line of
  `swaption_evaluator.cpp`, the `SwaptionResponse` greek fields, the `SwapRateHelper`
  construction end line); the hand-pinned PV01 lines moved
  (`vanilla_swap_evaluator.cpp` L243 -> L237, `ois_swap_evaluator.cpp` L126 -> L116,
  `vanilla_swap_response.fbs` L41 unchanged). `greeks-bump-and-reprice` and `theta`
  rewritten to the 0.8.0 semantics citing `docs/http-api.md@v0.8.2` (units, optional
  fields, precedence, the whole-market roll, 0.8.2 single bump); the claim that units are
  undocumented and that theta can be absent on value curves is gone.
- README, docs, CI workflow and compose example point at `quantra-server:0.8.2`.

## 0.1.7 (2026-10-12)

Quant review fixes on the connector side (M5.7).

- **T1 Exogenous discounting on `build_curve`.** New optional `discount_curve`: the id
  of another curve in the same request; every SwapHelper / OISHelper point then carries
  `deps.discount_curve` (deposits, FRAs, futures untouched), which is what a dual-curve
  bootstrap of an IBOR strip under OIS discounting needs. Omitted = self-discounted, and
  the built request is byte-identical to 0.1.6 (goldens unchanged). Verified live on
  engine 0.7.0 with the multicurve fixture's quotes: the 6M curve's 5Y DF moves from
  0.858309659352 (self) to 0.858196982005 (OIS-discounted), 0.26bp in zero terms, and
  the two curves may be listed in either order. The Euribor and SONIA swap presets say so.
- **T2 `compare_results` PV01.** PV01 / BPV / annuity map to the engine's `fixed_leg_bps`
  (the fixed leg's BPS), no longer to the connector's bump-and-reprice `dv01`; `shifting`
  and `deltashift` map to `dv01`. `quantra://methodology/connector-analytics` gains a
  "PV01 vs DV01" note citing the engine lines that fill `fixed_leg_bps`;
  `scripts/methodology_gen.py --check` fails when the page is stale.
- **T3 Honest labels.** `EUR_CDS` no longer claims SNAC conventions (it is the engine
  fixture's: TwentiethIMM, MidPoint; not the ISDA standard model); `EUR_FIXED_BOND` says
  it is the fixture's 30/360 annual bond, not a Bund; `EUR_HICP` flags Linear CPI
  interpolation as the fixture's choice. Values unchanged.
- **T4 Sovereign bond presets.** `DE_BUND`, `US_TREASURY`, `UK_GILT` (`fixed_rate_bond`
  blocks: Act/Act ICMA, Unadjusted, Backward, redemption 100, yield Compounded at the
  coupon frequency; T+2 Germany / T+1 UnitedStatesGovernmentBond / T+1 UnitedKingdom) with
  a public source on every field (Deutsche Finanzagentur, 31 CFR 356 Appendix B, UK DMO
  gilt formulae) and the QuantLib 1.41 `ActualActual::ISMA` header for the day counter.
- README links the agents page (`/docs/app/agents`).

## 0.1.6 (2026-10-12)

Size, protocol, schemas and provenance, from the external review (M5.6).

- **Compact analytics results.** `swap_dv01`, `key_rate_ladder` and `scenario` take
  `include_calls` (default false): the result carries the base call's echoed request
  once (`base_request`) and, per reprice, its label, engine NPV, the quotes moved and a
  `request_diff` against the base (the `reprice_with` diff format); `include_calls=true`
  returns `calls`, every complete pricing result, as before. The 5Y EUR fixture's
  key-rate ladder is under 25 KB compact. `list_examples` without a filter answers
  counts per category and product plus the first 20 names (under 12 KB); a category,
  product or the new `limit` returns rows.
- **Titles and annotations.** Every tool has a human title and MCP `ToolAnnotations`
  (`readOnlyHint` true except `session_put` / `session`, `destructiveHint` false,
  `idempotentHint` true, `openWorldHint` true for the engine callers).
- **38 tools (was 47).** `calibrate_swaption_vol`, `calibrate_swaption_model`,
  `sample_vol_surface` and `bootstrap_inflation_curve` are `engine_request` with the
  engine's own body (same validation, same selected `summary`; the docstring names the
  example families). `session_get` / `session_list` / `session_delete` are
  `session(action, name)`. `list_endpoints` is `engine_schema()` without an argument;
  `list_presets` / `get_preset` are `preset(id?)`. `quantra_health` is gone:
  `quantra_meta` proves liveness and the server's `/readyz` serves operators.
- **Schemas.** Every argument carries a description (`market_data_source` as one line
  pointing at `quantra://guide/developers`, which now states the rule and the meaning
  of each value); ISO dates carry `^\d{4}-\d{2}-\d{2}$` (with `spot` / `as_of`
  where the tool accepts them), tenors `^\d+[DWMY]$`; `underlying_type`, the equity
  `exercise`, the swaption / cap-floor / CDS `model` and the vol `type` are enums
  (a plain string stays a market id where ids are accepted); pydantic's automatic
  `title` keys are stripped; docstrings are flush-left and under 1,500 characters;
  nested model fields are described too. A test pins all of it.
- **Provenance is public.** The six curve presets cite the QuantLib index class at
  the engine's QuantLib version (`QuantLib::Sofr`, `Estr`, `Sonia`, `Euribor6M`,
  `Euribor3M` in `ql/indexes/ibor/*.hpp` at v1.41, with GitHub permalinks to the
  header and the constructor that fixes the fixing days, calendar and day counter)
  instead of the private platform repository; helper conventions that come from
  nowhere in-repo say so. A test refuses any private repository or filesystem path.
- **Builder digests.** `build_curve` / `build_value_curve` / `curve_from_pasted_table`
  results carry `digest` (sha256 of the canonical `{curve, indices}` JSON) and
  `curve_digest`; a session item carries its own. Pricing and analytics tools report
  per market curve (`market_digests`, `notes`) whether it came out of a builder in
  this session or was hand-typed ("market block not produced by a builder in this
  session"). Nothing is added to what the engine receives.
- `quantra_meta`: `engine.api_version` falls back to the body's `openapi_version`
  when the engine does not send the header. README and docs agree that Claude
  Desktop uses the custom connector (same as claude.ai).

## 0.1.5 (2026-10-12)

Hosted-mode fixes from an external review of the public server (M5.5).

- **Session scratch is per MCP session.** `session_put` / `session_get` /
  `session_list` / `session_delete` and `{"session": name}` references now resolve to
  a store keyed on the transport's session id (the `Mcp-Session-Id` the streamable-HTTP
  transport mints at `initialize`; stdio and in-memory clients share one implicit
  session). One client's `session_put("sofr")` no longer overwrites another's, and a
  miss lists only the caller's own names. Bounds: `QUANTRA_SESSION_MAX_ITEMS` per
  session (unchanged), new `QUANTRA_SESSION_MAX_SESSIONS` (default 256, whole-session
  LRU), `QUANTRA_SESSION_MAX_TOTAL_BYTES` now a budget across every session. A
  protocol-2026-07-28 client (single-exchange streamable HTTP, no session id, no
  identity) gets a tool error from `session_*` and `{"session": name}` telling it to
  pass the built objects inline, instead of a store shared with strangers.
- **Rate limiting per MCP session, not per IP.** The token bucket is keyed on the
  session id when present (claude.ai connector traffic reaches the server from a few
  shared egress addresses), on the client address before `initialize`; only
  `tools/call` and `resources/read` spend tokens (the handshake and the listings are
  free). A separate, higher per-IP ceiling (`QUANTRA_RATE_LIMIT_IP_RPM`, default 600)
  stays as abuse protection. New `QUANTRA_MAX_CONCURRENCY_PER_SESSION` (default 2)
  caps the tool calls one session may hold or queue, so one client cannot take every
  global slot.
- **Failures are tool errors.** Every failed tool result (`ok: false`: an engine
  error, a local validation rejection, a provenance refusal) is returned with the MCP
  `isError` flag and the same JSON body as content and `structuredContent`
  (middleware `FailuresAreErrors`). A `tools/call` refused by the hosted layer (rate
  limit, per-session cap, concurrency gate) is answered as a JSON-RPC tool error with
  `status` 429 / 503, `retry_after_s` and a retry hint instead of a bare HTTP error;
  plain routes and resource reads keep the HTTP status and `Retry-After`.
- **Instructions reordered and trimmed** (7,628 -> under 2,000 characters): the
  ABSOLUTE RULE on market data first, the example-shape rule second, the business-user
  guidance condensed; the developer technical map moved to the resource
  `quantra://guide/developers`. Both rules live in `rules.py` and are quoted verbatim
  by the instructions and every prompt's VOICE block.
- Access log: a flagged failure keeps its engine status (`engine_422`, `local_error`).

## 0.1.4 (2026-10-07)

Connector self-documentation, centered DV01, front-stub default (M5.4). Driven by two
live reconciliations against vendor swap screens: the server works; these are the gaps
they exposed.

- New methodology topic `connector-analytics` (`quantra://methodology/connector-analytics`,
  `explain_method("connector-analytics")`): what `swap_dv01`, `key_rate_ladder`,
  `scenario`, `fair_rate` and `reprice_with` compute on top of engine outputs, GENERATED
  from this repository's own source (`builders/bumps.py`, `tools/analytics.py`,
  `tools/reconcile.py`) with the same anchor / excerpt mechanism as the engine pages:
  `path@<short sha>:Lstart-Lend` citations plus
  `https://github.com/joseprupi/quantra-mcp/blob/<sha>/<path>#L..` permalinks, 20
  citations. The page opens with a ledger of which result fields are engine outputs and
  which are differences computed here. `scripts/methodology_gen.py --connector-only`
  regenerates it after a source commit; a test asserts every cited range exists in the
  working tree with a byte-identical excerpt.
- New engine methodology topic `schedules-and-stubs` (12 citations): the `Schedule`
  table and its stub anchors, the date-generation and business-day enums, the
  schedule parser (required fields, stub validation, the QuantLib `Schedule`
  constructor call), the rule mapping, the 0.7.0 stub note and the catalog's stub
  examples; Forward vs Backward stub placement, `termination_date_convention` and
  `end_of_month` behaviour stated as "not documented in engine prose, QuantLib semantics".
- `curve-bootstrap` now excerpts the engine's C++ (14 citations): the required
  trait / interpolator / day-counter guards, the helper-to-`buildCurve` dispatch, the
  `PiecewiseYieldCurve<Discount | ZeroYield | ForwardRate, LogLinear>` instantiation
  (tolerance 1e-15), the `LogCubic` / fail-closed branch, and the `OISRateHelper` and
  `SwapRateHelper` constructions field by field.
- Every methodology citation now carries a GitHub permalink right after its `Source:`
  line (`https://github.com/joseprupi/quantraserver/blob/v0.7.0/<path>#Lstart-Lend`);
  `INDEX.json`, the `quantra://methodology` index (first entry) and `explain_method`
  (`repos`, `links`, `repository`) carry the two repository URLs. 128 citations over 13
  topics (28 engine files at `v0.7.0`, 3 connector files).
- `swap_dv01` / `key_rate_ladder`: new `method` = `centered` (default) | `up` | `down`.
  Centered = `(NPV(+bp) - NPV(-bp)) / 2` from three engine calls (base, up, down);
  `up` = `NPV(+bp) - NPV(base)`; `down` = `NPV(base) - NPV(-bp)`. Results carry every
  per-call NPV (`npvs`; `npv_up` / `npv_down` per ladder row and for `parallel`) and a
  `dv01_definition` for the method used; `bumped_quotes` is keyed by side; call labels
  are `base` / `up` / `down` and `parallel:<side>` / `pillar:<label>:<side>`. `bump_bp`
  must be positive (the method sets the sign). Live on the 5Y EUR fixture the centered
  parallel DV01 is bracketed by the one-sided numbers (see the live test's printout).
- `market_data_source` (M5.3 enum, same stamping) is now required on `swap_dv01`,
  `key_rate_ladder`, `scenario` and `fair_rate`; `reprice_with` carries the declaration
  of the result it reprices (noted), requires it for an explicit `{endpoint, body}` or an
  unstamped result, and refuses a disagreeing one.
- Presets: the `trades.ois_swap` blocks of `USD_SOFR_OIS` and `EUR_ESTR_OIS` and the
  `trades.vanilla_swap` blocks of `EUR_EURIBOR_6M` and `EUR_EURIBOR_3M` now default
  `schedule.date_generation_rule` to `Backward` (short front stub, the market default for
  odd-dated swaps) with a `field_provenance` note: market practice for cleared and
  interbank swaps, not from an in-repo fixture; no vendor fixture is vendored.
  The engine fixtures these blocks otherwise follow use Forward, so the fixture
  reproductions (`price_vanilla_swap`, `price_ois_swap`, the blog OIS, the swaption
  underlyings) set `Forward` explicitly through the leg overrides and remain JSON-equal
  (goldens unchanged). For a regular spot-start tenor the two rules generate the same
  dates (the 5Y EUR analytics fixture still reproduces its catalog NPV under Backward).
- Instructions: the developer paragraph names the connector-analytics page and the two
  repository URLs so an agent can hand over the links.

## 0.1.3 (2026-10-07)

Absolute rule on market data (M5.3). Observed live: asked only what market data a
swaption ticket needed, the assistant typed 13 OIS quotes "from memory", priced the
trade on them and labelled the table "placeholder", using the former "illustrative /
demo price" exception on its own initiative. That exception is gone.

- Server instructions, `VOICE` and every prompt now state one absolute rule, verbatim:
  "Never type, estimate, recall or invent market data (quotes, discount factors, zero
  rates, vols, fixings). Not as a placeholder, not as a test run, not labelled as
  approximate. Market data for a user's trade exists only when the user has pasted or
  dictated it in this conversation. Until then: say what is needed, in paste-able form,
  and stop. Do not call any curve-building or pricing tool." The shipped examples remain
  request-shape references only; an example's own market data may be run only when the
  user explicitly asks to run that example, and is then reported as the example's data.
  The words "illustrative" and "demo price" no longer appear anywhere in instructions,
  prompts or README (pinned by a test).
- Mechanical guard: `build_curve`, `build_value_curve`, `curve_from_pasted_table`,
  `session_put` and every `price_*` tool (14) gained a REQUIRED `market_data_source`
  argument, an enum of exactly `user_pasted` | `user_file` | `engine_example` |
  `session`. No value describes estimated, recalled or placeholder data; the argument
  description says so and tells the model to ask the user instead of calling. A call
  without it, or with any other value, fails validation before anything runs. The
  declaration is echoed in the result (`market_data_source`) and as the first entry of
  `notes`; `session_put` stores it with the item (`item.market_data_source`, reported by
  `session_get` / `session_list`), a build result's own declaration must agree with the
  argument, and a later `{"session": name}` reference reports the stored source in the
  resolving tool's `notes`. No value-level validation is possible; the point is the
  explicit declaration.
- Tests: `tests/unit/test_market_data_source.py` (schema enum on every guarded tool,
  missing / invalid value fails validation, echo per tool and per value, session
  storage and carry-forward); the hermetic and live suites pass `market_data_source`
  on every call. README "For business users" (now `docs/clients.md`) states the rule in one sentence.

## 0.1.2 (2026-10-07)

Methodology transparency, generic (M5.2). Operator rule: nothing in the server is
specific to an example, a vendor or a screenshot.

- Scrubbed the vendor- / example-specific sentences from the server instructions,
  the `price-from-screen` prompt (title and steps are now vendor-neutral), the README
  and the `USD_SOFR_OIS` swaption provenance (engine fixture file names stay: that is
  sourcing). Vendor names remain only as neutral examples of ticket sources and of
  licensed data that is never server-side.
- `quantra://methodology` (index) and `quantra://methodology/{topic}` + tool
  `explain_method(topic)`: eleven pages (`npv`, `fair-rate`,
  `greeks-bump-and-reprice`, `theta`, `curve-bootstrap`, `value-curves`,
  `settlement-and-cash-settlement`, `volatility-types`, `calendars-and-overrides`,
  `day-counters-and-compounding`, `error-codes`) GENERATED by `scripts/pin_engine.py`
  (`scripts/methodology_gen.py` + the curated anchors in
  `scripts/methodology_rules.py`) from the engine's `docs/http-api.md`,
  `docs/versioning.md`, the `.fbs` schema comments and the evaluator / domain sources
  at the pinned tag. Every statement is a verbatim excerpt with a
  `path@tag:Lstart-Lend` citation under a one-line paraphrase; each page ends with
  what the engine does not document (e.g. units of theta, cash-settlement formulas).
  90 citations over 25 engine files at `v0.7.0`. `docs/methodology/INDEX.json`
  records every cited file's line count and each excerpt's sha256; the unit test
  checks every range hermetically and byte-for-byte against the engine repository
  when it is available locally.
- New tool `compare_results(external, quantra)`: pure presentation of the user's
  `{label: number}` against the first priced item of a result (`abs_diff = quantra -
  external`, `rel_diff = abs_diff / |external|`, inputs shown), label mapping shown
  (premium / PV -> `npv`, PV01 -> `dv01`, fair rate -> `fair_rate` then
  `atm_forward`, ...), unknown or absent labels listed as unmapped with the reason,
  and per metric the methodology topic to consult.
- New tool `reprice_with(result_or_request, changes)`: edit fields of a previous
  result's echoed request (or an explicit `{endpoint, body}`) by dotted / indexed path
  (`{path, value}` or `{path, bump_bp}`), validate, reprice, and return
  `changes_applied`, `request_diff` (every differing leaf), both complete results and
  the numeric `differences` of the first item (`changed - base`). The generic "test
  the hypothesis" primitive.
- New prompt `reconcile-external-price`: identify the instrument -> inputs in
  paste-able form -> confirm conventions -> build the market -> price ->
  `compare_results` -> `explain_method` per differing metric -> TEST each candidate
  cause with `reprice_with` -> report demonstrated vs. unexplained. Instructions:
  never claim access to vendor data; never assert a cause without a citation or a
  reprice.
- Shipped examples: `scripts/pin_engine.py` now EXCLUDES any engine fixture whose
  file name, title or catalog description carries a vendor reference (case-insensitive
  `bbg`, `bloomberg`, `swpm`, `refinitiv`, `markit`, `icvs`); at `v0.7.0` that is
  `examples/data/swaption_ois_bbg_zerorate_request.json`, recorded under
  `excluded_vendor_specific` in `examples/INDEX.json`. 223 -> 222 examples. The USD
  OIS swaption path stays live-proven on the generic `swaption_ois_request` fixture
  (ParYieldCurve); the `USD_SOFR_OIS` swaption provenance cites only that fixture.
- HARD RULE in the instructions and every prompt: the shipped examples are
  request-SHAPE references only; the market data for a trade the user brought
  (curves, vols, fixings) MUST come from the user; never reuse an example's market
  data for the user's trade, never say "the engine already ships this trade"; without
  the user's market data, ask for it in paste-able form and stop; an explicitly
  requested demo price is labelled illustrative and names the example used.
- Live (engine 0.7.0): `reprice_with` on the EUR 5Y payer example flips the side in
  exactly one request leaf and reprices 200 (NPV -47408.48798885872 ->
  +47408.48798885872 within 1e-12 relative; both results replay byte-equal); a 1bp
  bump of one curve quote changes one leaf and the NPV. A scripted client runs the
  prompt's order on a pasted 11-node discount-factor table of the SOFR curve and
  reaches a price, then demonstrates the interpolation hypothesis by repricing with
  `interpolator: Linear`.

## 0.1.1 (2026-10-07)

Business-user voice and the swaption-on-OIS path (M5.1):

- Server instructions rewritten for a business audience (trade and market terms;
  can-it-be-priced -> missing market data in paste-able form -> conventions ->
  price -> reconcile against the screen's spot premium; licensed vendor curves
  must be pasted, never recalled), with a "for developers" technical map at the end.
  Prompt titles / descriptions in the same voice.
- `USD_SOFR_OIS` gains a `trades.swaption` block (swap index `USD_SOFR_OIS`, vol
  base UnitedStatesGovernmentBond / ModifiedFollowing / Actual365Fixed, settlement
  2 business days; underlying = the preset's `ois_swap`: annual Act/360 both legs,
  SOFR compounded, payment lag 2) sourced from the engine fixtures
  `swaption_ois_bbg_zerorate_request.json` / `swaption_ois_request.json` (v0.7.0)
  and the public swaption comparison post. `price_swaption` with
  `underlying_type: OisSwap` now reads an underlying given with the shared swap
  fields only as an OIS trade (the pydantic union used to pick the vanilla model
  and reject it). Both fixtures are rebuilt JSON-equal (goldens
  `price_swaption.swaption_ois_*.json`); live, the engine NPV equals a direct
  POST of the fixture (10585.395459259198 CollateralizedCashPrice;
  9743.012590146607 ParYieldCurve).
- New tool `curve_from_pasted_table(text, id, kind=discount|zero|par, ...)`:
  parses a pasted CSV / TSV / whitespace table (header optional; ISO, `DD-Mon-YYYY`
  or slash dates with an explicit or inferable format; tenors; `%` -> decimal via
  decimal arithmetic; thousands separators stripped; nothing else transformed)
  into `build_value_curve` or `build_curve`; returns the built curve plus
  `parsed_rows`, `unparsed` with reasons and parsing `notes`. For a discount table
  the engine's required anchor `{reference_date: 1.0}` is added in front and said so.
- New prompt `price-from-screen(product?)`.
- Live findings (engine 0.7.0): pasting the bootstrapped SOFR curve's discount
  factors back on a daily grid reprices the blog OIS to -7.5e-6 of
  337986.79130030936 (the engine prints DFs with 12 significant digits, so byte
  identity is not reachable); pasting only the true curve nodes (spot + tenor +
  2bd payment lag) gives +1.6e-6; pasting the DFs at the engine's reported
  `pillar_dates` (tenor dates from the reference date, not the helper nodes)
  does not reproduce the curve (+13.78).
- README: "For business users" section (now `docs/clients.md`) with a claude.ai Project instructions
  snippet.

## 0.1.0 (2026-10-07)

First release (phases M1 to M5): discovery, calendars, raw passthrough,
market construction with presets, one pricing tool per product, analytics by
composition, the engine's example catalog, and a hardened streamable-HTTP
mode packaged as a container and published to PyPI and GHCR.

Phase M5 (packaging, hosted hardening, CI publish):

- `--http` now serves the SDK's streamable-HTTP app wrapped in
  `hosted.HostedGuard`: per-client token bucket (`QUANTRA_RATE_LIMIT_RPM`
  60, `QUANTRA_RATE_LIMIT_BURST` 20; 429 with `Retry-After`;
  `X-Forwarded-For` honoured only with `QUANTRA_TRUST_PROXY=1`), request body
  cap (`QUANTRA_MAX_BODY_BYTES` 2 MiB; 413), global concurrency gate on
  `tools/call` with a bounded wait queue (`QUANTRA_MAX_CONCURRENCY` 4 +
  `QUANTRA_MAX_QUEUE` 8; 503 with `Retry-After`).
- `GET /` (JSON pointer with the MCP URL, `QUANTRA_PUBLIC_URL`), `GET /healthz`
  (no engine call), `GET /readyz` (engine `/health`, 2 s timeout) beside `/mcp`;
  CORS `*` on the GET routes.
- `QUANTRA_ALLOWED_HOSTS` wires the SDK's DNS-rebinding protection (wrong
  `Host` -> 421); `QUANTRA_ALLOWED_ORIGINS` restricts `Origin` (default: any,
  the server holds no credentials). Loopback binds keep the SDK's localhost
  defaults; a public bind without the variable logs a warning.
- Access log: one stderr line per `tools/call` (`client=` sha256 prefix of the
  address unless `QUANTRA_LOG_RAW_IP=1`, `tool=`, `endpoint=`, `status=`,
  `ms=`). Arguments and bodies are never logged.
- `QUANTRA_REQUIRE_ENGINE=1` refuses to start when the engine's `/meta` is
  unreachable (default: warn and serve). Session store bounded by
  `QUANTRA_SESSION_MAX_TOTAL_BYTES` (32 MiB, LRU) in addition to the item cap;
  `session_list` reports `size_bytes`.
- `Dockerfile` (python:3.12-slim, uv, non-root, `EXPOSE 8765`, healthcheck,
  `CMD quantra-mcp --http --host 0.0.0.0`), `.dockerignore`,
  `docker-compose.example.yml` (engine 0.7.0 + mcp).
- `.github/workflows/release.yml` on tag `v*`: version guard, gate, image to
  `ghcr.io/joseprupi/quantra-mcp:{version},latest` (linux/amd64), PyPI via
  trusted publishing (environment `pypi`), GitHub Release with this
  CHANGELOG section as body. Operator steps in `docs/RELEASING.md`.
- Tests: guard primitives, raw-ASGI saturation, Starlette TestClient
  coverage of every route and limit, MCP-over-HTTP tool call with access-log
  assertions, SDK host/origin validation with our settings.

Phase M4 (analytics by composition):

- `swap_dv01`, `key_rate_ladder`, `scenario`, `fair_rate` (`tools/analytics.py`):
  reprice a vanilla or OIS swap through the existing pricing tools under
  bumped markets; results report engine NPVs plus `bumped - base` differences
  and bucket sums only, with every reprice's complete uniform pricing result
  (echoed request included) in `calls`. `fair_rate` reads the engine's field
  and says so when the response has none.
- `builders/bumps.py`: pillars of a `TermStructure` in wire order with labels
  (`5Y`, `3x6`, `FUT <date>`, `<start>/<end>`, `BOND <maturity>`, value-point
  dates; duplicates suffixed `#i`); per-point quote field (`rate`, `spread`,
  `futures_price`, `zero_rate`, `forward_rate`); discount factors, bond clean
  prices, FX points and `quote_id` pillars refused by name.
- Spot / tenor dates are resolved once (base call) and pinned for reprices;
  fan-out bounded by new `QUANTRA_MAX_CONCURRENCY` (default 4).
- Tests: bump builder unit tests, analytics tools against a quote-sensitive
  fake engine (differences / sums checked against the fake's per-call
  outputs, concurrency bound observed), live M4 suite on the EUR 5Y payer
  fixture (DV01 sign and magnitude, ladder sum vs parallel within 2%,
  receiver concentration at 5Y, zero-bump scenario == base, replay equality).

Phase M3 (pricing convenience tools, examples catalog, prompts):

- One pricing tool per product: `price_vanilla_swap`, `price_ois_swap`
  (overnight parameters exposed), `price_fixed_rate_bond`,
  `price_floating_rate_bond`, `price_zero_coupon_bond`,
  `price_callable_fixed_rate_bond`, `price_fra`, `price_cap_floor`,
  `price_swaption`, `price_cds`, `price_equity_option`,
  `price_zc_inflation_swap`, `price_yoy_inflation_swap`,
  `price_yoy_inflation_cap_floor`; raw-shaped `calibrate_swaption_vol`,
  `calibrate_swaption_model`, `sample_vol_surface`. All accept a `market`
  (session reference, engine pricing block or build_curve results), a preset
  trade block for every convention, `additional_trades` batching and
  `calendar_overrides`; every default is listed in `notes` with its source;
  `spot` / tenor / FRA dates are resolved by the engine's `/calendar-advance`
  and kept in `date_resolution`; the built request is validated against the
  vendored spec before it is sent.
- Presets gained `trades` blocks sourced from named engine fixtures
  (`EUR_EURIBOR_6M`, `EUR_EURIBOR_3M`, `USD_SOFR_OIS`, `EUR_ESTR_OIS`) and
  four trade-only presets (`EUR_FIXED_BOND`, `EUR_CDS`, `EUR_EQUITY`,
  `EUR_HICP`); `field_provenance` lookups match the longest dotted prefix.
- Examples catalog: `scripts/pin_engine.py` vendors the engine's 221 example
  requests, its functional catalog (`quantra://docs/engine-catalog`) and
  license, and writes `examples/INDEX.json` (endpoint from the engine
  manifest or by unique schema match, title / description / QuantLib reference
  value, machine-checkable `oracle`, `expected_status`). Tools
  `list_examples` / `get_example`; resources `quantra://examples`,
  `quantra://examples/{category}/{name}`, `quantra://examples/{name}`.
- Prompts: `price-a-swap`, `bootstrap-from-strip`, `holiday-check`,
  `explore-examples`.
- Tests: hermetic rebuild of twelve engine fixtures through the tools
  (JSON-equal, goldens under `tests/golden/products/`), contract validation of
  all 223 examples, live sweep of all examples with oracle checks and live
  per-tool fixture reproduction; `scripts/live_check.py` M3 section.

Phase M2 (market construction):

- Presets as data (`src/quantra_mcp/presets/*.json`, pydantic-validated
  registry, `quantra://presets[/{id}]` resources): `USD_SOFR_OIS`,
  `EUR_ESTR_OIS`, `GBP_SONIA_OIS`, `GBP_SONIA_SWAP`, `EUR_EURIBOR_6M`,
  `EUR_EURIBOR_3M`, each with a provenance line and per-field provenance for
  conventions that are market standard rather than from an in-repo source.
  Bond presets deferred (BondHelper conventions not fully sourced).
- Builders (pure): `build_curve` (strip -> TermStructure + IndexDef, sorted by
  maturity, duplicates rejected, every convention noted), `build_value_curve`
  (explicit zero / discount / forward points; discount first point must be 1.0
  at the reference date), `build_query` (TenorGrid / RangeGrid, explicit
  zero / fwd options).
- Tools: `list_presets`, `get_preset`, `build_curve`, `build_value_curve`,
  `build_query`, `bootstrap_curve` (`/bootstrap-curves`; resolves
  `{"session": name}` references; echoes the resolved request; summary is a
  selection of response fields), `bootstrap_inflation_curve` (raw body,
  validated), `session_put` / `session_get` / `session_list` /
  `session_delete` (in-memory, LRU-capped by `QUANTRA_SESSION_MAX_ITEMS`).
- Tests: tenor parser, golden `/bootstrap-curves` requests per preset
  (`tests/golden/`), contract checks that every preset helper block carries
  every engine helper field, live tests (SOFR strip JSON-equal to the gold
  example + 50Y DF oracle, value curve, session round trip, every preset with
  monotone DFs), `scripts/live_check.py` M2 section, `scripts/parity_ql.py`
  (QuantLib parity per preset).
- Dropped the unused `respx` dev dependency.

Phase M1 (discovery, calendars, raw passthrough):

- Tools: `quantra_meta`, `quantra_health`, `list_endpoints`, `engine_schema`,
  `list_enums`, `calendar_holidays`, `calendar_business_days`,
  `calendar_advance` (all with per-request `calendar_overrides`),
  `engine_request` (any endpoint, validated against the vendored spec before
  sending, `X-Request-Id` forwarded).
- Resources: `quantra://docs/http-api`, `quantra://docs/versioning`,
  `quantra://schema/{endpoint}`, `quantra://enums/{name}`,
  `quantra://examples/{name}` (the two live-verified SOFR requests),
  `quantra://pin`.
- Engine contract vendored at `v0.7.0` (`scripts/pin_engine.py`); enums
  generated from the spec.
- Transports: stdio (default) and streamable HTTP (`--http --port`).
- `scripts/live_check.py`: every tool against a real engine, response
  byte-equal to a direct replay of the echoed request.
