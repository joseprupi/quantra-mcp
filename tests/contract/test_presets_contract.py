"""Presets and goldens against the vendored spec.

Every preset helper block must carry every field the corresponding engine
helper schema lists, except the per-quote fields (rate / quote_id / tenor and
the FRA / future quote-shape fields), the index reference the builder fills
from the preset's index, the dual-curve ``deps`` wiring and the engine-0.6.0
deprecated ``fixed_leg_day_counter`` (accepted-but-ignored; the gold SOFR
example omits it). Every golden request validates as a ``/bootstrap-curves``
body.
"""

from __future__ import annotations

import json

import pytest

from quantra_mcp.presets.registry import POINT_TYPE, curve_preset_ids, get_preset
from quantra_mcp.schema.loader import load_spec
from quantra_mcp.schema.validate import validate_component, validate_request
from tests.strips import GOLDEN_DIR

QUOTE_FIELDS = {"rate", "quote_id", "tenor", "deps"}
EXCLUDED: dict[str, set[str]] = {
    "deposit": set(),
    "fra": {"months_to_start", "months_to_end"},
    "future": {"future_start_date", "futures_price"},
    "swap": {"float_index"},
    "ois": {"overnight_index", "fixed_leg_day_counter"},
}


@pytest.mark.parametrize("preset_id", curve_preset_ids())
def test_helper_blocks_cover_every_schema_field(preset_id: str) -> None:
    spec = load_spec()
    preset = get_preset(preset_id)
    for helper_type in preset.helpers.available:
        block = preset.helpers.block(helper_type)
        assert block is not None
        have = set(block.model_dump())
        schema_fields = set(spec.schema(POINT_TYPE[helper_type])["properties"])
        expected = schema_fields - QUOTE_FIELDS - EXCLUDED[helper_type]
        assert have == expected, (preset_id, helper_type, have ^ expected)


@pytest.mark.parametrize("preset_id", curve_preset_ids())
def test_preset_index_is_a_valid_indexdef(preset_id: str) -> None:
    from quantra_mcp.builders.curves import index_def

    assert validate_component("IndexDef", index_def(get_preset(preset_id))) == []


def test_every_golden_validates_against_the_spec() -> None:
    goldens = sorted(p for p in GOLDEN_DIR.glob("*.json") if p.name != "strips.json")
    assert len(goldens) >= len(curve_preset_ids()) + 1, [g.name for g in goldens]
    for path in goldens:
        body = json.loads(path.read_text())
        assert validate_request("/bootstrap-curves", body) == [], path.name
        for curve in body["pricing"]["rates"]["curves"]:
            assert validate_component("TermStructure", curve) == [], (path.name, curve["id"])
        for ix in body["pricing"]["rates"]["indices"]:
            assert validate_component("IndexDef", ix) == [], (path.name, ix["id"])
        for q in body["queries"]:
            assert validate_component("CurveQuerySpec", q) == [], (path.name, q["curve_id"])
