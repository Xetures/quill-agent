"""桌面壳必须**自己**把数据根定下来，不能指望 cwd。

回归的是这个故障：从访达双击启动时 cwd 是 `/`，而 `config._default_home` 的三条优先级里
第 2 条「就地运行」要求 cwd 是个源码 / 发布目录，于是只剩下平台目录 —— 用户的数据其实在
项目目录里，壳却去 `~/Library/Application Support/Quill` 翻一个空目录。表现是打开 App
之后提示词库、API 设置、会话**全是空的**（数据没丢，只是读错了地方）。

顺带一提：内置的「就地运行」判据认的是 **cwd**，而双击启动给不出 cwd，所以壳得自己
从**解释器所在位置**往上找项目根 —— 打包产物就放在项目的 `release/` 里。
"""

import os

from quill_agent import desktop
from quill_agent.config import HOME_ENV


def test_home_is_pinned_by_walking_up_from_the_interpreter(monkeypatch, tmp_path) -> None:
    """包在项目里跑（`release/Quill.app`）→ 数据根应当是那个项目目录。"""
    # 伪造一个「包在项目里」的布局：<tmp>/pyproject.toml + <tmp>/release/X.app/.../python
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    fake_python = tmp_path / "release" / "X.app" / "Contents" / "Resources" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("", encoding="utf-8")

    monkeypatch.delenv(HOME_ENV, raising=False)
    monkeypatch.setattr(desktop.sys, "executable", str(fake_python))

    desktop._pin_home()

    assert os.environ[HOME_ENV] == str(tmp_path)


def test_an_explicit_home_is_never_touched(monkeypatch, tmp_path) -> None:
    """环境里已经给了 `QUILL_HOME` 就一个字都别改 —— 显式指定永远优先。"""
    monkeypatch.setenv(HOME_ENV, "/somewhere/explicit")

    desktop._pin_home()

    assert os.environ[HOME_ENV] == "/somewhere/explicit"


def test_nothing_happens_when_no_project_is_found(monkeypatch, tmp_path) -> None:
    """往上找不到项目根（真装到 /Applications 之后）就交给 config 自己判断。"""
    fake_python = tmp_path / "Quill.app" / "Contents" / "Resources" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("", encoding="utf-8")

    monkeypatch.delenv(HOME_ENV, raising=False)
    monkeypatch.setattr(desktop.sys, "executable", str(fake_python))

    desktop._pin_home()

    assert HOME_ENV not in os.environ
