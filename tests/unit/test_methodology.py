"""The generated methodology pages: every citation points at a real file and a
real line range at the pin, every page is reachable as a resource and through
``explain_method``, and nothing in them is vendor- or example-specific."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from quantra_mcp import methodology
from quantra_mcp.schema.loader import pin

METH_DIR = Path(__file__).resolve().parents[2] / "src" / "quantra_mcp" / "docs" / "methodology"
ENGINE_REPO = Path(os.environ.get("QUANTRA_ENGINE_REPO", "/root/quantra_refractor/quantraserver"))

BRIEF_TOPICS = {
    "npv",
    "fair-rate",
    "greeks-bump-and-reprice",
    "theta",
    "curve-bootstrap",
    "value-curves",
    "settlement-and-cash-settlement",
    "volatility-types",
    "calendars-and-overrides",
    "day-counters-and-compounding",
    "error-codes",
}

_CITE_RE = re.compile(r"`([^`@]+)@(v[\d.]+):L(\d+)-L(\d+)`")


def _index() -> dict[str, Any]:
    return json.loads((METH_DIR / "INDEX.json").read_text())


def test_index_matches_the_pin_and_the_brief_topics() -> None:
    index = _index()
    assert index["engine_tag"] == pin().tag and index["engine_sha"] == pin().sha
    assert {t["slug"] for t in index["topics"]} == BRIEF_TOPICS
    assert set(methodology.slugs()) == BRIEF_TOPICS
    for slug, topic in index["metric_topics"].items():
        assert topic in BRIEF_TOPICS, (slug, topic)
    assert methodology.topic_for_metric("npv") == "npv"
    assert methodology.topic_for_metric("theta") == "theta"
    assert methodology.topic_for_metric("dv01") == "greeks-bump-and-reprice"
    assert methodology.topic_for_metric("not_a_field") is None


def test_every_citation_is_within_a_cited_file_hermetically() -> None:
    """Without the engine repo: the generator recorded every cited file's line count;
    every range must lie inside it and every page must print its own citations."""
    index = _index()
    files: dict[str, int] = index["files"]
    for t in index["topics"]:
        page = (METH_DIR / t["file"]).read_text()
        assert t["citations"], t["slug"]
        printed = {(m.group(1), int(m.group(3)), int(m.group(4))) for m in _CITE_RE.finditer(page)}
        for c in t["citations"]:
            assert c["path"] in files, (t["slug"], c["path"])
            assert 1 <= c["start"] <= c["end"] <= files[c["path"]], (t["slug"], c)
            assert c["ref"] == f"{c['path']}@{index['engine_tag']}:L{c['start']}-L{c['end']}"
            assert (c["path"], c["start"], c["end"]) in printed, (t["slug"], c["ref"])
            assert len(c["sha256"]) == 64
        assert f"## Not documented in engine {index['engine_tag']}" in page
        assert page.startswith(f"# {t['title']}")


@pytest.mark.skipif(
    not (ENGINE_REPO / ".git").exists() and not (ENGINE_REPO / "HEAD").exists(),
    reason="engine repository not available locally (set QUANTRA_ENGINE_REPO)",
)
def test_every_citation_exists_at_the_pin_in_the_engine_repo() -> None:
    """With the engine repo: each cited path exists at the tag, its line count is the
    recorded one, and the excerpt on the page is byte-identical to the file's lines."""
    index = _index()
    tag = index["engine_tag"]
    cache: dict[str, list[str]] = {}
    for path, n in index["files"].items():
        out = subprocess.run(
            ["git", "-C", str(ENGINE_REPO), "show", f"{tag}:{path}"],
            check=True,
            capture_output=True,
        ).stdout.decode()
        lines = out.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        assert len(lines) == n, path
        cache[path] = lines
    for t in index["topics"]:
        for c in t["citations"]:
            body = "\n".join(cache[c["path"]][c["start"] - 1 : c["end"]])
            assert hashlib.sha256(body.encode()).hexdigest() == c["sha256"], (t["slug"], c["ref"])


def test_pages_carry_no_vendor_or_example_specific_claims() -> None:
    """Operator rule: the server is generic. A vendor name may appear only inside an
    engine excerpt (the engine's own words), never in the generated prose."""
    banned = re.compile(r"bloomberg|swpm|refinitiv|markit|10,?359|10,?585", re.IGNORECASE)
    for t in _index()["topics"]:
        page = (METH_DIR / t["file"]).read_text()
        prose = re.sub(r"````.*?````", "", page, flags=re.DOTALL)
        assert not banned.search(prose), (t["slug"], banned.search(prose))


async def test_explain_method_tool_and_resources(app: Any) -> None:
    async with Client(app) as c:
        theta = await c.call_tool("explain_method", {"topic": "theta"})
        assert not theta.is_error and theta.structured_content is not None
        page = theta.structured_content
        assert page["ok"] is True and page["topic"] == "theta"
        assert "rollDays = 1" in page["markdown"]
        assert any(ref.startswith("src/domain/swaption_rebump.h@") for ref in page["citations"])
        assert page["not_documented"]
        bad = await c.call_tool("explain_method", {"topic": "vendor-screens"})
        assert bad.structured_content is not None and bad.structured_content["ok"] is False
        assert "unknown methodology topic" in bad.structured_content["error"]
        assert {t["slug"] for t in bad.structured_content["topics"]} == BRIEF_TOPICS
        upper = await c.call_tool("explain_method", {"topic": "Curve Bootstrap"})
        assert upper.structured_content is not None
        assert upper.structured_content["topic"] == "curve-bootstrap"

        index = (await c.read_resource("quantra://methodology")).contents[0]
        data = json.loads(index.text)  # type: ignore[union-attr]
        assert {t["slug"] for t in data["topics"]} == BRIEF_TOPICS
        assert data["engine_tag"] == pin().tag
        md = (await c.read_resource("quantra://methodology/error-codes")).contents[0]
        assert md.text.startswith("# Error codes")  # type: ignore[union-attr]
        assert "docs/http-api.md@" in md.text  # type: ignore[union-attr]
        with pytest.raises(Exception, match="unknown methodology topic"):
            await c.read_resource("quantra://methodology/nope")
