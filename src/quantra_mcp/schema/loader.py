"""Load the vendored OpenAPI spec; resolve refs; list endpoints and enums.

Nothing here is engine-specific beyond the ``quantra_`` / ``quantra_enums_``
component prefixes the engine's generator uses.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parent
SPEC_PATH = SCHEMA_DIR / "openapi3.json"
PIN_PATH = SCHEMA_DIR / "PIN"

REF_PREFIX = "#/components/schemas/"
ENUM_PREFIX = "quantra_enums_"
COMPONENT_PREFIX = "quantra_"


class SpecError(LookupError):
    """An endpoint, schema or enum name that the vendored spec does not define."""


@dataclass(frozen=True, slots=True)
class Pin:
    tag: str
    sha: str

    @property
    def version(self) -> str:
        """``v0.7.0`` -> ``0.7.0``."""
        return self.tag.removeprefix("v")


@dataclass(frozen=True, slots=True)
class EndpointInfo:
    path: str
    summary: str
    description: str
    tag: str
    request_schema: str
    response_schema: str


def normalize_endpoint(endpoint: str) -> str:
    """``price-ois-swap`` / ``/price-ois-swap/`` -> ``/price-ois-swap``."""
    e = endpoint.strip()
    if not e.startswith("/"):
        e = "/" + e
    return e.rstrip("/") or "/"


def _ref_name(ref: str) -> str:
    if not ref.startswith(REF_PREFIX):
        raise SpecError(f"unsupported $ref {ref!r}")
    return ref.removeprefix(REF_PREFIX)


def _short(name: str) -> str:
    """``quantra_enums_Calendar`` -> ``Calendar``; ``quantra_Pricing`` -> ``Pricing``."""
    if name.startswith(ENUM_PREFIX):
        return name.removeprefix(ENUM_PREFIX)
    return name.removeprefix(COMPONENT_PREFIX)


class Spec:
    """The vendored spec plus the lookups the tools need."""

    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        self.schemas: dict[str, Any] = document["components"]["schemas"]
        self.api_version: str = str(document["info"]["version"])
        self._endpoints = self._collect_endpoints()
        self._enum_by_short = {
            _short(n): n
            for n, s in self.schemas.items()
            if n.startswith(ENUM_PREFIX) and "enum" in s
        }

    # --- endpoints --------------------------------------------------------

    def _collect_endpoints(self) -> dict[str, EndpointInfo]:
        out: dict[str, EndpointInfo] = {}
        for path, ops in self.document["paths"].items():
            op = ops.get("post")
            if op is None:
                continue
            req = op["requestBody"]["content"]["application/json"]["schema"]["$ref"]
            resp = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
            tags = op.get("tags") or [""]
            out[path] = EndpointInfo(
                path=path,
                summary=str(op.get("summary", "")).strip(),
                description=" ".join(str(op.get("description", "")).split()),
                tag=str(tags[0]),
                request_schema=_ref_name(req),
                response_schema=_ref_name(resp),
            )
        return out

    @property
    def endpoints(self) -> list[EndpointInfo]:
        return list(self._endpoints.values())

    @property
    def endpoint_paths(self) -> list[str]:
        return list(self._endpoints)

    def endpoint(self, endpoint: str) -> EndpointInfo:
        path = normalize_endpoint(endpoint)
        try:
            return self._endpoints[path]
        except KeyError:
            raise SpecError(
                f"unknown endpoint {endpoint!r}; POST endpoints are: {', '.join(self._endpoints)}"
            ) from None

    # --- schemas ----------------------------------------------------------

    def schema(self, name: str) -> dict[str, Any]:
        """A component schema by full (``quantra_Pricing``) or short (``Pricing``) name."""
        if name in self.schemas:
            return dict(self.schemas[name])
        for prefix in (COMPONENT_PREFIX, ENUM_PREFIX):
            if prefix + name in self.schemas:
                return dict(self.schemas[prefix + name])
        raise SpecError(f"unknown schema {name!r}")

    def request_schema(self, endpoint: str) -> dict[str, Any]:
        return self.schema(self.endpoint(endpoint).request_schema)

    def response_schema(self, endpoint: str) -> dict[str, Any]:
        return self.schema(self.endpoint(endpoint).response_schema)

    def required_fields(self, endpoint: str) -> list[str]:
        """Top-level ``required`` of the request schema (the spec's own list)."""
        req = self.request_schema(endpoint)
        return [str(r) for r in req.get("required", [])]

    def top_level_properties(self, endpoint: str) -> list[str]:
        return list(self.request_schema(endpoint).get("properties", {}))

    def resolve(self, schema: Any, depth: int) -> Any:
        """Inline ``$ref``s up to ``depth`` levels.

        A ref beyond the depth is left as ``{"$ref": "<short name>", "unresolved": true}``
        so the agent can ask for that schema explicitly. Enums are always
        inlined (they are leaves). Cycles are cut by the depth.
        """
        return self._resolve(copy.deepcopy(schema), depth)

    def _resolve(self, node: Any, depth: int) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                name = _ref_name(ref)
                target = self.schemas[name]
                is_enum = "enum" in target
                if depth <= 0 and not is_enum:
                    return {"$ref": _short(name), "unresolved": True}
                merged = copy.deepcopy(target)
                for k, v in node.items():
                    if k != "$ref":
                        merged[k] = v
                merged.setdefault("title", _short(name))
                return self._resolve(merged, depth if is_enum else depth - 1)
            return {k: self._resolve(v, depth) for k, v in node.items()}
        if isinstance(node, list):
            return [self._resolve(v, depth) for v in node]
        return node

    # --- enums ------------------------------------------------------------

    @property
    def enum_names(self) -> list[str]:
        return list(self._enum_by_short)

    def enum_values(self, name: str) -> list[str]:
        key = _short(name) if name.startswith(ENUM_PREFIX) else name
        try:
            full = self._enum_by_short[key]
        except KeyError:
            raise SpecError(
                f"unknown enum {name!r}; available enums: {', '.join(self._enum_by_short)}"
            ) from None
        return [str(v) for v in self.schemas[full]["enum"]]


@lru_cache(maxsize=1)
def load_spec(path: Path = SPEC_PATH) -> Spec:
    with path.open("rb") as fh:
        return Spec(json.load(fh))


@lru_cache(maxsize=1)
def pin(path: Path = PIN_PATH) -> Pin:
    tag, sha = path.read_text().split()
    return Pin(tag=tag, sha=sha)
