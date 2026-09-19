"""Loop do agente.

Cada iteração: monta histórico → chama o modelo em streaming → coleta tool calls (nativas ou
texto) → sem calls: checa promessa sem ação (reinjeta até 2x) → com calls: aprovação (card na UI)
→ executa → devolve resultado → repete. Tudo vira evento SSE e é persistido no SQLite.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import AsyncIterator

from . import config, db, llm
from .parsing import LoopDetector, detect_promise, parse_text_tool_calls, split_think
from .tools import REGISTRY, ToolError, get_tool, preview_tool, run_tool

MAX_NUDGES = 2


@dataclass
class RunRequest:
    content: str
    provider: str
    model: str
    mode: str = "agent"          # chat | agent
    write_policy: str = "ask"    # ask | auto


class Run:
    def __init__(self):
        self.id = uuid.uuid4().hex
        self.cancel = asyncio.Event()
        self.pending: dict[str, asyncio.Future] = {}

    def resolve(self, call_id: str, approved: bool) -> bool:
        fut = self.pending.get(call_id)
        if fut and not fut.done():
            fut.set_result(approved)
            return True
        return False

    def stop(self) -> None:
        self.cancel.set()
        for fut in self.pending.values():
            if not fut.done():
                fut.set_result(False)


RUNS: dict[str, Run] = {}


# ------------------------------------------------------------------ prompts

TEXT_FORMAT = """
Este modelo não usa tool calling nativo. Para chamar uma ferramenta, escreva EXATAMENTE:
<tool_call>
{"name": "NOME_DA_FERRAMENTA", "arguments": {...}}
</tool_call>
Depois da chamada, pare e espere o resultado, que chega em <tool_response>.
Ferramentas (JSON Schema):
"""


def system_prompt(via: str) -> str:
    if via == "none":
        return ("Você é o Forja, um assistente de programação. Você está no modo Chat: NÃO tem ferramentas "
                "e não acessa arquivos. Se o usuário pedir para criar ou editar arquivos, peça para ele "
                "trocar para o modo Agente. Responda no idioma do usuário.")
    names = ", ".join(REGISTRY)
    prompt = f"""Você é o Forja, um agente de programação. Pasta de trabalho: /workspace (caminhos relativos a ela).
