"""Environment-driven settings.

| Env | Default | Meaning |
|---|---|---|
| ``QUANTRA_ENGINE_URL`` | ``http://localhost:8080`` | Engine JSON gateway. |
| ``QUANTRA_TIMEOUT_S`` | ``60`` | Per-request timeout in seconds. |
| ``QUANTRA_MCP_LOG`` | ``info`` | stderr log level (stdout is the MCP channel). |
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_ENGINE_URL = "http://localhost:8080"
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_LOG_LEVEL = "info"

_LOG_LEVELS = ("debug", "info", "warning", "error", "critical")


class SettingsError(ValueError):
    """An environment variable holds a value the server cannot use."""


@dataclass(frozen=True, slots=True)
class Settings:
    engine_url: str = DEFAULT_ENGINE_URL
    timeout_s: float = DEFAULT_TIMEOUT_S
    log_level: str = DEFAULT_LOG_LEVEL

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

        return cls(engine_url=url, timeout_s=timeout, log_level=level)
