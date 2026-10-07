"""M5.2 live acceptance (skipped unless ``QUANTRA_ENGINE_URL`` is set).

1. ``reprice_with`` on an engine example changes exactly one field of the echoed
   request and reprices 200; both results replay byte-equal; the numeric
   differences are the subtraction of the two engine NPVs.
2. A scripted client runs the ``reconcile-external-price`` prompt's order on a
   user-style pasted discount-factor table: curve_from_pasted_table ->
   price_ois_swap -> compare_results -> explain_method -> reprice_with, and
   reaches a price whose difference to the external number is explained by a
   demonstrated reprice (the pasted curve vs the bootstrapped one).
"""

from __future__ import annotations

from typing import Any

import pytest

from quantra_mcp.resources import load_example
from tests.live.test_live_engine import ENGINE_URL, _call, canonical, live_client, replay
from tests.product_cases import blog_ois_args
from tests.strips import STRIPS

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not ENGINE_URL, reason="QUANTRA_ENGINE_URL not set"),
]

FIXTURE = "irs_eur_5y_payer_ois_discounted_multicurve"
FIXTURE_NPV = -47408.48798885872  # engine catalog reference


async def test_reprice_with_changes_one_field_of_an_engine_example() -> None:
    body = load_example(FIXTURE)
    async with live_client() as client:
        base = await _call(
            client, "engine_request", {"endpoint": "/price-vanilla-swap", "body": body}
        )
        assert base["ok"], base
        assert base["response"]["swaps"][0]["npv"] == FIXTURE_NPV
        r = await _call(
            client,
            "reprice_with",
            {
                "result_or_request": base,
                "changes": [{"path": "swaps[0].vanilla_swap.swap_type", "value": "Receiver"}],
            },
        )
        assert r["ok"], r
        assert r["request_diff"] == [
            {"path": "swaps[0].vanilla_swap.swap_type", "before": "Payer", "after": "Receiver"}
        ]
        assert r["base"]["response"] == base["response"] and r["base_source"] == "given result"
        changed = r["changed"]
        assert (
            changed["ok"]
            and changed["request"]["swaps"][0]["vanilla_swap"]["swap_type"] == "Receiver"
        )
        npv_c = changed["response"]["swaps"][0]["npv"]
        assert npv_c == pytest.approx(-FIXTURE_NPV, rel=1e-12)
        assert r["differences"]["fields"]["npv"] == {
            "base": FIXTURE_NPV,
            "changed": npv_c,
            "difference": npv_c - FIXTURE_NPV,
        }
        assert canonical(await replay(changed["endpoint"], changed["request"])) == canonical(
            changed["response"]
        )
        # a 1bp bump of one curve quote: exactly one leaf differs, prices 200
        r2 = await _call(
            client,
            "reprice_with",
            {
                "result_or_request": base,
                "changes": [
                    {"path": "pricing.rates.curves[0].points[0].point.rate", "bump_bp": 1.0}
                ],
            },
        )
    assert r2["ok"], r2
    assert len(r2["request_diff"]) == 1
    assert r2["request_diff"][0]["path"] == "pricing.rates.curves[0].points[0].point.rate"
    assert r2["request_diff"][0]["after"] == pytest.approx(r2["request_diff"][0]["before"] + 1e-4)
    assert r2["differences"]["fields"]["npv"]["difference"] != 0.0


def _df_table(bs: dict[str, Any], curve_id: str) -> str:
    res = next(x for x in bs["response"]["results"] if x["id"] == curve_id)
    series = next(s for s in res["series"] if s.get("measure", "DF") == "DF")
    return "Date,DF\n" + "\n".join(
        f"{d},{v!r}" for d, v in zip(res["grid_dates"], series["values"], strict=True)
    )


