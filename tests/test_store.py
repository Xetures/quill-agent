"""持久化层测试：重点是「一个坏文件不该让应用起不来」。

这些 JSON 用户会直接编辑（手滑写坏一个括号是常事），所以容错行为值得钉住。
"""

import json
from pathlib import Path

from quill_agent.store import ModelStore, read_json


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


def test_single_bad_entry_is_skipped(tmp_path: Path) -> None:
    """一条记录不合法只丢那一条。

    这条曾经是硬伤：`read_json` 只兜「JSON 本身坏了」，兜不住「JSON 合法但字段
    不合法」—— 用户把某个 api_key 改出格式问题，模型页、任务页会一起打不开。
    """
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            [
                {"id": "ok", "name": "好的一条", "models": ["m"]},
                {"id": "bad", "name": "坏的一条", "api_key": "带空格 的 key"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    items = ModelStore(path).list()

    assert [item.id for item in items] == ["ok"]


def test_bad_entry_does_not_hide_the_rest(tmp_path: Path) -> None:
    """坏的在中间时，前面和后面的记录都要读出来（不是遇到坏的就把后面全丢）。"""
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            [
                {"id": "first", "name": "第一条", "models": ["m"]},
                {"id": "bad", "name": ""},
                {"id": "last", "name": "最后一条", "models": ["m"]},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert [item.id for item in ModelStore(path).list()] == ["first", "last"]
