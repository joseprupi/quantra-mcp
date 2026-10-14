"""``search_source`` / ``read_source`` / ``list_source`` through the real tool code over
the fixture trees under ``tests/fixtures/sources`` (root overridden by
``QUANTRA_SOURCES_DIR`` / ``Settings.sources_dir``): bounds, refusals (traversal, absolute,
symlink, binary, short query, bad regex), the time guard and byte budget, the citation
and permalink formats, and the error text when a tree is absent."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from quantra_mcp import sources as src
from quantra_mcp.config import Settings
from quantra_mcp.schema.loader import pin, quantlib_pin
from quantra_mcp.server import build_server
from quantra_mcp.sources import SourceTrees
from tests.conftest import FakeBackend, structured

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sources"
CITE_RE = re.compile(r"^(engine|quantlib):([^@]+)@([\w.]+):L(\d+)-L(\d+)$")


@pytest.fixture
def trees() -> SourceTrees:
    return SourceTrees(FIXTURES)


@pytest.fixture
def source_app() -> Any:
    return build_server(
        Settings(engine_url="http://fake", sources_dir=str(FIXTURES)), backend=FakeBackend()
    )


async def _call(app: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    async with Client(app) as c:
        return structured(await c.call_tool(name, args))


def test_env_override_and_default_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(src.ENV_SOURCES_DIR, str(FIXTURES))
    assert src.default_sources_dir() == FIXTURES
    monkeypatch.delenv(src.ENV_SOURCES_DIR)
    assert src.default_sources_dir().as_posix().endswith("/vendor/sources")
    assert Settings.from_env({"QUANTRA_SOURCES_DIR": " /x/y "}).sources_dir == "/x/y"


def test_refs_come_from_the_pins(trees: SourceTrees) -> None:
    assert trees.repos["engine"].tag == pin().tag
    assert trees.repos["quantlib"].tag == quantlib_pin().tag == f"v{quantlib_pin().version}"
    assert trees.repos["quantlib"].github == "lballabio/QuantLib"
    assert trees.repos["engine"].github == "joseprupi/quantraserver"
    stats = trees.stats()
    assert stats["engine"]["files"] == 3 and stats["quantlib"]["files"] == 4  # dotfiles excluded
    assert stats["engine"]["available"] and stats["quantlib"]["available"]


def test_index_skips_dotfiles_and_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "sources"
    (root / "engine").mkdir(parents=True)
    (root / "quantlib").mkdir()
    (root / "engine" / "a.cpp").write_text("int a;\n")
    (root / "engine" / ".fetched.json").write_text("{}")
    (root / "engine" / "link.cpp").symlink_to(root / "engine" / "a.cpp")
    (root / "quantlib" / "b.hpp").write_text("int b;\n")
    t = SourceTrees(root)
    assert t.index("engine") == [("a.cpp", 7)]
    with pytest.raises(src.LocalValidationError, match="symlinks"):
        t.read("engine", "link.cpp")


async def test_search_finds_hits_with_context_citation_and_permalink(source_app: Any) -> None:
    r = await _call(source_app, "search_source", {"query": "PiecewiseYieldCurve", "repo": "both"})
    assert r["ok"] and r["repo"] == "both" and r["truncated"] is False
    assert r["refs"] == {"engine": pin().tag, "quantlib": quantlib_pin().tag}
    paths = [(h["repo"], h["path"], h["line"]) for h in r["hits"]]
    assert paths == sorted(paths)  # by repo, path, then line
    assert ("engine", "src/parsers/term_structure_parser.cpp", 2) in paths
    assert ("engine", "src/notes.txt", 2) in paths
    assert ("quantlib", "ql/termstructures/yield/piecewiseyieldcurve.hpp", 8) in paths
    hit = next(h for h in r["hits"] if h["path"] == "src/notes.txt")
    assert hit["before"] == "Three lines of notes." and hit["after"] == "The third line does not."
    assert hit["citation"] == f"engine:src/notes.txt@{pin().tag}:L2-L2"
    assert hit["permalink"] == (
        f"https://github.com/joseprupi/quantraserver/blob/{pin().tag}/src/notes.txt#L2-L2"
    )
    for h in r["hits"]:
        assert CITE_RE.match(h["citation"]), h
    # case-insensitive by default; exact when asked; the binary file is never scanned
    lower = await _call(source_app, "search_source", {"query": "piecewiseyieldcurve"})
    assert lower["count"] == sum(1 for h in r["hits"] if h["repo"] == "engine")  # engine only
    exact = await _call(
        source_app, "search_source", {"query": "piecewiseyieldcurve", "case_sensitive": True}
    )
    assert exact["ok"] and [h["line"] for h in exact["hits"]] == [2]  # only the #include path
    assert all("blob.bin" not in h["path"] for h in r["hits"])


async def test_search_regex_prefix_max_results_and_refusals(source_app: Any) -> None:
    r = await _call(
        source_app,
        "search_source",
        {"query": r"calculate\(\)\s+const", "repo": "quantlib", "regex": True},
    )
    assert r["ok"] and [h["line"] for h in r["hits"]] == [7, 11]
    r = await _call(
        source_app,
        "search_source",
        {"query": "QuantLib", "repo": "quantlib", "path_prefix": "ql/termstructures/yield"},
    )
    assert r["ok"] and {h["path"] for h in r["hits"]} == {
        "ql/termstructures/yield/piecewiseyieldcurve.hpp"
    }
    r = await _call(
        source_app, "search_source", {"query": "line", "repo": "engine", "max_results": 1}
    )
    assert r["ok"] and r["count"] == 1 and r["truncated"] and r["truncated_reason"] == "max_results"
    bad = await _call(source_app, "search_source", {"query": "(unclosed", "regex": True})
    assert bad["ok"] is False and "invalid regular expression" in bad["error"]
    with pytest.raises(Exception) as info:  # schema min_length 3: refused by the SDK
        await _call(source_app, "search_source", {"query": "ab", "repo": "engine"})
    assert re.search(r"(?i)query|validation|short", repr(info.value)), repr(info.value)
    with pytest.raises(Exception) as info:  # repo is a closed choice: refused by the SDK
        await _call(source_app, "search_source", {"query": "abc", "repo": "nope"})
    assert "literal_error" in repr(info.value)
    bad = await _call(source_app, "search_source", {"query": "abc", "path_prefix": "../../etc"})
    assert bad["ok"] is False and "path_prefix" in bad["error"]


def test_search_time_guard_and_byte_budget(trees: SourceTrees) -> None:
    r = trees.search("line", "engine", deadline_s=-1.0)
    assert r["truncated"] and r["truncated_reason"] == "deadline" and r["count"] == 0
    r = trees.search("line", "engine", byte_budget=10)
    assert r["truncated"] and r["truncated_reason"] == "byte_budget" and r["scanned_files"] == 0
    with pytest.raises(src.LocalValidationError, match="at least 3"):
        trees.search("ab", "engine")
    with pytest.raises(src.LocalValidationError, match="longer than"):
        trees.search("x" * 300, "engine")


async def test_read_source_ranges_bounds_and_permalink(source_app: Any) -> None:
    path = "ql/termstructures/iterativebootstrap.hpp"
    r = await _call(
        source_app,
        "read_source",
        {"repo": "quantlib", "path": path, "start_line": 11, "end_line": 16},
    )
    assert r["ok"] and r["start_line"] == 11 and r["end_line"] == 16 and r["total_lines"] == 18
    assert r["text"].startswith("    void IterativeBootstrap<Curve>::calculate() const {")
    assert r["text"].endswith("    }")
    assert r["ref"] == quantlib_pin().tag and r["truncated"] is False
    assert r["citation"] == f"quantlib:{path}@{quantlib_pin().tag}:L11-L16"
    assert r["permalink"] == (
        f"https://github.com/lballabio/QuantLib/blob/{quantlib_pin().tag}/{path}#L11-L16"
    )
    whole = await _call(source_app, "read_source", {"repo": "quantlib", "path": path})
    assert whole["start_line"] == 1 and whole["end_line"] == 18 and whole["text"].count("\n") == 17
    capped = await _call(
        source_app, "read_source", {"repo": "quantlib", "path": path, "max_lines": 5}
    )
    assert capped["end_line"] == 5 and capped["truncated"] is True
    with pytest.raises(Exception) as info:  # schema cap 400: refused by the SDK
        await _call(source_app, "read_source", {"repo": "quantlib", "path": path, "max_lines": 401})
    assert re.search(r"(?i)max_lines|validation|less", repr(info.value)), repr(info.value)
    past = await _call(
        source_app, "read_source", {"repo": "quantlib", "path": path, "start_line": 99}
    )
    assert past["ok"] is False and "past the end" in past["error"]


async def test_read_source_refuses_traversal_absolute_binary_and_dirs(source_app: Any) -> None:
    for bad_path in ("../../../etc/passwd", "/etc/passwd", "ql/../../engine/src/notes.txt"):
        r = await _call(source_app, "read_source", {"repo": "quantlib", "path": bad_path})
        assert r["ok"] is False and r["problems"][0]["path"] == "/path", bad_path
    r = await _call(source_app, "read_source", {"repo": "quantlib", "path": "ql/blob.bin"})
    assert r["ok"] is False and "not a text file" in r["error"]
    r = await _call(source_app, "read_source", {"repo": "quantlib", "path": "ql"})
    assert r["ok"] is False and "list_source" in r["error"]
    r = await _call(source_app, "read_source", {"repo": "engine", "path": "src/missing.cpp"})
    assert r["ok"] is False and "no such file" in r["error"]


async def test_list_source(source_app: Any) -> None:
    root = await _call(source_app, "list_source", {"repo": "quantlib"})
    assert (
        root["ok"]
        and root["path_prefix"] == ""
        and root["entries"][0]
        == {
            "path": "ql",
            "type": "dir",
            "files": 3,
        }
    )
    assert {e["path"] for e in root["entries"]} == {"ql", "LICENSE.TXT"}
    assert root["tree_url"] == f"https://github.com/lballabio/QuantLib/tree/{quantlib_pin().tag}"
    ts = await _call(
        source_app, "list_source", {"repo": "quantlib", "path_prefix": "ql/termstructures"}
    )
    assert [e["path"] for e in ts["entries"]] == [
        "ql/termstructures/yield",
        "ql/termstructures/iterativebootstrap.hpp",
    ]
    assert ts["entries"][1]["bytes"] == os.path.getsize(
        FIXTURES / "quantlib" / "ql" / "termstructures" / "iterativebootstrap.hpp"
    )
    capped = await _call(
        source_app, "list_source", {"repo": "quantlib", "path_prefix": "", "max_entries": 1}
    )
    assert capped["count"] == 1 and capped["total"] == 2 and capped["truncated"]
    bad = await _call(source_app, "list_source", {"repo": "quantlib", "path_prefix": "../x"})
    assert bad["ok"] is False
    file = await _call(
        source_app, "list_source", {"repo": "quantlib", "path_prefix": "LICENSE.TXT"}
    )
    assert file["ok"] is False and "read_source" in file["error"]


async def test_missing_tree_names_the_fetch_script(tmp_path: Path) -> None:
    app = build_server(
        Settings(engine_url="http://fake", sources_dir=str(tmp_path)), backend=FakeBackend()
    )
    for name, args in (
        ("search_source", {"query": "anything"}),
        ("read_source", {"repo": "quantlib", "path": "ql/x.hpp"}),
        ("list_source", {"repo": "engine"}),
    ):
        r = await _call(app, name, args)
        assert r["ok"] is False, name
        assert "scripts/fetch_sources.py" in r["error"] and "not on disk" in r["error"], name
        assert r["sources"]["engine"]["available"] is False
        assert r["sources"]["quantlib"]["tag"] == quantlib_pin().tag


async def test_pin_resource_reports_quantlib_and_sources(source_app: Any) -> None:
    import json

    async with Client(source_app) as c:
        body = json.loads((await c.read_resource("quantra://pin")).contents[0].text)  # type: ignore[union-attr]
    assert body["quantlib"] == quantlib_pin().version
    assert body["quantlib_tag"] == quantlib_pin().tag
    assert body["sources_lock"]["quantlib"]["tag"] == quantlib_pin().tag
    assert body["sources_lock"]["engine"]["tag"] == pin().tag
    assert len(body["sources_lock"]["quantlib"]["sha256"]) == 64
