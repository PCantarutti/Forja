"""Cliente de LLM local.

- LM Studio (e qualquer servidor OpenAI-compatível): POST {base}/chat/completions em streaming SSE.
- Ollama: POST {host}/api/chat (API nativa). A camada /v1 do Ollama ignora `options`, então
  `num_ctx` só é respeitado pela API nativa — era a causa do contexto de 4k truncando as ferramentas.

`chat_stream` normaliza os dois para eventos: ("content", str), ("reasoning", str),
("done", {"tool_calls": [...], "prompt_tokens": int|None}).
Mensagens de entrada/saída ficam sempre no formato OpenAI.
"""
from __future__ import annotations

import json
import uuid
from typing import AsyncIterator

import httpx

from . import config

TIMEOUT = httpx.Timeout(connect=10, read=600, write=60, pool=10)


class LLMError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def base_url(provider: str) -> str:
    if provider not in config.PROVIDERS:
        raise LLMError(f"Provider desconhecido: {provider}")
    return config.PROVIDERS[provider].rstrip("/")


def _conn_error(provider: str, e: Exception) -> LLMError:
    hint = {
        "ollama": "Ollama está rodando? Ele precisa escutar em 0.0.0.0 (OLLAMA_HOST=0.0.0.0) para o Docker alcançar.",
        "lmstudio": "LM Studio está com o servidor ligado e 'Serve on Local Network' ativo?",
    }.get(provider, "")
    return LLMError(f"Não foi possível conectar em {base_url(provider)}: {e.__class__.__name__}. {hint}")


async def list_models(provider: str) -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{base_url(provider)}/models")
    except httpx.HTTPError as e:
        raise _conn_error(provider, e) from e
    if r.status_code >= 400:
        raise LLMError(f"/models respondeu {r.status_code}: {r.text[:300]}", r.status_code)
    return sorted(m["id"] for m in r.json().get("data", []) if "embed" not in m["id"].lower())


async def context_limit(provider: str, model: str, num_ctx: int) -> int | None:
    """Tamanho real da janela de contexto. Ollama: o num_ctx que enviamos. LM Studio: o carregado."""
    if provider == "ollama":
        return num_ctx
    if provider == "lmstudio":
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{base_url(provider).removesuffix('/v1')}/api/v0/models/{model}")
            info = r.json()
            return info.get("loaded_context_length") or info.get("max_context_length")
        except (httpx.HTTPError, ValueError, AttributeError):
            return None
    return None


def _raise_for(provider: str, status: int, body: bytes) -> None:
    text = body.decode("utf-8", "replace")[:1000]
    raise LLMError(f"{provider} respondeu HTTP {status}: {text}", status)


async def chat_stream(provider: str, model: str, messages: list[dict], tools: list[dict] | None,
                      num_ctx: int) -> AsyncIterator[tuple[str, object]]:
    impl = _ollama_stream if provider == "ollama" else _openai_stream
    try:
        async for ev in impl(provider, model, messages, tools, num_ctx):
            yield ev
    except httpx.HTTPError as e:
        raise _conn_error(provider, e) from e


# ------------------------------------------------------------------ OpenAI-compatível

async def _openai_stream(provider, model, messages, tools, num_ctx):
    body: dict = {"model": model, "messages": messages, "stream": True,
                  "stream_options": {"include_usage": True}}
    if tools:
        body["tools"] = tools
    calls: dict[int, dict] = {}
    prompt_tokens = None
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        async with c.stream("POST", f"{base_url(provider)}/chat/completions", json=body) as r:
            if r.status_code >= 400:
                _raise_for(provider, r.status_code, await r.aread())
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("usage"):
                    prompt_tokens = chunk["usage"].get("prompt_tokens")
                if chunk.get("error"):
                    raise LLMError(str(chunk["error"]))
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta") or {}
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        yield "reasoning", reasoning
                    if delta.get("content"):
                        yield "content", delta["content"]
                    for tc in delta.get("tool_calls") or []:
                        acc = calls.setdefault(tc.get("index", len(calls)), {"id": None, "name": "", "arguments": ""})
                        acc["id"] = tc.get("id") or acc["id"]
                        fn = tc.get("function") or {}
                        acc["name"] += fn.get("name") or ""
                        acc["arguments"] += fn.get("arguments") or ""
    out = []
    for acc in calls.values():
        try:
            args = json.loads(acc["arguments"] or "{}", strict=False)
        except json.JSONDecodeError:
            args = {"__raw__": acc["arguments"]}
        out.append({"id": acc["id"] or _new_id(), "name": acc["name"], "arguments": args})
    yield "done", {"tool_calls": out, "prompt_tokens": prompt_tokens}


# ------------------------------------------------------------------ Ollama nativo

def _to_ollama(messages: list[dict]) -> list[dict]:
    names: dict[str, str] = {}
    out = []
    for m in messages:
        m = dict(m)
        if m.get("tool_calls"):
            tcs = []
            for tc in m["tool_calls"]:
                args = tc["function"]["arguments"]
                names[tc["id"]] = tc["function"]["name"]
                tcs.append({"function": {"name": tc["function"]["name"],
                                         "arguments": json.loads(args) if isinstance(args, str) else args}})
            m["tool_calls"] = tcs
        if m["role"] == "tool":
            m["tool_name"] = names.get(m.pop("tool_call_id", ""), "")
        out.append(m)
    return out


async def _ollama_stream(provider, model, messages, tools, num_ctx):
    host = base_url(provider).removesuffix("/v1")
    body: dict = {"model": model, "messages": _to_ollama(messages), "stream": True,
                  "options": {"num_ctx": num_ctx}}
    if tools:
        body["tools"] = tools
    calls, prompt_tokens = [], None
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        async with c.stream("POST", f"{host}/api/chat", json=body) as r:
            if r.status_code >= 400:
                _raise_for(provider, r.status_code, await r.aread())
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise LLMError(str(chunk["error"]))
                msg = chunk.get("message") or {}
                if msg.get("thinking"):
                    yield "reasoning", msg["thinking"]
                if msg.get("content"):
                    yield "content", msg["content"]
                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    args = fn.get("arguments") or {}
                    if isinstance(args, str):
                        args = json.loads(args or "{}", strict=False)
                    calls.append({"id": _new_id(), "name": fn.get("name", ""), "arguments": args})
                if chunk.get("done"):
                    prompt_tokens = chunk.get("prompt_eval_count")
    yield "done", {"tool_calls": calls, "prompt_tokens": prompt_tokens}


def _new_id() -> str:
    return "call_" + uuid.uuid4().hex[:12]
