"""compare_results and reprice_with through the real MCP server with a fake engine
whose NPV is a function of the quotes and the fixed rate it receives."""

from __future__ import annotations

import json
from typing import Any

from mcp import Client

from quantra_mcp.backend.base import BackendResponse
from quantra_mcp.config import Settings
from quantra_mcp.resources import load_example
from quantra_mcp.server import build_server
from quantra_mcp.tools import reconcile
from quantra_mcp.tools.reconcile import FieldChange, apply_change, parse_path, request_diff
from tests.conftest import FakeBackend
from tests.product_cases import product_cases

FIXTURE = "irs_eur_5y_payer_ois_discounted_multicurve"


def _rates(body: dict[str, Any]) -> list[float]:
    return [
        float(w["point"]["rate"])
        for c in body["pricing"]["rates"]["curves"]
        for w in c["points"]
        if "rate" in w["point"]
    ]


class SwapBackend(FakeBackend):
    """npv = -1e6 * sum(curve rates) + 2e8 * fixed rate; payer/receiver flips the sign."""

    def __init__(self) -> None:
        super().__init__()
        self.bodies: list[dict[str, Any]] = []

    async def post(
        self, endpoint: str, body: Any, request_id: str | None = None
    ) -> BackendResponse:
        if endpoint != "/price-vanilla-swap":
            return await super().post(endpoint, body, request_id)
        self.calls.append(("POST", endpoint, body, request_id))
        self.bodies.append(json.loads(json.dumps(body)))
        swap = body["swaps"][0]["vanilla_swap"]
        npv = -1e6 * sum(_rates(body)) + 2e8 * float(swap["fixed_leg"]["rate"])
        if swap["swap_type"] == "Receiver":
            npv = -npv
        return BackendResponse(
            200,
            {
                "swaps": [
                    {
                        "npv": npv,
                        "fair_rate": 0.0311,
                        "fixed_leg_npv": 1.0,
                        "fixed_leg_flows": [],
                        "used_cms_pricer_type": "LinearTsr",
                    }
                ]
            },
            {"x-quantra-api-version": "0.7.0", "x-request-id": request_id or ""},
        )


async def _call(backend: FakeBackend, name: str, args: dict[str, Any]) -> dict[str, Any]:
    app = build_server(Settings(engine_url="http://fake"), backend=backend)
    async with Client(app) as c:
        r = await c.call_tool(name, args)
    assert not r.is_error, r.content
    assert r.structured_content is not None
    return dict(r.structured_content)


# ---------------------------------------------------------------- path helpers


def test_parse_path_forms() -> None:
    assert parse_path("swaps[0].vanilla_swap.fixed_leg.rate") == [
        "swaps",
        0,
        "vanilla_swap",
        "fixed_leg",
        "rate",
    ]
    assert parse_path("pricing.rates.curves.1.points.3.point.rate") == [
        "pricing",
        "rates",
        "curves",
        "1",
        "points",
        "3",
        "point",
        "rate",
    ]
    assert parse_path("/swaps/0/discounting_curve") == ["swaps", 0, "discounting_curve"]
    assert parse_path("$.pricing.as_of_date") == ["pricing", "as_of_date"]


def test_apply_change_set_bump_and_errors() -> None:
    body = {"a": [{"x": 0.03, "s": "P"}], "b": {"c": 1}}
    done = apply_change(body, FieldChange(path="a[0].x", bump_bp=1.0))
    assert body["a"][0]["x"] == 0.03 + 1e-4 and done["kind"] == "bump +1bp"
    assert done["before"] == 0.03 and done["after"] == body["a"][0]["x"]
    done = apply_change(body, FieldChange(path="b.new", value=[1, 2]))
    assert body["b"]["new"] == [1, 2] and done["before_present"] is False
    done = apply_change(body, FieldChange(path="a.0.s", value=None))
    assert body["a"][0]["s"] is None and done["before"] == "P"
    for bad in (
        FieldChange(path="a[0].s", bump_bp=1.0),  # not numeric
        FieldChange(path="a[5].x", value=1),  # index out of range
        FieldChange(path="b.zzz.q", value=1),  # intermediate key missing
        FieldChange(path="b.c.d", value=1),  # descend into a scalar
    ):
        try:
            apply_change(body, bad)
        except Exception as exc:  # LocalValidationError
            assert bad.path.split(".")[0] in str(exc) or "bump_bp" in str(exc)
        else:
            raise AssertionError(bad)


