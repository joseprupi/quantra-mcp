"""Generate the ``quantra://methodology/*`` pages.

Engine pages are generated from the engine repository at a tag; the
``connector-analytics`` page from THIS repository's working tree. Driven by the
curated rules in :mod:`methodology_rules`; called by ``pin_engine.py`` (and
runnable alone)::

    uv run python scripts/methodology_gen.py --engine-repo /path/to/quantraserver --tag v0.7.0
    uv run python scripts/methodology_gen.py --connector-only   # after a source commit

For every engine rule the cited file is read with ``git show <tag>:<path>``
(the working tree of the engine repository is never touched), the excerpt is
located by its anchor patterns, cut out verbatim and rendered under the
paraphrase with a ``path@tag:Lstart-Lend`` citation and the GitHub permalink
``https://github.com/joseprupi/quantraserver/blob/<tag>/<path>#Lstart-Lend``.
The connector page reads this repository's files, cites them at the short sha
of ``HEAD`` at generation time (``path@<sha>:Lstart-Lend`` +
``https://github.com/joseprupi/quantra-mcp/blob/<sha>/<path>#L..``), so it must
be regenerated AFTER the cited source is committed. A rule whose anchors do not
match, whose path does not exist or whose range runs past the file FAILS the
generation: nothing is rendered from a stale rule.

Outputs (under ``src/quantra_mcp/docs/methodology/``):

* ``<slug>.md`` per topic;
* ``INDEX.json``: engine tag + sha, connector sha, the two repository URLs,
  per-topic citations (path, lines, sha256 of the excerpt, GitHub url), the
  line count of every cited file per repository (so a hermetic test can check
  every range without the engine repository), and ``metric_topics``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import methodology_rules as rules

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "src" / "quantra_mcp" / "docs" / "methodology"

_LANG = {".md": "text", ".fbs": "text", ".h": "cpp", ".cpp": "cpp", ".py": "python"}


class RuleError(RuntimeError):
    pass


def git_show_lines(repo: Path, tag: str, path: str) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "show", f"{tag}:{path}"],
            check=True,
            capture_output=True,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise RuleError(f"{path} does not exist at {tag}: {exc.stderr.decode().strip()}") from None
    return out.decode("utf-8", errors="replace").split("\n")


def locate(lines: list[str], cite: dict[str, Any]) -> tuple[int, int]:
    """1-based inclusive (start, end) of the excerpt a cite describes."""
    start_re = re.compile(str(cite["start"]))
    nth = int(cite.get("nth", 1))
    seen = 0
    start = 0
    for i, line in enumerate(lines, start=1):
        if start_re.search(line):
            seen += 1
            if seen == nth:
                start = i
                break
    if not start:
        raise RuleError(f"{cite['path']}: start pattern {cite['start']!r} (match {nth}) not found")
    end_spec = cite["end"]
    if isinstance(end_spec, int):
        end = start + end_spec - 1
    else:
        end_re = re.compile(str(end_spec))
        end = 0
        for i in range(start, len(lines) + 1):
            if end_re.search(lines[i - 1]):
                end = i
                break
        if not end:
            raise RuleError(
                f"{cite['path']}: end pattern {end_spec!r} not found after line {start}"
            )
    # a trailing '' from split('\n') is not a line of the file
    n_lines = len(lines) - 1 if lines and lines[-1] == "" else len(lines)
    if end > n_lines:
        raise RuleError(f"{cite['path']}: lines {start}-{end} exceed the file ({n_lines} lines)")
    return start, end


def _fence(text: str, lang: str) -> str:
    return f"````{lang}\n{text}\n````"


def github_url(repo_url: str, rev: str, path: str, start: int, end: int) -> str:
    return f"{repo_url}/blob/{rev}/{path}#L{start}-L{end}"


def _count(lines: list[str]) -> int:
    return len(lines) - 1 if lines and lines[-1] == "" else len(lines)


class _Excerpter:
    """Cuts cited excerpts out of a source tree (engine at a tag, or this repo's
    working tree) and records every citation."""

    def __init__(
        self,
        read: Callable[[str], list[str]],
        rev: str,
        repo_url: str,
        source: str,
        cache: dict[str, list[str]],
    ) -> None:
        self.read = read
        self.rev = rev
        self.repo_url = repo_url
        self.source = source
        self.cache = cache
        self.citations: list[dict[str, Any]] = []

    def __call__(self, st: dict[str, Any], kind: str) -> str:
        cite = st["cite"]
        path = cite["path"]
        if path not in self.cache:
            self.cache[path] = self.read(path)
        lines = self.cache[path]
        start, end = locate(lines, cite)
        body = "\n".join(lines[start - 1 : end])
        ref = f"{path}@{self.rev}:L{start}-L{end}"
        url = github_url(self.repo_url, self.rev, path, start, end)
        self.citations.append(
            {
                "source": self.source,
                "path": path,
                "start": start,
                "end": end,
                "sha256": hashlib.sha256(body.encode()).hexdigest(),
                "kind": kind,
                "ref": ref,
                "url": url,
            }
        )
        lang = _LANG.get(Path(path).suffix, "text")
        return f"{_fence(body, lang)}\n\nSource: `{ref}`\n\nGitHub: <{url}>"


def _gaps(out: list[str], topic: dict[str, Any], heading: str, none: str) -> None:
    out += [heading, ""]
    if topic.get("gaps"):
        out += [f"- {g}" for g in topic["gaps"]]
    else:
        out.append(f"- {none}")
    out.append("")


def render_topic(
    topic: dict[str, Any], repo: Path, tag: str, sha: str, cache: dict[str, list[str]]
) -> tuple[str, dict[str, Any]]:
    excerpt = _Excerpter(
        lambda path: git_show_lines(repo, tag, path), tag, rules.ENGINE_REPO_URL, "engine", cache
    )
    out: list[str] = [f"# {topic['title']}", ""]
    out.append(
        f"_Generated from the engine's documentation and source at `{tag}` (`{sha[:12]}`) "
        "by `scripts/pin_engine.py`. Every statement is an excerpt of that tree with its "
        "location `path@tag:Lstart-Lend` and the GitHub permalink "
        f"`{rules.ENGINE_REPO_URL}/blob/{tag}/<path>#L..`; the one-line headings are "
        "paraphrases of the excerpt under them. Where the engine documents nothing, the last "
        "section says so._"
    )
    out += ["", topic["summary"], "", "## What the engine does", ""]
    for i, st in enumerate(topic["statements"], start=1):
        out += [f"### {i}. {st['say']}", "", excerpt(st, "statement"), ""]
    if topic.get("fields"):
        out += ["## Request fields that control it", ""]
        for st in topic["fields"]:
            out += [f"- {st['say']}", "", excerpt(st, "field"), ""]
    _gaps(
        out,
        topic,
        f"## Not documented in engine {tag}",
        "Nothing further: the excerpts above cover the behaviour the server relies on.",
    )
    meta = {
        "slug": topic["slug"],
        "title": topic["title"],
        "summary": topic["summary"],
        "source": "engine",
        "file": f"{topic['slug']}.md",
        "citations": excerpt.citations,
        "gaps": list(topic.get("gaps", [])),
    }
    return "\n".join(out), meta


def read_tree_lines(path: str) -> list[str]:
    file = ROOT / path
    if not file.is_file():
        raise RuleError(f"{path} does not exist in the working tree")
    return file.read_text(encoding="utf-8").split("\n")


def connector_sha() -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def render_connector_topic(
    topic: dict[str, Any], sha: str, cache: dict[str, list[str]]
) -> tuple[str, dict[str, Any]]:
    """The connector page: this repository's working tree, cited at ``sha``."""
    excerpt = _Excerpter(read_tree_lines, sha, rules.CONNECTOR_REPO_URL, "connector", cache)
    out: list[str] = [f"# {topic['title']}", ""]
    out.append(
        f"_Generated from THIS server's source (quantra-mcp) at commit `{sha}` by "
        "`scripts/methodology_gen.py --connector-only`. Unlike the other pages this one is "
        "not about the engine: it documents the request edits and the arithmetic the "
        "connector performs on engine outputs. Every statement is an excerpt of this "
        "repository with its location `path@sha:Lstart-Lend` and the GitHub permalink "
        f"`{rules.CONNECTOR_REPO_URL}/blob/{sha}/<path>#L..`._"
    )
    out += ["", topic["summary"], ""]
    out += ["## Which numbers are the engine's and which are computed here", ""]
    out += ["| Result field | Origin |", "|---|---|"]
    for field, origin in topic["ledger"]:
        out.append(f"| {field} | {origin} |")
    out += ["", "## What this server does", ""]
    for i, st in enumerate(topic["statements"], start=1):
        out += [f"### {i}. {st['say']}", "", excerpt(st, "statement"), ""]
    _gaps(out, topic, "## Caveats", "None.")
    meta = {
        "slug": topic["slug"],
        "title": topic["title"],
        "summary": topic["summary"],
        "source": "connector",
        "file": f"{topic['slug']}.md",
        "citations": excerpt.citations,
        "gaps": list(topic.get("gaps", [])),
    }
    return "\n".join(out), meta


