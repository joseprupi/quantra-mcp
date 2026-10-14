# Development

Gate, container, live acceptance, QuantLib parity, goldens, the engine pin and
the repository layout. Releases are in [RELEASING.md](RELEASING.md).

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
the image, the PyPI package and the GitHub Release. See [RELEASING.md](RELEASING.md).

Live acceptance (needs a real engine; the CI `live` job does the same):

```bash
docker run -d --name quantra-mcp-engine -p 18087:8080 ghcr.io/joseprupi/quantra-server:0.8.2
QUANTRA_ENGINE_URL=http://localhost:18087 uv run pytest tests/live -m live
QUANTRA_ENGINE_URL=http://localhost:18087 uv run python scripts/live_check.py
```

`live_check.py` runs every tool and asserts each tool's `response` is
byte-equal to a direct HTTP replay of its echoed `request`; its M2 section
builds the SOFR strip through `build_curve` and asserts the request is
JSON-equal to the gold example and the 50Y DF matches the oracle, round-trips
a discount value curve, bootstraps every preset (monotone DFs) and resolves a
session reference; its M3 section sweeps all 223 vendored examples through
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
`examples/INDEX.json`, writes `PIN` and `QUANTLIB_PIN`, the `ARG QUANTLIB_VERSION`
of the engine's Dockerfile at that tag):

```bash
uv run python scripts/pin_engine.py --engine-repo /path/to/quantraserver --tag v0.8.2
uv run python scripts/fetch_sources.py   # then refresh the source trees + schema/SOURCES.lock
```

Source trees for `search_source` / `read_source` / `list_source`:
`scripts/fetch_sources.py` downloads the engine tarball at the pinned tag and the
QuantLib tarball at `v<QUANTLIB_PIN>`, verifies both sha256 against
`src/quantra_mcp/schema/SOURCES.lock` (recorded on the first fetch of a tag; a later
fetch of the same tag must match or the script fails), and extracts only text files
(`.h .hpp .cpp .c .fbs .md .json .txt .cmake .sh .py`) of the allowed trees into
`vendor/sources/` (gitignored, ~30 MB; `--check` verifies without network, `--force`
re-extracts). The hermetic gate runs the tools over `tests/fixtures/sources/`; the
live-ish tests in `tests/unit/test_source_live_ish.py` run over the fetched trees and
are skipped until the script has run. `tests/live` asserts the engine's `/meta`
`dependencies.quantlib` equals `QUANTLIB_PIN`. The Dockerfile runs the fetch in its
build stage (network at build time only) and ships the trees in the image.

## Layout

```
src/quantra_mcp/
  server.py          MCPServer app, transport selection, startup engine check
  hosted.py          --http hardening: guard (per-session + per-IP rate limits, body cap, concurrency; refusals as tool errors), / /healthz /readyz, host validation, access log
  middleware.py      MCP-level middleware: session scope (binds the Mcp-Session-Id), failures -> isError
  rules.py           the two market-data rules (quoted verbatim by INSTRUCTIONS and every prompt) and the source rule
  sources.py         the vendored engine + QuantLib trees: lazy index, bounded search, read, list
  config.py          env -> Settings
  backend/           Backend protocol + the engine HTTP client
  schema/            vendored openapi3.json, PIN, QUANTLIB_PIN, SOURCES.lock, generated enums, loader, validator
  presets/           market-convention presets (JSON data + registry; curve + trade blocks)
  builders/          pure builders: tenor parser, curves, schedule, market, bumps, products/<product>
  session.py         in-memory session scratch: one store per MCP session (LRU per session, LRU sessions, global byte budget)
  tools/             discovery, calendar, raw passthrough, curves, pricing, analytics, examples, session,
                     explain (methodology), reconcile (compare_results, reprice_with)
  resources.py       docs / schema / enums / examples / pin / presets / methodology
  methodology.py     loader for the generated methodology pages (metric -> topic map)
  prompts.py         price-a-swap, bootstrap-from-strip, holiday-check, explore-examples,
                     price-from-screen, reconcile-external-price
  examples_catalog.py  INDEX.json access + oracle checks
  docs/, examples/   vendored engine docs, catalog and the 223 example requests;
                     docs/methodology/ = the generated cited pages + INDEX.json
scripts/             pin_engine.py (+ methodology_gen.py, methodology_rules.py), live_check.py, parity_ql.py
tests/               unit, contract (vendored spec), live (real engine), golden/ requests
Dockerfile, docker-compose.example.yml, .github/workflows/{ci,release}.yml
docs/               clients, configuration, tools, presets, walkthroughs, development, RELEASING
```
