"""Tier 0 (M3): the examples catalog as tools.

``list_examples`` filters the vendored engine fixtures by category or product;
``get_example`` returns one complete, valid request body (plus its endpoint
and catalog metadata) ready for ``engine_request`` or to be taken apart (its
``pricing`` block is a valid ``market`` for every pricing tool). No engine call.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from quantra_mcp import examples_catalog as cat
from quantra_mcp.errors import LocalValidationError
from quantra_mcp.tools._meta import tool

UNFILTERED_NAMES = 20


def list_examples_impl(
    category: str | None = None, product: str | None = None, limit: int | None = None
) -> dict[str, Any]:
    if category is not None and category not in cat.categories():
        return {
            "ok": False,
            "error": f"unknown category {category!r}; categories: {cat.categories()}",
        }
    if product is not None and product not in cat.products():
        return {"ok": False, "error": f"unknown product {product!r}; products: {cat.products()}"}
    if limit is not None and limit < 1:
        return {"ok": False, "error": f"limit must be >= 1, got {limit}"}
    items = cat.list_examples(category, product)
    index = cat.load_index()
    out: dict[str, Any] = {
        "ok": True,
        "engine_tag": index["engine_tag"],
        "count": len(items),
        "categories": cat.categories(),
        "products": cat.products(),
    }
    shown = items
    if category is None and product is None and limit is None:
        # the whole catalog is 245 rows (~60 KB): counts per category / product and the
        # first names only; a filter or a limit returns rows
        by_cat: dict[str, int] = {}
        by_prod: dict[str, int] = {}
        for r in items:
            by_cat[str(r["category"])] = by_cat.get(str(r["category"]), 0) + 1
            prod = str(r.get("product"))
            by_prod[prod] = by_prod.get(prod, 0) + 1
        shown = items[:UNFILTERED_NAMES]
        out["by_category"] = by_cat
        out["by_product"] = by_prod
        out["names"] = [str(r["name"]) for r in shown]
        out["hint"] = (
            f"unfiltered: {len(items)} examples; only the first {len(shown)} rows are shown. "
            "Pass category=<folder> or product=<product> for the full rows of one group, "
            "or limit=<n> for the first n rows of everything."
        )
    elif limit is not None:
        shown = items[:limit]
    out["shown"] = len(shown)
    out["examples"] = shown
    return out


def get_example_impl(name: str) -> dict[str, Any]:
    try:
        row = cat.find(name)
    except LocalValidationError as exc:
        return {"ok": False, "error": exc.error, "problems": exc.problems}
    return {
        "ok": True,
        "name": row["name"],
        "category": row["category"],
        "endpoint": row["endpoint"],
        "product": cat.PRODUCT_OF_ENDPOINT.get(str(row["endpoint"])),
        "title": row.get("title"),
        "description": row.get("description"),
        "exercises": row.get("exercises", []),
        "reference_text": row.get("reference_text") or None,
        "reference_value": row.get("reference_value"),
        "oracle": row.get("oracle"),
        "source": row.get("source"),
        "body": cat.load_body(row),
    }


def register(app: MCPServer) -> None:
    @tool(app, "Vendored engine examples (list)", engine=False)
    def list_examples(
        category: Annotated[
            str | None,
            Field(
                description="One of the fixture folders: ir_swaps, bonds, swaption, cds, fra, "
                "cap_floor, equity, inflation, inflation_cap_floor, curves, calendar, vol, "
                "callable_bonds, zero_coupon_swap, misc, blog."
            ),
        ] = None,
        product: Annotated[
            str | None,
            Field(
                description="Filter by endpoint product instead: vanilla_swap, ois_swap, "
                "fixed_rate_bond, swaption, cds, equity_option, ..."
            ),
        ] = None,
        limit: Annotated[
            int | None,
            Field(
                ge=1,
                description="At most this many rows. Without a filter and without limit the "
                "answer is compact: counts per category and product plus the first 20 names.",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """List the vendored engine example requests (no engine call). Each row: name, category,
        endpoint, product, title, reference_text (the QuantLib value the engine is asserted to
        match, when cataloged) and the resource uri; get_example(name) returns the body.
        """
        return list_examples_impl(category, product, limit)

    @tool(app, "Vendored engine example (one body)", engine=False)
    def get_example(
        name: Annotated[
            str,
            Field(
                description="The example name (list_examples lists them), e.g. "
                "irs_eur_5y_payer_ois_discounted_multicurve."
            ),
        ],
    ) -> dict[str, Any]:
        """One vendored example: endpoint, catalog description, reference value and the complete
        request ``body`` (no engine call). Run it with engine_request(endpoint, body), or pass
        body['pricing'] as the market of a pricing tool and rebuild the trade from a preset.
        Examples are request-shape references: never reuse their market data for a user's trade.
        """
        return get_example_impl(name)
