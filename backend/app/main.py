import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from . import checkpoints, config, db, llm, mcp_client, memory, settings, subagents, uploads, workspace
from .agent import RUNS, Run, RunRequest, active_run
from .browser import MANAGER
from .tools import REGISTRY, ToolError


settings.apply()


@asynccontextmanager
async def lifespan(_app):
    # MCP conecta em background: npx/uvx podem demorar e a API não deve esperar (o painel mostra "connecting").
    task = asyncio.create_task(mcp_client.start())
    yield
    task.cancel()
    await mcp_client.stop()
    await MANAGER.shutdown()


app = FastAPI(title="Forja", lifespan=lifespan)


@app.get("/api/config")
def get_config():
    return {"providers": [{"id": p["id"], "name": p["name"]} for p in config.PROVIDERS.values()],
            "num_ctx": config.NUM_CTX, "max_iterations": config.MAX_ITERATIONS,
            "default_workspace": workspace.label(None), "drives": [d["name"] for d in workspace.roots()],
            "picker_url": config.PICKER_URL,
            "subagents": {k: v for k, v in subagents.configured().items()}}


# ------------------------------------------------------------------ pastas de trabalho

@app.get("/api/fs/roots")
def fs_roots():
    """Discos montados + pastas usadas recentemente (para o seletor de pasta)."""
    with db.session() as s:
        rows = s.execute(select(db.Conversation.workspace, db.Conversation.updated_at)
                         .where(db.Conversation.workspace.is_not(None))
                         .order_by(db.Conversation.updated_at.desc())).all()
    recent: list[str] = []
    for ws, _ in rows:
        if ws not in recent:
            recent.append(ws)
    return {"drives": workspace.roots(), "recent": recent[:8], "default": workspace.label(None)}


@app.get("/api/fs/list")
def fs_list(path: str):
    try:
        return workspace.list_dirs(path)
    except workspace.WorkspaceError as e:
        raise HTTPException(400, str(e))


def _conv_root(conv_id: int | str | None):
    """Pasta (no container) da conversa; 0/None = pasta padrão."""
    if not conv_id or str(conv_id) == "0":
        return workspace.default_root()
    with db.session() as s:
        c = s.get(db.Conversation, int(conv_id))
        folder = c.workspace if c else None
    try:
        return workspace.resolve(folder)
    except workspace.WorkspaceError as e:
        raise HTTPException(400, str(e))


@app.get("/api/tools")
def get_tools():
    return [{"name": t.name, "description": t.description, "mutating": t.mutating, "always_ask": t.always_ask,
             "source": t.source, "enabled": t.name not in config.DISABLED_TOOLS} for t in REGISTRY.values()]


# ------------------------------------------------------------------ configurações

@app.get("/api/settings")
def get_settings():
    return settings.public()


@app.put("/api/settings")
def put_settings(patch: dict):
    try:
        return settings.update(patch)
    except settings.SettingsError as e:
        raise HTTPException(400, str(e))


@app.post("/api/settings/reset")
def reset_settings(body: dict | None = None):
    return settings.reset((body or {}).get("keys"))


@app.get("/api/mcp/config")
def get_mcp_config():
    return {"path": str(config.MCP_CONFIG), "text": settings.read_mcp_config()}


@app.put("/api/mcp/config")
async def put_mcp_config(body: dict):
    if any(not r.finished for r in RUNS.values()):
        raise HTTPException(409, "Espere a execução atual terminar")
    try:
        settings.write_mcp_config(body.get("text", ""))
    except settings.SettingsError as e:
        raise HTTPException(400, str(e))
    await mcp_client.start()
    return mcp_client.status()


@app.get("/api/memory")
async def get_memory():
    return await memory.read()


@app.get("/api/memory/project")
def get_project_memory():
    return memory.project_read()


@app.put("/api/memory/project")
def put_project_memory(body: dict):
    from .tools import ToolError
    try:
        return memory.project_write(body.get("content", ""))
    except ToolError as e:
        raise HTTPException(400, str(e))


@app.post("/api/memory/delete")
async def delete_memory(body: dict):
    try:
        return await memory.delete(body.get("names") or [])
    except memory.MemoryError as e:
        raise HTTPException(400, str(e))


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
async def get_models(provider: str, all: bool = False):
    """Modelos do provedor. Sem `all`, só os marcados em Configurações › Provedores (se houver seleção)."""
    try:
        models = await llm.list_models(provider)
    except llm.LLMError as e:
        raise HTTPException(502, str(e))
    chosen = config.ENABLED_MODELS.get(provider)
    if all or not chosen:
        return {"models": models, "filtered": False, "total": len(models)}
    return {"models": [m for m in models if m in chosen], "filtered": True, "total": len(models)}


