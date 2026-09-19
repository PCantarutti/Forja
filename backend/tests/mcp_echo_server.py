"""Servidor MCP mínimo usado nos testes (stdio)."""
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("echo")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def echo(text: str) -> str:
    """Devolve o texto."""
    return f"eco: {text}"


@mcp.tool()
def boom() -> str:
    """Sempre falha."""
    raise ValueError("explodiu")


if __name__ == "__main__":
    mcp.run()
