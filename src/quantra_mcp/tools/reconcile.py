"""Tier 6: reconciling an external number against the engine, generically.

Two tools, none of them vendor- or example-specific:

* (``explain_method`` lives in ``tools/explain.py``; the two tools here point
  at its topics per metric.)
* ``compare_results(external, quantra)``: a side-by-side table of the user's
  numbers against a pricing result. Pure presentation: ``abs_diff = quantra -
  external`` and ``rel_diff = abs_diff / |external|``, with the inputs shown,
  the label mapping shown, unknown labels listed as unmapped, and per metric
  the methodology topic to consult.
* ``reprice_with(result_or_request, changes)``: take a previous result's echoed
  request (or an explicit ``{endpoint, body}``), apply explicit field changes
  (a value, or ``bump_bp`` on a numeric field), reprice, and return both
  results, the JSON diff of the two requests and the numeric differences of
  the first priced item (subtraction only, inputs shown). This is the
  primitive that turns "the difference is probably X" into a demonstration.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict, Field, model_validator

from quantra_mcp import methodology
from quantra_mcp.backend.base import Backend
from quantra_mcp.errors import LocalValidationError
from quantra_mcp.schema.loader import SpecError, load_spec, normalize_endpoint
from quantra_mcp.schema.validate import validate_request
from quantra_mcp.tools._market_source import (
    MARKET_DATA_SOURCES,
    MarketDataSource,
    check_source,
    stamp,
)
from quantra_mcp.tools._result import ToolResult, local_error_result, new_request_id, run_post

# --------------------------------------------------------------------------
# compare_results
# --------------------------------------------------------------------------

#: Normalised external label -> candidate response fields, in preference order.
#: A label that normalises to a response field name maps to it directly; this
#: table only adds synonyms.
LABEL_SYNONYMS: dict[str, list[str]] = {
    "pv": ["npv"],
    "premium": ["npv"],
    "spotpremium": ["npv"],
    "price": ["npv", "clean_price"],
    "value": ["npv"],
    "marketvalue": ["npv"],
    "presentvalue": ["npv"],
    "mtm": ["npv"],
    "pv01": ["dv01"],
    "bpv": ["dv01"],
    "fairrate": ["fair_rate", "atm_forward", "forward_rate"],
    "parrate": ["fair_rate", "atm_forward", "forward_rate"],
    "swaprate": ["fair_rate", "atm_forward"],
    "atm": ["atm_forward", "fair_rate"],
    "atmrate": ["atm_forward", "fair_rate"],
    "atmforward": ["atm_forward", "fair_rate"],
    "forward": ["atm_forward", "forward_rate", "fair_rate"],
    "forwardrate": ["forward_rate", "atm_forward", "fair_rate"],
    "fwd": ["forward_rate", "atm_forward", "fair_rate"],
    "parspread": ["fair_spread"],
    "spread": ["fair_spread"],
    "accrued": ["accrued_amount"],
    "accruedinterest": ["accrued_amount"],
    "ytm": ["yield"],
    "yieldtomaturity": ["yield"],
    "impliedvol": ["implied_volatility"],
    "vol": ["implied_volatility", "used_volatility"],
    "volatility": ["implied_volatility", "used_volatility"],
    "duration": ["modified_duration"],
    "moddur": ["modified_duration"],
    "fixedleg": ["fixed_leg_npv"],
    "floatleg": ["floating_leg_npv", "overnight_leg_npv"],
    "floatinglegnpv": ["floating_leg_npv", "overnight_leg_npv"],
    "floatlegnpv": ["floating_leg_npv", "overnight_leg_npv"],
    "protectionlegnpv": ["default_leg_npv"],
    "protectionleg": ["default_leg_npv"],
    "premiumleg": ["premium_leg_npv"],
    "cleanpx": ["clean_price"],
    "dirtypx": ["dirty_price"],
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm(label: str) -> str:
    return _NON_ALNUM.sub("", label.lower())


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _response_of(quantra: Any) -> Any:
    """A tool result carries the engine body under ``response``; a bare body is used as is."""
    if (
        isinstance(quantra, dict)
        and "response" in quantra
        and ("ok" in quantra or "endpoint" in quantra)
    ):
        return quantra["response"]
    return quantra


def first_item(response: Any) -> tuple[str, dict[str, Any]] | None:
    """The first priced item: the first list-of-objects field (``swaps[0]``,
    ``swaptions[0]``, ``bonds[0]``, ...), else the response object itself."""
    if not isinstance(response, dict):
        return None
    for key, value in response.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return f"{key}[0]", value[0]
    return "", response


def _find_field(obj: Any, field: str, prefix: str = "") -> tuple[str, Any] | None:
    """Depth-first search for ``field`` through nested objects (lists are not entered)."""
    if not isinstance(obj, dict):
        return None
    if field in obj:
        return (f"{prefix}.{field}" if prefix else field), obj[field]
    for k, v in obj.items():
        if isinstance(v, dict):
            hit = _find_field(v, field, f"{prefix}.{k}" if prefix else k)
            if hit is not None:
                return hit
    return None


def _all_keys(obj: Any, out: set[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _all_keys(v, out)
    elif isinstance(obj, list) and obj:
        _all_keys(obj[0], out)


def compare_results_impl(external: dict[str, Any], quantra: Any) -> dict[str, Any]:
    response = _response_of(quantra)
    located = first_item(response)
    if located is None:
        return {
            "ok": False,
            "error": "quantra must be a pricing tool result (with `response`) or an engine "
            "response object",
        }
    item_path, item = located
    keys: set[str] = set(methodology.load_index()["metric_topics"])  # the engine's metric names
    _all_keys(item, keys)
    _all_keys(response, keys)
    by_norm = {_norm(k): k for k in sorted(keys)}

    rows: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []
    mapping: dict[str, list[str]] = {}
    for label, ext in external.items():
        if not _is_num(ext):
            unmapped.append({"label": label, "external": ext, "reason": "not a number"})
            continue
        n = _norm(label)
        candidates: list[str] = []
        if n in by_norm:
            candidates.append(by_norm[n])
        candidates += [f for f in LABEL_SYNONYMS.get(n, []) if f not in candidates]
        mapping[label] = candidates
        if not candidates:
            unmapped.append(
                {
                    "label": label,
                    "external": ext,
                    "reason": "no known response field for this label",
                }
            )
            continue
        hit = None
        field = None
        for field in candidates:
            hit = _find_field(item, field) or _find_field(response, field)
            if hit is not None:
                break
        if hit is None:
            unmapped.append(
                {
                    "label": label,
                    "external": ext,
                    "reason": f"none of {candidates} is present in the result "
                    "(the product's response has no such field, or the request flag that "
                    "produces it is off)",
                }
            )
            continue
        path, value = hit
        assert field is not None
        row: dict[str, Any] = {
            "label": label,
            "mapped_to": field,
            "quantra_path": (f"{item_path}.{path}" if item_path and path in item else path),
            "external": ext,
            "quantra": value,
            "topic": methodology.topic_for_metric(field),
        }
        if row["topic"]:
            row["topic_uri"] = f"{methodology.URI_PREFIX}/{row['topic']}"
        if _is_num(value):
            row["abs_diff"] = value - ext
            row["rel_diff"] = (value - ext) / abs(ext) if ext != 0 else None
        else:
            row["abs_diff"] = None
            row["rel_diff"] = None
            row["note"] = "quantra value is not numeric"
        rows.append(row)
    return {
        "ok": True,
        "item": item_path or "(response)",
        "rows": rows,
        "unmapped": unmapped,
        "mapping": mapping,
        "arithmetic": "abs_diff = quantra - external; rel_diff = abs_diff / |external| "
        "(null when external is 0). Nothing else is computed.",
        "topics": methodology.topics(),
    }


# --------------------------------------------------------------------------
# reprice_with
# --------------------------------------------------------------------------


class FieldChange(BaseModel):
    """One edit to the request: set ``path`` to ``value``, or add ``bump_bp / 10000``
    to the numeric field at ``path``."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        description=(
            "Dotted / indexed path into the request body, e.g. "
            "`swaptions[0].swaption.settlement_method`, "
            "`pricing.rates.curves[0].points[3].point.rate`, `pricing.as_of_date`."
        )
    )
    value: Any = Field(default=None, description="New value (any JSON). A missing key is created.")
    bump_bp: float | None = Field(
        default=None, description="Add bump_bp / 10000 to the numeric field at `path`."
    )

    @model_validator(mode="after")
    def _one_of(self) -> FieldChange:
        has_value = "value" in self.model_fields_set
        if has_value == (self.bump_bp is not None):
            raise ValueError("give exactly one of `value` or `bump_bp`")
        return self


