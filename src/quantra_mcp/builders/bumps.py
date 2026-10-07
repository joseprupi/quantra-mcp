"""Quote bumps for the analytics tools: pure edits of an engine ``TermStructure``.

A curve's *pillars* are its actual points, in wire order. Bumping a pillar
adds ``bump_bp / 10_000`` to the point's quoted rate (or spread); a futures
pillar moves its IMM price by ``-bump_bp / 100`` (price = 100 - rate in
percent). That is the only arithmetic here. Pillars whose quote cannot be
shifted by a rate bump without local maths (discount factors, bond clean
prices, FX points) and pillars that carry a ``quote_id`` instead of a value
are rejected with a message that names them.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from quantra_mcp.builders.schedule import problem
from quantra_mcp.builders.tenor import tenor_label
from quantra_mcp.errors import LocalValidationError

BP = 1e-4

#: point_type -> (quote field, scale applied to bump_bp)
_RATE_FIELDS: dict[str, tuple[str, float]] = {
    "DepositHelper": ("rate", BP),
    "FRAHelper": ("rate", BP),
    "SwapHelper": ("rate", BP),
    "OISHelper": ("rate", BP),
    "DatedOISHelper": ("rate", BP),
    "BondHelper": ("rate", BP),
    "TenorBasisSwapHelper": ("spread", BP),
    "CrossCcyBasisHelper": ("spread", BP),
    "ZeroRatePoint": ("zero_rate", BP),
    "ForwardRatePoint": ("forward_rate", BP),
}

_UNBUMPABLE: dict[str, str] = {
    "DiscountFactorPoint": "a discount factor cannot be shifted by a rate bump without local "
    "maths; bump a zero or forward value curve instead",
    "FxSwapHelper": "fx_points are not a rate; this pillar cannot be bumped in bp",
}


@dataclass(frozen=True, slots=True)
class Pillar:
    index: int
    label: str
    point_type: str
    field: str | None  # None when the pillar cannot be bumped
    value: float | None
    reason: str | None = None  # why it cannot be bumped

    def as_result(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "index": self.index,
            "label": self.label,
            "point_type": self.point_type,
            "field": self.field,
            "value": self.value,
        }
        if self.reason:
            out["unbumpable"] = self.reason
        return out


def _raw_label(point_type: str, point: dict[str, Any]) -> str:
    tenor = point.get("tenor")
    if isinstance(tenor, dict) and "n" in tenor and "unit" in tenor:
        try:
            return tenor_label({"n": int(tenor["n"]), "unit": str(tenor["unit"])})
        except (TypeError, ValueError):
            pass
    if point_type == "FRAHelper":
        return f"{point.get('months_to_start')}x{point.get('months_to_end')}"
    if point_type == "FutureHelper":
        return f"FUT {point.get('future_start_date')}"
    if point_type == "DatedOISHelper":
        return f"{point.get('start_date')}/{point.get('end_date')}"
    if point_type == "BondHelper":
        sched = point.get("schedule")
        if isinstance(sched, dict) and sched.get("termination_date"):
            return f"BOND {sched['termination_date']}"
    if isinstance(point.get("date"), str):
        return str(point["date"])
    return f"{point_type}[?]"


def _quote(point_type: str, point: dict[str, Any]) -> tuple[str | None, float | None, str | None]:
    """-> (field, current value, reason-if-unbumpable)."""
    if point_type in _UNBUMPABLE:
        return None, None, _UNBUMPABLE[point_type]
    if point_type == "FutureHelper":
        if isinstance(point.get("futures_price"), int | float):
            return "futures_price", float(point["futures_price"]), None
        if isinstance(point.get("rate"), int | float):
            return "rate", float(point["rate"]), None
    elif point_type == "BondHelper" and isinstance(point.get("price"), int | float):
        return (
            None,
            float(point["price"]),
            "a bond clean price cannot be shifted by a rate bump without local maths",
        )
    elif point_type in _RATE_FIELDS:
        field, _ = _RATE_FIELDS[point_type]
        if isinstance(point.get(field), int | float):
            return field, float(point[field]), None
    else:
        return None, None, f"unknown point_type {point_type!r}"
    if point.get("quote_id") is not None:
        return (
            None,
            None,
            f"carries quote_id {point['quote_id']!r} instead of an inline value; "
            "resolve the quote to a number first",
        )
    return None, None, "has no numeric quote field"


def pillars_of(curve: dict[str, Any]) -> list[Pillar]:
    """The curve's points in wire order, labelled; labels are made unique with ``#i``."""
    points = curve.get("points")
    if not isinstance(points, list):
        return []
    raw: list[tuple[int, str, str, str | None, float | None, str | None]] = []
    for i, wrapper in enumerate(points):
        if not isinstance(wrapper, dict) or not isinstance(wrapper.get("point"), dict):
            raw.append((i, f"[{i}]", "?", None, None, "malformed point wrapper"))
            continue
        ptype = str(wrapper.get("point_type"))
        point = wrapper["point"]
        field, value, reason = _quote(ptype, point)
        raw.append((i, _raw_label(ptype, point), ptype, field, value, reason))
    counts: dict[str, int] = {}
    for _, label, *_rest in raw:
        counts[label] = counts.get(label, 0) + 1
    out: list[Pillar] = []
    for i, label, ptype, field, value, reason in raw:
        unique = label if counts[label] == 1 else f"{label}#{i}"
        out.append(Pillar(i, unique, ptype, field, value, reason))
    return out


