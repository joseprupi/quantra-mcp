# quantra-mcp

An [MCP](https://modelcontextprotocol.io) server that is a client of the
[Quantra](https://quantra.io) pricing engine's JSON API (QuantLib-based, open
source): it builds correct requests from trade terms and named market-convention
presets, forwards them, and returns the engine's numbers plus the exact request
that was sent. It computes nothing itself; every number comes from the engine.

```
agent ──(MCP: stdio / streamable HTTP)──▶ quantra-mcp ──(HTTP/JSON)──▶ Quantra engine
```

## Use the public server

Hosted instance, no account: `https://mcp.quantra.io/mcp` (prices on the same
engine as `api.quantra.io`; open, rate-limited, nothing stored).

claude.ai: Settings → Connectors → Add custom connector → URL
`https://mcp.quantra.io/mcp` → ask Claude to price a trade.

- Claude Code: `claude mcp add --transport http quantra https://mcp.quantra.io/mcp`
- Claude Desktop: add `{"mcpServers": {"quantra": {"url": "https://mcp.quantra.io/mcp"}}}` to `claude_desktop_config.json`
- Cursor: add the same `mcpServers` entry to `.cursor/mcp.json` or `~/.cursor/mcp.json`

Per-client setup, the stdio form for a local engine, and project instructions
for business users are in [docs/clients.md](docs/clients.md).

## Run it yourself

Run an engine (one line, no account):

```bash
docker run -d --name quantra-engine -p 8080:8080 ghcr.io/joseprupi/quantra-server:0.7.0
```

Run the server against it, from PyPI or from a clone:

```bash
QUANTRA_ENGINE_URL=http://localhost:8080 uvx quantra-mcp               # stdio
git clone https://github.com/joseprupi/quantra-mcp && cd quantra-mcp && uv sync && uv run quantra-mcp --http --port 8765
```

Or run engine and server together with Docker Compose (only port 8765 is published):

```bash
curl -O https://raw.githubusercontent.com/joseprupi/quantra-mcp/main/docker-compose.example.yml
docker compose -f docker-compose.example.yml up -d
curl http://localhost:8765/readyz
claude mcp add --transport http quantra http://localhost:8765/mcp
```

| Env | Default | Meaning |
|---|---|---|
| `QUANTRA_ENGINE_URL` | `http://localhost:8080` | Engine JSON gateway (self-hosted or `https://api.quantra.io`). |
| `QUANTRA_TIMEOUT_S` | `60` | Per-request engine timeout in seconds. |
| `QUANTRA_MAX_CONCURRENCY` | `4` | Simultaneous engine calls (analytics fan-outs; `tools/call` in `--http` mode). |
| `QUANTRA_RATE_LIMIT_RPM` | `60` | `--http` only: per-client requests per minute (`0` disables). |
| `QUANTRA_ALLOWED_HOSTS` | *(empty)* | `--http` only: `Host` allow-list for `/mcp` behind a public hostname. |
| `QUANTRA_PUBLIC_URL` | *(empty)* | `--http` only: the externally reachable base URL advertised by `GET /`. |

The full table, hosted-mode hardening, security and limits:
[docs/configuration.md](docs/configuration.md).

## What it does

One tool per engine capability. Convenience tools turn trade terms and a
preset into a complete engine request (dates the server cannot compute are
resolved by the engine's calendar endpoints); raw passthrough takes any
request body, validated against the vendored OpenAPI spec first. 47 tools:

- Discovery and calendars (8): `quantra_meta`, `quantra_health`, `list_endpoints`, `engine_schema`, `list_enums`, `calendar_holidays`, `calendar_business_days`, `calendar_advance`
- Raw passthrough (1): `engine_request` for any of the 24 POST endpoints
- Curves and presets (8): `list_presets`, `get_preset`, `build_curve`, `build_value_curve`, `curve_from_pasted_table`, `build_query`, `bootstrap_curve`, `bootstrap_inflation_curve`
- Pricing (17): `price_*` for vanilla and OIS swaps, fixed / floating / zero-coupon / callable bonds, FRA, cap/floor, swaption, CDS, equity option, ZC and YoY inflation swaps and YoY cap/floor (14), plus swaption vol / model calibration and vol-surface sampling (3)
- Analytics by composition (4): `swap_dv01`, `key_rate_ladder`, `scenario`, `fair_rate`, all finite differences of engine NPVs
- Reconciliation (3): `explain_method` (cited engine methodology), `compare_results`, `reprice_with`
- Session scratch (4): `session_put`, `session_get`, `session_list`, `session_delete`
- Examples (2): `list_examples`, `get_example` over the engine's 222 vendored example requests
- Resources (docs, schemas, enums, examples, presets, pin, methodology) and 6 prompts

Catalog with every argument: [docs/tools.md](docs/tools.md).

## Rules

- Market data comes from the user. The server has no market-data source and no
  vendor data; licensed curves and vols are never available server-side. Every
  tool that takes market numbers requires a `market_data_source` declaration
  (`user_pasted`, `user_file`, `engine_example`, `session`); there is no value
  for invented data. The assistant will
  never type, estimate, recall or invent market data for a trade: until the
  user has pasted or dictated it, it says what is needed, in paste-able form,
  and stops.
- Every result echoes the exact `request` sent and the engine's `response`
  verbatim, so any number can be replayed with `curl` against the same engine.
  Engine errors pass through unchanged (`400` request wrong, `422` unpriceable).
- Every convention applied is listed in `notes` with its source (a preset
  field, an engine fixture, or "market standard, not from an in-repo source").
- The shipped examples are request-shape references only; an example's market
  data is never reused for a user's trade.

## Documentation

- [docs/clients.md](docs/clients.md): install and connect each client; project instructions for business users
- [docs/configuration.md](docs/configuration.md): environment variables, hosted-mode settings, security and limits
- [docs/tools.md](docs/tools.md): tools, result shape, analytics, reconciliation, resources, prompts, examples catalog
- [docs/presets.md](docs/presets.md): the market-convention presets and their provenance
- [docs/walkthroughs.md](docs/walkthroughs.md): price a swap in three calls; build a curve from a strip
- [docs/development.md](docs/development.md): gate, live checks, QuantLib parity, engine pin, layout
- [docs/RELEASING.md](docs/RELEASING.md): how a release is cut
- User guide on the site: https://quantra.io/docs/app/claude

## Development

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src   # the gate
QUANTRA_ENGINE_URL=http://localhost:18087 uv run python scripts/live_check.py            # every tool vs a real engine
```

The engine contract is pinned to the tag in `src/quantra_mcp/schema/PIN`
(v0.7.0); any engine `>= 0.7.0` that keeps the contract works. Bump it with
`scripts/pin_engine.py`, never by hand ([docs/development.md](docs/development.md)).

## License

BSD-3-Clause. See `LICENSE`.
