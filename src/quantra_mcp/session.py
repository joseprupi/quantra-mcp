"""In-memory session scratch: named curves / indices / market blocks reused across calls.

One :class:`SessionStore` per MCP session, held by a :class:`SessionStores`
registry keyed on the transport's session id (the ``Mcp-Session-Id`` the
streamable-HTTP transport mints at ``initialize``); stdio and in-memory clients,
which have no session id, share one implicit session. Bounded three ways:
least-recently-used eviction at ``QUANTRA_SESSION_MAX_ITEMS`` items per session,
at ``QUANTRA_SESSION_MAX_SESSIONS`` sessions (whole least-recently-used sessions
go), and at ``QUANTRA_SESSION_MAX_TOTAL_BYTES`` of serialized JSON across every
session together. Never persisted, never shared across processes, never visible
to another session: a ``session_get`` miss lists only the caller's own names.

Tools receive the registry, which implements the store interface by delegating
to the session bound to the current request (:func:`bind_session`, set by the
server's per-request middleware), so a tool body never sees another client's
items. Tools resolve ``{"session": "<name>"}`` references through
:func:`resolve_refs`, and the echoed request always shows the resolved body.
"""

from __future__ import annotations

import contextvars
import datetime as dt
import json
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from quantra_mcp.errors import LocalValidationError

SessionKind = Literal["curve", "index", "market"]
SESSION_KINDS: tuple[SessionKind, ...] = ("curve", "index", "market")

#: the key of the one implicit session used by stdio and in-process clients
LOCAL_SESSION = "local"
#: bound for an HTTP request that carries no session id (protocol 2026-07-28 is
#: single-exchange by design: no ``initialize``, no ``Mcp-Session-Id``); there is no
#: identity to key scratch on, so scratch is unavailable rather than shared
SESSIONLESS = "sessionless"
DEFAULT_MAX_SESSIONS = 256

SESSIONLESS_ERROR = (
    "session scratch is unavailable on this transport: the request carries no MCP session "
    "id (single-exchange streamable HTTP, protocol 2026-07-28), so there is nothing to key "
    "a private store on and a shared one would expose your market data to other clients. "
    'Pass the built objects inline instead of {"session": name}: a build_curve result '
    "goes directly into `curves` / `market`, an IndexDef into `indices`."
)

_current_session: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "quantra_mcp_session", default=None
)


def current_session_id() -> str | None:
    """The MCP session id bound to the running request, ``None`` outside one."""
    return _current_session.get()


@contextmanager
def bind_session(session_id: str | None) -> Iterator[None]:
    """Bind ``session_id`` (``None`` = the implicit local session) for the block."""
    token = _current_session.set(session_id)
    try:
        yield
    finally:
        _current_session.reset(token)


#: what each kind must look like (shallow shape check; the spec validator does the rest)
_SHAPE_HINT = {
    "curve": "an engine TermStructure object (id, reference_date, day_counter, interpolator, "
    "bootstrap_trait, points) or a build_curve result ({curve, indices})",
    "index": "an engine IndexDef object (id, name, index_type, ...)",
    "market": "an object with 'curves' and/or 'indices' lists",
}


@dataclass(slots=True)
class SessionItem:
    name: str
    kind: str
    value: dict[str, Any]
    stored_at: str
    size_bytes: int = 0
    #: the caller's declaration of where the numbers came from (user_pasted / user_file /
    #: engine_example / session); None only for items stored without one (direct API use)
    market_data_source: str | None = None

    def summary(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "stored_at": self.stored_at,
            "size_bytes": self.size_bytes,
            "market_data_source": self.market_data_source,
        }
        v = self.value
        if self.kind == "curve":
            row["curve_id"] = v.get("id")
            row["points"] = len(v.get("points") or [])
            row["bootstrap_trait"] = v.get("bootstrap_trait")
            row["reference_date"] = v.get("reference_date")
        elif self.kind == "index":
            row["index_id"] = v.get("id")
            row["index_type"] = v.get("index_type")
        else:
            row["curves"] = [c.get("id") for c in v.get("curves") or [] if isinstance(c, dict)]
            row["indices"] = [c.get("id") for c in v.get("indices") or [] if isinstance(c, dict)]
        return row


