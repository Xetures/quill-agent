"""起服务 / 停服务这一层 —— 命令行与桌面壳都走它。

值得单独测的是三件「只有真跑起来才成立」的事：

    - **非本机监听必须给令牌**：这条判断在两个入口共用，漏一处就是一个能被局域网
      任意调用的后端；
    - **服务真的能应答**：桌面壳靠 `wait_ready` 决定什么时候开窗口。判早了用户先
      看到一片白，判晚了他觉得启动慢 —— 两个都是一次就能感觉到的体验问题；
    - **停得干净**：窗口关掉要带走 uvicorn（`stop` 走 lifespans 收尾，MCP 用 stdio
      起的子进程在那里收），端口也要放开。
"""

from __future__ import annotations

import socket
import time
from pathlib import Path

import pytest

from quill_agent import config, server_runner


@pytest.fixture
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """数据根指到临时目录。

    起真服务会跑 lifespan —— 那里面有播种（写出厂提示词、模式）和迁移。不隔离的话，
    跑一次测试就往开发机的数据根里写一遍。
    """
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path))
    config.get_settings.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()


def test_non_loopback_requires_a_token(
    monkeypatch: pytest.MonkeyPatch, isolated_home: Path
) -> None:
    """监听 0.0.0.0 却不给令牌，必须当场拒绝 —— 那等于把后端敞给整个局域网。"""
    monkeypatch.delenv("QUILL_ACCESS_TOKEN", raising=False)

    with pytest.raises(SystemExit, match="必须配置访问令牌"):
        server_runner.start_server("0.0.0.0", 8000)


def test_pick_port_skips_a_busy_port() -> None:
    """默认端口被占时顺延 —— 双击启动的用户看不到报错，顺延是他唯一的出路。"""
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        taken = busy.getsockname()[1]

        assert server_runner.pick_port("127.0.0.1", taken) > taken


def test_server_becomes_healthy_then_stops_cleanly(isolated_home: Path) -> None:
    """起 → 应答 → 停，整条链路。

    桌面壳「关窗带走服务」建立的正是这条链路上：`wait_ready` 通过（才开窗口）、
    `stop` 走完 lifespan（端口放开、MCP 子进程收掉）。中间任何一环不对，用户看到的
    要么是空白窗口，要么是关不掉的进程。
    """
    port = server_runner.pick_port("127.0.0.1", 18300)
    running = server_runner.start_server("127.0.0.1", port)

    try:
        assert running.wait_ready(timeout=30), "服务没能在 30 秒内就绪"
        assert running.healthy()
        assert running.url.endswith(f":{port}")
    finally:
        running.stop()

    # 停完端口要放开：否则用户第二次启动会「端口被占用」顺延，久而久之端口越飘越远
    assert not running.healthy(timeout=0.5)


def test_wait_ready_gives_up_when_the_server_dies(
    monkeypatch: pytest.MonkeyPatch, isolated_home: Path
) -> None:
    """服务自己退了就**立刻**放弃，而不是等满超时。

    这条守的是桌面壳的体验：服务起不来时（端口被抢、app 导入报错），窗口不该让用户
    对着一个空白页等半分钟 —— 等满超时只把「打不开」变成「半分钟之后才说打不开」。
    """

    def die(_self: object) -> None:
        """模拟 uvicorn 线程起来就退：`Server.run` 直接返回。"""
        return

    monkeypatch.setattr("uvicorn.Server.run", die)

    port = server_runner.pick_port("127.0.0.1", 18400)
    running = server_runner.start_server("127.0.0.1", port)

    started = time.monotonic()
    assert running.wait_ready(timeout=10) is False
    assert time.monotonic() - started < 3, "服务已经死了，却还在那儿等超时"