def test_field_change_requires_exactly_one_of_value_or_bump() -> None:
    import pytest

    with pytest.raises(ValueError):
        FieldChange(path="x")
    with pytest.raises(ValueError):
        FieldChange(path="x", value=1, bump_bp=1)
    assert FieldChange(path="x", value=None).bump_bp is None  # explicit null is a value


def test_request_diff_reports_every_leaf() -> None:
    a = {"p": {"q": [1, 2], "r": "x"}, "k": 1}
    b = {"p": {"q": [1, 3, 4], "r": "x"}, "z": True}
    diff = request_diff(a, b)
    paths = {d["path"]: d for d in diff}
    assert paths["p.q[1]"] == {"path": "p.q[1]", "before": 2, "after": 3}
    assert paths["p.q[2]"]["added"] is True
    assert paths["k"]["removed"] is True and paths["z"]["added"] is True
    assert request_diff(a, json.loads(json.dumps(a))) == []


# ---------------------------------------------------------------- compare_results


def _quantra_result() -> dict[str, Any]:
    return {
        "ok": True,
        "endpoint": "/price-swaption",
        "request": {},
        "response": {
            "swaptions": [
                {
                    "npv": 10585.395,
                    "dv01": 430.13,
                    "gamma": 10.94,
                    "vega": 101.07,
                    "atm_forward": 0.033674,
                    "annuity": 8.1,
                    "used_strike_kind": "Absolute",
                }
            ]
        },
    }


async def test_compare_results_table_mapping_and_unmapped(fake_backend: FakeBackend) -> None:
    r = await _call(
        fake_backend,
        "compare_results",
        {
            "external": {
                "Premium": 10359.49,
                "DV01": 415.51,
                "fair rate": 0.0337,
                "Theta": 64.01,
                "Smile skew": 1.0,
                "note": "n/a",
                "Gamma": 0.0,
            },
            "quantra": _quantra_result(),
        },
    )
    assert r["ok"] is True and r["item"] == "swaptions[0]"
    rows = {row["label"]: row for row in r["rows"]}
    prem = rows["Premium"]
    assert prem["mapped_to"] == "npv" and prem["quantra_path"] == "swaptions[0].npv"
    assert prem["external"] == 10359.49 and prem["quantra"] == 10585.395
    assert prem["abs_diff"] == 10585.395 - 10359.49
    assert prem["rel_diff"] == (10585.395 - 10359.49) / 10359.49
    assert prem["topic"] == "npv" and prem["topic_uri"] == "quantra://methodology/npv"
    assert rows["DV01"]["mapped_to"] == "dv01"
    assert rows["DV01"]["topic"] == "greeks-bump-and-reprice"
    fr = rows["fair rate"]
    assert fr["mapped_to"] == "atm_forward" and fr["topic"] == "fair-rate"
    assert r["mapping"]["fair rate"] == ["fair_rate", "atm_forward", "forward_rate"]
    assert rows["Gamma"]["rel_diff"] is None  # external 0 -> no division
    un = {u["label"]: u for u in r["unmapped"]}
    assert "Theta" in un and "none of ['theta']" in un["Theta"]["reason"]
    assert "Smile skew" in un and "no known response field" in un["Smile skew"]["reason"]
    assert un["note"]["reason"] == "not a number"
    assert "abs_diff = quantra - external" in r["arithmetic"]
    assert {t["slug"] for t in r["topics"]} >= {"npv", "theta"}


async def test_compare_results_accepts_a_bare_response_and_rejects_junk(
    fake_backend: FakeBackend,
) -> None:
    r = await _call(
        fake_backend,
        "compare_results",
        {"external": {"npv": 1.0}, "quantra": {"swaps": [{"npv": 2.5}]}},
    )
    assert r["rows"][0]["abs_diff"] == 1.5 and r["rows"][0]["quantra_path"] == "swaps[0].npv"
    r = await _call(fake_backend, "compare_results", {"external": {"npv": 1.0}, "quantra": {}})
    assert r["ok"] is True and r["item"] == "(response)" and r["unmapped"]
    assert reconcile.compare_results_impl({"npv": 1.0}, [1, 2])["ok"] is False


# ---------------------------------------------------------------- reprice_with


