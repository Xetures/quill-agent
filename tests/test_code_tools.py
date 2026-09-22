"""git 工具与符号检索。

两个模块放在一个测试文件里：它们都是「让模型看懂自己的改动」这一件事的两半 ——
一半是版本历史，一半是代码结构。而且都靠**文本启发式 + 子进程**，出问题的样子也像：
在临时目录里跑，都得把「工作目录」换成临时目录。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from quill_agent.tools import git, symbols

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="这台机器上没有 git")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """一个真的（临时）git 仓库 —— 用真的，才测得出 status/diff 的真实输出格式。"""
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "test"],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把两个模块的「工作目录」指到临时目录上，别去动真实仓库。"""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(git, "current_work_dir", lambda: work)
    monkeypatch.setattr(symbols, "current_work_dir", lambda: work)
    return work


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


def test_git_status_outside_repo(workspace: Path) -> None:
    """不在仓库里时要说清「这不是仓库」，而不是抛一句 git 的原始错误。"""
    assert "不是一个 git 仓库" in git.git_status()
    assert "不是一个 git 仓库" in git.git_diff()
    assert "不是一个 git 仓库" in git.git_log()


@requires_git
def test_git_status_reports_changes(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(git, "current_work_dir", lambda: repo)
    (repo / "a.txt").write_text("hello\n", encoding="utf-8")

    text = git.git_status()

    assert "main" in text
    assert "a.txt" in text


@requires_git
def test_git_diff_shows_content(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(git, "current_work_dir", lambda: repo)
    target = repo / "a.txt"
    target.write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True, capture_output=True)

    target.write_text("changed\n", encoding="utf-8")
    text = git.git_diff()

    assert "-hello" in text
    assert "+changed" in text


@requires_git
def test_git_commit_refused_changes_nothing(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """用户拒绝时，仓库历史必须一动不动 —— 这是确认通道存在的全部意义。"""
    monkeypatch.setattr(git, "current_work_dir", lambda: repo)
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(git.interaction, "confirm", lambda **_: False)

    text = git.git_commit("试试")

    assert "拒绝" in text
    # 还没有任何提交 → git log 会失败
    done = subprocess.run(["git", "log"], cwd=repo, capture_output=True)
    assert done.returncode != 0


@requires_git
def test_git_commit_asks_before_writing(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """允许时真的提交，而且请求体里带着改动清单（用户要看着它决定）。"""
    monkeypatch.setattr(git, "current_work_dir", lambda: repo)
    (repo / "a.txt").write_text("x\n", encoding="utf-8")

    seen: dict = {}

    def fake_confirm(*, text: str, detail: str = "") -> bool:
        seen["detail"] = detail
        return True

    monkeypatch.setattr(git.interaction, "confirm", fake_confirm)

    text = git.git_commit("加一个文件")

    assert "a.txt" in seen["detail"]
    assert "加一个文件" in text
    done = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"], cwd=repo, capture_output=True, text=True
    )
    assert done.stdout.strip() == "加一个文件"


def test_git_commit_rejects_empty_message(workspace: Path) -> None:
    assert "不能为空" in git.git_commit("   ")


# ---------------------------------------------------------------------------
# 符号检索
# ---------------------------------------------------------------------------


def test_outline_lists_definitions(workspace: Path) -> None:
    (workspace / "m.py").write_text(
        "class Alpha:\n    def method(self):\n        pass\n\n\ndef beta():\n    pass\n",
        encoding="utf-8",
    )

    text = symbols.outline("m.py")

    assert "Alpha" in text
    assert "method" in text
    assert "beta" in text


def test_outline_walks_directory(workspace: Path) -> None:
    (workspace / "src").mkdir()
    (workspace / "src" / "a.py").write_text("def alpha():\n    pass\n", encoding="utf-8")
    (workspace / "src" / "b.ts").write_text("export function beta() {}\n", encoding="utf-8")

    text = symbols.outline("src")

    assert "alpha" in text
    assert "beta" in text


def test_outline_skips_generated_dirs(workspace: Path) -> None:
    """node_modules 不是源码，扫它只会淹没真正的结果。"""
    (workspace / "node_modules" / "pkg").mkdir(parents=True)
    (workspace / "node_modules" / "pkg" / "index.js").write_text(
        "function noise() {}\n", encoding="utf-8"
    )
    (workspace / "app.js").write_text("function real() {}\n", encoding="utf-8")

    text = symbols.outline(".")

    assert "real" in text
    assert "noise" not in text


def test_outline_unknown_type(workspace: Path) -> None:
    target = workspace / "notes.txt"
    target.write_text("随便写点什么\n", encoding="utf-8")

    assert "还不支持" in symbols.outline("notes.txt")


def test_outline_refuses_outside_work_dir(workspace: Path) -> None:
    assert "工作目录之外" in symbols.outline("../")


def test_find_symbol_separates_definition_from_reference(workspace: Path) -> None:
    (workspace / "m.py").write_text(
        "def target():\n    pass\n\n\ndef caller():\n    target()\n",
        encoding="utf-8",
    )

    text = symbols.find_symbol("target")

    assert "定义：" in text
    assert "引用：" in text
    assert "m.py:1" in text  # 定义在第 1 行
    assert "m.py:6" in text  # 调用在第 6 行


def test_find_symbol_respects_word_boundary(workspace: Path) -> None:
    """搜 `read` 不该把 `read_file` 也算上 —— 词边界是这里最容易漏的一处。"""
    (workspace / "m.py").write_text(
        "def read():\n    pass\n\n\ndef read_file():\n    pass\n",
        encoding="utf-8",
    )

    text = symbols.find_symbol("read", kind="definition")

    assert "m.py:1" in text
    assert "read_file" not in text


def test_find_symbol_says_it_is_heuristic(workspace: Path) -> None:
    """结果里要自带「这是候选」的提醒：模型看到的是返回值，不是工具描述。"""
    (workspace / "m.py").write_text("def anything():\n    pass\n", encoding="utf-8")

    assert "文本匹配" in symbols.find_symbol("anything")
    assert "文本匹配" in symbols.find_symbol("nope_not_here")
