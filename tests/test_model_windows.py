"""上下文窗口：模型级字段的迁移、端点解析、快照查表。

窗口大小是「用量仪表盘」的分母，填错比留空更糟，所以这里的用例大半是在验证
「拿不准的时候必须返回『不知道』」，而不是返回一个看着合理的数。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from quill_agent.core import _pick_window
from quill_agent.model_catalog import ModelCatalog, normalize
from quill_agent.models import ModelConfig


class FakeModel:
    """假的 SDK 模型对象：字段既挂在属性上，也能通过 model_dump 拿到。"""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        for key, value in payload.items():
            setattr(self, key, value)

    def model_dump(self) -> dict:
        return dict(self._payload)


def _catalog(tmp_path: Path, payload: dict) -> ModelCatalog:
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return ModelCatalog(path)


# ---------------------------------------------------------------------------
# 数据模型：连接级 -> 模型级
# ---------------------------------------------------------------------------


def test_legacy_connection_level_window_spreads_to_every_model() -> None:
    """旧数据把窗口挂在连接上；摊给当时配的每个模型，正是它本来想表达的意思。"""
    config = ModelConfig.model_validate(
        {"id": "c1", "name": "连接", "models": ["a", "b"], "context_window": 64000}
    )

    assert config.context_windows == {"a": 64000, "b": 64000}


def test_legacy_single_model_field_also_migrates() -> None:
    """更早期的写法：一条记录只能存一个模型，字段名叫 model。"""
    config = ModelConfig.model_validate(
        {"id": "c1", "name": "连接", "model": "deepseek-chat", "context_window": 64000}
    )

    assert config.models == ["deepseek-chat"]
    assert config.context_windows == {"deepseek-chat": 64000}


def test_new_field_wins_over_the_leftover_legacy_key() -> None:
    """两个字段同时存在时以新的为准，别让残留的老字段把用户改过的值盖回去。"""
    config = ModelConfig.model_validate(
        {
            "id": "c1",
            "name": "连接",
            "models": ["a"],
            "context_window": 64000,
            "context_windows": {"a": 128000},
        }
    )

    assert config.context_windows == {"a": 128000}


def test_unknown_window_is_absent_instead_of_defaulted() -> None:
    """没填就是没有这个键 —— 界面要能区分「不知道」和「128k」。"""
    config = ModelConfig(id="c1", name="连接", models=["a"])

    assert config.context_windows == {}


def test_non_positive_window_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ModelConfig(id="c1", name="连接", models=["a"], context_windows={"a": 0})


# ---------------------------------------------------------------------------
# 端点返回的解析
# ---------------------------------------------------------------------------


def test_pick_window_reads_the_known_field_names() -> None:
    assert _pick_window(FakeModel({"id": "x", "context_length": 128000})) == 128000
    assert _pick_window(FakeModel({"id": "x", "max_model_len": 32768})) == 32768
    assert _pick_window(FakeModel({"id": "x", "context_window": 8192})) == 8192


def test_pick_window_returns_none_for_a_clean_payload() -> None:
    """官方 OpenAI / Anthropic 的返回就是这样 —— 必须返回 None，不能猜。"""
    assert _pick_window(FakeModel({"id": "gpt-4o", "created": 1, "owned_by": "openai"})) is None


def test_pick_window_ignores_unusable_values() -> None:
    assert _pick_window(FakeModel({"id": "x", "context_length": 0})) is None
    assert _pick_window(FakeModel({"id": "x", "context_length": "128000"})) is None
    assert _pick_window(FakeModel({"id": "x", "context_length": True})) is None


# ---------------------------------------------------------------------------
# 本地快照：形状容错 + 归一化匹配
# ---------------------------------------------------------------------------


def test_catalog_reads_a_hand_written_flat_table(tmp_path: Path) -> None:
    """最简单的形式：用户自己写的 `{模型名: 窗口}`。"""
    catalog = _catalog(tmp_path, {"gpt-4o": 128000, "gpt-4": 8192})

    assert catalog.available
    assert catalog.lookup("gpt-4o") == 128000
    assert catalog.lookup("gpt-4") == 8192


def test_catalog_reads_the_models_dev_shape(tmp_path: Path) -> None:
    """models.dev 的形状：provider -> models -> {id: {limit: {context}}}。"""
    catalog = _catalog(
        tmp_path,
        {
            "openai": {
                "id": "openai",
                "name": "OpenAI",
                "models": {
                    "gpt-4o": {"id": "gpt-4o", "name": "GPT-4o", "limit": {"context": 128000}}
                },
            }
        },
    )

    assert catalog.lookup("gpt-4o") == 128000


def test_catalog_survives_a_broken_file(tmp_path: Path) -> None:
    """快照坏了应该降级成「查不到」，而不是让页面打不开。"""
    path = tmp_path / "catalog.json"
    path.write_text("{ 这不是 JSON", encoding="utf-8")

    catalog = ModelCatalog(path)

    assert catalog.available is False
    assert catalog.lookup("gpt-4o") is None


def test_missing_catalog_is_simply_unavailable(tmp_path: Path) -> None:
    catalog = ModelCatalog(tmp_path / "nope.json")

    assert catalog.available is False
    assert catalog.lookup("anything") is None


def test_lookup_normalizes_the_common_spellings(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, {"claude-sonnet-4": 200000, "deepseek-chat": 64000})

    assert catalog.lookup("anthropic/claude-sonnet-4-20250514") == 200000
    assert catalog.lookup("Claude_Sonnet_4") == 200000
    assert catalog.lookup("claude-3.5-sonnet") is None  # 表里没有这个，不要瞎猜
    assert catalog.lookup("deepseek-chat-0324") == 64000


def test_lookup_does_not_fuzzily_match_a_neighbour(tmp_path: Path) -> None:
    """`gpt-4` 是 8k、`gpt-4-1106-preview` 是 128k：名字互为前缀，窗口差 16 倍。

    模糊匹配一定会在这里命中错行，而错的值会让用户以为上下文还有空间。
    """
    catalog = _catalog(tmp_path, {"gpt-4": 8192, "gpt-4o": 128000})

    assert catalog.lookup("gpt-4-1106-preview") is None
    assert catalog.lookup("gpt-4o-2024-11-20") == 128000


def test_normalize_keeps_distinguishing_suffixes() -> None:
    """`-preview` / `-mini` / `-v3` 后面是不同的模型，不能当成版本尾巴剥掉。"""
    assert normalize("gpt-4-turbo-preview") == "gpt-4-turbo-preview"
    assert normalize("gpt-4o-mini") == "gpt-4o-mini"
    assert normalize("deepseek-v3") == "deepseek-v3"
