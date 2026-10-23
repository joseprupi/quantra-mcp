"""The source tools over the REAL fetched trees (``vendor/sources``, or
``QUANTRA_SOURCES_DIR``): skipped when ``scripts/fetch_sources.py`` has not run.

The scenario the operator asked for: from the methodology citation of
``src/parsers/term_structure_parser.cpp`` (the engine instantiating
``PiecewiseYieldCurve``), follow the class into QuantLib and read the bootstrap loop."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from quantra_mcp.config import Settings
from quantra_mcp.schema.loader import pin, quantlib_pin
from quantra_mcp.server import build_server
from quantra_mcp.sources import SourceTrees, default_sources_dir, sources_lock
from tests.conftest import FakeBackend, structured

METH_INDEX = (
    Path(__file__).resolve().parents[2] / "src" / "quantra_mcp" / "docs" / "methodology"
) / "INDEX.json"
TS_PARSER = "src/parsers/term_structure_parser.cpp"
PWYC = "ql/termstructures/yield/piecewiseyieldcurve.hpp"
ITER = "ql/termstructures/iterativebootstrap.hpp"

_trees = SourceTrees(default_sources_dir())
pytestmark = pytest.mark.skipif(
    not all(_trees.available().values()),
    reason="vendored source trees absent: run `uv run python scripts/fetch_sources.py`",
)


@pytest.fixture
def app() -> Any:
    return build_server(Settings(engine_url="http://fake"), backend=FakeBackend())


async def _call(app: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    async with Client(app) as c:
        return structured(await c.call_tool(name, args))


def test_lock_matches_the_pins_and_the_trees() -> None:
    lock = sources_lock()
    assert lock["engine"]["tag"] == pin().tag and lock["quantlib"]["tag"] == quantlib_pin().tag
    stats = _trees.stats()
    for key in ("engine", "quantlib"):
        assert stats[key]["files"] == lock[key]["files"], key
        assert stats[key]["bytes"] == lock[key]["bytes"], key
        assert re.fullmatch(r"[0-9a-f]{64}", lock[key]["sha256"])
    # both licence files are served verbatim at the tree roots
    assert "Redistribution and use" in _trees.read("engine", "LICENSE.txt")["text"]
    assert "QuantLib" in _trees.read("quantlib", "LICENSE.TXT", max_lines=400)["text"]
    assert 20_000_000 < stats["quantlib"]["bytes"] < 80_000_000  # ql/ text files only


async def test_engine_search_finds_the_term_structure_parser(app: Any) -> None:
    r = await _call(app, "search_source", {"query": "PiecewiseYieldCurve", "repo": "engine"})
    assert r["ok"] and r["count"] >= 1
    hit = next(h for h in r["hits"] if h["path"] == TS_PARSER)
    assert hit["citation"].startswith(f"engine:{TS_PARSER}@{pin().tag}:L")
    # consistency with the methodology pages: the same file is cited there, at a range
    # that contains at least one of the lines found here
    index = json.loads(METH_INDEX.read_text())
    ranges = [
        (c["start"], c["end"])
        for t in index["topics"]
        for c in t["citations"]
        if c["path"] == TS_PARSER
    ]
    assert ranges, "methodology no longer cites the term structure parser"
    lines = {h["line"] for h in r["hits"] if h["path"] == TS_PARSER}
    assert any(a <= n <= b for n in lines for a, b in ranges), (sorted(lines), ranges)
    cited = next(c for t in index["topics"] for c in t["citations"] if c["path"] == TS_PARSER)
    read = await _call(
        app,
        "read_source",
        {
            "repo": "engine",
            "path": TS_PARSER,
            "start_line": cited["start"],
            "end_line": cited["end"],
        },
    )
    assert read["ok"] and read["citation"] == f"engine:{cited['ref']}"
    assert read["permalink"] == cited["url"]


async def test_quantlib_class_search_and_read_the_class_doc(app: Any) -> None:
    r = await _call(
        app, "search_source", {"query": "class PiecewiseYieldCurve", "repo": "quantlib"}
    )
    assert r["ok"] and {h["path"] for h in r["hits"]} == {PWYC}, r["hits"]
    head = await _call(
        app, "read_source", {"repo": "quantlib", "path": PWYC, "start_line": 1, "end_line": 60}
    )
    assert head["ok"] and head["start_line"] == 1 and head["end_line"] == 60
    assert "Piecewise yield term structure" in head["text"]  # the class doc comment
    assert head["citation"] == f"quantlib:{PWYC}@{quantlib_pin().tag}:L1-L60"
    assert head["permalink"] == (
        f"https://github.com/lballabio/QuantLib/blob/{quantlib_pin().tag}/{PWYC}#L1-L60"
    )


async def test_iterative_bootstrap_search_and_the_calculate_body(app: Any) -> None:
    r = await _call(
        app,
        "search_source",
        {"query": "IterativeBootstrap", "repo": "quantlib", "max_results": 200},
    )
    assert r["ok"] and r["count"] >= 1
    assert ITER in {h["path"] for h in r["hits"]}
    # the bootstrap loop: the definition of IterativeBootstrap<Curve>::calculate()
    loop = await _call(
        app,
        "search_source",
        {"query": r"IterativeBootstrap<Curve>::calculate\(\)", "repo": "quantlib", "regex": True},
    )
    assert loop["ok"] and loop["count"] >= 1
    start = next(h["line"] for h in loop["hits"] if h["path"] == ITER)
    body = await _call(
        app,
        "read_source",
        {"repo": "quantlib", "path": ITER, "start_line": start, "max_lines": 120},
    )
    assert body["ok"] and body["start_line"] == start
    assert body["text"].lstrip().startswith("void IterativeBootstrap<Curve>::calculate() const")
    text = body["text"]
    assert "calculate() const {" in text
    assert "maxIterations" in text or "iteration" in text  # the loop over bootstrap passes
    assert body["permalink"] == (
        f"https://github.com/lballabio/QuantLib/blob/{quantlib_pin().tag}/{ITER}"
        f"#L{body['start_line']}-L{body['end_line']}"
    )
    assert body["citation"] == (
        f"quantlib:{ITER}@{quantlib_pin().tag}:L{body['start_line']}-L{body['end_line']}"
    )


async def test_list_source_browses_the_yield_directory(app: Any) -> None:
    r = await _call(
        app, "list_source", {"repo": "quantlib", "path_prefix": "ql/termstructures/yield"}
    )
    assert r["ok"] and PWYC in {e["path"] for e in r["entries"]}
    assert r["tree_url"].endswith(f"/tree/{quantlib_pin().tag}/ql/termstructures/yield")


def test_issuer_and_real_security_text_is_not_searchable() -> None:
    """0.1.14: the engine tree served by search_source / read_source carries no
    issuer-named or real-security passage (scripts/issuer_scrub.py, line for line)."""
    for query in ("06419JME7", "scotia", "XS2741509801", "XS3109492564", "46661PT64", "santander"):
        r = _trees.search(query, "engine")
        assert not r["hits"], (query, r["hits"][:2])
    stamp = json.loads((default_sources_dir() / "engine" / ".fetched.json").read_text())
    assert stamp.get("scrub_version", 0) >= 1
