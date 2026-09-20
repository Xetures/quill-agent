"""会话导出：把记录渲染成能直接读的 Markdown。

渲染的原则是「正文照抄、过程收起」—— 对话要能直接读，工具调用是过程，
收进折叠块：回看时通常不关心，删掉又可惜。
"""

from __future__ import annotations

from quill_agent.history import render_markdown


def test_user_and_assistant_turns_are_rendered() -> None:
    messages = [
        {"role": "user", "content": "帮我看看认证"},
        {"role": "assistant", "content": "在 src/auth.py:42"},
    ]

    out = render_markdown(messages, title="我的会话")

    assert out.startswith("# 我的会话")
    assert "## 用户" in out
    assert "帮我看看认证" in out
    assert "## 助手" in out
    assert "src/auth.py:42" in out


def test_tool_calls_go_into_a_folded_block() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "查一下",
            "steps": [
                {"name": "search_content", "arguments": '{"pattern": "auth"}', "result": "a.py:3"}
            ],
        }
    ]

    out = render_markdown(messages)

    assert "<details>" in out
    assert "工具调用（1 次）" in out
    assert "search_content" in out
    assert "a.py:3" in out


def test_summary_record_gets_its_own_section() -> None:
    """被压缩的早期对话在导出里也要看得到 —— 否则这份导出件看起来缺了一段。"""
    messages = [{"role": "summary", "content": "【摘要】之前定了用 JWT", "covers": 2}]

    out = render_markdown(messages)

    assert "## 已压缩的早期对话" in out
    assert "之前定了用 JWT" in out


def test_stats_line_shows_model_and_tokens() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "好了",
            "stats": {"elapsed": 3.2, "total_tokens": 1234},
            "model": "deepseek-chat",
        }
    ]

    out = render_markdown(messages)

    assert "3.2s" in out
    assert "1234 tokens" in out
    assert "deepseek-chat" in out


def test_missing_fields_do_not_break_it() -> None:
    """老记录字段不全（没有 stats / steps / content）也要能导出。

    会话文件是「只追加」的，字段是一路长出来的 —— 导出要能吃下每一种历史形态。
    """
    out = render_markdown([{"role": "user"}, {"role": "assistant", "content": ""}])

    assert "## 用户" in out
    assert "## 助手" in out


def test_empty_history_is_still_valid_markdown() -> None:
    out = render_markdown([], title="空会话")

    assert out.startswith("# 空会话")
    assert "共 0 条记录" in out
