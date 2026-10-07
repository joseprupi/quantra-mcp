"""The vendored examples catalog (``examples/INDEX.json`` + the request files).

Every engine fixture at the pinned tag is vendored by ``scripts/pin_engine.py``
into ``examples/<category>/<name>.json``; ``INDEX.json`` carries, per example,
the endpoint, the catalog title / description / reference value (from the
engine's functional manifest and ``CATALOG.md``) and a machine-checkable
``oracle`` when there is one. The two blog examples sit at the top level with
category ``blog``. Names are unique across categories.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from quantra_mcp.errors import LocalValidationError

EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"
INDEX_PATH = EXAMPLES_DIR / "INDEX.json"

#: endpoint -> the product word agents use (``list_examples(product=...)``)
PRODUCT_OF_ENDPOINT: dict[str, str] = {
    "/price-vanilla-swap": "vanilla_swap",
    "/price-ois-swap": "ois_swap",
    "/price-basis-swap": "basis_swap",
    "/price-zero-coupon-swap": "zero_coupon_swap",
    "/price-fixed-rate-bond": "fixed_rate_bond",
    "/price-floating-rate-bond": "floating_rate_bond",
    "/price-zero-coupon-bond": "zero_coupon_bond",
    "/price-callable-fixed-rate-bond": "callable_fixed_rate_bond",
    "/price-fra": "fra",
    "/price-cap-floor": "cap_floor",
    "/price-swaption": "swaption",
    "/price-cds": "cds",
    "/price-equity-option": "equity_option",
    "/price-zero-coupon-inflation-swap": "zc_inflation_swap",
    "/price-year-on-year-inflation-swap": "yoy_inflation_swap",
    "/price-year-on-year-inflation-cap-floor": "yoy_inflation_cap_floor",
    "/bootstrap-curves": "curves",
    "/bootstrap-inflation-curves": "inflation_curves",
    "/sample-vol-surfaces": "vol_sampling",
    "/calibrate-swaption-vol": "sabr_calibration",
    "/calibrate-swaption-model": "hull_white_calibration",
    "/calendar-holidays": "calendar",
    "/calendar-business-days": "calendar",
    "/calendar-advance": "calendar",
}


@lru_cache(maxsize=1)
def load_index() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(INDEX_PATH.read_text())
    return data


def rows() -> list[dict[str, Any]]:
    return list(load_index()["examples"])


def categories() -> list[str]:
    seen: dict[str, int] = {}
    for r in rows():
        seen[r["category"]] = seen.get(r["category"], 0) + 1
    return sorted(seen)


def find(name: str) -> dict[str, Any]:
    for r in rows():
        if r["name"] == name:
            return r
    raise LocalValidationError(
        f"unknown example {name!r}; list_examples() lists the {len(rows())} available names",
        [{"path": "/name", "message": "unknown example"}],
    )


def load_body(row: dict[str, Any]) -> dict[str, Any]:
    path = EXAMPLES_DIR / str(row["file"])
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def summary_row(r: dict[str, Any]) -> dict[str, Any]:
    """The index row without the heavy fields, as served by ``list_examples``."""
    return {
        "name": r["name"],
        "category": r["category"],
        "endpoint": r["endpoint"],
        "product": PRODUCT_OF_ENDPOINT.get(str(r["endpoint"])),
        "title": r.get("title"),
        "reference_text": r.get("reference_text") or None,
        "uri": f"quantra://examples/{r['category']}/{r['name']}",
    }


def list_examples(category: str | None = None, product: str | None = None) -> list[dict[str, Any]]:
    out = []
    for r in rows():
        if category is not None and r["category"] != category:
            continue
        if product is not None and PRODUCT_OF_ENDPOINT.get(str(r["endpoint"])) != product:
            continue
        out.append(summary_row(r))
    return out


def products() -> list[str]:
    return sorted(set(PRODUCT_OF_ENDPOINT.values()))


# --------------------------------------------------------------------------
# oracle checks (used by the live sweep; a comparison, never a computation)
# --------------------------------------------------------------------------


def _primary_number(row: dict[str, Any], response: Any) -> float | None:
    list_key = row.get("list_key")
    if not isinstance(response, dict) or not list_key:
        return None
    items = response.get(list_key)
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return None
    value = items[0].get("npv")
    return float(value) if isinstance(value, int | float) else None


def _find_field(response: Any, name: str) -> float | None:
    if not isinstance(response, dict):
        return None
    value = response.get(name)
    if isinstance(value, int | float):
        return float(value)
    diag = response.get("diagnostics")
    if isinstance(diag, dict):
        cal = diag.get("calibration")
        if isinstance(cal, dict) and isinstance(cal.get(name), int | float):
            return float(cal[name])
        if isinstance(diag.get(name), int | float):
            return float(diag[name])
    return None


def oracle_check(row: dict[str, Any], response: Any) -> dict[str, Any]:
    """Compare an engine response with the example's catalog oracle.

    Returns ``{checked, ok, kind, expected, actual, detail}``; ``checked`` is False
    when the example has no machine-checkable oracle. NPV oracles are CATALOG.md's
    two-decimal values (tolerance 0.01 abs, the engine's own gate); calibration
    fields are printed with 6 significant digits (tolerance max(1e-7, 1e-5 rel)).
    """
    oracle = row.get("oracle")
    if not oracle:
        return {"checked": False, "ok": None, "kind": None, "expected": None, "actual": None}
    kind = oracle["kind"]
    if kind == "npv":
        actual = _primary_number(row, response)
        ok = actual is not None and abs(actual - oracle["value"]) <= oracle["tolerance"]
        return {
            "checked": True,
            "ok": ok,
            "kind": kind,
            "expected": oracle["value"],
            "actual": actual,
        }
    if kind == "count":
        dates = response.get("dates") if isinstance(response, dict) else None
        actual_n = len(dates) if isinstance(dates, list) else None
        return {
            "checked": True,
            "ok": actual_n == oracle["count"],
            "kind": kind,
            "expected": oracle["count"],
            "actual": actual_n,
        }
    if kind == "advanced_date":
        actual_d = response.get("advanced_date") if isinstance(response, dict) else None
        return {
            "checked": True,
            "ok": actual_d == oracle["value"],
            "kind": kind,
            "expected": oracle["value"],
            "actual": actual_d,
        }
    if kind == "series":
        results = response.get("results") if isinstance(response, dict) else None
        first = (
            results[0]
            if isinstance(results, list) and results and isinstance(results[0], dict)
            else {}
        )
        if "vols" in first:
            names = ["vols"]
            points = len(first["vols"]) if isinstance(first.get("vols"), list) else None
        else:
            series = first.get("series") or []
            names = [s.get("measure", "DF") for s in series if isinstance(s, dict)]
            points = (
                len(series[0].get("values", [])) if series and isinstance(series[0], dict) else None
            )
        ok = sorted(names) == sorted(oracle["names"]) and points == oracle["points"]
        return {
            "checked": True,
            "ok": ok,
            "kind": kind,
            "expected": {"names": oracle["names"], "points": oracle["points"]},
            "actual": {"names": names, "points": points},
        }
    if kind == "fields":
        actual_f: dict[str, float | None] = {}
        ok = True
        for name, expected in oracle["values"].items():
            got = _find_field(response, name)
            actual_f[name] = got
            tol = max(float(oracle.get("tolerance") or 1e-7), abs(expected) * 1e-5)
            ok = ok and got is not None and abs(got - expected) <= tol
        return {
            "checked": True,
            "ok": ok,
            "kind": kind,
            "expected": oracle["values"],
            "actual": actual_f,
        }
    if kind == "last_df":
        results = response.get("results") if isinstance(response, dict) else None
        text = None
        if isinstance(results, list) and results:
            series = results[0].get("series") or []
            df = next(
                (s for s in series if isinstance(s, dict) and s.get("measure", "DF") == "DF"), None
            )
            if df and df.get("values"):
                text = repr(df["values"][-1])
        return {
            "checked": True,
            "ok": bool(text and text.startswith(oracle["value_prefix"])),
            "kind": kind,
            "expected": oracle["value_prefix"],
            "actual": text,
        }
    return {"checked": False, "ok": None, "kind": kind, "expected": None, "actual": None}
