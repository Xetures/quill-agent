"""Agent 层的历史还原测试：会话记录（界面口径）-> API 消息。

重点验证两件事：
    1. 上一轮的工具调用不会被丢掉，而是还原成 assistant.tool_calls + tool 消息；
    2. 截断按「记录」进行，绝不会把一组工具调用从中间切开。
"""

from quill_agent.agent import (
    MAX_HISTORY_RECORDS,
    MAX_TOOL_RESULT_CHARS,
    build_history_messages,
)


def _records_of(messages: list[dict]) -> list[dict]:
    """剥掉历史被截断时插的那条 system 提示，只看由记录还原出来的部分。

    有几个用例关心的是「记录怎么切」，而不是「提示在不在」—— 把提示单独测
    （见 test_omitted_note_*），这些用例就能专心地断自己那件事。
    """
    return [item for item in messages if item["role"] != "system"]


def test_none_history() -> None:
    assert build_history_messages(None) == []


def test_plain_records_pass_through() -> None:
    """普通记录只保留 API 需要的两个字段，展示字段（ts）被丢掉。"""
    history = [
        {"role": "user", "content": "你好", "ts": "2026-09-18T10:00:00"},
        {"role": "assistant", "content": "你好！", "ts": "2026-09-18T10:00:01", "steps": []},
    ]

    assert build_history_messages(history) == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！"},
    ]


def test_tool_steps_are_restored_as_tool_messages() -> None:
    """带 steps 的助手记录要还原成一组自洽的 assistant + tool 消息。"""
    history = [
        {"role": "user", "content": "看看目录"},
        {
            "role": "assistant",
            "content": "我来看看",
            "steps": [{"name": "list_dir", "arguments": '{"path": "."}', "result": "a.txt"}],
        },
    ]

    messages = build_history_messages(history)

    assert [item["role"] for item in messages] == ["user", "assistant", "tool"]

    assistant, tool = messages[1], messages[2]
    assert assistant["content"] == "我来看看"
    assert assistant["tool_calls"][0]["function"]["name"] == "list_dir"
    # tool 消息必须指回它对应的那次调用，否则 API 会拒绝整个请求
    assert tool["tool_call_id"] == assistant["tool_calls"][0]["id"]
    assert tool["content"] == "a.txt"


def test_tool_only_turn_has_null_content() -> None:
    """只有工具调用、没有文本的一轮，content 用 None 而不是空串。"""
    history = [
        {
            "role": "assistant",
            "content": "",
            "steps": [{"name": "list_dir", "arguments": "{}", "result": "a.txt"}],
        }
    ]

    assert build_history_messages(history)[0]["content"] is None


def test_history_is_truncated_by_record() -> None:
    """超过上限时只保留最近的记录。"""
    history = [
        {"role": "user", "content": f"问题{index}"} for index in range(MAX_HISTORY_RECORDS + 5)
    ]

    messages = build_history_messages(history)

    assert len(_records_of(messages)) == MAX_HISTORY_RECORDS
    assert messages[-1]["content"] == f"问题{MAX_HISTORY_RECORDS + 4}"


def test_budget_keeps_everything_when_the_window_is_large() -> None:
    """窗口够大时不该被「20 条」这种固定值裁掉。

    这正是这次改动的目的：条数和 token 不成比例 —— 同样是 20 条，闲聊是 2k token，
    读代码的可以是 20 万。按条数截的后果是「20 条长记录悄悄撑爆窗口」。
    """
    history = [
        {"role": "user", "content": f"问题{index}"} for index in range(MAX_HISTORY_RECORDS + 5)
    ]

    messages = build_history_messages(history, context_window=200_000)

    assert len(messages) == len(history)  # 一条都没丢


def test_budget_keeps_only_the_recent_ones_when_the_window_is_small() -> None:
    """窗口小时按预算留最近几条。

    每条 100 字 → 估算 50 token（CHARS_PER_TOKEN=2）；窗口 2000 → 历史预算 1000，
    所以正好留 20 条 —— 条数和预算都算得出来，不依赖「刚好 20」这个巧合。
    """
    history = [{"role": "user", "content": "字" * 100} for _ in range(60)]

    messages = build_history_messages(history, context_window=2000)

    assert len(_records_of(messages)) == 20


def test_the_last_record_survives_any_budget() -> None:
    """最后一条无论多大都留着 —— 丢掉它，模型会彻底失忆。"""
    history = [
        {"role": "user", "content": "很久以前说过的话"},
        {"role": "user", "content": "字" * 100_000},  # 单条就远超预算
    ]

    records = _records_of(build_history_messages(history, context_window=100))

    assert len(records) == 1
    assert records[0]["content"].startswith("字")


def test_budget_truncation_keeps_tool_groups_whole() -> None:
    """按预算截断同样不能把一组工具调用切开（和按条数截断同一个约束）。"""
    history = [{"role": "user", "content": "字" * 400} for _ in range(10)]
    history.append(
        {
            "role": "assistant",
            "content": "",
            "steps": [{"name": "read_file", "arguments": "{}", "result": "内容"}],
        }
    )

    messages = build_history_messages(history, context_window=1000)

    assert messages[-2]["role"] == "assistant"
    assert messages[-1]["role"] == "tool"