_SEG_RE = re.compile(r"([^.\[\]]+)|\[(-?\d+)\]")


def parse_path(path: str) -> list[str | int]:
    text = path.strip()
    if text.startswith("$."):
        text = text[2:]
    elif text.startswith("$"):
        text = text[1:]
    if text.startswith("/"):  # JSON pointer
        return [int(p) if p.lstrip("-").isdigit() else p for p in text.split("/")[1:]]
    segs: list[str | int] = []
    pos = 0
    while pos < len(text):
        if text[pos] == ".":
            pos += 1
            continue
        m = _SEG_RE.match(text, pos)
        if not m:
            raise LocalValidationError(
                f"cannot parse path {path!r} at offset {pos}",
                [{"path": "/changes/path", "message": "use a.b[0].c or a.b.0.c"}],
            )
        segs.append(int(m.group(2)) if m.group(2) is not None else m.group(1))
        pos = m.end()
    if not segs:
        raise LocalValidationError("empty path", [{"path": "/changes/path", "message": "empty"}])
    return segs


def _descend(root: Any, segs: list[str | int], path: str) -> tuple[Any, str | int]:
    """Walk to the parent of the last segment; return (parent, last_key)."""
    node = root
    for i, seg in enumerate(segs[:-1]):
        if isinstance(node, list):
            idx = (
                seg
                if isinstance(seg, int)
                else (int(seg) if str(seg).lstrip("-").isdigit() else None)
            )
            if idx is None or not -len(node) <= idx < len(node):
                raise LocalValidationError(
                    f"{path}: list index {seg!r} out of range (len {len(node)})",
                    [{"path": "/changes/path", "message": "index out of range"}],
                )
            node = node[idx]
        elif isinstance(node, dict):
            key = str(seg)
            if key not in node:
                raise LocalValidationError(
                    f"{path}: key {key!r} not found at `{'.'.join(map(str, segs[:i]))}`"
                    f" (available: {sorted(node)})",
                    [{"path": "/changes/path", "message": "intermediate key missing"}],
                )
            node = node[key]
        else:
            raise LocalValidationError(
                f"{path}: cannot descend into a {type(node).__name__} at segment {seg!r}",
                [{"path": "/changes/path", "message": "not an object or list"}],
            )
    last = segs[-1]
    if isinstance(node, list):
        idx = (
            last
            if isinstance(last, int)
            else (int(last) if str(last).lstrip("-").isdigit() else None)
        )
        if idx is None or not -len(node) <= idx < len(node):
            raise LocalValidationError(
                f"{path}: list index {last!r} out of range (len {len(node)})",
                [{"path": "/changes/path", "message": "index out of range"}],
            )
        return node, idx
    if isinstance(node, dict):
        return node, str(last)
    raise LocalValidationError(
        f"{path}: parent is a {type(node).__name__}, not an object or list",
        [{"path": "/changes/path", "message": "not an object or list"}],
    )


