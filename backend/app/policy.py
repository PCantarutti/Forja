"""Regras de auto-aprovação (Configurações › Permissões).

Uma ferramenta que normalmente pede aprovação passa direto se casar com um glob:
- `auto_approve_tools`: nome da ferramenta, ex. `mcp__memoria__*`, `write_file`
- `auto_approve_commands`: comando do run_command, ex. `pytest*`, `git status`, `ls *`

O glob é comparado com o comando inteiro já sem espaços nas pontas. A regra que casou fica
gravada no resultado da ferramenta e aparece na UI, para nunca haver aprovação invisível.
"""
from __future__ import annotations

from fnmatch import fnmatch

from . import config


def auto_rule(name: str, args: dict) -> str | None:
    for pattern in config.AUTO_APPROVE_TOOLS:
        if fnmatch(name, pattern):
            return f"ferramenta {pattern}"
    if name == "run_command":
        command = str(args.get("command") or "").strip()
        for pattern in config.AUTO_APPROVE_COMMANDS:
            if fnmatch(command, pattern):
                return f"comando {pattern}"
    return None


def suggest(name: str, args: dict) -> str:
    """Sugestão de regra para o botão 'sempre permitir' do card de aprovação."""
    if name != "run_command":
        return name
    first = str(args.get("command") or "").strip().split()
    if not first:
        return name
    base = first[0]
    # "git status" é mais útil como regra do que "git *"
    if base in ("git", "npm", "pnpm", "yarn", "docker", "uv", "pip", "python", "node") and len(first) > 1:
        return f"{base} {first[1]}*"
    return f"{base}*"
