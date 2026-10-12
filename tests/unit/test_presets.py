import json

import pytest

from quantra_mcp.presets.registry import (
    HELPER_TYPES,
    MARKET_STANDARD,
    PRESETS_DIR,
    TRADE_TYPES,
    PresetError,
    curve_preset_ids,
    get_preset,
    list_presets,
    preset_ids,
)

EXPECTED = [
    "DE_BUND",
    "EUR_CDS",
    "EUR_EQUITY",
    "EUR_ESTR_OIS",
    "EUR_EURIBOR_3M",
    "EUR_EURIBOR_6M",
    "EUR_FIXED_BOND",
    "EUR_HICP",
    "GBP_SONIA_OIS",
    "GBP_SONIA_SWAP",
    "UK_GILT",
    "USD_SOFR_OIS",
    "US_TREASURY",
]
CURVE_PRESETS = [
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
    assert curve_preset_ids() == CURVE_PRESETS
    for r in rows:
        assert r["provenance"] and r["description"] and (r["helpers"] or r["trades"])
        assert set(r["helpers"]) <= set(HELPER_TYPES)
        assert set(r["trades"]) <= set(TRADE_TYPES)
        if r["id"] in CURVE_PRESETS:
            assert r["index"] and r["curve"] and r["helpers"]
        else:
            assert r["index"] is None and r["curve"] is None and r["helpers"] == []


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
    # the curve side is fully sourced; the only field-level entry is the trade block's
    assert set(p.field_provenance) == {
        "trades.ois_swap",
        "trades.ois_swap.schedule.date_generation_rule",
        "trades.swaption",
    }


def test_swap_trade_blocks_default_to_a_short_front_stub() -> None:
    """M5.4: OIS / vanilla swap schedules generate Backward (short front stub) by default;
    the provenance is a market-standard note, not an in-repo fixture (those use Forward)."""
    for pid, block in [
        ("USD_SOFR_OIS", "ois_swap"),
        ("EUR_ESTR_OIS", "ois_swap"),
        ("EUR_EURIBOR_6M", "vanilla_swap"),
        ("EUR_EURIBOR_3M", "vanilla_swap"),
    ]:
        p = get_preset(pid)
        conv = p.trade_block(block)
        assert conv.schedule.date_generation_rule == "Backward", (pid, block)
        prov = p.provenance_of(f"trades.{block}.schedule.date_generation_rule")
        assert prov.startswith("market standard: short front stub (Backward generation)"), pid
        assert "15-month USD SOFR OIS, 2023-08-17" in prov
        # the rest of the block still cites its fixture
        assert "engine fixture" in p.provenance_of(f"trades.{block}.schedule.calendar")


def test_market_standard_fields_carry_the_exact_provenance_string() -> None:
    # ESTR / SONIA OIS helper conventions and the EUR fixed legs are not in any in-repo source
    for pid, dotted in [
        ("EUR_ESTR_OIS", "helpers.ois.payment_lag"),
        ("GBP_SONIA_OIS", "helpers.ois.settlement_days"),
        ("EUR_EURIBOR_6M", "helpers.swap.sw_fixed_leg_frequency"),
        ("EUR_EURIBOR_3M", "helpers.future.future_months"),
    ]:
        assert get_preset(pid).provenance_of(dotted).startswith(MARKET_STANDARD), (pid, dotted)
    # Euribor end-of-month is now sourced from QuantLib (euriborEOM), with a permalink
    eom = get_preset("EUR_EURIBOR_6M").provenance_of("index.end_of_month")
    assert eom.startswith("QuantLib 1.41 euriborEOM") and "/blob/v1.41/" in eom
    # a sourced field falls back to the preset-level provenance (the QuantLib index class)
    assert "QuantLib::Sonia" in get_preset("GBP_SONIA_SWAP").provenance_of("index.calendar")


def test_index_provenance_cites_quantlib_and_no_private_path() -> None:
    """Every curve preset's index conventions cite the QuantLib index class at the
    engine's QuantLib version with a GitHub permalink; no preset mentions the private
    platform repository or a filesystem path."""
    expected = {
        "USD_SOFR_OIS": "QuantLib::Sofr",
        "EUR_ESTR_OIS": "QuantLib::Estr",
        "GBP_SONIA_OIS": "QuantLib::Sonia",
        "GBP_SONIA_SWAP": "QuantLib::Sonia",
        "EUR_EURIBOR_6M": "QuantLib::Euribor6M",
        "EUR_EURIBOR_3M": "QuantLib::Euribor3M",
    }
    for pid, cls in expected.items():
        prov = get_preset(pid).provenance_of("index.fixing_days")
        assert cls in prov and "https://github.com/lballabio/QuantLib/blob/v1.41/ql/" in prov, pid
    for pid in preset_ids():
        text = (PRESETS_DIR / f"{pid}.json").read_text()
        assert "quantra-backend" not in text and "/root/" not in text, pid
        assert "seed_demo_entities" not in text and "_translator" not in text, pid


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
