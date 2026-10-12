"""Resources: the engine docs at the pin, schemas, enums, examples, the pin itself."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError

from quantra_mcp import examples_catalog as cat
from quantra_mcp import methodology
from quantra_mcp.errors import LocalValidationError
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


DEVELOPER_GUIDE = """\
# quantra-mcp for developers: the technical map

quantra_meta reports the engine version and
products; list_endpoints / engine_schema / list_enums describe request shapes;
quantra://examples/* and list_examples / get_example give 222 complete verified
engine requests (an example's `pricing` block is a valid `market`); engine_request
POSTs any endpoint. Curves: list_presets / get_preset -> build_curve(preset, quotes)
or curve_from_pasted_table(text, kind=discount|zero|par) or build_value_curve ->
build_query -> bootstrap_curve; session_put stores a built curve so later calls pass
{"session": "<name>"}. Pricing: one price_<product> tool per product takes a `market`
({"session": name} | an engine pricing block | {curves, indices, ...}), a `preset`
whose trade block supplies every convention, and the trade economics; "spot" and
tenor dates are resolved by the engine's /calendar-advance and reported in
`date_resolution`. Analytics by composition: swap_dv01, key_rate_ladder, scenario,
fair_rate (each reprice's full result is in `calls`; DV01 `method` centered | up | down,
centered by default = (NPV(+bp) - NPV(-bp)) / 2). Reconciliation: compare_results
(external {label: number} vs a result; abs / rel differences; topic per metric),
explain_method(topic) (the cited methodology page) and reprice_with(result, changes)
(edit fields of an echoed request by path, reprice, diff both requests and the numeric
fields). The connector-side computations (bumps, DV01 differences, scenario changes,
reprice diffs) are documented and cited into this server's own source at
quantra://methodology/connector-analytics, which also lists which result fields are
engine outputs and which are differences computed here. Every citation carries a GitHub
permalink; the two repositories are the engine https://github.com/joseprupi/quantraserver
(cited at the pinned tag) and this server https://github.com/joseprupi/quantra-mcp (cited
at a commit); both URLs are also the first entry of the quantra://methodology index.
Every result echoes the exact
request sent (`request`) and the engine body verbatim (`response`); on an engine error
`ok` is false and `error` is the engine's text (400 = request wrong, 422 = well-formed
but unpriceable). The engine does not default omitted fields; every convention a
builder applies is listed in `notes` with its preset source.