class ScratchStore(Protocol):
    """What the tools need from a session scratch (a :class:`SessionStore` or the
    per-session :class:`SessionStores` registry)."""

    @property
    def max_items(self) -> int: ...

    def put(
        self, name: str, kind: str, value: Any, market_data_source: str | None = None
    ) -> tuple[SessionItem, str | None]: ...

    def delete(self, name: str) -> bool: ...

    def clear(self) -> None: ...

    def get(self, name: str) -> SessionItem: ...

    def attached_indices(self, name: str) -> list[dict[str, Any]]: ...

    def items(self) -> list[SessionItem]: ...

    def __len__(self) -> int: ...


class SessionStore:
    def __init__(self, max_items: int = 64, max_total_bytes: int = 32 * 1024 * 1024) -> None:
        if max_items < 1:
            raise ValueError("max_items must be >= 1")
        if max_total_bytes < 1:
            raise ValueError("max_total_bytes must be >= 1")
        self.max_items = max_items
        self.max_total_bytes = max_total_bytes
        self._items: OrderedDict[str, SessionItem] = OrderedDict()
        self._attached_indices: dict[str, list[dict[str, Any]]] = {}

    @property
    def total_bytes(self) -> int:
        return sum(item.size_bytes for item in self._items.values())

    # --- mutation ---------------------------------------------------------

    def put(
        self, name: str, kind: str, value: Any, market_data_source: str | None = None
    ) -> tuple[SessionItem, str | None]:
        """Store (or replace) ``name``. Returns the item and the evicted names (comma-joined)
        if the item cap or the byte cap pushed older items out, else ``None``.

        ``market_data_source`` is the caller's declaration; a build_curve result carries
        its own (``value["market_data_source"]``), which is used when no argument is given
        and must agree with the argument when both are present."""
        if not isinstance(name, str) or not name.strip():
            raise LocalValidationError(
                "name: a non-empty string is required", [{"path": "/name", "message": "empty"}]
            )
        if kind not in SESSION_KINDS:
            raise LocalValidationError(
                f"kind must be one of {SESSION_KINDS}, got {kind!r}",
                [{"path": "/kind", "message": "unknown kind"}],
            )
        if not isinstance(value, dict):
            raise LocalValidationError(
                f"value must be {_SHAPE_HINT[kind]}",
                [{"path": "/value", "message": "not an object"}],
            )
        attached: list[dict[str, Any]] = []
        carried = value.get("market_data_source")
        if isinstance(carried, str):
            if market_data_source is None:
                market_data_source = carried
            elif market_data_source != carried:
                raise LocalValidationError(
                    f"market_data_source {market_data_source!r} differs from the one the value "
                    f"carries ({carried!r}); store it under the source it really has",
                    [{"path": "/market_data_source", "message": "conflicts with value"}],
                )
        if kind == "curve" and "curve" in value and isinstance(value["curve"], dict):
            # a build_curve result: keep its indices alongside the curve
            attached = [i for i in value.get("indices") or [] if isinstance(i, dict)]
            value = value["curve"]
        self._check_shape(kind, value)
        size = len(json.dumps(value, separators=(",", ":"))) + sum(
            len(json.dumps(i, separators=(",", ":"))) for i in attached
        )
        if size > self.max_total_bytes:
            raise LocalValidationError(
                f"value is {size} bytes serialized; the session store holds at most "
                f"{self.max_total_bytes} bytes in total",
                [{"path": "/value", "message": "too large"}],
            )
        now = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()
        item = SessionItem(
            name=name,
            kind=kind,
            value=value,
            stored_at=now,
            size_bytes=size,
            market_data_source=market_data_source,
        )
        if name in self._items:
            del self._items[name]
        self._items[name] = item
        self._attached_indices[name] = attached
        evicted: list[str] = []
        while len(self._items) > self.max_items or self.total_bytes > self.max_total_bytes:
            gone, _ = self._items.popitem(last=False)
            self._attached_indices.pop(gone, None)
            evicted.append(gone)
        return item, (", ".join(evicted) if evicted else None)

    def delete(self, name: str) -> bool:
        self._attached_indices.pop(name, None)
        return self._items.pop(name, None) is not None

    def clear(self) -> None:
        self._items.clear()
        self._attached_indices.clear()

    # --- reads ------------------------------------------------------------

    def get(self, name: str) -> SessionItem:
        try:
            item = self._items[name]
        except KeyError:
            raise LocalValidationError(
                f"no session item named {name!r}; stored: {', '.join(self._items) or '(none)'}",
                [{"path": "/session", "message": "unknown name"}],
            ) from None
        self._items.move_to_end(name)
        return item

    def attached_indices(self, name: str) -> list[dict[str, Any]]:
        return list(self._attached_indices.get(name, []))

    def items(self) -> list[SessionItem]:
        return list(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    # --- helpers ----------------------------------------------------------

    @staticmethod
    def _check_shape(kind: str, value: dict[str, Any]) -> None:
        ok = True
        if kind == "curve":
            ok = isinstance(value.get("id"), str) and isinstance(value.get("points"), list)
        elif kind == "index":
            ok = isinstance(value.get("id"), str) and isinstance(value.get("name"), str)
        else:
            ok = (
                isinstance(value.get("curves", []), list)
                and isinstance(value.get("indices", []), list)
                and ("curves" in value or "indices" in value)
            )
        if not ok:
            raise LocalValidationError(
                f"value does not look like {_SHAPE_HINT[kind]}",
                [{"path": "/value", "message": f"not a {kind}"}],
            )


class SessionStores:
    """One :class:`SessionStore` per MCP session, LRU-bounded, with a shared byte budget.

    Implements the :class:`SessionStore` interface itself by delegating to the store
    of the session bound to the current request (:func:`bind_session`), so the tools
    hold one object and never pick the wrong session. Sessions beyond
    ``max_sessions`` are evicted least-recently-used as whole; ``max_total_bytes``
    is the budget across every session together (each session's own store is
    capped at the same figure, so a single session can never exceed it, and
    the least-recently-used *other* sessions are dropped when the total is over).
    """

    def __init__(
        self,
        max_items: int = 64,
        max_total_bytes: int = 32 * 1024 * 1024,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
    ) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be >= 1")
        self.max_items = max_items
        self.max_total_bytes = max_total_bytes
        self.max_sessions = max_sessions
        self._stores: OrderedDict[str, SessionStore] = OrderedDict()

    # --- registry -----------------------------------------------------------

    @staticmethod
    def key(session_id: str | None) -> str:
        return session_id or LOCAL_SESSION

    def for_session(self, session_id: str | None) -> SessionStore:
        """The store of ``session_id`` (created on first use; marks it most recently used).

        Raises :class:`LocalValidationError` for :data:`SESSIONLESS`."""
        k = self.key(session_id)
        if k == SESSIONLESS:
            raise LocalValidationError(
                SESSIONLESS_ERROR, [{"path": "/session", "message": "no MCP session id"}]
            )
        store = self._stores.get(k)
        if store is None:
            store = SessionStore(self.max_items, self.max_total_bytes)
            self._stores[k] = store
            while len(self._stores) > self.max_sessions:
                self._stores.popitem(last=False)
        else:
            self._stores.move_to_end(k)
        return store

    def current(self) -> SessionStore:
        return self.for_session(current_session_id())

    def drop_session(self, session_id: str | None) -> bool:
        return self._stores.pop(self.key(session_id), None) is not None

    @property
    def available(self) -> bool:
        """False when the current request is bound to :data:`SESSIONLESS`."""
        return current_session_id() != SESSIONLESS

    @property
    def session_count(self) -> int:
        return len(self._stores)

    @property
    def total_bytes(self) -> int:
        return sum(s.total_bytes for s in self._stores.values())

    def _enforce_budget(self, keep: str) -> list[str]:
        """Drop least-recently-used sessions other than ``keep`` until under budget."""
        dropped: list[str] = []
        while self.total_bytes > self.max_total_bytes and len(self._stores) > 1:
            for k in list(self._stores):
                if k != keep:
                    del self._stores[k]
                    dropped.append(k)
                    break
            else:  # pragma: no cover - only ``keep`` is left
                break
        return dropped

    # --- SessionStore interface, delegated to the current session --------------

    def put(
        self, name: str, kind: str, value: Any, market_data_source: str | None = None
    ) -> tuple[SessionItem, str | None]:
        k = self.key(current_session_id())
        result = self.for_session(k).put(name, kind, value, market_data_source)
        self._enforce_budget(k)
        return result

    def delete(self, name: str) -> bool:
        return self.current().delete(name)

    def clear(self) -> None:
        self.current().clear()

    def get(self, name: str) -> SessionItem:
        return self.current().get(name)

    def attached_indices(self, name: str) -> list[dict[str, Any]]:
        return self.current().attached_indices(name)

    def items(self) -> list[SessionItem]:
        return self.current().items()

    def __len__(self) -> int:
        return len(self.current())


def is_ref(item: Any) -> bool:
    return isinstance(item, dict) and set(item) == {"session"} and isinstance(item["session"], str)


def resolve_refs(
    store: ScratchStore,
    curves: list[Any],
    indices: list[Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Expand ``{"session": name}`` references and build_curve results.

    ``curves`` items may be a TermStructure, ``{"curve": ..., "indices": [...]}``
    (a build_curve result) or a session ref to a ``curve`` or ``market`` item.
    ``indices`` items may be an IndexDef or a session ref to an ``index`` or
    ``market`` item. Returns resolved curves, resolved indices (undeduped) and
    notes describing every expansion.
    """
    out_curves: list[dict[str, Any]] = []
    out_indices: list[dict[str, Any]] = []
    notes: list[str] = []
    for i, item in enumerate(curves):
        if is_ref(item):
            name = item["session"]
            got = store.get(name)
            if got.kind == "curve":
                out_curves.append(got.value)
                attached = store.attached_indices(name)
                out_indices.extend(attached)
                notes.append(
                    f"curves[{i}] <- session {name!r} (curve {got.value.get('id')}"
                    + (f", +{len(attached)} attached index" if attached else "")
                    + f"; stored market_data_source={got.market_data_source})"
                )
            elif got.kind == "market":
                cs = [c for c in got.value.get("curves") or [] if isinstance(c, dict)]
                ixs = [c for c in got.value.get("indices") or [] if isinstance(c, dict)]
                out_curves.extend(cs)
                out_indices.extend(ixs)
                notes.append(
                    f"curves[{i}] <- session {name!r} "
                    f"(market: {len(cs)} curves, {len(ixs)} indices; "
                    f"stored market_data_source={got.market_data_source})"
                )
            else:
                raise LocalValidationError(
                    f"curves[{i}]: session {name!r} is an index, not a curve or market",
                    [{"path": f"/curves/{i}", "message": "wrong session kind"}],
                )
        elif isinstance(item, dict) and isinstance(item.get("curve"), dict):
            out_curves.append(item["curve"])
            ixs = [c for c in item.get("indices") or [] if isinstance(c, dict)]
            out_indices.extend(ixs)
            notes.append(
                f"curves[{i}] is a build_curve result; using its curve and {len(ixs)} index"
            )
        elif isinstance(item, dict):
            out_curves.append(item)
        else:
            raise LocalValidationError(
                f"curves[{i}]: expected a TermStructure object, a build_curve result or "
                f"{{'session': name}}, got {type(item).__name__}",
                [{"path": f"/curves/{i}", "message": "not an object"}],
            )
    for i, item in enumerate(indices or []):
        if is_ref(item):
            name = item["session"]
            got = store.get(name)
            if got.kind == "index":
                out_indices.append(got.value)
                notes.append(f"indices[{i}] <- session {name!r} (index {got.value.get('id')})")
            elif got.kind == "market":
                ixs = [c for c in got.value.get("indices") or [] if isinstance(c, dict)]
                out_indices.extend(ixs)
                notes.append(f"indices[{i}] <- session {name!r} (market: {len(ixs)} indices)")
            else:
                raise LocalValidationError(
                    f"indices[{i}]: session {name!r} is a curve, not an index or market",
                    [{"path": f"/indices/{i}", "message": "wrong session kind"}],
                )
        elif isinstance(item, dict):
            out_indices.append(item)
        else:
            raise LocalValidationError(
                f"indices[{i}]: expected an IndexDef object or {{'session': name}}",
                [{"path": f"/indices/{i}", "message": "not an object"}],
            )
    return out_curves, out_indices, notes
