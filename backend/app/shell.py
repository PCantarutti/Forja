"""Ferramenta run_command: executa comando no container, com cwd na pasta de trabalho.

Roda DENTRO do container do backend (não no Windows), com cwd na pasta de trabalho da conversa.
Como no Claude Desktop, a proteção é a aprovação (always_ask), não um sandbox: o bash enxerga os
discos montados em /host. Tem python, git, node e uv.
"""
from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

from . import config, workspace
from .tools import Tool, ToolError, register, resolve_path

MAX_OUTPUT = 20_000


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    half = MAX_OUTPUT // 2
    return f"{text[:half]}\n\n... ({len(text) - MAX_OUTPUT} caracteres omitidos) ...\n\n{text[-half:]}"


def run_command(root: Path, args: dict) -> str:
    command = args["command"].strip()
    if not command:
        raise ToolError("command vazio.")
    cwd = resolve_path(root, args.get("cwd"))
    timeout = max(1, min(int(args.get("timeout") or 60), config.SHELL_TIMEOUT_MAX))
    # Sessão própria para matar o grupo inteiro (bash + filhos) no timeout.
    p = subprocess.Popen(["bash", "-lc", command], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, text=True, errors="replace", start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        out, _ = p.communicate()
        raise ToolError(f"Timeout: o comando passou de {timeout}s e foi encerrado.\nSaída parcial:\n{_truncate(out)}")
    body = f"exit code: {p.returncode}\n{_truncate(out) or '(sem saída)'}"
    if p.returncode != 0:
        raise ToolError(body)
    return body


def command_preview(root: Path, args: dict) -> dict:
    cwd = resolve_path(root, args.get("cwd"))
    return {"kind": "command", "path": workspace.to_host(cwd) or str(cwd), "text": args["command"]}


register(Tool(
    "run_command",
    "Executa um comando bash no container (Linux) com cwd na pasta de trabalho. Use para rodar testes, "
    "scripts, git, pip/npm. stdout e stderr vêm juntos; exit code diferente de 0 vira erro. "
    "Comandos interativos não funcionam (stdin fechado).",
    {"type": "object", "properties": {
        "command": {"type": "string", "description": "Comando bash"},
        "cwd": {"type": "string", "description": "Subpasta da pasta de trabalho. Padrão: '.'"},
        "timeout": {"type": "integer", "description": f"Segundos (padrão 60, máx {config.SHELL_TIMEOUT_MAX})"}},
     "required": ["command"]},
    run_command, mutating=True, preview=command_preview, always_ask=True))