def apply_change(body: dict[str, Any], change: FieldChange) -> dict[str, Any]:
    """Apply one change in place; return what was done (path, before, after)."""
    segs = parse_path(change.path)
    parent, key = _descend(body, segs, change.path)
    if isinstance(parent, dict):
        present = key in parent
        before: Any = parent.get(key)
    else:
        present = True
        before = parent[key]
    if change.bump_bp is not None:
        if not present or isinstance(before, bool) or not isinstance(before, (int, float)):
            raise LocalValidationError(
                f"{change.path}: bump_bp needs an existing numeric field (found "
                f"{'absent' if not present else type(before).__name__})",
                [{"path": "/changes/bump_bp", "message": "field is not numeric"}],
            )
        after: Any = before + change.bump_bp / 10_000.0
        kind = f"bump {change.bump_bp:+g}bp"
    else:
        after = copy.deepcopy(change.value)
        kind = "set"
    parent[key] = after
    return {
        "path": change.path,
        "kind": kind,
        "before": before if present else None,
        "before_present": present,
        "after": after,
    }


def request_diff(a: Any, b: Any, prefix: str = "") -> list[dict[str, Any]]:
    """Every leaf that differs between two JSON values, as ``{path, before, after}``."""
    out: list[dict[str, Any]] = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            p = f"{prefix}.{k}" if prefix else k
            if k not in a:
                out.append({"path": p, "before": None, "after": b[k], "added": True})
            elif k not in b:
                out.append({"path": p, "before": a[k], "after": None, "removed": True})
            else:
                out += request_diff(a[k], b[k], p)
    elif isinstance(a, list) and isinstance(b, list):
        for i in range(min(len(a), len(b))):
            out += request_diff(a[i], b[i], f"{prefix}[{i}]")
        for i in range(min(len(a), len(b)), len(a)):
            out.append({"path": f"{prefix}[{i}]", "before": a[i], "after": None, "removed": True})
        for i in range(min(len(a), len(b)), len(b)):
            out.append({"path": f"{prefix}[{i}]", "before": None, "after": b[i], "added": True})
    elif a != b:
        out.append({"path": prefix, "before": a, "after": b})
    return out


