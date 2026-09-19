"""Cliente MCP: conecta nos servidores de config/mcp.json e registra as ferramentas deles.

Formato (igual ao Claude Desktop):
{"mcpServers": {
    "nome": {"command": "npx", "args": ["-y", "pacote"], "env": {"X": "1"}},   # stdio
    "remoto": {"url": "http://host:porta/mcp", "headers": {"Authorization": "..."}},  # streamable HTTP
    "off": {"command": "...", "disabled": true}}}

Cada servidor vive numa task própria que abre e fecha o transporte (anyio exige que o
contexto seja aberto e fechado na mesma task). Ferramentas viram `mcp__<servidor>__<tool>`;
as que não declaram readOnlyHint são tratadas como `mutating` (pedem aprovação).
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from . import config
from .tools import Tool, ToolError, register, unregister_source

CALL_TIMEOUT = timedelta(seconds=120)


def tool_name(server: str, tool: str) -> str:
    """Nome aceito pela API OpenAI: [a-zA-Z0-9_-], máx 64."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", f"mcp__{server}__{tool}")[:64]


def result_text(result) -> str:
    parts = []
    for c in result.content:
        if c.type == "text":
            parts.append(c.text)
        elif c.type == "resource" and hasattr(c.resource, "text"):
            parts.append(c.resource.text)
        else:
            parts.append(f"[{c.type} omitido]")
    if getattr(result, "structuredContent", None) and not parts:
        parts.append(json.dumps(result.structuredContent, ensure_ascii=False))
    return "\n".join(parts) or "(sem conteúdo)"


class Server:
    def __init__(self, name: str, spec: dict):
        self.name, self.spec = name, spec
        self.status, self.error, self.tools = "connecting", "", []
        self.session: ClientSession | None = None
        self._stop = asyncio.Event()
        self._ready = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    def _transport(self):
        if self.spec.get("url"):
            return streamablehttp_client(self.spec["url"], headers=self.spec.get("headers"))
        if not self.spec.get("command"):
            raise ValueError("defina 'command' (stdio) ou 'url' (HTTP)")
        return stdio_client(StdioServerParameters(
            command=self.spec["command"], args=self.spec.get("args", []),
            env=self.spec.get("env"), cwd=str(config.WORKSPACE_ROOT)))

    async def _run(self) -> None:
        source = f"mcp:{self.name}"
        try:
            async with self._transport() as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await asyncio.wait_for(session.initialize(), 60)
                    listed = await session.list_tools()
                    self.session = session
                    for t in listed.tools:
                        read_only = bool(t.annotations and t.annotations.readOnlyHint)
                        register(Tool(
                            tool_name(self.name, t.name), f"[MCP {self.name}] {t.description or t.name}",
                            t.inputSchema or {"type": "object", "properties": {}},
                            self._handler(t.name), mutating=not read_only, source=source))
                        self.tools.append(t.name)
                    self.status = "connected"
                    self._ready.set()
                    await self._stop.wait()
        except Exception as e:  # servidor ruim não derruba o app
            self.status, self.error = "error", f"{e.__class__.__name__}: {e}"[:500]
        finally:
            self.session = None
            unregister_source(source)
            if self.status == "connected":
                self.status = "stopped"
            self._ready.set()

    def _handler(self, remote: str):
        async def call(_root: Path, args: dict) -> str:
            if not self.session:
                raise ToolError(f"Servidor MCP '{self.name}' desconectado.")
            try:
                res = await self.session.call_tool(remote, args, read_timeout_seconds=CALL_TIMEOUT)
            except Exception as e:
                raise ToolError(f"Falha no MCP '{self.name}': {e.__class__.__name__}: {e}") from e
            text = result_text(res)
            if res.isError:
                raise ToolError(text)
            return text
        return call

    async def wait_ready(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except asyncio.TimeoutError:
            pass

    async def stop(self) -> None:
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, 10)
        except (asyncio.TimeoutError, Exception):
            self._task.cancel()

    def info(self) -> dict:
        return {"name": self.name, "status": self.status, "error": self.error, "tools": self.tools,
                "transport": "http" if self.spec.get("url") else "stdio"}


SERVERS: dict[str, Server] = {}
config_error = ""


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in (data.get("mcpServers") or {}).items() if not v.get("disabled")}


async def start(timeout: float = 30) -> None:
    """(Re)carrega config/mcp.json e conecta em todos os servidores."""
    global config_error
    await stop()
    try:
        specs = load_config(config.MCP_CONFIG)
        config_error = ""
    except (json.JSONDecodeError, OSError, AttributeError) as e:
        config_error = f"{config.MCP_CONFIG}: {e}"
        return
    for name, spec in specs.items():
        SERVERS[name] = Server(name, spec)
    await asyncio.gather(*(s.wait_ready(timeout) for s in SERVERS.values()))


async def stop() -> None:
    await asyncio.gather(*(s.stop() for s in SERVERS.values()))
    SERVERS.clear()


def status() -> dict:
    return {"config": str(config.MCP_CONFIG), "config_error": config_error,
            "servers": [s.info() for s in SERVERS.values()]}
