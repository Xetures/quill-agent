"""记忆：跨会话保留的长期事实与偏好。

和「会话历史」的区别：
    会话历史是原始消息，只活在一个会话里，超长就截断丢掉；
    记忆是提炼过的短句，跨会话存在，由用户决定留哪些。

和「技能」的关系：
    技能管「这类任务怎么做」，记忆管「关于用户的既定事实」。两者机制相同 ——
    都存成数据、都由模式决定给不给、都作为清单进上下文，用不到的不花 token。

为什么不做成自动抽取：自动抽需要额外调一次模型，而且用户看不见模型到底记了
什么、错在哪。这里改成让模型显式调用 remember 工具写入 —— 用户在「记忆」页面
能看到、能关、能删，记错的东西可以立刻纠正。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from quill_agent.locking import atomic_write_text, file_lock
from quill_agent.store import read_json

# 记忆条数上限。它要以清单形式常驻上下文，放任增长就等于把「按需加载」又改回
# 「全量注入」。到顶后拒绝写入、让用户先清理 —— 不自动淘汰，记忆是用户的资产。
MAX_MEMORIES = 50

# 单条记忆的长度上限。记忆是一句话，不是一段笔记。
MAX_MEMORY_CHARS = 200


class MemoryItem(BaseModel):
    """一条记忆。

    Attributes:
        id: 唯一标识，切换与删除时定位用（对用户不可见）。
        text: 正文。一条记忆就是一句话，不存多段。
        enabled: 是否参与注入；关掉不等于删除，可以留着以后再用。
        created_at: 记录时间。记忆会过时，时间是最基本的判断依据。
    """

    id: str = Field(description="唯一标识")
    text: str = Field(min_length=1, description="正文")
    enabled: bool = Field(default=True, description="是否参与注入")
    created_at: str = Field(default="", description="记录时间")


class MemoryStore:
    """记忆的持久化：一个 JSON 数组。

    Args:
        path: JSON 文件路径；父目录在首次写入时自动创建。
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def list(self) -> list[MemoryItem]:
        """全部记忆，新的在前。

        整体坏掉、单条不合法都不报错 —— 少几条记忆不该让应用起不来。
        """
        raw = read_json(self._path, [])
        if not isinstance(raw, list):
            return []

        items: list[MemoryItem] = []
        for entry in raw:
            try:
                items.append(MemoryItem.model_validate(entry))
            except ValidationError:
                continue  # 坏的那条跳过，其余照常读出

        return items

    def enabled(self) -> list[MemoryItem]:
        """参与注入的记忆。"""
        return [item for item in self.list() if item.enabled]

    def add(self, text: str) -> MemoryItem:
        """新增一条记忆。

        空白会被压成单个空格：记忆是一句话，换行没有意义。

        Raises:
            ValueError: 内容为空 / 太长 / 与已有记忆重复 / 已达上限。
                异常文案是直接给模型看的（remember 工具会把它转成文本回给模型）。
        """
        cleaned = " ".join(text.split())

        if not cleaned:
            raise ValueError("记忆内容不能为空。")
        if len(cleaned) > MAX_MEMORY_CHARS:
            raise ValueError(
                f"这条太长了（{len(cleaned)} 字，上限 {MAX_MEMORY_CHARS} 字），请压缩成一句话。"
            )

        item = MemoryItem(
            id=uuid4().hex,
            text=cleaned,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )

        # 去重与上限都在锁内、对着同一份快照判：模型可能连着两轮都想「记一下」，
        # 而两套界面也可能同时写同一个文件
        with file_lock(self._path):
            items = self.list()

            # 完全相同的正文不再记第二遍：模型每轮都可能想「记一下」，
            # 不去重的话同一件事会被反复写进来，把清单撑满
            if any(existing.text == cleaned for existing in items):
                raise ValueError("这条已经记过了，不需要重复记录。")

            if len(items) >= MAX_MEMORIES:
                raise ValueError(
                    f"记忆已满（上限 {MAX_MEMORIES} 条）。请提醒用户到「记忆」页面清理一些。"
                )

            self._save_all([item, *items])

        return item

    def set_enabled(self, memory_id: str, enabled: bool) -> None:
        """切换某条记忆是否参与注入。"""
        with file_lock(self._path):
            self._save_all(
                [
                    item.model_copy(update={"enabled": enabled}) if item.id == memory_id else item
                    for item in self.list()
                ]
            )

    def forget(self, text: str) -> MemoryItem:
        """按原文忘掉一条记忆。

        为什么只认完全一致的原文：模型看到的是清单里的原文，所以它能一字不差地引用；
        而模糊匹配（包含、相似）会让「忘掉 A」顺手把「A 和 B」也删了 ——
        删除不可逆，这里宁可让模型多失败一次。

        匹配前做和 add 相同的空白归一化，模型多带几个空格也能删对。

        Raises:
            ValueError: 内容为空，或没有内容完全一致的记忆。
                异常文案直接给模型看，所以要说清「下一步怎么办」。
        """
        target = " ".join(text.split())

        if not target:
            raise ValueError("要忘掉的内容不能为空。")

        with file_lock(self._path):
            items = self.list()
            for index, item in enumerate(items):
                if item.text != target:
                    continue

                self._save_all(items[:index] + items[index + 1 :])
                return item

        raise ValueError(
            f"没有找到内容为「{target}」的记忆。"
            "请照抄「关于用户的已知信息」清单里的原文，一个字都不要改。"
        )

    def remove(self, memory_id: str) -> None:
        """删除一条记忆；id 不存在时静默忽略（和别的 store 保持一致）。"""
        with file_lock(self._path):
            self._save_all([item for item in self.list() if item.id != memory_id])

    def clear(self) -> int:
        """清空全部记忆。

        Returns:
            被删掉的条数。
        """
        with file_lock(self._path):
            removed = len(self.list())
            self._save_all([])

        return removed

    def _save_all(self, items: list[MemoryItem]) -> None:
        """整体覆写：记忆条数天然很少，简单可靠优先。

        与别的存储一样：调用方持锁进入，落盘走原子替换（见 `file_lock`）。
        """
        atomic_write_text(
            self._path,
            json.dumps([item.model_dump() for item in items], ensure_ascii=False, indent=2),
        )
