# Clients: install and connect

How to run `quantra-mcp` and connect it to claude.ai, Claude Code, Claude Desktop,
Cursor, your own streamable-HTTP deployment or Docker Compose. The environment
variables are in [configuration.md](configuration.md); the tools in
[tools.md](tools.md).

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
docker run --rm -p 8765:8765 -e QUANTRA_ENGINE_URL=http://host.docker.internal:8080 ghcr.io/joseprupi/quantra-mcp:0.1.4
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
limits described under [Security](configuration.md#security-and-limits).

### For business users (claude.ai)

You do not need to know any of the tool names in [tools.md](tools.md). Connect the server (above),
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
