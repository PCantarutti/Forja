import os
from pathlib import Path

WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", "/workspace"))
DB_PATH = os.getenv("DB_PATH", "/data/forja.db")
NUM_CTX = int(os.getenv("NUM_CTX", "32768"))
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "25"))
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", "1000000"))

PROVIDERS = {
    "ollama": os.getenv("OLLAMA_URL", "http://host.docker.internal:11434/v1"),
    "lmstudio": os.getenv("LMSTUDIO_URL", "http://host.docker.internal:1234/v1"),
}

SHELL_TIMEOUT_MAX = int(os.getenv("SHELL_TIMEOUT_MAX", "300"))
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080")
MCP_CONFIG = Path(os.getenv("MCP_CONFIG", "/config/mcp.json"))
COMPACT_AT = float(os.getenv("COMPACT_AT", "0.8"))  # fração da janela que dispara a compactação
