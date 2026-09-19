import json

import pytest

from app import agent, config, memory, settings
from app.tools import ToolError, active, get_tool


@pytest.fixture(autouse=True)
def clean():
    settings.reset()
    yield
    settings.reset()


# ------------------------------------------------ valores gerais

def test_update_applies_to_config_without_restart():
    settings.update({"num_ctx": 65536, "compact_at": 0.5})
    assert config.NUM_CTX == 65536 and config.COMPACT_AT == 0.5
    settings.reset(["num_ctx"])
    assert config.NUM_CTX == settings.ENV_DEFAULTS["num_ctx"]  # volta para o .env
    assert config.COMPACT_AT == 0.5  # reset parcial não mexe no resto


@pytest.mark.parametrize("patch", [{"num_ctx": 10}, {"compact_at": 2}, {"max_iterations": 0},
                                   {"searxng_url": "ftp://x"}, {"inexistente": 1}, {"disabled_tools": "tudo"}])
def test_invalid_values_are_rejected(patch):
    with pytest.raises(settings.SettingsError):
        settings.update(patch)


def test_custom_instructions_go_to_system_prompt():
    settings.update({"custom_instructions": "Responda sempre em pt-BR e use tabs."})
    assert "use tabs" in agent.system_prompt("native")
    assert "use tabs" in agent.system_prompt("none")  # também no modo Chat


# ------------------------------------------------ provedores e chaves

def test_provider_api_key_is_kept_and_never_returned():
    settings.update({"providers": [{"id": "openrouter", "name": "OpenRouter", "type": "openai",
                                    "url": "https://openrouter.ai/api/v1", "api_key": "sk-abcd1234"}]})
    assert config.PROVIDERS["openrouter"]["api_key"] == "sk-abcd1234"
    pub = settings.public()
    assert pub["providers"][0]["has_api_key"] and pub["providers"][0]["api_key_hint"] == "…1234"
    assert "api_key" not in pub["providers"][0]

    # patch sem api_key mantém a chave; "" apaga
    settings.update({"providers": [{"id": "openrouter", "name": "OR", "type": "openai",
                                    "url": "https://openrouter.ai/api/v1"}]})
    assert config.PROVIDERS["openrouter"]["api_key"] == "sk-abcd1234"
    settings.update({"providers": [{"id": "openrouter", "name": "OR", "type": "openai",
                                    "url": "https://openrouter.ai/api/v1", "api_key": ""}]})
    assert config.PROVIDERS["openrouter"]["api_key"] == ""


def test_api_key_becomes_authorization_header():
    from app import llm
    settings.update({"providers": [{"id": "x", "name": "X", "type": "openai", "url": "https://api.x.com/v1",
                                    "api_key": "segredo"}]})
    assert llm.headers("x") == {"Authorization": "Bearer segredo"}
    settings.reset()
    assert llm.headers("ollama") == {}


@pytest.mark.parametrize("bad", [[], [{"id": "A B", "type": "openai", "url": "https://x"}],
                                 [{"id": "x", "type": "magico", "url": "https://x"}],
                                 [{"id": "x", "type": "openai", "url": "sem-protocolo"}],
                                 [{"id": "x", "type": "openai", "url": "https://x"},
                                  {"id": "x", "type": "openai", "url": "https://y"}]])
def test_invalid_providers(bad):
    with pytest.raises(settings.SettingsError):
        settings.update({"providers": bad})


# ------------------------------------------------ ligar/desligar ferramentas

def test_disabled_tool_is_not_sent_and_cannot_run():
    settings.update({"disabled_tools": ["run_command", "web_search"]})
    names = [t.name for t in active()]
    assert "run_command" not in names and "read_file" in names
    assert "run_command" not in agent.system_prompt("native")
    with pytest.raises(ToolError, match="desativada"):
        get_tool("run_command")


# ------------------------------------------------ mcp.json

def test_mcp_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MCP_CONFIG", tmp_path / "mcp.json")
    assert json.loads(settings.read_mcp_config()) == {"mcpServers": {}}
    settings.write_mcp_config('{"mcpServers": {"x": {"command": "echo"}}}')
    assert json.loads(settings.read_mcp_config())["mcpServers"]["x"]["command"] == "echo"
    with pytest.raises(settings.SettingsError, match="JSON inválido"):
        settings.write_mcp_config("{nao é json}")
    with pytest.raises(settings.SettingsError):
        settings.write_mcp_config('{"mcpServers": []}')


# ------------------------------------------------ memória

def test_memory_parse_formats():
    assert memory._parse('{"entities": [{"name": "Pedro"}], "relations": []}')["entities"][0]["name"] == "Pedro"
    assert memory._parse('[{"name": "Pedro"}]')["entities"][0]["name"] == "Pedro"
    assert memory._parse("texto solto")["raw"] == "texto solto"


def test_memory_unavailable_without_mcp_server(anyio_backend=None):
    import asyncio
    out = asyncio.run(memory.read())
    assert out["available"] is False and "read_graph" in out["reason"]
