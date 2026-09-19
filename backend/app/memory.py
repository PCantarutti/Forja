"""Leitura da memória da IA para a tela de Configurações.

Hoje a memória vem de um servidor MCP de grafo de conhecimento (o `server-memory` oficial e
compatíveis): a ferramenta `read_graph` devolve entidades e relações. Como é procurado por
sufixo do nome da ferramenta, qualquer servidor que exponha `read_graph`/`delete_entities`
aparece aqui. Se nenhum estiver ligado, a aba mostra o aviso em vez de quebrar.
"""
from __future__ import annotations

import json

from .tools import REGISTRY, ToolError, execute


class MemoryError(Exception):
    pass


def _find(suffix: str) -> str | None:
    return next((t.name for t in REGISTRY.values()
                 if t.source.startswith("mcp:") and t.name.endswith(f"__{suffix}")), None)


def _parse(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"entities": [], "relations": [], "raw": raw[:5000]}
    if isinstance(data, list):  # alguns servidores devolvem só a lista de entidades
        return {"entities": data, "relations": []}
    return {"entities": data.get("entities", []), "relations": data.get("relations", [])}


async def read() -> dict:
    tool = _find("read_graph")
    if not tool:
        return {"available": False, "reason": "Nenhum servidor MCP com memória (read_graph) está ligado.",
                "entities": [], "relations": []}
    try:
        raw = await execute(tool, {})
    except ToolError as e:
        return {"available": False, "reason": str(e), "entities": [], "relations": []}
    return {"available": True, "server": tool.split("__")[1], "can_delete": bool(_find("delete_entities")),
            **_parse(raw)}


async def delete(names: list[str]) -> dict:
    if not names:
        raise MemoryError("Nenhuma entidade selecionada.")
    tool = _find("delete_entities")
    if not tool:
        raise MemoryError("O servidor de memória ligado não permite apagar entidades.")
    try:
        await execute(tool, {"entityNames": names})
    except ToolError as e:
        raise MemoryError(str(e)) from None
    return await read()
