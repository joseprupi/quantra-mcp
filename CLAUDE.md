# quantra-mcp — worker instructions

This repo is the Quantra MCP server: a Python MCP server that fronts the
Quantra pricing engine's JSON API for agents. It is a CLIENT of the engine.

Rules:
- Never implement pricing math here. Build requests, call the engine, return
  its numbers plus the exact request sent.
- Every convenience tool default is explicit, documented, and echoed back in
  the tool result. No silent conventions.
- Engine errors pass through verbatim (`error` field, HTTP status). Keep the
  400 (request wrong) vs 422 (unpriceable) distinction.
- The vendored OpenAPI spec under `src/quantra_mcp/schema/` is pinned to an
  engine tag. Do not hand-edit it; bump it with the pin script.

Test gate (must exit 0 before any commit):
  uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src

Live gate: tools that touch the engine are accepted only after a run against a
real engine container (`ghcr.io/joseprupi/quantra-server:<pinned tag>`), with
the MCP result equal to a direct HTTP replay of the echoed request.

Plan of record: Quantra hub repo, `redesign_plan/modules/mcp/`.