async def test_scripted_client_runs_the_reconcile_prompt_on_a_pasted_df_table() -> None:
    strip = STRIPS["USD_SOFR_OIS"]
    async with live_client() as client:
        prompt = await client.get_prompt("reconcile-external-price", {})
        text = "\n".join(
            m.content.text
            for m in prompt.messages
            if hasattr(m.content, "text")  # type: ignore[union-attr]
        )
        for tool in (
            "curve_from_pasted_table",
            "compare_results",
            "explain_method",
            "reprice_with",
        ):
            assert tool in text, tool
        assert (
            text.index("compare_results")
            < text.index("explain_method")
            < text.index("reprice_with")
        )

        # the "external" system here is the engine itself on the bootstrapped strip, so
        # the difference the prompt must explain is pasted-curve interpolation
        built = await _call(
            client,
            "build_curve",
            {
                "market_data_source": "user_pasted",
                "id": "USD_SOFR_OIS",
                "preset": "USD_SOFR_OIS",
                "quotes": strip["quotes"],
                "reference_date": strip["reference_date"],
            },
        )
        external_run = await _call(client, "price_ois_swap", blog_ois_args(built))
        assert external_run["ok"], external_run
        external_npv = external_run["response"]["swaps"][0]["npv"]

        q = await _call(
            client,
            "build_query",
            {
                "curve_id": "USD_SOFR_OIS",
                "measures": ["DF"],
                "tenors": ["1W", "1M", "3M", "6M", "1Y", "2Y", "3Y", "4Y", "5Y", "6Y", "7Y"],
                "calendar": "UnitedStatesGovernmentBond",
                "business_day_convention": "ModifiedFollowing",
            },
        )
        bs = await _call(
            client, "bootstrap_curve", {"curves": [built], "as_of": "2025-01-15", "queries": [q]}
        )
        assert bs["ok"], bs
        pasted = _df_table(bs, "USD_SOFR_OIS")

        # prompt step: build the market from the pasted table
        vc = await _call(
            client,
            "curve_from_pasted_table",
            {
                "market_data_source": "user_pasted",
                "text": pasted,
                "id": "USD_SOFR_OIS",
                "kind": "discount",
                "preset": "USD_SOFR_OIS",
                "reference_date": strip["reference_date"],
            },
        )
        assert vc["ok"] and vc["unparsed"] == [], vc
        args = blog_ois_args(vc)
        args["market"] = {"curves": [vc], "indices": built["indices"]}
        priced = await _call(client, "price_ois_swap", args)
        assert priced["ok"], priced
        npv = priced["response"]["swaps"][0]["npv"]

        # prompt step: side by side
        cmp = await _call(
            client,
            "compare_results",
            {"external": {"NPV": external_npv}, "quantra": priced},
        )
        row = cmp["rows"][0]
        assert row["mapped_to"] == "npv" and row["quantra"] == npv
        assert row["abs_diff"] == npv - external_npv and row["topic"] == "npv"

        # prompt step: methodology for the metric, then the candidate cause
        npv_page = await _call(client, "explain_method", {"topic": row["topic"]})
        assert npv_page["ok"] and npv_page["citations"]
        vc_page = await _call(client, "explain_method", {"topic": "value-curves"})
        assert "InterpolatedDiscount" in vc_page["markdown"]

        # prompt step: TEST the hypothesis (interpolation between pasted nodes) by
        # repricing the pasted curve with another interpolator; the request changes in
        # exactly that field and the engine answers 200 with a different number
        rp = await _call(
            client,
            "reprice_with",
            {
                "result_or_request": priced,
                "changes": [{"path": "pricing.rates.curves[0].interpolator", "value": "Linear"}],
            },
        )
        assert rp["ok"], rp
        assert rp["request_diff"] == [
            {
                "path": "pricing.rates.curves[0].interpolator",
                "before": "LogLinear",
                "after": "Linear",
            }
        ]
        moved = rp["differences"]["fields"]["npv"]["difference"]
        assert moved != 0.0
        assert canonical(
            await replay(rp["changed"]["endpoint"], rp["changed"]["request"])
        ) == canonical(rp["changed"]["response"])
    # the pasted 11-node curve is not the bootstrapped curve; the gap is real but small
    assert abs(npv - external_npv) < 0.05 * abs(external_npv)
