"""命令行入口：参数解析与端口顺延。

`serve` 是「双击启动」那条路用的命令，所以它的参数与端口行为值得钉住 ——
双击启动的用户看不到报错，出了问题只能靠这里的行为是可预期的。
"""

from __future__ import annotations

import re
import socket
from pathlib import Path

import pytest

from quill_agent import cli

ROOT = Path(__file__).resolve().parents[1]


def test_serve_defaults() -> None:
    args = cli.build_parser().parse_args(["serve"])

    assert args.command == "serve"
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.open_browser is True


def test_serve_accepts_host_and_port() -> None:
    args = cli.build_parser().parse_args(
        ["serve", "--host", "0.0.0.0", "--port", "9123", "--no-browser"]
    )

    assert args.host == "0.0.0.0"
    assert args.port == 9123
    assert args.open_browser is False


def test_both_browser_flags_are_accepted() -> None:
    """`--open-browser` 与 `--no-browser` 都要认。

    只认其中一个就会出事：启动脚本里写的是 `--open-browser`（读脚本的人一眼能看出
    它会开浏览器），而命令行用户需要的是 `--no-browser`（默认开着，得有个关的开关）。
    """
    assert cli.build_parser().parse_args(["serve", "--open-browser"]).open_browser is True
    assert cli.build_parser().parse_args(["serve", "--no-browser"]).open_browser is False


@pytest.mark.parametrize("name", ["start.bat", "start.sh", "start.command"])
def test_launcher_scripts_only_pass_supported_flags(name: str) -> None:
    """启动脚本里写的参数，parser 必须真的认识。

    这条是补上来的 —— 有一次脚本传 `--open-browser`、parser 只定义了 `--no-browser`，
    双击启动直接报「unrecognized arguments」。这类错误只在别人机器上、双击的那一刻
    才暴露，所以这里直接把脚本内容当输入跑一遍解析。
    """
    text = (ROOT / name).read_text(encoding="utf-8", errors="replace")
    match = re.search(r"quill serve([^\n\r]*)", text)
    assert match, f"{name} 里找不到 `quill serve` 调用"

    # 截到第一个 shell 操作符为止：start.command 里写的是 `... --open-browser || { ...`
    raw = re.split(r"[|&;<>]", match.group(1))[0]

    # 丢掉 shell 的转发符号（bat 的 %*、sh 的 "$@"），其余原样交给 parser
    arguments = [
        token.strip("\"'")
        for token in raw.split()
        if not token.strip("\"'").startswith(("%", "$"))
    ]

    assert cli.build_parser().parse_args(["serve", *arguments]).command == "serve"


def test_existing_flags_still_work() -> None:
    """加了子命令之后，原来的只读命令不能被挤掉。"""
    assert cli.build_parser().parse_args(["-t"]).tools is True
    assert cli.build_parser().parse_args(["-v"]).version is True
    assert cli.build_parser().parse_args([]).command is None


def test_pick_port_returns_the_requested_port_when_free() -> None:
    # 先占一个端口再放开：拿到的一定是个不冲突的号，比硬编码 8000 靠谱
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]

    assert cli.pick_port("127.0.0.1", free) == free


def test_pick_port_skips_a_busy_port() -> None:
    """默认端口被占时顺延，而不是直接报错退出。"""
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        taken = busy.getsockname()[1]

        assert cli.pick_port("127.0.0.1", taken) > taken
