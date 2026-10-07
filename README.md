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

Status: **0.1.0.dev0**, phase M1 (discovery, calendars, raw passthrough).
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
`response` is byte-equal to a direct HTTP replay of its echoed `request`.

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
  tools/             discovery, calendar, raw passthrough
  resources.py       docs / schema / enums / examples / pin
  docs/, examples/   vendored engine docs and the example requests
scripts/             pin_engine.py, live_check.py
tests/               unit, contract (vendored spec), live (real engine)
```

## License

BSD-3-Clause. See `LICENSE`.
