"""Resources: the engine docs at the pin, schemas, enums, examples, the pin itself."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError

from quantra_mcp.presets.registry import PresetError, get_preset, list_presets
from quantra_mcp.schema.loader import SpecError, load_spec, normalize_endpoint, pin
from quantra_mcp.tools.discovery import endpoint_schema, enum_listing

PKG_DIR = Path(__file__).resolve().parent
DOCS_DIR = PKG_DIR / "docs"
EXAMPLES_DIR = PKG_DIR / "examples"

EXAMPLE_ENDPOINTS: dict[str, str] = {
    "sofr-bootstrap-request": "/bootstrap-curves",
    "sofr-ois-swap-request": "/price-ois-swap",
}


def example_names() -> list[str]:
    return sorted(p.stem for p in EXAMPLES_DIR.glob("*.json"))


def load_example(name: str) -> dict[str, Any]:
    path = EXAMPLES_DIR / f"{name}.json"
    if not path.is_file() or "/" in name or name.startswith("."):
        raise ResourceNotFoundError(
            f"unknown example {name!r}; available: {', '.join(example_names())}"
        )
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def read_doc(name: str) -> str:
    path = DOCS_DIR / f"{name}.md"
    if not path.is_file():
        raise ResourceNotFoundError(f"unknown doc {name!r}")
    return path.read_text()


def register(app: MCPServer) -> None:
    @app.resource(
        "quantra://docs/http-api",
        name="engine-http-api",
        description=(
            "The engine's HTTP API contract (status codes, headers, calendar overrides) "
            "at the pinned tag."
        ),
        mime_type="text/markdown",
    )
    def docs_http_api() -> str:
        return read_doc("http-api")

    @app.resource(
        "quantra://docs/versioning",
        name="engine-versioning",
        description="The engine's versioning policy and migration notes at the pinned tag.",
        mime_type="text/markdown",
    )
    def docs_versioning() -> str:
        return read_doc("versioning")

    @app.resource(
        "quantra://pin",
        name="engine-pin",
        description="The engine tag and commit this server's vendored contract is pinned to.",
        mime_type="application/json",
    )
    def pin_resource() -> dict[str, Any]:
        p = pin()
        spec = load_spec()
        return {
            "tag": p.tag,
            "sha": p.sha,
            "api_version": spec.api_version,
            "image": f"ghcr.io/joseprupi/quantra-server:{p.version}",
            "endpoints": len(spec.endpoints),
        }

    @app.resource(
        "quantra://schema/{endpoint}",
        name="engine-schema",
        description="Full request/response schema for one POST endpoint, e.g. quantra://schema/price-ois-swap.",
        mime_type="application/json",
    )
    def schema_resource(endpoint: str) -> dict[str, Any]:
        try:
            return endpoint_schema(normalize_endpoint(endpoint), depth=8)
        except SpecError as exc:
            raise ResourceNotFoundError(str(exc)) from None

    @app.resource(
        "quantra://enums",
        name="engine-enums",
        description="Names of every engine enum.",
        mime_type="application/json",
    )
    def enums_index() -> dict[str, Any]:
        return {"enums": load_spec().enum_names}

    @app.resource(
        "quantra://enums/{name}",
        name="engine-enum",
        description="Values of one engine enum, e.g. quantra://enums/Calendar.",
        mime_type="application/json",
    )
    def enum_resource(name: str) -> dict[str, Any]:
        try:
            return enum_listing(name)
        except SpecError as exc:
            raise ResourceNotFoundError(str(exc)) from None

    @app.resource(
        "quantra://examples",
        name="engine-examples",
        description="Names of the live-verified example requests and their endpoints.",
        mime_type="application/json",
    )
    def examples_index() -> dict[str, Any]:
        return {
            "examples": [
                {"name": n, "endpoint": EXAMPLE_ENDPOINTS.get(n), "uri": f"quantra://examples/{n}"}
                for n in example_names()
            ]
        }

    @app.resource(
        "quantra://examples/{name}",
        name="engine-example",
        description=(
            "A live-verified request body, e.g. quantra://examples/sofr-ois-swap-request "
            "(POST /price-ois-swap, NPV 337986.7913...)."
        ),
        mime_type="application/json",
    )
    def example_resource(name: str) -> dict[str, Any]:
        return load_example(name)

    @app.resource(
        "quantra://presets",
        name="market-presets",
        description=(
            "Market-convention presets for build_curve / build_value_curve "
            "(id, currency, index, helper types, provenance)."
        ),
        mime_type="application/json",
    )
    def presets_index() -> dict[str, Any]:
        return {
            "presets": [{**row, "uri": f"quantra://presets/{row['id']}"} for row in list_presets()]
        }

    @app.resource(
        "quantra://presets/{id}",
        name="market-preset",
        description=(
            "One preset as data with per-field provenance, e.g. quantra://presets/USD_SOFR_OIS."
        ),
        mime_type="application/json",
    )
    def preset_resource(id: str) -> dict[str, Any]:
        try:
            return get_preset(id).as_data()
        except PresetError as exc:
            raise ResourceNotFoundError(str(exc)) from None
