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

from quill_agent.locking import atomic_write_text, file_lock
from quill_agent.store import read_json

# 草稿类偏好的键前缀：一个会话一份，避免互相覆盖
DRAFT_KEY_PREFIX = "prompt_draft"

# 模式偏好的键前缀。模式同样按会话记：任务是「用这个模式在聊」，
# 切到另一个任务就该回到那个任务自己的模式，而不是把上一个任务的模式带过去
MODE_KEY_PREFIX = "mode"

# 单轮对话的开销上限（token，以字符串存）。空串 / 非法值 / 0 一律表示**不限制**。
#
# 放偏好文件而不是只写 .env：阈值这种东西用户会想边用边调，改 .env 得重启进程。
# 它是安全阀而不是必配项 —— 没配就维持原来的行为（只受轮次兜底约束）。
MAX_RUN_TOKENS_KEY = "max_run_tokens"

# 单轮最多跑几轮工具调用（以字符串存）。空串 / 非法值 / 非正数一律退回默认值。
#
# 它是**兜底**，不是主闸门（主闸门是上面那个 token 上限）：真跑飞了，该先撞上的是
# 预算，而不是「数到第几步」。留这个可配的口子，是因为「正常轮次」因任务而异 ——
# 闲聊几轮就完，探路型任务动辄十几轮（摸结构 → 读文档 → 确认工具链 → 动手）。
MAX_ITERATIONS_KEY = "max_iterations"


def draft_key(conversation_id: str) -> str:
    """草稿的存储键。

    按会话隔离：在会话 A 里写了一半切到 B，两边草稿互不干扰。
    """
    return f"{DRAFT_KEY_PREFIX}::{conversation_id}"


def mode_key(conversation_id: str) -> str:
    """会话记住的模式 id 的存储键。

    和 `draft_key` 同一个思路：按会话隔离。不带前缀的 `mode` 键仍然保留，
    作为「这个会话还没记过模式」时的回落值（老版本存的就是它）。
    """
    return f"{MODE_KEY_PREFIX}::{conversation_id}"


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
        """写入一个偏好，保留文件里的其他键；值没变则不落盘。

        「读 → 改 → 写」整段在文件锁里：前端是**每个键各发一个请求**，
        而后端把同步路由跑在线程池里 —— 两个请求真会并发。不加锁的话，
        它们各自读一份快照再整份写回，后写的那个会把先写的键抹掉。
        """
        with file_lock(self.path):
            data = self._load()
            if data.get(key) == value:
                return

            data[key] = value
            atomic_write_text(self.path, json.dumps(data, ensure_ascii=False, indent=2))

    def remove(self, key: str) -> None:
        """删除一个偏好；不存在时静默忽略。"""
        with file_lock(self.path):
            data = self._load()
            if key not in data:
                return

            del data[key]
            atomic_write_text(self.path, json.dumps(data, ensure_ascii=False, indent=2))

    def _load(self) -> dict[str, Any]:
        """读取整个文件；任何异常都退回空字典。"""
        data = read_json(self.path, {})
        # 顶层不是对象（比如手滑写成了数组）也当空处理
        return data if isinstance(data, dict) else {}