async def test_reprice_with_changes_exactly_one_field_and_subtracts() -> None:
    backend = SwapBackend()
    body = load_example(FIXTURE)
    base = await _call(backend, "engine_request", {"endpoint": "/price-vanilla-swap", "body": body})
    assert base["ok"]
    n_calls = len(backend.bodies)
    r = await _call(
        backend,
        "reprice_with",
        {
            "result_or_request": base,
            "market_data_source": "engine_example",
            "changes": [{"path": "swaps[0].vanilla_swap.fixed_leg.rate", "value": 0.035}],
        },
    )
    assert r["ok"] is True and r["endpoint"] == "/price-vanilla-swap"
    assert r["market_data_source"] == "engine_example"
    assert r["notes"][0].startswith("market_data_source=engine_example (declared")
    assert r["base_source"] == "given result" and len(backend.bodies) == n_calls + 1
    assert r["request_diff"] == [
        {"path": "swaps[0].vanilla_swap.fixed_leg.rate", "before": 0.032, "after": 0.035}
    ]
    assert r["changes_applied"][0]["before"] == 0.032 and r["changes_applied"][0]["kind"] == "set"
    assert r["base"]["response"] == base["response"]
    npv_b = base["response"]["swaps"][0]["npv"]
    npv_c = r["changed"]["response"]["swaps"][0]["npv"]
    assert npv_c == npv_b + 2e8 * (0.035 - 0.032)
    d = r["differences"]
    assert d["item"] == "swaps[0]"
    assert d["fields"]["npv"] == {"base": npv_b, "changed": npv_c, "difference": npv_c - npv_b}
    assert d["fields"]["fair_rate"]["difference"] == 0.0
    assert "fixed_leg_flows" not in d["fields"] and "used_cms_pricer_type" not in d["fields"]
    # the changed request really is what was sent
    assert backend.bodies[-1] == r["changed"]["request"]
    assert backend.bodies[-1]["swaps"][0]["vanilla_swap"]["fixed_leg"]["rate"] == 0.035


async def test_reprice_with_bump_bp_on_a_curve_quote_and_reprice_base() -> None:
    backend = SwapBackend()
    body = load_example(FIXTURE)
    r = await _call(
        backend,
        "reprice_with",
        {
            "result_or_request": {"endpoint": "price-vanilla-swap", "body": body},
            "market_data_source": "engine_example",
            "changes": [{"path": "pricing.rates.curves[0].points[0].point.rate", "bump_bp": 1.0}],
            "reprice_base": True,
        },
    )
    assert r["ok"] and r["base_source"] == "repriced" and len(backend.bodies) == 2
    before = body["pricing"]["rates"]["curves"][0]["points"][0]["point"]["rate"]
    assert r["changes_applied"][0]["kind"] == "bump +1bp"
    assert r["changes_applied"][0]["after"] == before + 1e-4
    assert len(r["request_diff"]) == 1
    assert r["differences"]["fields"]["npv"]["difference"] == -1e6 * 1e-4
    # request ids: base and changed are distinct and both carried
    assert backend.calls[-2][3] != backend.calls[-1][3]


async def test_reprice_with_flip_side_and_several_changes() -> None:
    backend = SwapBackend()
    body = load_example(FIXTURE)
    r = await _call(
        backend,
        "reprice_with",
        {
            "result_or_request": {"endpoint": "/price-vanilla-swap", "body": body},
            "market_data_source": "engine_example",
            "changes": [
                {"path": "swaps[0].vanilla_swap.swap_type", "value": "Receiver"},
                {"path": "pricing.as_of_date", "value": "2025-01-16"},
            ],
        },
    )
    assert r["ok"] and len(r["request_diff"]) == 2
    npv_b = r["base"]["response"]["swaps"][0]["npv"]
    assert r["differences"]["fields"]["npv"]["changed"] == -npv_b


