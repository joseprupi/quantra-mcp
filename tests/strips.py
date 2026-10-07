"""The canonical strips (tests/golden/strips.json) as builder inputs, shared by the suites."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quantra_mcp.builders import curves as cb
from quantra_mcp.presets.registry import get_preset

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
STRIPS: dict[str, Any] = {
    k: v for k, v in json.loads((GOLDEN_DIR / "strips.json").read_text()).items() if k[0] != "_"
}


def quotes_for(preset_id: str) -> list[cb.CurveQuote]:
    return [cb.CurveQuote.model_validate(q) for q in STRIPS[preset_id]["quotes"]]


def bootstrap_body_for(preset_id: str) -> tuple[dict[str, Any], cb.BuiltCurve]:
    """The full ``/bootstrap-curves`` body for a preset's canonical strip (DF + ZERO grid)."""
    strip = STRIPS[preset_id]
    preset = get_preset(preset_id)
    built = cb.build_curve(preset_id, preset, quotes_for(preset_id), strip["reference_date"])
    query = cb.build_query(
        preset_id,
        ["DF", "ZERO"],
        strip["grid"],
        None,
        preset.index.calendar,
        preset.index.business_day_convention,
    )
    body = cb.bootstrap_request(
        [built.curve], built.indices, strip["reference_date"], [query.query]
    )
    return body, built