@app.get("/api/catalog")
async def catalog():
    """Provedores com os modelos habilitados de cada um (para o seletor do campo de mensagem)."""
    import asyncio

    async def one(p):
        try:
            data = await get_models(p["id"])
            return {"id": p["id"], "name": p["name"], "type": p["type"], "models": data["models"], "error": ""}
        except HTTPException as e:
            return {"id": p["id"], "name": p["name"], "type": p["type"], "models": [], "error": e.detail}

    return await asyncio.gather(*(one(p) for p in config.PROVIDERS.values()))


class ModelSettingBody(BaseModel):
    model: str
    tool_mode: str | None = None  # native | text | auto
    vision: str | None = None     # auto | yes | no


@app.get("/api/model-settings")
def get_model_settings(model: str):
    return {"model": model, **db.get_model_setting(model)}


@app.put("/api/model-settings")
def put_model_settings(body: ModelSettingBody):
    if body.tool_mode is not None and body.tool_mode not in ("native", "text", "auto"):
        raise HTTPException(400, "tool_mode deve ser native, text ou auto")
    if body.vision is not None and body.vision not in ("auto", "yes", "no"):
        raise HTTPException(400, "vision deve ser auto, yes ou no")
    current = db.get_model_setting(body.model)
    with db.session() as s:
        s.merge(db.ModelSetting(model=body.model, tool_mode=body.tool_mode or current["tool_mode"],
                                vision=body.vision or current["vision"]))
        s.commit()
    return {"model": body.model, **db.get_model_setting(body.model)}


# ------------------------------------------------------------------ navegador integrado
# Uma sessão por conversa: `conv` é o id da conversa ("0" = rascunho da tela inicial).

class NavigateBody(BaseModel):
    url: str = ""
    action: str = ""  # back | forward | reload (vazio = goto url)


class InputBody(BaseModel):
    type: str  # click | dblclick | move | wheel | key | text
    x: float = 0
    y: float = 0
    button: str = "left"
    delta_x: float = 0
    delta_y: float = 0
    key: str = ""
    text: str = ""


class ViewportBody(BaseModel):
    width: int
    height: int


class TabsBody(BaseModel):
    action: str  # new | switch | close
    index: int | None = None
    url: str = ""


def _sess(conv: str):
    return MANAGER.session(conv)


@app.get("/api/browser")
async def get_browser(conv: str = "0"):
    return await _sess(conv).state_with_title()


@app.get("/api/browser/stream")
def browser_stream(conv: str = "0"):
    """SSE do espelho: evento `state` + frames do screencast enquanto houver assinante."""
    async def stream():
        async for ev in _sess(conv).frames():
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/browser/navigate")
async def browser_navigate(body: NavigateBody, conv: str = "0"):
    try:
        await _sess(conv).navigate(body.url, body.action)
    except ToolError as e:
        raise HTTPException(400, str(e))
    return await _sess(conv).state_with_title()


@app.post("/api/browser/input")
async def browser_input(body: InputBody, conv: str = "0"):
    """Mouse/teclado do usuário no espelho. Sem aprovação: é o usuário agindo, não o modelo."""
    try:
        await _sess(conv).input(body.model_dump())
    except ToolError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/browser/viewport")
async def browser_viewport(body: ViewportBody, conv: str = "0"):
    """O painel da UI redimensionou: as abas do Chromium passam a ter esse tamanho."""
    try:
        await _sess(conv).set_viewport(body.width, body.height)
    except ToolError as e:
        raise HTTPException(400, str(e))
    return _sess(conv).state()


@app.post("/api/browser/tabs")
async def browser_tabs(body: TabsBody, conv: str = "0"):
    s = _sess(conv)
    try:
        if body.action == "new":
            await s.new_tab(body.url)
        elif body.action == "switch":
            await s.switch_tab(body.index)
        elif body.action == "close":
            await s.close_tab(body.index)
        else:
            raise HTTPException(400, "action deve ser new, switch ou close")
    except ToolError as e:
        raise HTTPException(400, str(e))
    return await s.state_with_title()


@app.post("/api/browser/upload")
async def browser_upload(conv: str = "0", file: UploadFile | None = File(None)):
    """Responde ao seletor de arquivo aberto pela página: sem arquivo = cancelar."""
    s = _sess(conv)
    try:
        if file is None:
            await s.upload([])
        else:
            root = _conv_root(conv)
            att = uploads.save(file.filename or "arquivo", await file.read(), file.content_type, root)
            await s.upload([str(root / att["path"])])
    except (ToolError, ValueError, OSError) as e:
        raise HTTPException(400, str(e))
    return await s.state_with_title()


