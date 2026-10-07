"""Environment-driven settings.

| Env | Default | Meaning |
|---|---|---|
| ``QUANTRA_ENGINE_URL`` | ``http://localhost:8080`` | Engine JSON gateway. |
| ``QUANTRA_TIMEOUT_S`` | ``60`` | Per-request timeout in seconds. |
| ``QUANTRA_MCP_LOG`` | ``info`` | stderr log level (stdout is the MCP channel). |
| ``QUANTRA_SESSION_MAX_ITEMS`` | ``64`` | Cap on in-memory session scratch items (LRU). |
| ``QUANTRA_SESSION_MAX_TOTAL_BYTES`` | ``33554432`` | Cap on serialized session bytes (LRU). |
| ``QUANTRA_MAX_CONCURRENCY`` | ``4`` | Max simultaneous engine calls (fan-outs; HTTP tool calls). |
| ``QUANTRA_MAX_QUEUE`` | ``8`` | HTTP: tool calls allowed to wait for a slot before 503. |
| ``QUANTRA_RATE_LIMIT_RPM`` | ``60`` | HTTP: per-client requests per minute (``0`` disables). |
| ``QUANTRA_RATE_LIMIT_BURST`` | ``20`` | HTTP: token-bucket burst size. |
| ``QUANTRA_MAX_BODY_BYTES`` | ``2097152`` | HTTP: request body cap (413 above). |
| ``QUANTRA_TRUST_PROXY`` | ``0`` | HTTP: honour ``X-Forwarded-For`` (only behind your proxy). |
| ``QUANTRA_ALLOWED_HOSTS`` | *(empty)* | HTTP: ``Host`` allow-list (DNS-rebinding protection). |
| ``QUANTRA_ALLOWED_ORIGINS`` | *(empty = any)* | HTTP: ``Origin`` allow-list. |
| ``QUANTRA_LOG_RAW_IP`` | ``0`` | HTTP: log client IPs verbatim instead of a sha256 prefix. |
| ``QUANTRA_REQUIRE_ENGINE`` | ``0`` | Refuse to start when the engine's ``/meta`` is unreachable. |
| ``QUANTRA_PUBLIC_URL`` | *(empty)* | HTTP mode: the MCP URL advertised by ``GET /``. |
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

DEFAULT_ENGINE_URL = "http://localhost:8080"
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_LOG_LEVEL = "info"
DEFAULT_SESSION_MAX_ITEMS = 64
DEFAULT_SESSION_MAX_TOTAL_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_CONCURRENCY = 4
DEFAULT_MAX_QUEUE = 8
DEFAULT_RATE_LIMIT_RPM = 60
DEFAULT_RATE_LIMIT_BURST = 20
DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024

_LOG_LEVELS = ("debug", "info", "warning", "error", "critical")
_TRUE = ("1", "true", "yes", "on")
_FALSE = ("", "0", "false", "no", "off")


class SettingsError(ValueError):
    """An environment variable holds a value the server cannot use."""


def _int(source: Mapping[str, str], key: str, default: int, minimum: int) -> int:
    raw = source.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{key} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise SettingsError(f"{key} must be >= {minimum}, got {raw!r}")
    return value


def _bool(source: Mapping[str, str], key: str) -> bool:
    raw = source.get(key, "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise SettingsError(f"{key} must be 0/1 (or true/false), got {raw!r}")


def _list(source: Mapping[str, str], key: str) -> tuple[str, ...]:
    raw = source.get(key, "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    engine_url: str = DEFAULT_ENGINE_URL
    timeout_s: float = DEFAULT_TIMEOUT_S
    log_level: str = DEFAULT_LOG_LEVEL
    session_max_items: int = DEFAULT_SESSION_MAX_ITEMS
    session_max_total_bytes: int = DEFAULT_SESSION_MAX_TOTAL_BYTES
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    max_queue: int = DEFAULT_MAX_QUEUE
    rate_limit_rpm: int = DEFAULT_RATE_LIMIT_RPM
    rate_limit_burst: int = DEFAULT_RATE_LIMIT_BURST
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    trust_proxy: bool = False
    allowed_hosts: tuple[str, ...] = field(default_factory=tuple)
    allowed_origins: tuple[str, ...] = field(default_factory=tuple)
    log_raw_ip: bool = False
    require_engine: bool = False
    public_url: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        url = source.get("QUANTRA_ENGINE_URL", DEFAULT_ENGINE_URL).strip() or DEFAULT_ENGINE_URL
        if not url.startswith(("http://", "https://")):
            raise SettingsError(
                f"QUANTRA_ENGINE_URL must start with http:// or https://, got {url!r}"
            )
        url = url.rstrip("/")

        raw_timeout = source.get("QUANTRA_TIMEOUT_S", "").strip()
        timeout = DEFAULT_TIMEOUT_S
        if raw_timeout:
            try:
                timeout = float(raw_timeout)
            except ValueError as exc:
                raise SettingsError(
                    f"QUANTRA_TIMEOUT_S must be a number, got {raw_timeout!r}"
                ) from exc
            if timeout <= 0:
                raise SettingsError(f"QUANTRA_TIMEOUT_S must be positive, got {raw_timeout!r}")

        level = (
            source.get("QUANTRA_MCP_LOG", DEFAULT_LOG_LEVEL).strip().lower() or DEFAULT_LOG_LEVEL
        )
        if level not in _LOG_LEVELS:
            raise SettingsError(f"QUANTRA_MCP_LOG must be one of {_LOG_LEVELS}, got {level!r}")

        public_url = source.get("QUANTRA_PUBLIC_URL", "").strip().rstrip("/")
        if public_url and not public_url.startswith(("http://", "https://")):
            raise SettingsError(
                f"QUANTRA_PUBLIC_URL must start with http:// or https://, got {public_url!r}"
            )

        return cls(
            engine_url=url,
            timeout_s=timeout,
            log_level=level,
            session_max_items=_int(
                source, "QUANTRA_SESSION_MAX_ITEMS", DEFAULT_SESSION_MAX_ITEMS, 1
            ),
            session_max_total_bytes=_int(
                source, "QUANTRA_SESSION_MAX_TOTAL_BYTES", DEFAULT_SESSION_MAX_TOTAL_BYTES, 1024
            ),
            max_concurrency=_int(source, "QUANTRA_MAX_CONCURRENCY", DEFAULT_MAX_CONCURRENCY, 1),
            max_queue=_int(source, "QUANTRA_MAX_QUEUE", DEFAULT_MAX_QUEUE, 0),
            rate_limit_rpm=_int(source, "QUANTRA_RATE_LIMIT_RPM", DEFAULT_RATE_LIMIT_RPM, 0),
            rate_limit_burst=_int(source, "QUANTRA_RATE_LIMIT_BURST", DEFAULT_RATE_LIMIT_BURST, 1),
            max_body_bytes=_int(source, "QUANTRA_MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES, 1024),
            trust_proxy=_bool(source, "QUANTRA_TRUST_PROXY"),
            allowed_hosts=_list(source, "QUANTRA_ALLOWED_HOSTS"),
            allowed_origins=_list(source, "QUANTRA_ALLOWED_ORIGINS"),
            log_raw_ip=_bool(source, "QUANTRA_LOG_RAW_IP"),
            require_engine=_bool(source, "QUANTRA_REQUIRE_ENGINE"),
            public_url=public_url,
        )
