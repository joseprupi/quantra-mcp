"""Content digests of built market objects.

A builder result (``build_curve`` / ``build_value_curve`` /
``curve_from_pasted_table``) carries ``digest`` = sha256 of the canonical JSON of
``{curve, indices}``; the curve's own digest is remembered here, keyed on the MCP
session (a bounded least-recently-used set per session, a process-wide bounded
set for stdio / in-process clients and for sessionless requests). A pricing or
analytics tool looks every curve of its market up and says in ``notes`` whether
it came out of a builder in this process or was hand-typed. Nothing is added to
what is sent to the engine (it rejects unknown fields); the digest lives in the
tool result and the session item only.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from typing import Any

from quantra_mcp.session import SESSIONLESS, current_session_id

PROCESS = "process"
DEFAULT_PER_BUCKET = 512
DEFAULT_BUCKETS = 256


def canonical_digest(obj: Any) -> str:
    """``sha256:<hex>`` of the canonical (sorted keys, no whitespace) JSON of ``obj``."""
    data = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(data.encode()).hexdigest()


class DigestRegistry:
    """Bounded LRU sets of digests, one per session key."""

    def __init__(
        self, per_bucket: int = DEFAULT_PER_BUCKET, max_buckets: int = DEFAULT_BUCKETS
    ) -> None:
        self.per_bucket = per_bucket
        self.max_buckets = max_buckets
        self._buckets: OrderedDict[str, OrderedDict[str, None]] = OrderedDict()

    @staticmethod
    def key() -> str:
        sid = current_session_id()
        return PROCESS if sid is None or sid == SESSIONLESS else sid

    def _bucket(self, create: bool) -> OrderedDict[str, None] | None:
        k = self.key()
        bucket = self._buckets.get(k)
        if bucket is None:
            if not create:
                return None
            bucket = OrderedDict()
            self._buckets[k] = bucket
            while len(self._buckets) > self.max_buckets:
                self._buckets.popitem(last=False)
        else:
            self._buckets.move_to_end(k)
        return bucket

    def remember(self, *digests: str) -> None:
        bucket = self._bucket(create=True)
        assert bucket is not None
        for d in digests:
            bucket.pop(d, None)
            bucket[d] = None
        while len(bucket) > self.per_bucket:
            bucket.popitem(last=False)

    def known(self, digest: str) -> bool:
        bucket = self._bucket(create=False)
        return bucket is not None and digest in bucket

    def clear(self) -> None:
        self._buckets.clear()


#: the one registry of this process
REGISTRY = DigestRegistry()


def stamp_built(result: dict[str, Any]) -> dict[str, Any]:
    """Add ``digest`` to a builder result and remember the curve's own digest."""
    curve = result.get("curve")
    if not result.get("ok") or not isinstance(curve, dict):
        return result
    curve_digest = canonical_digest(curve)
    result["digest"] = canonical_digest({"curve": curve, "indices": result.get("indices") or []})
    result["curve_digest"] = curve_digest
    REGISTRY.remember(curve_digest, result["digest"])
    return result


def market_notes(pricing: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Per curve of ``pricing.rates.curves``: its digest and whether a builder in this
    process produced it; returns the notes and a ``{curve_id: {digest, from_builder}}`` map."""
    rates = pricing.get("rates") if isinstance(pricing, dict) else None
    curves = rates.get("curves") if isinstance(rates, dict) else None
    notes: list[str] = []
    info: dict[str, Any] = {}
    for c in curves or []:
        if not isinstance(c, dict):
            continue
        d = canonical_digest(c)
        cid = str(c.get("id"))
        from_builder = REGISTRY.known(d)
        info[cid] = {"digest": d, "from_builder": from_builder}
        if from_builder:
            notes.append(f"market curve {cid!r}: from a builder result in this session ({d})")
        else:
            notes.append(
                f"market curve {cid!r}: market block not produced by a builder in this "
                f"session ({d}); its numbers are as the caller supplied them"
            )
    return notes, info
