"""Builders -> golden request JSON per preset (tests/golden/*.json) + local rules.

Regenerate the goldens after an intentional change with
``QUANTRA_REGEN_GOLDENS=1 uv run pytest tests/unit/test_builders.py``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from quantra_mcp.builders import curves as cb
from quantra_mcp.errors import LocalValidationError
from quantra_mcp.presets.registry import get_preset, preset_ids
from quantra_mcp.resources import load_example
from tests.strips import GOLDEN_DIR, STRIPS, bootstrap_body_for

REGEN = os.environ.get("QUANTRA_REGEN_GOLDENS") == "1"


def _golden(name: str, body: dict[str, Any]) -> None:
    path: Path = GOLDEN_DIR / f"{name}.json"
    text = json.dumps(body, indent=2) + "\n"
    if REGEN:
        path.write_text(text)
    assert path.is_file(), f"missing golden {path.name}; run with QUANTRA_REGEN_GOLDENS=1"
    assert json.loads(path.read_text()) == body, f"golden {path.name} differs"


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize("preset_id", preset_ids())
def test_build_curve_matches_golden(preset_id: str) -> None:
    assert preset_id in STRIPS, f"no canonical strip for {preset_id} in strips.json"
    body, built = bootstrap_body_for(preset_id)
    _golden(f"{preset_id}.bootstrap", body)
    assert built.preset == preset_id
    assert body["pricing"]["rates"]["indices"][0]["id"] == get_preset(preset_id).index.id
    # every note names its source; every helper field used is noted
    assert all(
        ("preset " + preset_id) in n or n.startswith(("quotes", "swap", "ois")) for n in built.notes
    )
    for pt in body["pricing"]["rates"]["curves"][0]["points"]:
        kind = {
            v: k
            for k, v in __import__(
                "quantra_mcp.presets.registry", fromlist=["POINT_TYPE"]
            ).POINT_TYPE.items()
        }[pt["point_type"]]
        for field_name in pt["point"]:
            if field_name in (
                "rate",
                "tenor",
                "float_index",
                "overnight_index",
                "months_to_start",
                "months_to_end",
                "future_start_date",
                "futures_price",
            ):
                continue
            assert any(n.startswith(f"{kind}.{field_name}=") for n in built.notes), (
                kind,
                field_name,
            )


def test_sofr_build_is_json_equal_to_the_gold_example() -> None:
    body, _ = bootstrap_body_for("USD_SOFR_OIS")
    assert _canon(body) == _canon(load_example("sofr-bootstrap-request"))


def test_points_are_sorted_by_maturity_and_noted() -> None:
    p = get_preset("USD_SOFR_OIS")
    quotes = [
        cb.CurveQuote(type="ois", tenor="2Y", rate=0.044),
        cb.CurveQuote(type="ois", tenor="6M", rate=0.052),
        cb.CurveQuote(type="ois", tenor={"n": 18, "unit": "Months"}, rate=0.047),
    ]
    built = cb.build_curve("c", p, quotes, "2025-01-15")
    tenors = [pt["point"]["tenor"] for pt in built.curve["points"]]
    assert tenors == [
        {"n": 6, "unit": "Months"},
        {"n": 18, "unit": "Months"},
        {"n": 2, "unit": "Years"},
    ]
    assert "quotes were re-ordered by maturity" in built.notes


def test_overrides_are_applied_and_noted() -> None:
    p = get_preset("EUR_ESTR_OIS")
    built = cb.build_curve(
        "c",
        p,
        [cb.CurveQuote(type="ois", tenor="1Y", rate=0.03)],
        "2025-01-15",
        trait="ZeroRate",
        interpolator="Linear",
        day_counter="Actual360",
    )
    assert built.curve["bootstrap_trait"] == "ZeroRate"
    assert built.curve["interpolator"] == "Linear"
    assert built.curve["day_counter"] == "Actual360"
    assert (
        "curve.bootstrap_trait='ZeroRate' (explicit override of preset EUR_ESTR_OIS)" in built.notes
    )
    # market-standard provenance surfaces in the notes
    assert any("payment_lag=1" in n and "market standard (ISDA/CCP)" in n for n in built.notes)


@pytest.mark.parametrize(
    ("quotes", "path", "fragment"),
    [
        ([], "/quotes", "at least one"),
        ([{"type": "ois", "tenor": "1Y"}], "/quotes/0/rate", "rate is required"),
        ([{"type": "ois", "rate": 0.03}], "/quotes/0/tenor", "tenor is required"),
        ([{"type": "ois", "tenor": "0D", "rate": 0.03}], "/quotes/0/tenor", "> 0"),
        (
            [
                {"type": "ois", "tenor": "1Y", "rate": 0.03},
                {"type": "ois", "tenor": "12M", "rate": 0.03},
            ],
            "/quotes/1",
            "duplicates quotes[0]",
        ),
        (
            [{"type": "deposit", "tenor": "6M", "rate": 0.03}],
            "/quotes/0/type",
            "no 'deposit' helper",
        ),
        (
            [{"type": "ois", "tenor": "1Y", "rate": 0.03, "price": 97.0}],
            "/quotes/0/price",
            "does not apply",
        ),
    ],
)
def test_build_curve_local_errors(quotes: list[dict[str, Any]], path: str, fragment: str) -> None:
    p = get_preset("USD_SOFR_OIS")
    with pytest.raises(LocalValidationError) as exc:
        cb.build_curve("c", p, [cb.CurveQuote.model_validate(q) for q in quotes], "2025-01-15")
    assert exc.value.problems[0]["path"] == path, exc.value.error
    assert fragment in exc.value.error


def test_fra_and_future_rules() -> None:
    p = get_preset("EUR_EURIBOR_3M")
    with pytest.raises(LocalValidationError, match="months_to_start < months_to_end"):
        cb.build_curve(
            "c",
            p,
            [cb.CurveQuote(type="fra", months_to_start=6, months_to_end=3, rate=0.03)],
            "2025-01-15",
        )
    with pytest.raises(LocalValidationError, match="or rate is required"):
        cb.build_curve(
            "c", p, [cb.CurveQuote(type="future", future_start_date="2025-03-19")], "2025-01-15"
        )
    with pytest.raises(LocalValidationError, match="not both"):
        cb.build_curve(
            "c",
            p,
            [cb.CurveQuote(type="future", future_start_date="2025-03-19", price=97.0, rate=0.03)],
            "2025-01-15",
        )
    built = cb.build_curve(
        "c",
        p,
        [
            cb.CurveQuote(
                type="future",
                future_start_date="2025-03-19",
                rate=0.029,
                convexity_adjustment=0.0001,
            )
        ],
        "2025-01-15",
    )
    pt = built.curve["points"][0]["point"]
    assert (
        pt["rate"] == 0.029 and "futures_price" not in pt and pt["convexity_adjustment"] == 0.0001
    )
    assert pt["future_months"] == 3


def test_value_curve_trait_is_rejected_for_strips() -> None:
    p = get_preset("USD_SOFR_OIS")
    with pytest.raises(LocalValidationError, match="value-curve family"):
        cb.build_curve(
            "c",
            p,
            [cb.CurveQuote(type="ois", tenor="1Y", rate=0.03)],
            "2025-01-15",
            trait="InterpolatedZero",
        )


def test_bad_reference_date() -> None:
    p = get_preset("USD_SOFR_OIS")
    with pytest.raises(LocalValidationError) as exc:
        cb.build_curve("c", p, [cb.CurveQuote(type="ois", tenor="1Y", rate=0.03)], "2025-02-30")
    assert exc.value.problems[0]["path"] == "/reference_date"


# --- value curves -----------------------------------------------------------


def _vp(**kw: Any) -> cb.ValuePoint:
    return cb.ValuePoint(**kw)


def test_value_curves_golden() -> None:
    p = get_preset("USD_SOFR_OIS")
    disc = cb.build_value_curve(
        "VC_DF",
        "discount",
        [_vp(date="2025-01-15", value=1.0), _vp(tenor="1Y", value=0.96)],
        "2025-01-15",
        p,
    )
    zero = cb.build_value_curve(
        "VC_ZERO",
        "zero",
        [
            _vp(date="2025-01-15", value=0.04),
            _vp(tenor="1Y", value=0.042),
            _vp(date="2030-01-15", value=0.045),
        ],
        "2025-01-15",
        conventions=cb.ValueCurveConventions(
            day_counter="Actual365Fixed",
            calendar="TARGET",
            business_day_convention="ModifiedFollowing",
        ),
        compounding="Compounded",
        frequency="Semiannual",
    )
    fwd = cb.build_value_curve(
        "VC_FWD",
        "forward",
        [_vp(date="2025-01-15", value=0.03), _vp(tenor="2Y", value=0.032)],
        "2025-01-15",
        p,
        interpolator="BackwardFlat",
    )
    q = cb.build_query(
        "VC_FWD",
        ["DF", "ZERO", "FWD"],
        range_grid=cb.RangeGrid(
            start_date="2025-01-15", end_date="2027-01-15", step_number=6, step_time_unit="Months"
        ),
        fwd=cb.FwdQuery(
            forward_type="Period", tenor="3M", compounding="Simple", frequency="Annual"
        ),
    )
    body = cb.bootstrap_request(
        [disc.curve, zero.curve, fwd.curve],
        [],
        "2025-01-15",
        [
            cb.build_query(
                "VC_DF",
                ["DF", "ZERO"],
                ["6M", "1Y"],
                None,
                "UnitedStatesGovernmentBond",
                "ModifiedFollowing",
            ).query,
            cb.build_query("VC_ZERO", ["DF"], ["1Y"], None, "TARGET", "ModifiedFollowing").query,
            q.query,
        ],
    )
    _golden("value_curves.bootstrap", body)
    assert (
        disc.curve["bootstrap_trait"] == "InterpolatedDiscount"
        and disc.curve["interpolator"] == "LogLinear"
    )
    assert (
        zero.curve["bootstrap_trait"] == "InterpolatedZero"
        and zero.curve["interpolator"] == "Linear"
    )
    assert zero.curve["points"][1]["point"] == {
        "tenor": {"n": 1, "unit": "Years"},
        "calendar": "TARGET",
        "business_day_convention": "ModifiedFollowing",
        "zero_rate": 0.042,
        "compounding": "Compounded",
        "frequency": "Semiannual",
    }
    assert (
        fwd.curve["bootstrap_trait"] == "InterpolatedFwd"
        and fwd.curve["interpolator"] == "BackwardFlat"
    )
    assert disc.preset == "USD_SOFR_OIS" and zero.preset is None and disc.indices == []
    assert "checked: first discount point is 1.0 at the reference date" in disc.notes
    assert any("default for kind='discount'" in n for n in disc.notes)
    assert q.query["grid"]["grid_type"] == "RangeGrid" and q.query["fwd"]["tenor"] == {
        "n": 3,
        "unit": "Months",
    }


@pytest.mark.parametrize(
    ("kind", "points", "kw", "fragment"),
    [
        (
            "discount",
            [{"tenor": "1Y", "value": 0.96}],
            {},
            "first point must be the reference date",
        ),
        ("discount", [{"date": "2025-01-15", "value": 0.999}], {}, "exactly 1.0"),
        (
            "discount",
            [{"date": "2025-01-15", "value": 1.0, "tenor": "0D"}],
            {},
            "exactly one of date or tenor",
        ),
        (
            "forward",
            [{"date": "2025-01-15", "value": 0.03}],
            {"interpolator": "LogLinear"},
            "InterpolatedFwd supports",
        ),
        (
            "discount",
            [{"date": "2025-01-15", "value": 1.0}],
            {"compounding": "Simple"},
            "kind='zero' only",
        ),
        ("zero", [], {}, "at least one point"),
    ],
)
def test_value_curve_local_rules(
    kind: str, points: list[dict[str, Any]], kw: dict[str, Any], fragment: str
) -> None:
    p = get_preset("USD_SOFR_OIS")
    with pytest.raises(LocalValidationError, match=fragment):
        cb.build_value_curve(
            "c", kind, [cb.ValuePoint.model_validate(x) for x in points], "2025-01-15", p, **kw
        )  # type: ignore[arg-type]


def test_value_curve_needs_exactly_one_convention_source() -> None:
    p = get_preset("USD_SOFR_OIS")
    conv = cb.ValueCurveConventions(
        day_counter="Actual360", calendar="TARGET", business_day_convention="Following"
    )
    pts = [_vp(date="2025-01-15", value=0.03)]
    with pytest.raises(LocalValidationError, match="exactly one of preset or conventions"):
        cb.build_value_curve("c", "zero", pts, "2025-01-15", None, None)
    with pytest.raises(LocalValidationError, match="exactly one of preset or conventions"):
        cb.build_value_curve("c", "zero", pts, "2025-01-15", p, conv)
    # (no metacharacters above; the parametrized rules test uses plain fragments)


# --- queries ----------------------------------------------------------------


def test_build_query_defaults_and_errors() -> None:
    q = cb.build_query("c", ["DF", "ZERO"], ["1Y"], None, "TARGET", "ModifiedFollowing")
    assert q.query["zero"] == {
        "use_curve_day_counter": True,
        "compounding": "Continuous",
        "frequency": "Annual",
    }
    assert any(n.startswith("zero=") and "default" in n for n in q.notes)
    assert (
        cb.build_query("c", ["DF"], ["1Y"], None, "TARGET", "Following").query.get("zero") is None
    )
    with pytest.raises(LocalValidationError, match="needs calendar"):
        cb.build_query("c", ["DF"], ["1Y"])
    with pytest.raises(LocalValidationError, match="FWD needs fwd options"):
        cb.build_query("c", ["FWD"], ["1Y"], None, "TARGET", "Following")
    with pytest.raises(LocalValidationError, match="exactly one of tenors"):
        cb.build_query("c", ["DF"])
    with pytest.raises(LocalValidationError, match="not in measures"):
        cb.build_query("c", ["DF"], ["1Y"], None, "TARGET", "Following", zero=cb.ZeroQuery())
    with pytest.raises(LocalValidationError, match=r"fwd\.tenor"):
        cb.build_query(
            "c",
            ["FWD"],
            ["1Y"],
            None,
            "TARGET",
            "Following",
            fwd=cb.FwdQuery(forward_type="Period", compounding="Simple", frequency="Annual"),
        )
    inst = cb.build_query(
        "c",
        ["FWD"],
        ["1Y"],
        None,
        "TARGET",
        "Following",
        fwd=cb.FwdQuery(
            forward_type="Instantaneous",
            instantaneous_eps_number=1,
            instantaneous_eps_time_unit="Days",
            compounding="Continuous",
            frequency="Annual",
        ),
    )
    assert (
        inst.query["fwd"]["instantaneous_eps_time_unit"] == "Days"
        and "tenor" not in inst.query["fwd"]
    )


# --- request assembly -------------------------------------------------------


def test_merge_indices() -> None:
    a = {"id": "X", "name": "x"}
    merged, notes = cb.merge_indices([a, dict(a), {"id": "Y", "name": "y"}])
    assert [m["id"] for m in merged] == ["X", "Y"] and "sent once" in notes[0]
    with pytest.raises(LocalValidationError, match="different definitions"):
        cb.merge_indices([a, {"id": "X", "name": "other"}])


def test_bootstrap_summary_reads_default_measure_as_df() -> None:
    resp = {
        "results": [
            {
                "id": "c",
                "grid_dates": ["2025-02-18", "2075-01-15"],
                "pillar_dates": ["2025-01-15", "2075-01-15"],
                "series": [{"values": [0.99, 0.26]}, {"measure": "ZERO", "values": [0.05, 0.03]}],
            },
            {"id": "d", "error": {"message": "boom"}},
        ]
    }
    assert cb.bootstrap_summary(resp) == {
        "curves": [
            {
                "id": "c",
                "pillars": 2,
                "first_grid_date": "2025-02-18",
                "last_grid_date": "2075-01-15",
                "measures": ["DF", "ZERO"],
            },
            {
                "id": "d",
                "pillars": None,
                "first_grid_date": None,
                "last_grid_date": None,
                "measures": [],
                "error": {"message": "boom"},
            },
        ]
    }
    assert cb.bootstrap_summary({"nope": 1}) is None