def _write_index(
    out_dir: Path,
    tag: str,
    sha: str,
    engine_files: dict[str, int],
    conn_sha: str,
    conn_files: dict[str, int],
    topics_meta: list[dict[str, Any]],
) -> dict[str, Any]:
    for stale in out_dir.glob("*.md"):
        if stale.name not in {m["file"] for m in topics_meta}:
            stale.unlink()
    for metric, slug in rules.METRIC_TOPICS.items():
        if slug not in {m["slug"] for m in topics_meta}:
            raise RuleError(f"METRIC_TOPICS[{metric!r}] -> unknown topic {slug!r}")
    index = {
        "engine_tag": tag,
        "engine_sha": sha,
        "connector_sha": conn_sha,
        "repos": {"engine": rules.ENGINE_REPO_URL, "connector": rules.CONNECTOR_REPO_URL},
        "generated_by": "scripts/pin_engine.py (methodology_gen.py + methodology_rules.py); "
        "connector-analytics by methodology_gen.py --connector-only",
        "metric_topics": dict(rules.METRIC_TOPICS),
        "files": engine_files,
        "connector_files": conn_files,
        "topics": topics_meta,
    }
    (out_dir / "INDEX.json").write_text(json.dumps(index, indent=2) + "\n")
    return index


def generate(repo: Path, tag: str, sha: str, out_dir: Path = OUT_DIR) -> dict[str, Any]:
    """Engine pages from the engine repo at ``tag`` plus the connector page."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cache: dict[str, list[str]] = {}
    topics_meta: list[dict[str, Any]] = []
    for topic in rules.TOPICS:
        page, meta = render_topic(topic, repo, tag, sha, cache)
        (out_dir / meta["file"]).write_text(page)
        topics_meta.append(meta)
    conn_cache: dict[str, list[str]] = {}
    conn_sha = connector_sha()
    page, meta = render_connector_topic(rules.CONNECTOR_TOPIC, conn_sha, conn_cache)
    (out_dir / meta["file"]).write_text(page)
    topics_meta.append(meta)
    return _write_index(
        out_dir,
        tag,
        sha,
        {p: _count(lines) for p, lines in sorted(cache.items())},
        conn_sha,
        {p: _count(lines) for p, lines in sorted(conn_cache.items())},
        topics_meta,
    )


def generate_connector(out_dir: Path = OUT_DIR) -> dict[str, Any]:
    """Regenerate only the connector page; the engine parts of INDEX.json are kept."""
    index: dict[str, Any] = json.loads((out_dir / "INDEX.json").read_text())
    conn_cache: dict[str, list[str]] = {}
    conn_sha = connector_sha()
    page, meta = render_connector_topic(rules.CONNECTOR_TOPIC, conn_sha, conn_cache)
    (out_dir / meta["file"]).write_text(page)
    topics_meta = [t for t in index["topics"] if t.get("source") != "connector"] + [meta]
    return _write_index(
        out_dir,
        index["engine_tag"],
        index["engine_sha"],
        index["files"],
        conn_sha,
        {p: _count(lines) for p, lines in sorted(conn_cache.items())},
        topics_meta,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--engine-repo", type=Path)
    parser.add_argument("--tag")
    parser.add_argument(
        "--connector-only",
        action="store_true",
        help="regenerate only connector-analytics.md from the working tree (cited at HEAD)",
    )
    args = parser.parse_args(argv)
    if args.connector_only:
        index = generate_connector()
    else:
        if args.engine_repo is None or args.tag is None:
            parser.error("--engine-repo and --tag are required unless --connector-only")
        sha = subprocess.run(
            ["git", "-C", str(args.engine_repo), "rev-parse", f"{args.tag}^{{commit}}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        index = generate(args.engine_repo, args.tag, sha)
    for t in index["topics"]:
        print(f"{t['slug']}: {len(t['citations'])} citations ({t['source']})", file=sys.stderr)
    print(
        f"methodology: {len(index['topics'])} topics, "
        f"{sum(len(t['citations']) for t in index['topics'])} citations, "
        f"{len(index['files'])} engine files @ {index['engine_tag']}, "
        f"{len(index['connector_files'])} connector files @ {index['connector_sha']} -> "
        f"{OUT_DIR.relative_to(ROOT)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
