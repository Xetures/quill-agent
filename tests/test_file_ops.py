"""目录 / 文件管理工具：新建、移动、复制、删空目录。

`PathGuard` 与 `_target` 的通用校验在 test_files.py 里已单独测过，这里盯的是
新加的几个动作本身，重点三条：**不覆盖**、**不越界**、**不跟着符号链接跑到外面**。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from quill_agent.tools import files


@pytest.fixture(autouse=True)
def _work_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(files, "current_work_dir", lambda: tmp_path)


# ---------------------------------------------------------------------------
# make_dir
# ---------------------------------------------------------------------------


def test_make_dir_creates_nested(tmp_path: Path) -> None:
    assert "已创建目录" in files.make_dir("a/b/c")

    assert (tmp_path / "a" / "b" / "c").is_dir()


def test_make_dir_is_idempotent(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()

    assert "已存在" in files.make_dir("a")


def test_make_dir_refuses_to_clobber_a_file(tmp_path: Path) -> None:
    (tmp_path / "a").write_text("x", encoding="utf-8")

    assert "同名文件" in files.make_dir("a")
    assert (tmp_path / "a").is_file()  # 原文件没被动


def test_make_dir_rejects_path_outside_work_dir(tmp_path: Path) -> None:
    out = files.make_dir("../escaped")

    assert "拒绝访问工作目录之外" in out
    assert not (tmp_path.parent / "escaped").exists()


# ---------------------------------------------------------------------------
# 符号链接与边界
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="Windows 上建符号链接需要额外权限")
def test_search_content_does_not_follow_a_symlink_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """工作目录里的软链指向外面时，搜索不该把外面的内容读出来。

    与 `read_file` 一个口径：同一道边界，两个工具不能有两种行为 ——
    `read_file` 拒绝的东西，`search_content` 却读得出来，等于边界不存在。
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("TOPSECRET", encoding="utf-8")

    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(files, "current_work_dir", lambda: work)
    (work / "link.txt").symlink_to(outside / "secret.txt")

    out = files.search_content("TOPSECRET")

    # 没有命中，而且一个文件都没读 —— 那个软链在遍历时就被挡下了
    assert "没有找到匹配" in out
    assert "已扫描 0 个文件" in out
    assert "link.txt" not in files.search_files("*.txt")


def test_search_still_reads_real_files_in_the_work_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """边界收紧了，但不能把正常工作目录也一起挡掉。"""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(files, "current_work_dir", lambda: work)
    (work / "notes.txt").write_text("hello quill", encoding="utf-8")

    assert "hello quill" in files.search_content("quill")


# ---------------------------------------------------------------------------
# move_file
# ---------------------------------------------------------------------------


def test_move_file_renames(tmp_path: Path) -> None:
    (tmp_path / "old.txt").write_text("hi", encoding="utf-8")

    out = files.move_file("old.txt", "new.txt")

    assert "已移动文件" in out
    assert not (tmp_path / "old.txt").exists()
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "hi"


