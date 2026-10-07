"""Operator rule: nothing vendor- or example-specific ships. No vendored example
carries a vendor reference, and the instructions and prompts carry the hard rule
on example market data, stated verbatim."""

from __future__ import annotations

import json
import re
from pathlib import Path

from quantra_mcp import examples_catalog as cat
from quantra_mcp import prompts, server

EXAMPLES = Path(__file__).resolve().parents[2] / "src" / "quantra_mcp" / "examples"
VENDOR_RE = re.compile(r"bbg|bloomberg|swpm|refinitiv|markit|icvs", re.IGNORECASE)

RULE_PHRASES = (
    "request-SHAPE references only",
    "MUST come from the user",
    "Never reuse an example's market data for the user's trade",
    'never say "the engine already ships this trade"',
    "ask for it in paste-able form and stop there",
    "label it as illustrative and name the example used",
)


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


def test_instructions_and_prompts_state_the_hard_rule() -> None:
    for phrase in RULE_PHRASES:
        assert phrase in _flat(server.INSTRUCTIONS), phrase
        assert phrase in _flat(prompts.VOICE), phrase
    assert server.INSTRUCTIONS.count("HARD RULE on the shipped examples") == 1
    # every prompt built on VOICE carries it
    for text in (
        prompts.HOLIDAY_CHECK,
        prompts.RECONCILE_EXTERNAL_PRICE,
        prompts._price_a_swap(),
        prompts._bootstrap_from_strip(),
        prompts._price_from_screen(),
    ):
        assert "HARD RULE on the shipped examples" in text
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
