"""Operator rule: nothing vendor- or example-specific ships. No vendored example
carries a vendor reference, and the instructions and prompts carry the ABSOLUTE
rule on market data (M5.3), stated verbatim, with no illustrative / demo loophole."""

from __future__ import annotations

import json
import re
from pathlib import Path

from quantra_mcp import examples_catalog as cat
from quantra_mcp import prompts, server

EXAMPLES = Path(__file__).resolve().parents[2] / "src" / "quantra_mcp" / "examples"
VENDOR_RE = re.compile(r"bbg|bloomberg|swpm|refinitiv|markit|icvs", re.IGNORECASE)

ABSOLUTE_RULE = (
    "Never type, estimate, recall or invent market data (quotes, discount factors, zero "
    "rates, vols, fixings). Not as a placeholder, not as a test run, not labelled as "
    "approximate. Market data for a user's trade exists only when the user has pasted or "
    "dictated it in this conversation. Until then: say what is needed, in paste-able form, "
    "and stop. Do not call any curve-building or pricing tool."
)

RULE_PHRASES = (
    ABSOLUTE_RULE,
    "requires a market_data_source declaration",
    "request-SHAPE references only",
    "MUST come from the user",
    "Never reuse an example's market data for the user's trade",
    'never say "the engine already ships this trade"',
    "ask for it in paste-able form and stop there",
)

#: the retired loophole: no instruction or prompt may offer it again
LOOPHOLE_RE = re.compile(r"illustrative|demo price|placeholder price", re.IGNORECASE)


def test_no_vendored_example_carries_a_vendor_reference() -> None:
    index = cat.load_index()
    for row in index["examples"]:
        for key in ("name", "file", "title", "description", "source"):
            value = str(row.get(key) or "")
            assert not VENDOR_RE.search(value), (row["name"], key, value)
    for path in EXAMPLES.rglob("*.json"):
        rel = path.relative_to(EXAMPLES).as_posix()
        assert not VENDOR_RE.search(rel), rel
    excluded = index["excluded_vendor_specific"]
    assert {e["path"] for e in excluded} == {"examples/data/swaption_ois_bbg_zerorate_request.json"}
    assert all(VENDOR_RE.search(e["matched"]) for e in excluded)
    assert index["count"] == 222
    assert not (EXAMPLES / "misc" / "swaption_ois_bbg_zerorate_request.json").exists()


def test_preset_provenance_cites_only_vendored_fixtures() -> None:
    presets = Path(__file__).resolve().parents[2] / "src" / "quantra_mcp" / "presets"
    vendored = {r["name"] for r in cat.rows()}
    for path in presets.glob("*.json"):
        text = path.read_text()
        assert not VENDOR_RE.search(text), path.name
        for name in re.findall(r"examples/data/(?:[a-z_]+/)?([a-z0-9_]+)\.json", text):
            assert name in vendored, (path.name, name)


def _flat(text: str) -> str:
    return " ".join(text.split())


ALL_PROMPTS = (
    prompts.HOLIDAY_CHECK,
    prompts.RECONCILE_EXTERNAL_PRICE,
    prompts.EXPLORE_EXAMPLES,
    prompts._price_a_swap(),
    prompts._price_a_swap("USD", "ois"),
    prompts._bootstrap_from_strip(),
    prompts._price_from_screen(),
    prompts._price_from_screen("swaption"),
)


def test_instructions_and_prompts_state_the_absolute_rule() -> None:
    for phrase in RULE_PHRASES:
        assert phrase in _flat(server.INSTRUCTIONS), phrase
        assert phrase in _flat(prompts.VOICE), phrase
    assert server.INSTRUCTIONS.count("ABSOLUTE RULE on market data") == 1
    # every prompt built on VOICE carries it, verbatim
    for text in ALL_PROMPTS[:2] + ALL_PROMPTS[3:]:
        assert "ABSOLUTE RULE on market data" in text
        assert ABSOLUTE_RULE in _flat(text)


def test_no_illustrative_or_demo_price_loophole_anywhere() -> None:
    for text in (server.INSTRUCTIONS, prompts.VOICE, *ALL_PROMPTS):
        assert not LOOPHOLE_RE.search(text), LOOPHOLE_RE.search(text)
        assert "HARD RULE" not in text  # the old, weaker rule is gone
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text()
    assert not LOOPHOLE_RE.search(readme)
    assert "never type, estimate, recall or invent market data" in readme


def test_instructions_carry_no_vendor_claim() -> None:
    # and the instructions / prompts carry no vendor-specific claim (neutral examples only)
    neutral_ok = re.compile(
        r"Bloomberg SWPM screen|Bloomberg SWPM,|Bloomberg ICVS ?/ ?SWDF", re.IGNORECASE
    )
    for text in (server.INSTRUCTIONS, prompts.VOICE, prompts.RECONCILE_EXTERNAL_PRICE):
        stripped = neutral_ok.sub("", text)
        assert not re.search(r"bbg|swpm|swpm-ov|10,?359|10,?585", stripped, re.IGNORECASE), text[
            :80
        ]
    json.loads((EXAMPLES / "INDEX.json").read_text())  # well-formed
