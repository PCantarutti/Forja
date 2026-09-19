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
