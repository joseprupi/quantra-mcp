"""Tier 6b: ``search_source`` / ``read_source`` / ``list_source`` -- the engine source at
the pinned tag and the QuantLib source at the pinned version, served read-only from the
vendored trees (``scripts/fetch_sources.py``; the container image ships them). No engine
call, no shelling out; every result carries a ``citation`` and a GitHub ``permalink``."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from quantra_mcp.errors import LocalValidationError
from quantra_mcp.sources import (
    DEFAULT_LIST_ENTRIES,
    DEFAULT_MAX_RESULTS,
    DEFAULT_READ_LINES,
    MAX_LIST_ENTRIES,
    MAX_READ_LINES,
    MAX_RESULTS_CAP,
    SourceTrees,
)
from quantra_mcp.tools._meta import tool

Repo = Literal["engine", "quantlib"]
SearchRepo = Literal["engine", "quantlib", "both"]


def _failed(exc: LocalValidationError, trees: SourceTrees) -> dict[str, Any]:
    return {
        "ok": False,
        "error": exc.error,
        "problems": exc.problems,
        "sources": trees.stats(),
    }


def search_source_impl(
    trees: SourceTrees,
    query: str,
    repo: str = "engine",
    regex: bool = False,
    path_prefix: str | None = None,
    max_results: int = DEFAULT_MAX_RESULTS,
    case_sensitive: bool = False,
) -> dict[str, Any]:
    try:
        return trees.search(
            query,
            repo,
            regex=regex,
            path_prefix=path_prefix,
            max_results=max_results,
            case_sensitive=case_sensitive,
        )
    except LocalValidationError as exc:
        return _failed(exc, trees)


def read_source_impl(
    trees: SourceTrees,
    repo: str,
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
    max_lines: int = DEFAULT_READ_LINES,
) -> dict[str, Any]:
    try:
        return trees.read(repo, path, start_line, end_line, max_lines)
    except LocalValidationError as exc:
        return _failed(exc, trees)


def list_source_impl(
    trees: SourceTrees, repo: str, path_prefix: str = "", max_entries: int = DEFAULT_LIST_ENTRIES
) -> dict[str, Any]:
    try:
        return trees.listing(repo, path_prefix, max_entries)
    except LocalValidationError as exc:
        return _failed(exc, trees)


def register(app: MCPServer, trees: SourceTrees) -> None:
    @tool(app, "Search the engine and QuantLib source at the pin", engine=False)
    def search_source(
        query: Annotated[
            str,
            Field(
                description="Text to find (at least 3 characters; case-insensitive unless "
                "case_sensitive); a Python regular expression when regex is true "
                "(at most 256 characters).",
                min_length=3,
                max_length=256,
            ),
        ],
        repo: Annotated[
            SearchRepo,
            Field(
                description="engine (joseprupi/quantraserver at the pinned tag: src/, "
                "jsonserver/, flatbuffers/*.fbs, docs/, the functional CATALOG), quantlib "
                "(lballabio/QuantLib ql/ at the pinned version) or both."
            ),
        ] = "engine",
        regex: Annotated[
            bool, Field(description="Treat query as a regular expression (matched per line).")
        ] = False,
        path_prefix: Annotated[
            str | None,
            Field(
                description="Only files under this relative directory (or this one file), "
                "e.g. ql/termstructures/yield."
            ),
        ] = None,
        max_results: Annotated[
            int,
            Field(description=f"Cap on hits (1..{MAX_RESULTS_CAP}).", ge=1, le=MAX_RESULTS_CAP),
        ] = DEFAULT_MAX_RESULTS,
        case_sensitive: Annotated[bool, Field(description="Match case exactly.")] = False,
    ) -> dict[str, Any]:
        """Find where something is defined or used in the engine source at the pinned tag or
        in the QuantLib source at the pinned version (no engine call; the trees are on
        disk). ``hits`` are sorted by path then line, each with the matching line, one
        line of context before and after, a ``citation`` (``<repo>:<path>@<tag>:L<n>-L<n>``)
        and a GitHub ``permalink``; ``truncated`` says whether more hits exist
        (``max_results``, the scan byte budget or the time guard). Use it when
        explain_method's page says something is not documented, or to follow the engine
        into the QuantLib class it instantiates (search the class name in quantlib,
        then read_source the file). Cite what you read; never answer from memory when
        the file is readable here.
        """
        return search_source_impl(
            trees, query, repo, regex, path_prefix, max_results, case_sensitive
        )

    @tool(app, "Read lines of a source file at the pin", engine=False)
    def read_source(
        repo: Annotated[Repo, Field(description="engine or quantlib.")],
        path: Annotated[
            str,
            Field(
                description="File path relative to the repository root, as returned by "
                "search_source / list_source (e.g. ql/termstructures/iterativebootstrap.hpp)."
            ),
        ],
        start_line: Annotated[int, Field(description="First line (1-based).", ge=1)] = 1,
        end_line: Annotated[
            int | None,
            Field(description="Last line (inclusive); default: start_line + max_lines - 1.", ge=1),
        ] = None,
        max_lines: Annotated[
            int,
            Field(
                description=f"Cap on returned lines (1..{MAX_READ_LINES}).", ge=1, le=MAX_READ_LINES
            ),
        ] = DEFAULT_READ_LINES,
    ) -> dict[str, Any]:
        """Return a line range of one file of the engine (pinned tag) or QuantLib (pinned
        version) source tree, verbatim: ``text``, ``start_line``, ``end_line``,
        ``total_lines``, ``ref`` (the tag), the ``citation``
        (``<repo>:<path>@<tag>:L<a>-L<b>``) and the GitHub ``permalink`` (``#La-Lb``) to
        quote next to any statement taken from it. At most 400 lines per call; ``truncated``
        is true when the requested range was longer. Binary files and paths outside the tree
        are refused. No engine call.
        """
        return read_source_impl(trees, repo, path, start_line, end_line, max_lines)

    @tool(app, "Browse a source directory at the pin", engine=False)
    def list_source(
        repo: Annotated[Repo, Field(description="engine or quantlib.")],
        path_prefix: Annotated[
            str,
            Field(
                description="Directory relative to the repository root ('' = the root), e.g. "
                "ql/termstructures/yield or src/evaluators."
            ),
        ] = "",
        max_entries: Annotated[
            int,
            Field(
                description=f"Cap on entries (1..{MAX_LIST_ENTRIES}).", ge=1, le=MAX_LIST_ENTRIES
            ),
        ] = DEFAULT_LIST_ENTRIES,
    ) -> dict[str, Any]:
        """List the immediate children of a directory in the engine (pinned tag) or QuantLib
        (pinned version) source tree: directories first (with their file counts), then files
        (with sizes), plus the GitHub ``tree_url``. Cheap; use it to find the file to
        read_source or the prefix to search_source (e.g. ql/termstructures/yield holds the
        bootstrapped yield curves). No engine call.
        """
        return list_source_impl(trees, repo, path_prefix, max_entries)