def test_move_file_into_subdir(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    (tmp_path / "sub").mkdir()

    files.move_file("a.txt", "sub/a.txt")

    assert (tmp_path / "sub" / "a.txt").is_file()


def test_move_file_renames_directory(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "inside.txt").write_text("x", encoding="utf-8")

    out = files.move_file("src", "dst")

    assert "已移动目录" in out
    assert (tmp_path / "dst" / "inside.txt").is_file()


def test_move_file_wont_overwrite(tmp_path: Path) -> None:
    """不覆盖是刻意的：要替换已有文件，先 delete_file 是明确的。"""
    (tmp_path / "a.txt").write_text("source", encoding="utf-8")
    (tmp_path / "b.txt").write_text("target", encoding="utf-8")

    out = files.move_file("a.txt", "b.txt")

    assert "目标已存在" in out
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "source"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "target"


def test_move_file_reports_missing_source(tmp_path: Path) -> None:
    assert "路径不存在" in files.move_file("nope.txt", "x.txt")


def test_move_file_same_path_is_noop(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")

    assert "源和目标相同" in files.move_file("a.txt", "a.txt")
    assert (tmp_path / "a.txt").is_file()


def test_move_file_rejects_moving_dir_into_itself(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()

    assert "源目录内部" in files.move_file("src", "src/inner")


def test_move_file_needs_existing_parent(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")

    out = files.move_file("a.txt", "nodir/a.txt")

    assert "make_dir" in out


def test_move_file_rejects_source_outside_work_dir(tmp_path: Path) -> None:
    assert "拒绝访问工作目录之外" in files.move_file("../outside.txt", "inside.txt")


def test_move_file_rejects_destination_outside_work_dir(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")

    out = files.move_file("a.txt", "../escaped.txt")

    assert "拒绝访问工作目录之外" in out
    assert (tmp_path / "a.txt").is_file()  # 越界被拒，源文件还在


def test_move_file_wont_move_the_work_dir_itself(tmp_path: Path) -> None:
    assert "工作目录本身" in files.move_file(".", "sub")


# ---------------------------------------------------------------------------
# copy_file
# ---------------------------------------------------------------------------


def test_copy_file_keeps_the_original(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")

    out = files.copy_file("a.txt", "b.txt")

    assert "已复制文件" in out
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "hi"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "hi"


def test_copy_file_copies_a_directory(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "inside.txt").write_text("x", encoding="utf-8")

    out = files.copy_file("src", "dst")

    assert "已复制目录" in out
    assert (tmp_path / "src" / "inside.txt").is_file()  # 源目录还在
    assert (tmp_path / "dst" / "inside.txt").read_text(encoding="utf-8") == "x"


def test_copy_file_does_not_follow_symlinks_out_of_work_dir(tmp_path: Path) -> None:
    """目录里的软链接照原样复制，**不跟随** —— 否则工作目录外的内容会被抄进来。"""
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("秘密", encoding="utf-8")

    src = tmp_path / "src"
    src.mkdir()
    try:
        os.symlink(outside, src / "link.txt")
    except (OSError, NotImplementedError):
        pytest.skip("当前平台不支持创建符号链接")

    files.copy_file("src", "dst")

    copied = tmp_path / "dst" / "link.txt"
    assert copied.is_symlink()  # 还是链接，说明没被跟随
    assert not (tmp_path / "dst" / "outside-secret.txt").exists()


def test_copy_file_wont_overwrite(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("source", encoding="utf-8")
    (tmp_path / "b.txt").write_text("target", encoding="utf-8")

    assert "目标已存在" in files.copy_file("a.txt", "b.txt")
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "target"


def test_copy_file_same_path_is_noop(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")

    assert "源和目标相同" in files.copy_file("a.txt", "a.txt")


def test_copy_file_wont_copy_the_work_dir_itself(tmp_path: Path) -> None:
    assert "工作目录" in files.copy_file(".", "backup")


# ---------------------------------------------------------------------------
# delete_file（新增：空目录）
# ---------------------------------------------------------------------------


def test_delete_file_removes_a_file(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")

    assert "已删除文件" in files.delete_file("a.txt")
    assert not (tmp_path / "a.txt").exists()


def test_delete_file_removes_an_empty_dir(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()

    assert "已删除空目录" in files.delete_file("empty")
    assert not (tmp_path / "empty").exists()


def test_delete_file_refuses_a_non_empty_dir(tmp_path: Path) -> None:
    """递归删除太危险，这个工具不提供 —— 一次误判就没了整棵目录树。"""
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "keep.txt").write_text("x", encoding="utf-8")

    out = files.delete_file("full")

    assert "只删除空目录" in out
    assert (tmp_path / "full" / "keep.txt").is_file()


def test_delete_file_wont_delete_the_work_dir_itself(tmp_path: Path) -> None:
    assert "工作目录本身" in files.delete_file(".")
    assert tmp_path.is_dir()


# ---------------------------------------------------------------------------
# _destination
# ---------------------------------------------------------------------------


def test_destination_allows_a_new_path_in_an_existing_dir(tmp_path: Path) -> None:
    target, error = files._destination("new.txt")

    assert error == ""
    assert target == tmp_path / "new.txt"


def test_destination_rejects_an_existing_path(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")

    target, error = files._destination("a.txt")

    assert target is None
    assert "目标已存在" in error


def test_destination_requires_existing_parent(tmp_path: Path) -> None:
    target, error = files._destination("nodir/a.txt")

    assert target is None
    assert "make_dir" in error


# ---------------------------------------------------------------------------
# edit_file 的大小上限
# ---------------------------------------------------------------------------


def test_edit_file_refuses_a_file_above_the_size_limit(tmp_path: Path) -> None:
    """edit_file 也要有大小上限 —— 它同样得把整个文件读进内存才谈得上替换。

    `read_file` 一直有这道限制，edit_file 之前漏了：让它去改一个几百 MB 的文件，
    读的那一下就把内存顶上去了。
    """
    (tmp_path / "big.txt").write_text("x" * (files.MAX_FILE_BYTES + 1), encoding="utf-8")

    out = files.edit_file("big.txt", "x", "y")

    assert "太大" in out


# ---------------------------------------------------------------------------
# 正则的安全兜底
# ---------------------------------------------------------------------------


def test_search_content_rejects_a_backtracking_bomb(tmp_path: Path) -> None:
    """嵌套量词的正则直接拒掉。

    Python 的 `re` **没有超时机制** —— 一个写歪的模式能把整个进程挂住，
    而这个模式是模型随手生成的、没人审过。
    """
    (tmp_path / "a.txt").write_text("a" * 40, encoding="utf-8")

    out = files.search_content("(a+)+$")

    assert "灾难性回溯" in out


def test_search_content_still_accepts_normal_patterns(tmp_path: Path) -> None:
    """正常写法不该被误伤：相邻量词（`a+b+`）不在拦截范围里。"""
    (tmp_path / "a.txt").write_text("aaabbb", encoding="utf-8")

    out = files.search_content("a+b+")

    assert "灾难性回溯" not in out
    assert "aaabbb" in out
