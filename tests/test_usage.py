"""用量统计的汇总规则：按天分桶、按模型分组、按任务合计。

时间全部定死，不依赖「今天」是哪天 —— 否则这些用例只在某几天能过。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from quill_agent.history import ConversationStore
from quill_agent.usage import build_report

TODAY = date(2026, 9, 19)


def _write(root: Path, conv_id: str, records: list[dict], archived: bool = False) -> None:
    """按会话文件的真实格式落一个文件。"""
    directory = root / ("archived" if archived else "active")
    directory.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    (directory / f"{conv_id}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _turn(
    text: str,
    *,
    when: str,
    model: str = "",
    total: int = 0,
    prompt: int = 0,
    completion: int = 0,
) -> list[dict]:
    """一轮对话 = 一条 user + 一条 assistant。"""
    return [
        {"role": "user", "content": text, "ts": when},
        {
            "role": "assistant",
            "content": "好",
            "ts": when,
            "model": model,
            "stats": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": total,
                "elapsed": 1.0,
            },
        },
    ]


def _report(root: Path, days: int = 7):
    return build_report(ConversationStore(root), days=days, today=TODAY)


def _series(report) -> dict[str, list[int]]:
    return {item.model: item.values for item in report.series}


def test_dates_cover_the_requested_window(tmp_path: Path) -> None:
    report = _report(tmp_path / "conversations")

    assert report.dates == [
        "2026-09-13",
        "2026-09-14",
        "2026-09-15",
        "2026-09-16",
        "2026-09-17",
        "2026-09-18",
        "2026-09-19",
    ]
    assert report.series == []
    assert report.tasks == []


def test_tokens_are_bucketed_by_day_and_model(tmp_path: Path) -> None:
    root = tmp_path / "conversations"
    _write(
        root,
        "20260918-100000-aaaa",
        _turn("一", when="2026-09-18T10:00:00", model="alpha", total=100),
    )
    _write(
        root,
        "20260919-120000-bbbb",
        _turn("二", when="2026-09-19T12:00:00", model="beta", total=50)
        + _turn("三", when="2026-09-19T13:00:00", model="alpha", total=30),
    )

    buckets = _series(_report(root))

    # 09-18 是窗口里的第 6 天（下标 5），09-19 是最后一天（下标 6）
    assert buckets["alpha"] == [0, 0, 0, 0, 0, 100, 30]
    assert buckets["beta"] == [0, 0, 0, 0, 0, 0, 50]


def test_records_outside_the_window_still_count_toward_the_task(tmp_path: Path) -> None:
    root = tmp_path / "conversations"
    _write(
        root,
        "20260918-100000-aaaa",
        _turn("上个月", when="2026-08-01T09:00:00", model="alpha", total=999)
        + _turn("今天", when="2026-09-19T09:00:00", model="alpha", total=10),
    )

    report = _report(root)

    # 折线只画窗口内的 10
    assert _series(report)["alpha"] == [0, 0, 0, 0, 0, 0, 10]
    # 表格是任务的完整历史，999 也要算进去
    assert report.tasks[0].tokens == 1009


def test_records_without_a_model_are_excluded_from_the_chart(tmp_path: Path) -> None:
    """`model` 是后加的字段，老记录没有它 —— 只进合计，不进按模型的折线。"""
    root = tmp_path / "conversations"
    _write(root, "20260919-090000-cccc", _turn("老记录", when="2026-09-19T09:00:00", total=42))

    report = _report(root)

    assert report.series == []
    assert report.tasks[0].tokens == 42
    assert report.tasks[0].models == []


def test_archived_conversations_are_included_and_marked(tmp_path: Path) -> None:
    root = tmp_path / "conversations"
    _write(
        root,
        "20260917-080000-dddd",
        _turn("归档的", when="2026-09-17T08:00:00", model="alpha", total=7),
        archived=True,
    )

    report = _report(root)

    assert report.tasks[0].archived is True
    assert _series(report)["alpha"] == [0, 0, 0, 0, 7, 0, 0]


def test_models_never_used_in_the_window_are_not_plotted(tmp_path: Path) -> None:
    """一个只剩 0 的模型连线都没有，只会占着图例干扰阅读。"""
    root = tmp_path / "conversations"
    _write(
        root,
        "20260919-090000-eeee",
        _turn("很久以前", when="2026-01-01T09:00:00", model="old", total=5),
    )

    report = _report(root)

    assert report.series == []
    assert report.tasks[0].models == ["old"]


def test_total_tokens_falls_back_to_in_plus_out(tmp_path: Path) -> None:
    """部分网关不返回用量，字段缺失或为 0 时要能从 in / out 加起来。"""
    root = tmp_path / "conversations"
    _write(
        root,
        "20260919-090000-ffff",
        _turn(
            "没有 total",
            when="2026-09-19T09:00:00",
            model="alpha",
            total=0,
            prompt=12,
            completion=3,
        ),
    )

    report = _report(root)

    assert _series(report)["alpha"] == [0, 0, 0, 0, 0, 0, 15]
    assert report.tasks[0].tokens == 15


def test_tasks_are_sorted_by_creation_time_desc(tmp_path: Path) -> None:
    root = tmp_path / "conversations"
    _write(
        root,
        "20260915-080000-gggg",
        _turn("早", when="2026-09-15T08:00:00", model="alpha", total=1),
    )
    _write(
        root,
        "20260919-080000-hhhh",
        _turn("晚", when="2026-09-19T08:00:00", model="alpha", total=1),
    )

    report = _report(root)

    assert [item.conversation_id for item in report.tasks] == [
        "20260919-080000-hhhh",
        "20260915-080000-gggg",
    ]
