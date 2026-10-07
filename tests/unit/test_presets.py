import json

import pytest

from quantra_mcp.presets.registry import (
    HELPER_TYPES,
    MARKET_STANDARD,
    PRESETS_DIR,
    PresetError,
    get_preset,
    list_presets,
    preset_ids,
)

EXPECTED = [
    "EUR_ESTR_OIS",
    "EUR_EURIBOR_3M",
    "EUR_EURIBOR_6M",
    "GBP_SONIA_OIS",
    "GBP_SONIA_SWAP",
    "USD_SOFR_OIS",
]


def test_registry_lists_the_v1_set() -> None:
    assert preset_ids() == EXPECTED
    rows = list_presets()
    assert [r["id"] for r in rows] == EXPECTED
    for r in rows:
        assert r["provenance"] and r["description"] and r["helpers"]
        assert set(r["helpers"]) <= set(HELPER_TYPES)


def test_unknown_preset_lists_available() -> None:
    with pytest.raises(PresetError, match="USD_SOFR_OIS"):
        get_preset("UST_BOND")


def test_sofr_preset_matches_the_gold_example_conventions() -> None:
    p = get_preset("USD_SOFR_OIS")
    assert p.index.id == "USD_SOFR" and p.index.calendar == "UnitedStatesGovernmentBond"
    assert p.helpers.ois is not None
    assert p.helpers.ois.payment_lag == 2 and p.helpers.ois.settlement_days == 2
    assert p.helpers.ois.averaging_method == "Compound"
    assert p.curve.model_dump(mode="json") == {
        "day_counter": "Actual365Fixed",
        "interpolator": "LogLinear",
        "bootstrap_trait": "Discount",
    }
    assert p.field_provenance == {}  # fully sourced


def test_market_standard_fields_carry_the_exact_provenance_string() -> None:
    # ESTR / SONIA OIS helper conventions and the EUR fixed legs are not in any in-repo source
    for pid, dotted in [
        ("EUR_ESTR_OIS", "helpers.ois.payment_lag"),
        ("GBP_SONIA_OIS", "helpers.ois.settlement_days"),
        ("EUR_EURIBOR_6M", "helpers.swap.sw_fixed_leg_frequency"),
        ("EUR_EURIBOR_3M", "helpers.future.future_months"),
        ("EUR_EURIBOR_6M", "index.end_of_month"),
    ]:
        assert get_preset(pid).provenance_of(dotted).startswith(MARKET_STANDARD), (pid, dotted)
    # a sourced field falls back to the preset-level provenance
    assert "seed_demo_entities" in get_preset("GBP_SONIA_SWAP").provenance_of(
        "helpers.swap.calendar"
    )


def test_as_data_round_trips_the_file() -> None:
    for pid in preset_ids():
        on_disk = json.loads((PRESETS_DIR / f"{pid}.json").read_text())
        assert get_preset(pid).as_data() == on_disk


def test_field_provenance_keys_point_at_real_fields() -> None:
    for pid in preset_ids():
        data = get_preset(pid).as_data()
        for dotted in data["field_provenance"]:
            node = data
            for part in dotted.split("."):
                assert part in node, (pid, dotted)
                node = node[part]
