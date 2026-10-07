import pytest

from quantra_mcp.config import Settings, SettingsError


def test_defaults() -> None:
    s = Settings.from_env({})
    assert s.engine_url == "http://localhost:8080"
    assert s.timeout_s == 60.0
    assert s.log_level == "info"
    assert s.session_max_items == 64
    assert s.max_concurrency == 4


def test_env_overrides_and_trailing_slash() -> None:
    s = Settings.from_env(
        {
            "QUANTRA_ENGINE_URL": "https://api.quantra.io/",
            "QUANTRA_TIMEOUT_S": "5.5",
            "QUANTRA_MCP_LOG": "DEBUG",
            "QUANTRA_SESSION_MAX_ITEMS": "8",
            "QUANTRA_MAX_CONCURRENCY": "2",
        }
    )
    assert s.max_concurrency == 2
    assert s.engine_url == "https://api.quantra.io"
    assert s.timeout_s == 5.5
    assert s.log_level == "debug"
    assert s.session_max_items == 8


@pytest.mark.parametrize(
    "env",
    [
        {"QUANTRA_ENGINE_URL": "localhost:8080"},
        {"QUANTRA_TIMEOUT_S": "abc"},
        {"QUANTRA_TIMEOUT_S": "0"},
        {"QUANTRA_MCP_LOG": "loud"},
        {"QUANTRA_SESSION_MAX_ITEMS": "0"},
        {"QUANTRA_SESSION_MAX_ITEMS": "many"},
        {"QUANTRA_MAX_CONCURRENCY": "0"},
        {"QUANTRA_MAX_CONCURRENCY": "lots"},
    ],
)
def test_bad_values_raise(env: dict[str, str]) -> None:
    with pytest.raises(SettingsError):
        Settings.from_env(env)


def test_hosted_defaults() -> None:
    s = Settings.from_env({})
    assert s.session_max_total_bytes == 32 * 1024 * 1024
    assert (s.max_queue, s.rate_limit_rpm, s.rate_limit_burst) == (8, 60, 20)
    assert s.max_body_bytes == 2 * 1024 * 1024
    assert s.trust_proxy is False and s.log_raw_ip is False and s.require_engine is False
    assert s.allowed_hosts == () and s.allowed_origins == () and s.public_url == ""


def test_hosted_overrides() -> None:
    s = Settings.from_env(
        {
            "QUANTRA_SESSION_MAX_TOTAL_BYTES": "4096",
            "QUANTRA_MAX_QUEUE": "0",
            "QUANTRA_RATE_LIMIT_RPM": "0",
            "QUANTRA_RATE_LIMIT_BURST": "5",
            "QUANTRA_MAX_BODY_BYTES": "1024",
            "QUANTRA_TRUST_PROXY": "1",
            "QUANTRA_ALLOWED_HOSTS": "mcp.quantra.io, localhost",
            "QUANTRA_ALLOWED_ORIGINS": "https://claude.ai",
            "QUANTRA_LOG_RAW_IP": "true",
            "QUANTRA_REQUIRE_ENGINE": "yes",
            "QUANTRA_PUBLIC_URL": "https://mcp.quantra.io/",
        }
    )
    assert s.session_max_total_bytes == 4096 and s.max_queue == 0 and s.rate_limit_rpm == 0
    assert s.rate_limit_burst == 5 and s.max_body_bytes == 1024
    assert s.trust_proxy and s.log_raw_ip and s.require_engine
    assert s.allowed_hosts == ("mcp.quantra.io", "localhost")
    assert s.allowed_origins == ("https://claude.ai",)
    assert s.public_url == "https://mcp.quantra.io"


@pytest.mark.parametrize(
    "env",
    [
        {"QUANTRA_SESSION_MAX_TOTAL_BYTES": "10"},
        {"QUANTRA_MAX_QUEUE": "-1"},
        {"QUANTRA_RATE_LIMIT_BURST": "0"},
        {"QUANTRA_MAX_BODY_BYTES": "1"},
        {"QUANTRA_TRUST_PROXY": "maybe"},
        {"QUANTRA_REQUIRE_ENGINE": "2"},
        {"QUANTRA_PUBLIC_URL": "mcp.quantra.io"},
    ],
)
def test_hosted_bad_values_raise(env: dict[str, str]) -> None:
    with pytest.raises(SettingsError):
        Settings.from_env(env)
