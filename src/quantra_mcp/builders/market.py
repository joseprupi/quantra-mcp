"""The ``market`` argument of every pricing tool -> an engine ``pricing`` block (pure).

Accepted forms:

* ``{"session": "<name>"}`` — a stored ``market`` (``{curves, indices, ...}``)
  or ``curve`` (plus its attached indices);
* an engine ``pricing`` block (anything with ``as_of_date``) — used verbatim
  (deep-copied), so a vendored example's ``pricing`` can be passed straight in;
* a market object: ``{curves: [...], indices?: [...], swap_indices?,
  coupon_pricers?, vol_surfaces?, models?, credit_curves?, quotes?,
  equity_underlyings?, inflation_indices?, inflation_curves?, options?}``
  where ``curves`` items may be ``TermStructure`` objects, ``build_curve``
  results or session references.

Tools then add what they build (vol surfaces, models, credit curves, coupon
pricers, flat curves, quotes, underlyings) with :func:`add_item`: an item whose
id already exists with identical content is sent once; a conflicting id is a
local error (the engine rejects duplicate ids and the server never picks one).
"""

from __future__ import annotations

import copy
from typing import Any

from quantra_mcp.builders.curves import merge_indices
from quantra_mcp.builders.schedule import check_date, problem
from quantra_mcp.session import SessionStore, resolve_refs

#: market-object key -> (pricing path, list key)
SECTIONS: dict[str, tuple[tuple[str, ...], str]] = {
    "indices": (("rates",), "indices"),
    "swap_indices": (("rates",), "swap_indices"),
    "curves": (("rates",), "curves"),
    "coupon_pricers": (("rates",), "coupon_pricers"),
    "vol_surfaces": (("volatility",), "vol_surfaces"),
    "models": (("volatility",), "models"),
    "credit_curves": (("credit",), "credit_curves"),
    "quotes": ((), "quotes"),
    "equity_underlyings": (("equity",), "equity_underlyings"),
    "inflation_indices": (("inflation",), "inflation_indices"),
    "inflation_curves": (("inflation",), "inflation_curves"),
}


def _is_pricing_block(market: Any) -> bool:
    return isinstance(market, dict) and "as_of_date" in market


def _section(pricing: dict[str, Any], key: str, create: bool) -> list[dict[str, Any]] | None:
    path, list_key = SECTIONS[key]
    node: dict[str, Any] = pricing
    for part in path:
        child = node.get(part)
        if child is None:
            if not create:
                return None
            child = node[part] = {}
        node = child
    lst = node.get(list_key)
    if lst is None:
        if not create:
            return None
        lst = node[list_key] = []
    if not isinstance(lst, list):
        raise problem(f"/pricing/{'/'.join([*path, list_key])}", f"{key}: expected a list")
    return lst


def find_item(pricing: dict[str, Any], key: str, item_id: str) -> dict[str, Any] | None:
    lst = _section(pricing, key, create=False)
    if not lst:
        return None
    for it in lst:
        if isinstance(it, dict) and it.get("id") == item_id:
            return it
    return None


def ids_in(pricing: dict[str, Any], key: str) -> list[str]:
    lst = _section(pricing, key, create=False) or []
    return [str(it.get("id")) for it in lst if isinstance(it, dict) and "id" in it]


def add_item(
    pricing: dict[str, Any], key: str, item: dict[str, Any], notes: list[str], what: str
) -> None:
    """Append ``item`` to the section; identical duplicate -> sent once; conflict -> error."""
    lst = _section(pricing, key, create=True)
    assert lst is not None
    existing = find_item(pricing, key, str(item.get("id")))
    if existing is None:
        lst.append(item)
        notes.append(f"{what} {item.get('id')!r} added to pricing.{key}")
    elif existing == item:
        notes.append(f"{what} {item.get('id')!r} already in pricing.{key} with identical fields")
    else:
        raise problem(
            f"/market/{key}",
            f"{what} id {item.get('id')!r} is already in the market with different fields; "
            "pick another id or pass the existing one by id",
        )


