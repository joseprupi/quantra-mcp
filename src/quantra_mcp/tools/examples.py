"""Tier 0 (M3): the examples catalog as tools.

``list_examples`` filters the vendored engine fixtures by category or product;
``get_example`` returns one complete, valid request body (plus its endpoint
and catalog metadata) ready for ``engine_request`` or to be taken apart (its
``pricing`` block is a valid ``market`` for every pricing tool). No engine call.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from quantra_mcp import examples_catalog as cat
from quantra_mcp.errors import LocalValidationError


def list_examples_impl(category: str | None = None, product: str | None = None) -> dict[str, Any]:
    if category is not None and category not in cat.categories():
        return {
            "ok": False,
            "error": f"unknown category {category!r}; categories: {cat.categories()}",
        }
    if product is not None and product not in cat.products():
        return {"ok": False, "error": f"unknown product {product!r}; products: {cat.products()}"}
    items = cat.list_examples(category, product)
    index = cat.load_index()
    return {
        "ok": True,
        "engine_tag": index["engine_tag"],
        "count": len(items),
        "categories": cat.categories(),
        "products": cat.products(),
        "examples": items,
    }


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
    @app.tool()
    def list_examples(category: str | None = None, product: str | None = None) -> dict[str, Any]:
        """List the vendored engine example requests (no engine call).

        Args:
            category: one of the fixture folders (``ir_swaps``, ``bonds``, ``swaption``,
                ``cds``, ``fra``, ``cap_floor``, ``equity``, ``inflation``,
                ``inflation_cap_floor``, ``curves``, ``calendar``, ``vol``,
                ``callable_bonds``, ``zero_coupon_swap``, ``misc``, ``blog``).
            product: filter by endpoint product instead (``vanilla_swap``, ``ois_swap``,
                ``fixed_rate_bond``, ``swaption``, ``cds``, ``equity_option``, ...).

        Each row: ``name``, ``category``, ``endpoint``, ``product``, ``title``,
        ``reference_text`` (the QuantLib value the engine is asserted to match, when
        cataloged) and the resource ``uri``. ``get_example(name)`` returns the body.
        """
        return list_examples_impl(category, product)

    @app.tool()
    def get_example(name: str) -> dict[str, Any]:
        """One vendored example: endpoint, catalog description, reference value and the
        complete request ``body`` (no engine call).

        Use it as ``engine_request(endpoint, body)``; or pass ``body["pricing"]`` as the
        ``market`` of a pricing tool and let the tool rebuild the trade from a preset.
        """
        return get_example_impl(name)
