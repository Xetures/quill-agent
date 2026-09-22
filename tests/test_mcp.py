"""MCP 接入：连接、列工具、调用、注册表集成、工具组展开。

用一个自己写的假服务器（`tests/fake_mcp.py`）起真实进程 —— MCP 的价值全在「跨进程协议」
上，把它 mock 掉就什么都没验证到。
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from quill_agent import agent, mcp
from quill_agent.models import McpServer
from quill_agent.tools.base import ToolRegistry

FAKE = str(Path(__file__).parent / "fake_mcp.py")


@pytest.fixture
def server() -> McpServer:
    return McpServer(
        id="srv-test",
        name="fake",
        command=sys.executable,
        args=[FAKE],
        timeout=20.0,
    )


@pytest.fixture
def isolated() -> Iterator[ToolRegistry]:
    """一个干净的注册表。

    **不能碰全局那个 `registry`**：它装着全部内置工具，测试往里加东西会串到别的用例。
    `mcp.register_tools` 接的是一个参数而不是直接用全局 —— 就是为了这个。
    """
    yield ToolRegistry()


@pytest.fixture
def manager(server: McpServer) -> Iterator[mcp.McpManager]:
    """一个独立的连接管理器（不用全局那个，同样是为了不串）。"""
    manager = mcp.McpManager()
    manager.start([server])
    yield manager
    manager.stop()


# ---------------------------------------------------------------------------
# 连接与调用
# ---------------------------------------------------------------------------


def test_connect_and_list_tools(manager: mcp.McpManager) -> None:
    connection = manager.get("srv-test")
    assert connection is not None
    assert connection.connected, connection.error

    names = [tool.local_name for tool in connection.tools]
    assert names == ["mcp__fake__echo", "mcp__fake__boom"]


def test_call_tool_returns_text(manager: mcp.McpManager) -> None:
    connection = manager.get("srv-test")
    assert connection is not None

    assert "echo: 你好" in connection.call("echo", {"text": "你好"})


def test_failing_tool_returns_text_not_raise(manager: mcp.McpManager) -> None:
    """工具自己报错是「一次失败的调用」，不是「连接坏了」—— 必须转成文本给模型看。"""
    connection = manager.get("srv-test")
    assert connection is not None

    text = connection.call("boom", {})

    assert "错误" in text
    assert connection.connected  # 连接依然可用


def test_unknown_tool_returns_text(manager: mcp.McpManager) -> None:
    connection = manager.get("srv-test")
    assert connection is not None

    assert connection.call("不存在", {})  # 有返回、不抛


def test_call_after_stop_says_unavailable(server: McpServer) -> None:
    """断开之后给一句能看懂的话，而不是抛异常 —— 模型会拿它当工具结果读。"""
    connection = mcp.McpConnection(server)
    connection.start()
    connection.stop()

    text = connection.call("echo", {"text": "x"})

    assert "不可用" in text


def test_bad_command_fails_only_itself() -> None:
    """连不上只是这一个不可用：要记下原因，且不能让 start 抛出去。"""
    broken = McpServer(id="broken", name="broken", command="/不存在的命令", timeout=5.0)

    manager = mcp.McpManager()
    manager.start([broken])
    try:
        connection = manager.get("broken")
        assert connection is not None
        assert not connection.connected
        assert connection.error  # 有原因，界面才有话可说
    finally:
        manager.stop()


# ---------------------------------------------------------------------------
# 注册表集成
# ---------------------------------------------------------------------------


def test_register_and_unregister(
    manager: mcp.McpManager, isolated: ToolRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp, "manager", manager)

    added = mcp.register_tools(isolated)
    assert added == ["mcp__fake__echo", "mcp__fake__boom"]
    # 能像内置工具一样经过注册表执行（含参数解析）
    assert "echo: 通过注册表" in isolated.execute("mcp__fake__echo", '{"text": "通过注册表"}')

    manager.stop()
    left = mcp.register_tools(isolated)

    assert left == []
    assert isolated.all() == []


def test_unregister_source_keeps_builtin(isolated: ToolRegistry) -> None:
    """摘掉外部来源时不能误伤内置工具（它们不挂在任何 source 之下）。"""
    from quill_agent.tools.base import ToolSpec

    @isolated.tool(description="一个内置工具")
    def inner() -> str:
        return "ok"

    isolated.register_external(
        [(ToolSpec(name="external", description="x"), lambda **_: "ok")],
        source="srv-a",
    )

    isolated.unregister_source("srv-a")

    assert [spec.name for spec in isolated.all()] == ["inner"]
    assert isolated.execute("inner", {}) == "ok"


# ---------------------------------------------------------------------------
# 工具组展开
# ---------------------------------------------------------------------------


def test_expand_mcp_ref(manager: mcp.McpManager, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp, "manager", manager)

    expanded = agent._expand_mcp_refs(["read_file", "mcp:srv-test", "write_file"])

    assert expanded == [
        "read_file",
        "mcp__fake__echo",
        "mcp__fake__boom",
        "write_file",
    ]


def test_tool_group_accepts_mcp_ref(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """工具组里写 `mcp:<id>` 要能存下去。

    回归测试：这处的校验原先只查「注册表里有没有这个工具名」，于是 `mcp:<id>` 被当成
    拼错的工具名拒掉，用户在界面上看到「未知的工具名：mcp:xxxx」，而那个引用其实是对的。
    **只有端到端点一次「创建」才会撞上** —— 单测当时没覆盖路由层。
    """

    from server.routes import tools as routes

    from quill_agent.store import McpServerStore

    store = McpServerStore(tmp_path / "mcp.json")
    created = store.add(name="fake")
    monkeypatch.setattr(routes.stores, "mcp_servers", lambda: store)

    routes._check_tool_names([f"mcp:{created.id}", "read_file"])  # 不抛就算过


def test_tool_group_rejects_unknown_mcp_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """引用一个**不存在**的服务器仍然要拦 —— 那还是「引用了一个不存在的东西」。"""
    from fastapi import HTTPException
    from server.routes import tools as routes

    from quill_agent.store import McpServerStore

    empty = McpServerStore(Path("/tmp/不存在的路径/mcp.json"))
    monkeypatch.setattr(routes.stores, "mcp_servers", lambda: empty)

    with pytest.raises(HTTPException) as excinfo:
        routes._check_tool_names(["mcp:没有这个"])

    assert "MCP 服务器" in str(excinfo.value.detail)


def test_confirm_rejects_server_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    """「需要确认」不能引用整个服务器：展开后名字变了，那条规则永远命中不了。"""
    from fastapi import HTTPException
    from server.routes import tools as routes

    with pytest.raises(HTTPException) as excinfo:
        routes._check_confirm(["mcp:any"], ["mcp:any"])

    assert "逐个指定" in str(excinfo.value.detail)


def test_expand_ignores_unknown_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """引用了一个不存在的服务器：展开成空，而不是报错或留下一个假名字。"""
    monkeypatch.setattr(mcp, "manager", mcp.McpManager())

    assert agent._expand_mcp_refs(["read_file", "mcp:没有这个"]) == ["read_file"]