Failures are tool errors: every result whose `ok` is false (an engine error, a local
validation rejection, a provenance refusal, a hosted-mode rate or concurrency refusal)
is returned with the MCP `isError` flag set, with the same JSON body as content and as
`structuredContent`; a refusal carries `retry_after_s`. Session scratch (session_put /
session_get / session_list / session_delete and `{"session": name}` references) is
private to the MCP session, in memory, bounded, never persisted.
"""


def example_names() -> list[str]:
    """The two blog examples at the top of ``examples/`` (the M1 resource set)."""
    return sorted(p.stem for p in EXAMPLES_DIR.glob("*.json") if p.name != "INDEX.json")


def load_example(name: str) -> dict[str, Any]:
    """Any vendored example by name (blog examples and engine fixtures alike)."""
    try:
        row = cat.find(name)
    except LocalValidationError as exc:
        raise ResourceNotFoundError(exc.error) from None
    return cat.load_body(row)


def read_doc(name: str) -> str:
    path = DOCS_DIR / f"{name}.md"
    if not path.is_file():
        raise ResourceNotFoundError(f"unknown doc {name!r}")
    return path.read_text()


def register(app: MCPServer) -> None:
    @app.resource(
        "quantra://guide/developers",
        name="developer-guide",
        description=(
            "The technical map of this server for developers: discovery, curve, pricing, "
            "analytics and reconciliation tools, result shapes, errors, session scratch."
        ),
        mime_type="text/markdown",
    )
    def guide_developers() -> str:
        return DEVELOPER_GUIDE

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
        "quantra://docs/engine-catalog",
        name="engine-catalog",
        description=(
            "The engine's functional parity catalog at the pinned tag: one row per example "
            "with its description and QuantLib reference value."
        ),
        mime_type="text/markdown",
    )
    def docs_engine_catalog() -> str:
        return read_doc("engine-catalog")

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
        description=(
            "Index of every vendored example request (engine fixtures at the pin + the two "
            "blog examples): name, category, endpoint, title, reference value."
        ),
        mime_type="application/json",
    )
    def examples_index() -> dict[str, Any]:
        index = cat.load_index()
        return {
            "engine_tag": index["engine_tag"],
            "count": index["count"],
            "categories": cat.categories(),
            "examples": [cat.summary_row(r) for r in cat.rows()],
        }

    @app.resource(
        "quantra://examples/{category}/{name}",
        name="engine-example-in-category",
        description=(
            "One vendored example body with its catalog metadata, e.g. "
            "quantra://examples/ir_swaps/irs_eur_5y_payer_ois_discounted_multicurve."
        ),
        mime_type="application/json",
    )
    def example_in_category(category: str, name: str) -> dict[str, Any]:
        try:
            row = cat.find(name)
        except LocalValidationError as exc:
            raise ResourceNotFoundError(exc.error) from None
        if row["category"] != category:
            raise ResourceNotFoundError(
                f"example {name!r} is in category {row['category']!r}, not {category!r}"
            )
        return {
            **cat.summary_row(row),
            "description": row.get("description"),
            "body": cat.load_body(row),
        }

    @app.resource(
        "quantra://examples/{name}",
        name="engine-example",
        description=(
            "A vendored example request body by name (any category), e.g. "
            "quantra://examples/sofr-ois-swap-request (POST /price-ois-swap, NPV 337986.7913...); "
            "a category name (quantra://examples/bonds) lists that category."
        ),
        mime_type="application/json",
    )
    def example_resource(name: str) -> dict[str, Any]:
        if name in cat.categories():
            return {"category": name, "examples": cat.list_examples(category=name)}
        return load_example(name)

    @app.resource(
        "quantra://methodology",
        name="methodology-index",
        description=(
            "How the engine computes what it returns: one page per topic (npv, fair-rate, "
            "greeks-bump-and-reprice, theta, curve-bootstrap, value-curves, "
            "settlement-and-cash-settlement, volatility-types, calendars-and-overrides, "
            "day-counters-and-compounding, schedules-and-stubs, error-codes), generated from "
            "the engine's own docs and source at the pin with path@tag:line citations and "
            "GitHub permalinks; plus connector-analytics (what this server computes on top of "
            "engine outputs, cited into its own source). The two repository URLs come first."
        ),
        mime_type="application/json",
    )
    def methodology_index() -> dict[str, Any]:
        index = methodology.load_index()
        return {
            "repos": methodology.repos(),
            "engine_tag": index["engine_tag"],
            "engine_sha": index["engine_sha"],
            "connector_sha": index["connector_sha"],
            "topics": methodology.topics(),
            "metric_topics": index["metric_topics"],
        }

    @app.resource(
        "quantra://methodology/{topic}",
        name="methodology-page",
        description=(
            "One methodology page as markdown, e.g. quantra://methodology/theta: plain-language "
            "summary, the engine's own excerpts each cited path@tag:Lstart-Lend with a GitHub "
            "permalink, the request fields that control it, and what the engine does not "
            "document. quantra://methodology/connector-analytics documents this server's own "
            "arithmetic (DV01 differences, bumps, reprice diffs) cited into its source."
        ),
        mime_type="text/markdown",
    )
    def methodology_page(topic: str) -> str:
        try:
            return str(methodology.get_topic(topic)["markdown"])
        except LocalValidationError as exc:
            raise ResourceNotFoundError(exc.error) from None

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
