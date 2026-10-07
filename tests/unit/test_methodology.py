"""The generated methodology pages: every engine citation points at a real file
and a real line range at the pin, every connector citation at real lines of this
repository's working tree, every citation carries a GitHub permalink, every page
is reachable as a resource and through ``explain_method``, and nothing in them is
vendor- or example-specific."""

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

ENGINE_TOPICS = {
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
    "schedules-and-stubs",
    "error-codes",
}
CONNECTOR_TOPICS = {"connector-analytics"}
BRIEF_TOPICS = ENGINE_TOPICS | CONNECTOR_TOPICS
ENGINE_URL = "https://github.com/joseprupi/quantraserver"
CONNECTOR_URL = "https://github.com/joseprupi/quantra-mcp"

_CITE_RE = re.compile(r"`([^`@]+)@([\w.]+):L(\d+)-L(\d+)`")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def _index() -> dict[str, Any]:
    return json.loads((METH_DIR / "INDEX.json").read_text())


def test_index_matches_the_pin_and_the_brief_topics() -> None:
    index = _index()
    assert index["engine_tag"] == pin().tag and index["engine_sha"] == pin().sha
    assert _SHA_RE.match(index["connector_sha"])
    assert index["repos"] == {"engine": ENGINE_URL, "connector": CONNECTOR_URL}
    assert methodology.repos() == index["repos"]
    assert {t["slug"] for t in index["topics"]} == BRIEF_TOPICS
    assert {t["slug"] for t in index["topics"] if t["source"] == "engine"} == ENGINE_TOPICS
    assert {t["slug"] for t in index["topics"] if t["source"] == "connector"} == CONNECTOR_TOPICS
    assert set(methodology.slugs()) == BRIEF_TOPICS
    for slug, topic in index["metric_topics"].items():
        assert topic in ENGINE_TOPICS, (slug, topic)
    assert methodology.topic_for_metric("npv") == "npv"
    assert methodology.topic_for_metric("theta") == "theta"
    assert methodology.topic_for_metric("dv01") == "greeks-bump-and-reprice"
    assert methodology.topic_for_metric("not_a_field") is None


def test_every_citation_is_within_a_cited_file_hermetically() -> None:
    """Without the engine repo: the generator recorded every cited file's line count;
    every range must lie inside it, every page must print its own citations and, right
    after each, the GitHub permalink of the same lines."""
    index = _index()
    files: dict[str, dict[str, int]] = {
        "engine": index["files"],
        "connector": index["connector_files"],
    }
    rev = {"engine": index["engine_tag"], "connector": index["connector_sha"]}
    url = {"engine": ENGINE_URL, "connector": CONNECTOR_URL}
    for t in index["topics"]:
        page = (METH_DIR / t["file"]).read_text()
        assert t["citations"], t["slug"]
        printed = {(m.group(1), int(m.group(3)), int(m.group(4))) for m in _CITE_RE.finditer(page)}
        for c in t["citations"]:
            src = c["source"]
            assert src == t["source"], (t["slug"], c)
            assert c["path"] in files[src], (t["slug"], c["path"])
            assert 1 <= c["start"] <= c["end"] <= files[src][c["path"]], (t["slug"], c)
            assert c["ref"] == f"{c['path']}@{rev[src]}:L{c['start']}-L{c['end']}"
            assert c["url"] == f"{url[src]}/blob/{rev[src]}/{c['path']}#L{c['start']}-L{c['end']}"
            assert (c["path"], c["start"], c["end"]) in printed, (t["slug"], c["ref"])
            assert f"Source: `{c['ref']}`\n\nGitHub: <{c['url']}>" in page, (t["slug"], c["ref"])
            assert len(c["sha256"]) == 64
        if t["source"] == "engine":
            assert f"## Not documented in engine {index['engine_tag']}" in page
            assert f"`{ENGINE_URL}/blob/{index['engine_tag']}/<path>#L..`" in page
        else:
            assert "## Caveats" in page and "## Which numbers are the engine's" in page
            assert f"`{CONNECTOR_URL}/blob/{index['connector_sha']}/<path>#L..`" in page
        assert page.startswith(f"# {t['title']}")


