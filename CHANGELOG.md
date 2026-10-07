# Changelog

## 0.1.0.dev0 (unreleased)

First usable server (phases M1 + M2 + M3).

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
