# quantra-mcp

An [MCP](https://modelcontextprotocol.io) server that lets any MCP-capable agent
(Claude Desktop, Claude Code, Cursor, custom agents) query business calendars,
discover the engine's request shapes and call any pricing endpoint on a
[Quantra](https://quantra.io) pricing engine (QuantLib-based, open source).

It is a **client** of the engine's JSON API. **Every number comes from the
engine; this server computes nothing.** Each tool result echoes the exact
request that was sent and the engine's response verbatim.

```
agent ──(MCP: stdio / streamable HTTP)──▶ quantra-mcp ──(HTTP/JSON)──▶ Quantra engine
                                                                       (localhost:8080
                                                                        or api.quantra.io)
```

Status: **0.1.0.dev0**, phase M2 (discovery, calendars, raw passthrough,
market construction: presets, curve builders, bootstrap, session scratch).
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
| `quantra://examples/{name}` | Live-verified request bodies: `sofr-bootstrap-request`, `sofr-ois-swap-request` (`quantra://examples` lists them; provenance in `src/quantra_mcp/examples/README.md`). |
| `quantra://pin` | The engine tag, commit and image this server is pinned to. |
| `quantra://presets/{id}` | A market-convention preset as data with per-field provenance (`quantra://presets` lists them). |

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

`live_check.py` runs every tool and both examples and asserts each tool's
`response` is byte-equal to a direct HTTP replay of its echoed `request`; its
M2 section builds the SOFR strip through `build_curve` and asserts the request
is JSON-equal to the gold example and the 50Y DF matches the oracle, round-trips
a discount value curve, bootstraps every preset (monotone DFs) and resolves a
session reference.

QuantLib parity (the only place QuantLib is imported; needs a Python with
`QuantLib` and `pydantic`, e.g. the engine's QuantLib 1.41):

```bash
QUANTRA_ENGINE_URL=http://localhost:18087 PYTHONPATH=src python3 scripts/parity_ql.py
# or: QUANTRA_ENGINE_URL=http://localhost:18087 uv run --with QuantLib python scripts/parity_ql.py
```

It rebuilds every preset's canonical strip (`tests/golden/strips.json`) in
QuantLib with the preset's conventions and asserts max |DF diff| <= 1e-10 on
the engine's grid dates.

Golden request files under `tests/golden/` are regenerated with
`QUANTRA_REGEN_GOLDENS=1 uv run pytest tests/unit/test_builders.py` after an
intentional builder change.

Bump the engine pin (copies the spec and docs from an engine checkout at a tag,
regenerates the enums, writes `PIN`):

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
  presets/           market-convention presets (JSON data + registry)
  builders/          pure builders: tenor parser, curves (strip / value curve / query)
  session.py         in-memory session scratch (LRU, per process)
  tools/             discovery, calendar, raw passthrough, curves, session
  resources.py       docs / schema / enums / examples / pin / presets
  docs/, examples/   vendored engine docs and the example requests
scripts/             pin_engine.py, live_check.py, parity_ql.py
tests/               unit, contract (vendored spec), live (real engine), golden/ requests
```

## License

BSD-3-Clause. See `LICENSE`.
