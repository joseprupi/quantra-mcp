import pytest

from quantra_mcp.schema import enums_generated
from quantra_mcp.schema.loader import SpecError, load_spec, normalize_endpoint, pin


def test_pin_matches_spec_version() -> None:
    p = pin()
    assert p.tag == "v0.7.0"
    assert len(p.sha) == 40
    assert load_spec().api_version == p.version == "0.7.0"


def test_24_post_endpoints_with_summaries() -> None:
    spec = load_spec()
    assert len(spec.endpoints) == 24
    assert all(e.summary for e in spec.endpoints)
    assert "/price-ois-swap" in spec.endpoint_paths
    assert "/health" not in spec.endpoint_paths


@pytest.mark.parametrize("raw", ["/price-ois-swap", "price-ois-swap", " /price-ois-swap/ "])
def test_normalize_endpoint(raw: str) -> None:
    assert normalize_endpoint(raw) == "/price-ois-swap"
    assert load_spec().endpoint(raw).path == "/price-ois-swap"


def test_unknown_endpoint_lists_valid_ones() -> None:
    with pytest.raises(SpecError) as exc:
        load_spec().endpoint("/price-everything")
    assert "/price-ois-swap" in str(exc.value)
    assert not str(exc.value).startswith('"')


def test_required_and_top_level_fields() -> None:
    spec = load_spec()
    assert spec.required_fields("/calendar-holidays") == ["start_date", "end_date"]
    assert spec.top_level_properties("/price-ois-swap") == ["pricing", "swaps", "include_flows"]


def test_enum_lookup_short_and_full_names() -> None:
    spec = load_spec()
    assert "Calendar" in spec.enum_names
    assert spec.enum_values("Calendar") == spec.enum_values("quantra_enums_Calendar")
    assert "TARGET" in spec.enum_values("Calendar")
    with pytest.raises(SpecError) as exc:
        spec.enum_values("Nope")
    assert "Calendar" in str(exc.value)


def test_resolve_depth_leaves_markers_and_inlines_enums() -> None:
    spec = load_spec()
    shallow = spec.resolve(spec.request_schema("/price-ois-swap"), 0)
    assert shallow["properties"]["pricing"] == {"$ref": "Pricing", "unresolved": True}
    deep = spec.resolve(spec.request_schema("/calendar-advance"), 1)
    assert deep["properties"]["calendar"]["enum"] == spec.enum_values("Calendar")
    override = deep["properties"]["calendar_overrides"]["items"]
    assert override["title"] == "CalendarOverride"
    assert override["properties"]["calendar"]["enum"][0] == "Argentina"


def test_resolve_does_not_mutate_spec() -> None:
    spec = load_spec()
    before = spec.request_schema("/price-ois-swap")
    spec.resolve(before, 5)
    assert spec.request_schema("/price-ois-swap")["properties"]["pricing"] == {
        "$ref": "#/components/schemas/quantra_Pricing"
    }


def test_generated_enums_match_spec() -> None:
    spec = load_spec()
    assert sorted(enums_generated.__all__) == sorted(spec.enum_names)
    for name in spec.enum_names:
        cls = getattr(enums_generated, name)
        assert [m.value for m in cls] == spec.enum_values(name), name
    assert enums_generated.CdsIsdaNumericalFix.None_.value == "None"
