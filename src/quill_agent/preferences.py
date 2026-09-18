"""界面偏好：跨进程保留用户的选择。

与 models.json / modes.json 的区别：
    那些是用户配置的「数据」，缺了功能就不完整；
    这里存的是「上次选了什么」，属于体验范畴 —— 丢了只会退回默认值。

因此读写都不做严格校验：文件缺失、JSON 损坏、类型不符，一律当作默认值，
绝不因为一个偏好文件坏掉就让页面起不来。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quill_agent.store import read_json

# 草稿类偏好的键前缀：一个会话一份，避免互相覆盖
DRAFT_KEY_PREFIX = "prompt_draft"


def draft_key(conversation_id: str) -> str:
    """草稿的存储键。

    按会话隔离：在会话 A 里写了一半切到 B，两边草稿互不干扰。
    """
    return f"{DRAFT_KEY_PREFIX}::{conversation_id}"


class PreferenceStore:
    """极简键值存储：整个文件就是一个小 JSON 对象。

    值统一按字符串处理（存的都是 id 或 "连接id::模型名" 这类复合标识），
    够用且省掉了类型转换。
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def all(self) -> dict[str, str]:
        """读出全部偏好。

        界面启动时要一次性拿到「上次选的模型 / 模式 / 工作目录」，逐个 `get`
        得来回好几趟；顺便把非字符串的值过滤掉，和 `get` 的口径保持一致。
        """
        return {key: value for key, value in self._load().items() if isinstance(value, str)}

    def get(self, key: str, default: str = "") -> str:
        """读取一个偏好；不存在或不可用时返回 default。"""
        value = self._load().get(key)
        return value if isinstance(value, str) else default

    def set(self, key: str, value: str) -> None:
        """写入一个偏好，保留文件里的其他键；值没变则不落盘。"""
        data = self._load()
        if data.get(key) == value:
            return

        data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def remove(self, key: str) -> None:
        """删除一个偏好；不存在时静默忽略。"""
        data = self._load()
        if key not in data:
            return

        del data[key]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load(self) -> dict[str, Any]:
        """读取整个文件；任何异常都退回空字典。"""
        data = read_json(self.path, {})
        # 顶层不是对象（比如手滑写成了数组）也当空处理
        return data if isinstance(data, dict) else {}