async def test_reprice_with_validates_before_sending_and_reports_engine_errors() -> None:
    backend = SwapBackend()
    body = load_example(FIXTURE)
    r = await _call(
        backend,
        "reprice_with",
        {
            "result_or_request": {"endpoint": "/price-vanilla-swap", "body": body},
            "market_data_source": "engine_example",
            "changes": [{"path": "swaps[0].vanilla_swap.swap_type", "value": "Sideways"}],
        },
    )
    assert r["ok"] is False and r["status"] is None and backend.bodies == []
    assert "does not match" in r["error"] and r["problems"]
    assert r["changes_applied"][0]["after"] == "Sideways"
    # a bad path never reaches the engine either
    r = await _call(
        backend,
        "reprice_with",
        {
            "result_or_request": {"endpoint": "/price-vanilla-swap", "body": body},
            "market_data_source": "engine_example",
            "changes": [{"path": "swaps[3].vanilla_swap.swap_type", "value": "Payer"}],
        },
    )
    assert r["ok"] is False and "out of range" in r["error"] and backend.bodies == []
    # unknown endpoint / missing body
    r = await _call(
        backend,
        "reprice_with",
        {
            "result_or_request": {"endpoint": "/nope", "body": body},
            "market_data_source": "engine_example",
            "changes": [{"path": "pricing.as_of_date", "value": "2025-01-16"}],
        },
    )
    assert r["ok"] is False
    r = await _call(
        backend,
        "reprice_with",
        {
            "result_or_request": {"endpoint": "/price-vanilla-swap"},
            "market_data_source": "engine_example",
            "changes": [{"path": "pricing.as_of_date", "value": "2025-01-16"}],
        },
    )
    assert r["ok"] is False and "request" in r["error"]
    # an engine error on the changed request is passed through
    from quantra_mcp.errors import EngineError

    class Failing(SwapBackend):
        async def post(
            self, endpoint: str, body: Any, request_id: str | None = None
        ) -> BackendResponse:
            if body["swaps"][0]["vanilla_swap"]["fixed_leg"]["rate"] > 1:
                raise EngineError(
                    422, "QuantLib error: silly rate", {"error": "QuantLib error: silly rate"}
                )
            return await super().post(endpoint, body, request_id)

    fb = Failing()
    r = await _call(
        fb,
        "reprice_with",
        {
            "result_or_request": {"endpoint": "/price-vanilla-swap", "body": body},
            "market_data_source": "engine_example",
            "changes": [{"path": "swaps[0].vanilla_swap.fixed_leg.rate", "value": 5.0}],
        },
    )
    assert r["ok"] is False and r["status"] == 422 and "silly rate" in r["error"]
    assert r["base"]["ok"] is True and r["changed"]["ok"] is False
    assert "differences" not in r


async def test_reprice_with_market_data_source_is_carried_or_required() -> None:
    """M5.4: a stamped pricing result carries its declaration into the reprice; an
    explicit request (or an unstamped result) must declare one; a disagreeing
    declaration is refused; nothing is sent in either failure."""
    from tests.unit.test_pricing_tools import CANNED

    backend = FakeBackend(CANNED)
    case = next(k for k in product_cases() if k.tool == "price_vanilla_swap")
    priced = await _call(backend, "price_vanilla_swap", case.explicit)
    assert priced["ok"] and priced["market_data_source"] == "engine_example"

    def posts() -> int:
        return sum(1 for c in backend.calls if c[0] == "POST")

    n = posts()
    change = [{"path": "swaps[0].vanilla_swap.fixed_leg.rate", "value": 0.035}]
    carried = await _call(backend, "reprice_with", {"result_or_request": priced, "changes": change})
    assert carried["ok"], carried
    assert carried["market_data_source"] == "engine_example"
    assert any("carried from the given result (engine_example)" in x for x in carried["notes"])
    assert posts() == n + 1
    same = await _call(
        backend,
        "reprice_with",
        {"result_or_request": priced, "changes": change, "market_data_source": "engine_example"},
    )
    assert same["ok"] and same["market_data_source"] == "engine_example"
    clash = await _call(
        backend,
        "reprice_with",
        {"result_or_request": priced, "changes": change, "market_data_source": "user_pasted"},
    )
    assert (
        clash["ok"] is False and "differs from the one the given result carries" in clash["error"]
    )
    missing = await _call(
        backend,
        "reprice_with",
        {
            "result_or_request": {"endpoint": "/price-vanilla-swap", "body": priced["request"]},
            "changes": change,
        },
    )
    assert missing["ok"] is False and missing["status"] is None
    assert "market_data_source is required" in missing["error"]
    assert missing["problems"][0]["path"] == "/market_data_source"
    assert posts() == n + 2  # the clash and the missing declaration sent nothing
