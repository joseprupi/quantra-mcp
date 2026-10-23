"""The ``market_data_source`` declaration every tool that takes market numbers requires,
and the ``sources`` citation list that goes with it.

No value describes estimated, recalled or placeholder data: a caller that would
have to invent numbers has no permitted value to declare and must not call the
tool. ``sourced`` covers numbers the model obtained in this conversation from a
trusted public publisher (central bank, treasury, FRED, exchange, index administrator,
regulator filing, fund sponsor; any other site only after asking the user) with a web
or code tool, or from a market-data connector the user connected in the client; it
requires ``sources``, one citation per value (what, value, as_of, url or connector, and
``derived`` when the value was computed from obtained data). Nothing
here validates the numbers themselves (that is impossible); the point is the explicit
declaration, which the tool echoes in ``notes`` and in the result's ``provenance``
block (and ``market_data_source``), and which ``session_put`` stores with the item so
a later ``{"session": name}`` reference carries it forward.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from quantra_mcp.errors import LocalValidationError

MarketDataSource = Literal["user_pasted", "user_file", "sourced", "engine_example", "session"]
MARKET_DATA_SOURCES: tuple[MarketDataSource, ...] = (
    "user_pasted",
    "user_file",
    "sourced",
    "engine_example",
    "session",
)

#: the one-line argument description every tool carries (the full rule and the meaning of
#: each value live in the developers guide, ``quantra://guide/developers``)
SOURCE_DESCRIPTION = (
    "Where the market numbers in this call come from: user_pasted | user_file | "
    "sourced | engine_example | session (meanings and the full rule: "
    "quantra://guide/developers). sourced = obtained in this conversation from a trusted "
    "public publisher or a data connector the user connected, every value cited in "
    "`sources`. There is "
    "no value for estimated, recalled or placeholder data; if you would have to invent "
    "numbers, do not call this tool: ask the user for the data."
)

SOURCES_DESCRIPTION = (
    "Citations of the market numbers: [{what, value, as_of, url | connector, derived?}]; "
    "url = the http(s) page or file, connector = the connected data tool "
    "('<connector>:<tool>'), at least one of the two; as_of YYYY-MM-DD (the data date); "
    "derived = the computation when the value was computed from obtained data (e.g. "
    "'realized 1Y correlation of daily log returns'). REQUIRED and non-empty when "
    "market_data_source=sourced; echoed in the result's provenance."
)

MEANING: dict[str, str] = {
    "user_pasted": "the user pasted or typed the numbers in this conversation",
    "user_file": "the user attached a file or screenshot the numbers were read from",
    "sourced": "obtained in this conversation from a trusted public publisher (central "
    "bank, treasury, FRED, exchange, index administrator, regulator filing, fund sponsor; "
    "any other site only with the user's OK) or a market-data connector the user "
    "connected; every value cited with URL or connector and as-of date",
    "engine_example": "an engine example's pricing block, run because the user explicitly "
    "asked to run an example",
    "session": "a market previously stored in this session (which itself came from one "
    "of the above)",
}


class SourceRef(BaseModel):
    """One cited market number."""

    model_config = ConfigDict(extra="forbid")

    what: str = Field(description="What the value is, e.g. 'SOFR 5Y OIS par rate'.")
    value: str | float | None = Field(
        default=None, description="The value as obtained (or as derived)."
    )
    as_of: str = Field(description="Data date YYYY-MM-DD of the value.")
    url: str | None = Field(default=None, description="The http(s) URL the value was fetched from.")
    connector: str | None = Field(
        default=None,
        description="The connected data tool the value came from (connector:tool).",
    )
    derived: str | None = Field(
        default=None,
        description="How the value was computed from obtained data, when it was.",
    )


def check_source(source: Any) -> MarketDataSource:
    """Reject anything outside the enum (the MCP layer already does; impl functions
    called directly get the same guarantee)."""
    for permitted in MARKET_DATA_SOURCES:
        if source == permitted:
            return permitted
    raise LocalValidationError(
        f"market_data_source must be one of {list(MARKET_DATA_SOURCES)}, got {source!r}; "
        "there is no value for estimated, recalled or placeholder data: if the numbers "
        "were not supplied by the user or obtained from a cited source in this "
        "conversation, do not call this tool, ask for them",
        [{"path": "/market_data_source", "message": "not a permitted source"}],
    )


def _as_dict(item: Any) -> Any:
    if isinstance(item, BaseModel):
        return item.model_dump(exclude_none=True)
    return item


def check_sources(source: Any, sources: Any) -> list[dict[str, Any]] | None:
    """Validate ``sources`` against the declared ``source``; returns the normalized list
    (``None`` when none were given). ``sourced`` requires a non-empty list; every
    given item needs what, an http(s) url or a connector, and an as_of date, whatever
    the source."""
    if sources is None or (isinstance(sources, list) and not sources):
        if source == "sourced":
            raise LocalValidationError(
                "market_data_source=sourced requires `sources`: one entry per market "
                "value, {what, value, as_of, url | connector, derived?}, citing the URL or the "
                "connected data tool you obtained it from in this conversation and its data "
                "date. Without obtained, citable data, do not call this tool: show the user "
                "what is needed and ask for a paste.",
                [{"path": "/sources", "message": "required for sourced"}],
            )
        return None
    if not isinstance(sources, list):
        raise LocalValidationError(
            "sources must be a list of {what, value, as_of, url | connector, derived?}",
            [{"path": "/sources", "message": "not a list"}],
        )
    out: list[dict[str, Any]] = []
    problems: list[dict[str, str]] = []
    for i, raw in enumerate(sources):
        item = _as_dict(raw)
        if not isinstance(item, dict):
            problems.append({"path": f"/sources/{i}", "message": "not an object"})
            continue
        what = item.get("what")
        if not isinstance(what, str) or not what.strip():
            problems.append({"path": f"/sources/{i}/what", "message": "missing: say what it is"})
        url = item.get("url")
        connector = item.get("connector")
        has_connector = isinstance(connector, str) and bool(connector.strip())
        if url is not None or not has_connector:
            parsed = urlparse(url) if isinstance(url, str) else None
            if parsed is None or parsed.scheme not in ("http", "https") or not parsed.netloc:
                problems.append(
                    {
                        "path": f"/sources/{i}/url",
                        "message": f"need an http(s) url or a connector; url={url!r}",
                    }
                )
        as_of = item.get("as_of")
        ok_date = False
        if isinstance(as_of, str) and len(as_of) == 10:
            try:
                dt.date.fromisoformat(as_of)
                ok_date = True
            except ValueError:
                pass
        if not ok_date:
            problems.append(
                {
                    "path": f"/sources/{i}/as_of",
                    "message": f"missing or not a YYYY-MM-DD date: {as_of!r}",
                }
            )
        out.append(dict(item))
    if problems:
        raise LocalValidationError(
            f"sources: {len(problems)} problem{'s' if len(problems) != 1 else ''}; every "
            "cited value needs `what`, where it came from (an http(s) `url` or the "
            "`connector` tool) and its `as_of` data date (YYYY-MM-DD). Fix the citations; "
            "never fill them in from memory.",
            problems,
        )
    return out


def source_refusal(source: Any, sources: Any) -> dict[str, Any] | None:
    """The tool result refusing a call whose declaration does not hold up (``ok: false``,
    an MCP tool error), or ``None`` when it does. Called BEFORE anything is sent."""
    try:
        check_source(source)
        check_sources(source, sources)
    except LocalValidationError as exc:
        return {
            "ok": False,
            "status": None,
            "error": exc.error,
            "problems": exc.problems,
            "market_data_source": source,
        }
    return None


def source_note(source: str, sources: list[dict[str, Any]] | None = None) -> str:
    note = f"market_data_source={source} (declared by the caller: {MEANING.get(source, '?')})"
    if sources:
        note += f"; {len(sources)} cited source{'s' if len(sources) != 1 else ''} in provenance"
    return note


def stamp(result: dict[str, Any], source: str, sources: Any = None) -> dict[str, Any]:
    """Record the declared source (and its citations) in the result's ``provenance``
    block, as ``market_data_source`` and at the head of its ``notes``."""
    cited = [_as_dict(s) for s in sources] if isinstance(sources, list) and sources else None
    result["market_data_source"] = source
    result["provenance"] = {
        "market_data_source": source,
        "meaning": MEANING.get(source, "?"),
        "sources": cited or [],
    }
    notes = list(result.get("notes") or [])
    result["notes"] = [source_note(source, cited), *notes]
    return result
