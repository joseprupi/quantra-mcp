"""Error types. Engine errors are carried verbatim; nothing is rephrased."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class EngineError(Exception):
    """A non-2xx answer from the engine.

    ``status`` is the HTTP status, ``error`` the engine's ``error`` field
    verbatim (or the raw body when the engine did not send JSON), ``body`` the
    decoded response body when there was one.
    """

    status: int
    error: str
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"engine returned HTTP {self.status}: {self.error}"


@dataclass(slots=True)
class TransportError(Exception):
    """The engine could not be reached or did not answer in time."""

    error: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.error


@dataclass(slots=True)
class LocalValidationError(Exception):
    """The request was rejected locally, before any engine call.

    ``problems`` is a list of ``{"path": "/json/pointer", "message": "..."}``.
    """

    error: str
    problems: list[dict[str, str]] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.error


def extract_error_text(status: int, body: Any, raw_text: str) -> str:
    """The engine's error text, verbatim, from a non-2xx body."""
    if isinstance(body, dict):
        for key in ("error", "message", "error_message"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
    text = raw_text.strip()
    return text if text else f"HTTP {status} with empty body"