Ferramentas disponíveis: {names}.
Regras:
- Execute, não descreva. Para mexer em arquivos, CHAME a ferramenta na mesma resposta. Nunca diga "vou criar/editar" sem fazer a chamada.
- Leia o arquivo antes de editar. Use edit_file para mudanças pontuais (old_str exato e único, sem números de linha) e write_file para arquivos novos ou reescritas completas.
- Se uma ferramenta devolver erro, leia a mensagem e corrija a chamada.
- Ao terminar, responda com um resumo curto do que foi feito.
Responda no idioma do usuário."""
    if via == "prompt":
        prompt += "\n" + TEXT_FORMAT + json.dumps(
            [t.openai_schema()["function"] for t in REGISTRY.values()], ensure_ascii=False)
    return prompt


def nudge_text(via: str) -> str:
    fmt = " usando o formato <tool_call>{...}</tool_call>" if via == "prompt" else ""
    return ("[Sistema] Você anunciou uma ação mas não chamou nenhuma ferramenta. "
            f"Faça a chamada agora{fmt}, sem descrever. Se não precisar de ferramenta, dê só a resposta final.")


# ------------------------------------------------------------------ histórico

def build_history(msgs: list[db.Message], via: str) -> list[dict]:
    native = via == "native"
    out: list[dict] = [{"role": "system", "content": system_prompt(via)}]
    for m in msgs:
        if m.role == "user":
            out.append({"role": "user", "content": m.content})
        elif m.role == "event" and (m.meta or {}).get("to_model"):
            # Nudge vai como "user": o template do Qwen rejeita "system" fora da 1ª posição.
            out.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            calls = m.tool_calls or []
            if native and calls:
                out.append({"role": "assistant", "content": m.content or "", "tool_calls": [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                    for c in calls]})
            else:
                text = m.content + "".join(
                    "\n<tool_call>\n" + json.dumps({"name": c["name"], "arguments": c["arguments"]},
                                                   ensure_ascii=False) + "\n</tool_call>" for c in calls)
                if text.strip():
                    out.append({"role": "assistant", "content": text.strip()})
        elif m.role == "tool":
            if native:
                out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
            else:
                out.append({"role": "user",
                            "content": f"<tool_response>\n[{m.name}: {m.status}]\n{m.content}\n</tool_response>"})
    # Modo texto: junta mensagens "user" seguidas (vários tool_response) — alguns templates exigem alternância.
    merged: list[dict] = []
    for m in out:
        if merged and m["role"] == "user" == merged[-1]["role"]:
            merged[-1] = {"role": "user", "content": merged[-1]["content"] + "\n\n" + m["content"]}
        else:
            merged.append(m)
    return merged


def _save(conv_id: int, **fields) -> db.Message:
    with db.session() as s:
        m = db.Message(conversation_id=conv_id, **fields)
        s.add(m)
        conv = s.get(db.Conversation, conv_id)
        conv.updated_at = db._now()
        s.commit()
        return m


def _load(conv_id: int) -> list[db.Message]:
    with db.session() as s:
        return list(s.get(db.Conversation, conv_id).messages)


def _stats(messages, tools, content, reasoning, done, t0, t_first, ctx_max, model) -> dict:
    """Tokens reais do provider quando disponíveis; senão estimativa chars/4 (estimated=True)."""
    end = time.monotonic()
    est_prompt = sum(len(json.dumps(m, ensure_ascii=False)) for m in messages) // 4 + (
        len(json.dumps(tools)) // 4 if tools else 0)
    est_out = (len(content) + len(reasoning)) // 4
    out = done.get("completion_tokens") or est_out
    gen = end - (t_first or end)
    return {"model": model, "prompt_tokens": done.get("prompt_tokens") or est_prompt, "tokens": out,
            "estimated": not done.get("completion_tokens"), "seconds": round(end - t0, 2),
            "tps": round(out / gen, 2) if gen > 0.05 else None, "ctx_max": ctx_max}


def _save_partial(conv_id: int, content: str, reasoning: str) -> None:
    if content or reasoning:
        _save(conv_id, role="assistant", content=split_think(content)[1], thinking=reasoning, meta={"partial": True})


def _event(conv_id: int, kind: str, text: str, to_model: bool = False) -> dict:
    m = _save(conv_id, role="event", content=text, meta={"kind": kind, "to_model": to_model})
    return {"type": "event", "message": m.to_dict()}


# ------------------------------------------------------------------ loop

async def run_agent(conv_id: int, req: RunRequest, run: Run) -> AsyncIterator[dict]:
    yield {"type": "run_started", "run_id": run.id}

    with db.session() as s:
        conv = s.get(db.Conversation, conv_id)
        if conv.title == "Nova conversa":
            conv.title = req.content.strip().splitlines()[0][:60] or "Nova conversa"
        s.commit()
    user_msg = _save(conv_id, role="user", content=req.content)
    yield {"type": "message", "message": user_msg.to_dict()}

    agent = req.mode == "agent"
    tool_mode = db.get_tool_mode(req.model) if agent else "none"
    via = "none" if not agent else ("prompt" if tool_mode == "text" else "native")
    ctx_max = await llm.context_limit(req.provider, req.model, config.NUM_CTX)
    loop = LoopDetector()
    nudges = iterations = 0

    def tools_sent() -> dict:
        # Fonte da verdade do painel lateral: exatamente o que vai nesta requisição.
        return {"type": "tools_sent", "mode": req.mode, "provider": req.provider, "model": req.model,
                "tool_mode": tool_mode, "via": via, "num_ctx": ctx_max,
                "tools": [{"name": t.name, "mutating": t.mutating} for t in REGISTRY.values()] if agent else []}

    yield tools_sent()

    while not run.cancel.is_set():
        if iterations >= config.MAX_ITERATIONS:
            yield _event(conv_id, "warning", f"Limite de {config.MAX_ITERATIONS} iterações atingido. O agente parou.")
            break
        iterations += 1

        messages = build_history(_load(conv_id), via)
        tools = [t.openai_schema() for t in REGISTRY.values()] if via == "native" else None

        content = reasoning = ""
        done: dict = {"tool_calls": [], "prompt_tokens": None, "completion_tokens": None}
        yield {"type": "assistant_start"}
        t0 = time.monotonic()
        t_first = None
        try:
            async for kind, val in llm.chat_stream(req.provider, req.model, messages, tools, config.NUM_CTX):
                if run.cancel.is_set():
                    break
                if kind != "done" and t_first is None:
                    t_first = time.monotonic()
                if kind == "content":
                    content += val
                    yield {"type": "token", "text": val}
                elif kind == "reasoning":
                    reasoning += val
                    yield {"type": "thinking", "text": val}
                else:
                    done = val
        except llm.LLMError as e:
            body = str(e).lower()
            if tools and tool_mode == "auto" and e.status == 400 and "tool" in body and not content:
                via = "prompt"
                yield _event(conv_id, "warning",
                             "O modelo não aceitou tool calling nativo. Mudando para chamadas em texto (fallback).")
                yield tools_sent()
                iterations -= 1
                continue
            yield _event(conv_id, "error", str(e))
            break
        except (asyncio.CancelledError, GeneratorExit):  # cliente desconectou
            _save_partial(conv_id, content, reasoning)
            raise

        if run.cancel.is_set():
            _save_partial(conv_id, content, reasoning)
            break

        stats = _stats(messages, tools, content, reasoning, done, t0, t_first, ctx_max, req.model)
        yield {"type": "context", "used": stats["prompt_tokens"], "estimated": stats["estimated"], "max": ctx_max}

        think, visible = split_think(content)
        reasoning = (reasoning + "\n" + think).strip()
        calls = done["tool_calls"]
        if agent and not calls and tool_mode != "native":
            parsed, visible = parse_text_tool_calls(content, REGISTRY)
            calls = [{"id": "call_" + uuid.uuid4().hex[:12], **c} for c in parsed]

        msg = _save(conv_id, role="assistant", content=visible, thinking=reasoning,
                    tool_calls=calls or None, meta={"via": via, "stats": stats})
        yield {"type": "assistant_end", "message": msg.to_dict()}

        if not calls:
            if agent and detect_promise(visible):
                if nudges < MAX_NUDGES:
                    nudges += 1
                    yield _event(conv_id, "nudge", nudge_text(via), to_model=True)
                    continue
                yield _event(conv_id, "warning",
                             f"O modelo anunciou uma ação mas não chamou nenhuma ferramenta, mesmo após "
                             f"{MAX_NUDGES} lembretes. Tente reformular o pedido ou trocar o modo de tool calling "
                             "deste modelo no painel lateral.")
            break

        stop = False
        for call in calls:
            if not stop and loop.record(call["name"], call["arguments"]):
                stop = True
                yield _event(conv_id, "warning",
                             f"Loop detectado: {call['name']} pedida 3 vezes seguidas com os mesmos argumentos. "
                             "O agente foi interrompido.")
            if stop or run.cancel.is_set():
                # Toda tool_call precisa de resposta no histórico, senão a próxima requisição falha.
                m = _save(conv_id, role="tool", tool_call_id=call["id"], name=call["name"], status="cancelada",
                          content="Não executada: o loop foi interrompido.", meta={"arguments": call["arguments"]})
                yield {"type": "tool_result", "message": m.to_dict()}
                continue
            async for ev in _execute(conv_id, call, req, run):
                yield ev
        if stop:
            break

    if run.cancel.is_set():
        yield _event(conv_id, "info", "Geração interrompida pelo usuário.")
    yield {"type": "done"}


async def _execute(conv_id: int, call: dict, req: RunRequest, run: Run) -> AsyncIterator[dict]:
    name, args = call["name"], call["arguments"]
    meta: dict = {"arguments": args}
    yield {"type": "tool_call", "call": call}

    def result(status: str, text: str) -> dict:
        m = _save(conv_id, role="tool", tool_call_id=call["id"], name=name, status=status, content=text, meta=meta)
        return {"type": "tool_result", "message": m.to_dict()}

    if "__raw__" in args:
        yield result("erro", f"Argumentos não são JSON válido: {args['__raw__'][:200]}")
        return
    try:
        tool = get_tool(name)
        if tool.mutating:
            meta["preview"] = preview_tool(name, args)  # valida antes de pedir aprovação
    except ToolError as e:
        yield result("erro", str(e))
        return

    if tool.mutating and req.write_policy != "auto":
        fut = asyncio.get_running_loop().create_future()
        run.pending[call["id"]] = fut
        yield {"type": "approval_request", "call": call, "preview": meta["preview"]}
        approved = await fut
        run.pending.pop(call["id"], None)
        if run.cancel.is_set():
            yield result("cancelada", "Não executada: geração interrompida pelo usuário.")
            return
        if not approved:
            meta["approved"] = False
            yield result("rejeitada", "O usuário rejeitou esta alteração. Não tente de novo sem perguntar; "
                                      "pergunte o que ele prefere.")
            return
        meta["approved"] = True

    try:
        out = await asyncio.to_thread(run_tool, name, args)
        yield result("ok", out)
    except ToolError as e:
        yield result("erro", str(e))
    except Exception as e:  # nunca derrubar o loop
        yield result("erro", f"Erro inesperado: {e.__class__.__name__}: {e}")