def test_connector_citations_match_the_working_tree() -> None:
    """The connector-analytics page cites THIS repository: every cited file exists in
    the working tree with the recorded line count and the excerpt is byte-identical to
    the current lines (edit bumps.py / analytics.py / reconcile.py -> regenerate with
    ``methodology_gen.py --connector-only`` after committing)."""
    index = _index()
    root = Path(__file__).resolve().parents[2]
    cache: dict[str, list[str]] = {}
    for path, n in index["connector_files"].items():
        lines = (root / path).read_text().split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        assert len(lines) == n, (path, len(lines), n)
        cache[path] = lines
    topics = [t for t in index["topics"] if t["source"] == "connector"]
    assert [t["slug"] for t in topics] == ["connector-analytics"]
    cited_files = {c["path"] for t in topics for c in t["citations"]}
    assert cited_files == {
        "src/quantra_mcp/builders/bumps.py",
        "src/quantra_mcp/tools/analytics.py",
        "src/quantra_mcp/tools/reconcile.py",
    }
    for t in topics:
        for c in t["citations"]:
            body = "\n".join(cache[c["path"]][c["start"] - 1 : c["end"]])
            assert hashlib.sha256(body.encode()).hexdigest() == c["sha256"], (t["slug"], c["ref"])
    page = (METH_DIR / "connector-analytics.md").read_text()
    # the page says which numbers are engine outputs and which are differences made here
    assert "engine output" in page and "connector arithmetic" in page
    assert "(npv_up - npv_down) / 2" in page and "npv - base_npv" in page
    assert "changed - base" in page and "quantra - external" in page
    if (root / ".git").exists():
        # the cited commit exists in this repository (the page is regenerated after a commit)
        ok = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-e", f"{index['connector_sha']}^{{commit}}"],
            check=False,
            capture_output=True,
        )
        assert ok.returncode == 0, index["connector_sha"]


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
        if t["source"] != "engine":
            continue
        for c in t["citations"]:
            body = "\n".join(cache[c["path"]][c["start"] - 1 : c["end"]])
            assert hashlib.sha256(body.encode()).hexdigest() == c["sha256"], (t["slug"], c["ref"])
    # the curve-bootstrap page shows real C++: the PiecewiseYieldCurve instantiation, the
    # OISRateHelper construction and the interpolator mapping, each with file:line
    boot = next(t for t in index["topics"] if t["slug"] == "curve-bootstrap")
    cpp = {c["path"] for c in boot["citations"] if c["path"].endswith(".cpp")}
    assert cpp == {
        "src/parsers/term_structure_parser.cpp",
        "src/parsers/term_structure_point_parser.cpp",
    }
    page = (METH_DIR / boot["file"]).read_text()
    assert "PiecewiseYieldCurve<Discount, LogLinear>" in page
    assert "std::make_shared<OISRateHelper>(" in page
    assert "case enums::Interpolator_LogCubic:" in page
    sched = (METH_DIR / "schedules-and-stubs.md").read_text()
    assert "DateGenerationToQL(schedule->date_generation_rule().value())" in sched
    assert "first_date" in sched and "Backward" in sched and "Forward" in sched


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
        assert page["source"] == "engine" and page["repository"] == ENGINE_URL
        assert page["repos"] == {"engine": ENGINE_URL, "connector": CONNECTOR_URL}
        assert len(page["links"]) == len(page["citations"])
        assert all(link.startswith(f"{ENGINE_URL}/blob/{pin().tag}/") for link in page["links"])
        conn = await c.call_tool("explain_method", {"topic": "connector-analytics"})
        assert conn.structured_content is not None and conn.structured_content["ok"]
        cpage = conn.structured_content
        assert cpage["source"] == "connector" and cpage["repository"] == CONNECTOR_URL
        sha = cpage["connector_sha"]
        assert all(link.startswith(f"{CONNECTOR_URL}/blob/{sha}/") for link in cpage["links"])
        assert any(
            ref.startswith("src/quantra_mcp/tools/analytics.py@") for ref in cpage["citations"]
        )
        bad = await c.call_tool("explain_method", {"topic": "vendor-screens"})
        assert bad.structured_content is not None and bad.structured_content["ok"] is False
        assert "unknown methodology topic" in bad.structured_content["error"]
        assert {t["slug"] for t in bad.structured_content["topics"]} == BRIEF_TOPICS
        upper = await c.call_tool("explain_method", {"topic": "Curve Bootstrap"})
        assert upper.structured_content is not None
        assert upper.structured_content["topic"] == "curve-bootstrap"

        index = (await c.read_resource("quantra://methodology")).contents[0]
        data = json.loads(index.text)  # type: ignore[union-attr]
        assert next(iter(data)) == "repos"  # the two repository URLs come first
        assert data["repos"] == {"engine": ENGINE_URL, "connector": CONNECTOR_URL}
        assert {t["slug"] for t in data["topics"]} == BRIEF_TOPICS
        assert {t["source"] for t in data["topics"]} == {"engine", "connector"}
        assert data["engine_tag"] == pin().tag and _SHA_RE.match(data["connector_sha"])
        conn_md = (await c.read_resource("quantra://methodology/connector-analytics")).contents[0]
        assert conn_md.text.startswith("# Connector analytics")  # type: ignore[union-attr]
        md = (await c.read_resource("quantra://methodology/error-codes")).contents[0]
        assert md.text.startswith("# Error codes")  # type: ignore[union-attr]
        assert "docs/http-api.md@" in md.text  # type: ignore[union-attr]
        with pytest.raises(Exception, match="unknown methodology topic"):
            await c.read_resource("quantra://methodology/nope")
