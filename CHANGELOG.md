# Changelog

## 0.1.0.dev0 (unreleased)

First usable server (phase M1: discovery, calendars, raw passthrough).

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
