"""Validate a request body against the vendored spec before it leaves the box.

The engine would reject the same mistakes, but its message names one field
at a time; here the agent gets every problem with a JSON-pointer path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from jsonschema import Draft7Validator
from jsonschema.exceptions import ValidationError

from quantra_mcp.schema.loader import REF_PREFIX, Spec, load_spec


@dataclass(frozen=True, slots=True)
class ValidationProblem:
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message}


def _pointer(error: ValidationError) -> str:
    parts = [str(p) for p in error.absolute_path]
    return "/" + "/".join(parts) if parts else "/"


def _message(error: ValidationError) -> str:
    msg = error.message
    if error.validator == "enum" and isinstance(error.schema, Mapping):
        allowed = error.schema.get("enum", [])
        msg = f"{error.instance!r} is not one of {allowed}"
    elif error.validator == "additionalProperties":
        msg = msg.replace("were unexpected", "are not fields of this object").replace(
            "was unexpected", "is not a field of this object"
        )
    return msg


def _most_relevant(error: ValidationError) -> ValidationError:
    """For an anyOf union (curve helper points, vol payloads...) descend into the
    branch that got furthest (deepest path). Branches that tie with the same
    problem (several helpers share a numeric ``rate``) collapse to one; branches
    that tie with different problems mean no branch matched, and the union as a
    whole is reported rather than every branch's mismatch.
    """
    while error.context:
        deepest = max(len(c.absolute_path) for c in error.context)
        tied = [c for c in error.context if len(c.absolute_path) == deepest]
        distinct = {(_pointer(c), _message(c)) for c in tied}
        if len(distinct) != 1:
            return error
        error = tied[0]
    return error


@lru_cache(maxsize=64)
def _validator(schema_name: str) -> Draft7Validator:
    spec = load_spec()
    root = {"$ref": REF_PREFIX + schema_name, "components": spec.document["components"]}
    return Draft7Validator(root)


def validate_request(endpoint: str, body: Any, spec: Spec | None = None) -> list[ValidationProblem]:
    """All problems found in ``body`` for ``endpoint`` (empty list == valid)."""
    spec = spec or load_spec()
    info = spec.endpoint(endpoint)
    validator = _validator(info.request_schema)
    errors = sorted(validator.iter_errors(body), key=lambda e: (list(e.absolute_path), e.message))
    seen: set[tuple[str, str]] = set()
    out: list[ValidationProblem] = []
    for err in errors:
        cand = _most_relevant(err)
        key = (_pointer(cand), _message(cand))
        if key in seen:
            continue
        seen.add(key)
        out.append(ValidationProblem(path=key[0], message=key[1]))
    return out
