"""Lista de tarefas do agente (como o TodoWrite do Claude Code).

O modelo chama `update_tasks` com a lista inteira a cada mudança; a UI mostra os passos e o que já
foi concluído em tempo real. O run define `SINK` (contextvar) para publicar o evento `tasks`; o
estado final é gravado como mensagem de evento no fim da execução (agent.run_agent).
"""
from __future__ import annotations

import contextvars
from pathlib import Path
from typing import Callable

from .tools import Tool, ToolError, register

STATUSES = ("pending", "doing", "done")
MAX_TASKS = 30
SINK: contextvars.ContextVar[Callable[[list[dict]], None] | None] = contextvars.ContextVar("forja_tasks_sink", default=None)


def normalize(raw) -> list[dict]:
    if not isinstance(raw, list):
        raise ToolError("tasks deve ser uma lista de {text, status}.")
    out = []
    for i, t in enumerate(raw[:MAX_TASKS]):
        if isinstance(t, str):
            t = {"text": t}
        if not isinstance(t, dict) or not str(t.get("text") or "").strip():
            raise ToolError(f"Tarefa {i + 1} sem texto.")
        status = str(t.get("status") or "pending").lower()
        status = {"todo": "pending", "in_progress": "doing", "in-progress": "doing", "completed": "done",
                  "concluida": "done", "concluída": "done", "fazendo": "doing", "pendente": "pending"}.get(status, status)
        if status not in STATUSES:
            raise ToolError(f"Status inválido '{t.get('status')}' na tarefa {i + 1}: use pending, doing ou done.")
        out.append({"text": str(t["text"]).strip()[:200], "status": status})
    return out


def update_tasks(_root: Path, args: dict) -> str:
    tasks = normalize(args.get("tasks"))
    sink = SINK.get()
    if sink:
        sink(tasks)
    done = sum(1 for t in tasks if t["status"] == "done")
    doing = [t["text"] for t in tasks if t["status"] == "doing"]
    return (f"Lista atualizada: {done}/{len(tasks)} concluídas"
            + (f"; em andamento: {'; '.join(doing)}" if doing else "") + ".")


register(Tool(
    "update_tasks",
    "Mantém a lista de tarefas visível ao usuário. Envie a lista COMPLETA a cada mudança de status. "
    "Use em trabalhos com 3 ou mais passos: crie no início, marque doing ao começar e done ao terminar cada um.",
    {"type": "object", "properties": {
        "tasks": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"},
            "status": {"type": "string", "description": "pending | doing | done"}}, "required": ["text"]}}},
     "required": ["tasks"]},
    update_tasks))
