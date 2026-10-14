"""Read-only access to the vendored source trees: the engine at the pinned tag and the
QuantLib release it is built on (``scripts/fetch_sources.py`` fetches both GitHub
tarballs, verifies ``schema/SOURCES.lock`` and extracts the text files of the allowed
trees into ``vendor/sources/{engine,quantlib}/``; the container image ships them).

Pure Python, no shelling out, every operation bounded: the search streams files under a
byte budget and a deadline, a read returns at most ``MAX_READ_LINES`` lines, listings are
capped, paths are resolved and prefix-checked (no traversal, no symlinks), binary files
are refused. Every result carries a ``citation`` (``<repo>:<path>@<tag>:L<a>-L<b>``) and
a GitHub ``permalink`` so the assistant cites the same way the methodology pages do.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from quantra_mcp.errors import LocalValidationError
from quantra_mcp.schema.loader import SCHEMA_DIR, pin, quantlib_pin

ENV_SOURCES_DIR = "QUANTRA_SOURCES_DIR"
LOCK_PATH = SCHEMA_DIR / "SOURCES.lock"
FETCH_SCRIPT = "scripts/fetch_sources.py"

MIN_QUERY_CHARS = 3
MAX_PATTERN_CHARS = 256
MAX_RESULTS_CAP = 200
DEFAULT_MAX_RESULTS = 30
MAX_READ_LINES = 400
DEFAULT_READ_LINES = 200
MAX_LIST_ENTRIES = 1000
DEFAULT_LIST_ENTRIES = 200
#: the search scans at most this many bytes of file content per call
SEARCH_BYTE_BUDGET = 96 * 1024 * 1024
#: and stops after this many seconds (checked between files and every 2,000 lines)
SEARCH_DEADLINE_S = 10.0
#: a single file larger than this is skipped by the search (none in either tree)
MAX_FILE_BYTES = 8 * 1024 * 1024
_BINARY_PROBE = 8192


@dataclass(frozen=True, slots=True)
class RepoSpec:
    key: str
    github: str  # owner/name
    tag: str

    @property
    def url(self) -> str:
        return f"https://github.com/{self.github}"

    def permalink(self, path: str, start: int, end: int) -> str:
        return f"{self.url}/blob/{self.tag}/{path}#L{start}-L{end}"

    def citation(self, path: str, start: int, end: int) -> str:
        return f"{self.key}:{path}@{self.tag}:L{start}-L{end}"


def pinned_repos() -> dict[str, RepoSpec]:
    """The two repositories at their pinned refs."""
    return {
        "engine": RepoSpec("engine", "joseprupi/quantraserver", pin().tag),
        "quantlib": RepoSpec("quantlib", "lballabio/QuantLib", quantlib_pin().tag),
    }


def sources_lock() -> dict[str, Any]:
    """``schema/SOURCES.lock``: per repository the tag, tarball URL, sha256, file count
    and byte size recorded by ``scripts/fetch_sources.py`` (empty before the first fetch)."""
    if not LOCK_PATH.is_file():
        return {}
    data: dict[str, Any] = json.loads(LOCK_PATH.read_text())
    return data


def default_sources_dir() -> Path:
    """``$QUANTRA_SOURCES_DIR`` or ``<checkout>/vendor/sources``."""
    env = os.environ.get(ENV_SOURCES_DIR, "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "vendor" / "sources"


class SourcesMissing(LocalValidationError):
    """The vendored tree of a repository is not on disk."""


def _refuse(message: str, path: str = "/") -> LocalValidationError:
    return LocalValidationError(message, [{"path": path, "message": message}])


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:_BINARY_PROBE]


def _lines(data: bytes) -> list[str]:
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


class SourceTrees:
    """The vendored trees under ``root/<repo>/``; indexes are built lazily per repo."""

    def __init__(self, root: Path | None = None, repos: dict[str, RepoSpec] | None = None):
        self.root = (root or default_sources_dir()).resolve()
        self.repos = repos or pinned_repos()
        self._index: dict[str, list[tuple[str, int]]] = {}

    # -- repositories and paths -------------------------------------------------------

    def spec(self, repo: str) -> RepoSpec:
        key = (repo or "").strip().lower()
        if key not in self.repos:
            raise _refuse(f"unknown repo {repo!r}; one of {sorted(self.repos)}", "/repo")
        return self.repos[key]

    def repo_root(self, repo: str) -> Path:
        spec = self.spec(repo)
        root = self.root / spec.key
        if not root.is_dir() or not any(root.iterdir()):
            raise SourcesMissing(
                f"the {spec.key} source tree ({spec.github} at {spec.tag}) is not on disk under "
                f"{root}; run `uv run python {FETCH_SCRIPT}` once (set {ENV_SOURCES_DIR} to "
                "point elsewhere). The container image ships both trees.",
                [{"path": "/repo", "message": f"{spec.key} sources not fetched"}],
            )
        return root

    def available(self) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for key in self.repos:
            try:
                self.repo_root(key)
                out[key] = True
            except SourcesMissing:
                out[key] = False
        return out

    def _safe_path(self, repo: str, path: str) -> tuple[Path, str]:
        """``(absolute, relative)`` for ``path`` inside the repo tree, or a refusal.
        Absolute paths, ``..`` components, symlinks and anything resolving outside the
        tree are rejected."""
        root = self.repo_root(repo).resolve()
        raw = (path or "").strip().replace("\\", "/")
        if not raw or raw == ".":
            return root, ""
        if raw.startswith("/") or raw.startswith("~") or re.match(r"^[A-Za-z]:", raw):
            raise _refuse("path must be relative to the repository root", "/path")
        parts = [p for p in raw.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise _refuse("path must not contain '..'", "/path")
        candidate = root.joinpath(*parts)
        # no symlink at any component (the fetch never extracts any; belt and braces)
        probe = root
        for part in parts:
            probe = probe / part
            if probe.is_symlink():
                raise _refuse("symlinks are not served", "/path")
        resolved = candidate.resolve()
        if resolved != root and root not in resolved.parents:
            raise _refuse("path resolves outside the repository tree", "/path")
        return resolved, "/".join(parts)

    # -- index ---------------------------------------------------------------------------

    def index(self, repo: str) -> list[tuple[str, int]]:
        """Sorted ``(relative path, size)`` of every regular file in the repo tree."""
        key = self.spec(repo).key
        cached = self._index.get(key)
        if cached is not None:
            return cached
        root = self.repo_root(key)
        files: list[tuple[str, int]] = []
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not (Path(dirpath) / d).is_symlink())
            for name in filenames:
                if name.startswith("."):
                    continue  # the fetch stamp and any other dotfile
                full = Path(dirpath) / name
                if full.is_symlink() or not full.is_file():
                    continue
                rel = full.relative_to(root).as_posix()
                files.append((rel, full.stat().st_size))
        files.sort()
        self._index[key] = files
        return files

    def stats(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, spec in self.repos.items():
            try:
                idx = self.index(key)
            except SourcesMissing:
                out[key] = {"tag": spec.tag, "github": spec.github, "available": False}
                continue
            out[key] = {
                "tag": spec.tag,
                "github": spec.github,
                "available": True,
                "files": len(idx),
                "bytes": sum(size for _, size in idx),
            }
        return out

    # -- search --------------------------------------------------------------------------

    def search(
        self,
        query: str,
        repo: str = "engine",
        *,
        regex: bool = False,
        path_prefix: str | None = None,
        max_results: int = DEFAULT_MAX_RESULTS,
        case_sensitive: bool = False,
        byte_budget: int = SEARCH_BYTE_BUDGET,
        deadline_s: float = SEARCH_DEADLINE_S,
    ) -> dict[str, Any]:
        q = (query or "").strip()
        if len(q) < MIN_QUERY_CHARS:
            raise _refuse(f"query must be at least {MIN_QUERY_CHARS} characters", "/query")
        if len(q) > MAX_PATTERN_CHARS:
            raise _refuse(f"query longer than {MAX_PATTERN_CHARS} characters", "/query")
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(q if regex else re.escape(q), flags)
        except re.error as exc:
            raise _refuse(f"invalid regular expression: {exc}", "/query") from None
        which = (repo or "engine").strip().lower()
        keys = list(self.repos) if which == "both" else [self.spec(which).key]
        limit = max(1, min(int(max_results), MAX_RESULTS_CAP))
        prefix = (path_prefix or "").strip().strip("/")
        if prefix and (prefix.startswith("/") or ".." in prefix.split("/")):
            raise _refuse("path_prefix must be a relative path without '..'", "/path_prefix")
        plain_needle = None if regex else (q if case_sensitive else q.lower())

        hits: list[dict[str, Any]] = []
        scanned_files = 0
        scanned_bytes = 0
        truncated = False
        reason: str | None = None
        started = time.monotonic()
        for key in keys:
            spec = self.repos[key]
            root = self.repo_root(key)
            for rel, size in self.index(key):
                if prefix and not (rel == prefix or rel.startswith(prefix + "/")):
                    continue
                if time.monotonic() - started > deadline_s:
                    truncated, reason = True, "deadline"
                    break
                if size > MAX_FILE_BYTES:
                    continue
                if scanned_bytes + size > byte_budget:
                    truncated, reason = True, "byte_budget"
                    break
                data = (root / rel).read_bytes()
                scanned_files += 1
                scanned_bytes += size
                if _is_binary(data):
                    continue
                text = data.decode("utf-8", errors="replace")
                if plain_needle is not None:
                    hay = text if case_sensitive else text.lower()
                    if plain_needle not in hay:
                        continue
                lines = text.split("\n")
                if lines and lines[-1] == "":
                    lines.pop()
                for i, line in enumerate(lines):
                    if i % 2000 == 1999 and time.monotonic() - started > deadline_s:
                        truncated, reason = True, "deadline"
                        break
                    if pattern.search(line) is None:
                        continue
                    n = i + 1
                    hits.append(
                        {
                            "repo": key,
                            "path": rel,
                            "line": n,
                            "text": line,
                            "before": lines[i - 1] if i > 0 else None,
                            "after": lines[i + 1] if i + 1 < len(lines) else None,
                            "citation": spec.citation(rel, n, n),
                            "permalink": spec.permalink(rel, n, n),
                        }
                    )
                    if len(hits) >= limit:
                        truncated, reason = True, "max_results"
                        break
                if truncated:
                    break
            if truncated:
                break
        return {
            "ok": True,
            "query": q,
            "regex": regex,
            "case_sensitive": case_sensitive,
            "repo": which,
            "refs": {k: self.repos[k].tag for k in keys},
            "path_prefix": prefix or None,
            "hits": hits,
            "count": len(hits),
            "truncated": truncated,
            "truncated_reason": reason,
            "scanned_files": scanned_files,
            "scanned_bytes": scanned_bytes,
            "elapsed_s": round(time.monotonic() - started, 3),
        }

    # -- read ----------------------------------------------------------------------------

    def read(
        self,
        repo: str,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
        max_lines: int = DEFAULT_READ_LINES,
    ) -> dict[str, Any]:
        spec = self.spec(repo)
        full, rel = self._safe_path(spec.key, path)
        if not rel or full.is_dir():
            raise _refuse("path is a directory; use list_source to browse it", "/path")
        if not full.is_file():
            raise _refuse(f"no such file {rel!r} in the {spec.key} tree", "/path")
        data = full.read_bytes()
        if _is_binary(data):
            raise _refuse(f"{rel!r} is not a text file", "/path")
        lines = _lines(data)
        total = len(lines)
        cap = max(1, min(int(max_lines), MAX_READ_LINES))
        start = max(1, int(start_line))
        if start > total:
            raise _refuse(f"start_line {start} is past the end ({total} lines)", "/start_line")
        wanted_end = total if end_line is None else max(start, min(int(end_line), total))
        end = min(wanted_end, start + cap - 1)
        body = "\n".join(lines[start - 1 : end])
        return {
            "ok": True,
            "repo": spec.key,
            "path": rel,
            "ref": spec.tag,
            "start_line": start,
            "end_line": end,
            "total_lines": total,
            "text": body,
            "truncated": end < wanted_end,
            "citation": spec.citation(rel, start, end),
            "permalink": spec.permalink(rel, start, end),
        }

    # -- list ----------------------------------------------------------------------------

    def listing(
        self, repo: str, path_prefix: str = "", max_entries: int = DEFAULT_LIST_ENTRIES
    ) -> dict[str, Any]:
        spec = self.spec(repo)
        full, rel = self._safe_path(spec.key, path_prefix or "")
        if not full.exists():
            raise _refuse(f"no such path {rel!r} in the {spec.key} tree", "/path_prefix")
        if full.is_file():
            raise _refuse("path_prefix is a file; use read_source", "/path_prefix")
        cap = max(1, min(int(max_entries), MAX_LIST_ENTRIES))
        entries: list[dict[str, Any]] = []
        children = sorted(full.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        for child in children:
            if child.is_symlink():
                continue
            child_rel = f"{rel}/{child.name}" if rel else child.name
            if child.is_dir():
                n_files = sum(1 for p, _ in self.index(spec.key) if p.startswith(child_rel + "/"))
                entries.append({"path": child_rel, "type": "dir", "files": n_files})
            else:
                entries.append({"path": child_rel, "type": "file", "bytes": child.stat().st_size})
        truncated = len(entries) > cap
        return {
            "ok": True,
            "repo": spec.key,
            "ref": spec.tag,
            "path_prefix": rel,
            "entries": entries[:cap],
            "count": min(len(entries), cap),
            "total": len(entries),
            "truncated": truncated,
            "tree_url": f"{spec.url}/tree/{spec.tag}/{rel}"
            if rel
            else f"{spec.url}/tree/{spec.tag}",
        }


@lru_cache(maxsize=1)
def default_trees() -> SourceTrees:
    return SourceTrees()
