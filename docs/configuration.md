# Configuration, security and limits

Every setting is an environment variable read at startup (`src/quantra_mcp/config.py`).
Client setup is in [clients.md](clients.md).

## Environment

| Env | Default | Meaning |
|---|---|---|
| `QUANTRA_ENGINE_URL` | `http://localhost:8080` | Engine JSON gateway (self-hosted or `https://api.quantra.io`). |
| `QUANTRA_TIMEOUT_S` | `60` | Per-request engine timeout in seconds. |
| `QUANTRA_MCP_LOG` | `info` | stderr log level (`debug`, `info`, `warning`, `error`). stdout is the MCP channel. |
| `QUANTRA_SESSION_MAX_ITEMS` | `64` | Cap on in-memory session scratch items **per MCP session** (least-recently-used eviction). |
| `QUANTRA_SESSION_MAX_TOTAL_BYTES` | `33554432` | Cap on the serialized size of all session items together, across every session (32 MiB; the least-recently-used other sessions are dropped first). |
| `QUANTRA_SESSION_MAX_SESSIONS` | `256` | Cap on the number of MCP sessions holding scratch (whole least-recently-used sessions are dropped). |
| `QUANTRA_MAX_CONCURRENCY` | `4` | Maximum simultaneous engine calls: the analytics fan-outs, and in `--http` mode the number of `tools/call` requests served at once. |
| `QUANTRA_REQUIRE_ENGINE` | `0` | `1`: refuse to start when the engine's `/meta` is unreachable (default: warn and serve). |
| `QUANTRA_SOURCES_DIR` | `<checkout>/vendor/sources` | Root of the vendored source trees (`engine/`, `quantlib/`) the `search_source` / `read_source` / `list_source` tools serve; fetched by `scripts/fetch_sources.py`; the container image sets it to `/app/vendor/sources`. |

`--http` mode only:

| Env | Default | Meaning |
|---|---|---|
| `QUANTRA_MAX_QUEUE` | `8` | `tools/call` requests allowed to wait for a concurrency slot; beyond that the call is refused with a tool error (`isError`, `status` 503, `retry_after_s`). |
| `QUANTRA_MAX_CONCURRENCY_PER_SESSION` | `2` | `tools/call` requests one MCP session may have running or queued at once; beyond that the call is refused with a tool error (`status` 429, `retry_after_s`), so one client cannot hold every global slot. |
| `QUANTRA_RATE_LIMIT_RPM` | `60` | Per-MCP-session token bucket over `tools/call` and `resources/read` (`0` disables); keyed on the `Mcp-Session-Id` the transport mints at `initialize`, on the client address before one exists. `initialize`, `notifications/initialized` and the `tools/list` / `resources/list` / `prompts/list` listings are free. A refused `tools/call` is a tool error (`isError`, `status` 429, `retry_after_s`); a refused resource read gets HTTP 429 with `Retry-After`. |
| `QUANTRA_RATE_LIMIT_BURST` | `20` | Token-bucket burst size (per session). |
| `QUANTRA_RATE_LIMIT_IP_RPM` | `600` | Per-IP ceiling over every request (abuse protection; burst = max(`QUANTRA_RATE_LIMIT_BURST`, a tenth of this); `0` disables). Plain routes answer 429 with `Retry-After`. |
| `QUANTRA_MAX_BODY_BYTES` | `2097152` | Request body cap (2 MiB); larger bodies get 413. |
| `QUANTRA_TRUST_PROXY` | `0` | `1`: take the client address from the first `X-Forwarded-For` hop. Set only behind your own reverse proxy. |
| `QUANTRA_ALLOWED_HOSTS` | *(empty)* | Comma-separated `Host` header allow-list for `/mcp` (DNS-rebinding protection; a listed host matches with or without a port). Empty on a loopback bind keeps the SDK's localhost defaults; empty on a public bind disables the check and logs a warning. |
| `QUANTRA_ALLOWED_ORIGINS` | *(empty = any)* | Comma-separated `Origin` allow-list for `/mcp`. Unset accepts any origin (the server has no credentials to protect). |
| `QUANTRA_LOG_RAW_IP` | `0` | `1`: log client addresses verbatim instead of a 12-hex sha256 prefix. |
| `QUANTRA_PUBLIC_URL` | *(empty)* | The externally reachable base URL, advertised by `GET /` (e.g. `https://mcp.quantra.io`). |

## Security and limits

- **No authentication.** `--http` mode and the hosted `mcp.quantra.io` are
  open by design: the engine is stateless, prices public-domain-style
  requests and stores nothing. Do not front a private engine with it without
  a proxy that authenticates.
- **Limits** (all configurable, see the table above): 60 tool calls or
  resource reads per minute per MCP session with a burst of 20, a 600/min
  ceiling per IP, 4 concurrent `tools/call` plus 8 queued, at most 2 in
  flight per session, 2 MiB request bodies (413), 60 s per engine call. The
  hosted instance runs these defaults. Limits are per MCP session, not per
  IP, because connector traffic from a hosted assistant reaches the server
  from a few shared egress addresses.
- **Failures are tool errors.** Every failed tool result (an engine error,
  a local validation rejection, a provenance refusal, a rate or concurrency
  refusal) carries the MCP `isError` flag and the same `{ok: false, error,
  ...}` JSON body as content and `structuredContent`; a refusal adds
  `retry_after_s`. A model sees an actionable message, never a bare transport
  failure.
- **Host validation.** With `QUANTRA_ALLOWED_HOSTS` set, `/mcp` rejects any
  other `Host` header with 421 (the SDK's DNS-rebinding protection); `Origin`
  is unrestricted unless `QUANTRA_ALLOWED_ORIGINS` is set. `GET /`, `/healthz`
  and `/readyz` are not host-checked and answer with CORS `*`.
- **Session scratch is per MCP session, in memory, bounded, never persisted.**
  Each streamable-HTTP session (the `Mcp-Session-Id` minted at `initialize`,
  protocol versions 2025-03-26 / 2025-06-18 / 2025-11-25) has its own store;
  another client of the same server never sees its items and a miss lists
  only the caller's own names. stdio has one implicit session. Bounded per
  session (items), globally (sessions, total bytes), all least-recently-used;
  gone when the process stops. A **2026-07-28 client** (single-exchange
  streamable HTTP: no `initialize`, no `Mcp-Session-Id`) carries no identity
  at all, so scratch is *unavailable* there rather than shared: `session_*`
  and `{"session": name}` answer a tool error that says to pass the built
  objects inline (every tool accepts a `build_curve` result in `curves` /
  `market`). Rate limits for such clients fall back to the client address.
- **What is logged.** One stderr line per tool call: timestamp, client address
  as a sha256 prefix (raw only with `QUANTRA_LOG_RAW_IP=1`), tool name, engine
  endpoint, status, duration. Never tool arguments, requests or responses. The
  startup line records the active limits. Nothing is persisted.
- **Numbers come from the engine.** Every result carries the exact request
  sent and the engine's body verbatim; replay it with `curl` against the same
  engine and you get the same bytes. Supported engines: `>= 0.7.0` (responses as the 0.8.2 pin documents them).
