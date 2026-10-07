"""The ``market_data_source`` declaration every tool that takes market numbers requires.

No value describes estimated, recalled or placeholder data: a caller that would
have to invent numbers has no permitted value to declare and must not call the
tool. Nothing here validates the numbers themselves (that is impossible); the
point is the explicit declaration, which the tool echoes in ``notes`` and in the
result (``market_data_source``) and which ``session_put`` stores with the item so a
later ``{"session": name}`` reference carries it forward.
"""

from __future__ import annotations

from typing import Any, Literal

from quantra_mcp.errors import LocalValidationError

MarketDataSource = Literal["user_pasted", "user_file", "engine_example", "session"]
MARKET_DATA_SOURCES: tuple[MarketDataSource, ...] = (
    "user_pasted",
    "user_file",
    "engine_example",
    "session",
)

MEANING: dict[str, str] = {
    "user_pasted": "the user pasted or typed the numbers in this conversation",
    "user_file": "the user attached a file or screenshot the numbers were read from",
    "engine_example": "an engine example's pricing block, run because the user explicitly "
    "asked to run an example",
    "session": "a market previously stored in this session (which itself came from one "
    "of the above)",
}


def check_source(source: Any) -> MarketDataSource:
    """Reject anything outside the enum (the MCP layer already does; impl functions
    called directly get the same guarantee)."""
    for permitted in MARKET_DATA_SOURCES:
        if source == permitted:
            return permitted
    raise LocalValidationError(
        f"market_data_source must be one of {list(MARKET_DATA_SOURCES)}, got {source!r}; "
        "there is no value for estimated, recalled or placeholder data: if the numbers "
        "were not supplied by the user, do not call this tool, ask for them",
        [{"path": "/market_data_source", "message": "not a permitted source"}],
    )


def source_note(source: str) -> str:
    return f"market_data_source={source} (declared by the caller: {MEANING.get(source, '?')})"


def stamp(result: dict[str, Any], source: str) -> dict[str, Any]:
    """Record the declared source in the result and at the head of its ``notes``."""
    result["market_data_source"] = source
    notes = list(result.get("notes") or [])
    result["notes"] = [source_note(source), *notes]
    return result
