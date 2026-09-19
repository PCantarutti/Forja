import asyncio
import json
import sys
from pathlib import Path

import pytest

from app import mcp_client
from app.tools import REGISTRY, ToolError, execute

SERVER = Path(__file__).with_name("mcp_echo_server.py")


def test_tool_name_is_openai_safe():
    assert mcp_client.tool_name("my server", "do.it") == "mcp__my_server__do_it"
    assert len(mcp_client.tool_name("s" * 40, "t" * 40)) == 64


def test_stdio_server_registers_and_calls(tmp_path, monkeypatch):
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {
        "echo": {"command": sys.executable, "args": [str(SERVER)]},
        "off": {"command": "nada", "disabled": True},
        "quebrado": {"command": "comando-que-nao-existe-xyz"}}}))
    monkeypatch.setattr("app.config.MCP_CONFIG", cfg)
    monkeypatch.setattr("app.config.WORKSPACE_ROOT", tmp_path)

    async def scenario():
        await mcp_client.start(timeout=60)
        try:
            st = {s["name"]: s for s in mcp_client.status()["servers"]}
            assert "off" not in st
            assert st["echo"]["status"] == "connected", st["echo"]
            assert st["quebrado"]["status"] == "error"
            echo, boom = REGISTRY["mcp__echo__echo"], REGISTRY["mcp__echo__boom"]
            assert not echo.mutating and boom.mutating  # readOnlyHint respeitado
            assert await execute("mcp__echo__echo", {"text": "oi"}) == "eco: oi"
            with pytest.raises(ToolError, match="explodiu"):
                await execute("mcp__echo__boom", {})
        finally:
            await mcp_client.stop()
        assert "mcp__echo__echo" not in REGISTRY  # desregistra ao parar

    asyncio.run(scenario())
