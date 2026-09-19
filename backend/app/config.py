"""Configuração.

Os valores vêm do .env e podem ser sobrescritos em tempo de execução pela tela de
Configurações (settings.py aplica os valores salvos no banco nestes atributos). Por isso todo
módulo deve ler `config.X` na hora de usar, nunca copiar o valor no import.
"""
import os
from pathlib import Path

WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", "/workspace"))
DB_PATH = os.getenv("DB_PATH", "/data/forja.db")
MCP_CONFIG = Path(os.getenv("MCP_CONFIG", "/config/mcp.json"))

NUM_CTX = int(os.getenv("NUM_CTX", "32768"))
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "25"))
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", "1000000"))
SHELL_TIMEOUT_MAX = int(os.getenv("SHELL_TIMEOUT_MAX", "300"))
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080")
COMPACT_AT = float(os.getenv("COMPACT_AT", "0.8"))  # fração da janela que dispara a compactação

# type: ollama (API nativa, aceita num_ctx) | lmstudio (OpenAI + janela do modelo carregado) | openai
PROVIDERS = {
    "ollama": {"id": "ollama", "name": "Ollama", "type": "ollama",
               "url": os.getenv("OLLAMA_URL", "http://host.docker.internal:11434/v1"), "api_key": ""},
    "lmstudio": {"id": "lmstudio", "name": "LM Studio", "type": "lmstudio",
                 "url": os.getenv("LMSTUDIO_URL", "http://host.docker.internal:1234/v1"), "api_key": ""},
}

DISABLED_TOOLS: set[str] = set()   # ferramentas desligadas na tela de Configurações
CUSTOM_INSTRUCTIONS = ""           # texto extra no fim do system prompt
