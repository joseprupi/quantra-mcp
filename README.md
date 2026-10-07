# quantra-mcp

An [MCP](https://modelcontextprotocol.io) server that lets any MCP-capable agent
(Claude Desktop, Claude Code, Cursor, custom agents) price derivatives, bootstrap
curves, calibrate volatilities and query business calendars on a
[Quantra](https://quantra.io) pricing engine.

It is a **client** of the open-source engine's JSON API. It builds correct,
fully explicit engine requests from a few agent-friendly parameters, forwards
them, and returns the engine's numbers together with the exact request it sent.
It never prices anything itself.

```
agent ──(MCP: stdio / streamable HTTP)──▶ quantra-mcp ──(HTTP/JSON)──▶ Quantra engine
                                                                       (api.quantra.io
                                                                        or localhost:8080)
```

## Status

**Designing.** Nothing runs yet. The plan (north star, feature catalog,
architecture, phases, open questions) lives in the Quantra hub repo under
`redesign_plan/modules/mcp/`.

## Planned usage

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

Point `QUANTRA_ENGINE_URL` at a self-hosted engine
(`docker run -p 8080:8080 ghcr.io/joseprupi/quantra-server:0.7.0`) or at the
public demo `https://api.quantra.io`.

## Layout (planned)

```
src/quantra_mcp/
  server.py          FastMCP app, transport selection
  backend/           engine HTTP client (orchestrator backend later)
  tools/             calendar, curves, pricing, raw passthrough
  presets/           market convention presets (USD SOFR, EUR ESTR, ...)
  schema/            vendored engine OpenAPI spec, pinned to an engine tag
tests/               unit + contract (vendored spec) + live (real engine)
```

## License

To be decided before the first release (see the plan's open questions).
