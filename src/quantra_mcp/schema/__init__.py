"""The vendored engine contract (OpenAPI spec pinned to an engine tag).

The validator (and its ``jsonschema`` dependency) is imported lazily so the
pure data modules (``enums_generated``, ``loader``) stay importable from a
bare interpreter, e.g. ``scripts/parity_ql.py`` under the engine's QuantLib.
"""

from __future__ import annotations

from typing import Any

from quantra_mcp.schema.loader import (
    EndpointInfo,
    Spec,
    SpecError,
    load_spec,
    pin,
)

__all__ = [
    "EndpointInfo",
    "Spec",
    "SpecError",
    "ValidationProblem",
    "load_spec",
    "pin",
    "validate_request",
]


def __getattr__(name: str) -> Any:
    if name in ("ValidationProblem", "validate_request"):
        from quantra_mcp.schema import validate

        return getattr(validate, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