def numeric_differences(base: Any, changed: Any) -> dict[str, Any]:
    """Top-level numeric fields of the first priced item: base, changed, changed - base."""
    lb, lc = first_item(base), first_item(changed)
    if lb is None or lc is None:
        return {"item": None, "fields": {}, "note": "no priced item in one of the responses"}
    path_b, item_b = lb
    path_c, item_c = lc
    fields: dict[str, Any] = {}
    for k in item_b:
        if k in item_c and _is_num(item_b[k]) and _is_num(item_c[k]):
            fields[k] = {
                "base": item_b[k],
                "changed": item_c[k],
                "difference": item_c[k] - item_b[k],
            }
    only_base = sorted(k for k in item_b if k not in item_c and _is_num(item_b[k]))
    only_changed = sorted(k for k in item_c if k not in item_b and _is_num(item_c[k]))
    out: dict[str, Any] = {
        "item": path_b if path_b == path_c else f"{path_b} vs {path_c}",
        "fields": fields,
        "arithmetic": "difference = changed - base (subtraction only)",
    }
    if only_base or only_changed:
        out["only_in_base"] = only_base
        out["only_in_changed"] = only_changed
    return out


def _source(src: Any) -> tuple[str, dict[str, Any], ToolResult | None]:
    if not isinstance(src, dict):
        raise LocalValidationError(
            "result_or_request must be an object",
            [{"path": "/result_or_request", "message": "expected object"}],
        )
    endpoint = src.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint:
        raise LocalValidationError(
            "result_or_request needs `endpoint` (a previous tool result carries it; an explicit "
            "request is {endpoint, body})",
            [{"path": "/result_or_request/endpoint", "message": "missing"}],
        )
    if isinstance(src.get("request"), dict):
        base_result: ToolResult | None = None
        if src.get("ok") is True and "response" in src:
            base_result = src
        return endpoint, copy.deepcopy(src["request"]), base_result
    if isinstance(src.get("body"), dict):
        return endpoint, copy.deepcopy(src["body"]), None
    raise LocalValidationError(
        "result_or_request must carry `request` (a previous tool result) or `body` (an explicit "
        "engine request object)",
        [{"path": "/result_or_request", "message": "neither request nor body is an object"}],
    )


