"""Generate the ``quantra://methodology/*`` pages from the engine repository at a tag.

Driven by the curated rules in :mod:`methodology_rules`; called by
``pin_engine.py`` (and runnable alone)::

    uv run python scripts/methodology_gen.py --engine-repo /path/to/quantraserver --tag v0.7.0

For every rule the cited file is read with ``git show <tag>:<path>`` (the
working tree of the engine repository is never touched), the excerpt is
located by its anchor patterns, cut out verbatim and rendered under the
paraphrase with a ``path@tag:Lstart-Lend`` citation. A rule whose anchors do
not match, whose path does not exist at the tag or whose range runs past the
file FAILS the generation: nothing is rendered from a stale rule.

Outputs (under ``src/quantra_mcp/docs/methodology/``):

* ``<slug>.md`` per topic;
* ``INDEX.json``: tag, sha, per-topic citations (path, lines, sha256 of the
  excerpt), the line count of every cited file (so a hermetic test can check
  every range without the engine repository), and ``metric_topics``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
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


def render_topic(
    topic: dict[str, Any], repo: Path, tag: str, sha: str, cache: dict[str, list[str]]
) -> tuple[str, dict[str, Any]]:
    citations: list[dict[str, Any]] = []

    def excerpt(st: dict[str, Any], kind: str) -> str:
        cite = st["cite"]
        path = cite["path"]
        if path not in cache:
            cache[path] = git_show_lines(repo, tag, path)
        lines = cache[path]
        start, end = locate(lines, cite)
        body = "\n".join(lines[start - 1 : end])
        ref = f"{path}@{tag}:L{start}-L{end}"
        citations.append(
            {
                "path": path,
                "start": start,
                "end": end,
                "sha256": hashlib.sha256(body.encode()).hexdigest(),
                "kind": kind,
                "ref": ref,
            }
        )
        lang = _LANG.get(Path(path).suffix, "text")
        return f"{_fence(body, lang)}\n\nSource: `{ref}`"

    out: list[str] = [f"# {topic['title']}", ""]
    out.append(
        f"_Generated from the engine's documentation and source at `{tag}` (`{sha[:12]}`) "
        "by `scripts/pin_engine.py`. Every statement is an excerpt of that tree with its "
        "location `path@tag:Lstart-Lend`; the one-line headings are paraphrases of the "
        "excerpt under them. Where the engine documents nothing, the last section says so._"
    )
    out += ["", topic["summary"], "", "## What the engine does", ""]
    for i, st in enumerate(topic["statements"], start=1):
        out += [f"### {i}. {st['say']}", "", excerpt(st, "statement"), ""]
    if topic.get("fields"):
        out += ["## Request fields that control it", ""]
        for st in topic["fields"]:
            out += [f"- {st['say']}", "", excerpt(st, "field"), ""]
    out += [f"## Not documented in engine {tag}", ""]
    if topic.get("gaps"):
        out += [f"- {g}" for g in topic["gaps"]]
    else:
        out.append(
            "- Nothing further: the excerpts above cover the behaviour the server relies on."
        )
    out.append("")
    meta = {
        "slug": topic["slug"],
        "title": topic["title"],
        "summary": topic["summary"],
        "file": f"{topic['slug']}.md",
        "citations": citations,
        "gaps": list(topic.get("gaps", [])),
    }
    return "\n".join(out), meta


def generate(repo: Path, tag: str, sha: str, out_dir: Path = OUT_DIR) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cache: dict[str, list[str]] = {}
    topics_meta: list[dict[str, Any]] = []
    for topic in rules.TOPICS:
        page, meta = render_topic(topic, repo, tag, sha, cache)
        (out_dir / meta["file"]).write_text(page)
        topics_meta.append(meta)
    for stale in out_dir.glob("*.md"):
        if stale.name not in {m["file"] for m in topics_meta}:
            stale.unlink()
    for metric, slug in rules.METRIC_TOPICS.items():
        if slug not in {m["slug"] for m in topics_meta}:
            raise RuleError(f"METRIC_TOPICS[{metric!r}] -> unknown topic {slug!r}")
    files = {
        path: (len(lines) - 1 if lines and lines[-1] == "" else len(lines))
        for path, lines in sorted(cache.items())
    }
    index = {
        "engine_tag": tag,
        "engine_sha": sha,
        "generated_by": "scripts/pin_engine.py (methodology_gen.py + methodology_rules.py)",
        "metric_topics": dict(rules.METRIC_TOPICS),
        "files": files,
        "topics": topics_meta,
    }
    (out_dir / "INDEX.json").write_text(json.dumps(index, indent=2) + "\n")
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--engine-repo", required=True, type=Path)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args(argv)
    sha = subprocess.run(
        ["git", "-C", str(args.engine_repo), "rev-parse", f"{args.tag}^{{commit}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    index = generate(args.engine_repo, args.tag, sha)
    for t in index["topics"]:
        print(f"{t['slug']}: {len(t['citations'])} citations", file=sys.stderr)
    print(
        f"methodology: {len(index['topics'])} topics, "
        f"{sum(len(t['citations']) for t in index['topics'])} citations, "
        f"{len(index['files'])} cited files -> {OUT_DIR.relative_to(ROOT)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
