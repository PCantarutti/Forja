import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from . import config, db, llm, mcp_client
from .agent import RUNS, Run, RunRequest, active_run
from .tools import REGISTRY


@asynccontextmanager
async def lifespan(_app):
    # MCP conecta em background: npx/uvx podem demorar e a API não deve esperar (o painel mostra "connecting").
    task = asyncio.create_task(mcp_client.start())
    yield
    task.cancel()
    await mcp_client.stop()


app = FastAPI(title="Forja", lifespan=lifespan)


@app.get("/api/config")
def get_config():
    return {"providers": list(config.PROVIDERS), "provider_urls": config.PROVIDERS,
            "num_ctx": config.NUM_CTX, "max_iterations": config.MAX_ITERATIONS, "workspace": "/workspace"}


@app.get("/api/tools")
def get_tools():
    return [{"name": t.name, "description": t.description, "mutating": t.mutating, "always_ask": t.always_ask,
             "source": t.source} for t in REGISTRY.values()]


@app.get("/api/mcp")
def get_mcp():
    return mcp_client.status()


@app.post("/api/mcp/reload")
async def reload_mcp():
    if any(not r.finished for r in RUNS.values()):
        raise HTTPException(409, "Espere a execução atual terminar antes de recarregar o MCP")
    await mcp_client.start()
    return mcp_client.status()


@app.get("/api/models")
async def get_models(provider: str):
    try:
        return {"models": await llm.list_models(provider)}
    except llm.LLMError as e:
        raise HTTPException(502, str(e))


class ToolModeBody(BaseModel):
    model: str
    tool_mode: str


@app.get("/api/model-settings")
def get_model_settings(model: str):
    return {"model": model, "tool_mode": db.get_tool_mode(model)}


@app.put("/api/model-settings")
def put_model_settings(body: ToolModeBody):
    if body.tool_mode not in ("native", "text", "auto"):
        raise HTTPException(400, "tool_mode deve ser native, text ou auto")
    with db.session() as s:
        s.merge(db.ModelSetting(model=body.model, tool_mode=body.tool_mode))
        s.commit()
    return body


# ------------------------------------------------------------------ conversas

def _conv_dict(c: db.Conversation) -> dict:
    return {"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat()}


@app.get("/api/conversations")
def list_conversations():
    with db.session() as s:
        rows = s.scalars(select(db.Conversation).order_by(db.Conversation.updated_at.desc())).all()
        return [_conv_dict(c) for c in rows]


@app.post("/api/conversations")
def create_conversation():
    with db.session() as s:
        c = db.Conversation()
        s.add(c)
        s.commit()
        return _conv_dict(c)


def _get_conv(s, conv_id: int) -> db.Conversation:
    c = s.get(db.Conversation, conv_id)
    if not c:
        raise HTTPException(404, "Conversa não encontrada")
    return c


@app.get("/api/conversations/{conv_id}")
def get_conversation(conv_id: int):
    with db.session() as s:
        c = _get_conv(s, conv_id)
        return {**_conv_dict(c), "messages": [m.to_dict() for m in c.messages]}


@app.delete("/api/conversations/{conv_id}")
def delete_conversation(conv_id: int):
    with db.session() as s:
        s.delete(_get_conv(s, conv_id))
        s.commit()
    return {"ok": True}


# ------------------------------------------------------------------ execução

class RunBody(BaseModel):
    content: str
    provider: str
    model: str
    mode: str = "agent"
    write_policy: str = "ask"


def _sse(run: Run, cursor: int) -> StreamingResponse:
    # Desconectar só encerra esta assinatura; a execução continua em background.
    async def stream():
        async for ev in run.subscribe(cursor):
            yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/conversations/{conv_id}/run")
async def start_run(conv_id: int, body: RunBody):
    with db.session() as s:
        _get_conv(s, conv_id)
    if body.mode not in ("chat", "agent") or body.write_policy not in ("ask", "auto"):
        raise HTTPException(400, "mode deve ser chat|agent e write_policy ask|auto")
    if active_run(conv_id):
        raise HTTPException(409, "Esta conversa já tem uma execução em andamento")
    run = Run(conv_id)
    RUNS[run.id] = run
    run.start(RunRequest(**body.model_dump()))
    return _sse(run, 0)


@app.get("/api/conversations/{conv_id}/live")
async def live(conv_id: int):  # async: roda no event loop, atômico em relação ao publish()
    """Mensagens salvas + estado da execução ativa (rascunho, aprovações pendentes, cursor)."""
    with db.session() as s:
        c = _get_conv(s, conv_id)
        messages = [m.to_dict() for m in c.messages]
    run = active_run(conv_id)  # sem await entre as duas leituras: snapshot consistente
    return {"messages": messages, "run": run.snapshot() if run else None}


@app.get("/api/runs/{run_id}/stream")
def stream_run(run_id: str, cursor: int = 0):
    return _sse(_get_run(run_id), cursor)


class ApproveBody(BaseModel):
    call_id: str
    approved: bool


def _get_run(run_id: str) -> Run:
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "Execução não está mais ativa")
    return run


@app.post("/api/runs/{run_id}/approve")
def approve(run_id: str, body: ApproveBody):
    if not _get_run(run_id).resolve(body.call_id, body.approved):
        raise HTTPException(409, "Nenhuma aprovação pendente para esta chamada")
    return {"ok": True}


@app.post("/api/runs/{run_id}/stop")
def stop(run_id: str):
    _get_run(run_id).stop()
    return {"ok": True}