def _resolve_source(result_or_request: Any, declared: Any) -> str:
    """The market_data_source of a reprice: the one the input result carries (a previous
    tool result was stamped with it), the one declared, or both when they agree."""
    raw = (
        result_or_request.get("market_data_source") if isinstance(result_or_request, dict) else None
    )
    carried: str | None = str(raw) if raw in MARKET_DATA_SOURCES else None
    if declared is None and carried is None:
        raise LocalValidationError(
            "market_data_source is required: the given result_or_request carries no "
            f"market_data_source, so declare one of {list(MARKET_DATA_SOURCES)}; there is no "
            "value for estimated, recalled or placeholder data: if the numbers were not "
            "supplied by the user, do not call this tool, ask for them",
            [{"path": "/market_data_source", "message": "missing"}],
        )
    if declared is not None:
        source = check_source(declared)
        if carried is not None and carried != source:
            raise LocalValidationError(
                f"market_data_source={source!r} differs from the one the given result carries "
                f"({carried!r}); pass the same value or omit the argument",
                [{"path": "/market_data_source", "message": "disagrees with the result"}],
            )
        return source
    assert carried is not None
    return carried


async def reprice_with_impl(
    backend: Backend,
    result_or_request: Any,
    changes: list[FieldChange],
    reprice_base: bool = False,
    validate: bool = True,
    request_id: str | None = None,
    market_data_source: Any = None,
) -> dict[str, Any]:
    rid = request_id or new_request_id()
    spec = load_spec()
    try:
        source = _resolve_source(result_or_request, market_data_source)
        endpoint, base_body, base_result = _source(result_or_request)
        info = spec.endpoint(endpoint)
        if not changes:
            raise LocalValidationError(
                "changes must not be empty", [{"path": "/changes", "message": "empty"}]
            )
        changed_body = copy.deepcopy(base_body)
        applied = [apply_change(changed_body, ch) for ch in changes]
    except SpecError as exc:
        return local_error_result(
            normalize_endpoint(str(result_or_request.get("endpoint", "")))
            if isinstance(result_or_request, dict)
            else "",
            None,
            str(exc),
            request_id=rid,
        )
    except LocalValidationError as exc:
        rejected = local_error_result(
            str(result_or_request.get("endpoint", ""))
            if isinstance(result_or_request, dict)
            else "",
            None,
            exc.error,
            exc.problems,
            request_id=rid,
        )
        if market_data_source in MARKET_DATA_SOURCES:
            stamp(rejected, str(market_data_source))
        return rejected
    if validate:
        problems = validate_request(info.path, changed_body, spec)
        if problems:
            return stamp(
                {
                    **local_error_result(
                        info.path,
                        changed_body,
                        f"the changed request does not match the {spec.api_version} schema "
                        f"for {info.path} ({len(problems)} problem"
                        f"{'s' if len(problems) != 1 else ''}); nothing was sent (pass "
                        "validate=false to send anyway)",
                        [p.as_dict() for p in problems],
                        request_id=rid,
                    ),
                    "changes_applied": applied,
                },
                source,
            )
    notes: list[str] = []
    if market_data_source is None:
        notes.append(f"market_data_source carried from the given result ({source})")
    if base_result is None or reprice_base:
        base_result = await run_post(backend, info.path, base_body, request_id=f"{rid}-base")
        notes.append("base repriced now" + (" (reprice_base=true)" if reprice_base else ""))
        base_source = "repriced"
    else:
        notes.append("base taken from the given result's response (not repriced)")
        base_source = "given result"
    changed_result = await run_post(backend, info.path, changed_body, request_id=rid)
    diff = request_diff(base_body, changed_body)
    ok = bool(base_result.get("ok")) and bool(changed_result.get("ok"))
    out: dict[str, Any] = {
        "ok": ok,
        "endpoint": info.path,
        "changes_applied": applied,
        "request_diff": diff,
        "base_source": base_source,
        "base": base_result,
        "changed": changed_result,
        "notes": notes,
        "engine": changed_result.get("engine"),
    }
    if ok:
        out["differences"] = numeric_differences(
            base_result.get("response"), changed_result.get("response")
        )
    else:
        failed = "changed" if not changed_result.get("ok") else "base"
        bad = changed_result if failed == "changed" else base_result
        out["error"] = f"{failed} request failed: {bad.get('error')}"
        out["status"] = bad.get("status")
    return stamp(out, source)


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------


