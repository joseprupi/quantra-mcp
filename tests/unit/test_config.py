import pytest

from quantra_mcp.config import Settings, SettingsError


def test_defaults() -> None:
    s = Settings.from_env({})
    assert s.engine_url == "http://localhost:8080"
    assert s.timeout_s == 60.0
    assert s.log_level == "info"


def test_env_overrides_and_trailing_slash() -> None:
    s = Settings.from_env(
        {
            "QUANTRA_ENGINE_URL": "https://api.quantra.io/",
            "QUANTRA_TIMEOUT_S": "5.5",
            "QUANTRA_MCP_LOG": "DEBUG",
        }
    )
    assert s.engine_url == "https://api.quantra.io"
    assert s.timeout_s == 5.5
    assert s.log_level == "debug"


@pytest.mark.parametrize(
    "env",
    [
        {"QUANTRA_ENGINE_URL": "localhost:8080"},
        {"QUANTRA_TIMEOUT_S": "abc"},
        {"QUANTRA_TIMEOUT_S": "0"},
        {"QUANTRA_MCP_LOG": "loud"},
    ],
)
def test_bad_values_raise(env: dict[str, str]) -> None:
    with pytest.raises(SettingsError):
        Settings.from_env(env)