def resolve_pillar(pillars: list[Pillar], ref: str | int, where: str) -> Pillar:
    """``ref`` is a label (``"5Y"``) or a 0-based index."""
    if isinstance(ref, bool):
        raise problem(where, f"pillar must be a label or an index, got {ref!r}")
    if isinstance(ref, int):
        if 0 <= ref < len(pillars):
            return pillars[ref]
        raise problem(where, f"pillar index {ref} out of range (0..{len(pillars) - 1})")
    for p in pillars:
        if p.label == ref:
            return p
    raise problem(
        where,
        f"unknown pillar {ref!r}; this curve's pillars: {[p.label for p in pillars]}",
    )


def bump_curve(
    curve: dict[str, Any],
    bump_bp: float,
    pillar_indices: Iterable[int] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Deep-copy ``curve`` with the selected pillars (default: all) bumped by ``bump_bp``.

    -> (bumped curve, [{curve, pillar, point_type, field, from, to, bump_bp}]).
    Raises LocalValidationError if a selected pillar cannot be bumped.
    """
    pillars = pillars_of(curve)
    selected = list(pillar_indices) if pillar_indices is not None else [p.index for p in pillars]
    out = copy.deepcopy(curve)
    edits: list[dict[str, Any]] = []
    cid = curve.get("id")
    for idx in selected:
        p = pillars[idx]
        if p.field is None:
            raise problem(
                f"/curves/{cid}/points/{idx}",
                f"curve {cid!r} pillar {p.label!r} ({p.point_type}): {p.reason}",
            )
        scale = -0.01 if p.field == "futures_price" else BP
        point = out["points"][idx]["point"]
        before = float(point[p.field])
        after = before + bump_bp * scale
        point[p.field] = after
        edits.append(
            {
                "curve": cid,
                "pillar": p.label,
                "point_type": p.point_type,
                "field": p.field,
                "from": before,
                "to": after,
                "bump_bp": bump_bp,
            }
        )
    return out, edits


def replace_quote(
    curve: dict[str, Any], pillar_index: int, value: float
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Deep-copy ``curve`` with one pillar's quote replaced by ``value``."""
    pillars = pillars_of(curve)
    p = pillars[pillar_index]
    cid = curve.get("id")
    if p.field is None:
        raise problem(
            f"/curves/{cid}/points/{pillar_index}",
            f"curve {cid!r} pillar {p.label!r} ({p.point_type}): {p.reason}",
        )
    out = copy.deepcopy(curve)
    point = out["points"][pillar_index]["point"]
    before = float(point[p.field])
    point[p.field] = float(value)
    return out, {
        "curve": cid,
        "pillar": p.label,
        "point_type": p.point_type,
        "field": p.field,
        "from": before,
        "to": float(value),
    }


__all__ = [
    "BP",
    "LocalValidationError",
    "Pillar",
    "bump_curve",
    "pillars_of",
    "replace_quote",
    "resolve_pillar",
]
