"""持久化层测试：重点是「一个坏文件不该让应用起不来」。

这些 JSON 用户会直接编辑（手滑写坏一个括号是常事），所以容错行为值得钉住。
"""

from pathlib import Path

from quill_agent.store import ToolStateStore, read_json


def test_missing_file_returns_default(tmp_path: Path) -> None:
    assert read_json(tmp_path / "nope.json", []) == []


def test_blank_file_returns_default(tmp_path: Path) -> None:
    """只有空白的文件等同于不存在 —— 不用额外判断。"""
    path = tmp_path / "blank.json"
    path.write_text("   \n", encoding="utf-8")

    assert read_json(path, {}) == {}


def test_corrupt_file_is_quarantined(tmp_path: Path) -> None:
    """坏文件先改名留档，再退回默认值。

    只忽略不处理的话，下一次保存就把用户原来的数据永久盖掉了。
    """
    path = tmp_path / "bad.json"
    path.write_text("{ 这不是 JSON", encoding="utf-8")

    assert read_json(path, []) == []
    assert not path.exists()
    assert (tmp_path / "bad.json.corrupt").exists()


def test_default_is_deep_copied(tmp_path: Path) -> None:
    """退回默认值时给的是新对象，不是同一份 —— 否则一处修改会污染另一处。"""
    shared: list[str] = []

    first = read_json(tmp_path / "nope.json", shared)
    first.append("脏了")

    assert shared == []
    assert read_json(tmp_path / "nope.json", shared) == []


def test_tool_state_store_survives_corrupt_file(tmp_path: Path) -> None:
    """坏掉的 tools.json 退回「全默认」，页面照常能打开。"""
    path = tmp_path / "tools.json"
    path.write_text("坏得没法看", encoding="utf-8")

    assert ToolStateStore(path).load() == {}
