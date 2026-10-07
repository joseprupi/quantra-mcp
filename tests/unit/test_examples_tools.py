"""The examples catalog: index integrity, tools, resources and prompts (no engine)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp import Client

from quantra_mcp import examples_catalog as cat
from quantra_mcp.schema.loader import load_spec, pin


def _s(result: Any) -> dict[str, Any]:
    assert result.structured_content is not None, result.content
    return dict(result.structured_content)


def test_index_covers_every_vendored_file_once() -> None:
    index = cat.load_index()
    rows = index["examples"]
    assert index["count"] == len(rows) == 223  # 221 engine fixtures + 2 blog examples
    assert index["engine_tag"] == pin().tag and index["engine_sha"] == pin().sha
    names = [r["name"] for r in rows]
    assert len(set(names)) == len(names)
    files = {str(p.relative_to(cat.EXAMPLES_DIR)) for p in cat.EXAMPLES_DIR.rglob("*.json")}
    files.discard("INDEX.json")
    assert files == {r["file"] for r in rows}
    endpoints = set(load_spec().endpoint_paths)
    for r in rows:
        assert r["endpoint"] in endpoints, r["name"]
        assert r.get("endpoint_source") in ("manifest", "schema-match") or r["category"] == "blog"
    assert all(
        r["expected_status"] == 200
        for r in rows
        if r["name"] != "fixed_rate_bond_beyond_pillar_request"
    )
    beyond = cat.find("fixed_rate_bond_beyond_pillar_request")
    assert beyond["expected_status"] == 422 and "beyond_pillar_error" in beyond["why"]
    assert cat.find("sofr-ois-swap-request")["list_key"] == "swaps"
    cataloged = [r for r in rows if r.get("catalog_id")]
    assert len(cataloged) == 192
    assert sum(1 for r in rows if r["oracle"]) == 194
    assert sum(1 for r in rows if r["category"] == "blog") == 2


def test_catalog_metadata_was_parsed() -> None:
    row = cat.find("irs_eur_5y_payer_ois_discounted_multicurve")
    assert row["endpoint"] == "/price-vanilla-swap" and row["family"] == "IR Swaps"
    assert row["title"].startswith("EUR 5Y payer swap, OIS-discounted")
    assert "ESTR OIS curve" in row["description"]
    assert row["reference_value"] == -47408.49 and row["reference_text"] == "-47,408.49"
    assert row["oracle"] == {"kind": "npv", "value": -47408.49, "tolerance": 0.01}
    assert "multicurve" in row["exercises"]
    sabr = cat.find("sabrcal_eur_2x2_beta_fixed_exact_fit")
    assert sabr["oracle"]["kind"] == "fields" and "overall_rmse" in sabr["oracle"]["values"]
    hol = cat.find("cal_hol_target_2025")
    assert hol["oracle"] == {"kind": "count", "count": 6, "list_key": "dates"}
    adv = cat.find("cal_adv_target_eom_jan31_1m")
    assert adv["oracle"]["kind"] == "advanced_date" and adv["oracle"]["value"] == "2025-02-28"
    misc = cat.find("vanilla_swap_request")
    assert misc["category"] == "misc" and misc["endpoint_source"] == "schema-match"
    assert misc["oracle"] is None


def test_oracle_check_kinds() -> None:
    npv = cat.find("irs_eur_5y_payer_ois_discounted_multicurve")
    assert cat.oracle_check(npv, {"swaps": [{"npv": -47408.4879}]})["ok"] is True
    assert cat.oracle_check(npv, {"swaps": [{"npv": -47408.60}]})["ok"] is False
    assert cat.oracle_check(npv, {"swaps": []})["ok"] is False
    hol = cat.find("cal_hol_target_2025")
    assert cat.oracle_check(hol, {"dates": list(range(6))})["ok"] is True
    ser = cat.find("curves_eur_depo_swap_df_zero_tenor_grid")
    resp = {
        "results": [{"series": [{"values": [1] * 10}, {"measure": "ZERO", "values": [1] * 10}]}]
    }
    assert cat.oracle_check(ser, resp)["ok"] is True
    fields = cat.find("hwcal_eur_constant_normal_sigma_only")
    good = {"hw_a": 0.03, "hw_sigma": 0.00868797698, "rmse": 0.000293306843, "num_helpers": 4}
    assert cat.oracle_check(fields, good)["ok"] is True
    assert cat.oracle_check(fields, {**good, "hw_sigma": 0.009})["ok"] is False
    blog = cat.find("sofr-bootstrap-request")
    assert cat.oracle_check(blog, {"results": [{"series": [{"values": [0.262755579831]}]}]})["ok"]
    none = cat.find("vanilla_swap_request")
    assert cat.oracle_check(none, {})["checked"] is False


async def test_list_and_get_example_tools(app: Any) -> None:
    async with Client(app) as c:
        all_ = _s(await c.call_tool("list_examples", {}))
        bonds = _s(await c.call_tool("list_examples", {"category": "bonds"}))
        swaptions = _s(await c.call_tool("list_examples", {"product": "swaption"}))
        bad = _s(await c.call_tool("list_examples", {"category": "nope"}))
        one = _s(await c.call_tool("get_example", {"name": "frb_eur_5y_at_par_annual_30360"}))
        missing = _s(await c.call_tool("get_example", {"name": "nope"}))
    assert all_["ok"] and all_["count"] == 223 and "ir_swaps" in all_["categories"]
    assert bonds["count"] == 24 and all(r["category"] == "bonds" for r in bonds["examples"])
    assert swaptions["count"] == 13 + 7  # swaption/ folder + 7 misc swaption requests
    assert all(r["endpoint"] == "/price-swaption" for r in swaptions["examples"])
    assert bad["ok"] is False and "categories" in bad["error"]
    assert one["ok"] and one["endpoint"] == "/price-fixed-rate-bond"
    assert one["body"]["bonds"][0]["fixed_rate_bond"]["rate"] == 0.031
    assert one["reference_value"] == 999841.67 and one["oracle"]["kind"] == "npv"
    assert one["source"].startswith("quantraserver v0.7.0")
    assert missing["ok"] is False and "unknown example" in missing["error"]


async def test_example_resources(app: Any) -> None:
    async with Client(app) as c:
        idx = json.loads((await c.read_resource("quantra://examples")).contents[0].text)  # type: ignore[union-attr]
        by_cat = json.loads(
            (await c.read_resource("quantra://examples/cds/cds_eur_5y_buyer_100bp_spread_curve"))
            .contents[0]
            .text  # type: ignore[union-attr]
        )
        by_name = json.loads(
            (await c.read_resource("quantra://examples/cds_eur_5y_buyer_100bp_spread_curve"))
            .contents[0]
            .text  # type: ignore[union-attr]
        )
        blog = json.loads(
            (await c.read_resource("quantra://examples/sofr-ois-swap-request")).contents[0].text  # type: ignore[union-attr]
        )
        cat_list = json.loads((await c.read_resource("quantra://examples/vol")).contents[0].text)  # type: ignore[union-attr]
        catalog = (await c.read_resource("quantra://docs/engine-catalog")).contents[0].text  # type: ignore[union-attr]
        with pytest.raises(Exception, match="not 'bonds'"):
            await c.read_resource("quantra://examples/bonds/cds_eur_5y_buyer_100bp_spread_curve")
    assert idx["count"] == 223 and idx["examples"][0]["uri"].startswith("quantra://examples/blog/")
    assert by_cat["endpoint"] == "/price-cds" and by_cat["body"] == by_name
    assert by_name["cds_list"][0]["cds"]["side"] == "Buyer"
    assert blog["swaps"][0]["ois_swap"]["overnight_leg"]["payment_lag"] == 2
    assert cat_list["category"] == "vol" and len(cat_list["examples"]) == 11
    assert catalog.startswith("# Functional Parity Catalog")


async def test_prompts_are_concrete(app: Any) -> None:
    async with Client(app) as c:
        names = {p.name for p in (await c.list_prompts()).prompts}
        swap = await c.get_prompt("price-a-swap", {"currency": "USD", "kind": "ois"})
        boot = await c.get_prompt("bootstrap-from-strip", {"preset": "EUR_ESTR_OIS"})
        hol = await c.get_prompt("holiday-check", {})
        ex = await c.get_prompt("explore-examples", {})
    assert names == {"price-a-swap", "bootstrap-from-strip", "holiday-check", "explore-examples"}
    text = swap.messages[0].content.text  # type: ignore[union-attr]
    assert "price_ois_swap(" in text and "build_curve" in text and "`request`" in text
    assert "Never compute a price" in text
    assert "EUR_ESTR_OIS" in boot.messages[0].content.text  # type: ignore[union-attr]
    assert "calendar_overrides" in hol.messages[0].content.text  # type: ignore[union-attr]
    assert "get_example" in ex.messages[0].content.text  # type: ignore[union-attr]
