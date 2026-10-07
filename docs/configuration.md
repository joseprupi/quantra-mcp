# Configuration, security and limits

Every setting is an environment variable read at startup (`src/quantra_mcp/config.py`).
Client setup is in [clients.md](clients.md).

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