def test_unknown_window_falls_back_to_the_record_limit() -> None:
    """窗口没配时不猜窗口，退回按条数上限。"""
    history = [
        {"role": "user", "content": f"问题{index}"} for index in range(MAX_HISTORY_RECORDS + 5)
    ]

    messages = build_history_messages(history, context_window=0)

    assert len(_records_of(messages)) == MAX_HISTORY_RECORDS


def test_a_tool_result_is_budgeted_at_its_replayed_size() -> None:
    """工具结果按「回放时的上限」算，不按原文长度算。

    否则一条读了大文件的历史会被估得极重，把前面几条无辜的记录一起挤掉 ——
    而实际发出去的只是截断后的那 2000 字符。
    """
    history = [
        {"role": "user", "content": "看看这个文件"},
        {
            "role": "assistant",
            "content": "",
            "steps": [{"name": "read_file", "arguments": "{}", "result": "x" * 500_000}],
        },
        {"role": "user", "content": "再改一下"},
    ]

    messages = build_history_messages(history, context_window=4000)

    # 三条都在：若按原文长度估，那条 50 万字符的记录会被算成 25 万 token，
    # 后面两条也会被一起挤掉
    assert [item["role"] for item in messages] == ["user", "assistant", "tool", "user"]


def test_truncation_never_splits_a_tool_group() -> None:
    """截断边界落在带工具调用的记录上时，整组消息要一起留下。"""
    history = [{"role": "user", "content": "旧消息"} for _ in range(MAX_HISTORY_RECORDS)]
    history.append(
        {
            "role": "assistant",
            "content": "",
            "steps": [{"name": "read_file", "arguments": "{}", "result": "内容"}],
        }
    )

    messages = _records_of(build_history_messages(history))

    assert messages[0]["role"] == "user"  # 没有被切成孤立的一条
    assert messages[-2]["role"] == "assistant"
    assert messages[-1]["role"] == "tool"
    assert messages[-1]["tool_call_id"] == messages[-2]["tool_calls"][0]["id"]


def test_omitted_note_appears_only_when_history_was_cut() -> None:
    """历史被截掉时给模型一句提示 —— 否则它会对着残缺的历史装作记得。

    提示里还必须点出取回的办法（recall_history）：只说「你看不到」对模型是条死路。
    """
    short = [{"role": "user", "content": "只有一条"}]
    long = [
        {"role": "user", "content": f"问题{index}"} for index in range(MAX_HISTORY_RECORDS + 5)
    ]

    # 没截断：一个字都不多插
    assert build_history_messages(short) == [{"role": "user", "content": "只有一条"}]

    messages = build_history_messages(long)

    assert messages[0]["role"] == "system"
    assert "recall_history" in messages[0]["content"]


def test_omitted_note_is_absent_when_nothing_was_dropped() -> None:
    """窗口够大、一条没丢时不插提示 —— 否则会平白让模型以为自己失忆了。"""
    history = [{"role": "user", "content": f"问题{index}"} for index in range(50)]

    messages = build_history_messages(history, context_window=200_000)

    assert all(item["role"] != "system" for item in messages)


def test_long_tool_result_is_clipped() -> None:
    """工具结果回放时有长度上限，避免一次读大文件就把历史撑爆。"""
    long_text = "x" * (MAX_TOOL_RESULT_CHARS + 100)

    history = [
        {
            "role": "assistant",
            "content": "",
            "steps": [{"name": "read_file", "arguments": "{}", "result": long_text}],
        }
    ]

    result = build_history_messages(history)[1]["content"]

    assert result.startswith("x" * MAX_TOOL_RESULT_CHARS)
    assert "已截断" in result
    assert len(result) < len(long_text)


def test_notice_only_record_is_not_sent_to_model() -> None:
    """只有系统提示、没有正文的助手记录不进历史。

    否则「请先选择模型」这类提示会被当成模型说过的话，下一轮又塞回上下文。
    """
    history = [
        {"role": "user", "content": "在吗"},
        {"role": "assistant", "content": "", "notices": ["请先选择模型。"]},
    ]

    assert build_history_messages(history) == [{"role": "user", "content": "在吗"}]


def test_reasoning_only_record_is_not_sent_to_model() -> None:
    """思维链只在本机留存，不回传给模型。"""
    history = [
        {"role": "user", "content": "1+1 等于几"},
        {"role": "assistant", "content": "2", "reasoning": "先想想…"},
    ]

    assert build_history_messages(history) == [
        {"role": "user", "content": "1+1 等于几"},
        {"role": "assistant", "content": "2"},
    ]


def test_malformed_records_are_skipped() -> None:
    """role 不认识、内容全空的记录直接跳过，不污染发给模型的消息序列。"""
    history = [
        {"role": "system", "content": "忽略我"},
        {"content": "没有 role"},
        {"role": "assistant", "content": "", "steps": []},
        {"role": "user", "content": "正常"},
    ]

    assert build_history_messages(history) == [{"role": "user", "content": "正常"}]