@app.post("/api/browser/close")
async def browser_close(conv: str = "0"):
    await MANAGER.close(conv)
    return _sess(conv).state()


# ------------------------------------------------------------------ conversas

def _conv_dict(c: db.Conversation) -> dict:
    return {"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat(),
            "workspace": c.workspace, "workspace_label": workspace.label(c.workspace)}


@app.get("/api/conversations")
def list_conversations():
    with db.session() as s:
        rows = s.scalars(select(db.Conversation).order_by(db.Conversation.updated_at.desc())).all()
        return [_conv_dict(c) for c in rows]


@app.post("/api/conversations")
def create_conversation(body: dict | None = None):
    folder = (body or {}).get("workspace") or None
    if folder:
        try:
            workspace.resolve(folder)
            folder = workspace.normalize(folder)
        except workspace.WorkspaceError as e:
            raise HTTPException(400, str(e))
    with db.session() as s:
        c = db.Conversation(workspace=folder)
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


@app.put("/api/conversations/{conv_id}/workspace")
def set_workspace(conv_id: int, body: dict):
    """Troca a pasta de trabalho da conversa (vale a partir da próxima mensagem)."""
    folder = body.get("workspace") or None
    if active_run(conv_id):
        raise HTTPException(409, "Espere a execução atual terminar")
    if folder:
        try:
            workspace.resolve(folder)
            folder = workspace.normalize(folder)
        except workspace.WorkspaceError as e:
            raise HTTPException(400, str(e))
    with db.session() as s:
        c = _get_conv(s, conv_id)
        c.workspace = folder
        s.commit()
        return _conv_dict(c)


@app.get("/api/conversations/{conv_id}/checkpoints")
def list_checkpoints(conv_id: int):
    return {str(k): v for k, v in checkpoints.summary(conv_id).items()}


@app.post("/api/conversations/{conv_id}/checkpoints/restore")
def restore_checkpoints(conv_id: int, body: dict):
    """Desfaz as alterações de arquivo do turno indicado e dos seguintes."""
    if active_run(conv_id):
        raise HTTPException(409, "Espere a execução atual terminar")
    return {"restored": checkpoints.restore_from(conv_id, int(body["turn_id"]))}


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: int):
    with db.session() as s:
        s.delete(_get_conv(s, conv_id))
        s.commit()
    await MANAGER.close(str(conv_id))  # a sessão do navegador morre com a conversa
    return {"ok": True}


# ------------------------------------------------------------------ execução

class RunBody(BaseModel):
    content: str | None = None  # None = continua de onde parou (regenerar ou mensagem editada)
    provider: str
    model: str
    mode: str = "agent"
    write_policy: str = "ask"
    attachments: list | None = None


@app.get("/api/files")
def get_file(path: str, conv: str = "0"):
    """Serve um arquivo da pasta de trabalho (miniatura de anexo). Confinado como as ferramentas."""
    from .tools import ToolError, resolve_path
    try:
        p = resolve_path(_conv_root(conv), path)
    except ToolError as e:
        raise HTTPException(400, str(e))
    if not p.is_file():
        raise HTTPException(404, "Arquivo não encontrado")
    return FileResponse(p)


@app.post("/api/uploads")
async def upload(file: UploadFile = File(...), conv: str = "0"):
    """Salva o anexo dentro da pasta de trabalho da conversa para o agente conseguir abrir."""
    try:
        return uploads.save(file.filename or "arquivo", await file.read(), file.content_type, _conv_root(conv))
    except (ValueError, OSError) as e:
        raise HTTPException(400, str(e))


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


@app.post("/api/conversations/{conv_id}/rewind")
def rewind(conv_id: int, body: dict):
    """Apaga da mensagem indicada em diante (editar) ou tudo depois dela (regenerar)."""
    message_id = int(body.get("message_id"))
    keep = bool(body.get("keep"))  # True = mantém a própria mensagem (regenerar)
    if active_run(conv_id):
        raise HTTPException(409, "Espere a execução atual terminar")
    # Arquivos: desfazer as alterações dos turnos apagados, ou esquecer os checkpoints deles.
    restored = checkpoints.restore_from(conv_id, message_id) if body.get("restore_files") else []
    if not body.get("restore_files"):
        checkpoints.forget_from(conv_id, message_id)
    with db.session() as s:
        c = _get_conv(s, conv_id)
        removed = [m for m in c.messages if (m.id > message_id if keep else m.id >= message_id)]
        for m in removed:
            s.delete(m)
        s.commit()
        c = _get_conv(s, conv_id)
        return {"messages": [m.to_dict() for m in c.messages], "removed": len(removed), "restored": restored}


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
