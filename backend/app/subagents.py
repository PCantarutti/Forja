"""Subagentes: o agente principal delega uma subtarefa para outro modelo.

Dois slots nas Configurações: `rapido` (modelo menor/rápido) e `capaz` (maior/mais lento). O agente
principal chama `delegate_task(task, level)` e escolhe o nível pela dificuldade. O subagente roda
um loop próprio, com as mesmas ferramentas (menos delegate_task), as mesmas aprovações e a mesma
pasta de trabalho, e devolve só o relatório final. Os passos aparecem na UI dentro do bloco da
delegação, mas não entram no histórico do agente principal (só o relatório entra).
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import AsyncIterator, Callable

from . import config, db, llm
from .parsing import parse_text_tool_calls, split_think
from .tools import Tool, active, register, vision_caps

LEVELS = {"rapido": "Rápido", "capaz": "Capaz"}
SUB_PROMPT = """
Você é um SUBAGENTE do Forja: outro agente te passou a tarefa abaixo. O usuário não vê suas mensagens
intermediárias, só o seu relatório final. Faça apenas a tarefa pedida, usando as ferramentas. Ao terminar,
responda com um relatório curto e objetivo: o que fez, arquivos alterados, resultados e o que ficou pendente.
"""
MAX_RESULT_IN_STEP = 2000


def slot(level: str) -> dict | None:
    spec = config.SUBAGENTS.get(level) or {}
    return spec if spec.get("provider") and spec.get("model") else None


def configured() -> dict[str, dict]:
    return {lvl: spec for lvl in LEVELS if (spec := slot(lvl))}


def _unused(_root: Path, _args: dict) -> str:  # a execução real é o run() abaixo, chamado pelo agente
    raise RuntimeError("delegate_task é tratado pelo loop do agente")


register(Tool(
    "delegate_task",
    "Delega uma subtarefa autocontida a um subagente e devolve o relatório dele. level='rapido' para tarefas "
    "simples (modelo mais rápido), level='capaz' para tarefas difíceis (modelo mais forte e lento). "
    "Descreva tudo o que ele precisa saber: ele não vê esta conversa.",
    {"type": "object", "properties": {
        "task": {"type": "string", "description": "Tarefa completa, com contexto, arquivos e critério de pronto"},
        "level": {"type": "string", "enum": list(LEVELS), "description": "rapido ou capaz"}},
     "required": ["task", "level"]},
    _unused, available=lambda: bool(configured())))


def _est(messages: list[dict]) -> int:
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages) // 4


async def run(conv_id: int, call: dict, req, run_obj, out: dict,
              run_call: Callable) -> AsyncIterator[dict]:
    from .agent import system_prompt  # import tardio: agent importa este módulo

    args = call["arguments"]
    pid = call["id"]
    level = str(args.get("level") or "rapido").lower()
    task = str(args.get("task") or "").strip()
    spec = slot(level) or (slot("capaz" if level == "rapido" else "rapido"))
    meta: dict = {"arguments": args}
    if not task:
        out.update(status="erro", text="Informe 'task' com a tarefa completa.", meta=meta)
        return
    if not spec:
        out.update(status="erro", text="Nenhum subagente configurado (Configurações › Subagentes).", meta=meta)
        return
    used_level = level if slot(level) else ("capaz" if level == "rapido" else "rapido")
    provider, model = spec["provider"], spec["model"]

    setting = db.get_model_setting(model)
    via = "prompt" if setting["tool_mode"] == "text" else "native"
    caps = vision_caps(await llm.capabilities(provider, model), setting["vision"])
    from .agent import available_tools
    tools = available_tools(caps, run_obj.permission, exclude={"delegate_task"})
    schemas = [t.openai_schema() for t in tools] if via == "native" else None
    messages: list[dict] = [
        {"role": "system", "content": system_prompt(via, caps, exclude={"delegate_task"},
                                                    permission=run_obj.permission, effort=getattr(req, "effort", "medio"))
                                    + SUB_PROMPT},
        {"role": "user", "content": task}]

    info = {"level": used_level, "provider": provider, "model": model, "steps": [], "tokens": 0,
            "iterations": 0}
    if used_level != level:
        info["fallback"] = f"Nível '{level}' sem modelo configurado; usei '{used_level}'."
    meta["sub"] = info
    t0 = time.monotonic()
    final = ""
    yield {"type": "sub_status", "parent": pid, "text": f"{LEVELS[used_level]} · {model}: começando…"}

    for i in range(config.SUBAGENT_MAX_ITERATIONS):
        if run_obj.cancel.is_set():
            final = final or "(interrompido pelo usuário)"
            break
        info["iterations"] = i + 1
        yield {"type": "sub_status", "parent": pid, "text": f"{LEVELS[used_level]} · {model}: pensando (passo {i + 1})"}
        content = ""
        done: dict = {"tool_calls": []}
        try:
            async for kind, val in llm.chat_stream(provider, model, messages, schemas, config.NUM_CTX,
                                                   getattr(req, "effort", "medio")):
                if run_obj.cancel.is_set():
                    break
                if kind == "content":
                    content += val
                elif kind == "done":
                    done = val
        except llm.LLMError as e:
            out.update(status="erro", text=f"Subagente falhou ({model}): {e}", meta=meta)
            return
        info["tokens"] += done.get("completion_tokens") or len(content) // 4

        _, visible = split_think(content)
        calls = done.get("tool_calls") or []
        if not calls and via == "prompt" or not calls and setting["tool_mode"] == "auto":
            parsed, visible = parse_text_tool_calls(content, [t.name for t in tools])
            calls = [{"id": "call_" + uuid.uuid4().hex[:12], **c} for c in parsed]
        if not calls:
            final = visible
            break

        if via == "native":
            messages.append({"role": "assistant", "content": visible, "tool_calls": [
                {"id": c["id"], "type": "function",
                 "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                for c in calls]})
        else:
            messages.append({"role": "assistant", "content": visible + "".join(
                "\n<tool_call>\n" + json.dumps({"name": c["name"], "arguments": c["arguments"]},
                                               ensure_ascii=False) + "\n</tool_call>" for c in calls)})

        for c in calls:
            sub_out: dict = {}
            async for ev in run_call(conv_id, c, req, run_obj, caps, sub_out, parent=pid):
                yield ev
            step = {"id": c["id"], "name": c["name"], "arguments": c["arguments"], "status": sub_out["status"],
                    "result": sub_out["text"][:MAX_RESULT_IN_STEP], "meta": {
                        k: v for k, v in sub_out["meta"].items() if k in ("preview", "auto_rule", "approved")}}
            info["steps"].append(step)
            # resultado do passo para a UI (não é gravado como mensagem da conversa)
            yield {"type": "tool_result", "parent": pid, "message": {
                "id": None, "role": "tool", "content": sub_out["text"], "thinking": "", "tool_calls": None,
                "tool_call_id": c["id"], "name": c["name"], "status": sub_out["status"], "meta": sub_out["meta"]}}
            if via == "native":
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": sub_out["text"]})
            else:
                messages.append({"role": "user", "content":
                                 f"<tool_response>\n[{c['name']}: {sub_out['status']}]\n{sub_out['text']}\n</tool_response>"})
    else:
        final = f"(o subagente parou no limite de {config.SUBAGENT_MAX_ITERATIONS} passos sem concluir)"

    info["seconds"] = round(time.monotonic() - t0, 1)
    yield {"type": "sub_status", "parent": pid, "text": ""}
    out.update(status="ok", meta=meta,
               text=f"[Relatório do subagente {LEVELS[used_level]} ({model})]\n{final or '(sem relatório)'}")
