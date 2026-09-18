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

    assert len(messages) == MAX_HISTORY_RECORDS
    assert messages[-1]["content"] == f"问题{MAX_HISTORY_RECORDS + 4}"


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

    messages = build_history_messages(history)

    assert messages[0]["role"] == "user"  # 没有被切成孤立的一条
    assert messages[-2]["role"] == "assistant"
    assert messages[-1]["role"] == "tool"
    assert messages[-1]["tool_call_id"] == messages[-2]["tool_calls"][0]["id"]


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