def resolve_market(
    store: SessionStore,
    market: Any,
    as_of: str | None,
    calendar_overrides: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """-> (pricing block, notes). See the module docstring for the accepted forms."""
    notes: list[str] = []
    if not isinstance(market, dict):
        raise problem(
            "/market",
            "market must be {'session': name}, an engine pricing block (with as_of_date) or "
            "{curves: [...], indices: [...], ...}",
        )
    if _is_pricing_block(market):
        pricing: dict[str, Any] = copy.deepcopy(market)
        check_date(pricing["as_of_date"], "market/as_of_date")
        if as_of is not None and as_of != pricing["as_of_date"]:
            raise problem(
                "/as_of",
                f"as_of {as_of!r} differs from market.as_of_date {pricing['as_of_date']!r}; "
                "drop as_of or make them agree",
            )
        notes.append(
            f"market is an engine pricing block (as_of_date {pricing['as_of_date']}); used as given"
        )
    else:
        if as_of is None:
            raise problem(
                "/as_of", "as_of (YYYY-MM-DD) is required unless market is a pricing block"
            )
        as_of = check_date(as_of, "as_of")
        if set(market) == {"session"}:
            curves_in: list[Any] = [market]
            extra: dict[str, Any] = {}
        else:
            unknown = sorted(set(market) - set(SECTIONS) - {"options"})
            if unknown:
                raise problem(
                    "/market",
                    f"unknown market keys {unknown}; known: {sorted(SECTIONS)} and options",
                )
            curves_in = list(market.get("curves") or [])
            extra = {k: v for k, v in market.items() if k not in ("curves", "indices")}
        r_curves, r_indices, ref_notes = resolve_refs(store, curves_in, market.get("indices"))
        notes += ref_notes
        merged, merge_notes = merge_indices(r_indices)
        notes += merge_notes
        pricing = {"as_of_date": as_of, "rates": {"indices": merged, "curves": r_curves}}
        if not r_curves:
            raise problem("/market/curves", "market: at least one curve is required")
        for key, value in extra.items():
            if key == "options":
                if not isinstance(value, dict):
                    raise problem("/market/options", "options must be an object")
                pricing["options"] = copy.deepcopy(value)
                continue
            if not isinstance(value, list):
                raise problem(f"/market/{key}", f"{key} must be a list")
            lst = _section(pricing, key, create=True)
            assert lst is not None
            lst.extend(copy.deepcopy(value))
        notes.append(
            f"market assembled: {len(r_curves)} curve(s) {[c.get('id') for c in r_curves]}, "
            f"{len(merged)} index(es) {[i.get('id') for i in merged]}"
            + (f", extra sections {sorted(extra)}" if extra else "")
        )
    if calendar_overrides is not None:
        existing = pricing.get("calendar_overrides")
        if existing is not None and existing != calendar_overrides:
            raise problem(
                "/calendar_overrides",
                "the market already carries calendar_overrides that differ from the argument",
            )
        pricing["calendar_overrides"] = calendar_overrides
        notes.append("pricing.calendar_overrides set from the argument")
    return pricing, notes


def require_curve(pricing: dict[str, Any], curve_id: str, role: str) -> None:
    if find_item(pricing, "curves", curve_id) is None:
        raise problem(
            f"/{role}",
            f"{role} {curve_id!r} is not a curve of the market; "
            f"curves: {ids_in(pricing, 'curves')}",
        )


def require_index(pricing: dict[str, Any], index_id: str, role: str) -> None:
    if find_item(pricing, "indices", index_id) is None:
        raise problem(
            f"/{role}",
            f"{role}: index {index_id!r} is not defined in the market "
            f"(pricing.rates.indices has {ids_in(pricing, 'indices')}); pass index_id or add "
            "the IndexDef to the market",
        )