def register(app: MCPServer, backend: Backend) -> None:
    @app.tool()
    def compare_results(external: dict[str, Any], quantra: dict[str, Any]) -> dict[str, Any]:
        """Put the user's numbers next to the engine's (no engine call, no modelling).

        Args:
            external: ``{label: number}`` as the user quoted them, e.g.
                ``{"NPV": 10359.49, "DV01": 415.5, "fair rate": 0.0337}``. Labels are
                matched case- and punctuation-insensitively to response fields
                (``npv`` / ``premium`` / ``PV`` -> ``npv``; ``DV01`` / ``PV01`` -> ``dv01``;
                ``fair rate`` -> ``fair_rate`` then ``atm_forward``; ``vol`` ->
                ``implied_volatility``, ...). Give the external numbers in the engine's
                units (currency amounts; rates and vols as decimals).
            quantra: a pricing tool result (its ``response`` is used) or the engine
                response object itself. The first priced item is compared.

        Returns ``rows`` = ``[{label, mapped_to, quantra_path, external, quantra,
        abs_diff, rel_diff, topic, topic_uri}]`` with ``abs_diff = quantra - external``
        and ``rel_diff = abs_diff / |external|``; ``unmapped`` for labels with no field;
        ``mapping`` shows the candidates tried. ``topic`` is the methodology page
        (``explain_method``) to consult for that metric.
        """
        return compare_results_impl(external, quantra)

    @app.tool()
    async def reprice_with(
        result_or_request: dict[str, Any],
        changes: list[FieldChange],
        market_data_source: MarketDataSource | None = None,
        reprice_base: bool = False,
        validate: bool = True,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Test a hypothesis: change one or more request fields and reprice.

        Args:
            result_or_request: a previous pricing tool result (its ``endpoint`` and echoed
                ``request`` are used; its ``response`` is the base unless
                ``reprice_base``), or an explicit ``{"endpoint": "/price-swaption",
                "body": {...}}``.
            market_data_source: where the market numbers in the request come from. A previous
                tool result carries its own declaration and it is reused (the argument may be
                omitted or must agree); for an explicit ``{endpoint, body}`` or a result without
                one it is required: ``user_pasted`` (the user pasted or typed the numbers in
                this conversation), ``user_file`` (the user attached a file/screenshot the
                numbers were read from), ``engine_example`` (an engine example's pricing block,
                only when the user explicitly asked to run an example), ``session`` (a market
                previously stored in this session, which itself came from one of the above).
                There is no value for estimated, recalled or placeholder data. If you would have
                to invent numbers, do not call this tool: ask the user for the data.
            changes: ``[{path, value}]`` or ``[{path, bump_bp}]``; ``path`` is dotted /
                indexed into the request body (``swaptions[0].swaption.settlement_method``,
                ``pricing.rates.curves[0].points[2].point.rate``, ``pricing.as_of_date``,
                ``pricing.rates.curves[0].interpolator``). ``bump_bp`` adds
                ``bump_bp / 10000`` to a numeric field.
            reprice_base: also reprice the unchanged request now (default: reuse the
                given result's response).
            validate: check the changed body against the vendored schema before sending.

        Returns ``changes_applied`` (before / after per change), ``request_diff`` (every
        leaf that differs between the two requests), ``base`` and ``changed`` (complete
        uniform results, each replayable from its ``request``) and ``differences``: the
        numeric top-level fields of the first priced item with ``difference = changed -
        base``. Nothing else is computed. The engine's error, if any, is verbatim.
        """
        return await reprice_with_impl(
            backend,
            result_or_request,
            changes,
            reprice_base,
            validate,
            request_id,
            market_data_source,
        )
