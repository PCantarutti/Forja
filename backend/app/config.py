"""Configuração.

Os valores vêm do .env e podem ser sobrescritos em tempo de execução pela tela de
Configurações (settings.py aplica os valores salvos no banco nestes atributos). Por isso todo
módulo deve ler `config.X` na hora de usar, nunca copiar o valor no import.
"""
import os
from pathlib import Path

WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", "/workspace"))  # pasta padrão (conversa sem pasta escolhida)
WORKSPACE_HOST = os.getenv("WORKSPACE_HOST", "")  # o mesmo caminho visto no Windows, só para exibir
PICKER_URL = os.getenv("FORJA_PICKER_URL", "http://127.0.0.1:3001")  # forja-picker (diálogo nativo), visto pelo navegador
# forja-runner: comandos e servidores no sistema do usuário, visto pelo backend (container -> host)
RUNNER_URL = os.getenv("FORJA_RUNNER_URL", "http://host.docker.internal:3002")
RUNNER_TOKEN = os.getenv("FORJA_RUNNER_TOKEN", "")
RUNNER_TOKEN_FILE = os.getenv("FORJA_RUNNER_TOKEN_FILE", "/config/runner-token")  # gerado pelo runner
HOST_MOUNTS = os.getenv("HOST_MOUNTS", "")          # discos do Windows no container: "C=/host/c,D=/host/d"
DB_PATH = os.getenv("DB_PATH", "/data/forja.db")
MCP_CONFIG = Path(os.getenv("MCP_CONFIG", "/config/mcp.json"))

NUM_CTX = int(os.getenv("NUM_CTX", "32768"))
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "25"))
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", "1000000"))
# Documento de escritório entra pelo caminho do `documentos.py`, que extrai texto em vez de
# mandar o arquivo inteiro ao modelo: o teto pode ser bem mais folgado que o do texto puro.
MAX_DOC_BYTES = int(os.getenv("MAX_DOC_BYTES", "25000000"))
SHELL_TIMEOUT_MAX = int(os.getenv("SHELL_TIMEOUT_MAX", "300"))
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080")
COMPACT_AT = float(os.getenv("COMPACT_AT", "0.8"))  # fração da janela que dispara a compactação
# Afrouxa (>1) ou aperta (<1) o teto de raciocínio por esforço do llm.py, sem mexer em código.
REASONING_CAP_MULT = float(os.getenv("REASONING_CAP_MULT", "1.0"))

# Navegador integrado
BROWSER_IDLE_MINUTES = int(os.getenv("BROWSER_IDLE_MINUTES", "30"))  # 0 = nunca fechar sessão ociosa
BROWSER_SCALE = int(os.getenv("BROWSER_SCALE", "2"))                  # render 1x..3x (vale ao (re)lançar o Chromium)
BROWSER_STREAM = os.getenv("BROWSER_STREAM", "jpeg")                  # jpeg (leve, padrão) | png (sem perda, 3-5x mais pesado)

# type: ollama (API nativa, aceita num_ctx) | lmstudio (OpenAI + janela do modelo carregado) | openai
PROVIDERS = {
    "ollama": {"id": "ollama", "name": "Ollama", "type": "ollama",
               "url": os.getenv("OLLAMA_URL", "http://host.docker.internal:11434/v1"), "api_key": ""},
    "lmstudio": {"id": "lmstudio", "name": "LM Studio", "type": "lmstudio",
                 "url": os.getenv("LMSTUDIO_URL", "http://host.docker.internal:1234/v1"), "api_key": ""},
}

DISABLED_TOOLS: set[str] = set()   # ferramentas desligadas na tela de Configurações
CUSTOM_INSTRUCTIONS = ""           # texto extra no fim do system prompt
AUTO_APPROVE_TOOLS: list[str] = []     # globs de nomes de ferramenta que dispensam aprovação
AUTO_APPROVE_COMMANDS: list[str] = []  # globs de comandos do run_command que dispensam aprovação
TRUSTED_HOOKS: list[str] = []          # pastas onde .forja/hooks.json tem permissão de rodar
PROJECT_MEMORY = True                  # ler/oferecer o arquivo de memória do projeto
PROJECT_MEMORY_FILE = "FORJA.md"
ENABLED_MODELS: dict[str, list[str]] = {}  # provedor -> modelos visíveis nos chats (ausente = todos)
SUBAGENTS: dict[str, dict] = {}            # "rapido"/"capaz" -> {"provider", "model"}
SUBAGENT_MAX_ITERATIONS = 15
