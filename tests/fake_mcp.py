"""测试用的最小 MCP 服务器（stdio）。

自己写一个而不是连真实的第三方服务器：测试不该依赖网络、npm 或别人的服务是否在线。
它提供一个正常工具、一个报错的工具，覆盖「调用成功」和「调用失败」两条路。
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

server = MCPServer("fake")


@server.tool()
def echo(text: str) -> str:
    """把文字原样回显。"""
    return f"echo: {text}"


@server.tool()
def boom() -> str:
    """总是失败，用来验证错误处理。"""
    raise RuntimeError("故意失败")


if __name__ == "__main__":
    server.run()
