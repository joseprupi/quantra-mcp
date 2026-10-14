"""Every shipped example (221 engine fixtures + 2 blog examples), every product
golden and every calendar-tool request validates against the vendored spec."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from quantra_mcp import examples_catalog as cat
from quantra_mcp.resources import EXAMPLE_ENDPOINTS, example_names, load_example
from quantra_mcp.schema.enums_generated import BusinessDayConvention, Calendar, TimeUnit
from quantra_mcp.schema.validate import validate_request
from quantra_mcp.tools.calendar import (
    CalendarOverride,
    advance_request,
    business_days_request,
    holidays_request,
)

PRODUCT_GOLDENS = Path(__file__).resolve().parents[1] / "golden" / "products"

OVERRIDES = [
    CalendarOverride(
        calendar=Calendar.TARGET, added_holidays=["2024-06-14"], removed_holidays=["2024-05-01"]
    ),
    CalendarOverride(calendar=Calendar.UnitedKingdom, added_holidays=["2024-07-05"]),
]


def test_blog_examples_have_an_endpoint_and_validate() -> None:
    names = example_names()
    assert names == ["sofr-bootstrap-request", "sofr-ois-swap-request"]
    for name in names:
        endpoint = EXAMPLE_ENDPOINTS[name]
        assert validate_request(endpoint, load_example(name)) == [], name
        assert cat.find(name)["endpoint"] == endpoint


@pytest.mark.parametrize("name", [r["name"] for r in cat.rows()])
def test_every_vendored_example_validates_against_its_endpoint(name: str) -> None:
    row = cat.find(name)
    body = cat.load_body(row)
    assert validate_request(str(row["endpoint"]), body) == [], name


def test_product_goldens_validate() -> None:
    goldens = sorted(PRODUCT_GOLDENS.glob("*.json"))
    assert len(goldens) >= 14, [g.name for g in goldens]
    endpoint_of = {
        "price_vanilla_swap": "/price-vanilla-swap",
        "price_ois_swap": "/price-ois-swap",
        "price_ois_swap.blog": "/price-ois-swap",
        "price_fixed_rate_bond": "/price-fixed-rate-bond",
        "price_floating_rate_bond": "/price-floating-rate-bond",
        "price_zero_coupon_bond": "/price-zero-coupon-bond",
        "price_fra": "/price-fra",
        "price_cap_floor": "/price-cap-floor",
        "price_swaption": "/price-swaption",
        "price_swaption.swaption_ois_request": "/price-swaption",
        "price_cds": "/price-cds",
        "price_equity_option": "/price-equity-option",
        "price_zc_inflation_swap": "/price-zero-coupon-inflation-swap",
        "price_yoy_inflation_swap": "/price-year-on-year-inflation-swap",
        "price_yoy_inflation_cap_floor": "/price-year-on-year-inflation-cap-floor",
    }
    for path in goldens:
        body = json.loads(path.read_text())
        assert validate_request(endpoint_of[path.stem], body) == [], path.name


def test_examples_match_the_published_files() -> None:
    """The bootstrap example is byte-identical to the published file. The OIS pricing
    example was published in the pre-0.10.0 swap shape; ours must equal the engine's own
    converter (tools/migrate_swap_requests at the pinned tag) applied to the published
    file. Only meaningful on the box with the website and engine checkouts."""
    import hashlib
    import importlib.util
    import subprocess
    import tempfile

    from quantra_mcp.resources import EXAMPLES_DIR
    from quantra_mcp.schema.loader import pin

    published = Path("/root/quantraweb/public/blog/data")
    engine = Path("/root/quantra_refractor/quantraserver")
    if not published.is_dir() or not engine.is_dir():
        return
    src = subprocess.run(
        [
            "git",
            "-C",
            str(engine),
            "show",
            f"{pin().tag}:tools/migrate_swap_requests/migrate_swap_requests.py",
        ],
        check=True,
        capture_output=True,
    ).stdout
    with tempfile.TemporaryDirectory() as d:
        conv_path = Path(d) / "migrate_swap_requests.py"
        conv_path.write_bytes(src)
        spec = importlib.util.spec_from_file_location("migrate_swap_requests", conv_path)
        assert spec is not None and spec.loader is not None
        conv = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(conv)
    for name in example_names():
        ours_bytes = (EXAMPLES_DIR / f"{name}.json").read_bytes()
        theirs_bytes = (published / f"{name}.json").read_bytes()
        theirs = json.loads(theirs_bytes)
        try:
            converted = conv.convert_request(theirs)
        except conv.NotASwapRequest:
            ours = hashlib.sha256(ours_bytes).hexdigest()
            assert ours == hashlib.sha256(theirs_bytes).hexdigest(), name
            continue
        assert json.loads(ours_bytes) == converted, name


def _cases() -> list[tuple[str, dict[str, Any]]]:
    return [
        ("/calendar-holidays", holidays_request(Calendar.TARGET, "2024-04-29", "2024-06-21")),
        (
            "/calendar-holidays",
            holidays_request(Calendar.TARGET, "2024-04-29", "2024-06-21", True, OVERRIDES),
        ),
        (
            "/calendar-business-days",
            business_days_request(Calendar.UnitedStates, "2024-05-01", "2024-05-31"),
        ),
        (
            "/calendar-business-days",
            business_days_request(
                Calendar.Japan, "2024-05-01", "2024-05-31", False, False, OVERRIDES
            ),
        ),
        (
            "/calendar-advance",
            advance_request(
                Calendar.TARGET, "2024-05-01", 2, TimeUnit.Days, BusinessDayConvention.Following
            ),
        ),
        (
            "/calendar-advance",
            advance_request(
                Calendar.UnitedKingdom,
                "2024-01-31",
                -6,
                TimeUnit.Months,
                BusinessDayConvention.ModifiedFollowing,
                True,
                OVERRIDES,
            ),
        ),
    ]


def test_every_calendar_tool_request_validates() -> None:
    for endpoint, body in _cases():
        assert validate_request(endpoint, body) == [], (endpoint, body)
